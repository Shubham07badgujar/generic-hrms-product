"""
Importing: deduplication, idempotency, transactions, RBAC and audit.

The claims that matter most, and the ones easiest to get quietly wrong:

  * a second import of the same file changes nothing
  * one bad row costs one row, and never leaves a candidate without an
    application — such a candidate is invisible to everyone below Scope.ALL
  * commit re-resolves from the database instead of replaying the preview
  * a department-scoped importer cannot reach another department's job, and
    gets 404 rather than 403
  * nothing here fabricates consent
"""

from __future__ import annotations

import pytest
from django.http import Http404

from apps.imports.models import BatchStatus, ImportRow, MatchRule, RowStatus
from apps.imports.platforms import NAUKRI, WORKINDIA
from apps.imports.services import importer
from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    Candidate,
    CandidateExternalRef,
    LegalBasis,
)
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db


# --------------------------------------------------------- the happy path


def test_a_workindia_import_creates_candidates_and_applications(
    attested_batch, workindia_xlsx, importer_user, import_job
):
    batch = attested_batch(workindia_xlsx())

    result = importer.commit(actor=importer_user, batch=batch)

    assert result.created == 2
    assert Application.objects.filter(job_opening=import_job).count() == 2


def test_imported_applications_start_at_the_workflow_first_stage(
    attested_batch, workindia_xlsx, importer_user, import_job
):
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    first_stage = import_job.workflow.first_stage
    for application in Application.objects.filter(job_opening=import_job):
        assert application.current_stage_id == first_stage.pk


def test_an_applied_event_is_written(attested_batch, workindia_xlsx, importer_user):
    """`Kind.APPLIED` existed for a long time and was never once recorded."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    assert ApplicationEvent.objects.filter(
        kind=ApplicationEvent.Kind.APPLIED
    ).count() == 2


def test_a_phone_only_candidate_is_imported_without_an_invented_email(
    attested_batch, workindia_xlsx, importer_user
):
    """The row this whole feature exists for."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    candidate = Candidate.objects.get(first_name="Ramesh")
    assert candidate.email is None
    assert candidate.phone_e164 == "+919876543211"


def test_a_naukri_import_works_through_the_same_service(
    attested_batch, naukri_xlsx, importer_user
):
    batch = attested_batch(naukri_xlsx(), platform=NAUKRI)

    result = importer.commit(actor=importer_user, batch=batch)

    assert result.created == 1
    assert Candidate.objects.filter(source="naukri").count() == 1


# ------------------------------------------------------------- consent


def test_an_import_never_records_consent(
    attested_batch, workindia_xlsx, importer_user
):
    """They agreed with the platform, not with us."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    for candidate in Candidate.objects.filter(source="workindia"):
        assert candidate.consent_given is False
        assert candidate.consent_at is None
        assert candidate.legal_basis == LegalBasis.VOLUNTARILY_PROVIDED
        assert candidate.notice_due_at is not None


def test_a_consent_record_links_back_to_the_batch(
    attested_batch, workindia_xlsx, importer_user
):
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    candidate = Candidate.objects.filter(source="workindia").first()
    record = candidate.consent_records.get()
    assert record.origin_batch_id == batch.pk
    assert record.evidence["file_sha256"] == batch.file_sha256


def test_an_unattested_batch_cannot_be_committed(
    importer_user, import_job, workindia_xlsx
):
    from core.api.exceptions import BusinessRuleError

    batch = importer.create_batch(
        actor=importer_user, platform=WORKINDIA,
        job_opening=import_job, file=workindia_xlsx(),
    )

    with pytest.raises(BusinessRuleError):
        importer.commit(actor=importer_user, batch=batch)


# ---------------------------------------------------------- deduplication


def test_an_external_id_matches_before_anything_else(
    attested_batch, workindia_xlsx, importer_user, import_job
):
    """Rule 1: the platform's own assertion of identity."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    original = Candidate.objects.get(first_name="Priya")

    # Same platform id, different email entirely.
    again = attested_batch(
        workindia_xlsx(rows=[[
            "WI-1001", "Priya Deshmukh", "9876543210", "moved@example.test",
            "", "", "", "", "", "", "",
        ]])
    )
    importer.commit(actor=importer_user, batch=again)

    row = ImportRow.objects.filter(batch=again).get()
    assert row.match_rule == MatchRule.EXTERNAL_ID
    assert row.matched_candidate_id == original.pk


