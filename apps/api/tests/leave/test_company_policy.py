"""
The company leave policy, as configured behaviour.

The claims:
  1. PROBATION: applying is never blocked, but approved probation leave is
     unpaid — no PL/CL hold, no deduction, payroll sees loss of pay, and the
     row displays as "Unpaid Leave – Probation Period". After confirmation
     the normal rules resume.
  2. SETTINGS: one configurable row; anyone who can request leave may read
     it (the apply form explains the notice ladder from it); only
     LEAVE_POLICY holders may change it; the change is audited.
  3. SHORT LEAVE: cumulative hours convert monthly into full days at the
     configured rate — deducted from the paid balance while it lasts, loss
     of pay beyond it. Idempotent per employee-month.
  4. HOLIDAY WORK: recordable only on a declared public holiday, once per
     day; payroll reads it through its own seam.
  5. PATTERNS: the HR review signals exist and are approval-gated.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.employees.models import Employee, EmployeeStatus
from apps.leave import services
from apps.leave.models import (
    Holiday,
    HolidayCalendar,
    LeaveBalance,
    LeaveSettings,
    LeaveStatus,
    ShortLeave,
    ShortLeaveConversion,
)
from apps.leave.seeds import seed_leave
from apps.leave.services import LeaveError
from core.access.catalog import DepartmentKind, Layer
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


@pytest.fixture
def leave_config(db):
    seed_leave()
    from apps.leave.models import LeaveType

    return {t.code: t for t in LeaveType.objects.all()}


@pytest.fixture
def staff(db, roles, org):
    people: dict = {}
    counter = [4000]

    def hire(role_code, kind, name, status=EmployeeStatus.ACTIVE):
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@policy.test", password=PASSWORD, first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        people[role_code] = Employee.objects.create(
            employee_code=f"EMP{counter[0]:06d}", user=user, first_name=name,
            department=org["departments"][DepartmentKind.MEDICAL],
            level=org["levels"][Layer.STAFF] if role_code == "therapist" else None,
            location=org["location"],
            date_of_joining=dt.date(2024, 1, 1),
            status=status,
        )
        return people[role_code]

    hire("hr_head", DepartmentKind.HR, "Hema")
    hire("therapist", DepartmentKind.MEDICAL, "Tara")
    hire("clinic_doctor", DepartmentKind.MEDICAL, "Probin", status=EmployeeStatus.ON_PROBATION)
    return people


def _next_working_day(employee, days_from_now):
    day = timezone.localdate() + dt.timedelta(days=days_from_now)
    while services.working_days(employee, day, day) <= 0:
        day += dt.timedelta(days=1)
    return day


# ============================================================ probation


def test_probation_leave_is_unpaid_and_never_touches_the_balance(staff, leave_config):
    prob = staff["clinic_doctor"]
    start = _next_working_day(prob, 7)
    request = services.apply_leave(
        actor=prob.user, leave_type=leave_config["paid"],
        start_date=start, end_date=start,
        reason="Personal work during probation.",
    )
    assert request.probation_unpaid is True
    # No hold: the PL/CL balance is untouched by the application...
    balance = LeaveBalance.objects.filter(
        employee=prob, leave_type=leave_config["paid"], year=start.year
    ).first()
    assert balance is None or balance.pending == 0

    services.approve_leave(request=request, actor=staff["hr_head"].user)
    # ...and by the approval.
    balance = LeaveBalance.objects.filter(
        employee=prob, leave_type=leave_config["paid"], year=start.year
    ).first()
    assert balance is None or (balance.used == 0 and balance.pending == 0)

    # Payroll sees the approved probation day as loss of pay.
    assert services.get_lop_days(prob, start.year, start.month) >= Decimal("1")


def test_after_confirmation_the_normal_rules_resume(staff, leave_config):
    prob = staff["clinic_doctor"]
    prob.status = EmployeeStatus.CONFIRMED
    prob.save(update_fields=["status", "updated_at"])

    start = _next_working_day(prob, 7)
    request = services.apply_leave(
        actor=prob.user, leave_type=leave_config["paid"],
        start_date=start, end_date=start,
        reason="First leave after confirmation.",
    )
    assert request.probation_unpaid is False
    balance = services.balance_for(prob, leave_config["paid"], start.year)
    assert balance.pending == request.days


def test_probation_display_name(staff, leave_config, api):
    from apps.leave.api import LeaveRequestSerializer

    prob = staff["clinic_doctor"]
    start = _next_working_day(prob, 7)
    request = services.apply_leave(
        actor=prob.user, leave_type=leave_config["paid"],
        start_date=start, end_date=start, reason="Displayed as unpaid.",
    )
    data = LeaveRequestSerializer(request).data
    assert data["display_type"] == "Unpaid Leave – Probation Period"


def test_a_valid_certificate_is_accepted(staff, leave_config):
    """The E2E gap: refusal was tested, a SUCCESSFUL upload never was —
    and a dot-less extension set silently refused every certificate."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    therapist = staff["therapist"]
    start = _next_working_day(therapist, 10)
    cert = SimpleUploadedFile(
        "certificate.pdf", b"%PDF-1.4 " + b"x" * 2048, content_type="application/pdf"
    )
    request = services.apply_leave(
        actor=therapist.user, leave_type=leave_config["sick"],
        start_date=start, end_date=start,
        reason="Medical, certificate attached.", attachment=cert,
    )
    assert request.attachment


# ============================================================ settings


