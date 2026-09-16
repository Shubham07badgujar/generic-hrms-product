"""
Leave API.

Same architecture as every other module: ScopedModelViewSets over the access
registry, service calls for anything that moves a balance, and no approver
ids, day counts or balances accepted from a client — the serializer for a new
request carries dates, a type and a reason, and nothing else.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from core.access import Action, Resource, require
from core.access.drf import ScopedAPIView, ScopedModelViewSet, ScopedQuerysetMixin

from . import services
from .models import (
    Holiday,
    HolidayCalendar,
    HolidayWork,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    LeaveSettings,
    LeaveStatus,
    LeaveType,
    ShortLeave,
)

# ---------------------------------------------------------------- serializers


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin

class LeaveTypeSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = ["id", "code", "name", "description", "is_paid", "order"]


class LeavePolicySerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    department_name = serializers.CharField(
        source="department.name", read_only=True, default=None
    )

    class Meta:
        model = LeavePolicy
        fields = [
            "id", "leave_type", "leave_type_name", "name",
            "department", "department_name", "employment_type",
            "annual_allocation", "accrual_per_month", "carry_forward", "carry_forward_limit",
            "allow_half_day", "requires_attachment", "min_notice_days",
            "max_consecutive_days", "allow_negative_balance",
            "min_service_months", "is_encashable", "approval_authority",
        ]


class HolidaySerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ["id", "calendar", "date", "name", "is_optional"]


class HolidayCalendarSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)
    holidays = HolidaySerializer(many=True, read_only=True)

    class Meta:
        model = HolidayCalendar
        fields = ["id", "name", "location", "location_name", "weekly_off", "holidays"]


class LeaveBalanceSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    leave_type_code = serializers.CharField(source="leave_type.code", read_only=True)
    is_paid = serializers.BooleanField(source="leave_type.is_paid", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    available = serializers.DecimalField(max_digits=6, decimal_places=1, read_only=True)

    class Meta:
        model = LeaveBalance
        fields = [
            "id", "employee", "employee_name", "employee_code",
            "leave_type", "leave_type_name", "leave_type_code", "is_paid",
            "year", "allocated", "carried_forward", "used", "pending", "available",
        ]


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    #: What the row is CALLED: probation leave is unpaid whatever type was
    #: chosen, and says so.
    display_type = serializers.SerializerMethodField()

    def get_display_type(self, row) -> str:
        if row.probation_unpaid:
            return "Unpaid Leave – Probation Period"
        return row.leave_type.name

    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=""
    )
    designation_title = serializers.CharField(
        source="employee.designation.title", read_only=True, default=""
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    is_paid = serializers.BooleanField(source="leave_type.is_paid", read_only=True)
    decided_by_email = serializers.CharField(
        source="decided_by.email", read_only=True, default=""
    )
    has_attachment = serializers.SerializerMethodField()

    def get_has_attachment(self, row) -> bool:
        # Never the storage URL — attachments are served through the
        # authorised download action only.
        return bool(row.attachment)

    manager_decided_by_email = serializers.CharField(
        source="manager_decided_by.email", read_only=True, default=""
    )
    #: Who holds the request RIGHT NOW — the label the status chip shows.
    pending_with = serializers.SerializerMethodField()
    #: Whether it is the CALLER'S turn to decide — the approvals screen only
    #: renders Approve/Reject when this is true; anyone else sees who holds it.
    can_decide = serializers.SerializerMethodField()

    def get_can_decide(self, row) -> bool:
        http = self.context.get("request")
        if http is None or not getattr(http.user, "is_authenticated", False):
            return False
        return services.may_decide(http.user, row)

    def get_pending_with(self, row) -> str:
        if row.status != "pending":
            return ""
        if row.approval_stage == "manager":
            manager = row.employee.reporting_manager
            name = manager.full_name if manager else "Reporting manager"
            return f"Reporting manager ({name})"
        return "Admin" if row.approval_band == "admin" else "HR Head"

    class Meta:
        model = LeaveRequest
        fields = [
            "id", "employee", "employee_name", "employee_code",
            "department_name", "designation_title",
            "leave_type", "leave_type_name", "is_paid",
            "start_date", "end_date", "half_day", "days", "reason",
            "has_attachment", "status", "approval_band", "approval_stage",
            "pending_with", "can_decide", "is_emergency", "probation_unpaid", "display_type",
            "manager_decided_by_email", "manager_decided_at", "manager_note",
            "decided_by_email", "decided_at", "decision_reason", "created_at",
        ]
        read_only_fields = fields


class LeaveApplySerializer(ScopedRelationsMixin, serializers.Serializer):
    """Dates, type, reason. The server computes and decides everything else."""

    leave_type = serializers.PrimaryKeyRelatedField(
        queryset=LeaveType.objects.filter(is_active=True)
    )
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    half_day = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(max_length=2000)
    attachment = serializers.FileField(required=False, allow_null=True)
    is_emergency = serializers.BooleanField(required=False, default=False)


class LeaveDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


def _call(fn, **kwargs):
    try:
        return fn(**kwargs)
    except ValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


# -------------------------------------------------------------------- views


def _probation_applicant(request):
    """
    The caller's own employee, when the probation-unpaid policy applies to
    them. During probation every leave is recorded unpaid whatever type was
    chosen — so offering paid types and balances to the applicant is not a
    convenience, it is a misleading promise. HR reading OTHER people's data
    is never affected by this.
    """
    from apps.employees.models import EmployeeStatus

    from .models import LeaveSettings

    employee = getattr(request.user, "employee", None)
    if employee is None or employee.status != EmployeeStatus.ON_PROBATION:
        return None
    if not LeaveSettings.get_solo().probation_leave_unpaid:
        return None
    return employee


class LeaveTypeViewSet(ScopedModelViewSet):
    """Writes are configuration (Admin and HR Head); reads are for everyone.

    Types are reference data — a name and a paid flag — that every applicant
    needs to fill the form, so reads are gated by LEAVE_REQUEST, the grant
    self-service carries, rather than LEAVE_POLICY.
    """

    access_resource = Resource.LEAVE_POLICY
    access_resources = {
        "list": Resource.LEAVE_REQUEST,
        "retrieve": Resource.LEAVE_REQUEST,
    }
    queryset = LeaveType.objects.filter(is_active=True)
    serializer_class = LeaveTypeSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # No row scoping on reads: types carry nothing personal, and the
        # LEAVE_REQUEST spec's employee path does not exist on this model.
        if self.action in ("list", "retrieve"):
            rows = LeaveType.objects.filter(is_active=True)
            # A probationer's apply form offers only what they can actually
            # take: unpaid leave. The service coerces paid choices to unpaid
            # anyway — this makes the form say so up front.
            if _probation_applicant(self.request) is not None:
                rows = rows.filter(is_paid=False)
            return rows
        return super().get_queryset()


class LeavePolicyViewSet(ScopedModelViewSet):
    access_resource = Resource.LEAVE_POLICY
    queryset = LeavePolicy.objects.select_related("leave_type", "department").filter(
        is_active=True
    )
    serializer_class = LeavePolicySerializer
    filterset_fields = ["leave_type", "department"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]


class HolidayCalendarViewSet(ScopedModelViewSet):
    access_resource = Resource.LEAVE_POLICY
    queryset = HolidayCalendar.objects.prefetch_related("holidays").filter(is_active=True)
    serializer_class = HolidayCalendarSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]


class HolidayViewSet(ScopedModelViewSet):
    access_resource = Resource.LEAVE_POLICY
    queryset = Holiday.objects.select_related("calendar").filter(is_active=True)
    serializer_class = HolidaySerializer
    filterset_fields = ["calendar"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]


class LeaveBalanceViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """Balances: everyone their own; HR everyone's. Scoped like requests."""

    access_resource = Resource.LEAVE_REQUEST
    #: Retired types keep their balance history in the DB, but the cards on
    #: the leave page show the CURRENT policy only.
    queryset = LeaveBalance.objects.select_related(
        "employee", "employee__department", "leave_type"
    ).filter(is_active=True, leave_type__is_active=True)
    serializer_class = LeaveBalanceSerializer
    filterset_fields = ["year", "leave_type", "employee"]

    def get_queryset(self):
        rows = super().get_queryset()
        # A probationer's OWN paid balances are hidden: leave taken now is
        # unpaid whatever they pick, so a "1.6 days available" card would be
        # a promise the policy does not keep. The rows still exist and keep
        # accruing — they surface the day probation ends — and HR reading the
        # probationer's balances (employee=<them>) is untouched because only
        # the CALLER's own rows are excluded.
        probationer = _probation_applicant(self.request)
        if probationer is not None:
            rows = rows.exclude(employee=probationer, leave_type__is_paid=True)
        return rows

    def list(self, request, *args, **kwargs):
        # Balance rows are created on first touch, and opening the leave page
        # is a first touch: materialise the caller's own rows so a person who
        # has never applied sees their allocation instead of nothing.
        employee = getattr(request.user, "employee", None)
        if employee is not None:
            services.ensure_balances(employee)
        return super().list(request, *args, **kwargs)


