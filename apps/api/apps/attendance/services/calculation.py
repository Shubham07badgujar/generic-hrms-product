"""
From punches to a day's verdict, under the company's own timing chart.

THE POLICY (configuration, not constants — see ShiftRule), hours first:
  * Day = first punch in, last punch out, at the employee's location's rule.
  * 10:00-10:15 (grace): present, no late mark. After grace: LATE.
  * A late that still completes `full_day_hours` is COMPENSATED: the day is
    Present, the late stays visible (is_late) but is never COUNTED — it does
    not feed the monthly allowance and cannot cause a half day.
  * The month's first `allowed_late_per_month` COUNTED lates stay ordinary
    Present days; every counted late beyond that is a HALF DAY.
  * Worked hours below `half_day_below_hours` are a half day regardless.
  * A single-punch day cannot prove its hours: status stands, but the day is
    noted for HR's exception review.
  * A working day with no punches is ABSENT — unless an approved leave,
    a holiday or a weekly off explains it, which each get their own status.

PROTECTION ORDER: a record whose source is `manual` or `regularized` is never
recomputed. HR's correction outlives every sync.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from django.utils import timezone

from apps.attendance.models import (
    AttendanceRecord,
    RawPunch,
    RecordSource,
    RecordStatus,
    ShiftRule,
)

logger = logging.getLogger("hrms.attendance")


def rule_for(employee) -> ShiftRule | None:
    """The employee's location's rule, else the org default, else nothing."""
    if employee.location_id:
        rule = ShiftRule.objects.active().filter(location_id=employee.location_id).first()
        if rule:
            return rule
    return ShiftRule.objects.active().filter(location__isnull=True).first()


def _day_context(employee, date: dt.date) -> str | None:
    """Holiday / weekly-off / approved-leave verdicts, reusing leave's calendar."""
    from apps.leave.models import LeaveRequest, LeaveStatus
    from apps.leave.services import working_days

    if working_days(employee, date, date) <= Decimal("0"):
        # The leave module's calendar decides WHICH kind of non-working day;
        # holidays are dated rows, weekly offs are weekday numbers.
        from apps.leave.services import calendar_for

        calendar = calendar_for(employee)
        if calendar and calendar.holidays.filter(date=date, is_active=True).exists():
            return RecordStatus.HOLIDAY
        return RecordStatus.WEEKLY_OFF

    on_leave = LeaveRequest.objects.filter(
        employee=employee,
        status=LeaveStatus.APPROVED,
        is_active=True,
        start_date__lte=date,
        end_date__gte=date,
    ).exists()
    return RecordStatus.ON_LEAVE if on_leave else None


def _late_count_this_month(employee, date: dt.date, *, excluding: dt.date) -> int:
    """COUNTED late marks already recorded this month, before this day.

    Compensated lates (is_late without late_counted) deliberately do not
    appear here — a made-up day must never push a later one into a half day.
    """
    return AttendanceRecord.objects.active().filter(
        employee=employee,
        date__year=date.year,
        date__month=date.month,
        date__lt=excluding,
        late_counted=True,
    ).count()


