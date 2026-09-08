"""
The public application door, and what candidates are told along the way.

The claims:

  1. A job's application link resolves to exactly that job; a submission
     through it becomes a Candidate and an Application at the workflow's first
     stage, and NOTHING else — no second model, no side pipeline.
  2. Replaying a submission, or applying twice for the same job, produces one
     application and one "received" email.
  3. Every real transition emails the candidate exactly what the record says,
     exactly once, from the same SMTP configuration as everything else.
  4. A mail failure is recorded and retryable; it never unwinds the transition.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.recruitment.models import (
    Application,
    Candidate,
    CandidateNotification,
    CandidateNotificationKind as K,
    CandidateNotificationStatus as S,
    InterviewStatus,
    JobStatus,
)
from apps.recruitment.services import public_intake
from apps.recruitment.services.engine import record_decision
from apps.recruitment.services.interviews import cancel_interview, reschedule_interview, schedule_interview
from apps.workflows.models import Decision

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


def _login(user):
    api = APIClient()
    token = api.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json"
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def _answers(**over):
    base = {
        "full_name": "Nisha Verma",
        "email": "nisha.verma@example.test",
        "phone": "9876543210",
        "city": "Pune",
        "qualification": "BPT",
        "resume_link": "https://drive.google.com/file/d/abc/view",
        "current_salary": "300000",
        "expected_ctc": "420000",
    }
    base.update(over)
    return base


def _apply(job, submission="sub-1", consent=True, **over):
    return public_intake.public_apply(
        job=job, submission_id=submission, answers=_answers(**over), consented=consent
    )


# ------------------------------------------------------------ the link


def test_every_job_gets_a_unique_unguessable_token(therapist_job, office_boy_job):
    assert therapist_job.application_token and office_boy_job.application_token
    assert therapist_job.application_token != office_boy_job.application_token
    assert len(therapist_job.application_token) >= 32
    assert therapist_job.application_url.endswith(f"/apply/{therapist_job.application_token}")


def test_the_token_resolves_to_exactly_one_job(therapist_job, office_boy_job):
    assert public_intake.job_for_token(therapist_job.application_token) == therapist_job
    assert public_intake.job_for_token(office_boy_job.application_token) == office_boy_job


def test_the_public_get_exposes_the_posting_and_questions_only(therapist_job):
    api = APIClient()
    r = api.get(f"/api/v1/public/apply/{therapist_job.application_token}/")
    assert r.status_code == 200
    assert r.data["title"] == "Therapist"
    keys = [f["key"] for f in r.data["fields"]]
    # Exactly the nine candidate-facing questions, in this order.
    assert keys == ["full_name", "email", "phone", "city", "qualification", "resume",
                    "resume_link", "current_salary", "expected_ctc"]
    # Nothing internal leaks through the anonymous door.
    assert "recruiter" not in r.data and "workflow" not in r.data and "id" not in r.data


def test_an_unknown_token_is_a_404_not_a_hint():
    assert APIClient().get("/api/v1/public/apply/definitely-not-a-token/").status_code == 404


def test_job_specific_extra_questions_are_asked_and_stored(therapist_job):
    therapist_job.application_fields = [
        "qualification",
        {"key": "shift_ok", "label": "Can you work evening shifts?", "type": "select",
         "options": ["Yes", "No"], "required": True},
    ]
    therapist_job.save()

    fields = public_intake.public_job_summary(therapist_job)["fields"]
    keys = [f["key"] for f in fields]
    assert keys[:3] == ["full_name", "email", "phone"]  # identity always first
    assert "qualification" in keys and "shift_ok" in keys and "skills" not in keys

    result = _apply(therapist_job, shift_ok="Yes")
    assert result.application.form_answers["shift_ok"] == "Yes"
    # A standard question lands on the candidate, not on the application.
    assert result.candidate.profile["qualification"] == "BPT"


# ------------------------------------------------------------ submission


def test_a_submission_creates_candidate_and_application_at_the_first_stage(
    therapist_job, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        result = _apply(therapist_job)

    assert result.created_candidate and result.created_application
    # Every answer lands where it belongs — Expected Salary was silently
    # dropped once (hardcoded None in the intake), so it is pinned here.
    assert float(result.candidate.expected_ctc) == 420000
    app = result.application
    assert app.job_opening == therapist_job
    assert app.current_stage == therapist_job.workflow.first_stage
    assert app.candidate.first_name == "Nisha" and app.candidate.last_name == "Verma"
    assert app.candidate.email == "nisha.verma@example.test"
    assert app.candidate.source == "hosted_form"
    assert app.candidate.consent_given is True and app.candidate.legal_basis == "consent"
    assert app.candidate.consent_records.count() == 1
    assert app.events.filter(kind="applied").exists()

    # ...and the "application received" email went out, once, after commit.
    row = CandidateNotification.objects.get(application=app)
    assert row.kind == K.APPLICATION_RECEIVED and row.status == S.SENT
    assert len(mail.outbox) == 1
    assert "Application received" in mail.outbox[0].subject
    assert result.reference in mail.outbox[0].body


def test_over_http_end_to_end(therapist_job, django_capture_on_commit_callbacks):
    api = APIClient()
    with django_capture_on_commit_callbacks(execute=True):
        r = api.post(
            f"/api/v1/public/apply/{therapist_job.application_token}/",
            {"submission_id": "web-1", "answers": _answers(), "consent": True},
            format="json",
        )
    assert r.status_code == 201, r.data
    assert r.data["reference"].startswith("APP-")
    assert set(r.data) == {"reference", "job_title"}
    assert Application.objects.filter(job_opening=therapist_job).count() == 1


def test_a_closed_job_refuses_applications(therapist_job):
    therapist_job.status = JobStatus.CLOSED
    therapist_job.save()
    with pytest.raises(public_intake.PublicIntakeError, match="no longer accepting"):
        _apply(therapist_job)
    r = APIClient().post(
        f"/api/v1/public/apply/{therapist_job.application_token}/",
        {"submission_id": "x", "answers": _answers(), "consent": True}, format="json",
    )
    assert r.status_code == 400


def test_consent_is_mandatory(therapist_job):
    with pytest.raises(public_intake.PublicIntakeError, match="declaration"):
        _apply(therapist_job, consent=False)
    assert Candidate.objects.count() == 0


def test_required_and_malformed_answers_are_refused_before_any_write(therapist_job):
    with pytest.raises(public_intake.PublicIntakeError) as exc:
        _apply(therapist_job, full_name="", email="not-an-email", phone="12")
    errors = exc.value.message_dict
    assert "full_name" in errors and "email" in errors and "phone" in errors
    assert Candidate.objects.count() == 0 and Application.objects.count() == 0


def test_a_select_answer_must_be_one_of_the_options(therapist_job):
    therapist_job.application_fields = [
        {"key": "shift", "label": "Shift", "type": "select", "options": ["Day", "Night"]},
    ]
    therapist_job.save()
    with pytest.raises(public_intake.PublicIntakeError) as exc:
        _apply(therapist_job, shift="Evening")
    assert "shift" in exc.value.message_dict


# ------------------------------------------------------------ idempotency


def test_the_same_submission_replayed_is_one_application_and_one_email(
    therapist_job, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        first = _apply(therapist_job, submission="same")
        second = _apply(therapist_job, submission="same")

    assert second.application == first.application
    assert not second.created_candidate and not second.created_application
    assert Application.objects.count() == 1 and Candidate.objects.count() == 1
    assert CandidateNotification.objects.count() == 1
    assert len(mail.outbox) == 1


def test_a_known_person_applying_again_reuses_the_candidate(therapist_job, office_boy_job):
    first = _apply(therapist_job, submission="a")
    # Different submission, different job, same email — same person.
    second = _apply(office_boy_job, submission="b", phone="9123456789")
    assert second.candidate == first.candidate
    assert not second.created_candidate and second.created_application
    assert Candidate.objects.count() == 1
    assert Application.objects.count() == 2


def test_applying_twice_for_the_same_job_is_one_application(therapist_job):
    first = _apply(therapist_job, submission="a")
    second = _apply(therapist_job, submission="b")  # a fresh submission id
    assert second.application == first.application
    assert "already_applied" in second.warnings
    assert Application.objects.filter(job_opening=therapist_job).count() == 1


def test_email_is_required_on_the_candidate_form(therapist_job):
    """The form asks for an email and will not do without one: the
    confirmation, the slot link and every later update go there."""
    with pytest.raises(public_intake.PublicIntakeError) as exc:
        _apply(therapist_job, email="")
    assert "email" in exc.value.message_dict


def test_a_known_phone_with_a_new_email_is_still_the_same_person(therapist_job, office_boy_job):
    first = _apply(therapist_job, submission="a")
    second = _apply(office_boy_job, submission="b", email="nisha.other@example.test")
    # Same phone, names overlap -> the identity rules match them.
    assert second.candidate == first.candidate


def test_candidates_never_cross_between_jobs(therapist_job, office_boy_job):
    _apply(therapist_job, submission="a")
    _apply(office_boy_job, submission="b", email="other@example.test", phone="9000000001")
    assert Application.objects.filter(job_opening=therapist_job).count() == 1
    assert Application.objects.filter(job_opening=office_boy_job).count() == 1
    assert (
        Application.objects.get(job_opening=therapist_job).candidate
        != Application.objects.get(job_opening=office_boy_job).candidate
    )


# ------------------------------------------------------------ the emails


def _kinds(application):
    return list(
        CandidateNotification.objects.filter(application=application)
        .order_by("created_at")
        .values_list("kind", flat=True)
    )


def test_recruiter_verification_sends_no_email(
    therapist_job, make_application, staff, django_capture_on_commit_callbacks
):
    # The recruiter's Verify is an internal act: the candidate already has the
    # application-received email and next hears from us when interview slots
    # are configured. Nothing goes out here.
    app = make_application(therapist_job)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    assert app.is_verified is True
    assert _kinds(app) == [] and len(mail.outbox) == 0


def test_a_rejection_never_sends_a_selection_email(
    therapist_job, make_application, at_stage, staff, django_capture_on_commit_callbacks
):
    # The engine only lets HR Head reject at the final-decision stage; the
    # candidate is told a rejection, and never a selection.
    app = at_stage(make_application(therapist_job), 60)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(
            application=app,
            actor=staff["hr_head"].user,
            decision=Decision.REJECT,
            rationale="Does not hold the required registration for this role.",
        )
    kinds = _kinds(app)
    assert kinds == [K.FINAL_REJECTION]
    assert K.FINAL_SELECTION not in kinds
    body = CandidateNotification.objects.get(application=app, kind=K.FINAL_REJECTION).body_text
    # The internal rationale is NOT in the candidate's email.
    assert "registration" not in body and "regret" in body


def test_a_rejection_of_a_never_verified_candidate_reads_as_hr_screening(
    therapist_job, make_application, at_stage, staff, django_capture_on_commit_callbacks
):
    app = at_stage(make_application(therapist_job), 60, verified=False)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["hr_head"].user, decision=Decision.REJECT,
                        rationale="Application incomplete and unverifiable.")
    assert _kinds(app) == [K.HR_VERIFICATION_REJECTED]


def test_interview_scheduled_rescheduled_and_cancelled_each_email_once(
    therapist_job, make_application, at_stage, staff, django_capture_on_commit_callbacks
):
    app = at_stage(make_application(therapist_job), 30)
    stage = therapist_job.workflow.stages.get(order=30)
    when = timezone.now() + dt.timedelta(days=2)
    with django_capture_on_commit_callbacks(execute=True):
        interview = schedule_interview(
            application=app, stage=stage, interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user, scheduled_at=when, mode="video",
            location_or_link="https://meet.example/abc",
        )
        reschedule_interview(interview=interview, actor=staff["hr_head"].user,
                             scheduled_at=when + dt.timedelta(hours=2))
        cancel_interview(interview=interview, actor=staff["hr_head"].user, reason="Interviewer unwell")

    kinds = _kinds(app)
    assert kinds == [K.INTERVIEW_SCHEDULED, K.INTERVIEW_RESCHEDULED, K.INTERVIEW_CANCELLED]
    interview.refresh_from_db()
    assert interview.status == InterviewStatus.CANCELLED
    scheduled = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SCHEDULED)
    assert "https://meet.example/abc" in scheduled.body_text
    assert stage.name in scheduled.subject
    assert staff["clinic_doctor"].full_name in scheduled.body_text


def test_a_completed_interview_cannot_be_cancelled(
    therapist_job, make_application, at_stage, staff
):
    from django.core.exceptions import ValidationError

    app = at_stage(make_application(therapist_job), 30)
    stage = therapist_job.workflow.stages.get(order=30)
    interview = schedule_interview(
        application=app, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=timezone.now() + dt.timedelta(days=1),
    )
    interview.status = InterviewStatus.COMPLETED
    interview.save()
    with pytest.raises(ValidationError, match="cannot be cancelled"):
        cancel_interview(interview=interview, actor=staff["hr_head"].user)


def test_the_full_journey_emails_every_transition_and_never_twice(
    therapist_job, make_application, drive_to_selection, staff, org,
    django_capture_on_commit_callbacks,
):
    from apps.recruitment.services.hiring import create_offer, record_offer_response, send_offer
    from core.access.catalog import Layer

    app = make_application(therapist_job)
    with django_capture_on_commit_callbacks(execute=True):
        drive_to_selection(app, interview_roles=("clinic_doctor", "senior_doctor"),
                           department_head="medical_director")
        offer = create_offer(
            application=app, actor=staff["hr_head"].user, offered_ctc="480000",
            joining_date=dt.date(2026, 10, 1), designation=org["designation"],
            level=org["levels"][Layer.STAFF],
        )
        send_offer(offer=offer, actor=staff["hr_head"].user)
        record_offer_response(offer=offer, actor=staff["hr_head"].user, accepted=True)

    kinds = _kinds(app)
    for expected in (
        K.INTERVIEW_SCHEDULED, K.INTERVIEW_SELECTED,
        K.DEPARTMENT_DECISION, K.FINAL_SELECTION, K.OFFER_SENT, K.OFFER_ACCEPTED,
    ):
        assert expected in kinds, f"{expected} missing from {kinds}"
    # Verification is silent by design.
    assert K.HR_VERIFICATION_PASSED not in kinds
    assert K.FINAL_REJECTION not in kinds and K.OFFER_DECLINED not in kinds
    # Two interview rounds → two "scheduled" and two "selected", not more.
    assert kinds.count(K.INTERVIEW_SCHEDULED) == 2
    assert kinds.count(K.INTERVIEW_SELECTED) == 2
    assert kinds.count(K.FINAL_SELECTION) == 1
    # Every send is a real send through the configured backend.
    assert CandidateNotification.objects.filter(application=app).exclude(status=S.SENT).count() == 0
    offer_mail = CandidateNotification.objects.get(application=app, kind=K.OFFER_SENT)
    assert "₹4,80,000" in offer_mail.body_text and "01 Oct 2026" in offer_mail.body_text


def test_a_candidate_without_an_email_is_recorded_as_skipped_not_failed(
    therapist_job, make_application, at_stage, staff, django_capture_on_commit_callbacks
):
    app = at_stage(make_application(therapist_job), 30)
    app.candidate.email = None
    app.candidate.save()
    stage = therapist_job.workflow.stages.get(order=30)
    with django_capture_on_commit_callbacks(execute=True):
        schedule_interview(
            application=app, stage=stage, interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user, scheduled_at=timezone.now() + dt.timedelta(days=2),
        )
    row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SCHEDULED)
    assert row.status == S.SKIPPED and len(mail.outbox) == 0


# ------------------------------------------------------------ failure + retry


def test_smtp_failure_is_recorded_audited_and_does_not_undo_the_transition(
    therapist_job, make_application, staff, django_capture_on_commit_callbacks
):
    from apps.audit.models import AuditLog

    from apps.recruitment.models import InterviewSlotInvite
    from apps.recruitment.services import slots

    app = make_application(therapist_job)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP down")):
        with django_capture_on_commit_callbacks(execute=True):
            slots.configure_slot_invite(
                application=app, actor=staff["hr_head"].user,
                options=slots.default_slot_options(),
            )

    assert InterviewSlotInvite.objects.filter(application=app).exists()  # the invite stood
    row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SLOT_INVITE)
    assert row.status == S.FAILED and "SMTP down" in row.error and row.attempts == 1
    assert AuditLog.objects.filter(
        entity_id=str(row.pk), after__event="candidate_email_failed"
    ).exists()


def test_hr_can_retry_a_failed_email_over_http_and_it_updates_the_same_row(
    therapist_job, make_application, staff, django_capture_on_commit_callbacks
):
    from apps.recruitment.services import slots

    app = make_application(therapist_job)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP down")):
        with django_capture_on_commit_callbacks(execute=True):
            slots.configure_slot_invite(
                application=app, actor=staff["hr_head"].user,
                options=slots.default_slot_options(),
            )
    row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SLOT_INVITE)

    api = _login(staff["hr_head"].user)
    listing = api.get(f"/api/v1/applications/{app.pk}/notifications/")
    assert listing.status_code == 200
    assert [r["kind"] for r in listing.data] == ["interview_slot_invite"]
    assert listing.data[0]["status"] == "failed"

    r = api.post(
        f"/api/v1/applications/{app.pk}/retry-notification/",
        {"notification": str(row.pk)}, format="json",
    )
    assert r.status_code == 200, r.data
    assert r.data["status"] == "sent" and r.data["attempts"] == 2
    # The retry updated the SAME row; no new row appeared.
    assert CandidateNotification.objects.filter(application=app).count() == 1
    assert len(mail.outbox) == 1  # only the retried send actually left


def test_a_sent_email_is_not_retried(therapist_job, make_application, staff, django_capture_on_commit_callbacks):
    from apps.recruitment.services import slots

    app = make_application(therapist_job)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user,
            options=slots.default_slot_options(),
        )
    row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SLOT_INVITE)
    api = _login(staff["hr_head"].user)
    r = api.post(f"/api/v1/applications/{app.pk}/retry-notification/",
                 {"notification": str(row.pk)}, format="json")
    assert r.status_code == 400
    # The slot invite went out once; the refusal added nothing.
    assert len(mail.outbox) == 1


def test_a_read_only_role_cannot_retry(therapist_job, make_application, staff, roles, django_capture_on_commit_callbacks):
    from apps.accounts.models import User, UserRole

    ceo = User.objects.create_user(email="ceo@example.test", password=PASSWORD)
    UserRole.objects.create(user=ceo, role=roles["ceo"])
    from apps.recruitment.services import slots

    app = make_application(therapist_job)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user,
            options=slots.default_slot_options(),
        )
    row = CandidateNotification.objects.filter(application=app).first()
    assert row is not None
    api = _login(ceo)
    assert api.get(f"/api/v1/applications/{app.pk}/notifications/").status_code == 200
    r = api.post(f"/api/v1/applications/{app.pk}/retry-notification/",
                 {"notification": str(row.pk)}, format="json")
    assert r.status_code == 403


def test_the_history_is_scoped_like_the_application(
    therapist_job, office_boy_job, make_application, staff, django_capture_on_commit_callbacks
):
    """A medical interviewer cannot read what an operations candidate was told."""
    app = make_application(office_boy_job)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    api = _login(staff["clinic_doctor"].user)
    r = api.get(f"/api/v1/applications/{app.pk}/notifications/")
    assert r.status_code in (403, 404)


def test_cancel_over_http(therapist_job, make_application, at_stage, staff, django_capture_on_commit_callbacks):
    app = at_stage(make_application(therapist_job), 30)
    stage = therapist_job.workflow.stages.get(order=30)
    interview = schedule_interview(
        application=app, stage=stage, interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user, scheduled_at=timezone.now() + dt.timedelta(days=1),
    )
    api = _login(staff["recruiter"].user)
    with django_capture_on_commit_callbacks(execute=True):
        r = api.post(f"/api/v1/interviews/{interview.pk}/cancel/", {"reason": "clash"}, format="json")
    assert r.status_code == 200, r.data
    assert r.data["status"] == "cancelled"
    assert CandidateNotification.objects.filter(application=app, kind=K.INTERVIEW_CANCELLED).exists()


def test_a_malformed_optional_link_does_not_lose_the_application(therapist_job):
    """A Google Form does not validate links, so neither should the sync refuse them."""
    result = _apply(therapist_job, resume_link="file:///C:/Users/me/resume.pdf")
    assert result.created_application
    assert result.candidate.profile["resume_link"] == "file:///C:/Users/me/resume.pdf"


# ------------------------------------------------------------ the resume


def _pdf(name="Nisha Verma Resume.pdf", size=2000):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.4\n" + b"x" * size, content_type="application/pdf")


def test_an_uploaded_resume_is_stored_on_the_candidate_and_downloadable_by_hr(
    therapist_job, staff, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        result = public_intake.public_apply(
            job=therapist_job, submission_id="with-file", answers=_answers(), consented=True,
            resume_file=_pdf(),
        )
    candidate = result.candidate
    assert candidate.resume and candidate.resume.name.endswith(".pdf")

    api = _login(staff["recruiter"].user)
    detail = api.get(f"/api/v1/candidates/{candidate.pk}/")
    assert detail.data["has_resume"] is True and detail.data["resume_name"].endswith(".pdf")
    assert detail.data["resume"] is True  # a flag, never a storage URL

    download = api.get(f"/api/v1/candidates/{candidate.pk}/resume/")
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment")
    assert b"%PDF" in b"".join(download.streaming_content)


def test_the_resume_is_not_reachable_without_the_right_to_see_the_candidate(
    therapist_job, make_user, django_capture_on_commit_callbacks
):
    result = public_intake.public_apply(
        job=therapist_job, submission_id="with-file-2", answers=_answers(), consented=True,
        resume_file=_pdf(),
    )
    # No session at all: refused.
    assert APIClient().get(f"/api/v1/candidates/{result.candidate.pk}/resume/").status_code == 401
    # A role that cannot see candidates (payroll) is refused too.
    payroll = make_user("payroll_executive", email="pay@example.test")
    assert _login(payroll).get(f"/api/v1/candidates/{result.candidate.pk}/resume/").status_code in (403, 404)


def test_an_oversized_or_wrong_type_resume_is_refused_before_any_write(therapist_job):
    from django.core.files.uploadedfile import SimpleUploadedFile

    with pytest.raises(public_intake.PublicIntakeError) as exc:
        public_intake.public_apply(
            job=therapist_job, submission_id="bad", answers=_answers(), consented=True,
            resume_file=SimpleUploadedFile("virus.exe", b"MZ" * 10, content_type="application/octet-stream"),
        )
    assert "resume" in exc.value.message_dict
    with pytest.raises(public_intake.PublicIntakeError):
        public_intake.public_apply(
            job=therapist_job, submission_id="big", answers=_answers(), consented=True,
            resume_file=_pdf(size=6 * 1024 * 1024),
        )
    assert Candidate.objects.count() == 0


def test_the_public_endpoint_accepts_multipart_with_a_resume(therapist_job, django_capture_on_commit_callbacks):
    import json

    api = APIClient()
    with django_capture_on_commit_callbacks(execute=True):
        r = api.post(
            f"/api/v1/public/apply/{therapist_job.application_token}/",
            {"submission_id": "mp-1", "answers": json.dumps(_answers()), "consent": "true", "resume": _pdf()},
            format="multipart",
        )
    assert r.status_code == 201, r.data
    app = Application.objects.get(job_opening=therapist_job)
    assert app.candidate.resume.name.endswith(".pdf")
    # ...and the application-received email still goes.
    assert CandidateNotification.objects.filter(application=app, kind=K.APPLICATION_RECEIVED, status=S.SENT).exists()


def test_candidate_history_carries_the_same_shape_as_application_history(
    therapist_job, staff, django_capture_on_commit_callbacks
):
    """The profile page reads id/status/current_stage/events per application."""
    with django_capture_on_commit_callbacks(execute=True):
        result = _apply(therapist_job)
    api = _login(staff["hr_head"].user)
    r = api.get(f"/api/v1/candidates/{result.candidate.pk}/history/")
    assert r.status_code == 200
    app = r.data["applications"][0]
    assert app["id"] == str(result.application.pk)
    assert app["status"] == "active" and app["current_stage"]
    assert app["events"][0]["kind"] == "applied"
    # ...and it is exactly what the application endpoint says.
    single = api.get(f"/api/v1/applications/{result.application.pk}/history/").data
    assert single["events"] == app["events"] and single["status"] == app["status"]


def test_the_candidate_list_survives_a_storage_with_no_public_url(
    therapist_job, staff, django_capture_on_commit_callbacks
):
    """
    Production storage has no public media URL — deliberately. DRF's
    FileField renders by asking the storage for one, which raised and took
    the whole candidate LIST down for every HR user the moment one candidate
    had an uploaded resume. The serializer must never touch the URL.
    """
    from unittest.mock import PropertyMock, patch

    result = public_intake.public_apply(
        job=therapist_job, submission_id="no-url", answers=_answers(), consented=True,
        resume_file=_pdf(),
    )
    api = _login(staff["recruiter"].user)
    with patch(
        "django.db.models.fields.files.FieldFile.url",
        new_callable=PropertyMock,
        side_effect=ValueError("This file is not accessible via a URL."),
    ):
        listing = api.get("/api/v1/candidates/")
        assert listing.status_code == 200, listing.data
        row = next(r for r in listing.data["data"] if r["id"] == str(result.candidate.pk))
        assert row["resume"] is True and row["has_resume"] is True
        assert row["resume_name"].endswith(".pdf")
        detail = api.get(f"/api/v1/candidates/{result.candidate.pk}/")
        assert detail.status_code == 200