def test_a_normalized_email_matches_case_insensitively(
    attested_batch, workindia_xlsx, importer_user
):
    attested = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=attested)

    again = attested_batch(
        workindia_xlsx(rows=[[
            "", "Priya Deshmukh", "", "PRIYA@EXAMPLE.TEST",
            "", "", "", "", "", "", "",
        ]])
    )
    importer.commit(actor=importer_user, batch=again)

    assert ImportRow.objects.filter(batch=again).get().match_rule == MatchRule.EMAIL


def test_a_single_phone_match_needs_the_names_to_agree(
    attested_batch, workindia_xlsx, importer_user
):
    """
    Rule 3. TRAI recycles numbers after about 90 days, so a phone match with a
    completely different name is more likely a new person than the old one.
    """
    importer.commit(actor=importer_user, batch=attested_batch(workindia_xlsx()))

    stranger = attested_batch(
        workindia_xlsx(rows=[[
            "", "Vikram Malhotra", "9876543210", "",
            "", "", "", "", "", "", "",
        ]])
    )
    importer.commit(actor=importer_user, batch=stranger)

    row = ImportRow.objects.filter(batch=stranger).get()
    assert row.status == RowStatus.NEEDS_REVIEW
    assert any(w["code"] == "phone_match_name_mismatch" for w in row.warnings)


def test_several_phone_matches_are_never_auto_merged(
    attested_batch, workindia_xlsx, importer_user
):
    """Rule 4. A shared handset is normal; guessing which person is not."""
    Candidate.objects.create(
        first_name="Priya", last_name="A", email="a@example.test",
        phone="9876543210", consent_given=True, legal_basis=LegalBasis.CONSENT,
    )
    Candidate.objects.create(
        first_name="Priya", last_name="B", email="b@example.test",
        phone="9876543210", consent_given=True, legal_basis=LegalBasis.CONSENT,
    )

    batch = attested_batch(
        workindia_xlsx(rows=[[
            "", "Priya Sharma", "9876543210", "", "", "", "", "", "", "", "",
        ]])
    )
    importer.commit(actor=importer_user, batch=batch)

    row = ImportRow.objects.filter(batch=batch).get()
    assert row.status == RowStatus.NEEDS_REVIEW
    assert any(w["code"] == "ambiguous_phone_match" for w in row.warnings)


def test_a_row_with_no_identity_key_is_invalid(
    attested_batch, workindia_xlsx, importer_user
):
    """Without one, every re-import manufactures a fresh duplicate forever."""
    batch = attested_batch(
        workindia_xlsx(rows=[["", "Nameless Person", "", "", "", "", "", "", "", "", ""]])
    )
    importer.commit(actor=importer_user, batch=batch)

    row = ImportRow.objects.filter(batch=batch).get()
    assert row.status == RowStatus.INVALID
    assert row.errors[0]["code"] == "no_identity_key"


def test_a_duplicate_within_one_file_is_flagged_not_imported(
    attested_batch, workindia_xlsx, importer_user
):
    batch = attested_batch(
        workindia_xlsx(rows=[
            ["WI-9", "Priya Deshmukh", "9876543210", "p@example.test", "", "", "", "", "", "", ""],
            ["WI-9", "Priya Deshmukh", "9876543210", "p@example.test", "", "", "", "", "", "", ""],
        ])
    )
    importer.commit(actor=importer_user, batch=batch)

    statuses = set(ImportRow.objects.filter(batch=batch).values_list("status", flat=True))
    assert RowStatus.DUPLICATE_IN_FILE in statuses
    assert Candidate.objects.filter(source="workindia").count() == 1


def test_enrichment_never_overwrites_curated_data(
    attested_batch, workindia_xlsx, importer_user
):
    """
    Enrich-only. The database is HR's record; a platform export is bulk data.
    Overwriting would also make the operation order-dependent, which is exactly
    what the idempotency claim rests on not being true.
    """
    existing = Candidate.objects.create(
        first_name="Priya", last_name="Corrected", email="priya@example.test",
        current_employer="What HR Verified",
        consent_given=True, legal_basis=LegalBasis.CONSENT,
    )

    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    existing.refresh_from_db()

    assert existing.last_name == "Corrected"
    assert existing.current_employer == "What HR Verified"
    # Consent is never touched by an import.
    assert existing.consent_given is True
    assert existing.legal_basis == LegalBasis.CONSENT


def test_enrichment_fills_genuinely_empty_fields(
    attested_batch, workindia_xlsx, importer_user
):
    existing = Candidate.objects.create(
        first_name="Priya", email="priya@example.test",
        consent_given=True, legal_basis=LegalBasis.CONSENT,
    )

    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    existing.refresh_from_db()

    assert existing.current_employer == "City Physio"


