"""
Attendance background jobs.

The beat schedule lives in django-celery-beat rows (registered by data
migration, the house pattern) — a settings dict would be read by nobody.
The task itself is a thin, always-safe wrapper: it no-ops unless the
integration is enabled, and enforces ESSL_SYNC_INTERVAL as a minimum gap so
the env var governs pace even though beat's row says every 5 minutes.
"""

from __future__ import annotations

import datetime as dt
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("hrms.attendance")


@shared_task(name="attendance.sync_essl")
def sync_essl() -> dict:
    from apps.attendance.models import EsslSyncRun, SyncKind
    from apps.attendance.services.essl_client import essl_enabled
    from apps.attendance.services.sync import sync_all

    if not essl_enabled():
        return {"skipped": True}

    from apps.attendance.models import AttendanceDevice

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
