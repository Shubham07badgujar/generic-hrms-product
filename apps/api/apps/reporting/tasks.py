"""
Scheduled BI work.

The beat row names a dispatcher, which queues one subtask per running
organization. See `core/tasks.py` for the rule and the reasoning.

The subtask hands its organization to `refresh_snapshots` explicitly rather
than relying on the bound tenant alone. Snapshots are computed by an
unrestricted principal, which skips `scope_queryset()` and with it the org
predicate the request path applies first; the organization argument is what
puts that predicate back. The tenant manager filters these models as well, so
the argument is a second answer rather than the only one -- which is what it
should be for a path whose whole purpose is to step around request scoping.
"""

from __future__ import annotations

import logging

from celery import shared_task

from core.tasks import fan_out, organization_task

logger = logging.getLogger("hrms.reporting")


@organization_task(name="reporting.refresh_snapshots.for_organization")
def refresh_snapshots_for_organization(organization) -> int:
    """Materialise one organization's snapshotable metrics."""
    from .services import refresh_snapshots

    written = refresh_snapshots(organization)
    logger.info(
        "reporting.snapshots_refreshed organization=%s written=%s",
        organization.pk, written,
    )
    return written


@shared_task(name="reporting.refresh_snapshots")
def refresh_snapshots_task() -> dict:
    return fan_out(refresh_snapshots_for_organization)
