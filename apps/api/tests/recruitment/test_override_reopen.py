"""
Reopening a rejected application.

The bug this covers: the override used to change `status` to ACTIVE while
leaving `current_stage` on the terminal stage the rejection had moved it to.
The application then read as live but offered no decisions — reopened on paper
and unworkable in practice.

The fix is a stage the ENGINE derives from the configured workflow. These tests
are written so that hard-coding a stage name would fail them: the last one
builds a workflow out of stages this codebase has never heard of and asserts it
reopens correctly anyway.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.recruitment.models import ApplicationEvent, ApplicationStatus, DecisionOverride
from apps.recruitment.services.engine import (
    align_stage_with_status,
    record_decision,
    resolve_reopen_stage,
    stage_is_actionable,
)
from apps.recruitment.services.hiring import override_decision
from apps.workflows.models import Decision, StageTransition
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

REJECTION_REASON = "The candidate does not meet the clinical requirements for this role."
OVERRIDE_REASON = "Requisition reinstated after the department corrected its headcount forecast."


@pytest.fixture
def rejected(therapist_job, make_application, at_stage, staff):
    """An application taken to the terminal rejected stage by the real engine."""
    application = at_stage(make_application(therapist_job), 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale=REJECTION_REASON,
    )
    application.refresh_from_db()
    assert application.status == ApplicationStatus.REJECTED
    assert application.current_stage.is_terminal, "precondition: rejection parks at a terminal stage"
    return application


# ===================================================== the reopening itself


def test_a_rejected_application_can_be_reopened(rejected, admin_user):
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()
    assert rejected.status == ApplicationStatus.ACTIVE


def test_a_reopened_application_leaves_the_terminal_stage(rejected, admin_user):
    """The defect, stated directly."""
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()
    assert rejected.current_stage.is_terminal is False


def test_a_reopened_application_lands_somewhere_actionable(rejected, admin_user):
    """
    Not merely non-terminal: the stage must offer decisions AND have somewhere
    for each of them to go. A stage with decisions and no transition is a dead
    end that would look fine in the UI and fail on submit.
    """
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()
    stage = rejected.current_stage

    assert stage.allowed_decisions, "the reopened stage must offer decisions"
    assert stage_is_actionable(stage)

    for decision in stage.allowed_decisions:
        assert StageTransition.objects.filter(
            from_stage=stage, on_decision=decision, is_active=True
        ).exists(), f"'{decision}' at '{stage.name}' has nowhere to go"


def test_the_reopened_application_can_actually_be_decided_again(rejected, admin_user, staff):
    """
    The proof that matters: run a real decision through the engine afterwards.
    Everything above could pass while the application remained stuck.
    """
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()

    result = record_decision(
        application=rejected,
        actor=staff["hr_head"].user,
        decision=Decision.SELECT,
    )
    rejected.refresh_from_db()

    assert result.to_stage is not None
    assert rejected.status == ApplicationStatus.SELECTED


def test_reopening_returns_to_where_the_rejection_was_taken(rejected, admin_user):
    """The rule: the stage the engine recorded on the rejection record."""
    rejection_stage = rejected.rejection.rejection_stage

    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()
    assert rejected.current_stage_id == rejection_stage.pk


def test_reopening_stops_the_retention_clock(rejected, admin_user):
    """
    A candidate back in a live pipeline must not stay queued for anonymisation.
    """
    rejected.candidate.refresh_from_db()
    assert rejected.candidate.retention_until is not None, "precondition: clock started"

    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.candidate.refresh_from_db()
    assert rejected.candidate.final_decision_at is None
    assert rejected.candidate.retention_until is None


# ===================================================== the record is preserved


def test_the_original_rejection_survives_intact(rejected, admin_user):
    """Every fact about HR's decision stays exactly as HR left it."""
    original = rejected.rejection
    before = {
        "pk": original.pk,
        "rejected_by": original.rejected_by_id,
        "reason": original.reason,
        "rejected_at": original.rejected_at,
        "stage": original.rejection_stage_id,
    }

    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )

    original.refresh_from_db()
    assert original.pk == before["pk"]
    assert original.rejected_by_id == before["rejected_by"]
    assert original.reason == before["reason"]
    assert original.rejected_at == before["rejected_at"]
    assert original.rejection_stage_id == before["stage"]
    # Flagged, never deleted or rewritten.
    assert original.is_overridden is True