def test_an_external_ref_is_recorded_for_a_matched_candidate(
    attested_batch, workindia_xlsx, importer_user
):
    """The one thing an import always adds — it makes the NEXT import stronger."""
    Candidate.objects.create(
        first_name="Priya", email="priya@example.test",
        consent_given=True, legal_basis=LegalBasis.CONSENT,
    )
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    assert CandidateExternalRef.objects.filter(
        source="workindia", external_id="WI-1001"
    ).exists()


# ------------------------------------------------------------ idempotency


def test_the_same_file_twice_is_a_noop(
    attested_batch, workindia_xlsx, importer_user
):
    """The headline guarantee. Write this one first, keep it passing."""
    importer.commit(actor=importer_user, batch=attested_batch(workindia_xlsx()))

    candidates = Candidate.objects.count()
    applications = Application.objects.count()

    second = attested_batch(workindia_xlsx())
    result = importer.commit(actor=importer_user, batch=second)

    assert result.created == 0
    assert result.updated == 0
    assert Candidate.objects.count() == candidates
    assert Application.objects.count() == applications


def test_re_applying_to_the_same_job_is_reported_not_duplicated(
    attested_batch, workindia_xlsx, importer_user
):
    importer.commit(actor=importer_user, batch=attested_batch(workindia_xlsx()))
    second = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=second)

    statuses = set(ImportRow.objects.filter(batch=second).values_list("status", flat=True))
    assert RowStatus.APPLICATION_EXISTS in statuses


def test_committing_a_finished_batch_twice_is_safe(
    attested_batch, workindia_xlsx, importer_user
):
    """A retried request must not double-write."""
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)
    before = Candidate.objects.count()

    importer.commit(actor=importer_user, batch=batch)

    assert Candidate.objects.count() == before


def test_commit_re_resolves_rather_than_trusting_the_preview(
    attested_batch, workindia_xlsx, importer_user
):
    """
    The staleness trap: between preview and commit, someone else creates the
    candidate this row was going to create. Replaying the preview's "CREATE"
    verdict would produce a duplicate — or an IntegrityError.
    """
    batch = attested_batch(workindia_xlsx())
    assert ImportRow.objects.filter(batch=batch, status=RowStatus.VALID).exists()

    # Somebody types Priya in by hand, after the preview.
    Candidate.objects.create(
        first_name="Priya", last_name="Deshmukh", email="priya@example.test",
        consent_given=True, legal_basis=LegalBasis.CONSENT,
    )

    result = importer.commit(actor=importer_user, batch=batch)

    assert result.created == 1, "Only Ramesh should be new."
    assert Candidate.objects.filter(email_normalized="priya@example.test").count() == 1


# ------------------------------------------------- transactions & orphans


def test_no_imported_candidate_is_ever_left_without_an_application(
    attested_batch, workindia_xlsx, importer_user, import_job
):
    """
    The invariant behind the per-row savepoint scope.

    A candidate with no application is not merely untidy — both candidate scope
    paths traverse `applications__`, so they are invisible to everyone below
    Scope.ALL while still occupying the email unique index.
    """
    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    orphans = Candidate.objects.filter(
        source="workindia", applications__isnull=True
    ).count()
    assert orphans == 0


def test_one_bad_row_does_not_stop_the_good_ones(
    attested_batch, workindia_xlsx, importer_user
):
    """
    Real exports always contain some malformed rows. All-or-nothing would mean
    HR perfecting a spreadsheet before anything imported at all.
    """
    batch = attested_batch(
        workindia_xlsx(rows=[
            ["WI-1", "Good Person", "9876543210", "good@example.test", "", "", "", "", "", "", ""],
            # Carries data but no name — malformed, not blank. A wholly empty
            # row is a spacer and is skipped by the parser, so it would prove
            # nothing about per-row failure handling.
            ["WI-2", "", "9876543299", "", "", "", "", "", "", "", ""],
            ["WI-3", "Also Good", "9876543212", "also@example.test", "", "", "", "", "", "", ""],
        ])
    )

    result = importer.commit(actor=importer_user, batch=batch)
    batch.refresh_from_db()

    assert result.created == 2
    assert batch.rows_failed == 1
    assert batch.status == BatchStatus.PARTIAL


# -------------------------------------------------------------- RBAC


def test_a_role_without_import_is_refused(make_user, import_job, workindia_xlsx):
    outsider = make_user("employee")

    with pytest.raises(AccessDenied):
        importer.create_batch(
            actor=outsider, platform=WORKINDIA,
            job_opening=import_job, file=workindia_xlsx(),
        )


