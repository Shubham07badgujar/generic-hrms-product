"""
The day-status matrix, under the company's own timing chart.

10:00 start, 15-minute grace (late after 10:15), three lates tolerated per
month with the fourth becoming a half day, short hours a half day, no
punches on a working day an absence — and HR's word always standing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.attendance.models import AttendanceRecord, RecordSource, RecordStatus
from apps.attendance.services.calculation import recompute_day

pytestmark = pytest.mark.django_db

# 2026-08-25 is a Tuesday.
DAY = dt.date(2026, 8, 25)


def test_on_time_full_day_is_present(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:02", "19:01"])
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.is_late is False
    assert record.worked_minutes == (19 * 60 + 1) - (10 * 60 + 2)


def test_the_grace_window_is_not_late(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:15", "19:00"])
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.is_late is False


def test_after_grace_is_late_but_still_present(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:16", "19:00"])
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.PRESENT
    assert record.is_late is True
    assert record.late_minutes == 1


def test_the_fourth_late_of_the_month_is_a_half_day(worker, shift_rule, punch_day):
    """The chart's rule: three lates tolerated, the fourth costs half a day."""
    # Mon 24th, Wed 26th, Thu 27th late — three tolerated lates on record.
    for day in (24, 26, 27):
        date = dt.date(2026, 8, day)
        punch_day(date, ["10:30", "19:00"])
        assert recompute_day(worker, date).status == RecordStatus.PRESENT

    date = dt.date(2026, 8, 28)
    punch_day(date, ["10:30", "19:00"])
    record = recompute_day(worker, date)
    assert record.is_late is True
    assert record.status == RecordStatus.HALF_DAY
    assert "no. 4" in record.notes


def test_short_hours_are_a_half_day(worker, shift_rule, punch_day):
    punch_day(DAY, ["10:05", "13:30"])  # 3h25m < 4.5h threshold
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.HALF_DAY


def test_a_working_day_with_no_punches_is_absent(worker, shift_rule):
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.ABSENT


def test_an_approved_leave_day_is_on_leave_not_absent(worker, shift_rule, org, roles):
    from apps.leave.models import LeaveRequest, LeaveStatus, LeaveType

    leave_type = LeaveType.objects.create(code="tst", name="Test Leave")
    LeaveRequest.objects.create(
        employee=worker, leave_type=leave_type, start_date=DAY, end_date=DAY,
        days=1, reason="test", status=LeaveStatus.APPROVED,
    )
    record = recompute_day(worker, DAY)
    assert record.status == RecordStatus.ON_LEAVE


def test_a_holiday_without_punches_stores_nothing(worker, shift_rule):
    from apps.leave.models import Holiday, HolidayCalendar

    calendar = HolidayCalendar.objects.create(name="Test", weekly_off=[6])
    Holiday.objects.create(calendar=calendar, date=DAY, name="Festival")

    assert recompute_day(worker, DAY) is None
    assert AttendanceRecord.objects.filter(employee=worker, date=DAY).count() == 0


def test_a_weekly_off_without_punches_stores_nothing(worker, shift_rule):
    from apps.leave.models import HolidayCalendar

    HolidayCalendar.objects.create(name="Test", weekly_off=[DAY.weekday()])
    assert recompute_day(worker, DAY) is None


def test_hr_corrections_are_never_recomputed(worker, shift_rule, punch_day):
    """Requirement 13, pinned: the machine defers to people."""
    AttendanceRecord.objects.create(
        employee=worker, date=DAY, status=RecordStatus.PRESENT,
        source=RecordSource.MANUAL, notes="HR corrected after a device fault.",
    )
    punch_day(DAY, ["13:00", "13:05"])  # would compute a half day

    assert recompute_day(worker, DAY) is None
    record = AttendanceRecord.objects.get(employee=worker, date=DAY)
    assert record.source == RecordSource.MANUAL
    assert record.status == RecordStatus.PRESENT


def test_days_outside_employment_are_ignored(worker, shift_rule, punch_day):
    before_joining = dt.date(2025, 12, 30)
    assert recompute_day(worker, before_joining) is None
