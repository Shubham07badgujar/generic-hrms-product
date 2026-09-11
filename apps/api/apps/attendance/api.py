"""
Attendance routes.

Three permission surfaces, deliberately separate:
  * ATTENDANCE — the day records. Everyone sees their own (the scoping engine
    narrows the queryset); HR EDITs are manual corrections and are marked so.
  * REGULARIZATION — the employee's correction request and its approval
    chain (team lead / department head / HR, straight from the matrix).
  * ATTENDANCE_DEVICE — the eSSL integration: devices, mappings, sync
    controls, unmapped punches, history. Employees hold nothing on it, so
    every route here is invisible to them; credentials never appear in any
    payload.

Every custom @action is listed in access_actions — the unmapped fall-through
would otherwise authorise POST /sync-now/ as a generic CREATE.
"""

from __future__ import annotations

import csv
import datetime as dt
import io as _io
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.audit.events import record_event
from core.access import Action, Resource, require
from core.access.drf import ScopedModelViewSet

from .models import (
    AttendanceDevice,
    AttendanceRecord,
    RawPunch,
    EsslEmployeeLink,
    EsslSyncRun,
    RecordSource,
    RecordStatus,
    RegularizationRequest,
    RegularizationStatus,
    ShiftRule,
    SyncKind,
)


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin

def _call(func, **kwargs):
    try:
        return func(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


# ---------------------------------------------------------------- records


class AttendanceRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = [
            "id", "employee", "employee_name", "employee_code", "date",
            "first_in", "last_out", "worked_minutes", "status",
            "is_late", "late_minutes", "late_counted", "source", "notes",
            "reviewed_by", "reviewed_at",
        ]
        read_only_fields = [
            "id", "employee", "employee_name", "employee_code", "date",
            "late_counted", "reviewed_by", "reviewed_at",
        ]


#: One exported row per employee-day — the columns HR asked for, in the
#: order a spreadsheet reader expects them.
EXPORT_COLUMNS = [
    "Date", "Day", "Employee Code", "Employee Name", "First Punch",
    "Last Punch", "Worked (h:mm)", "Status", "Late (min)", "Late Counted",
    "Source", "Notes",
]

#: Exports are bounded like range syncs: enough for a quarter, small enough
#: that a mistyped year cannot stream the whole table.
EXPORT_MAX_DAYS = 92


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}" if minutes else ""


def _local_time(value) -> str:
    if not value:
        return ""
    return timezone.localtime(value).strftime("%H:%M")


def _export_rows(records) -> list[list[str]]:
    rows = []
    for record in records:
        rows.append([
            record.date.isoformat(),
            record.date.strftime("%a"),
            record.employee.employee_code,
            record.employee.full_name,
            _local_time(record.first_in),
            _local_time(record.last_out),
            _hhmm(record.worked_minutes),
            record.get_status_display(),
            str(record.late_minutes) if record.is_late else "",
            "yes" if record.late_counted else ("no" if record.is_late else ""),
            record.get_source_display(),
            record.notes,
        ])
    return rows


def _as_csv(rows: list[list[str]]) -> bytes:
    buffer = _io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")  # BOM: Excel opens UTF-8 right