def test_the_original_decision_row_survives(rejected, admin_user):
    """The StageDecision that recorded the rejection is untouched."""
    decision_before = rejected.stage_decisions.filter(decision=Decision.REJECT).get()

    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )

    decision_after = rejected.stage_decisions.filter(decision=Decision.REJECT).get()
    assert decision_after.pk == decision_before.pk
    assert decision_after.rationale == REJECTION_REASON
    assert decision_after.decided_by_id == decision_before.decided_by_id


# ===================================================== the override is recorded


def test_the_override_records_both_the_status_and_the_stage_move(rejected, admin_user):
    previous_stage = rejected.current_stage

    override = override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()

    assert override.previous_status == ApplicationStatus.REJECTED
    assert override.new_status == ApplicationStatus.ACTIVE
    assert override.previous_stage_id == previous_stage.pk
    assert override.new_stage_id == rejected.current_stage_id
    assert override.overridden_by_id == admin_user.pk
    assert override.reason == OVERRIDE_REASON
    assert override.overridden_at is not None


def test_the_override_is_a_separate_record_from_the_rejection(rejected, admin_user):
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    # Two rows, two tables, two authors. An override is never mistakable for an
    # HR decision in the history.
    assert DecisionOverride.objects.filter(application=rejected).count() == 1
    assert rejected.rejection is not None
    assert rejected.rejection.rejected_by_id != admin_user.pk


def test_the_override_is_audited_with_the_stage_move(rejected, admin_user):
    from apps.audit.models import AuditAction, AuditLog

    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()

    entry = AuditLog.objects.filter(
        action=AuditAction.OVERRIDE, entity_id=str(rejected.pk)
    ).first()
    assert entry is not None
    assert entry.actor_id == admin_user.pk
    # Prior state in `before`, resulting state in `after` — the shape the audit
    # viewer's previous/new columns read. Every fact this test asserted before
    # is still asserted; only the column changed.
    assert entry.before["status"] == ApplicationStatus.REJECTED
    assert entry.after["status"] == ApplicationStatus.ACTIVE
    assert entry.before["stage"] != entry.after["stage"], "The stage move was not recorded."
    assert entry.after["stage"] == rejected.current_stage.name
    assert entry.after["reason"] == OVERRIDE_REASON
    # Also lifted onto its own column, so the viewer can show it without
    # knowing this writer's payload shape.
    assert entry.reason == OVERRIDE_REASON


def test_the_override_appears_in_the_application_history(rejected, admin_user):
    override_decision(
        application=rejected,
        actor=admin_user,
        new_status=ApplicationStatus.ACTIVE,
        reason=OVERRIDE_REASON,
    )
    rejected.refresh_from_db()

    event = rejected.events.filter(kind=ApplicationEvent.Kind.OVERRIDE).first()
    assert event is not None
    assert event.detail["reopened"] is True
    assert event.to_stage_id == rejected.current_stage_id
    assert event.from_stage is not None


# ===================================================== the security model holds


def test_the_override_still_requires_a_reason(rejected, admin_user):
    with pytest.raises(ValidationError):
        override_decision(
            application=rejected,
            actor=admin_user,
            new_status=ApplicationStatus.ACTIVE,
            reason="too short",
        )
    rejected.refresh_from_db()
    assert rejected.status == ApplicationStatus.REJECTED


@pytest.mark.parametrize("role_code", ["hr_head", "hr_manager", "medical_director", "recruiter"])
def test_nobody_but_admin_may_reopen(rejected, staff, role_code):
    with pytest.raises(AccessDenied):
        override_decision(
            application=rejected,
            actor=staff[role_code].user,
            new_status=ApplicationStatus.ACTIVE,
            reason=OVERRIDE_REASON,
        )
    rejected.refresh_from_db()
    assert rejected.status == ApplicationStatus.REJECTED
    assert rejected.current_stage.is_terminal


def test_admin_still_cannot_perform_a_normal_rejection(
    therapist_job, make_application, at_stage, admin_user
):
    """
    The override widened what Admin can do to a stage; it must not have widened
    what Admin can DECIDE. Rejection is still HR Head's alone.
    """
    application = at_stage(make_application(therapist_job), 60)
    with pytest.raises(AccessDenied):
        record_decision(
            application=application,
            actor=admin_user,
            decision=Decision.REJECT,
            rationale=REJECTION_REASON,
        )
    application.refresh_from_db()
    assert application.status == ApplicationStatus.ACTIVE


def test_hr_head_still_performs_the_normal_rejection(
    therapist_job, make_application, at_stage, staff
):
    application = at_stage(make_application(therapist_job), 60)
    record_decision(
        application=application,
        actor=staff["hr_head"].user,
        decision=Decision.REJECT,
        rationale=REJECTION_REASON,
    )
    application.refresh_from_db()
    assert application.status == ApplicationStatus.REJECTED
    assert application.rejection.reason == REJECTION_REASON