def recompute_day(employee, date: dt.date) -> AttendanceRecord | None:
    """
    Compute (or refresh) one employee-day from punches.

    Returns the record, or None when the day was protected (manual /
    regularized) or lies outside employment.
    """
    if date < employee.date_of_joining:
        return None
    if employee.date_of_exit and date > employee.date_of_exit:
        return None

    existing = AttendanceRecord.objects.active().filter(employee=employee, date=date).first()
    if existing and existing.source != RecordSource.DEVICE:
        return None  # HR's word stands.

    rule = rule_for(employee)
    tz = timezone.get_current_timezone()
    day_start = dt.datetime.combine(date, dt.time.min, tzinfo=tz)
    day_end = day_start + dt.timedelta(days=1)

    punches = list(
        RawPunch.objects.filter(
            employee=employee, punched_at__gte=day_start, punched_at__lt=day_end
        ).order_by("punched_at")
    )

    fields: dict = {
        "first_in": None,
        "last_out": None,
        "worked_minutes": 0,
        "is_late": False,
        "late_minutes": 0,
        "late_counted": False,
        # A recomputed verdict needs re-review; manual/regularized rows are
        # never recomputed, so an HR review on those is never cleared.
        "reviewed_by": None,
        "reviewed_at": None,
        "source": RecordSource.DEVICE,
        "notes": "",
    }

    context = _day_context(employee, date)
    if punches:
        first_in = punches[0].punched_at
        last_out = punches[-1].punched_at
        worked = int((last_out - first_in).total_seconds() // 60) if len(punches) > 1 else 0
        fields.update(first_in=first_in, last_out=last_out, worked_minutes=worked)

        status = RecordStatus.PRESENT
        if rule:
            local_in = first_in.astimezone(tz)
            cutoff = dt.datetime.combine(
                date, rule.start_time, tzinfo=tz
            ) + dt.timedelta(minutes=rule.grace_minutes)
            if local_in > cutoff:
                fields["is_late"] = True
                late_minutes = int((local_in - cutoff).total_seconds() // 60)
                fields["late_minutes"] = late_minutes
                # Hours first: a late made up by completing the full day is
                # recorded but never COUNTED — it neither reads the monthly
                # tally nor enters it.
                completed = (
                    len(punches) > 1
                    and Decimal(worked) / Decimal(60) >= rule.full_day_hours
                )
                if completed:
                    fields["notes"] = (
                        f"Late {late_minutes}m, compensated "
                        f"(worked {worked // 60}h{worked % 60:02d}m)."
                    )
                else:
                    fields["late_counted"] = True
                    prior_lates = _late_count_this_month(employee, date, excluding=date)
                    if prior_lates >= rule.allowed_late_per_month:
                        status = RecordStatus.HALF_DAY
                        fields["notes"] = (
                            f"Late arrival no. {prior_lates + 1} this month "
                            f"(allowed {rule.allowed_late_per_month})."
                        )
            if (
                status == RecordStatus.PRESENT
                and len(punches) > 1
                and Decimal(worked) / Decimal(60) < rule.half_day_below_hours
            ):
                status = RecordStatus.HALF_DAY
                fields["notes"] = (
                    f"Worked {worked // 60}h{worked % 60:02d}m, below the "
                    f"{rule.half_day_below_hours}h half-day threshold."
                )
        if len(punches) == 1 and status == RecordStatus.PRESENT:
            # One punch proves presence but not hours — surfaced for HR's
            # exception review rather than guessed either way.
            fields["notes"] = (fields["notes"] + " " if fields["notes"] else "") + (
                "Single punch — worked hours unverifiable."
            )
        fields["status"] = status
    else:
        # No punches: the calendar or an approved leave explains it, or it
        # is an absence. Non-working days without punches are not stored at
        # all — a weekly off is not attendance data.
        if context in (RecordStatus.HOLIDAY, RecordStatus.WEEKLY_OFF):
            if existing:
                existing.delete()
            return None
        fields["status"] = context or RecordStatus.ABSENT

    # Presence on a holiday/weekly-off stays PRESENT — the leave module's
    # holiday-work double-pay flow handles the compensation side.
    record, _created = AttendanceRecord.objects.update_or_create(
        employee=employee,
        date=date,
        is_active=True,
        defaults=fields,
    )
    return record


def recompute_days(pairs: set[tuple]) -> int:
    """Recompute a set of (employee, date) pairs; returns how many were written."""
    written = 0
    # Ascending by date per employee: the monthly late tally reads earlier
    # days, so order decides correctness, not just determinism.
    for employee, date in sorted(pairs, key=lambda p: (p[0].pk, p[1])):
        try:
            if recompute_day(employee, date) is not None:
                written += 1
        except Exception:  # noqa: BLE001 — one bad day must cost one day
            logger.exception(
                "attendance.recompute_failed employee=%s date=%s", employee.pk, date
            )
    return written