def _as_xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Attendance"
    sheet.append(EXPORT_COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row in rows:
        sheet.append(row)
    for index, width in enumerate(
        (12, 6, 14, 24, 12, 12, 12, 11, 10, 12, 12, 40), start=1
    ):
        sheet.column_dimensions[chr(64 + index)].width = width
    buffer = _io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class AttendanceRecordViewSet(ScopedModelViewSet):
    access_resource = Resource.ATTENDANCE
    queryset = (
        AttendanceRecord.objects.select_related("employee")
        .filter(is_active=True)
    )
    serializer_class = AttendanceRecordSerializer
    filterset_fields = {
        "employee": ["exact"],
        "date": ["exact", "gte", "lte"],
        "status": ["exact"],
        "is_late": ["exact"],
        "late_counted": ["exact"],
    }
    ordering_fields = ["date"]
    search_fields = [
        "employee__first_name", "employee__last_name", "employee__employee_code",
    ]
    http_method_names = ["get", "post", "patch", "head", "options"]
    # export and punches ride VIEW deliberately: both are different
    # serializations of rows the caller may already see, and the scoped
    # queryset decides WHICH rows — an employee exports their own days,
    # HR anyone's.
    access_actions = {
        "acknowledge": Action.EDIT,
        "punches": Action.VIEW,
        "export": Action.VIEW,
        "month": Action.VIEW,
    }

    @action(detail=False, methods=["get"], url_path="month")
    def month(self, request):
        """
        GET /attendance-records/month/?employee=&year=&month= — one person's
        month as a calendar: every day resolved to present / absent / half
        day / late / leave / holiday / week off, plus the summary the header
        shows. Scoped like everything else: an employee gets their own month,
        HR anyone's.
        """
        from apps.employees.models import Employee
        from core.access import scope_queryset

        try:
            year = int(request.query_params.get("year", ""))
            month = int(request.query_params.get("month", ""))
            assert 1 <= month <= 12
        except (TypeError, ValueError, AssertionError):
            raise DRFValidationError({"period": "year and month (1-12) are required."})

        import uuid as _uuid

        try:
            employee_id = _uuid.UUID(str(request.query_params.get("employee")))
        except (TypeError, ValueError):
            raise DRFValidationError({"employee": "employee is required."})
        employee = scope_queryset(
            Employee.objects.filter(is_active=True), request.user,
            resource=Resource.EMPLOYEE, action=Action.VIEW,
        ).filter(pk=employee_id).first()
        if employee is None:
            raise DRFValidationError({"employee": "No such employee."})

        from apps.attendance import services as attendance_services

        return Response(attendance_services.month_calendar(employee, year, month))

    def get_queryset(self):
        qs = super().get_queryset()
        # HR's exception review: the days the ENGINE flagged and nobody has
        # looked at yet. Manual/regularized rows are HR's own word, so they
        # are never exceptions.
        if self.request.query_params.get("exception") in ("true", "1"):
            qs = qs.filter(
                source=RecordSource.DEVICE, reviewed_at__isnull=True,
            ).filter(
                Q(status=RecordStatus.HALF_DAY)
                | Q(late_counted=True)
                | Q(status=RecordStatus.ABSENT)
                # A single punch: present, but the hours are unverifiable.
                | Q(
                    status=RecordStatus.PRESENT,
                    first_in__isnull=False,
                    worked_minutes=0,
                )
            )
        return qs

    def perform_update(self, serializer):
        """
        An HR edit is a MANUAL OVERRIDE.

        Marked source=manual so no sync ever recomputes over it; a status
        change demands a readable reason; overriding to Present erases the
        counted late (the month must stop charging a pardoned day); and the
        whole before/after goes to the audit log explicitly — original
        status, revised status, reason, actor, time.
        """
        record = serializer.instance
        before = {
            "status": record.status,
            "is_late": record.is_late,
            "late_counted": record.late_counted,
            "worked_minutes": record.worked_minutes,
            "source": record.source,
            "notes": record.notes,
        }
        new_status = serializer.validated_data.get("status", record.status)
        reason = str(serializer.validated_data.get("notes", record.notes) or "")
        if new_status != record.status and len(reason.strip()) < 5:
            raise DRFValidationError(
                {"notes": "An override needs a reason the employee could read."}
            )

        extra = {
            "source": RecordSource.MANUAL,
            "reviewed_by": self.request.user,
            "reviewed_at": timezone.now(),
        }
        if new_status == RecordStatus.PRESENT:
            extra["late_counted"] = False
        updated = serializer.save(**extra)

        record_event(
            updated,
            actor=self.request.user,
            entity_type="attendance.AttendanceRecord",
            verb="update",
            resource=Resource.ATTENDANCE,
            before=before,
            after={
                "status": updated.status,
                "is_late": updated.is_late,
                "late_counted": updated.late_counted,
                "worked_minutes": updated.worked_minutes,
                "source": updated.source,
                "notes": updated.notes,
            },
            reason=reason.strip() or None,
        )

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """HR marking "the system got this right" without changing the day."""
        record = self.get_object()
        if record.source != RecordSource.DEVICE:
            raise DRFValidationError(
                {"detail": "Only computed days need review — this one is HR-written."}
            )
        if record.reviewed_at is not None:
            raise DRFValidationError({"detail": "Already reviewed."})
        record.reviewed_by = request.user
        record.reviewed_at = timezone.now()
        record.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
        record_event(
            record,
            actor=request.user,
            entity_type="attendance.AttendanceRecord",
            verb="approve",
            resource=Resource.ATTENDANCE,
            before={"reviewed": False},
            after={"reviewed": True, "status": record.status},
        )
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["get"])
    def punches(self, request, pk=None):
        """Every raw punch behind one day — the evidence under the verdict."""
        record = self.get_object()
        tz = timezone.get_current_timezone()
        day_start = dt.datetime.combine(record.date, dt.time.min, tzinfo=tz)
        rows = RawPunch.objects.filter(
            employee_id=record.employee_id,
            punched_at__gte=day_start,
            punched_at__lt=day_start + dt.timedelta(days=1),
        ).select_related("device").order_by("punched_at")
        return Response([
            {
                "punched_at": punch.punched_at,
                "time": _local_time(punch.punched_at),
                "device": punch.device.name if punch.device_id else "",
            }
            for punch in rows
        ])

    @action(detail=False, methods=["get"])
    def export(self, request):
        """
        The scoped day records as a spreadsheet — .xlsx or .csv.

        Filters: employee (optional), date__gte / date__lte (required, at
        most {EXPORT_MAX_DAYS} days), fmt=xlsx|csv. Rows are exactly what the
        caller's scope lets them list; the export is audited.
        """
        fmt = request.query_params.get("fmt", "xlsx").lower()
        if fmt not in ("xlsx", "csv"):
            raise DRFValidationError({"fmt": "Choose xlsx or csv."})
        try:
            date_from = dt.date.fromisoformat(request.query_params.get("date__gte", ""))
            date_to = dt.date.fromisoformat(request.query_params.get("date__lte", ""))
        except ValueError as exc:
            raise DRFValidationError(
                {"detail": "date__gte and date__lte are required, YYYY-MM-DD."}
            ) from exc
        if date_to < date_from:
            raise DRFValidationError({"date__lte": "The range ends before it starts."})
        if (date_to - date_from).days + 1 > EXPORT_MAX_DAYS:
            raise DRFValidationError(
                {"detail": f"At most {EXPORT_MAX_DAYS} days per export — split the range."}
            )

        records = self.filter_queryset(self.get_queryset()).filter(
            date__gte=date_from, date__lte=date_to
        ).select_related("employee").order_by("employee__employee_code", "date")
        rows = _export_rows(records)

        stamp = f"{date_from.isoformat()}_to_{date_to.isoformat()}"
        employee_id = request.query_params.get("employee", "")
        label = "attendance"
        if employee_id:
            first = records.first()
            if first is not None:
                label = f"attendance-{first.employee.employee_code}"
                record_event(
                    first.employee,
                    actor=request.user,
                    entity_type="employees.Employee",
                    verb="export",
                    resource=Resource.ATTENDANCE,
                    after={
                        "export": "attendance", "format": fmt,
                        "from": date_from.isoformat(), "to": date_to.isoformat(),
                        "rows": len(rows),
                    },
                )

        if fmt == "csv":
            content, content_type = _as_csv(rows), "text/csv; charset=utf-8"
        else:
            content = _as_xlsx(rows)
            content_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="{label}_{stamp}.{fmt}"'
        )
        return response


