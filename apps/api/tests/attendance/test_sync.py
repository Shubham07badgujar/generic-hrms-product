"""
The sync pipeline: idempotency, unmapped retention, failure isolation.
"""

from __future__ import annotations

import pytest

from apps.attendance.models import (
    AttendanceRecord,
    EsslEmployeeLink,
    EsslSyncRun,
    RawPunch,
    RunStatus,
    SyncKind,
    SyncStatus,
)
from apps.attendance.services import sync as sync_service
from apps.attendance.services.essl_client import EsslError
from tests.attendance.conftest import FakeTransport, soap_reply

pytestmark = pytest.mark.django_db

ROWS = [
    "101\t2026-08-25 10:05:00",
    "101\t2026-08-25 18:58:00",
    "999\t2026-08-25 10:20:00",  # nobody is mapped to 999
]


def test_a_sync_ingests_resolves_and_computes(essl_settings, device, worker, shift_rule):
    run = sync_service.sync_all(
        kind=SyncKind.MANUAL, transport=FakeTransport([soap_reply(ROWS)])
    )

    assert run.status == RunStatus.COMPLETED
    assert run.punches_fetched == 3
    assert run.punches_created == 3
    assert run.punches_unmapped == 1
    # The mapped punches computed a day record straight away.
    record = AttendanceRecord.objects.get(employee=worker)
    assert str(record.date) == "2026-08-25"
    assert record.status == "present"


def test_a_second_sync_of_the_same_window_creates_nothing(
    essl_settings, device, worker, shift_rule
):
    """The database constraint is the idempotency — re-reading is free."""
    sync_service.sync_all(kind=SyncKind.MANUAL, transport=FakeTransport([soap_reply(ROWS)]))
    again = sync_service.sync_all(
        kind=SyncKind.MANUAL, transport=FakeTransport([soap_reply(ROWS)])
    )

    assert again.punches_fetched == 3
    assert again.punches_created == 0
    assert again.punches_duplicate == 3
    assert RawPunch.objects.count() == 3


def test_unmapped_punches_are_kept_and_claimed_by_a_new_mapping(
    essl_settings, device, worker, shift_rule, org, roles
):
    """Requirement 8: never discard; show under Unmapped until HR maps."""
    import datetime as dt

    from apps.employees.models import Employee

    sync_service.sync_all(kind=SyncKind.MANUAL, transport=FakeTransport([soap_reply(ROWS)]))

    summary = sync_service.unmapped_summary()
    assert [row["essl_user_id"] for row in summary] == ["999"]
    assert summary[0]["punches"] == 1

    stranger = Employee.objects.create(
        employee_code="EMP09002", first_name="Newly", last_name="Mapped",
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    link = EsslEmployeeLink.objects.create(essl_user_id="999", employee=stranger)
    claimed = sync_service.attach_mapping(link)

    assert claimed == 1
    assert RawPunch.objects.filter(employee__isnull=True).count() == 0
    assert sync_service.unmapped_summary() == []
    # And the day was computed for the newly mapped person too.
    assert AttendanceRecord.objects.filter(employee=stranger).exists()


def test_a_failing_device_costs_that_device_only(essl_settings, org, worker, shift_rule):
    from apps.attendance.models import AttendanceDevice

    ok = AttendanceDevice.objects.create(name="Works", serial_number="SN-OK")
    broken = AttendanceDevice.objects.create(name="Broken", serial_number="SN-BAD")

    class SplitTransport:
        def post(self, url, *, headers, body):
            if "SN-BAD" in body:
                raise EsslError("connection refused")
            return soap_reply(ROWS[:1])

    run = sync_service.sync_all(kind=SyncKind.SCHEDULED, transport=SplitTransport())

    assert run.status == RunStatus.PARTIAL
    assert run.devices_failed == 1
    assert run.errors and run.errors[0]["device"] == "SN-BAD"
    ok.refresh_from_db(), broken.refresh_from_db()
    assert ok.last_sync_status == SyncStatus.OK
    assert broken.last_sync_status == SyncStatus.FAILED
    assert "connection refused" in broken.last_sync_error


def test_the_sync_is_audited_as_one_import_event(essl_settings, device, worker, shift_rule):
    from apps.audit.models import AuditLog

    run = sync_service.sync_all(
        kind=SyncKind.MANUAL, transport=FakeTransport([soap_reply(ROWS)])
    )
    row = AuditLog.objects.filter(entity_id=str(run.pk)).first()
    assert row is not None
    assert row.after["event"] == "essl_sync"
    assert row.after["created"] == 3


def test_the_beat_schedule_is_registered():
    """
    A sync nobody schedules is a sync nobody runs.

    Registered by `sync_beat_schedule` rather than by a data migration, so the
    command is what this test exercises -- see the note in
    tests/imports/test_staging_retention.py.
    """
    from django.core.management import call_command
    from django_celery_beat.models import PeriodicTask

    call_command("sync_beat_schedule", verbosity=0)

    task = PeriodicTask.objects.filter(task="attendance.sync_essl").first()
    assert task is not None
    assert task.enabled
    assert task.interval.every == 5


def test_the_task_noops_while_disabled(settings, organization):
    """
    The per-ORGANIZATION task is what carries the behaviour now.

    `attendance.sync_essl` is the dispatcher the beat row names; it queues one
    of these per running customer and does no attendance work itself.
    """
    from apps.attendance.tasks import sync_essl_for_organization

    settings.ESSL_INTEGRATION_ENABLED = False
    result = sync_essl_for_organization(organization.pk)
    assert result["skipped"] is True
    assert EsslSyncRun.objects.count() == 0