@pytest.mark.parametrize("role", ["recruiter", "hr_manager", "hr_head", "admin"])
def test_every_granted_role_can_import(role, user_for, import_job, workindia_xlsx):
    actor = user_for(role)

    batch = importer.create_batch(
        actor=actor, platform=WORKINDIA,
        job_opening=import_job, file=workindia_xlsx(),
    )

    assert batch.rows_total == 2


def test_a_department_scoped_importer_cannot_reach_another_departments_job(
    user_for, ops_job, workindia_xlsx, everyone_grant
):
    """
    404, not 403 — a 403 would confirm the job exists, which is an enumeration
    oracle over another department's hiring.
    """
    director = user_for("medical_director")
    everyone_grant(director, "candidate", "import", 3)  # DEPARTMENT

    with pytest.raises(Http404):
        importer.create_batch(
            actor=director, platform=WORKINDIA,
            job_opening=ops_job, file=workindia_xlsx(),
        )


def test_commit_re_authorises_rather_than_trusting_the_upload(
    attested_batch, workindia_xlsx, importer_user, monkeypatch
):
    """
    The preview ran under permissions that may since have been revoked. If
    commit trusted the batch, a revoked user could still write.
    """
    batch = attested_batch(workindia_xlsx())

    from core.access import context

    monkeypatch.setattr(
        context, "resolve_context", lambda user: context.DENY_ALL
    )
    context.invalidate(importer_user.pk)

    with pytest.raises(AccessDenied):
        importer.commit(actor=importer_user, batch=batch)


# --------------------------------------------------------------- audit


def test_a_batch_level_audit_row_is_written_with_no_candidate_pii(
    attested_batch, workindia_xlsx, importer_user
):
    from apps.audit.models import AuditAction, AuditLog

    batch = attested_batch(workindia_xlsx())
    importer.commit(actor=importer_user, batch=batch)

    entries = AuditLog.objects.filter(
        action=AuditAction.IMPORT, entity_type="imports.ImportBatch"
    )
    assert entries.exists()

    blob = str([e.after for e in entries])
    for pii in ("Priya", "Deshmukh", "priya@example.test", "9876543210"):
        assert pii not in blob, f"{pii} leaked into the import audit record"


def test_per_row_candidate_creates_are_still_audited(
    attested_batch, workindia_xlsx, importer_user
):
    """
    No bulk_create in the commit path — signals do not fire for it, and every
    imported candidate must produce the same CREATE row a typed one does.
    """
    from apps.audit.models import AuditAction, AuditLog

    before = AuditLog.objects.filter(
        action=AuditAction.CREATE, entity_type="recruitment.Candidate"
    ).count()

    importer.commit(actor=importer_user, batch=attested_batch(workindia_xlsx()))

    after = AuditLog.objects.filter(
        action=AuditAction.CREATE, entity_type="recruitment.Candidate"
    ).count()
    assert after == before + 2


# ------------------------------------------------------- workflow guards


def test_a_stage_less_workflow_is_refused_cleanly(
    importer_user, import_job, workindia_xlsx
):
    """
    `current_stage` is NOT NULL, so the unguarded path is an IntegrityError from
    deep in the ORM — a stack trace instead of a row number.
    """
    from django.core.exceptions import ValidationError

    import_job.workflow.stages.update(is_active=False)

    with pytest.raises(ValidationError) as exc:
        importer.create_batch(
            actor=importer_user, platform=WORKINDIA,
            job_opening=import_job, file=workindia_xlsx(),
        )

    assert "stages" in str(exc.value).lower()


def test_an_unavailable_platform_is_refused_with_its_reason(
    importer_user, import_job, workindia_xlsx
):
    from apps.imports.platforms import LINKEDIN
    from core.api.exceptions import BusinessRuleError

    with pytest.raises(BusinessRuleError) as exc:
        importer.create_batch(
            actor=importer_user, platform=LINKEDIN,
            job_opening=import_job, file=workindia_xlsx(),
        )

    assert "partnership" in str(exc.value).lower() or "manual" in str(exc.value).lower()


# ------------------------------------------------------ file not retained


def test_the_uploaded_file_is_never_persisted(
    attested_batch, workindia_xlsx, importer_user
):
    """
    Only the parsed rows and a hash survive. Storing the archive would put
    attacker-controlled bytes on the media volume and into every backup, for no
    evidentiary gain the rows do not already provide.
    """
    from apps.imports.models import ImportBatch

    batch = attested_batch(workindia_xlsx())

    file_fields = [
        f for f in ImportBatch._meta.get_fields()
        if f.get_internal_type() in ("FileField", "ImageField")
    ]
    assert file_fields == []
    assert batch.file_sha256