# ---------------------------------------------------------- regularization


class RegularizationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)

    class Meta:
        model = RegularizationRequest
        fields = [
            "id", "employee", "employee_name", "employee_code", "date",
            "proposed_in", "proposed_out", "reason", "status",
            "decided_by", "decided_at", "decision_note", "created_at",
        ]
        read_only_fields = [
            "id", "employee", "employee_name", "employee_code", "status",
            "decided_by", "decided_at", "decision_note", "created_at",
        ]


class RegularizationViewSet(ScopedModelViewSet):
    access_resource = Resource.REGULARIZATION
    queryset = RegularizationRequest.objects.select_related("employee").filter(is_active=True)
    serializer_class = RegularizationSerializer
    filterset_fields = ["status", "employee"]
    http_method_names = ["get", "post", "head", "options"]
    access_actions = {"approve": Action.APPROVE, "reject": Action.APPROVE}

    def perform_create(self, serializer):
        employee = getattr(self.request.user, "employee", None)
        if employee is None:
            raise DRFValidationError({"employee": "Your login has no employee record."})
        serializer.save(employee=employee)

    def _decide(self, request, approved: bool):
        row = self.get_object()
        if row.status != RegularizationStatus.PENDING:
            raise DRFValidationError({"status": f"Already {row.status}."})
        require(request.user, Resource.REGULARIZATION, Action.APPROVE)

        row.status = (
            RegularizationStatus.APPROVED if approved else RegularizationStatus.REJECTED
        )
        row.decided_by = request.user
        row.decided_at = timezone.now()
        row.decision_note = str(request.data.get("note", ""))[:255]
        row.save()

        if approved:
            existing = AttendanceRecord.objects.active().filter(
                employee=row.employee, date=row.date
            ).first()
            if existing and existing.source == RecordSource.MANUAL:
                # HR already ruled on this day by hand; a later approval must
                # not quietly undo that ruling.
                raise DRFValidationError(
                    {
                        "detail": (
                            "This day was manually corrected by HR — edit the "
                            "attendance record instead of regularizing over it."
                        )
                    }
                )
            before = (
                {
                    "status": existing.status,
                    "is_late": existing.is_late,
                    "late_counted": existing.late_counted,
                    "source": existing.source,
                }
                if existing
                else None
            )
            worked = 0
            if row.proposed_in and row.proposed_out:
                worked = max(
                    int((row.proposed_out - row.proposed_in).total_seconds() // 60), 0
                )
            record, _ = AttendanceRecord.objects.update_or_create(
                employee=row.employee,
                date=row.date,
                is_active=True,
                defaults={
                    "first_in": row.proposed_in,
                    "last_out": row.proposed_out,
                    "worked_minutes": worked,
                    "status": "present",
                    "is_late": False,
                    "late_minutes": 0,
                    # Approval is a pardon: the day stops charging the month.
                    "late_counted": False,
                    "reviewed_by": request.user,
                    "reviewed_at": timezone.now(),
                    "source": RecordSource.REGULARIZED,
                    "notes": f"Regularized: {row.reason}"[:255],
                },
            )
            record_event(
                record,
                actor=request.user,
                entity_type="attendance.AttendanceRecord",
                verb="approve",
                resource=Resource.ATTENDANCE,
                before=before,
                after={"status": record.status, "source": record.source},
                reason=row.reason,
            )
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, approved=True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, approved=False)


