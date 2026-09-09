"""
Offboarding API.

Every mutating route is a thin wrapper over a service. In particular, nothing
here writes `stage` or `Employee.status` — the services own both, and the
lifecycle service owns the latter absolutely.

`GET /exits/{id}/` includes `blockers`, computed server-side by
`exit_blockers()`. The UI renders that list; it does not compute its own. The
same function guards approval and completion, so what the user is shown and
what the server enforces cannot drift.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.http import Http404
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.employees.models import Employee
from core.access import Action, Resource
from core.api.mixins import ServiceCreatedOnly
from core.access.drf import ScopedModelViewSet, ScopedReadOnlyModelViewSet

from . import services
from .models import (
    ClearanceTemplate,
    ExitClearanceItem,
    ExitInterview,
    ExitWorkflow,
    FinalSettlement,
    ResignationRequest,
)


from core.api.serializers import ScopedRelationsMixin

def _call(fn, **kwargs):
    """Run a service, translating its Django ValidationError into a DRF 400."""
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


# ===========================================================================
# Serializers
# ===========================================================================


class ResignationRequestSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=None
    )
    reviewed_by_email = serializers.CharField(
        source="reviewed_by.email", read_only=True, default=None
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = ResignationRequest
        fields = [
            "id", "employee", "employee_name", "employee_code", "department_name",
            "resignation_date", "requested_last_working_date", "reason", "employee_comments",
            "submitted_at", "submitted_by", "status",
            "reviewed_by", "reviewed_by_email", "reviewed_at", "review_notes",
            "approved_last_working_date", "is_open",
        ]
        read_only_fields = [
            "status", "submitted_at", "submitted_by", "reviewed_by", "reviewed_at",
            "review_notes", "approved_last_working_date",
        ]


class SubmitResignationSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.filter(is_active=True), required=False, allow_null=True
    )
    requested_last_working_date = serializers.DateField()
    reason = serializers.CharField()
    comments = serializers.CharField(required=False, allow_blank=True, default="")


class ReviewResignationSerializer(serializers.Serializer):
    approved_last_working_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    notice_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)


class RejectResignationSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=services.MIN_REASON_LENGTH)


class ExitClearanceItemSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(
        source="assigned_to.full_name", read_only=True, default=None
    )
    completed_by_email = serializers.CharField(
        source="completed_by.email", read_only=True, default=None
    )
    is_done = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    has_evidence = serializers.SerializerMethodField()

    class Meta:
        model = ExitClearanceItem
        fields = [
            "id", "exit_workflow", "title", "description", "category", "owner",
            "assigned_to", "assigned_to_name", "is_required", "requires_evidence",
            "due_date", "order", "status", "has_evidence",
            "completed_at", "completed_by", "completed_by_email", "notes",
            "is_done", "is_overdue",
        ]
        read_only_fields = fields

    def get_has_evidence(self, obj) -> bool:
        return bool(obj.evidence)


class FinalSettlementSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    gross_earnings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_deductions = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    net_payable = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    cleared_by_email = serializers.CharField(
        source="cleared_by.email", read_only=True, default=None
    )

    class Meta:
        model = FinalSettlement
        fields = [
            "id", "exit_workflow", "final_working_date",
            "pending_salary", "leave_encashment", "bonus_or_incentive", "other_earnings",
            "outstanding_advances", "notice_shortfall_recovery", "asset_recovery",
            "other_deductions", "notes", "status",
            "prepared_by", "cleared_by", "cleared_by_email", "cleared_at", "paid_at",
            "gross_earnings", "total_deductions", "net_payable",
        ]
        read_only_fields = ["status", "prepared_by", "cleared_by", "cleared_at", "paid_at"]


class ExitInterviewSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    conducted_by_email = serializers.CharField(
        source="conducted_by.email", read_only=True, default=None
    )
    is_conducted = serializers.BooleanField(read_only=True)

    class Meta:
        model = ExitInterview
        fields = [
            "id", "exit_workflow", "conducted_by", "conducted_by_email", "conducted_at",
            "primary_reason", "employee_feedback", "manager_feedback", "workplace_feedback",
            "improvement_suggestions", "would_recommend_employer", "rehire_eligibility",
            "hr_notes", "is_conducted",
        ]
        read_only_fields = ["conducted_by", "conducted_at", "is_conducted"]


class ExitWorkflowListSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=None
    )
    employee_status = serializers.CharField(source="employee.status", read_only=True)
    completed_items = serializers.SerializerMethodField()
    total_items = serializers.SerializerMethodField()

    class Meta:
        model = ExitWorkflow
        fields = [
            "id", "employee", "employee_name", "employee_code", "department_name",
            "employee_status", "exit_type", "stage",
            "notice_start_date", "notice_days",
            "expected_last_working_date", "actual_last_working_date",
            "notice_waived", "early_release_approved",
            "initiated_at", "approved_at", "completed_at",
            "completed_items", "total_items",
        ]

    def get_completed_items(self, obj) -> int:
        return obj.progress[0]

    def get_total_items(self, obj) -> int:
        return obj.progress[1]


class ExitWorkflowDetailSerializer(ExitWorkflowListSerializer):
    clearance_items = ExitClearanceItemSerializer(many=True, read_only=True)
    settlement = FinalSettlementSerializer(read_only=True)
    interview = ExitInterviewSerializer(read_only=True)
    resignation = ResignationRequestSerializer(read_only=True)
    #: Computed server-side. The UI reports this rather than deciding for itself.
    blockers = serializers.SerializerMethodField()
    can_complete = serializers.SerializerMethodField()
    unreturned_assets = serializers.SerializerMethodField()

    class Meta(ExitWorkflowListSerializer.Meta):
        fields = ExitWorkflowListSerializer.Meta.fields + [
            "resignation", "reason",
            "notice_waived_by", "notice_waived_at", "notice_waiver_reason",
            "early_release_by", "early_release_reason",
            "approved_by", "approval_notes", "cancelled_reason",
            "clearance_items", "settlement", "interview",
            "blockers", "can_complete", "unreturned_assets",
        ]

    def get_blockers(self, obj) -> list:
        return services.exit_blockers(obj)

    def get_can_complete(self, obj) -> bool:
        return not services.exit_blockers(obj)

    def get_unreturned_assets(self, obj) -> list:
        return [
            {
                "allocation_id": str(allocation.pk),
                "asset_tag": allocation.asset.asset_tag,
                "asset_name": allocation.asset.name,
                "category": allocation.asset.category.name,
            }
            for allocation in obj.unreturned_assets
        ]


class StartExitSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.filter(is_active=True))
    exit_type = serializers.CharField()
    last_working_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    notice_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)


class ExceptionSerializer(serializers.Serializer):
    """Notice waiver and early release both need a substantial reason."""

    reason = serializers.CharField(min_length=services.MIN_EXCEPTION_REASON_LENGTH)
    new_last_working_date = serializers.DateField(required=False, allow_null=True)


class UpdateNoticeSerializer(serializers.Serializer):
    last_working_date = serializers.DateField(required=False, allow_null=True)
    notice_days = serializers.IntegerField(required=False, allow_null=True, min_value=0)


class ClearanceActionSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    evidence = serializers.FileField(required=False, allow_null=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5)


class ApprovalSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class CompleteExitSerializer(serializers.Serializer):
    actual_last_working_date = serializers.DateField(required=False, allow_null=True)


class ClearanceTemplateSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = ClearanceTemplate
        fields = ["id", "name", "description", "department", "exit_type", "is_default", "item_count"]


# ===========================================================================
# Viewsets
# ===========================================================================


class ResignationViewSet(ScopedModelViewSet):
    access_resource = Resource.OFFBOARDING
    queryset = ResignationRequest.objects.select_related(
        "employee", "employee__department", "reviewed_by"
    ).filter(is_active=True)
    serializer_class = ResignationRequestSerializer
    filterset_fields = ["status", "employee"]
    ordering_fields = ["submitted_at", "requested_last_working_date"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "approve": Action.APPROVE,
        "reject": Action.APPROVE,
        "withdraw": Action.CREATE,
        "pending": Action.VIEW,
    }

    def create(self, request, *args, **kwargs):
        """
        Submit a resignation.

        Creates a REQUEST. The employee's status does not change here — HR must
        approve, and that approval is what moves them, through the lifecycle
        service.
        """
        payload = SubmitResignationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        employee = payload.validated_data.get("employee") or getattr(
            request.user, "employee", None
        )
        if employee is None:
            raise DRFValidationError(
                {
                    "employee": (
                        "This account has no employee record, so a resignation must name "
                        "the employee it is for."
                    )
                }
            )

        resignation = _call(
            services.submit_resignation,
            employee=employee,
            actor=request.user,
            requested_last_working_date=payload.validated_data["requested_last_working_date"],
            reason=payload.validated_data["reason"],
            comments=payload.validated_data.get("comments", ""),
        )
        return Response(
            ResignationRequestSerializer(resignation).data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """HR's review queue."""
        queryset = self.filter_queryset(self.get_queryset().filter(status="submitted"))
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payload = ReviewResignationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.approve_resignation,
            request=self.get_object(),
            actor=request.user,
            approved_last_working_date=payload.validated_data.get("approved_last_working_date"),
            notes=payload.validated_data.get("notes", ""),
            notice_days=payload.validated_data.get("notice_days"),
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        payload = RejectResignationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        resignation = _call(
            services.reject_resignation,
            request=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(ResignationRequestSerializer(resignation).data)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        resignation = _call(
            services.withdraw_resignation, request=self.get_object(), actor=request.user
        )
        return Response(ResignationRequestSerializer(resignation).data)


class ExitWorkflowViewSet(ScopedModelViewSet):
    access_resource = Resource.OFFBOARDING
    queryset = (
        ExitWorkflow.objects.select_related(
            "employee", "employee__department", "resignation", "settlement", "interview"
        )
        .prefetch_related("clearance_items__assigned_to")
        .filter(is_active=True)
    )
    serializer_class = ExitWorkflowListSerializer
    filterset_fields = ["stage", "exit_type", "employee"]
    ordering_fields = ["initiated_at", "expected_last_working_date"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "waive_notice": Action.APPROVE,
        "early_release": Action.APPROVE,
        "update_notice": Action.EDIT,
        "approve": Action.APPROVE,
        "complete": Action.APPROVE,
        "cancel": Action.APPROVE,
        "settlement": Action.EDIT,
        "clear_settlement": Action.APPROVE,
        "interview": Action.EDIT,
    }

    def get_serializer_class(self):
        if self.action in ("retrieve", "create"):
            return ExitWorkflowDetailSerializer
        return ExitWorkflowListSerializer

    def create(self, request, *args, **kwargs):
        """
        Start an exit directly — the termination path.

        A resignation reaches the same service through `/resignations/{id}/approve/`,
        so both lifecycles in the spec run one implementation.
        """
        payload = StartExitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(services.start_exit, actor=request.user, **payload.validated_data)
        return Response(
            ExitWorkflowDetailSerializer(workflow).data, status=status.HTTP_201_CREATED
        )

    # -------------------------------------------------- notice

    @action(detail=True, methods=["post"], url_path="waive-notice")
    def waive_notice(self, request, pk=None):
        payload = ExceptionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.waive_notice,
            workflow=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
            new_last_working_date=payload.validated_data.get("new_last_working_date"),
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)

    @action(detail=True, methods=["post"], url_path="early-release")
    def early_release(self, request, pk=None):
        payload = ExceptionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if not payload.validated_data.get("new_last_working_date"):
            raise DRFValidationError(
                {"new_last_working_date": "An early release must name the new last working day."}
            )
        workflow = _call(
            services.approve_early_release,
            workflow=self.get_object(),
            actor=request.user,
            new_last_working_date=payload.validated_data["new_last_working_date"],
            reason=payload.validated_data["reason"],
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)

    @action(detail=True, methods=["post"], url_path="update-notice")
    def update_notice(self, request, pk=None):
        payload = UpdateNoticeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.update_notice,
            workflow=self.get_object(),
            actor=request.user,
            last_working_date=payload.validated_data.get("last_working_date"),
            notice_days=payload.validated_data.get("notice_days"),
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)

    # -------------------------------------------------- settlement

    @action(detail=True, methods=["post"])
    def settlement(self, request, pk=None):
        workflow = self.get_object()
        record = getattr(workflow, "settlement", None)
        if record is None:
            raise Http404("This exit has no settlement record.")

        payload = FinalSettlementSerializer(record, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        updated = _call(
            services.update_settlement,
            settlement=record,
            actor=request.user,
            **payload.validated_data,
        )
        return Response(FinalSettlementSerializer(updated).data)

    @action(detail=True, methods=["post"], url_path="clear-settlement")
    def clear_settlement(self, request, pk=None):
        workflow = self.get_object()
        record = getattr(workflow, "settlement", None)
        if record is None:
            raise Http404("This exit has no settlement record.")

        payload = ApprovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        updated = _call(
            services.clear_settlement,
            settlement=record,
            actor=request.user,
            notes=payload.validated_data.get("notes", ""),
        )
        return Response(FinalSettlementSerializer(updated).data)

    # -------------------------------------------------- interview

    @action(detail=True, methods=["post"])
    def interview(self, request, pk=None):
        payload = ExitInterviewSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        record = _call(
            services.record_exit_interview,
            workflow=self.get_object(),
            actor=request.user,
            **payload.validated_data,
        )
        return Response(ExitInterviewSerializer(record).data)

    # -------------------------------------------------- approval

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Refused while any gate is open; the 400 names every blocker."""
        payload = ApprovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.approve_exit,
            workflow=self.get_object(),
            actor=request.user,
            notes=payload.validated_data.get("notes", ""),
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """The final transition to EXITED, through the lifecycle service."""
        payload = CompleteExitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.complete_exit,
            workflow=self.get_object(),
            actor=request.user,
            actual_last_working_date=payload.validated_data.get("actual_last_working_date"),
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        workflow = _call(
            services.cancel_exit,
            workflow=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(ExitWorkflowDetailSerializer(workflow).data)


class ExitClearanceItemViewSet(ServiceCreatedOnly, ScopedModelViewSet):
    """
    Clearance lines.

    Scoped through the exit's employee, so a manager sees their leavers' items
    and HR sees everyone's. Ownership of an individual line is checked by the
    service, not here — a finance officer holds the same OFFBOARDING/EDIT that
    HR does, and only the service knows which line is whose.
    """

    access_resource = Resource.OFFBOARDING
    queryset = ExitClearanceItem.objects.select_related(
        "exit_workflow", "exit_workflow__employee", "assigned_to", "completed_by"
    ).filter(is_active=True)
    serializer_class = ExitClearanceItemSerializer
    filterset_fields = ["exit_workflow", "status", "category", "owner", "assigned_to"]
    ordering_fields = ["due_date", "order"]
    #: JSON as well as multipart. Most clearance items carry no evidence, and
    #: forcing a multipart body for "I have done this" would make the common
    #: case awkward — and, worse, return 415 before the permission check ran,
    #: so a caller who was not allowed to act would be told the wrong thing.
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {"complete": Action.EDIT, "waive": Action.APPROVE, "mine": Action.VIEW}

    def get_queryset(self):
        """
        Scoped through the exit's employee.

        Not `scope_queryset` directly: the registry's OFFBOARDING spec walks
        `employee` on ExitWorkflow, and a clearance item reaches the employee
        one hop further along. The SCOPE still comes from the access engine —
        only the path is local, which is the same arrangement the recruitment
        pipeline uses for rows that hang off a job rather than a person.
        """
        from core.access import can, get_context
        from core.access.catalog import Scope
        from core.access.engine import apply_org_predicate

        scope = can(self.request.user, Resource.OFFBOARDING, Action.VIEW)
        context = get_context(self.request.user)
        # Hand-rolled scoper: routed through the shared tenant predicate so
        # it cannot drift from scope_queryset(). Without this the branch
        # below returns the whole TABLE at Scope.ALL, not the whole
        # organization.
        queryset = apply_org_predicate(self.queryset, context)

        if not scope:
            return queryset.none()
        if scope == Scope.ALL:
            return queryset
        if context.employee_id is None:
            return self.queryset.none()

        if scope == Scope.DEPARTMENT:
            if not context.department_ids:
                return self.queryset.none()
            return self.queryset.filter(
                exit_workflow__employee__department_id__in=context.department_ids
            )
        if scope == Scope.TEAM:
            ids = context.reporting_tree_ids
            if not ids:
                return self.queryset.none()
            return self.queryset.filter(exit_workflow__employee_id__in=ids)

        # SELF: the caller's own exit, plus any item assigned to them.
        return self.queryset.filter(
            models.Q(exit_workflow__employee_id=context.employee_id)
            | models.Q(assigned_to_id=context.employee_id)
        )

    @action(detail=False, methods=["get"])
    def mine(self, request):
        """Clearance work assigned to the caller, across every open exit."""
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return Response([])
        queryset = self.get_queryset().filter(
            assigned_to=employee, status__in=["pending", "in_progress", "blocked"]
        )
        return Response(self.get_serializer(queryset, many=True).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        payload = ClearanceActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = _call(
            services.complete_clearance_item,
            item=self.get_object(),
            actor=request.user,
            notes=payload.validated_data.get("notes", ""),
            evidence=payload.validated_data.get("evidence"),
        )
        return Response(ExitClearanceItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = _call(
            services.waive_clearance_item,
            item=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(ExitClearanceItemSerializer(item).data)


class ClearanceTemplateViewSet(ScopedReadOnlyModelViewSet):
    access_resource = Resource.OFFBOARDING
    pagination_class = None
    queryset = ClearanceTemplate.objects.filter(is_active=True).prefetch_related("items")
    serializer_class = ClearanceTemplateSerializer

    def get_queryset(self):
        from core.access import can

        # Templates are org configuration, not per-employee data; the generic
        # person-path scoper would return nothing for everyone.
        return self.queryset if can(self.request.user, Resource.OFFBOARDING) else self.queryset.none()


router = DefaultRouter()
router.register("resignations", ResignationViewSet, basename="resignation")
router.register("exits", ExitWorkflowViewSet, basename="exit-workflow")
router.register("exit-clearance-items", ExitClearanceItemViewSet, basename="exit-clearance-item")
router.register("clearance-templates", ClearanceTemplateViewSet, basename="clearance-template")

offboarding_patterns = router.urls
