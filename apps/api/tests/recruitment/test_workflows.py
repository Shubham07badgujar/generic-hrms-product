"""
The workflow engine.

The central claim under test: ONE engine runs both pipelines. Several tests are
parametrised across Therapist and Office Boy precisely to demonstrate that the
same code path serves both — if either needed special handling, the shared
assertions would fail.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.recruitment.models import ApplicationStatus
from apps.recruitment.services.engine import record_decision
from apps.workflows.models import Decision
from core.access import AccessDenied
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db


# ============================================ the engine is generic


def test_the_two_workflows_are_pure_configuration(workflows):
    """
    Both pipelines exist as data with identical shape and different actors.

    If either required bespoke code, they could not be described by the same
    structure this test walks.
    """
    therapist = workflows["Therapist hiring"]
    office_boy = workflows["Office Boy hiring"]

    def shape(workflow):
        return [(s.order, s.kind) for s in workflow.stages.order_by("order")]

    assert shape(therapist) == shape(office_boy), (
        "Both workflows share a stage shape; only the responsible roles differ."
    )

    def interviewers(workflow):
        return [
            s.responsible_role.code
            for s in workflow.stages.order_by("order")
            if s.kind in ("interview", "department_decision")
        ]

    assert interviewers(therapist) == ["clinic_doctor", "senior_doctor", "medical_director"]
    assert interviewers(office_boy) == ["cre", "operations_manager", "operational_head"]


def test_no_job_title_branching_in_the_engine():
    """
    The engine must not know what a Therapist is.

    A grep-based guard: if someone later adds `if job.title == ...`, this fails.
    """
    import re
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parents[2] / "apps" / "recruitment" / "services"
    # Whole-word matching: "cre" as a bare identifier is a role code, but it is
    # also a substring of "create", which appears legitimately everywhere.
    forbidden = (
        "therapist", "office_boy", "clinic_doctor", "senior_doctor",
        "medical_director", "operations_manager", "operational_head", "cre",
    )

    offenders = []
    for path in engine_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden:
            if re.search(rf"\b{re.escape(term)}\b", text):
                offenders.append(f"{path.name} mentions '{term}'")

    assert not offenders, (
        "The workflow engine must be generic — job titles and roles belong in "
        "configuration:\n  " + "\n  ".join(offenders)
    )


def test_exactly_one_final_decision_stage_per_workflow(workflows):
    for workflow in workflows.values():
        finals = workflow.stages.filter(is_final_hr_decision=True)
        assert finals.count() == 1
        assert finals.first().responsible_role.code == "hr_head"


def test_no_stage_but_the_final_one_may_carry_terminal_decisions(workflows):
    """The configuration-level guarantee that interviewers cannot reject."""
    for workflow in workflows.values():
        for stage in workflow.stages.all():
            terminal = set(stage.allowed_decisions) & {Decision.SELECT, Decision.REJECT}
            if terminal:
                assert stage.is_final_hr_decision, (
                    f"'{stage.name}' carries {terminal} without final authority."
                )


# ============================================ complete happy paths


@pytest.mark.parametrize(
    "job_fixture,interview_roles,department_head",
    [
        ("therapist_job", ["clinic_doctor", "senior_doctor"], "medical_director"),
        ("office_boy_job", ["cre", "operations_manager"], "operational_head"),
    ],
)
def test_complete_workflow_to_selection(
    request, job_fixture, interview_roles, department_head,
    staff, make_application, completed_interview,
):
    """
    The whole pipeline, driven end to end by the same engine.

    Parametrised across both workflows: one test body, two pipelines.
    """
    job = request.getfixturevalue(job_fixture)
    application = make_application(job)

    # The pipeline opens at Recruiter verification.
    assert application.current_stage.kind == "hr_verification"

    # HR verification → first interview stage
    record_decision(
        application=application, actor=staff["recruiter"].user, decision=Decision.VERIFY
    )
    application.refresh_from_db()
    assert application.is_verified

    # The two interview rounds
    for order, role_code in zip((30, 40), interview_roles):
        completed_interview(application, order, role_code)
        record_decision(
            application=application, actor=staff[role_code].user, decision=Decision.PASS
        )
        application.refresh_from_db()

    # Department recommendation
    assert application.current_stage.kind == "department_decision"
    record_decision(
        application=application,
        actor=staff[department_head].user,
        decision=Decision.RECOMMEND_SELECT,
        rationale="Strong candidate across all rounds.",
    )
    application.refresh_from_db()

    # HR Head final decision
    assert application.current_stage.is_final_hr_decision
    record_decision(
        application=application, actor=staff["hr_head"].user, decision=Decision.SELECT
    )
    application.refresh_from_db()

    assert application.status == ApplicationStatus.SELECTED
    assert application.current_stage.kind == "offer"


# ============================================ HR verification gate


def test_department_stages_are_closed_until_hr_verifies(
    therapist_job, staff, make_application, at_stage, completed_interview
):
    """
    The verification gate is enforced by the ENGINE, not merely by routing.

    Forcing an unverified application to an interview stage — as a direct API
    call would — must still be refused.
    """
    application = make_application(therapist_job)
    at_stage(application, 30, verified=False)
    completed_interview(application, 30, "clinic_doctor")

    with pytest.raises(ValidationError, match="not completed HR verification"):
        record_decision(
            application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
        )


def test_hr_can_request_more_information(therapist_job, staff, make_application, at_stage):
    application = make_application(therapist_job)
    at_stage(application, 20, verified=False)

    record_decision(
        application=application,
        actor=staff["recruiter"].user,  # verification is the Recruiter's stage
        decision=Decision.REQUEST_INFO,
        rationale="Resume missing.",
    )

    application.refresh_from_db()
    assert application.current_stage.order == 20, "Stays at verification."
    assert not application.is_verified


# ============================================ stage authorisation


def test_wrong_role_cannot_act_at_a_stage(
    therapist_job, staff, make_application, at_stage, completed_interview
):
    """A Senior Doctor cannot act at the Clinic Doctor stage."""
    application = make_application(therapist_job)
    at_stage(application, 30)
    completed_interview(application, 30, "clinic_doctor")

    with pytest.raises(ValidationError, match="may only be actioned by"):
        record_decision(
            application=application, actor=staff["senior_doctor"].user, decision=Decision.PASS
        )


def test_interviewer_from_another_department_is_refused(
    office_boy_job, staff, make_application, at_stage, roles, org
):
    """
    A medical interviewer cannot act on an operations vacancy.

    Constructed by granting the CRE role to a medical-department employee, so
    the ROLE check passes and only the DEPARTMENT check can refuse — isolating
    exactly the guard under test.
    """
    from apps.accounts.models import UserRole

    application = make_application(office_boy_job)
    at_stage(application, 30)

    intruder = staff["clinic_doctor"]
    UserRole.objects.create(user=intruder.user, role=roles["cre"])
    bind_membership(intruder.user)

    with pytest.raises(ValidationError, match="you are in"):
        record_decision(
            application=application, actor=intruder.user, decision=Decision.PASS
        )


def test_the_workflow_may_assign_a_senior_from_another_function(
    office_boy_job, staff, make_application, at_stage, roles, completed_interview
):
    """
    The stage naming a role from ANOTHER function is explicit permission to
    cross departments: a Senior Doctor round inside an operations pipeline
    admits the (medical-department) Senior Doctor. The department gate still
    stands for same-function stages — the intruder test above pins that.
    """
    stage = office_boy_job.workflow.stages.get(order=40)
    stage.responsible_role = roles["senior_doctor"]
    stage.save(update_fields=["responsible_role", "updated_at"])

    application = make_application(office_boy_job)
    at_stage(application, 40)
    completed_interview(application, 40, "senior_doctor")

    result = record_decision(
        application=application, actor=staff["senior_doctor"].user, decision=Decision.PASS
    )
    assert result.to_stage.order == 50

    # The cross-function assignment admits holders of THAT role only: an
    # operations manager (same function as the vacancy, different department
    # story) is still refused by the role check itself.
    application2 = make_application(office_boy_job, first_name="Second")
    at_stage(application2, 40)
    with pytest.raises(ValidationError, match="may only be actioned by"):
        record_decision(
            application=application2,
            actor=staff["operations_manager"].user,
            decision=Decision.PASS,
        )


def test_an_interviewer_may_recommend_reject_their_own_round(
    therapist_job, staff, make_application, at_stage, completed_interview
):
    """
    `recommend_reject` at an interview round is the interviewer's own verdict,
    authorised exactly like their pass — it routes to HR Head and rejects
    nobody. Requiring DEPARTMENT_DECISION/RECOMMEND here refused the very
    interviewer the workflow put in the chair.
    """
    application = make_application(therapist_job)
    at_stage(application, 30)
    completed_interview(application, 30, "clinic_doctor")
    result = record_decision(
        application=application,
        actor=staff["clinic_doctor"].user,
        decision=Decision.RECOMMEND_REJECT,
        rationale="Clinical depth below this round's bar.",
    )
    assert result.to_stage.is_final_hr_decision


def test_recruiter_cannot_make_the_final_decision(
    therapist_job, staff, make_application, at_stage
):
    application = make_application(therapist_job)
    at_stage(application, 60)

    with pytest.raises((ValidationError, AccessDenied)):
        record_decision(
            application=application, actor=staff["recruiter"].user, decision=Decision.SELECT
        )


@pytest.mark.parametrize(
    "role_code", ["clinic_doctor", "senior_doctor", "medical_director", "recruiter", "hr_manager"]
)
def test_only_hr_head_can_reject(therapist_job, staff, make_application, at_stage, role_code):
    """
    THE central authority rule, asserted for every other participant.

    Each is placed at the final decision stage and attempts a rejection.
    """
    application = make_application(therapist_job)
    at_stage(application, 60)

    with pytest.raises((ValidationError, AccessDenied)):
        record_decision(
            application=application,
            actor=staff[role_code].user,
            decision=Decision.REJECT,
            rationale="Attempting a rejection without the authority to do so.",
        )

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE


def test_department_head_can_only_recommend_rejection(
    therapist_job, staff, make_application, at_stage
):
    """A recommendation routes to HR; it does not end the candidacy."""
    application = make_application(therapist_job)
    at_stage(application, 50)

    record_decision(
        application=application,
        actor=staff["medical_director"].user,
        decision=Decision.RECOMMEND_REJECT,
        rationale="Insufficient clinical depth for this role.",
    )

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE, (
        "A department recommendation must never itself reject the candidate."
    )
    assert application.current_stage.is_final_hr_decision, "It routes to HR Head."


# ============================================ HR Head final decision


def test_hr_head_rejection_requires_a_reason(therapist_job, staff, make_application, at_stage):
    application = make_application(therapist_job)
    at_stage(application, 60)

    with pytest.raises(ValidationError, match="at least 20 characters"):
        record_decision(
            application=application,
            actor=staff["hr_head"].user,
            decision=Decision.REJECT,
            rationale="no",
        )

    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE


def test_hr_head_rejection_is_recorded_permanently(
    therapist_job, staff, make_application, at_stage, completed_interview
):
    from apps.recruitment.models import CandidateRejection

    application = make_application(therapist_job)
    at_stage(application, 30)
    completed_interview(application, 30, "clinic_doctor")
    record_decision(
        application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
    )
    application.refresh_from_db()
    at_stage(application, 50)
    record_decision(
        application=application,
        actor=staff["medical_director"].user,
        decision=Decision.RECOMMEND_REJECT,
        rationale="Not suitable for the clinical requirements of this post.",
    )
    application.refresh_from_db()

    reason = "Insufficient clinical experience for the therapist role as assessed."
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale=reason,
    )

    application.refresh_from_db()
    assert application.status == ApplicationStatus.REJECTED

    rejection = CandidateRejection.objects.get(application=application)
    assert rejection.reason == reason
    assert rejection.rejected_by_id == staff["hr_head"].user.pk
    assert rejection.department_recommendation is not None, (
        "The rejection must link the department recommendation that preceded it."
    )
    assert rejection.history_snapshot["interviews"], (
        "The history is frozen so the justification survives later edits."
    )


def test_a_closed_application_cannot_be_actioned_further(
    therapist_job, staff, make_application, at_stage
):
    application = make_application(therapist_job)
    at_stage(application, 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale="Not suitable for this position after full review.",
    )

    with pytest.raises(ValidationError, match="can no longer be actioned"):
        record_decision(
            application=application, actor=staff["hr_head"].user, decision=Decision.SELECT
        )


# ============================================ invalid transitions


def test_a_decision_not_allowed_at_a_stage_is_refused(
    therapist_job, staff, make_application, at_stage
):
    application = make_application(therapist_job)
    at_stage(application, 50)

    with pytest.raises(ValidationError, match="not permitted at"):
        record_decision(
            application=application, actor=staff["medical_director"].user, decision=Decision.VERIFY
        )


def test_an_interview_stage_refuses_a_decision_before_the_interview(
    therapist_job, staff, make_application, at_stage
):
    application = make_application(therapist_job)
    at_stage(application, 30)

    with pytest.raises(ValidationError, match="requires an interview"):
        record_decision(
            application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
        )


def test_an_interview_stage_refuses_a_decision_before_feedback(
    therapist_job, staff, make_application, at_stage
):
    from apps.recruitment.services.interviews import schedule_interview

    application = make_application(therapist_job)
    at_stage(application, 30)
    schedule_interview(
        application=application,
        stage=application.current_stage,
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user,
        scheduled_at=timezone.now() + dt.timedelta(days=1),
    )

    with pytest.raises(ValidationError, match="submitted\\s+feedback"):
        record_decision(
            application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
        )


def test_the_feedback_gate_is_structural_not_configuration(
    therapist_job, staff, make_application, at_stage
):
    """
    The rule the live workflows fell through: stages authored in the UI
    carried requires_feedback=False, and the old gate read that flag — so a
    candidate could pass a round nobody had assessed. The verdict on an
    interview round IS the submitted feedback; no authoring choice may turn
    that off.
    """
    from apps.recruitment.services.interviews import schedule_interview, submit_feedback

    application = make_application(therapist_job)
    at_stage(application, 30)
    stage = application.current_stage
    stage.requires_feedback = False
    stage.save(update_fields=["requires_feedback"])

    interview = schedule_interview(
        application=application,
        stage=stage,
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user,
        scheduled_at=timezone.now() + dt.timedelta(days=1),
    )

    with pytest.raises(ValidationError, match="submitted\\s+feedback"):
        record_decision(
            application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
        )

    answers = {
        field.key: (3 if field.kind == "rating_1_5" else True if field.kind == "boolean" else "ok")
        for field in stage.feedback_form.fields.filter(is_required=True)
    }
    submit_feedback(
        interview=interview, actor=staff["clinic_doctor"].user,
        answers=answers, recommendation="hire",
    )
    moved = record_decision(
        application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
    )
    assert moved.to_stage is not None


def test_a_cancelled_interview_does_not_satisfy_the_round(
    therapist_job, staff, make_application, at_stage
):
    """A rebooked round must be held again — its cancelled sitting counts for nothing."""
    from apps.recruitment.services.interviews import cancel_interview, schedule_interview

    application = make_application(therapist_job)
    at_stage(application, 30)
    interview = schedule_interview(
        application=application,
        stage=application.current_stage,
        interviewer=staff["clinic_doctor"],
        actor=staff["hr_head"].user,
        scheduled_at=timezone.now() + dt.timedelta(days=1),
    )
    cancel_interview(interview=interview, actor=staff["hr_head"].user, reason="rebooking")

    with pytest.raises(ValidationError, match="requires an interview"):
        record_decision(
            application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
        )


# ============================================ history


def test_every_movement_is_recorded_in_history(
    therapist_job, staff, make_application, completed_interview
):
    from apps.recruitment.models import ApplicationEvent

    application = make_application(therapist_job)
    record_decision(
        application=application, actor=staff["recruiter"].user, decision=Decision.VERIFY
    )
    application.refresh_from_db()
    completed_interview(application, 30, "clinic_doctor")
    # Passing the round is the first plain stage change — verification's own
    # movement is recorded as its dedicated "verified" event.
    record_decision(
        application=application, actor=staff["clinic_doctor"].user, decision=Decision.PASS
    )

    kinds = list(
        ApplicationEvent.objects.filter(application=application).values_list("kind", flat=True)
    )

    assert "stage_changed" in kinds
    assert "verified" in kinds
    assert "interview_scheduled" in kinds
    assert "feedback_submitted" in kinds

    for event in ApplicationEvent.objects.filter(application=application):
        assert event.actor_id is not None, "Every event must name its actor."
