"""
Authoring hiring workflows.

The admin does not hand-place stages and transitions. They describe the
PROCESS — who verifies, who conducts each interview round, whether a
department head recommends, and that HR Head decides — and this module
generates the same canonical stage/transition graph the seeds build. That is
a deliberate narrowing: the graph has invariants (terminal decisions only on
the final HR stage, every path ending in a terminal, rejection routing to the
decision stage rather than around it) that a free-form stage editor would let
a well-meaning admin violate on a Tuesday. Describing the process makes the
invalid pipelines inexpressible instead of merely refused.

Lifecycle: a workflow is born a DRAFT, editable and invisible to job forms
(the job serializer refuses drafts). Publishing freezes its structure —
candidates may then be riding it, and editing a live pipeline moves their
ground — so a process change is a NEW workflow, not an edit to the old one.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import Role
from core.access import Action, Resource, require

from .models import (
    Decision,
    FeedbackForm,
    HiringWorkflow,
    StageKind,
    StageTransition,
    WorkflowStage,
)

MAX_INTERVIEW_ROUNDS = 6


class WorkflowError(ValidationError):
    """A refused workflow operation."""


def _role(code_or_id) -> Role:
    """Resolve a role by id or by code, so the API accepts either."""
    try:
        uuid.UUID(str(code_or_id))
        query = Q(pk=code_or_id)
    except (ValueError, AttributeError, TypeError):
        query = Q(code=str(code_or_id))

    role = Role.objects.filter(is_active=True).filter(query).first()
    if role is None:
        raise WorkflowError({"role": f"No such role: {code_or_id}."})
    return role


@transaction.atomic
def create_workflow(
    *,
    actor,
    name: str,
    interview_rounds: list[dict],
    description: str = "",
    department_kind: str = "",
    verification_role="recruiter",
    recommendation_role=None,
) -> HiringWorkflow:
    """
    Build a draft workflow from a process description.

    `interview_rounds`, in interview order:
        [{"name": "Round 1 — Clinic Doctor", "role": <role id or code>,
          "feedback_form": <form id> | None}, ...]

    `recommendation_role` — the department head who recommends, or None to go
    straight from the last interview round to HR Head's decision.
    """
    require(actor, Resource.HIRING_WORKFLOW, Action.CREATE)

    if not interview_rounds:
        raise WorkflowError(
            {"interview_rounds": "A hiring process needs at least one interview round."}
        )
    if len(interview_rounds) > MAX_INTERVIEW_ROUNDS:
        raise WorkflowError(
            {"interview_rounds": f"At most {MAX_INTERVIEW_ROUNDS} interview rounds."}
        )
    # ALL rows, not only active ones: the name is unique at the database
    # across retired workflows too, so checking only the living would let a
    # clash reach the constraint and come back as a bare 500.
    clash = HiringWorkflow.objects.filter(name=name).first()
    if clash is not None:
        raise WorkflowError(
            {"name": "A workflow with this name already exists."
                     if clash.is_active else
                     "A retired workflow still holds this name — choose another."}
        )

    workflow = HiringWorkflow.objects.create(
        name=name,
        description=description,
        department_kind=department_kind,
        is_published=False,
        is_default=False,
    )
    _build_stages(
        workflow,
        verification_role=verification_role,
        interview_rounds=interview_rounds,
        recommendation_role=recommendation_role,
    )
    return workflow


@transaction.atomic
def update_workflow(
    *,
    actor,
    workflow: HiringWorkflow,
    name: str | None = None,
    description: str | None = None,
    department_kind: str | None = None,
    verification_role=None,
    interview_rounds: list[dict] | None = None,
    recommendation_role=...,
) -> HiringWorkflow:
    """Edit a DRAFT. A published workflow's structure is frozen — see module docstring."""
    require(actor, Resource.HIRING_WORKFLOW, Action.EDIT)

    structural = (
        interview_rounds is not None
        or verification_role is not None
        or recommendation_role is not ...
    )
    if workflow.is_published and structural:
        raise WorkflowError(
            {
                "workflow": (
                    "This workflow is published; candidates may be riding it, and "
                    "restructuring it would move their ground. Create a new "
                    "workflow for the new process and assign it to future jobs."
                )
            }
        )

    if name is not None:
        clash = HiringWorkflow.objects.filter(name=name).exclude(pk=workflow.pk)
        if clash.exists():
            raise WorkflowError({"name": "A workflow with this name already exists."})
        workflow.name = name
    if description is not None:
        workflow.description = description
    if department_kind is not None:
        if workflow.is_published:
            raise WorkflowError(
                {"department_kind": "A published workflow's function cannot change."}
            )
        workflow.department_kind = department_kind
    workflow.save()

    if structural:
        if interview_rounds is None or not interview_rounds:
            raise WorkflowError(
                {"interview_rounds": "A structural edit must restate the interview rounds."}
            )
        # Regenerate from scratch: the graph is derived, never hand-edited,
        # so replacing it wholesale is the honest operation.
        workflow.stages.all().hard_delete()
        _build_stages(
            workflow,
            verification_role=verification_role or "recruiter",
            interview_rounds=interview_rounds,
            recommendation_role=None if recommendation_role is ... else recommendation_role,
        )
    return workflow


