"""
An imported candidate walks the ordinary hiring pipeline.

The claim under test is a negative one: importing creates no second workflow, no
parallel state machine and no shortcut. A candidate who arrived in a WorkIndia
spreadsheet is moved by the same engine, through the same stages, under the same
permissions, as one somebody typed in — and the only difference visible anywhere
downstream is `source` and the lawful basis they are held on.

Reuses the recruitment fixtures rather than rebuilding them, for the reason
tests/e2e/conftest.py already gives: a parallel set would let the two drift, and
this test would then certify a pipeline nobody else runs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.imports.platforms import WORKINDIA
from apps.imports.services import importer
from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Candidate,
    LegalBasis,
)
from apps.recruitment.services import hiring, intake
from apps.workflows.models import Decision

pytestmark = pytest.mark.django_db

JOINING = dt.date(2026, 11, 1)


@pytest.fixture
def imported_application(staff, therapist_job, upload, workindia_xlsx):
    """One imported candidate, sitting at the therapist workflow's first stage."""
    actor = staff["recruiter"].user

    batch = importer.create_batch(
        actor=actor,
        platform=WORKINDIA,
        job_opening=therapist_job,
        file=workindia_xlsx(rows=[[
            "WI-7001", "Priya Deshmukh", "9876543210", "priya@example.test",
            "City Physio", "4.5", "6.5 lakh", "30 days", "29", "F", "Good",
        ]]),
    )
    importer.attest(
        actor=actor,
        batch=batch,
        legal_basis=LegalBasis.VOLUNTARILY_PROVIDED,
        legal_basis_note="Exported from our own WorkIndia employer account.",
        attestation_text="I confirm a lawful basis exists for this import.",
    )
    importer.commit(actor=actor, batch=batch)

    return Application.objects.get(candidate__external_refs__external_id="WI-7001")


def test_an_imported_candidate_enters_the_ordinary_pipeline(
    imported_application, therapist_job
):
    assert imported_application.job_opening_id == therapist_job.pk
    assert imported_application.current_stage_id == therapist_job.workflow.first_stage.pk
    assert imported_application.status == ApplicationStatus.ACTIVE
    assert imported_application.candidate.source == "workindia"


def test_the_whole_lifecycle_runs_on_the_existing_engine(
    imported_application, staff, org, drive_to_selection
):
    """
    Import → verification → interviews → department recommendation → HR decision
    → offer → acceptance → employee. Every step through the real services.
    """
    from core.access.catalog import Layer

    selected = drive_to_selection(
        imported_application,
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )
    assert selected.status == ApplicationStatus.SELECTED

    offer = hiring.create_offer(
        application=selected,
        actor=staff["hr_head"].user,
        offered_ctc="600000.00",
        joining_date=JOINING,
        designation=org["designation"],
        level=org["levels"][Layer.STAFF],
        reporting_manager=staff["medical_director"],
    )
    hiring.send_offer(offer=offer, actor=staff["hr_head"].user)
    hiring.record_offer_response(
        offer=offer, actor=staff["hr_head"].user, accepted=True
    )
    selected.refresh_from_db()
    assert selected.status == ApplicationStatus.OFFER_ACCEPTED

    result = hiring.convert_to_employee(
        application=selected,
        actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )

    assert result.employee.pk
    # The email lives on the linked login, not on the Employee row.
    assert result.user.email == "priya@example.test"
    selected.refresh_from_db()
    assert selected.status == ApplicationStatus.HIRED


def test_no_second_workflow_is_created_for_an_imported_candidate(
    imported_application, therapist_job
):
    """
    The point of routing import through `intake`. If the importer had its own
    creation path, this is where a parallel pipeline would show up.
    """
    from apps.workflows.models import HiringWorkflow

    assert HiringWorkflow.objects.count() == 2  # the two seeded, no more
    assert imported_application.job_opening.workflow_id == therapist_job.workflow_id


def test_the_journal_starts_at_applied_and_records_every_move(
    imported_application, staff, drive_to_selection
):
    """
    `Kind.APPLIED` is written by the intake service, so an imported candidate's
    history begins at the beginning rather than mid-story.
    """
    kinds_before = list(
        ApplicationEvent.objects.filter(application=imported_application)
        .values_list("kind", flat=True)
    )
    assert ApplicationEvent.Kind.APPLIED in kinds_before

    drive_to_selection(
        imported_application,
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )

    kinds = set(
        ApplicationEvent.objects.filter(application=imported_application)
        .values_list("kind", flat=True)
    )
    assert ApplicationEvent.Kind.APPLIED in kinds
    assert ApplicationEvent.Kind.STAGE_CHANGED in kinds


def test_an_imported_candidate_can_be_rejected_through_the_normal_path(
    imported_application, staff, at_stage
):
    """
    Rejection is HR Head's alone, imported or not. Nothing about arriving in a
    spreadsheet changes who may end someone's application.
    """
    from apps.recruitment.models import CandidateRejection
    from apps.recruitment.services.engine import record_decision

    at_stage(imported_application, 60)
    record_decision(
        application=imported_application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Not enough clinical experience for this particular role.",
    )
    imported_application.refresh_from_db()

    assert imported_application.status == ApplicationStatus.REJECTED
    assert CandidateRejection.objects.filter(application=imported_application).exists()


def test_rejection_writes_the_audit_trail(imported_application, staff, at_stage):
    from apps.audit.models import AuditLog
    from apps.recruitment.services.engine import record_decision

    at_stage(imported_application, 60)
    record_decision(
        application=imported_application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Not enough clinical experience for this particular role.",
    )

    # The rejection is auditable, and the import that produced the candidate is
    # still auditable alongside it — the two halves of "where did this go wrong".
    assert AuditLog.objects.filter(entity_type="imports.ImportBatch").exists()
    assert AuditLog.objects.filter(entity_type="recruitment.Candidate").exists()


def test_the_hired_employee_carries_no_fabricated_consent(
    imported_application, staff, org, drive_to_selection
):
    """
    Being hired is the strongest possible affirmation, but it is not consent to
    marketing or to being kept on file — the candidate record keeps the basis it
    was imported on until someone affirms otherwise through the service.
    """
    from core.access.catalog import Layer

    selected = drive_to_selection(
        imported_application,
        interview_roles=("clinic_doctor", "senior_doctor"),
        department_head="medical_director",
    )
    offer = hiring.create_offer(
        application=selected, actor=staff["hr_head"].user,
        offered_ctc="600000.00", joining_date=JOINING,
        designation=org["designation"], level=org["levels"][Layer.STAFF],
        reporting_manager=staff["medical_director"],
    )
    hiring.send_offer(offer=offer, actor=staff["hr_head"].user)
    hiring.record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)
    hiring.convert_to_employee(
        application=selected, actor=staff["hr_head"].user,
        reporting_manager=staff["medical_director"],
    )

    candidate = Candidate.objects.get(external_refs__external_id="WI-7001")
    assert candidate.consent_given is False
    assert candidate.legal_basis == LegalBasis.VOLUNTARILY_PROVIDED


def test_consent_can_be_affirmed_later_through_the_approved_service(
    imported_application, staff
):
    """The s.7(a) → s.6 upgrade, which only `intake.affirm_consent` may perform."""
    candidate = imported_application.candidate

    intake.affirm_consent(actor=staff["hr_head"].user, candidate=candidate)
    candidate.refresh_from_db()

    assert candidate.consent_given is True
    assert candidate.consent_at is not None
    assert candidate.legal_basis == LegalBasis.CONSENT
    assert candidate.notice_due_at is None
    assert candidate.consent_records.filter(affirmed_at__isnull=False).exists()
