"""
The whole front-door journey, chained: a person applies on the public form
(with a résumé) and ends with an accepted offer — through the Recruiter's
verification, slot selection and HR confirmation at every round, department
recommendation, HR Head's selection and the existing offer workflow. And the
rejection path: the Recruiter screens out with a mandatory reason.

Every step is the existing engine; what this pins is the CHAIN — that each
piece hands off to the next and the candidate hears about each transition
exactly once.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    ApplicationStatus,
    CandidateNotification,
    CandidateNotificationKind as K,
    CandidateRejection,
    InterviewSlotInvite,
    SlotInviteStatus,
)
from apps.recruitment.services import public_intake, slots
from apps.recruitment.services.engine import record_decision
from apps.recruitment.services.hiring import create_offer, record_offer_response, send_offer
from apps.recruitment.services.interviews import schedule_interview
from apps.workflows.models import Decision

pytestmark = pytest.mark.django_db


def _apply(job, submission="e2e-1", **over):
    answers = {
        "full_name": "Shubham Pramod Badgujar",
        "email": "shubham@example.test",
        "phone": "9511974562",
        "city": "Jalgaon",
        "qualification": "B.Tech",
        "resume_link": "https://drive.google.com/file/d/abc/view",
        "current_salary": "300000",
        "expected_ctc": "450000",
    }
    answers.update(over)
    return public_intake.public_apply(
        job=job, submission_id=submission, answers=answers, consented=True,
        resume_file=SimpleUploadedFile("resume.pdf", b"%PDF-1.4 body", content_type="application/pdf"),
    )


def _select_and_schedule(app, staff, interviewer_code):
    """The slot dance for the current round: HR offers times, the candidate
    picks, HR confirms."""
    slots.configure_slot_invite(
        application=app, actor=staff["hr_head"].user,
        options=slots.default_slot_options(),
    )
    invite = InterviewSlotInvite.objects.get(
        application=app, stage=app.current_stage,
        status__in=[SlotInviteStatus.PENDING, SlotInviteStatus.SELECTED],
    )
    chosen = invite.options[0]
    slots.select_slot(invite=invite, start=chosen["start"], end=chosen["end"])
    return schedule_interview(
        application=app, stage=app.current_stage, interviewer=staff[interviewer_code],
        actor=staff["hr_head"].user,
        scheduled_at=dt.datetime.fromisoformat(chosen["start"]),
        mode="video", location_or_link="https://meet.google.com/e2e-test",
    )


def test_selection_path_from_public_form_to_accepted_offer(
    therapist_job, staff, org, django_capture_on_commit_callbacks
):
    from apps.recruitment.services.interviews import submit_feedback
    from core.access.catalog import Layer

    with django_capture_on_commit_callbacks(execute=True):
        result = _apply(therapist_job)
        app = result.application
        assert app.candidate.resume  # the upload landed

        # Recruiter reviews and verifies → first round. The candidate hears
        # nothing until HR configures the round's times.
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
        app.refresh_from_db()

        for interviewer in ("clinic_doctor", "senior_doctor"):
            interview = _select_and_schedule(app, staff, interviewer)
            submit_feedback(interview=interview, actor=staff[interviewer].user,
                            answers={f.key: (3 if f.kind == "rating_1_5" else True if f.kind == "boolean" else "ok")
                                     for f in app.current_stage.feedback_form.fields.filter(is_required=True)},
                            recommendation="hire")
            record_decision(application=app, actor=staff[interviewer].user, decision=Decision.PASS)
            app.refresh_from_db()

        record_decision(application=app, actor=staff["medical_director"].user,
                        decision=Decision.RECOMMEND_SELECT, rationale="Strong across both rounds.")
        record_decision(application=app, actor=staff["hr_head"].user, decision=Decision.SELECT)
        app.refresh_from_db()  # record_decision saves its own locked copy
        offer = create_offer(application=app, actor=staff["hr_head"].user, offered_ctc="480000",
                             joining_date=dt.date(2026, 10, 1), designation=org["designation"],
                             level=org["levels"][Layer.STAFF])
        send_offer(offer=offer, actor=staff["hr_head"].user)
        record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)

    app.refresh_from_db()
    assert app.status == ApplicationStatus.OFFER_ACCEPTED

    kinds = list(
        CandidateNotification.objects.filter(application=app).order_by("created_at")
        .values_list("kind", flat=True)
    )
    # Two rounds → two invites, two schedules, two "selected for next round".
    assert kinds.count(K.INTERVIEW_SLOT_INVITE) == 2
    assert kinds.count(K.INTERVIEW_SCHEDULED) == 2
    assert kinds.count(K.INTERVIEW_SELECTED) == 2
    for expected in (K.APPLICATION_RECEIVED, K.DEPARTMENT_DECISION,
                     K.FINAL_SELECTION, K.OFFER_SENT, K.OFFER_ACCEPTED):
        assert kinds.count(expected) == 1, (expected, kinds)
    # Verification is silent by design.
    assert K.HR_VERIFICATION_PASSED not in kinds
    assert K.FINAL_REJECTION not in kinds
    assert not CandidateNotification.objects.filter(application=app).exclude(status="sent").exists()
    # Both invites settled by real scheduling.
    assert not InterviewSlotInvite.objects.filter(application=app).exclude(
        status=SlotInviteStatus.CONFIRMED
    ).exists()


def test_rejection_path_recruiter_screens_out_with_a_mandatory_reason(
    therapist_job, staff, django_capture_on_commit_callbacks
):
    from apps.recruitment.services.engine import WorkflowError

    with django_capture_on_commit_callbacks(execute=True):
        app = _apply(therapist_job, submission="e2e-rej", email="other@example.test",
                     phone="9000000009", full_name="Other Person").application

        # No reason, or a bloated one: refused on the backend.
        with pytest.raises(WorkflowError, match="at least"):
            record_decision(application=app, actor=staff["recruiter"].user,
                            decision=Decision.SCREEN_OUT, rationale="no")
        with pytest.raises(WorkflowError, match="limit is 250"):
            record_decision(application=app, actor=staff["recruiter"].user,
                            decision=Decision.SCREEN_OUT, rationale="word " * 251)

        record_decision(
            application=app, actor=staff["recruiter"].user, decision=Decision.SCREEN_OUT,
            rationale="The application does not meet the minimum qualification for this role.",
        )

    app.refresh_from_db()
    assert app.status == ApplicationStatus.REJECTED
    rejection = CandidateRejection.objects.get(application=app)
    assert "minimum qualification" in rejection.reason
    assert rejection.rejected_by == staff["recruiter"].user

    kinds = list(CandidateNotification.objects.filter(application=app).values_list("kind", flat=True))
    assert K.HR_VERIFICATION_REJECTED in kinds and K.FINAL_SELECTION not in kinds
    body = CandidateNotification.objects.get(application=app, kind=K.HR_VERIFICATION_REJECTED).body_text
    assert "minimum qualification" not in body  # internal reason stays internal