# ------------------------------------------------------- eSSL integration


class DeviceSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = AttendanceDevice
        fields = [
            "id", "name", "serial_number", "location", "location_name",
            "is_enabled", "last_synced_at", "last_sync_status", "last_sync_error",
        ]
        read_only_fields = ["id", "last_synced_at", "last_sync_status", "last_sync_error"]


class MappingSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    """
    One device ID for one employee, at one site.

    `essl_user_id` deliberately carries NO UniqueValidator: uniqueness spans
    soft-deleted rows, so the default validator would refuse an ID whose
    mapping was removed months ago. `services.sync.map_essl_id` decides — it
    revives that row, and refuses only an ID a DIFFERENT employee holds live.
    """

    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    essl_user_id = serializers.CharField(max_length=32, validators=[])

    class Meta:
        model = EsslEmployeeLink
        fields = [
            "id", "essl_user_id", "employee", "employee_name", "employee_code",
            "location", "location_name",
        ]
        read_only_fields = ["id"]


class SyncRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = EsslSyncRun
        fields = [
            "id", "kind", "range_from", "range_to", "started_at", "finished_at",
            "status", "devices_total", "devices_failed", "punches_fetched",
            "punches_created", "punches_duplicate", "punches_unmapped", "errors",
        ]


class ShiftRuleSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = ShiftRule
        fields = [
            "id", "location", "location_name", "start_time", "end_time",
            "grace_minutes", "allowed_late_per_month", "half_day_below_hours",
            "full_day_hours",
        ]
        read_only_fields = ["id", "location"]

    def validate(self, attrs):
        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name) if self.instance else None

        start, end = value("start_time"), value("end_time")
        if start and end and end <= start:
            raise serializers.ValidationError(
                {"end_time": "The day must end after it starts."}
            )
        full, half = value("full_day_hours"), value("half_day_below_hours")
        if full is not None and half is not None and full <= half:
            raise serializers.ValidationError(
                {
                    "full_day_hours": (
                        "Full-day hours must exceed the half-day threshold — "
                        "otherwise every full day would also be a half day."
                    )
                }
            )
        grace = value("grace_minutes")
        if grace is not None and grace > 60:
            raise serializers.ValidationError(
                {"grace_minutes": "Grace beyond an hour is a different start time."}
            )
        allowed = value("allowed_late_per_month")
        if allowed is not None and allowed > 31:
            raise serializers.ValidationError(
                {"allowed_late_per_month": "A month has at most 31 days."}
            )
        return attrs


