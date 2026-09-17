"""
Leave periodic tasks.

Each beat row names a dispatcher; the dispatcher queues one subtask per running
organization, and the subtask binds that organization for its whole body. See
`core/tasks.py`.

Nothing here passes an organization down into the services it calls. It does
not have to: every queryset inside them goes through the fail-closed manager,
which reads the bound tenant. That is the same property the request path
relies on, and it is why the conversion is a wrapper rather than a rewrite of
`apps/leave/services.py`.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from core.tasks import fan_out, organization_task

logger = logging.getLogger("hrms.leave")


@organization_task(name="leave.monthly_accrual.for_organization")
def monthly_accrual_for_organization(organization) -> int:
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
    logger.info(
        "leave.monthly_accrual organization=%s refreshed=%s", organization.pk, count
    )
    return count


@shared_task(name="leave.monthly_accrual")
def monthly_accrual() -> dict:
    return fan_out(monthly_accrual_for_organization)


@organization_task(name="leave.convert_short_leave.for_organization")
def convert_short_leave_for_organization(organization) -> int:
    """Settle last month's short-leave hours into day deductions."""
    from .services import convert_short_leave

    today = timezone.localdate()
    prev = today.replace(day=1) - timezone.timedelta(days=1)
    converted = convert_short_leave(prev.year, prev.month)
    logger.info(
        "leave.short_leave_converted organization=%s period=%s-%02d employees=%s",
        organization.pk, prev.year, prev.month, converted,
    )
    return converted


@shared_task(name="leave.convert_short_leave")
def convert_short_leave_previous_month() -> dict:
    return fan_out(convert_short_leave_for_organization)


@organization_task(name="leave.flag_absences.for_organization")
def flag_absences_for_organization(organization) -> int:
    """Flag uninformed multi-day absences to HR. No-op until attendance exists."""
    from .services import flag_unreported_absences

    flagged = flag_unreported_absences()
    if flagged:
        logger.info(
            "leave.absences_flagged organization=%s count=%s", organization.pk, flagged
        )
    return flagged


@shared_task(name="leave.flag_absences")
def flag_absences() -> dict:
    return fan_out(flag_absences_for_organization)
