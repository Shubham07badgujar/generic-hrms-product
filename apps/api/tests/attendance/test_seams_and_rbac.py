"""
The two seams payroll and leave built in advance, and the permission fence.

The highest-stakes claims in the whole integration: while the report-only
switch is off, payroll's numbers are IDENTICAL to the pre-integration
arithmetic; and the eSSL admin surface answers 403 to an employee.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.attendance.models import AttendanceRecord, RecordStatus

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 8, 25)


# ------------------------------------------------------------- payroll seam


def test_report_only_mode_leaves_payroll_arithmetic_untouched(
    essl_settings, worker, shift_rule, punch_day, settings
):
    """The default: attendance exists, pay does not move."""
    from apps.payroll.services.runs import paid_and_lop_days

    settings.ATTENDANCE_AFFECTS_PAYROLL = False
    punch_day(DAY, ["13:00", "13:05"])  # a computed half day, were it counted
    from apps.attendance.services.calculation import recompute_day

    recompute_day(worker, DAY)

    paid, lop = paid_and_lop_days(worker, 2026, 8)
    assert paid == Decimal("31")  # August: every calendar day paid
    assert lop == Decimal("0")


def test_with_the_switch_on_absences_and_half_days_reduce_paid_days(
    essl_settings, worker, shift_rule, punch_day, settings
):
    from apps.attendance.services import monthly_summary
    from apps.payroll.services.runs import paid_and_lop_days

    settings.ATTENDANCE_AFFECTS_PAYROLL = True
    from apps.attendance.services.calculation import recompute_day

    punch_day(DAY, ["13:00", "13:05"])          # half day
    recompute_day(worker, DAY)
    recompute_day(worker, dt.date(2026, 8, 26))  # no punches: absent

    summary = monthly_summary(worker, 2026, 8)
    assert summary.from_attendance is True
    assert summary.half_days == 1
    assert summary.absent_days == 1
    assert summary.paid_days == Decimal("31") - Decimal("1") - Decimal("0.5")

    paid, _lop = paid_and_lop_days(worker, 2026, 8)
    assert paid == summary.paid_days


def test_an_unmapped_employee_is_never_pay_cut(essl_settings, org, settings):
    """Guard two of three: no mapping, no attendance-driven arithmetic."""
    from apps.attendance.services import monthly_summary
    from apps.employees.models import Employee

    settings.ATTENDANCE_AFFECTS_PAYROLL = True
    unmapped = Employee.objects.create(
        employee_code="EMP09003", first_name="No", last_name="Mapping",
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    summary = monthly_summary(unmapped, 2026, 8)
    assert summary.from_attendance is False
    assert summary.paid_days == Decimal("31")


def test_a_month_without_device_coverage_falls_back(
    essl_settings, worker, shift_rule, settings
):
    """Guard three: devices silent all month -> assume presence, not absence."""
    from apps.attendance.services import monthly_summary

    settings.ATTENDANCE_AFFECTS_PAYROLL = True
    summary = monthly_summary(worker, 2026, 7)  # no punches exist in July
    assert summary.from_attendance is False
    assert summary.paid_days == Decimal("31")


# --------------------------------------------------------------- leave seam


def test_was_present_is_conservative(essl_settings, worker, shift_rule, punch_day, settings):
    from apps.attendance.services import was_present

    # Integration off: always True — the absence flag stays quiet.
    settings.ESSL_INTEGRATION_ENABLED = False
    assert was_present(worker, DAY) is True

    settings.ESSL_INTEGRATION_ENABLED = True
    # Mapped, with a computed absent day: genuinely not present.
    from apps.attendance.services.calculation import recompute_day

    recompute_day(worker, DAY)
    assert was_present(worker, DAY) is False

    # A punch on the day answers True even before any recompute.
    punch_day(dt.date(2026, 8, 26), ["10:01"])
    assert was_present(worker, dt.date(2026, 8, 26)) is True


# ------------------------------------------------------------------- RBAC


def _client_for(user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_employees_cannot_reach_the_essl_admin_surface(worker, make_user, roles):
    """Requirement 12: configuration and mappings are closed to employees."""
    user = make_user("employee", email="worker@example.test")
    worker.user = user
    worker.save(update_fields=["user"])

    client = _client_for(user)
    for path in (
        "/api/v1/essl/devices/",
        "/api/v1/essl/devices/status/",
        "/api/v1/essl/mappings/",
        "/api/v1/essl/devices/unmapped/",
        "/api/v1/essl/devices/runs/",
    ):
        assert client.get(path).status_code == 403, path
    assert client.post("/api/v1/essl/devices/sync-now/", {}).status_code == 403

    # Their own attendance, by contrast, is theirs to read.
    assert client.get("/api/v1/attendance-records/").status_code == 200


def test_hr_head_holds_the_whole_admin_surface(make_user, org, roles):
    from apps.employees.models import Employee

    user = make_user("hr_head", email="hrhead@example.test")
    Employee.objects.create(
        employee_code="EMP09010", first_name="Hema", user=user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )

    client = _client_for(user)
    assert client.get("/api/v1/essl/devices/").status_code == 200
    assert client.get("/api/v1/essl/devices/status/").status_code == 200
    assert client.get("/api/v1/essl/mappings/").status_code == 200
    # Sync-now with the integration disabled is a clean 400, not a crash.
    response = client.post("/api/v1/essl/devices/sync-now/", {})
    assert response.status_code == 400
    assert "disabled" in str(response.data)