class ShiftRuleViewSet(ScopedModelViewSet):
    """
    The per-branch attendance policy, editable by whoever runs the eSSL
    integration (HR Head / Admin via ATTENDANCE_DEVICE). Every edit is
    audited with before/after — policy changes are decisions, not tweaks.
    """

    access_resource = Resource.ATTENDANCE_DEVICE
    queryset = ShiftRule.objects.select_related("location").filter(is_active=True)
    serializer_class = ShiftRuleSerializer
    pagination_class = None
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        rule = serializer.instance
        watched = [
            "start_time", "end_time", "grace_minutes",
            "allowed_late_per_month", "half_day_below_hours", "full_day_hours",
        ]
        before = {name: str(getattr(rule, name)) for name in watched}
        updated = serializer.save()
        record_event(
            updated,
            actor=self.request.user,
            entity_type="attendance.ShiftRule",
            verb="update",
            resource=Resource.ATTENDANCE_DEVICE,
            before=before,
            after={name: str(getattr(updated, name)) for name in watched},
        )


class RangeSyncSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    def validate(self, data):
        if data["date_from"] > data["date_to"]:
            raise DRFValidationError({"date_from": "The range starts after it ends."})
        if (data["date_to"] - data["date_from"]).days > 92:
            raise DRFValidationError(
                {"date_to": "Import at most three months per run — repeat for more."}
            )
        return data


def _attendance_config(organization_id):
    from core.config import attendance_config

    return attendance_config(organization_id)


def _org_id(request):
    """This request's organization, for resolving device credentials."""
    from core.access.context import get_context

    return get_context(request).organization_id