# ===================================================== the invariant, both ways


def test_closing_by_override_moves_to_a_terminal_stage(
    therapist_job, make_application, at_stage, admin_user
):
    """
    The mirror of the bug. An override that CLOSES a live application must not
    leave it parked at a workable stage, or it reads as closed while still
    offering decisions.
    """
    application = at_stage(make_application(therapist_job), 30)
    assert application.current_stage.is_terminal is False

    override_decision(
        application=application,
        actor=admin_user,
        new_status=ApplicationStatus.WITHDRAWN,
        reason="Candidate asked us to close their application and remove them from the process.",
    )
    application.refresh_from_db()
    assert application.status == ApplicationStatus.WITHDRAWN
    assert application.current_stage.is_terminal is True


def test_an_override_that_changes_nothing_structural_leaves_the_stage_alone(
    therapist_job, make_application, at_stage, admin_user
):
    application = at_stage(make_application(therapist_job), 30)
    stage_before = application.current_stage_id

    override_decision(
        application=application,
        actor=admin_user,
        new_status=ApplicationStatus.SELECTED,
        reason="Selection recorded administratively while HR Head was unavailable to act.",
    )
    application.refresh_from_db()
    # Both statuses are live, so there is nothing to correct.
    assert application.current_stage_id == stage_before


def test_no_reopened_application_can_remain_terminal(
    request, staff, admin_user, make_application, at_stage
):
    """Swept across BOTH configured workflows, so neither is a special case."""
    for job_fixture, head in [
        ("therapist_job", "medical_director"),
        ("office_boy_job", "operational_head"),
    ]:
        job = request.getfixturevalue(job_fixture)
        application = at_stage(make_application(job, email=f"{job_fixture}@example.test"), 60)
        record_decision(
            application=application,
            actor=staff["hr_head"].user,
            decision=Decision.REJECT,
            rationale=REJECTION_REASON,
        )
        application.refresh_from_db()
        assert application.current_stage.is_terminal

        override_decision(
            application=application,
            actor=admin_user,
            new_status=ApplicationStatus.ACTIVE,
            reason=OVERRIDE_REASON,
        )
        application.refresh_from_db()

        assert application.status == ApplicationStatus.ACTIVE
        assert application.current_stage.is_terminal is False, f"{job_fixture} left terminal"
        assert stage_is_actionable(application.current_stage), f"{job_fixture} not actionable"
        assert head  # the department head differs per workflow; neither is named in the rule


# ===================================================== derivation, not hard-coding


def test_the_reopening_stage_is_derived_from_configuration(rejected, admin_user):
    """
    Change the configuration, and the answer changes with it.

    A second route into the terminal stage is configured, so the transition
    table now offers two ways back. The rejection stage is then made
    unactionable, and the resolver must fall through to the other one — which a
    rule that knew a stage by name could not do.
    """
    workflow = rejected.job_opening.workflow
    department_stage = workflow.stages.get(order=50)
    terminal = rejected.current_stage

    # A second, configured path into the same terminal stage.
    StageTransition.objects.create(
        from_stage=department_stage, on_decision=Decision.WITHDRAW, to_stage=terminal
    )

    original_stage = rejected.rejection.rejection_stage
    assert resolve_reopen_stage(rejected).pk == original_stage.pk, "prefers the rejection stage"

    original_stage.allowed_decisions = []
    original_stage.save(update_fields=["allowed_decisions", "updated_at"])

    resolved = resolve_reopen_stage(rejected)
    assert resolved.pk == department_stage.pk
    assert stage_is_actionable(resolved)