@transaction.atomic
def publish_workflow(*, actor, workflow: HiringWorkflow) -> HiringWorkflow:
    """One-way. From here the structure is frozen and job forms may offer it."""
    require(actor, Resource.HIRING_WORKFLOW, Action.EDIT)

    if workflow.is_published:
        return workflow
    if not workflow.stages.filter(is_active=True).exists():
        raise WorkflowError({"workflow": "A workflow needs stages before publishing."})
    assert_stage_roles_can_decide(workflow)

    workflow.is_published = True
    workflow.save(update_fields=["is_published", "updated_at"])
    return workflow


def assert_stage_roles_can_decide(workflow: HiringWorkflow) -> None:
    """
    Every stage's responsible role must HOLD the permission the engine will
    demand for each decision that stage allows.

    The engine authorises a decision twice: by the workflow (is this the
    stage's role?) and by the permission matrix (does that role hold, say,
    INTERVIEW_FEEDBACK/CREATE for an interview round?). A workflow can name a
    role the matrix never equipped — the first custom workflow did exactly
    that, putting a Recruiter in an interview chair — and the result was a
    person who could be booked but never record the outcome. Refusing at
    publish time puts the error where the designer can fix it.
    """
    from apps.accounts.models import RolePermission
    from apps.recruitment.services.engine import ADVISORY_DECISIONS, _rbac_action_for
    from core.access import Scope

    problems: list[str] = []
    for stage in workflow.stages.filter(is_active=True).select_related("responsible_role"):
        if stage.responsible_role_id is None:
            continue
        # Progression decisions only. An interviewer's advisory
        # "recommend_reject" has always been authorised as a department
        # recommendation and routed to the department head; that is a
        # deliberate asymmetry of the existing engine, not a misconfiguration
        # of the workflow, and is not what this guard is for.
        for decision in [d for d in (stage.allowed_decisions or []) if d not in ADVISORY_DECISIONS]:
            try:
                resource, action = _rbac_action_for(stage, decision)
            except Exception:  # noqa: BLE001 — an unmapped kind is its own error elsewhere
                continue
            held = RolePermission.objects.filter(
                role=stage.responsible_role, resource=resource, action=action, is_active=True
            ).values_list("scope", flat=True).first()
            if not held or held <= Scope.NONE:
                problems.append(
                    f"'{stage.name}': {stage.responsible_role.name} cannot record "
                    f"'{decision}' there — the role does not hold {resource}/{action}."
                )
    if problems:
        raise WorkflowError({"stages": problems})


@transaction.atomic
def deactivate_workflow(*, actor, workflow: HiringWorkflow) -> HiringWorkflow:
    require(actor, Resource.HIRING_WORKFLOW, Action.DELETE)

    if workflow.job_openings.filter(is_active=True).exists():
        raise WorkflowError(
            {
                "workflow": (
                    "Job openings still use this workflow. Close or repoint them "
                    "first — retiring it now would strand their pipelines."
                )
            }
        )
    workflow.delete()  # soft
    return workflow


# ------------------------------------------------------------- generation