class EsslDeviceViewSet(ScopedModelViewSet):
    """Devices, plus the integration's control surface. HR-only by resource."""

    access_resource = Resource.ATTENDANCE_DEVICE
    queryset = AttendanceDevice.objects.select_related("location").filter(is_active=True)
    serializer_class = DeviceSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    access_actions = {
        "status": Action.VIEW,
        "sync_now": Action.IMPORT,
        "sync_range": Action.IMPORT,
        "unmapped": Action.VIEW,
        "runs": Action.VIEW,
    }

    @action(detail=False, methods=["get"])
    def status(self, request):
        """Connection story for the status card. Never includes credentials."""
        from django.conf import settings

        from .services.essl_client import essl_enabled

        last = EsslSyncRun.objects.active().order_by("-started_at").first()
        return Response({
            "enabled": essl_enabled(_org_id(request)),
            # The HOST only, never the full endpoint and never the
            # credentials: this is a status card an HR user reads, and the
            # device password is not theirs to see.
            "base_host": (
                _attendance_config(_org_id(request)).base_url or ""
            ).split("//")[-1].split("/")[0],
            "affects_payroll": bool(getattr(settings, "ATTENDANCE_AFFECTS_PAYROLL", False)),
            "devices": DeviceSerializer(
                self.get_queryset().order_by("name"), many=True
            ).data,
            "last_run": SyncRunSerializer(last).data if last else None,
        })

    @action(detail=False, methods=["post"], url_path="sync-now")
    def sync_now(self, request):
        from .models import SyncKind
        from .services.essl_client import essl_enabled
        from .services.sync import sync_all

        require(request.user, Resource.ATTENDANCE_DEVICE, Action.IMPORT)
        if not essl_enabled(_org_id(request)):
            raise DRFValidationError(
                {"detail": "The eSSL integration is disabled on the server."}
            )
        run = sync_all(kind=SyncKind.MANUAL, actor=request.user)
        return Response(SyncRunSerializer(run).data)

    @action(detail=False, methods=["post"], url_path="sync-range")
    def sync_range(self, request):
        from .services.essl_client import essl_enabled
        from .services.sync import sync_all

        require(request.user, Resource.ATTENDANCE_DEVICE, Action.IMPORT)
        if not essl_enabled(_org_id(request)):
            raise DRFValidationError(
                {"detail": "The eSSL integration is disabled on the server."}
            )
        payload = RangeSyncSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        run = sync_all(
            kind=SyncKind.RANGE,
            range_from=payload.validated_data["date_from"],
            range_to=payload.validated_data["date_to"],
            actor=request.user,
        )
        return Response(SyncRunSerializer(run).data)

    @action(detail=False, methods=["get"])
    def unmapped(self, request):
        from .services.sync import unmapped_summary

        return Response(unmapped_summary())

    @action(detail=False, methods=["get"])
    def runs(self, request):
        rows = EsslSyncRun.objects.active().order_by("-started_at")[:50]
        return Response(SyncRunSerializer(rows, many=True).data)

class EsslMappingViewSet(ScopedModelViewSet):
    """
    The device-ID mappings. An employee may hold several — one per site.

    Filter with `?employee=<id>` to see one person's mappings. Every write
    goes through `map_essl_id`, so the API and any other caller enforce the
    same rule: many IDs per employee, never one ID for two employees.
    """

    access_resource = Resource.ATTENDANCE_DEVICE
    queryset = (
        EsslEmployeeLink.objects.select_related("employee", "location")
        .filter(is_active=True)
        .order_by("employee__employee_code", "essl_user_id")
    )
    serializer_class = MappingSerializer
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        rows = super().get_queryset()
        employee = self.request.query_params.get("employee")
        if employee:
            try:
                uuid.UUID(str(employee))
            except (ValueError, AttributeError, TypeError):
                return rows.none()
            rows = rows.filter(employee_id=employee)
        return rows

    def _save_through_service(self, serializer):
        from .services.sync import map_essl_id

        data = serializer.validated_data
        instance = serializer.instance
        employee = data.get("employee") or (instance.employee if instance else None)
        essl_user_id = data.get(
            "essl_user_id", instance.essl_user_id if instance else ""
        )
        location = data.get("location", instance.location if instance else None)
        try:
            link = map_essl_id(
                employee=employee, essl_user_id=essl_user_id, location=location
            )
        except DjangoValidationError as exc:
            detail = (
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            )
            raise DRFValidationError(detail) from exc
        # An edit that repoints an ID leaves the old row behind; drop it.
        if instance is not None and instance.pk != link.pk:
            instance.delete()
        serializer.instance = link

    def perform_create(self, serializer):
        self._save_through_service(serializer)

    def perform_update(self, serializer):
        self._save_through_service(serializer)

    def perform_destroy(self, instance):
        from .services.sync import unmap_essl_id

        # Removes THIS mapping only — the employee's other sites keep theirs.
        unmap_essl_id(instance)


# ------------------------------------------------------------------ routes

from rest_framework.routers import DefaultRouter  # noqa: E402

router = DefaultRouter()
router.register("attendance-records", AttendanceRecordViewSet, basename="attendance-record")
router.register("regularizations", RegularizationViewSet, basename="regularization")
router.register("essl/devices", EsslDeviceViewSet, basename="essl-device")
router.register("essl/mappings", EsslMappingViewSet, basename="essl-mapping")
router.register("essl/shift-rules", ShiftRuleViewSet, basename="essl-shift-rule")

attendance_patterns = [*router.urls]
