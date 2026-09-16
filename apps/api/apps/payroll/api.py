"""
Payroll API.

Every mutating route is a thin wrapper over a service, so the statutory gate
and the segregation-of-duties gate apply identically whether the caller is this
API, a management command or a test. Nothing here decides who may approve a
run — the permission matrix grants `payroll_run/approve` to Finance Head and
Admin only, and `approve_run()` additionally refuses the person who prepared it.

`GET /payroll/runs/{id}/` reports `blockers` and `can_approve`, both computed
server-side by the same function that guards the action. The UI renders that
answer rather than deriving its own, so what a user is shown and what the
server will accept cannot drift apart.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter
from rest_framework.views import APIView

from apps.employees.models import Employee
from apps.statutory.models import StatutoryRuleSet, VerificationStatus
from core.access import Action, Resource, Scope, can, require, scope_queryset
from core.access.drf import ScopedModelViewSet, ScopedReadOnlyModelViewSet

from . import outputs, services
from .models import (
    InvestmentDeclaration,
    PayrollAdjustment,
    PayrollRun,
    PayrollRunStatus,
    Payslip,
    PayslipLine,
    SalaryComponent,
    SalaryStructure,
    StatutoryContribution,
)


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin
from core.querysets import deferred

def _call(fn, **kwargs):
    """Run a service, translating its Django exceptions into DRF responses."""
    try:
        return fn(**kwargs)
    except DjangoPermissionDenied as exc:
        raise DRFPermissionDenied(str(exc)) from exc
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


# ===========================================================================
# Serializers
# ===========================================================================


class SalaryComponentSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = SalaryComponent
        fields = [
            "id", "code", "name", "component_type", "calc_type", "percent_of_code",
            "is_taxable", "is_part_of_ctc", "is_wage", "rounding", "display_order",
        ]


class SalaryStructureLineSerializer(serializers.Serializer):
    component = serializers.UUIDField()
    component_code = serializers.CharField(source="component.code", read_only=True)
    component_name = serializers.CharField(source="component.name", read_only=True)
    is_wage = serializers.BooleanField(source="component.is_wage", read_only=True)
    value = serializers.DecimalField(max_digits=14, decimal_places=2)
    monthly_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


class SalaryStructureSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    lines = SalaryStructureLineSerializer(many=True, read_only=True)
    monthly_gross = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )
    monthly_wage = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    wage_share = serializers.SerializerMethodField()

    class Meta:
        model = SalaryStructure
        fields = [
            "id", "employee", "employee_name", "employee_code", "ctc_annual",
            "valid_from", "valid_to", "revision_reason", "lines",
            "pf_applicable", "esi_applicable", "pt_applicable",
            "tds_applicable", "gratuity_applicable",
            "monthly_gross", "monthly_wage", "wage_share",
        ]
        read_only_fields = ["valid_to"]

    def get_wage_share(self, obj) -> float:
        return float(obj.wage_share())


class CreateStructureSerializer(serializers.Serializer):
    employee = serializers.UUIDField()
    ctc_annual = serializers.DecimalField(max_digits=14, decimal_places=2)
    valid_from = serializers.DateField()
    revision_reason = serializers.CharField(required=False, allow_blank=True, default="")
    lines = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    #: Statutory enrolment, per employee. Defaults keep everyone covered; HR
    #: Head / Finance Head untick what genuinely does not apply.
    pf_applicable = serializers.BooleanField(required=False, default=True)
    esi_applicable = serializers.BooleanField(required=False, default=True)
    pt_applicable = serializers.BooleanField(required=False, default=True)
    tds_applicable = serializers.BooleanField(required=False, default=True)
    gratuity_applicable = serializers.BooleanField(required=False, default=True)


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ["id", "label", "component_type", "amount", "is_employer_side", "display_order"]


class StatutoryContributionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatutoryContribution
        fields = [
            "id", "kind", "employee_amount", "employer_amount",
            "base_wage", "state", "applied", "exemption_reason",
        ]


class PayslipSummarySerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    period_month = serializers.IntegerField(source="payroll_run.period_month", read_only=True)
    period_year = serializers.IntegerField(source="payroll_run.period_year", read_only=True)
    run_status = serializers.CharField(source="payroll_run.status", read_only=True)
    #: The Finance Head's correction window, answered by the server so the
    #: UI's Delete button and the API's refusal can never disagree.
    deletable_until = serializers.SerializerMethodField()
    within_delete_window = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            "id", "payroll_run", "employee", "employee_name", "employee_code",
            "period_month", "period_year", "run_status", "paid_days", "lop_days",
            "gross_earnings", "total_deductions", "employer_contributions", "net_pay",
            "state", "deletable_until", "within_delete_window",
        ]

    def get_deletable_until(self, obj) -> str:
        return services.payslip_deletable_until(obj).isoformat()

    def get_within_delete_window(self, obj) -> bool:
        from django.utils import timezone as _tz

        return _tz.now() <= services.payslip_deletable_until(obj)


class PayslipDetailSerializer(PayslipSummarySerializer):
    lines = PayslipLineSerializer(many=True, read_only=True)
    statutory_contributions = StatutoryContributionSerializer(many=True, read_only=True)

    class Meta(PayslipSummarySerializer.Meta):
        fields = PayslipSummarySerializer.Meta.fields + [
            "lines", "statutory_contributions", "warnings",
        ]


class PayrollRunSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    location_code = serializers.CharField(source="location.code", read_only=True, default=None)
    payslip_count = serializers.IntegerField(source="payslips.count", read_only=True)
    financial_year = serializers.CharField(read_only=True)

    class Meta:
        model = PayrollRun
        fields = [
            "id", "period_month", "period_year", "financial_year", "location",
            "location_code", "run_type", "sequence", "status", "locked",
            "totals", "notes", "approved_at", "paid_at", "reversed_at",
            "reversal_reason", "payslip_count", "created_at",
        ]


class PayrollRunDetailSerializer(PayrollRunSerializer):
    """
    Adds the server's own answer on whether this run may be approved.

    `blockers` and `can_approve` come from the same function `approve_run()`
    consults, so a disabled button and a refused request always agree.
    """

    blockers = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    payslips = PayslipSummarySerializer(many=True, read_only=True)

    class Meta(PayrollRunSerializer.Meta):
        fields = PayrollRunSerializer.Meta.fields + [
            "rule_sets_used", "blockers", "can_approve", "payslips",
        ]

    def get_blockers(self, obj) -> list[dict]:
        blockers = [
            {"id": f"statutory:{item}", "gate": "statutory", "detail": item}
            for item in services.unverified_rule_sets(obj)
        ]
        if obj.status == PayrollRunStatus.REVIEW and not obj.payslips.exists():
            blockers.append({
                "id": "payslips:none", "gate": "payslips",
                "detail": "The run has no payslips. Process it before approving.",
            })
        actor = self.context["request"].user
        if obj.run_by_id and obj.run_by_id == actor.pk:
            blockers.append({
                "id": "segregation:self", "gate": "segregation",
                "detail": "You processed this run, so a second person must approve it.",
            })
        return blockers

    def get_can_approve(self, obj) -> bool:
        return (
            obj.status == PayrollRunStatus.REVIEW
            and not self.get_blockers(obj)
            and bool(can(self.context["request"].user, Resource.PAYROLL_RUN, Action.APPROVE))
        )


class PayrollAdjustmentSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)

    class Meta:
        model = PayrollAdjustment
        fields = [
            "id", "employee", "employee_name", "employee_code", "kind", "label",
            "amount", "period_month", "period_year", "is_taxable", "is_employer_side",
            "status", "approved_at", "source_ref", "notes",
        ]
        read_only_fields = ["status", "approved_at"]


class InvestmentDeclarationSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)

    class Meta:
        model = InvestmentDeclaration
        fields = [
            "id", "employee", "employee_name", "employee_code", "financial_year",
            "regime", "declarations", "status", "verified_at", "review_note",
        ]
        read_only_fields = ["status", "verified_at", "review_note"]


class StatutoryRuleSetSerializer(serializers.ModelSerializer):
    is_usable_for_payroll = serializers.BooleanField(read_only=True)
    is_tampered = serializers.BooleanField(read_only=True)
    verified_by_email = serializers.CharField(
        source="verified_by.email", read_only=True, default=None
    )

    class Meta:
        model = StatutoryRuleSet
        fields = [
            "id", "statute", "jurisdiction", "regime", "financial_year",
            "effective_from", "effective_to", "rule_version", "parameters",
            "source_citation", "source_url", "retrieved_on", "assumptions",
            "verification_status", "verified_by_email", "verified_at",
            "verification_note", "rejection_reason", "checksum",
            "is_usable_for_payroll", "is_tampered", "was_self_verified",
        ]
        read_only_fields = [
            "checksum", "verification_status", "verified_at", "was_self_verified",
        ]


# ===========================================================================
# Viewsets
# ===========================================================================


class SalaryComponentViewSet(ScopedModelViewSet):
    """The component catalogue — org configuration, not per-employee data."""

    access_resource = Resource.SALARY
    queryset = deferred(SalaryComponent).filter(is_active=True)
    serializer_class = SalaryComponentSerializer
    pagination_class = None
    filterset_fields = ["component_type", "is_wage"]

    def get_queryset(self):
        # The generic person-path scoper would return nothing for everyone,
        # because a component belongs to no one. Requiring more than SELF is the
        # point: every employee holds SALARY/VIEW at SELF for their own payslip,
        # and that must not also hand them the organisation's pay-structure
        # catalogue, which tells them how everyone else's pay is composed.
        scope = can(self.request.user, Resource.SALARY)
        return self.queryset if scope > Scope.SELF else self.queryset.none()

    def perform_create(self, serializer):
        serializer.instance = services.save_component(
            actor=self.request.user, data=serializer.validated_data
        )

    def perform_update(self, serializer):
        serializer.instance = services.save_component(
            actor=self.request.user, data=serializer.validated_data, component=self.get_object()
        )


class SalaryStructureViewSet(ScopedModelViewSet):
    access_resource = Resource.SALARY
    queryset = (
        deferred(SalaryStructure).select_related("employee")
        .prefetch_related("lines__component")
        .filter(is_active=True)
    )
    serializer_class = SalaryStructureSerializer
    filterset_fields = ["employee"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        payload = CreateStructureSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        # Scoped lookup: creating a structure for someone outside your scope
        # must 404, not 403 — a 403 confirms the employee exists.
        employee = scope_queryset(
            Employee.objects.all(), request.user,
            resource=Resource.EMPLOYEE, action=Action.VIEW,
        ).filter(pk=data["employee"]).first()
        if employee is None:
            raise DRFValidationError({"employee": "No such employee."})

        structure = _call(
            services.create_salary_structure,
            actor=request.user,
            employee=employee,
            ctc_annual=data["ctc_annual"],
            valid_from=data["valid_from"],
            revision_reason=data.get("revision_reason", ""),
            lines=data["lines"],
            pf_applicable=data["pf_applicable"],
            esi_applicable=data["esi_applicable"],
            pt_applicable=data["pt_applicable"],
            tds_applicable=data["tds_applicable"],
            gratuity_applicable=data["gratuity_applicable"],
        )
        return Response(
            SalaryStructureSerializer(structure).data, status=status.HTTP_201_CREATED
        )


class PayrollRunViewSet(ScopedModelViewSet):
    access_resource = Resource.PAYROLL_RUN
    queryset = deferred(PayrollRun).select_related("location").filter(is_active=True)
    serializer_class = PayrollRunSerializer
    parser_classes = [JSONParser]
    filterset_fields = ["status", "period_year", "period_month", "run_type", "location"]
    ordering_fields = ["period_year", "period_month", "created_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "process": Action.EDIT,
        "approve": Action.APPROVE,
        "reject": Action.APPROVE,
        "mark_paid": Action.APPROVE,
        "reverse": Action.APPROVE,
        "register": Action.EXPORT,
        "neft": Action.EXPORT,
    }

    def get_serializer_class(self):
        if self.action in ("retrieve", "create"):
            return PayrollRunDetailSerializer
        return PayrollRunSerializer

    def get_queryset(self):
        # A run belongs to a period, not a person, so the person-path scoper
        # does not apply. Visibility is the permission itself.
        base = self.queryset.prefetch_related(
            Prefetch("payslips", queryset=Payslip.objects.select_related("employee"))
        )
        return base if can(self.request.user, Resource.PAYROLL_RUN) else base.none()

    def create(self, request, *args, **kwargs):
        # Validated up front: a missing or non-numeric period must be a 400,
        # not a KeyError. `int()` still guards against "8.5".
        try:
            period_year = int(request.data["period_year"])
            period_month = int(request.data["period_month"])
        except (KeyError, TypeError, ValueError):
            raise DRFValidationError(
                {"period": "period_year and period_month are required integers."}
            )
        # Range-checked HERE, not left to the database: the check constraint
        # would raise IntegrityError, which surfaces as a 500 rather than the
        # 400 an out-of-range month deserves.
        if not 1 <= period_month <= 12:
            raise DRFValidationError({"period_month": "Month must be between 1 and 12."})
        if not 2000 <= period_year <= 2100:
            raise DRFValidationError({"period_year": "Year must be a real payroll year."})
        run = _call(
            services.create_run,
            actor=request.user,
            period_year=period_year,
            period_month=period_month,
            location=self._location(request),
            run_type=request.data.get("run_type", "regular"),
            notes=request.data.get("notes", ""),
        )
        return Response(
            self.get_serializer(run).data, status=status.HTTP_201_CREATED
        )

    def _location(self, request):
        from apps.organization.models import Location

        location_id = request.data.get("location")
        return Location.objects.filter(pk=location_id).first() if location_id else None

    @action(detail=True, methods=["post"])
    def process(self, request, pk=None):
        run = _call(services.process_run, run=self.get_object(), actor=request.user)
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        run = _call(
            services.approve_run, run=self.get_object(), actor=request.user,
            notes=request.data.get("notes", ""),
        )
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        run = _call(
            services.reject_run, run=self.get_object(), actor=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        paid_at = request.data.get("paid_at")
        run = _call(
            services.mark_paid, run=self.get_object(), actor=request.user,
            paid_at=dt.datetime.fromisoformat(paid_at) if paid_at else None,
        )
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        run = _call(
            services.reverse_run, run=self.get_object(), actor=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["get"])
    def register(self, request, pk=None):
        """Salary register as XLSX — the sheet finance reconciles against."""
        run = self.get_object()
        content, filename = outputs.salary_register(run)
        services.audit_event(
            run, actor=request.user, entity_type="PayrollRun", verb="export",
            after={"export": "salary_register", "payslips": run.payslips.count()},
        )
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=["get"])
    def neft(self, request, pk=None):
        """
        Bank advice file.

        Refused for a run that is not approved: this file moves money, and
        producing one from a draft invites paying figures nobody signed off.
        """
        run = self.get_object()
        if run.status not in {PayrollRunStatus.APPROVED, PayrollRunStatus.PAID}:
            raise DRFValidationError({
                "detail": "A bank advice can only be produced for an approved run."
            })
        content, total = outputs.neft_advice(run)
        services.audit_event(
            run, actor=request.user, entity_type="PayrollRun", verb="export",
            after={"export": "neft_advice", "total": str(total)},
        )
        response = HttpResponse(content, content_type="text/tab-separated-values")
        response["Content-Disposition"] = (
            f'attachment; filename="neft-{run.period_year}-{run.period_month:02d}.tsv"'
        )
        return response


class PayslipViewSet(ScopedModelViewSet):
    """
    Payslips, scoped per person.

    An employee sees their own; a manager their team's; finance everyone's —
    all from the same `scope_queryset` path, so there is no second unscoped
    query for anyone to reach.

    Read-only except for ONE write: DELETE, the Finance Head's correction
    window — a freshly generated payslip may be withdrawn with a reason
    within PAYSLIP_DELETE_WINDOW_DAYS, after which it is locked for everyone.
    """

    access_resource = Resource.PAYSLIP
    http_method_names = ["get", "delete", "head", "options"]
    queryset = (
        deferred(Payslip).select_related("employee", "payroll_run", "location")
        .prefetch_related("lines", "statutory_contributions")
        .filter(is_active=True)
    )
    serializer_class = PayslipSummarySerializer
    filterset_fields = ["employee", "payroll_run"]

    def get_serializer_class(self):
        return PayslipDetailSerializer if self.action == "retrieve" else PayslipSummarySerializer

    def destroy(self, request, *args, **kwargs):
        payslip = self.get_object()  # scoped; RBAC already demanded PAYSLIP/DELETE
        reason = ""
        if isinstance(request.data, dict):
            reason = str(request.data.get("reason", "") or "")[:500]
        _call(
            services.delete_payslip,
            payslip=payslip, actor=request.user, reason=reason,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        payslip = self.get_object()          # already scoped
        require(request.user, Resource.PAYSLIP, Action.VIEW)
        content = outputs.payslip_pdf(payslip)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="payslip-{payslip.employee.employee_code}-'
            f'{payslip.payroll_run.period_year}-{payslip.payroll_run.period_month:02d}.pdf"'
        )
        return response


class PayrollAdjustmentViewSet(ScopedModelViewSet):
    access_resource = Resource.PAYROLL_ADJUSTMENT
    queryset = deferred(PayrollAdjustment).select_related("employee").filter(is_active=True)
    serializer_class = PayrollAdjustmentSerializer
    parser_classes = [JSONParser]
    filterset_fields = ["employee", "status", "kind", "period_year", "period_month"]
    access_actions = {"approve": Action.APPROVE}

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Approve an adjustment so a run may pick it up.

        Separate from creating it: an adjustment nobody approved is a proposal,
        and the next run must not pay a proposal.
        """
        from django.utils import timezone

        from .models import AdjustmentStatus

        adjustment = self.get_object()
        if adjustment.status != AdjustmentStatus.DRAFT:
            raise DRFValidationError({"detail": "Only a draft adjustment can be approved."})

        adjustment.status = AdjustmentStatus.APPROVED
        adjustment.approved_by = request.user
        adjustment.approved_at = timezone.now()
        adjustment.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

        services.audit_event(
            adjustment, actor=request.user, entity_type="PayrollAdjustment", verb="approve",
            after={"amount": str(adjustment.amount), "kind": adjustment.kind},
            resource=Resource.PAYROLL_ADJUSTMENT,
        )
        return Response(self.get_serializer(adjustment).data)


