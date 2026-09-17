"""
Attendance background jobs.

The beat schedule lives in `manage.py sync_beat_schedule`, run on every deploy.
The row there names the DISPATCHER; the dispatcher queues one subtask per
running organization. See `core/tasks.py` for why the fan-out is one task per
customer rather than one scheduler row per customer.

Each subtask is a thin, always-safe wrapper: it no-ops unless THIS
organization can reach a device service, and enforces ESSL_SYNC_INTERVAL as a
minimum gap so the deployment's setting governs pace even though beat's row
says every five minutes.
"""

from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from core.tasks import fan_out, organization_task

logger = logging.getLogger("hrms.attendance")


@organization_task(name="attendance.sync_essl.for_organization")
def sync_essl_for_organization(organization) -> dict:
    """
    Pull punches for one customer's devices.

    The integration check is per-organization now. One customer having no
    endpoint configured is not a reason to skip the customer after them, which
    is exactly what a single global check inside a loop would have done.
    """
    from apps.attendance.models import AttendanceDevice, EsslSyncRun, SyncKind
    from apps.attendance.services.essl_client import essl_enabled
    from apps.attendance.services.sync import sync_all

    if not essl_enabled(organization):
        return {"skipped": True, "reason": "integration not configured"}

    if not AttendanceDevice.objects.active().filter(is_enabled=True).exists():
        # Nothing to pull from yet; a run row every five minutes would only
        # bury the real history once devices ARE registered.
        return {"skipped": True, "reason": "no devices"}

    gap = dt.timedelta(minutes=int(getattr(settings, "ESSL_SYNC_INTERVAL", 5)))
    last = (
        EsslSyncRun.objects.active()
        .filter(kind=SyncKind.SCHEDULED)
        .order_by("-started_at")
        .first()
    )
    if last and timezone.now() - last.started_at < gap:
        return {"skipped": True, "reason": "interval"}

    run = sync_all(kind=SyncKind.SCHEDULED)
    return {
        "status": run.status,
        "devices": run.devices_total,
        "failed": run.devices_failed,
        "created": run.punches_created,
        "duplicate": run.punches_duplicate,
        "unmapped": run.punches_unmapped,
    }


@shared_task(name="attendance.sync_essl")
def sync_essl() -> dict:
    """One subtask per running organization. The beat row points here."""
    return fan_out(sync_essl_for_organization)
