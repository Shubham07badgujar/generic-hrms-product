"""
Staging PII retention.

The claim: the copy of a stranger's spreadsheet stops being readable on
schedule, while everything needed to answer "what did this import do" survives
permanently — and the candidates it actually created are never touched.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.imports.models import BatchStatus, ImportBatch, ImportRow
from apps.imports.services import importer, retention
from apps.recruitment.models import Application, Candidate

pytestmark = pytest.mark.django_db


def _age_batch(batch, *, hours=0, days=0):
    """Backdate a batch — auto_now_add cannot be set on the way in."""
    when = timezone.now() - dt.timedelta(hours=hours, days=days)
    ImportBatch.objects.filter(pk=batch.pk).update(created_at=when)
    if batch.committed_at:
        ImportBatch.objects.filter(pk=batch.pk).update(committed_at=when)
    batch.refresh_from_db()
    return batch


# ------------------------------------------------------------ which rows


def test_a_fresh_preview_is_not_touched(attested_batch, workindia_xlsx):
    batch = attested_batch(workindia_xlsx())

    result = retention.purge_staging_pii(apply=True)
    row = ImportRow.objects.filter(batch=batch).first()

    assert result.total == 0
    assert row.raw != {}
    assert row.first_name


def test_an_abandoned_preview_is_purged_after_a_day(attested_batch, workindia_xlsx):
    """
    Nobody acted on it. There is no lawful purpose in keeping a stranger's
    contact details because somebody once opened a spreadsheet.
    """
    batch = _age_batch(attested_batch(workindia_xlsx()), hours=25)

    result = retention.purge_staging_pii(apply=True)
    row = ImportRow.objects.filter(batch=batch).first()

    assert result.uncommitted_rows == 2
    assert row.raw == {}
    assert row.first_name == ""
    assert row.email is None
    assert row.phone_e164 is None


def test_a_committed_batch_keeps_its_rows_for_a_week(
    attested_batch, workindia_xlsx, importer_user
):
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=3)

    result = retention.purge_staging_pii(apply=True)

    assert result.total == 0
    assert ImportRow.objects.filter(batch=batch).first().raw != {}


def test_a_committed_batch_is_purged_after_a_week(
    attested_batch, workindia_xlsx, importer_user
):
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=8)

    result = retention.purge_staging_pii(apply=True)

    assert result.committed_rows == 2
    assert ImportRow.objects.filter(batch=batch).first().raw == {}


# ------------------------------------------------- what must NOT be lost


def test_the_candidates_the_import_created_are_untouched(
    attested_batch, workindia_xlsx, importer_user
):
    """
    The entire point. Staging is a duplicate; the Candidate is the record, and
    it has its own clock in apps/recruitment/services/retention.py.
    """
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=8)

    candidates_before = Candidate.objects.count()
    applications_before = Application.objects.count()

    retention.purge_staging_pii(apply=True)

    assert Candidate.objects.count() == candidates_before
    assert Application.objects.count() == applications_before
    priya = Candidate.objects.get(first_name="Priya")
    assert priya.email == "priya@example.test"
    assert priya.phone_e164 == "+919876543210"


def test_the_batch_record_survives_intact(
    attested_batch, workindia_xlsx, importer_user
):
    """"What did this import do" must stay answerable forever."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=8)
    sha, filename, created = batch.file_sha256, batch.original_filename, batch.rows_created

    retention.purge_staging_pii(apply=True)
    batch.refresh_from_db()

    assert batch.file_sha256 == sha
    assert batch.original_filename == filename
    assert batch.rows_created == created
    assert batch.status in (BatchStatus.COMPLETED, BatchStatus.PARTIAL)
    assert batch.legal_basis
    assert batch.attested_by_id


def test_row_outcomes_and_error_codes_survive(
    attested_batch, workindia_xlsx, importer_user
):
    """
    Codes are not personal data, and they are what makes a purged batch still
    diagnosable — "row 5 had no name" without saying whose row it was.
    """
    batch = attested_batch(
        workindia_xlsx(rows=[
            ["WI-1", "Good Person", "9876543210", "good@example.test", "", "", "", "", "", "", ""],
            ["WI-2", "", "9876543299", "", "", "", "", "", "", "", ""],
        ])
    )
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=8)

    retention.purge_staging_pii(apply=True)

    failed = ImportRow.objects.get(batch=batch, row_number=3)
    assert failed.errors[0]["code"] == "missing_name"
    assert failed.status
    assert failed.row_number == 3


def test_the_audit_trail_survives(attested_batch, workindia_xlsx, importer_user):
    from apps.audit.models import AuditAction, AuditLog

    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    _age_batch(batch, days=8)
    before = AuditLog.objects.filter(action=AuditAction.IMPORT).count()

    retention.purge_staging_pii(apply=True)

    assert AuditLog.objects.filter(action=AuditAction.IMPORT).count() == before


# ------------------------------------------------------ safety properties


def test_the_purge_is_dry_by_default(attested_batch, workindia_xlsx):
    batch = _age_batch(attested_batch(workindia_xlsx()), hours=25)

    result = retention.purge_staging_pii()

    assert result.total == 2
    assert ImportRow.objects.filter(batch=batch).first().raw != {}


def test_the_purge_is_idempotent(attested_batch, workindia_xlsx):
    """A second run finds nothing, because the first left nothing to find."""
    _age_batch(attested_batch(workindia_xlsx()), hours=25)

    first = retention.purge_staging_pii(apply=True)
    second = retention.purge_staging_pii(apply=True)

    assert first.total == 2
    assert second.total == 0


def test_the_fingerprint_goes_too(attested_batch, workindia_xlsx):
    """
    Not readable, but still a stable identifier for a person — it would let two
    purged batches be correlated back to the same individual.
    """
    batch = _age_batch(attested_batch(workindia_xlsx()), hours=25)
    assert ImportRow.objects.filter(batch=batch).first().row_fingerprint

    retention.purge_staging_pii(apply=True)

    assert ImportRow.objects.filter(batch=batch).first().row_fingerprint == ""


def test_the_scheduled_task_runs_the_purge(attested_batch, workindia_xlsx):
    from apps.imports.tasks import purge_staging_pii_task

    _age_batch(attested_batch(workindia_xlsx()), hours=25)

    result = purge_staging_pii_task(apply=True)

    assert result["uncommitted_rows"] == 2


def test_the_nightly_schedule_is_registered():
    """A policy nobody schedules is a policy nobody enforces."""
    from django_celery_beat.models import PeriodicTask

    task = PeriodicTask.objects.filter(task="imports.purge_staging_pii").first()

    assert task is not None
    assert task.enabled


def test_no_personal_field_is_left_behind(attested_batch, workindia_xlsx):
    """
    Guards the field list itself. A column added to ImportRow later and not
    added to PII_FIELDS would silently survive every purge.
    """
    batch = _age_batch(attested_batch(workindia_xlsx()), hours=25)
    retention.purge_staging_pii(apply=True)

    row = ImportRow.objects.filter(batch=batch).first()
    for field, _kind in retention.PII_FIELDS.items():
        value = getattr(row, field)
        assert value in ({}, "", None), f"{field} still holds {value!r}"