class LeaveRequestViewSet(ScopedModelViewSet):
    """
    Scoped by LEAVE_REQUEST: employees see their own rows, HR sees all.
    Creation goes through `apply_leave` — always for the CALLER's own
    employee; there is no way to file leave for somebody else.
    """

    access_resource = Resource.LEAVE_REQUEST
    queryset = LeaveRequest.objects.select_related(
        "employee", "employee__department", "employee__designation",
        "leave_type", "decided_by",
    ).filter(is_active=True)
    serializer_class = LeaveRequestSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filterset_fields = ["status", "leave_type", "employee", "approval_band"]
    ordering_fields = ["start_date", "created_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "approve": Action.APPROVE,
        "reject": Action.APPROVE,
        # Cancelling your own request is the DELETE the self-service grant
        # carries; HR's cancel authority rides the same action.
        "cancel": Action.DELETE,
        "attachment": Action.VIEW,
        "calendar": Action.VIEW,
        # The review signals are the deciding authority's tool.
        "patterns": Action.APPROVE,
    }

    def create(self, request, *args, **kwargs):
        payload = LeaveApplySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _call(
            services.apply_leave,
            actor=request.user,
            leave_type=payload.validated_data["leave_type"],
            start_date=payload.validated_data["start_date"],
            end_date=payload.validated_data["end_date"],
            half_day=payload.validated_data.get("half_day", ""),
            reason=payload.validated_data["reason"],
            attachment=payload.validated_data.get("attachment"),
            is_emergency=payload.validated_data.get("is_emergency", False),
        )
        return Response(LeaveRequestSerializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        row = _call(
            services.approve_leave,
            request=self.get_object(), actor=request.user,
            note=str(request.data.get("note", "")),
        )
        return Response(LeaveRequestSerializer(row).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        payload = LeaveDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _call(
            services.reject_leave,
            request=self.get_object(), actor=request.user,
            reason=payload.validated_data.get("reason", ""),
        )
        return Response(LeaveRequestSerializer(row).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payload = LeaveDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _call(
            services.cancel_leave,
            request=self.get_object(), actor=request.user,
            reason=payload.validated_data.get("reason", ""),
        )
        return Response(LeaveRequestSerializer(row).data)

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        row = self.get_object()
        if not row.attachment:
            raise Http404("No attachment.")
        return FileResponse(row.attachment.open("rb"), as_attachment=True)

    @action(detail=False, methods=["get"])
    def patterns(self, request):
        """
        HR's review signals for the year: Monday/Friday clustering,
        last-minute requests, emergencies, short-leave hours. Numbers only —
        the judgement (and any conversation) stays human.
        """
        from django.utils import timezone as _tz

        try:
            year = int(request.query_params.get("year", ""))
        except ValueError:
            year = _tz.localdate().year
        return Response({"data": services.leave_patterns(year)})

    @action(detail=False, methods=["get"])
    def calendar(self, request):
        """
        Who is out, for the calendar view. Scoped like the list — an
        employee sees their own rows, HR sees everyone — and reasons and
        attachments are NOT included here even for rows the scope allows:
        the calendar answers "who is away", nothing more private.
        """
        qs = self.filter_queryset(self.get_queryset()).filter(
            status__in=[LeaveStatus.PENDING, LeaveStatus.APPROVED]
        )
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        if start:
            qs = qs.filter(end_date__gte=start)
        if end:
            qs = qs.filter(start_date__lte=end)
        rows = [
            {
                "id": str(row.pk),
                "employee_name": row.employee.full_name,
                "department_name": row.employee.department.name
                if row.employee.department_id else "",
                "leave_type_name": row.leave_type.name,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "half_day": row.half_day,
                "days": row.days,
                "status": row.status,
            }
            for row in qs.select_related("employee__department", "leave_type")[:500]
        ]
        return Response({"data": rows})


class LeaveSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveSettings
        fields = [
            "leave_year_start_month",
            "long_leave_threshold_days", "long_leave_notice_days",
            "single_day_notice_days", "general_notice_days",
            "emergency_window_hours",
            "probation_leave_unpaid",
            "short_leave_hours_per_day",
            "absence_flag_days",
            "holiday_work_double_pay",
        ]


class LeaveSettingsView(ScopedAPIView):
    """
    GET: the organisation's leave rules — readable by anyone who can request
    leave, because the notice ladder is exactly what the apply form must
    explain. PATCH: configuration, LEAVE_POLICY/EDIT enforced explicitly.
    """

    access_resource = Resource.LEAVE_REQUEST
    access_actions = {"GET": Action.VIEW, "PATCH": Action.VIEW}

    def get(self, request):
        return Response(LeaveSettingsSerializer(LeaveSettings.get_solo()).data)

    def patch(self, request):
        # The route gate above is deliberately weak; the WRITE authority is
        # this explicit require — same two-layer shape as the leave services.
        require(request.user, Resource.LEAVE_POLICY, Action.EDIT)
        row = LeaveSettings.get_solo()
        payload = LeaveSettingsSerializer(row, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        payload.save(updated_by=request.user)
        from apps.audit.events import record_event

        record_event(
            row, actor=request.user, entity_type="leave.LeaveSettings",
            verb="update", resource=Resource.LEAVE_POLICY,
            after={"event": "leave_settings_updated",
                   **{k: str(v) for k, v in payload.validated_data.items()}},
        )
        return Response(LeaveSettingsSerializer(row).data)


class ShortLeaveSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)

    class Meta:
        model = ShortLeave
        fields = ["id", "employee", "employee_name", "employee_code",
                  "date", "out_time", "hours", "reason", "created_at"]
        read_only_fields = ["id", "employee_name", "employee_code", "created_at"]


class ShortLeaveViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    """
    Informed early departures. Everyone reads their own (LEAVE_REQUEST
    scoping); recording is HR configuration authority, enforced in the
    service; removal takes the deciding authority.
    """

    access_resource = Resource.LEAVE_REQUEST
    access_actions = {"create": Action.VIEW, "destroy": Action.APPROVE}
    queryset = ShortLeave.objects.select_related("employee").filter(is_active=True)
    serializer_class = ShortLeaveSerializer
    filterset_fields = ["employee", "date"]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def create(self, request, *args, **kwargs):
        # Authority BEFORE payload validation: an unauthorised caller gets a
        # refusal, never a validation hint about a form they may not submit.
        require(request.user, Resource.LEAVE_POLICY, Action.EDIT)
        payload = ShortLeaveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _call(
            services.record_short_leave,
            actor=request.user,
            employee=payload.validated_data["employee"],
            date=payload.validated_data["date"],
            hours=payload.validated_data["hours"],
            out_time=payload.validated_data.get("out_time"),
            reason=payload.validated_data.get("reason", ""),
        )
        return Response(ShortLeaveSerializer(row).data, status=status.HTTP_201_CREATED)


class HolidayWorkSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    approved_by_email = serializers.CharField(
        source="approved_by.email", read_only=True, default=""
    )

    class Meta:
        model = HolidayWork
        fields = ["id", "employee", "employee_name", "employee_code",
                  "date", "holiday_name", "approved_by_email", "note", "created_at"]
        read_only_fields = ["id", "employee_name", "employee_code",
                            "holiday_name", "approved_by_email", "created_at"]


class HolidayWorkViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    """Approved work on a declared public holiday — payroll double-pays it."""

    access_resource = Resource.LEAVE_REQUEST
    access_actions = {"create": Action.VIEW, "destroy": Action.APPROVE}
    queryset = HolidayWork.objects.select_related("employee").filter(is_active=True)
    serializer_class = HolidayWorkSerializer
    filterset_fields = ["employee", "date"]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def create(self, request, *args, **kwargs):
        require(request.user, Resource.LEAVE_POLICY, Action.EDIT)
        payload = HolidayWorkSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _call(
            services.record_holiday_work,
            actor=request.user,
            employee=payload.validated_data["employee"],
            date=payload.validated_data["date"],
            note=payload.validated_data.get("note", ""),
        )
        return Response(HolidayWorkSerializer(row).data, status=status.HTTP_201_CREATED)


router = DefaultRouter()
router.register("leave-types", LeaveTypeViewSet, basename="leave-type")
router.register("leave-policies", LeavePolicyViewSet, basename="leave-policy")
router.register("holiday-calendars", HolidayCalendarViewSet, basename="holiday-calendar")
router.register("holidays", HolidayViewSet, basename="holiday")
router.register("leave-balances", LeaveBalanceViewSet, basename="leave-balance")
router.register("leave-requests", LeaveRequestViewSet, basename="leave-request")
router.register("short-leaves", ShortLeaveViewSet, basename="short-leave")
router.register("holiday-work", HolidayWorkViewSet, basename="holiday-work")

from django.urls import path as _path

leave_patterns = [
    _path("leave-settings/", LeaveSettingsView.as_view(), name="leave-settings"),
    *router.urls,
]
