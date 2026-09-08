"""
Leave periodic tasks. Scheduled by data migration into django_celery_beat,
the same way recruitment's retention purge is.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("hrms.leave")


@shared_task(name="leave.monthly_accrual")
def monthly_accrual() -> int:
    """
    Credit the month's accrual for every active employee on every accruing
    policy. Idempotent — `_refresh_accrual` only tops up to the target.
    """
    from apps.employees.models import Employee

    from .services import ensure_balances

    count = 0
    year = timezone.localdate().year
    for employee in Employee.objects.filter(is_active=True, user__isnull=False):
        ensure_balances(employee, year=year)
        count += 1
    logger.info("leave.monthly_accrual refreshed=%s", count)
    return count


@shared_task(name="leave.convert_short_leave")
def convert_short_leave_previous_month() -> int:
    """Settle last month's short-leave hours into day deductions."""
    from .services import convert_short_leave

    today = timezone.localdate()
    prev = (today.replace(day=1) - timezone.timedelta(days=1))
    converted = convert_short_leave(prev.year, prev.month)
    logger.info("leave.short_leave_converted period=%s-%02d employees=%s",
                prev.year, prev.month, converted)
    return converted


@shared_task(name="leave.flag_absences")
def flag_absences() -> int:
    """Flag uninformed multi-day absences to HR. No-op until attendance exists."""
    from .services import flag_unreported_absences

    flagged = flag_unreported_absences()
    if flagged:
        logger.info("leave.absences_flagged count=%s", flagged)
    return flagged