def test_settings_are_readable_by_requesters_and_writable_by_policy_holders(
    staff, leave_config, api
):
    def login(who):
        token = api.post(
            "/api/v1/auth/login/", {"email": f"{who}@policy.test", "password": PASSWORD}
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    login("therapist")
    read = api.get("/api/v1/leave-settings/")
    assert read.status_code == 200
    assert read.data["single_day_notice_days"] == 3
    # ...but a therapist cannot change the rules.
    assert api.patch(
        "/api/v1/leave-settings/", {"general_notice_days": 2}, format="json"
    ).status_code == 403

    login("hr_head")
    write = api.patch(
        "/api/v1/leave-settings/", {"general_notice_days": 5}, format="json"
    )
    assert write.status_code == 200 and write.data["general_notice_days"] == 5
    assert LeaveSettings.get_solo().general_notice_days == 5

    from apps.audit.models import AuditLog

    assert AuditLog.objects.filter(after__event="leave_settings_updated").exists()


# ============================================================ short leave


def test_short_leave_converts_monthly_and_is_idempotent(staff, leave_config):
    hr, therapist = staff["hr_head"], staff["therapist"]
    today = timezone.localdate()

    services.record_short_leave(
        actor=hr.user, employee=therapist, date=today.replace(day=3),
        hours=Decimal("2"), reason="Left early — clinic errand.",
    )
    services.record_short_leave(
        actor=hr.user, employee=therapist, date=today.replace(day=10),
        hours=Decimal("1.5"),
    )
    # A second row for the same day is refused.
    with pytest.raises(LeaveError, match="already recorded"):
        services.record_short_leave(
            actor=hr.user, employee=therapist, date=today.replace(day=3),
            hours=Decimal("1"),
        )
    # A therapist cannot record short leave at all.
    with pytest.raises(AccessDenied):
        services.record_short_leave(
            actor=therapist.user, employee=therapist,
            date=today.replace(day=15), hours=Decimal("1"),
        )

    # 3.5 hours at 3 h/day = 1 full day, deducted from the paid balance.
    converted = services.convert_short_leave(today.year, today.month, actor=hr.user)
    assert converted == 1
    conversion = ShortLeaveConversion.objects.get(
        employee=therapist, year=today.year, month=today.month
    )
    assert conversion.days == Decimal("1")
    assert conversion.deducted_days == Decimal("1") and conversion.lop_days == 0
    balance = services.balance_for(therapist, leave_config["paid"], today.year)
    assert balance.used >= Decimal("1")

    # Idempotent: running the conversion again changes nothing.
    assert services.convert_short_leave(today.year, today.month, actor=hr.user) == 0
    balance.refresh_from_db()
    assert balance.used == conversion.deducted_days


def test_short_leave_beyond_the_balance_is_loss_of_pay(staff, leave_config):
    hr, therapist = staff["hr_head"], staff["therapist"]
    today = timezone.localdate()

    # Drain the paid balance completely.
    balance = services.balance_for(therapist, leave_config["paid"], today.year)
    balance.used = balance.allocated + balance.carried_forward
    balance.save(update_fields=["used", "updated_at"])

    services.record_short_leave(
        actor=hr.user, employee=therapist, date=today.replace(day=5),
        hours=Decimal("6"),
    )
    services.convert_short_leave(today.year, today.month, actor=hr.user)
    conversion = ShortLeaveConversion.objects.get(
        employee=therapist, year=today.year, month=today.month
    )
    assert conversion.days == Decimal("2")
    assert conversion.deducted_days == 0 and conversion.lop_days == Decimal("2")
    # ...and payroll's seam includes it.
    assert services.get_lop_days(therapist, today.year, today.month) >= Decimal("2")


# ============================================================ holiday work


def test_holiday_work_needs_a_declared_holiday_and_is_unique(staff, leave_config):
    hr, therapist = staff["hr_head"], staff["therapist"]
    calendar = HolidayCalendar.objects.get(location__isnull=True)
    holiday = Holiday.objects.filter(calendar=calendar).first()
    assert holiday is not None  # the seeds declared the 2026 public holidays

    row = services.record_holiday_work(
        actor=hr.user, employee=therapist, date=holiday.date, note="Clinic open.",
    )
    assert row.holiday_name == holiday.name
    with pytest.raises(LeaveError, match="already recorded"):
        services.record_holiday_work(actor=hr.user, employee=therapist, date=holiday.date)
    with pytest.raises(LeaveError, match="not a declared public holiday"):
        services.record_holiday_work(
            actor=hr.user, employee=therapist,
            date=holiday.date + dt.timedelta(days=1),
        )
    assert services.holiday_work_dates(
        therapist, holiday.date.year, holiday.date.month
    ) == [holiday.date]

    # The double-pay switch is configuration.
    LeaveSettings.objects.filter(is_active=True).update(holiday_work_double_pay=False)
    assert services.holiday_work_dates(
        therapist, holiday.date.year, holiday.date.month
    ) == []


# ============================================================ patterns


def test_patterns_are_the_deciders_tool(staff, leave_config, api):
    therapist = staff["therapist"]
    start = _next_working_day(therapist, 7)
    services.apply_leave(
        actor=therapist.user, leave_type=leave_config["paid"],
        start_date=start, end_date=start, reason="Pattern fodder.",
    )

    def login(who):
        token = api.post(
            "/api/v1/auth/login/", {"email": f"{who}@policy.test", "password": PASSWORD}
        ).data["access"]
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    login("therapist")
    assert api.get("/api/v1/leave-requests/patterns/").status_code == 403

    login("hr_head")
    r = api.get(f"/api/v1/leave-requests/patterns/?year={start.year}")
    assert r.status_code == 200
    row = next(x for x in r.data["data"] if x["employee_code"] == therapist.employee_code)
    assert row["requests"] >= 1