def test_an_invented_workflow_reopens_correctly(db, roles, org, staff, admin_user):
    """
    THE ANTI-HARD-CODING TEST.

    Builds a pipeline whose stages, names and shape exist nowhere in this
    codebase, rejects a candidate through it, and reopens them. Passing means
    the rule genuinely reads configuration; any stage name baked into the
    resolver would fail here.
    """
    import datetime as dt

    from django.utils import timezone

    from apps.recruitment.models import Application, Candidate, JobOpening, JobStatus
    from apps.workflows.models import HiringWorkflow, StageKind, WorkflowStage
    from core.access.catalog import DepartmentKind

    workflow = HiringWorkflow.objects.create(
        name="Guild appointment", description="Invented", is_published=True
    )
    submitted = WorkflowStage.objects.create(
        workflow=workflow, name="Portfolio submitted", order=5, kind=StageKind.APPLICATION,
        responsible_role=roles["recruiter"], allowed_decisions=[Decision.PASS],
    )
    chancellor = WorkflowStage.objects.create(
        workflow=workflow, name="Chancellor sign-off", order=15,
        kind=StageKind.HR_FINAL_DECISION, responsible_role=roles["hr_head"],
        allowed_decisions=[Decision.SELECT, Decision.REJECT], is_final_hr_decision=True,
    )
    appointed = WorkflowStage.objects.create(
        workflow=workflow, name="Appointed", order=90, kind=StageKind.TERMINAL,
        is_terminal=True, is_won=True,
    )
    turned_away = WorkflowStage.objects.create(
        workflow=workflow, name="Turned away", order=95, kind=StageKind.TERMINAL,
        is_terminal=True,
    )
    StageTransition.objects.create(from_stage=submitted, on_decision=Decision.PASS, to_stage=chancellor)
    StageTransition.objects.create(from_stage=chancellor, on_decision=Decision.SELECT, to_stage=appointed)
    StageTransition.objects.create(from_stage=chancellor, on_decision=Decision.REJECT, to_stage=turned_away)

    job = JobOpening.objects.create(
        title="Guild Artisan", workflow=workflow,
        department=org["departments"][DepartmentKind.HR],
        target_role=roles["employee"], location=org["location"],
        status=JobStatus.PUBLISHED, published_at=timezone.now(),
    )
    candidate = Candidate.objects.create(
        first_name="Ines", last_name="Rocha", email="ines@example.test",
        consent_given=True, consent_at=timezone.now(),
    )
    application = Application.objects.create(
        candidate=candidate, job_opening=job, current_stage=chancellor, is_verified=True
    )

    record_decision(
        application=application, actor=staff["hr_head"].user,
        decision=Decision.REJECT, rationale=REJECTION_REASON,
    )
    application.refresh_from_db()
    assert application.current_stage_id == turned_away.pk

    override_decision(
        application=application, actor=admin_user,
        new_status=ApplicationStatus.ACTIVE, reason=OVERRIDE_REASON,
    )
    application.refresh_from_db()

    assert application.status == ApplicationStatus.ACTIVE
    assert application.current_stage_id == chancellor.pk
    assert stage_is_actionable(application.current_stage)
    assert dt  # imported for clarity of intent in this fixture-free construction


# ===================================================== failing safely


def test_a_workflow_with_no_actionable_stage_refuses_to_reopen(rejected, admin_user):
    """
    A misconfigured workflow must raise, not invent a destination.

    Every non-terminal stage is stripped of its decisions, leaving nowhere
    legitimate to go. Silently parking the candidate somewhere would hide the
    configuration fault and produce exactly the stuck state this work fixed.
    """
    workflow = rejected.job_opening.workflow
    workflow.stages.filter(is_terminal=False).update(allowed_decisions=[])

    with pytest.raises(ValidationError) as exc:
        override_decision(
            application=rejected,
            actor=admin_user,
            new_status=ApplicationStatus.ACTIVE,
            reason=OVERRIDE_REASON,
        )
    assert "no actionable stage" in str(exc.value)

    # And nothing was half-applied.
    rejected.refresh_from_db()
    assert rejected.status == ApplicationStatus.REJECTED
    assert rejected.current_stage.is_terminal
    assert DecisionOverride.objects.filter(application=rejected).count() == 0


def test_a_workflow_with_no_terminal_stage_refuses_to_close(
    therapist_job, make_application, at_stage, admin_user
):
    application = at_stage(make_application(therapist_job), 30)
    therapist_job.workflow.stages.filter(is_terminal=True, is_won=False).update(is_active=False)

    with pytest.raises(ValidationError) as exc:
        override_decision(
            application=application,
            actor=admin_user,
            new_status=ApplicationStatus.REJECTED,
            reason="Closing this application administratively at the candidate's request.",
        )
    assert "terminal stage" in str(exc.value)


def test_a_stage_with_decisions_but_no_transition_is_not_actionable(therapist_job):
    """
    A dead end must not count. The stage would render decision buttons that
    fail on submit, which is worse than showing none.
    """
    stage = therapist_job.workflow.stages.get(order=30)
    assert stage_is_actionable(stage) is True

    StageTransition.objects.filter(from_stage=stage).update(is_active=False)
    assert stage_is_actionable(stage) is False


def test_align_leaves_a_coherent_pairing_untouched(therapist_job, make_application, at_stage):
    application = at_stage(make_application(therapist_job), 30)
    resolved = align_stage_with_status(application, new_status=ApplicationStatus.ACTIVE)
    assert resolved.pk == application.current_stage_id
