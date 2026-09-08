"""
Pulling punches from the eTimeTrackLite server into the HRMS.

DOCTRINE (shared with the candidate importer): idempotent re-processing makes
recovery free. Every scheduled pull re-reads an overlapping window; the
RawPunch unique constraint means the overlap costs nothing, and a sync that
died halfway needs no bookkeeping — the next tick simply reads again.

Per-device failures are recorded on the device and the run, never raised: the
next tick is the retry (the same convention as the Google Forms sync).
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import (
    AttendanceDevice,
    EsslEmployeeLink,
    EsslSyncRun,
    RawPunch,
    RunStatus,
    SyncKind,
    SyncStatus,
)
from core.access import Resource

from . import essl_client
from .calculation import recompute_days

logger = logging.getLogger("hrms.attendance")

#: How far behind last_synced_at each incremental pull re-reads. Idempotent,
#: so generosity is free — it covers device clock drift and late uploads.
OVERLAP = dt.timedelta(days=1)
#: Historical pulls are chunked so no single response is unbounded.
RANGE_CHUNK_DAYS = 7


def _ingest(device: AttendanceDevice, rows: list[essl_client.PunchRow]) -> dict:
    """
    Insert one device's fetched rows. The constraint eats duplicates.

    ignore_conflicts cannot report which rows were skipped (and this model's
    UUID pks are assigned client-side anyway), so what already exists is read
    first — the natural key is small and indexed.
    """
    if not rows:
        return {"fetched": 0, "created": 0, "duplicate": 0, "unmapped": 0, "affected": set()}

    links = dict(
        EsslEmployeeLink.objects.active()
        .filter(essl_user_id__in={r.essl_user_id for r in rows})
        .values_list("essl_user_id", "employee_id")
    )
    lo = min(r.punched_at for r in rows)
    hi = max(r.punched_at for r in rows)
    existing = set(
        RawPunch.objects.filter(
            device=device, punched_at__gte=lo, punched_at__lte=hi
        ).values_list("essl_user_id", "punched_at")
    )
    fresh = [r for r in rows if (r.essl_user_id, r.punched_at) not in existing]

    RawPunch.objects.bulk_create(
        [
            RawPunch(
                device=device,
                essl_user_id=row.essl_user_id,
                punched_at=row.punched_at,
                raw=row.raw,
                employee_id=links.get(row.essl_user_id),
            )
            for row in fresh
        ],
        batch_size=500,
        ignore_conflicts=True,  # belt: a concurrent writer still cannot duplicate
    )
    return {
        "fetched": len(rows),
        "created": len(fresh),
        "duplicate": len(rows) - len(fresh),
        "unmapped": sum(1 for r in fresh if links.get(r.essl_user_id) is None),
        "affected": {
            (links[r.essl_user_id], timezone.localtime(r.punched_at).date())
            for r in fresh
            if links.get(r.essl_user_id) is not None
        },
    }


def sync_all(
    *,
    kind: str = SyncKind.SCHEDULED,
    range_from: dt.date | None = None,
    range_to: dt.date | None = None,
    actor=None,
    transport: essl_client.Transport | None = None,
) -> EsslSyncRun:
    """
    One sync pass over every enabled device.

    Incremental by default (each device's `last_synced_at` minus the overlap);
    a date range turns it into a historical import over the same path.
    """
    from apps.employees.models import Employee

    run = EsslSyncRun.objects.create(kind=kind, range_from=range_from, range_to=range_to)
    devices = list(AttendanceDevice.objects.active().filter(is_enabled=True))
    run.devices_total = len(devices)

    now = timezone.now()
    affected: set[tuple] = set()

    for device in devices:
        if range_from and range_to:
            windows = _chunked_windows(range_from, range_to)
        else:
            start = (device.last_synced_at or now - OVERLAP) - OVERLAP
            windows = [(start, now)]

        try:
            for w_from, w_to in windows:
                rows = essl_client.fetch_punches(
                    device.serial_number, w_from, w_to, transport=transport
                )
                counts = _ingest(device, rows)
                run.punches_fetched += counts["fetched"]
                run.punches_created += counts["created"]
                run.punches_duplicate += counts["duplicate"]
                run.punches_unmapped += counts["unmapped"]
                affected |= counts["affected"]
            device.last_synced_at = now
            device.last_sync_status = SyncStatus.OK
            device.last_sync_error = ""
        except Exception as exc:  # noqa: BLE001 — one device must cost one device
            logger.exception(
                "attendance.essl_sync_failed device=%s", device.serial_number
            )
            run.devices_failed += 1
            run.errors = [*run.errors, {
                "device": device.serial_number,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            }]
            device.last_sync_status = SyncStatus.FAILED
            device.last_sync_error = f"{type(exc).__name__}: {exc}"[:2000]
        device.save(update_fields=[
            "last_synced_at", "last_sync_status", "last_sync_error", "updated_at",
        ])

    # Recompute the touched days, resolving employees once.
    if affected:
        employees = Employee.objects.in_bulk({e for e, _ in affected})
        pairs = {(employees[e], d) for e, d in affected if e in employees}
        recompute_days(pairs)

    run.finished_at = timezone.now()
    if run.devices_failed and run.devices_failed == run.devices_total:
        run.status = RunStatus.FAILED
    elif run.devices_failed:
        run.status = RunStatus.PARTIAL
    else:
        run.status = RunStatus.COMPLETED
    run.save()

    _audit_run(run, actor=actor)
    logger.info(
        "attendance.essl_sync kind=%s devices=%s failed=%s fetched=%s created=%s "
        "duplicate=%s unmapped=%s",
        kind, run.devices_total, run.devices_failed, run.punches_fetched,
        run.punches_created, run.punches_duplicate, run.punches_unmapped,
    )
    return run


def _chunked_windows(range_from: dt.date, range_to: dt.date) -> list[tuple]:
    tz = timezone.get_current_timezone()
    windows = []
    cursor = range_from
    while cursor <= range_to:
        end = min(cursor + dt.timedelta(days=RANGE_CHUNK_DAYS - 1), range_to)
        windows.append((
            dt.datetime.combine(cursor, dt.time.min, tzinfo=tz),
            dt.datetime.combine(end, dt.time.max, tzinfo=tz),
        ))
        cursor = end + dt.timedelta(days=1)
    return windows


@transaction.atomic
def map_essl_id(*, employee, essl_user_id: str, location=None) -> EsslEmployeeLink:
    """
    Point one device ID at one employee, and claim that ID's parked punches.

    An employee may hold any number of these — one per site whose device knows
    them under a different ID. What is refused is the reverse: an ID already
    spoken for by SOMEBODY ELSE, because resolving it two ways would file one
    person's attendance against another.

    Two cases the caller must not have to think about:
      * the same employee re-submitting an ID they already hold — answered by
        updating that row's location rather than raising;
      * an ID whose mapping was REMOVED earlier — the row still exists
        (removal is a soft delete) and still occupies the unique constraint,
        so it is revived instead of colliding.
    """
    from django.core.exceptions import ValidationError

    essl_user_id = (essl_user_id or "").strip()
    if not essl_user_id:
        raise ValidationError({"essl_user_id": "Enter the eSSL user ID."})

    existing = EsslEmployeeLink.objects.filter(essl_user_id=essl_user_id).first()
    if existing is not None and existing.employee_id != employee.pk and existing.is_active:
        raise ValidationError({
            "essl_user_id": (
                f"eSSL ID {essl_user_id} is already mapped to "
                f"{existing.employee.full_name} ({existing.employee.employee_code}). "
                "One ID belongs to one employee — remove that mapping first."
            )
        })

    if existing is not None:
        existing.employee = employee
        existing.location = location
        existing.is_active = True
        existing.save(update_fields=["employee", "location", "is_active", "updated_at"])
        link = existing
    else:
        link = EsslEmployeeLink.objects.create(
            essl_user_id=essl_user_id, employee=employee, location=location
        )

    attach_mapping(link)
    return link


@transaction.atomic
def unmap_essl_id(link: EsslEmployeeLink) -> None:
    """
    Remove ONE mapping, leaving the employee's other mappings untouched.

    The punches already filed under this ID keep their employee: they are
    history, and re-deriving days from a mapping that no longer exists would
    silently rewrite attendance the person was actually marked on.
    """
    link.delete()


@transaction.atomic
def attach_mapping(link: EsslEmployeeLink) -> int:
    """
    A new mapping claims its orphaned punches and recomputes their days.
    Called after HR creates or repoints a mapping — requirement: an unmapped
    punch is parked, never lost.
    """
    orphans = RawPunch.objects.filter(
        essl_user_id=link.essl_user_id, employee__isnull=True
    )
    dates = {
        timezone.localtime(p).date()
        for p in orphans.values_list("punched_at", flat=True)
    }
    updated = orphans.update(employee=link.employee)
    if dates:
        recompute_days({(link.employee, d) for d in dates})
    return updated


def unmapped_summary() -> list[dict]:
    """The eSSL user IDs punching without a mapping — HR's to-do list."""
    from django.db.models import Count, Max, Min

    return list(
        RawPunch.objects.filter(employee__isnull=True)
        .values("essl_user_id")
        .annotate(
            punches=Count("id"),
            first_seen=Min("punched_at"),
            last_seen=Max("punched_at"),
        )
        .order_by("-last_seen")
    )


def _audit_run(run: EsslSyncRun, *, actor) -> None:
    """bulk_create writes no signal audit rows; the run's event is explicit."""
    from apps.audit.events import record_event

    record_event(
        run,
        actor=actor,
        entity_type="attendance.EsslSyncRun",
        verb="import",
        resource=Resource.ATTENDANCE,
        after={
            "event": "essl_sync",
            "kind": run.kind,
            "status": run.status,
            "devices": run.devices_total,
            "devices_failed": run.devices_failed,
            "fetched": run.punches_fetched,
            "created": run.punches_created,
            "duplicate": run.punches_duplicate,
            "unmapped": run.punches_unmapped,
        },
    )
