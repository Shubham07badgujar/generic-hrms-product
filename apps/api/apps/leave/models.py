"""
Leave: types, policies, holidays, balances, requests, and the ledger.

THE SHAPE
---------
A LeaveType is what the leave IS (casual, sick, earned, unpaid). A LeavePolicy
is the RULES an organisation attaches to a type — allocation, notice, carry
forward — resolved per employee by department/employment-type specificity,
exactly the way onboarding templates resolve. Balances are per employee, type
and year, and every number on them is explained by an append-only
LeaveTransaction row: nothing adjusts a balance without writing down why.

APPROVAL ROUTING (the office's rule)
------------------------------------
Who approves is decided by the APPLICANT'S band, not by the org chart walk:
Head-band employees (layer 1-2 — Medical Director, Operational Head, HR Head,
Finance Head) are decided by ADMIN; everyone else is decided by HR. The
reporting manager is informed, never required — unless a policy explicitly
switches `approval_authority` to the manager. Enforced in the service; the
serializer never carries an approver id.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from core.models import OrgOwnedModel
from core.validators import STORED_PATH_MAX, scoped_storage_path

ZERO = Decimal("0")


class LeaveType(OrgOwnedModel):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    #: Unpaid types are what payroll deducts — see `services.get_lop_days`.
    is_paid = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class ApprovalAuthority(models.TextChoices):
    #: The office rule: Heads → Admin, everyone else → HR.
    STANDARD = "standard", "HR (Admin for Heads)"
    #: The future switch the spec reserves: route to the reporting manager.
    REPORTING_MANAGER = "reporting_manager", "Reporting manager"


class LeavePolicy(OrgOwnedModel):
    """
    The rules for one leave type, optionally narrowed to a department and/or
    employment type. Resolution picks the most specific active policy.
    """

    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="policies")
    name = models.CharField(max_length=140)
    #: Optional narrowing; both empty = the organisation default for the type.
    department = models.ForeignKey(
        "organization.Department", on_delete=models.CASCADE, null=True, blank=True,
        related_name="leave_policies",
    )
    employment_type = models.CharField(max_length=20, blank=True)

    #: Paid-leave entitlement during probation is an explicit HR choice, not a
    #: default. Off (the company rule): nothing accrues until probation is
    #: CONFIRMED, and accrual then starts from the confirmation month. The
    #: apply flow already treats probation leave as unpaid and never touches
    #: the balance, so the two rules agree.
    accrues_during_probation = models.BooleanField(default=False)

    annual_allocation = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    #: Monthly accrual. 0 = the whole annual allocation is credited up front.
    #: Non-zero = the balance ACCRUES month by month (an employee can only use
    #: what has accrued so far — no advance leave), capped at the annual
    #: allocation, pro-rated from the joining month for mid-year joiners.
    accrual_per_month = models.DecimalField(max_digits=4, decimal_places=2, default=ZERO)
    carry_forward = models.BooleanField(default=False)
    carry_forward_limit = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    allow_half_day = models.BooleanField(default=True)
    requires_attachment = models.BooleanField(default=False)
    #: Calendar days of notice before the leave starts. 0 = none required.
    min_notice_days = models.PositiveSmallIntegerField(default=0)
    #: 0 = unlimited.
    max_consecutive_days = models.PositiveSmallIntegerField(default=0)
    allow_negative_balance = models.BooleanField(default=False)
    #: Months of service before this leave may be taken. 0 = from day one.
    min_service_months = models.PositiveSmallIntegerField(default=0)
    is_encashable = models.BooleanField(default=False)
    approval_authority = models.CharField(
        max_length=20, choices=ApprovalAuthority.choices, default=ApprovalAuthority.STANDARD
    )

    class Meta:
        ordering = ["leave_type", "name"]
        verbose_name_plural = "leave policies"

    def __str__(self) -> str:
        return self.name


class HolidayCalendar(OrgOwnedModel):
    """
    Working-day rules for a location. `location` empty = the organisation
    default. `weekly_off` holds weekday numbers (Monday=0 … Sunday=6) — NOT
    hard-coded, so a six-day clinic week is one edit.
    """

    name = models.CharField(max_length=140)
    location = models.ForeignKey(
        "organization.Location", on_delete=models.CASCADE, null=True, blank=True,
        related_name="holiday_calendars",
    )
    weekly_off = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Holiday(OrgOwnedModel):
    calendar = models.ForeignKey(HolidayCalendar, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=140)
    is_optional = models.BooleanField(default=False)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["calendar", "date"], name="uniq_holiday_per_calendar_day"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.name}"


class LeaveBalance(OrgOwnedModel):
    """
    One employee's balance for one type in one calendar year. Every figure is
    the SUM of ledger rows — the ledger is the truth, this is the running total.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_balances"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="balances")
    year = models.PositiveSmallIntegerField(db_index=True)

    allocated = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)
    carried_forward = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)
    used = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)
    pending = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)

    class Meta:
        ordering = ["-year", "leave_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "leave_type", "year"], name="uniq_balance_per_type_year"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.leave_type.code} {self.year}"

    @property
    def available(self) -> Decimal:
        return self.allocated + self.carried_forward - self.used - self.pending


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class HalfDay(models.TextChoices):
    FIRST = "first_half", "First half"
    SECOND = "second_half", "Second half"


def leave_attachment_path(instance, filename: str) -> str:
    return scoped_storage_path("leave", instance.employee_id, filename)


class LeaveRequest(OrgOwnedModel):
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    #: The policy the request was validated against, frozen for history.
    policy = models.ForeignKey(
        LeavePolicy, on_delete=models.SET_NULL, null=True, blank=True, related_name="requests"
    )

    start_date = models.DateField(db_index=True)
    end_date = models.DateField()
    #: Meaningful only when start == end; blank = full day(s).
    half_day = models.CharField(max_length=20, choices=HalfDay.choices, blank=True)
    #: Server-calculated working days. Never accepted from a client.
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.TextField()
    #: Medical certificates and the like. Served through an authorised
    #: download only — the privacy rules that guard employee documents apply.
    attachment = models.FileField(
        upload_to=leave_attachment_path, max_length=STORED_PATH_MAX, null=True, blank=True
    )

    status = models.CharField(
        max_length=20, choices=LeaveStatus.choices, default=LeaveStatus.PENDING, db_index=True
    )
    #: Which band decides: "hr" or "admin". Computed at submission from the
    #: APPLICANT's roles; the client never chooses its approver.
    approval_band = models.CharField(max_length=10, default="hr")
    #: Where the request stands in the two-step chain: "manager" while the
    #: applicant's reporting manager holds it, "hr" once forwarded (or from
    #: the start, for applicants without a manager). Computed at submission
    #: from the employee's CONFIGURED reporting manager — never hard-coded.
    approval_stage = models.CharField(max_length=10, default="hr", db_index=True)
    #: The first step's record: who forwarded it, when, with what remark.
    #: The FINAL decision stays in decided_by/decided_at below.
    manager_decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    manager_decided_at = models.DateTimeField(null=True, blank=True)
    manager_note = models.CharField(max_length=255, blank=True)
    #: Emergency leave: notice rules are waived, but the request must be for
    #: today/tomorrow — an "emergency" three weeks out is a planned leave.
    is_emergency = models.BooleanField(default=False)
    #: Set at submission when the applicant is on probation and the policy
    #: says probation leave is unpaid: no balance is held or deducted, and
    #: payroll treats the approved days as loss of pay regardless of type.
    #: Displayed as "Unpaid Leave – Probation Period".
    probation_unpaid = models.BooleanField(default=False)

    decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "-created_at"]
        indexes = [
            models.Index(fields=["employee", "status"]),
            models.Index(fields=["status", "start_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.leave_type.code} {self.start_date}"

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Leave cannot end before it starts."})
        if self.half_day and self.start_date != self.end_date:
            raise ValidationError({"half_day": "A half day applies to a single date only."})

    @property
    def is_open(self) -> bool:
        return self.status == LeaveStatus.PENDING


class TransactionKind(models.TextChoices):
    ALLOCATION = "allocation", "Annual allocation"
    CARRY_FORWARD = "carry_forward", "Carried forward"
    PENDING_HOLD = "pending_hold", "Held for a pending request"
    APPROVAL = "approval", "Approved and deducted"
    REJECTION_RELEASE = "rejection_release", "Released on rejection"
    CANCELLATION_REFUND = "cancellation_refund", "Refunded on cancellation"
    ADJUSTMENT = "adjustment", "Manual adjustment"


class LeaveTransaction(OrgOwnedModel):
    """
    Append-only. The ledger is what makes every balance auditable: each row
    says what moved, why, on whose authority, and for which request.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_transactions"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=30, choices=TransactionKind.choices)
    #: Signed. A hold is negative available, a release positive, and so on —
    #: always from the AVAILABLE balance's point of view.
    days = models.DecimalField(max_digits=6, decimal_places=1)
    request = models.ForeignKey(
        LeaveRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["employee", "leave_type", "year"])]


class LeaveSettings(OrgOwnedModel):
    """
    The organisation's leave POLICY KNOBS, editable by HR — one row.

    Everything the company handbook states as a number lives here rather than
    in code: notice windows, the emergency window, the short-leave conversion
    rate, probation behaviour. Per-TYPE rules (allocation, accrual, carry
    forward, encashment) stay on LeavePolicy where they always were; this row
    holds the rules that apply to leave as a whole.
    """

    #: Month the leave year starts in (1 = January–December).
    leave_year_start_month = models.PositiveSmallIntegerField(default=1)

    #: Notice ladder, in calendar days before the leave starts.
    #: A request longer than `long_leave_threshold_days` needs
    #: `long_leave_notice_days`; a single-day request needs
    #: `single_day_notice_days`; everything in between needs
    #: `general_notice_days`.
    long_leave_threshold_days = models.PositiveSmallIntegerField(default=3)
    long_leave_notice_days = models.PositiveSmallIntegerField(default=30)
    single_day_notice_days = models.PositiveSmallIntegerField(default=3)
    general_notice_days = models.PositiveSmallIntegerField(default=7)
    #: An emergency request must START within this many hours of applying.
    emergency_window_hours = models.PositiveSmallIntegerField(default=24)

    #: Approved leave taken during probation is unpaid and does not touch the
    #: PL/CL balance.
    probation_leave_unpaid = models.BooleanField(default=True)

    #: Cumulative short-leave hours in a month that convert to one full day's
    #: deduction (from the paid balance first, loss of pay beyond it).
    short_leave_hours_per_day = models.DecimalField(max_digits=3, decimal_places=1, default=Decimal("3"))

    #: Uninformed absence for this many consecutive working days is flagged to
    #: HR as potential abandonment of service. Flag only — never auto-act.
    absence_flag_days = models.PositiveSmallIntegerField(default=3)

    #: Working an approved declared public holiday earns that day again
    #: (double pay) in payroll.
    holiday_work_double_pay = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "leave settings"

    def __str__(self) -> str:
        return "Leave settings"

    @classmethod
    def get_solo(cls) -> "LeaveSettings":
        row = cls.objects.filter(is_active=True).first()
        if row is None:
            row = cls.objects.create()
        return row


class ShortLeave(OrgOwnedModel):
    """
    An early departure / short absence the employee informed HR about.

    Attendance hardware is not part of this system yet, so the out-time is
    recorded here by HR (or against the employee's own report). The monthly
    total converts to full-day deductions at the configured rate.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="short_leaves"
    )
    date = models.DateField(db_index=True)
    out_time = models.TimeField(null=True, blank=True)
    hours = models.DecimalField(max_digits=4, decimal_places=1)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="uniq_short_leave_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.date} {self.hours}h"


class ShortLeaveConversion(OrgOwnedModel):
    """
    The month's short-leave hours settled into days — the auditable record of
    the conversion. `deducted_days` came off the paid balance (ledger rows
    exist for them); `lop_days` exceeded it and go to payroll as loss of pay.
    Unique per employee-month, which is what makes the conversion idempotent.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="short_leave_conversions"
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    hours = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    days = models.DecimalField(max_digits=4, decimal_places=1, default=ZERO)
    deducted_days = models.DecimalField(max_digits=4, decimal_places=1, default=ZERO)
    lop_days = models.DecimalField(max_digits=4, decimal_places=1, default=ZERO)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "year", "month"], name="uniq_short_leave_conversion"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.year}-{self.month:02d}: {self.days}d"


class HolidayWork(OrgOwnedModel):
    """
    An approved day of work on a declared public holiday.

    Recorded by HR with prior approval, per the policy. Payroll pays the day
    AGAIN (double pay) through an explicit earning line on the payslip.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="holiday_work_days"
    )
    date = models.DateField(db_index=True)
    holiday_name = models.CharField(max_length=140, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="uniq_holiday_work_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} worked {self.date}"
