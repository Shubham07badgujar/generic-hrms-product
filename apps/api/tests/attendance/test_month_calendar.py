"""
The employee-month calendar: every day resolved to exactly one kind, and a
summary that counts the same resolution the grid shows.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.attendance.models import AttendanceRecord, RecordSource, RecordStatus
from apps.employees.models import Employee
from apps.leave.models import (
    HolidayCalendar, Holiday, LeaveRequest, LeaveStatus, LeaveType,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def hr(make_user, org):
    user = make_user("hr_head", email="cal.hr@x.test")
    Employee.objects.create(
        employee_code="EMP08100", first_name="Hema", user=user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )
    return user


@pytest.fixture
def worker(make_user, org):
    user = make_user("employee", email="cal.worker@x.test")
    return Employee.objects.create(
        employee_code="EMP08101", first_name="Cal", user=user,
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2025, 1, 1),
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_the_month_resolves_every_kind(hr, worker, org):
    # Calendar: Sunday off, one holiday (Fri 15 Aug).
    calendar = HolidayCalendar.objects.create(
        name="HO", location=org["location"], weekly_off=[6],
    )
    Holiday.objects.create(calendar=calendar, date=dt.date(2025, 8, 15),
                           name="Independence Day")

    # Device truth: present 11th (late), absent 12th, half day 13th.
    for day, status, late in [
        (11, RecordStatus.PRESENT, True),
        (12, RecordStatus.ABSENT, False),
        (13, RecordStatus.HALF_DAY, False),
    ]:
        AttendanceRecord.objects.create(
            employee=worker, date=dt.date(2025, 8, day), status=status,
            is_late=late, source=RecordSource.DEVICE,
        )

    # Approved paid leave on the 14th.
    leave_type = LeaveType.objects.create(name="Privilege Leave", code="PL", is_paid=True)
    LeaveRequest.objects.create(
        employee=worker, leave_type=leave_type,
        start_date=dt.date(2025, 8, 14), end_date=dt.date(2025, 8, 14),
        days=1, status=LeaveStatus.APPROVED, reason="x",
    )

    response = _client(hr).get("/api/v1/attendance-records/month/", {
        "employee": str(worker.pk), "year": 2025, "month": 8,
    })
    assert response.status_code == 200, response.content
    body = response.json()
    days = {row["date"]: row for row in body["days"]}

    assert days["2025-08-11"]["kind"] == "present" and days["2025-08-11"]["is_late"] is True
    assert days["2025-08-12"]["kind"] == "absent"
    assert days["2025-08-13"]["kind"] == "half_day"
    assert days["2025-08-14"]["kind"] == "leave"
    assert "Privilege Leave" in days["2025-08-14"]["label"]
    assert days["2025-08-15"]["kind"] == "holiday"
    assert days["2025-08-17"]["kind"] == "week_off"       # a Sunday
    assert days["2025-08-18"]["kind"] == "no_record"      # past working day, nothing

    summary = body["summary"]
    assert summary["present_days"] == 1
    assert summary["absent_days"] == 1
    assert summary["half_days"] == 1
    assert summary["late_days"] == 1
    assert summary["leave_days"] == "1"
    assert summary["holidays"] == 1
    assert summary["week_offs"] == 5                      # Sundays in Aug 2025
    assert summary["working_days"] == 31 - 5 - 1          # minus offs and holiday


def test_scoping_an_employee_sees_only_their_own_month(hr, worker, make_user, org):
    other_user = make_user("employee", email="cal.other@x.test")
    Employee.objects.create(
        employee_code="EMP08102", first_name="Other", user=other_user,
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )
    # Their own month works…
    own = _client(worker.user).get("/api/v1/attendance-records/month/", {
        "employee": str(worker.pk), "year": 2025, "month": 8,
    })
    assert own.status_code == 200
    # …someone else's is a 400 that reveals nothing.
    refused = _client(other_user).get("/api/v1/attendance-records/month/", {
        "employee": str(worker.pk), "year": 2025, "month": 8,
    })
    assert refused.status_code == 400
