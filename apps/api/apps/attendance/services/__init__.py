"""
The attendance app's public service surface.

Payroll and leave were built against this module BEFORE it existed:
`apps/payroll/services/runs.py` try-imports `monthly_summary` and
`apps/leave/services.py` try-imports `was_present`. Both fall back to
"assume presence" when the import fails — which means merely creating this
file changes their behavior. The guards below exist so that it does NOT:

  * `monthly_summary` returns the exact assumed-present arithmetic unless
    ATTENDANCE_AFFECTS_PAYROLL is true AND the employee has an eSSL mapping
    AND the month actually has device coverage. Report-only by default —
    a device outage or an unenrolled joiner can never cut anyone's pay.
  * `was_present` answers True (never-flag) unless the integration is
    enabled and the employee is mapped, so the leave module's absence flag
    stays exactly as quiet as it was.

CONTRACT NOTES (from the payroll seam's own documentation): paid_days is on a
CALENDAR-day basis, and must already be net of approved unpaid leave — the
caller uses it verbatim.

Import-time purity matters here: payroll detects this app by ImportError, so
nothing in this package may import `requests` (or anything optional) at
module scope.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from .calculation import recompute_day, recompute_days, rule_for  # noqa: F401
from .essl_client import essl_enabled  # noqa: F401

ZERO = Decimal("0")
HALF = Decimal("0.5")


@dataclass(frozen=True)
class MonthlySummary:
    paid_days: Decimal
    lop_days: Decimal
    present_days: int
    half_days: int
    absent_days: int
    late_days: int
    #: False when the assumed-present fallback produced the figures.
    from_attendance: bool


def _lop(employee, year: int, month: int) -> Decimal:
    try:
        from apps.leave.services import get_lop_days
    except ImportError:
        return ZERO
    return Decimal(str(get_lop_days(employee, year, month)))


def attendance_drives_payroll() -> bool:
    return bool(getattr(settings, "ATTENDANCE_AFFECTS_PAYROLL", False)) and essl_enabled()


def is_mapped_to_a_device(employee) -> bool:
    """
    Does this employee have at least one live eSSL mapping?

    An employee may hold several — one per site they work at — so this asks
    the reverse relation whether ANY is live. It must never be written as a
    `hasattr` check: the accessor is a manager that always exists, so the
    check would answer True for an unmapped employee and let the callers
    below trust device data that does not exist.
    """
    return employee.essl_links.filter(is_active=True).exists()


def monthly_summary(employee, year: int, month: int) -> MonthlySummary:
    """
    Days payable for the period, per the seam contract.

    Three independent guards keep this identical to the pre-integration
    arithmetic until each is deliberately satisfied: the payroll switch, the
    employee's mapping, and actual device coverage for the month.
    """
    from apps.attendance.models import RawPunch, RecordStatus

    total = Decimal(_calendar.monthrange(year, month)[1])
    lop = _lop(employee, year, month)
    fallback = MonthlySummary(
        paid_days=max(total - lop, ZERO), lop_days=lop,
        present_days=0, half_days=0, absent_days=0, late_days=0,
        from_attendance=False,
    )

    if not attendance_drives_payroll():
        return fallback
    if not is_mapped_to_a_device(employee):
        return fallback
    month_start = dt.date(year, month, 1)
    month_end = dt.date(year, month, int(total))
    covered = RawPunch.objects.filter(
        punched_at__date__gte=month_start, punched_at__date__lte=month_end
    ).exists()
    if not covered:
        return fallback

    from apps.attendance.models import AttendanceRecord

    records = AttendanceRecord.objects.active().filter(
        employee=employee, date__gte=month_start, date__lte=month_end
    )
    absent = records.filter(status=RecordStatus.ABSENT).count()
    half = records.filter(status=RecordStatus.HALF_DAY).count()
    present = records.filter(status=RecordStatus.PRESENT).count()
    late = records.filter(is_late=True).count()

    paid = max(total - lop - Decimal(absent) - HALF * Decimal(half), ZERO)
    return MonthlySummary(
        paid_days=paid, lop_days=lop,
        present_days=present, half_days=half, absent_days=absent, late_days=late,
        from_attendance=True,
    )


def was_present(employee, date: dt.date) -> bool:
    """
    Presence check for the leave module's uninformed-absence flag.

    Conservative by construction: any state in which the devices cannot vouch
    for the person — integration off, employee unmapped — answers True, so
    the flag never fires on missing data.
    """
    if not essl_enabled():
        return True
    if not is_mapped_to_a_device(employee):
        return True

    from apps.attendance.models import AttendanceRecord, RawPunch, RecordStatus

    record = AttendanceRecord.objects.active().filter(employee=employee, date=date).first()
    if record is not None:
        return record.status in (
            RecordStatus.PRESENT, RecordStatus.HALF_DAY,
            RecordStatus.ON_LEAVE, RecordStatus.HOLIDAY, RecordStatus.WEEKLY_OFF,
        )
    return RawPunch.objects.filter(employee=employee, punched_at__date=date).exists()



def month_calendar(employee, year: int, month: int) -> dict:
    """
    One employee's month, day by day, in the shape HR's calendar renders.

    Each day resolves to ONE kind, in priority order: the attendance record
    (device truth, or an HR correction) wins; then an approved leave; then a
    holiday; then the weekly off; a past working day with none of those is
    'no_record', a future one is 'future'. The summary counts the same
    resolution, so the header and the grid can never disagree.
    """
    import calendar as _cal
    import datetime as _dt
    from decimal import Decimal as _D

    from django.utils import timezone as _tz

    from apps.attendance.models import AttendanceRecord
    from apps.leave.models import Holiday, LeaveRequest, LeaveStatus
    from apps.leave.services import DEFAULT_WEEKLY_OFF, calendar_for

    total = _cal.monthrange(year, month)[1]
    month_start = _dt.date(year, month, 1)
    month_end = _dt.date(year, month, total)
    today = _tz.localdate()

    holiday_calendar = calendar_for(employee)
    weekly_off = set(
        holiday_calendar.weekly_off
        if holiday_calendar and holiday_calendar.weekly_off else DEFAULT_WEEKLY_OFF
    )
    holidays = {}
    if holiday_calendar:
        holidays = {
            row.date: row.name
            for row in Holiday.objects.filter(
                calendar=holiday_calendar, date__range=(month_start, month_end),
                is_active=True,
            )
        }

    records = {
        row.date: row
        for row in AttendanceRecord.objects.filter(
            employee=employee, date__range=(month_start, month_end), is_active=True
        )
    }

    leave_days: dict = {}
    for request_row in LeaveRequest.objects.filter(
        employee=employee, is_active=True, status=LeaveStatus.APPROVED,
        start_date__lte=month_end, end_date__gte=month_start,
    ).select_related("leave_type"):
        day = max(request_row.start_date, month_start)
        last = min(request_row.end_date, month_end)
        while day <= last:
            leave_days[day] = {
                "leave_type": request_row.leave_type.name,
                "half_day": bool(request_row.half_day),
                "unpaid": (not request_row.leave_type.is_paid) or request_row.probation_unpaid,
            }
            day += _dt.timedelta(days=1)

    days = []
    summary = {
        "working_days": 0, "present_days": 0, "absent_days": 0, "half_days": 0,
        "late_days": 0, "leave_days": _D("0"), "week_offs": 0, "holidays": 0,
        "no_record_days": 0,
    }

    for offset in range(total):
        day = month_start + _dt.timedelta(days=offset)
        is_off = day.weekday() in weekly_off
        holiday_name = holidays.get(day)
        employed = not (
            (employee.date_of_joining and day < employee.date_of_joining)
            or (employee.date_of_exit and day > employee.date_of_exit)
        )
        if employed and not is_off and holiday_name is None:
            summary["working_days"] += 1

        record = records.get(day)
        leave = leave_days.get(day)
        cell = {
            "date": day.isoformat(),
            "weekday": day.strftime("%a"),
            "is_late": False,
            "label": "",
            "first_in": None, "last_out": None, "worked_minutes": None,
        }
        if not employed and record is None:
            cell["kind"] = "not_employed"
            cell["label"] = (
                "Before joining"
                if employee.date_of_joining and day < employee.date_of_joining
                else "After exit"
            )
        elif record is not None:
            cell["kind"] = record.status  # present / absent / half_day
            cell["is_late"] = record.is_late
            cell["label"] = record.get_status_display() + (" · Late" if record.is_late else "")
            cell["first_in"] = (
                _tz.localtime(record.first_in).strftime("%H:%M") if record.first_in else None
            )
            cell["last_out"] = (
                _tz.localtime(record.last_out).strftime("%H:%M") if record.last_out else None
            )
            cell["worked_minutes"] = record.worked_minutes
            if record.status == "present":
                summary["present_days"] += 1
            elif record.status == "absent":
                summary["absent_days"] += 1
            elif record.status == "half_day":
                summary["half_days"] += 1
            if record.is_late:
                summary["late_days"] += 1
        elif leave is not None and not is_off and holiday_name is None:
            cell["kind"] = "leave"
            cell["label"] = leave["leave_type"] + (
                " (half day)" if leave["half_day"] else ""
            ) + (" · unpaid" if leave["unpaid"] else "")
            summary["leave_days"] += _D("0.5") if leave["half_day"] else _D("1")
        elif holiday_name is not None:
            cell["kind"] = "holiday"
            cell["label"] = holiday_name
            summary["holidays"] += 1
        elif is_off:
            cell["kind"] = "week_off"
            cell["label"] = "Week off"
            summary["week_offs"] += 1
        elif day > today:
            cell["kind"] = "future"
        else:
            cell["kind"] = "no_record"
            cell["label"] = "No record"
            summary["no_record_days"] += 1
        days.append(cell)

    summary["leave_days"] = str(summary["leave_days"])
    return {
        "employee": {
            "id": str(employee.pk),
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "department": employee.department.name if employee.department else None,
        },
        "year": year, "month": month,
        "summary": summary,
        "days": days,
    }