class InvestmentDeclarationViewSet(ScopedModelViewSet):
    access_resource = Resource.SALARY
    queryset = deferred(InvestmentDeclaration).select_related("employee").filter(is_active=True)
    serializer_class = InvestmentDeclarationSerializer
    parser_classes = [JSONParser]
    filterset_fields = ["employee", "financial_year", "status"]
    http_method_names = ["get", "post", "head", "options"]
    access_actions = {"review": Action.APPROVE}

    def create(self, request, *args, **kwargs):
        employee = scope_queryset(
            Employee.objects.all(), request.user,
            resource=Resource.EMPLOYEE, action=Action.VIEW,
        ).filter(pk=request.data.get("employee")).first()
        if employee is None:
            raise DRFValidationError({"employee": "No such employee."})

        declaration = _call(
            services.save_declaration,
            actor=request.user,
            employee=employee,
            financial_year=request.data["financial_year"],
            regime=request.data.get("regime", ""),
            declarations=request.data.get("declarations") or {},
        )
        return Response(
            self.get_serializer(declaration).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        declaration = _call(
            services.review_declaration,
            declaration=self.get_object(),
            actor=request.user,
            approve=bool(request.data.get("approve")),
            note=request.data.get("note", ""),
        )
        return Response(self.get_serializer(declaration).data)


class StatutoryRuleSetViewSet(ScopedModelViewSet):
    """
    The statutory rate sets, and their verification workflow.

    APPROVE on this resource is the certification authority — signing off that a
    rate matches the gazette. The matrix grants it to the Finance Head alone:
    Admin can edit a draft but can never certify one, because certification is
    a professional judgement rather than an administrative capability.
    """

    access_resource = Resource.STATUTORY_CONFIG
    queryset = deferred(StatutoryRuleSet).select_related("verified_by").filter(is_active=True)
    serializer_class = StatutoryRuleSetSerializer
    parser_classes = [JSONParser]
    pagination_class = None
    filterset_fields = ["statute", "jurisdiction", "regime", "verification_status"]
    access_actions = {"submit": Action.EDIT, "verify": Action.APPROVE, "reject": Action.APPROVE}

    def get_queryset(self):
        return (
            self.queryset
            if can(self.request.user, Resource.STATUTORY_CONFIG)
            else self.queryset.none()
        )

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        from apps.statutory.services import verification

        rule_set = _call(
            verification.submit_for_verification,
            rule_set=self.get_object(), actor=request.user,
        )
        return Response(self.get_serializer(rule_set).data)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        from apps.statutory.services import verification

        rule_set = _call(
            verification.verify,
            rule_set=self.get_object(),
            actor=request.user,
            note=request.data.get("note", ""),
            self_verification_reason=request.data.get("self_verification_reason", ""),
        )
        return Response(self.get_serializer(rule_set).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        from apps.statutory.services import verification

        rule_set = _call(
            verification.reject,
            rule_set=self.get_object(),
            actor=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(rule_set).data)


class MyPayrollView(APIView):
    """
    Employee self-service: my payslips and my declaration.

    A dedicated route rather than a filter on the payslip list, because "mine"
    must not depend on the client remembering to send the right employee id.
    """

    access_resource = Resource.PAYSLIP

    def get(self, request):
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return Response({"payslips": [], "declaration": None, "structure": None})

        payslips = (
            Payslip.objects.filter(employee=employee, is_active=True)
            .select_related("payroll_run")
            .order_by("-payroll_run__period_year", "-payroll_run__period_month")[:24]
        )
        declaration = (
            InvestmentDeclaration.objects.filter(employee=employee).order_by("-financial_year").first()
        )
        structure = services.structure_in_force(employee, dt.date.today())

        from .models import EmployeePackage, PackageStatus

        package = (
            EmployeePackage.objects.filter(
                employee=employee, is_active=True,
                status__in=(PackageStatus.ACTIVE, PackageStatus.ON_HOLD),
            )
            .prefetch_related("periods", "deferrals__released_in_adjustment")
            .first()
        )

        return Response({
            "payslips": PayslipSummarySerializer(payslips, many=True).data,
            "declaration": (
                InvestmentDeclarationSerializer(declaration).data if declaration else None
            ),
            # Their own salary structure — SELF scope on SALARY, which every
            # role holds for their own record.
            "structure": SalaryStructureSerializer(structure).data if structure else None,
            # Their own package, internal notes stripped by the serializer's
            # SELF-scope rule.
            "package": (
                EmployeePackageSerializer(package, context={"request": request}).data
                if package else None
            ),
        })




# ===========================================================================
# Custom packages
# ===========================================================================


class PackagePeriodSerializer(serializers.ModelSerializer):
    months = serializers.IntegerField(read_only=True)
    monthly_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        from .models import PackagePeriod

        model = PackagePeriod
        fields = ["id", "order", "label", "start_date", "end_date", "amount",
                  "months", "monthly_amount"]
        read_only_fields = ["id", "order"]


class PackageDeferralSerializer(serializers.ModelSerializer):
    effective_status = serializers.CharField(read_only=True)
    decided_by_email = serializers.CharField(
        source="decided_by.email", read_only=True, default=None
    )

    class Meta:
        from .models import PackageDeferral

        model = PackageDeferral
        fields = ["id", "label", "amount", "condition_type", "condition_months",
                  "eligible_on", "condition_note", "status", "effective_status",
                  "decided_by_email", "decided_at", "decision_reason",
                  "released_in_adjustment"]
        read_only_fields = ["id", "status", "effective_status", "decided_at",
                            "decision_reason", "released_in_adjustment"]


class EmployeePackageSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    periods = PackagePeriodSerializer(many=True, read_only=True)
    deferrals = PackageDeferralSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        from .models import EmployeePackage

        model = EmployeePackage
        fields = ["id", "employee", "employee_name", "employee_code",
                  "total_amount", "package_type", "start_date", "end_date",
                  "status", "notes", "supersedes",
                  "bond_start_date", "bond_end_date", "bond_required_months",
                  "activated_at", "periods", "deferrals", "summary"]
        read_only_fields = ["id", "status", "supersedes", "activated_at"]

    def get_summary(self, obj) -> dict:
        from .services import packages as package_services

        return package_services.summary(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Internal HR/Finance notes never reach a SELF-scope viewer — the
        # employee sees their own numbers, not the file about them.
        request = self.context.get("request")
        if request is not None:
            from core.access.engine import can

            if can(request.user, Resource.PACKAGE) <= Scope.SELF:
                data.pop("notes", None)
        return data


class PackageWriteSerializer(serializers.Serializer):
    employee = serializers.UUIDField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    package_type = serializers.CharField(required=False, default="period_wise")
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    bond_start_date = serializers.DateField(required=False, allow_null=True, default=None)
    bond_end_date = serializers.DateField(required=False, allow_null=True, default=None)
    bond_required_months = serializers.IntegerField(required=False, allow_null=True, default=None)
    periods = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    deferrals = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class EmployeePackageViewSet(ScopedModelViewSet):
    """
    Custom package schedules.

    WRITES (create, edit, activate, releases) are HR Head / Finance Head only
    — the matrix grants PACKAGE writes to exactly those two roles. The
    Payroll Executive holds VIEW at ALL; employees hold VIEW at SELF and get
    their own package with the internal notes stripped.
    """

    access_resource = Resource.PACKAGE
    serializer_class = EmployeePackageSerializer
    filterset_fields = ["employee", "status", "package_type"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    access_actions = {
        "activate": Action.APPROVE,
        "set_status": Action.EDIT,
        "revise": Action.EDIT,
        "decide": Action.APPROVE,
        "reschedule": Action.EDIT,
        "alerts": Action.VIEW,
    }

    def get_queryset(self):
        from .models import EmployeePackage

        return scope_queryset(
            EmployeePackage.objects.select_related("employee")
            .prefetch_related("periods", "deferrals__released_in_adjustment")
            .filter(is_active=True),
            self.request.user, resource=Resource.PACKAGE, action=Action.VIEW,
        )

    def list(self, request, *args, **kwargs):
        from .services import packages as package_services

        # Listing is when eligibility is noticed — dates that have arrived
        # surface as ELIGIBLE before anyone reads stale statuses.
        package_services.refresh_eligibility()
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        from .services import packages as package_services

        payload = PackageWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        employee = scope_queryset(
            Employee.objects.all(), request.user,
            resource=Resource.EMPLOYEE, action=Action.VIEW,
        ).filter(pk=payload.validated_data["employee"]).first()
        if employee is None:
            raise DRFValidationError({"employee": "No such employee."})
        package = _call(
            package_services.save_package,
            actor=request.user, employee=employee, data=payload.validated_data,
        )
        return Response(self.get_serializer(package).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        from .services import packages as package_services

        package = self.get_object()
        payload = PackageWriteSerializer(data={**request.data, "employee": str(package.employee_id)})
        payload.is_valid(raise_exception=True)
        package = _call(
            package_services.save_package,
            actor=request.user, employee=package.employee,
            data=payload.validated_data, package=package,
        )
        return Response(self.get_serializer(self._fresh(package)).data)

    def _fresh(self, package):
        """Re-read with prefetches — actions mutate rows the cached copy holds."""
        return self.get_queryset().get(pk=package.pk)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        from .services import packages as package_services

        package = _call(package_services.activate, actor=request.user, package=self.get_object())
        return Response(self.get_serializer(self._fresh(package)).data)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        from .services import packages as package_services

        package = _call(
            package_services.set_status,
            actor=request.user, package=self.get_object(),
            status=request.data.get("status", ""), reason=request.data.get("reason", ""),
        )
        return Response(self.get_serializer(package).data)

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        from .services import packages as package_services

        package = self.get_object()
        payload = PackageWriteSerializer(data={**request.data, "employee": str(package.employee_id)})
        payload.is_valid(raise_exception=True)
        replacement = _call(
            package_services.revise,
            actor=request.user, package=package, data=payload.validated_data,
        )
        return Response(self.get_serializer(replacement).data, status=status.HTTP_201_CREATED)

    def _deferral(self, package, deferral_id):
        from .models import PackageDeferral

        deferral = PackageDeferral.objects.filter(
            pk=deferral_id, package=package, is_active=True
        ).first()
        if deferral is None:
            raise DRFValidationError({"deferral": "No such deferred amount on this package."})
        return deferral

    @action(detail=True, methods=["post"],
            url_path=r"deferrals/(?P<deferral_id>[^/.]+)/decide")
    def decide(self, request, pk=None, deferral_id=None):
        """Approve (into a payroll period), reject, or hold a release."""
        from .services import packages as package_services

        deferral = self._deferral(self.get_object(), deferral_id)
        deferral = _call(
            package_services.decide_release,
            actor=request.user, deferral=deferral,
            decision=request.data.get("decision", ""),
            period_year=request.data.get("period_year"),
            period_month=request.data.get("period_month"),
            reason=request.data.get("reason", ""),
        )
        return Response(PackageDeferralSerializer(deferral).data)

    @action(detail=True, methods=["post"],
            url_path=r"deferrals/(?P<deferral_id>[^/.]+)/reschedule")
    def reschedule(self, request, pk=None, deferral_id=None):
        from .services import packages as package_services

        payload = serializers.DateField().to_internal_value(request.data.get("eligible_on"))
        deferral = _call(
            package_services.reschedule,
            actor=request.user, deferral=self._deferral(self.get_object(), deferral_id),
            eligible_on=payload, reason=request.data.get("reason", ""),
        )
        return Response(PackageDeferralSerializer(deferral).data)

    @action(detail=False, methods=["get"])
    def alerts(self, request):
        """The Payroll page's banner: how many releases await a decision."""
        from .models import DeferralStatus
        from .services import packages as package_services

        package_services.refresh_eligibility()
        rows = self.get_queryset().filter(
            deferrals__status=DeferralStatus.ELIGIBLE, deferrals__is_active=True
        ).distinct()
        return Response({
            "eligible": [
                {
                    "package": str(row.pk),
                    "employee_name": row.employee.full_name,
                    "employee_code": row.employee.employee_code,
                    "amounts": [
                        {"id": str(d.pk), "label": d.label, "amount": str(d.amount),
                         "eligible_on": str(d.eligible_on)}
                        for d in row.deferrals.filter(status=DeferralStatus.ELIGIBLE)
                    ],
                }
                for row in rows
            ]
        })


router = DefaultRouter()
router.register("payroll/components", SalaryComponentViewSet, basename="salary-component")
router.register("payroll/structures", SalaryStructureViewSet, basename="salary-structure")
router.register("payroll/packages", EmployeePackageViewSet, basename="employee-package")
router.register("payroll/runs", PayrollRunViewSet, basename="payroll-run")
router.register("payroll/adjustments", PayrollAdjustmentViewSet, basename="payroll-adjustment")
router.register("payroll/declarations", InvestmentDeclarationViewSet, basename="declaration")
router.register("payroll/rule-sets", StatutoryRuleSetViewSet, basename="statutory-rule-set")
router.register("payslips", PayslipViewSet, basename="payslip")

payroll_patterns = router.urls