def _build_stages(workflow, *, verification_role, interview_rounds, recommendation_role):
    """The canonical graph, exactly as the seeds shape it."""
    if not interview_rounds:
        raise WorkflowError(
            {"interview_rounds": "A hiring process needs at least one interview round."}
        )

    hr_head = _role("hr_head")
    hr_manager = _role("hr_manager")
    recruiter = _role("recruiter")

    def stage(order, name, kind, role, decisions, *, interview=False, form=None, **flags):
        return WorkflowStage.objects.create(
            workflow=workflow,
            order=order,
            name=name,
            kind=kind,
            responsible_role=role,
            allowed_decisions=[str(d) for d in decisions],
            requires_interview=interview,
            requires_feedback=interview and form is not None,
            feedback_form=form,
            is_mandatory=True,
            **flags,
        )

    application = stage(
        10, "Application received", StageKind.APPLICATION, recruiter, [Decision.PASS]
    )
    # Verification belongs to the Recruiter: they review the application and
    # the résumé, and either verify (on to the first round) or reject with a
    # reason. No second role is needed for an application to proceed.
    verification = stage(
        20,
        "Recruiter verification",
        StageKind.HR_VERIFICATION,
        _role(verification_role),
        [Decision.VERIFY, Decision.REQUEST_INFO, Decision.SCREEN_OUT],
    )

    order = 30
    interview_stages = []
    for index, round_spec in enumerate(interview_rounds, start=1):
        role = _role(round_spec.get("role"))
        form = None
        if round_spec.get("feedback_form"):
            form = FeedbackForm.objects.filter(
                pk=round_spec["feedback_form"], is_active=True
            ).first()
            if form is None:
                raise WorkflowError(
                    {"interview_rounds": f"Round {index}: no such feedback form."}
                )
        interview_stages.append(
            stage(
                order,
                round_spec.get("name") or f"Round {index} interview",
                StageKind.INTERVIEW,
                role,
                [Decision.PASS, Decision.RECOMMEND_REJECT],
                interview=True,
                form=form,
            )
        )
        order += 10

    recommend = None
    if recommendation_role:
        recommend = stage(
            order,
            "Department recommendation",
            StageKind.DEPARTMENT_DECISION,
            _role(recommendation_role),
            [Decision.RECOMMEND_SELECT, Decision.RECOMMEND_REJECT],
        )
        order += 10

    final = stage(
        order,
        "HR Head final decision",
        StageKind.HR_FINAL_DECISION,
        hr_head,
        [Decision.SELECT, Decision.REJECT],
        is_final_hr_decision=True,
    )
    offer = stage(order + 10, "Offer", StageKind.OFFER, hr_head, [])
    onboarding = stage(order + 20, "Onboarding", StageKind.ONBOARDING, hr_manager, [])
    hired = stage(
        order + 30, "Hired", StageKind.TERMINAL, None, [], is_terminal=True, is_won=True
    )
    rejected = stage(order + 40, "Rejected", StageKind.TERMINAL, None, [], is_terminal=True)

    def link(from_stage, decision, to_stage):
        StageTransition.objects.create(
            from_stage=from_stage, on_decision=decision, to_stage=to_stage
        )

    link(application, Decision.PASS, verification)
    link(verification, Decision.VERIFY, interview_stages[0])
    link(verification, Decision.REQUEST_INFO, verification)
    link(verification, Decision.SCREEN_OUT, rejected)

    for index, row in enumerate(interview_stages):
        following = (
            interview_stages[index + 1]
            if index + 1 < len(interview_stages)
            else (recommend or final)
        )
        link(row, Decision.PASS, following)
        # An interviewer's "no" is advisory: it routes to the decision stage,
        # never around it. Ending a candidacy stays HR Head's act alone.
        link(row, Decision.RECOMMEND_REJECT, final)

    if recommend is not None:
        link(recommend, Decision.RECOMMEND_SELECT, final)
        link(recommend, Decision.RECOMMEND_REJECT, final)

    link(final, Decision.SELECT, offer)
    link(final, Decision.REJECT, rejected)
    link(offer, Decision.OFFER_ACCEPTED, onboarding)
    link(offer, Decision.OFFER_DECLINED, rejected)
    link(onboarding, Decision.PASS, hired)
