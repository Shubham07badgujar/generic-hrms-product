"""
Attendance: devices, raw punches, computed day records, corrections.

THE SHAPE
---------
eSSL biometric devices at each branch report to one eTimeTrackLite server.
A periodic sync pulls every device's punches into `RawPunch` — an append-only
store where a database constraint makes the same punch unrepresentable twice,
so re-reading a window is always free. Punches resolve to employees through
`EsslEmployeeLink` (explicit mapping, NEVER by name); unresolved punches stay,
visible to HR as "unmapped", until a mapping appears.

`AttendanceRecord` is the computed truth for one employee-day — present,
half-day, absent, late — derived from punches by `services.calculation` under
the location's `ShiftRule`. Its `source` field is the protection order: a row
HR corrected (`manual`) or a row written by an approved regularization
(`regularized`) is never overwritten by the machine.

The names `AttendanceRecord` and `RegularizationRequest` are load-bearing:
`core/access/registry.py` bound the ATTENDANCE and REGULARIZATION resources to
these exact labels before this app had models, and the scoping engine resolves
row visibility through their `employee` FK.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q

from core.models import BaseModel, TimestampedModel


class SyncStatus(models.TextChoices):
    NEVER = "never", "Never synced"
    OK = "ok", "OK"
    FAILED = "failed", "Failed"


class AttendanceDevice(BaseModel):
    """One biometric device, addressed by the serial number the Web API uses."""

    name = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=64, unique=True)
    location = models.ForeignKey(
        "organization.Location",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attendance_devices",
    )
    is_enabled = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(
        max_length=12, choices=SyncStatus.choices, default=SyncStatus.NEVER
    )
    #: The failure, verbatim but bounded — shown to HR, never raised at them.
    last_sync_error = models.CharField(max_length=2000, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.serial_number})"


class EsslEmployeeLink(BaseModel):
    """
    The employee <-> eSSL user-ID mapping. MANY ids may point at ONE employee.

    Someone who works across branches is enrolled separately on each site's
    device and therefore carries a different user ID at each — 1025 at one
    site, 2087 at another. Both are the same person, so the mapping is
    many-to-one and
    each row records the location its ID belongs to.

    The direction that must NOT be many is the other one: a single device ID
    resolving to two employees would file one person's punches against
    another, so `essl_user_id` stays globally unique. Note the constraint spans
    soft-deleted rows too — `services.sync.map_essl_id` revives a removed
    mapping rather than colliding with it.

    Mapping is explicit: HR creates it, or accepts an auto-suggestion made ONLY
    on an exact employee-code match. Matching by name is deliberately
    unsupported — names on devices are freehand and collide.
    """

    essl_user_id = models.CharField(max_length=32, unique=True, db_index=True)
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="essl_links"
    )
    #: Which site this ID belongs to. Optional: mappings made before
    #: multi-location support carry none, and a single-site employee needs none.
    location = models.ForeignKey(
        "organization.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="essl_links",
    )

    class Meta:
        ordering = ["essl_user_id"]
        indexes = [models.Index(fields=["employee", "is_active"])]

    def __str__(self) -> str:
        where = f" @{self.location.name}" if self.location_id else ""
        return f"{self.essl_user_id}{where} -> {self.employee.employee_code}"


class RawPunch(TimestampedModel):
    """
    One punch, exactly as the device reported it. Append-only, high volume.

    The unique constraint IS the idempotency: every sync re-reads an
    overlapping window and inserts with ignore_conflicts, so a punch can be
    seen any number of times and exist once.
    """

    device = models.ForeignKey(
        AttendanceDevice, on_delete=models.CASCADE, related_name="punches"
    )
    essl_user_id = models.CharField(max_length=32)
    #: Aware UTC; the device reports naive local time in ESSL_TIMEZONE.
    punched_at = models.DateTimeField()
    #: The original tab-separated row, verbatim — nothing the server sent is lost.
    raw = models.CharField(max_length=512, blank=True)
    #: Resolved through EsslEmployeeLink; NULL is a KEPT, unmapped punch.
    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="raw_punches",
    )

    class Meta:
        ordering = ["-punched_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["device", "essl_user_id", "punched_at"],
                name="uniq_punch_per_device_user_moment",
            ),
        ]
        indexes = [
            models.Index(fields=["essl_user_id", "punched_at"]),
            models.Index(fields=["employee", "punched_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.essl_user_id} @ {self.punched_at:%Y-%m-%d %H:%M}"


class RecordStatus(models.TextChoices):
    PRESENT = "present", "Present"
    HALF_DAY = "half_day", "Half day"
    ABSENT = "absent", "Absent"
    ON_LEAVE = "on_leave", "On leave"
    HOLIDAY = "holiday", "Holiday"
    WEEKLY_OFF = "weekly_off", "Weekly off"


class RecordSource(models.TextChoices):
    #: Computed from device punches; freely recomputed by every sync.
    DEVICE = "device", "Device"
    #: HR typed it. The machine never touches it again.
    MANUAL = "manual", "Manual correction"
    #: Written by an approved regularization. Same protection as manual.
    REGULARIZED = "regularized", "Regularized"


class AttendanceRecord(BaseModel):
    """One employee-day, computed or corrected. The payroll-facing truth."""

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="attendance_records"
    )
    date = models.DateField(db_index=True)
    first_in = models.DateTimeField(null=True, blank=True)
    last_out = models.DateTimeField(null=True, blank=True)
    worked_minutes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=RecordStatus.choices)
    #: Informational: the arrival was after grace. A late that the person made
    #: up by completing the day's full hours stays is_late=True for HR's eyes
    #: but is NOT counted (late_counted=False) — only counted lates feed the
    #: monthly allowance and its half-day escalation.
    is_late = models.BooleanField(default=False)
    late_minutes = models.PositiveIntegerField(default=0)
    #: The field the monthly tally actually counts. True only when the arrival
    #: was late AND the day's worked hours fell short of the rule's full day.
    late_counted = models.BooleanField(default=False)
    source = models.CharField(
        max_length=12, choices=RecordSource.choices, default=RecordSource.DEVICE
    )
    notes = models.CharField(max_length=255, blank=True)
    #: HR's exception review. Set by acknowledge/override; cleared whenever the
    #: engine recomputes the day, because a re-derived verdict needs re-review.
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            # Conditioned on is_active so a soft-deleted row never blocks the
            # day forever — the trap payroll documents on its own constraint.
            models.UniqueConstraint(
                fields=["employee", "date"],
                condition=Q(is_active=True),
                name="uniq_attendance_day_active",
            ),
        ]
        indexes = [models.Index(fields=["employee", "date"])]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.date} {self.status}"


class RegularizationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RegularizationRequest(BaseModel):
    """
    An employee's request to correct their own day.

    Approval authority comes from the matrix (team lead at TEAM, department
    head at DEPARTMENT, HR at ALL). Approving writes the AttendanceRecord
    with source=regularized, which shields it from every future recompute.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="regularizations"
    )
    date = models.DateField()
    proposed_in = models.DateTimeField(null=True, blank=True)
    proposed_out = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=255)
    status = models.CharField(
        max_length=12, choices=RegularizationStatus.choices,
        default=RegularizationStatus.PENDING, db_index=True,
    )
    decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.date} ({self.status})"


