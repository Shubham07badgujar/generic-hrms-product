"""
A recruiter in the interview chair.

The first custom workflow put Recruiter as Round 1's interviewer. The recruiter
could schedule and be booked, then could not record the outcome: the engine
maps "pass" at an interview stage to INTERVIEW_FEEDBACK/CREATE, which only the
department interviewer roles held. The HR roles now hold it at SELF scope.
"""

from __future__ import annotations

import pytest

from apps.workflows import services

pytestmark = pytest.mark.django_db


def test_a_recruiter_round_can_be_passed_by_the_recruiter_who_held_it(admin_user, roles, org, staff):
    admin = admin_user
    """
    The workflow names Recruiter as Round 1's interviewer. The recruiter
    schedules themself, holds the round, and records "pass". This was refused
    with "no permission to create interview_feedback" until the HR roles were
    given the interviewer grant at SELF scope.
    """
    import datetime as dt

    from django.utils import timezone

    from apps.recruitment.models import Application, Candidate, JobOpening, JobStatus
    from apps.recruitment.services.engine import record_decision
    from apps.recruitment.services.interviews import schedule_interview, submit_feedback
    from apps.workflows.models import Decision
    from core.access.catalog import DepartmentKind

    workflow = services.create_workflow(
        actor=admin, name="shubh", department_kind="",
        interview_rounds=[{"name": "Round 1", "role": "recruiter"}, {"name": "Round 2", "role": "hr_manager"}],
    )
    services.publish_workflow(actor=admin, workflow=workflow)  # must not refuse

    job = JobOpening.objects.create(
        title="Customer Relationship Executive", workflow=workflow,
        department=org["departments"][DepartmentKind.HR], target_role=roles["employee"],
        status=JobStatus.PUBLISHED, published_at=timezone.now(),
    )
    candidate = Candidate.objects.create(first_name="Shubham", email="s@example.test", phone="9000000001",
                                         consent_given=True, consent_at=timezone.now())
    app = Application.objects.create(candidate=candidate, job_opening=job, current_stage=workflow.first_stage)

    recruiter = staff["recruiter"]
    record_decision(application=app, actor=recruiter.user, decision=Decision.PASS)             # application stage
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)  # HR verification
    app.refresh_from_db()
    assert app.current_stage.name == "Round 1" and app.current_stage.responsible_role.code == "recruiter"

    interview = schedule_interview(application=app, stage=app.current_stage, interviewer=recruiter,
                                   actor=staff["hr_head"].user, scheduled_at=timezone.now() + dt.timedelta(days=1))
    # The round's verdict is the submitted feedback — the pass opens after it.
    submit_feedback(interview=interview, actor=recruiter.user, answers={}, recommendation="hire")
    result = record_decision(application=app, actor=recruiter.user, decision=Decision.PASS)
    assert result.to_stage.name == "Round 2"




def test_feedback_is_recordable_at_a_round_with_no_assessment_form(admin_user, roles, org, staff):
    """
    Round 3 of the custom workflow had no form, and the HR Manager holding it
    could not submit feedback at all. The verdict — recommendation, rating,
    strengths, concerns — is recorded without one; only structured answers
    need a form, so none are accepted.
    """
    import datetime as dt

    from django.core.exceptions import ValidationError
    from django.utils import timezone

    from apps.recruitment.models import Application, Candidate, JobOpening, JobStatus
    from apps.recruitment.services.engine import build_history_snapshot, record_decision
    from apps.recruitment.services.interviews import schedule_interview, submit_feedback
    from apps.workflows.models import Decision
    from core.access.catalog import DepartmentKind

    workflow = services.create_workflow(
        actor=admin_user, name="shubh", interview_rounds=[{"name": "Round 1", "role": "hr_manager"}],
    )
    services.publish_workflow(actor=admin_user, workflow=workflow)
    round1 = workflow.stages.get(name="Round 1")
    assert round1.feedback_form_id is None

    job = JobOpening.objects.create(
        title="CRE", workflow=workflow, department=org["departments"][DepartmentKind.HR],
        target_role=roles["employee"], status=JobStatus.PUBLISHED, published_at=timezone.now(),
    )
    candidate = Candidate.objects.create(first_name="Shubham", email="s2@example.test", phone="9000000002",
                                         consent_given=True, consent_at=timezone.now())
    app = Application.objects.create(candidate=candidate, job_opening=job, current_stage=workflow.first_stage)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.PASS)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)

    hrm = staff["hr_manager"]
    interview = schedule_interview(application=app, stage=round1, interviewer=hrm,
                                   actor=staff["hr_head"].user, scheduled_at=timezone.now() + dt.timedelta(days=1))

    # Structured answers make no sense without a form.
    with pytest.raises(ValidationError, match="no assessment form"):
        submit_feedback(interview=interview, actor=hrm.user, answers={"x": 1}, recommendation="hire")

    feedback = submit_feedback(
        interview=interview, actor=hrm.user, answers={}, recommendation="hire",
        strengths="Clear communicator", concerns="Limited SQL", overall_rating=4,
    )
    assert feedback.form_id is None and feedback.answers == {}
    interview.refresh_from_db()
    assert interview.status == "completed"

    # ...and it is what the HR Head will read at the final decision.
    snap = build_history_snapshot(app)
    mine = [i for i in snap["interviews"] if i.get("recommendation")]
    assert mine and mine[0]["recommendation"] == "hire"
    assert "Clear communicator" in str(mine[0])
