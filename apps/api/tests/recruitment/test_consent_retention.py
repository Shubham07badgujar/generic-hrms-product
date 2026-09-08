"""
Consent ledger and retention.

Two claims, and the second is the one that has never been true before:

  1. A candidate acquired in bulk is held on a recorded legal basis, not on a
     consent nobody gave — and the upgrade to real consent is dated and
     evidenced rather than inferred from a boolean flipping.

  2. Retention actually executes. `retention_until` has been written by the
     engine and read by nothing since it was added, and the service file
     settings pointed at did not exist.

All contact details are invented; `.test` is reserved by RFC 6761.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.recruitment.models import (
    ApplicationStatus,
    Candidate,
    CandidateExternalRef,
    ConsentRecord,
    LegalBasis,
)
from apps.recruitment.services import intake, retention

pytestmark = pytest.mark.django_db


def _imported(**overrides):
    """A candidate as bulk import leaves them: lawful to hold, not consented."""
    fields = {
        "first_name": "Priya",
        "last_name": "Deshmukh",
        "email": "priya@example.test",
        "phone": "9876543210",
        "source": "workindia",
        "consent_given": False,
        "legal_basis": LegalBasis.VOLUNTARILY_PROVIDED,
        "notice_due_at": timezone.now(),
    }
    fields.update(overrides)
    return Candidate.objects.create(**fields)


def _age(candidate, *, days):
    """Backdate creation — auto_now_add cannot be set on the way in."""
    Candidate.objects.filter(pk=candidate.pk).update(
        created_at=timezone.now() - dt.timedelta(days=days)
    )
    candidate.refresh_from_db()
    return candidate


# ------------------------------------------------------------ the ledger


def test_an_imported_candidate_is_not_recorded_as_having_consented(staff):
    candidate = _imported()
    retention.record_consent(
        candidate=candidate,
        basis=LegalBasis.VOLUNTARILY_PROVIDED,
        recorded_by=staff["hr_head"].user,
        recorded_via="bulk_import",
        evidence={"attestation": "Sourced from a WorkIndia export."},
    )

    assert candidate.consent_given is False
    assert candidate.consent_at is None
    assert candidate.legal_basis == LegalBasis.VOLUNTARILY_PROVIDED
    assert candidate.consent_records.count() == 1


def test_the_ledger_holds_several_bases_for_one_person(staff):
    """Sourced twice on different grounds. A column could hold only the latest."""
    candidate = _imported()
    for basis, via in (
        (LegalBasis.VOLUNTARILY_PROVIDED, "bulk_import"),
        (LegalBasis.EMPLOYER_SUBSCRIPTION, "bulk_import"),
    ):
        retention.record_consent(
            candidate=candidate, basis=basis,
            recorded_by=staff["hr_head"].user, recorded_via=via,
        )

    assert candidate.consent_records.count() == 2


def test_affirming_consent_upgrades_the_record_and_dates_it(staff):
    """s.7(a) becomes s.6 — and the moment is evidenced, not inferred."""
    candidate = _imported()
    record = retention.record_consent(
        candidate=candidate, basis=LegalBasis.VOLUNTARILY_PROVIDED,
        recorded_by=staff["hr_head"].user, recorded_via="bulk_import",
    )

    intake.affirm_consent(actor=staff["hr_head"].user, candidate=candidate)
    candidate.refresh_from_db()
    record.refresh_from_db()

    assert candidate.consent_given is True
    assert candidate.consent_at is not None
    assert candidate.legal_basis == LegalBasis.CONSENT
    assert candidate.notice_due_at is None, "Notice is not owed to someone who replied."
    assert record.affirmed_at is not None


def test_affirming_consent_requires_permission(staff, make_user):
    """It is a write on a candidate, so it is gated like one."""
    from core.access.engine import AccessDenied

    candidate = _imported()
    outsider = make_user("office_boy")

    with pytest.raises(AccessDenied):
        intake.affirm_consent(actor=outsider, candidate=candidate)


# --------------------------------------------------------- which clock


def test_an_unaffirmed_import_is_purged_on_the_short_clock(staff, settings):
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)

    due = retention.candidates_due_for_purge()

    assert candidate.pk in set(due.values_list("pk", flat=True))


def test_a_recent_import_is_not_yet_due(staff, settings):
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=30)

    assert candidate.pk not in set(
        retention.candidates_due_for_purge().values_list("pk", flat=True)
    )


def test_affirming_consent_takes_a_candidate_off_the_short_clock(staff, settings):
    """
    The point of the two clocks: once they consent, they are an ordinary
    candidate governed by the ordinary retention period.
    """
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)
    retention.record_consent(
        candidate=candidate, basis=LegalBasis.VOLUNTARILY_PROVIDED,
        recorded_by=staff["hr_head"].user, recorded_via="bulk_import",
    )
    intake.affirm_consent(actor=staff["hr_head"].user, candidate=candidate)

    assert candidate.pk not in set(
        retention.candidates_due_for_purge().values_list("pk", flat=True)
    )


def test_a_candidate_in_a_live_pipeline_is_never_purged(staff, settings, org, roles):
    """
    An in-flight application is a live purpose for holding the data, and
    outranks any clock.
    """
    from apps.recruitment.models import Application, JobOpening
    from apps.workflows.seeds import seed_workflows
    from core.access.catalog import DepartmentKind

    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=200)

    workflow = {w.department_kind: w for w in seed_workflows()}[DepartmentKind.MEDICAL]
    job = JobOpening.objects.create(
        title="Therapist", workflow=workflow,
        department=org["departments"][DepartmentKind.MEDICAL],
        target_role=roles["therapist"], status="published",
    )
    Application.objects.create(
        candidate=candidate, job_opening=job,
        current_stage=workflow.first_stage, status=ApplicationStatus.ACTIVE,
    )

    assert candidate.pk not in set(
        retention.candidates_due_for_purge().values_list("pk", flat=True)
    )


# ------------------------------------------------------------- the purge


def test_the_purge_is_dry_by_default(staff, settings):
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)

    result = retention.purge_expired_candidates()
    candidate.refresh_from_db()

    assert result.considered == 1
    assert candidate.first_name == "Priya", "A dry run must write nothing."


def test_applying_the_purge_destroys_the_personal_data(staff, settings):
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)
    CandidateExternalRef.objects.create(
        candidate=candidate, source="workindia", external_id="WI-9"
    )

    retention.purge_expired_candidates(apply=True)
    candidate.refresh_from_db()

    assert candidate.first_name == retention.REDACTED_FIRST_NAME
    assert candidate.email is None
    assert candidate.phone == ""
    assert candidate.current_employer == ""
    # The derived keys must go too — they are a perfectly good way to recognise
    # the person we just anonymised.
    assert candidate.email_normalized is None
    assert candidate.phone_e164 is None
    # A platform's own id is itself an identifier, so it is removed outright.
    assert not CandidateExternalRef.objects.filter(candidate=candidate).exists()


def test_the_purge_keeps_the_row_rather_than_deleting_it(staff, settings):
    """
    Anonymise, never delete. Application.candidate is PROTECT, and the hiring
    record has to outlive the personal data.
    """
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)

    retention.purge_expired_candidates(apply=True)

    assert Candidate.objects.filter(pk=candidate.pk).exists()


def test_the_ledger_survives_the_purge_without_its_evidence(staff, settings):
    """
    The record that the basis existed is the evidence the purge itself was
    lawful, so it stays. The free-text evidence blob is where details about the
    person could hide, so it does not.
    """
    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)
    retention.record_consent(
        candidate=candidate, basis=LegalBasis.VOLUNTARILY_PROVIDED,
        recorded_by=staff["hr_head"].user, recorded_via="bulk_import",
        evidence={"note": "Sourced from an export naming this person."},
    )

    retention.purge_expired_candidates(apply=True)

    record = ConsentRecord.objects.get(candidate=candidate)
    assert record.basis == LegalBasis.VOLUNTARILY_PROVIDED
    assert record.evidence == {}


def test_the_scheduled_task_runs_the_purge(staff, settings):
    """
    Retention has to EXECUTE, not merely be recorded. This is the thing that
    was missing: the clock was written and nothing ever read it.
    """
    from apps.recruitment.tasks import purge_expired_candidates_task

    settings.CANDIDATE_UNAFFIRMED_RETENTION_DAYS = 90
    candidate = _age(_imported(), days=120)

    result = purge_expired_candidates_task(apply=True)
    candidate.refresh_from_db()

    assert result["anonymised"] == 1
    assert candidate.first_name == retention.REDACTED_FIRST_NAME


def test_the_nightly_schedule_is_registered():
    """A task nobody schedules is a policy nobody enforces."""
    from django_celery_beat.models import PeriodicTask

    task = PeriodicTask.objects.filter(
        task="recruitment.purge_expired_candidates"
    ).first()

    assert task is not None, "The retention purge is not scheduled."
    assert task.enabled