class ShiftRule(BaseModel):
    """
    The attendance policy for one location (or the org default when
    location is NULL). Values seeded from the company's timing chart and
    editable by HR — policy is configuration here, never a constant.
    """

    location = models.OneToOneField(
        "organization.Location",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shift_rule",
    )
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=15)
    #: Late arrivals tolerated per month; every late beyond this is a half day.
    allowed_late_per_month = models.PositiveSmallIntegerField(default=3)
    #: Worked hours below this make the day a half day regardless of lateness.
    half_day_below_hours = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal("4.5")
    )
    #: A full day's worked hours, measured first punch to last punch (so breaks
    #: are inside it) — normally the shift's whole span. A late arrival that
    #: still reaches this many hours is COMPENSATED: the late stays visible but
    #: never counts toward the monthly allowance. HR may lower it per branch to
    #: grant a standing break allowance.
    full_day_hours = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal("9.0")
    )

    class Meta:
        ordering = ["location__name"]
        constraints = [
            # Exactly one org-default row (location IS NULL).
            models.UniqueConstraint(
                fields=["location"],
                condition=Q(location__isnull=True),
                name="uniq_default_shift_rule",
            ),
        ]

    def __str__(self) -> str:
        where = self.location.name if self.location else "Default"
        return f"{where} {self.start_time:%H:%M}-{self.end_time:%H:%M}"


class SyncKind(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    MANUAL = "manual", "Sync now"
    RANGE = "range", "Date range"


class RunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Completed with errors"
    FAILED = "failed", "Failed"


class EsslSyncRun(BaseModel):
    """One sync attempt — the history HR reads when something looks off."""

    kind = models.CharField(max_length=12, choices=SyncKind.choices)
    range_from = models.DateField(null=True, blank=True)
    range_to = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=RunStatus.choices, default=RunStatus.RUNNING
    )
    devices_total = models.PositiveSmallIntegerField(default=0)
    devices_failed = models.PositiveSmallIntegerField(default=0)
    punches_fetched = models.PositiveIntegerField(default=0)
    punches_created = models.PositiveIntegerField(default=0)
    punches_duplicate = models.PositiveIntegerField(default=0)
    punches_unmapped = models.PositiveIntegerField(default=0)
    #: [{"device": serial, "error": "..."}] — codes and messages, no PII.
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.kind} sync {self.started_at:%Y-%m-%d %H:%M} ({self.status})"
