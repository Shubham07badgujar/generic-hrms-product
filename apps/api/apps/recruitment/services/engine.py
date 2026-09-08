"""
THE WORKFLOW ENGINE.

One function drives every pipeline: `record_decision`. It reads the current
stage's configuration, checks the actor is entitled to act there, checks the
stage's preconditions are met, looks up the transition, and moves.

There is NO branch on job title anywhere in this module. Not one role code or
position name appears in this package — `test_no_job_title_branching_in_the_engine`
greps for them and fails the build if any turns up, in code OR in prose, so this
paragraph deliberately names none of them either. Two pipelines differ only in
the configuration rows their workflow owns.

AUTHORISATION IS TWO-LAYER, and both must pass:

  1. RBAC — does this principal hold the permission at all?
     (`core.access.require`, from the seeded permission matrix)
  2. Workflow — is this principal the responsible role for THIS stage, in the
     right department, and assigned to this interview?

Layer 1 alone would let any interviewer act at any stage of any application.
Layer 2 alone would ignore the permission matrix. A direct API call must clear
both.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    CandidateRejection,
    StageDecision,
)
from apps.workflows.models import (
    ADVISORY_DECISIONS,
    TERMINAL_DECISIONS,
    Decision,
    StageKind,
    StageTransition,
)
from apps.notifications import events as notify_events
from apps.recruitment.services import communications as comms
from core.access import Action, Resource, require
from core.access.catalog import RoleCode

#: Minimum characters for any mandatory justification.
MIN_REASON_LENGTH = 20


class WorkflowError(ValidationError):
    """An invalid workflow transition or an unmet precondition."""


class StageAuthorisationError(ValidationError):
    """The actor is not entitled to act at this stage."""


@dataclass
class TransitionResult:
    application: Application
    decision: str
    from_stage: object
    to_stage: object
    event: ApplicationEvent


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def actor_roles(user) -> set[str]:
    from core.access import get_context

    return set(get_context(user).role_codes)


def assert_actor_may_act_at_stage(*, actor, application: Application, stage) -> None:
    """
    Layer 2: workflow-level authorisation.

    Three checks, each closing a distinct bypass:
      * responsible role — a Clinic Doctor cannot act at the Senior Doctor stage
      * department — a medical interviewer cannot act on an operations vacancy
      * assignment — an interviewer must be the one scheduled for THIS interview
    """
    roles = actor_roles(actor)

    # Admin is not exempt from the workflow; it has its own explicit override
    # path. Letting Admin walk stages "because Admin" would make the hierarchy
    # advisory rather than enforced.
    if stage.responsible_role_id is None:
        raise StageAuthorisationError(
            {"stage": f"'{stage.name}' is an automatic stage and accepts no manual decision."}
        )

    required = stage.responsible_role.code
    if required not in roles:
        raise StageAuthorisationError(
            {
                "stage": (
                    f"'{stage.name}' may only be actioned by {stage.responsible_role.name}. "
                    f"Your roles: {sorted(roles) or 'none'}."
                )
            }
        )

    _assert_department_match(actor=actor, application=application, stage=stage)


def _assert_department_match(*, actor, application, stage) -> None:
    """
    Department interviewers act only on their own function's vacancies —
    unless the workflow explicitly assigned them from another function.

    HR roles are exempt: HR coordinates hiring across every department, which
    is why HR Head holds org-wide user creation in the permission matrix too.

    The cross-function exemption is deliberate and narrow. A workflow author
    may put a Senior Doctor round inside an operations hiring pipeline; the
    stage naming that role IS the explicit permission to cross departments,
    and it is visible configuration, not a bypass. The check still stands
    where it protects something: when the stage's role belongs to the SAME
    function as the vacancy, a holder from a different department remains
    refused — the workflow assigned the function's own people, not peers
    from elsewhere.
    """
    hr_roles = {RoleCode.HR_HEAD, RoleCode.HR_MANAGER, RoleCode.RECRUITER}
    if hr_roles & actor_roles(actor):
        return

    job_department = application.job_opening.department
    role_kind = (
        stage.responsible_role.department_kind if stage.responsible_role_id else ""
    )
    if role_kind and role_kind != job_department.kind:
        # The workflow assigned a role from another function to this stage —
        # e.g. a clinical assessor in an operations pipeline. The role check
        # above already proved the actor holds exactly that role.
        return

    employee = getattr(actor, "employee", None)
    if employee is None:
        # Fail closed: a department-scoped stage cannot be actioned by someone
        # with no department.
        raise StageAuthorisationError(
            {
                "stage": (
                    "Your account has no employee record, so your department cannot "
                    "be determined. Department stages require one."
                )
            }
        )

    if employee.department_id != job_department.pk:
        raise StageAuthorisationError(
            {
                "stage": (
                    f"This vacancy is in '{job_department.name}' and you are in "
                    f"'{employee.department.name}'."
                )
            }
        )


def _rbac_action_for(stage, decision: str) -> tuple[str, str]:
    """
    Map a stage decision onto the permission the matrix must grant.

    Dispatches on the DECISION first (its authority is what matters), then on
    the stage KIND. There is no catch-all fallback: an unmapped stage kind
    raises rather than guessing, because guessing here would silently check the
    wrong permission — either denying a legitimate actor or, worse, admitting
    one who should be refused.
    """
    if decision in TERMINAL_DECISIONS:
        return (
            Resource.APPLICATION,
            Action.REJECT if decision == Decision.REJECT else Action.APPROVE,
        )
    if decision in ADVISORY_DECISIONS:
        # At an INTERVIEW round, `recommend_reject` is the interviewer's own
        # negative verdict — the same authority as their `pass`, routed to HR
        # Head by the transition table. Demanding DEPARTMENT_DECISION/RECOMMEND
        # there refused the very interviewer the workflow put in the chair,
        # since no interviewer role holds it. At a department-decision stage
        # the advisory IS the department's recommendation and keeps its own
        # permission.
        if stage.kind == StageKind.INTERVIEW:
            return Resource.INTERVIEW_FEEDBACK, Action.CREATE
        return Resource.DEPARTMENT_DECISION, Action.RECOMMEND
    if decision == Decision.SCREEN_OUT:
        # The screening stage's own act, by the screening stage's own role.
        return Resource.APPLICATION, Action.EDIT

    by_kind = {
        StageKind.APPLICATION: (Resource.APPLICATION, Action.EDIT),
        StageKind.HR_VERIFICATION: (Resource.APPLICATION, Action.EDIT),
        StageKind.INTERVIEW: (Resource.INTERVIEW_FEEDBACK, Action.CREATE),
        StageKind.DEPARTMENT_DECISION: (Resource.DEPARTMENT_DECISION, Action.RECOMMEND),
        StageKind.HR_FINAL_DECISION: (Resource.APPLICATION, Action.APPROVE),
        StageKind.OFFER: (Resource.OFFER, Action.EDIT),
        StageKind.ONBOARDING: (Resource.ONBOARDING, Action.EDIT),
    }
    mapped = by_kind.get(stage.kind)
    if mapped is None:
        raise WorkflowError(
            {
                "stage": (
                    f"Stage kind '{stage.kind}' has no permission mapping, so no "
                    f"decision can be authorised against it."
                )
            }
        )
    return mapped


# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------


def assert_stage_preconditions(*, application: Application, stage, decision: str) -> None:
    """Everything the stage configuration says must exist before a decision."""
    if decision not in stage.allowed_decisions:
        raise WorkflowError(
            {
                "decision": (
                    f"'{decision}' is not permitted at '{stage.name}'. "
                    f"Allowed: {sorted(stage.allowed_decisions)}."
                )
            }
        )

    if stage.requires_interview:
        # Cancelled rounds do not count: a rebooked interview must be held
        # (and judged) again before anyone moves.
        interview = (
            application.interviews.filter(stage=stage, is_active=True)
            .exclude(status="cancelled")
            .order_by("-scheduled_at")
            .first()
        )
        if interview is None:
            raise WorkflowError(
                {"decision": f"'{stage.name}' requires an interview before a decision."}
            )
        # STRUCTURAL, not configuration: the verdict on an interview round IS
        # the interviewer's submitted feedback, so every decision that moves
        # the candidate demands it — whatever the stage's authored flags say.
        # (The live workflows were authored with requires_feedback off, which
        # let a candidate pass a round nobody had assessed.)
        moving = {
            Decision.PASS, Decision.SELECT,
            Decision.RECOMMEND_SELECT, Decision.RECOMMEND_REJECT,
        }
        if decision in moving and not hasattr(interview, "feedback"):
            raise WorkflowError(
                {
                    "decision": (
                        f"'{stage.name}' requires the interviewer's submitted "
                        f"feedback before a decision can be recorded."
                    )
                }
            )

    # Verification gate: department stages are unreachable until HR verifies.
    # The transition table already routes this way; this is the second lock, so
    # a direct call at a later stage cannot skip it.
    if stage.kind in (StageKind.INTERVIEW, StageKind.DEPARTMENT_DECISION):
        if not application.is_verified:
            raise WorkflowError(
                {
                    "decision": (
                        "This candidate has not completed HR verification, so the "
                        "department stages are not yet open."
                    )
                }
            )


def resolve_transition(stage, decision: str):
    """The configured destination for `(stage, decision)`."""
    transition = StageTransition.objects.filter(
        from_stage=stage, on_decision=decision, is_active=True
    ).select_related("to_stage").first()
    if transition is None:
        raise WorkflowError(
            {
                "decision": (
                    f"'{stage.name}' has no configured transition for '{decision}'. "
                    f"This is a workflow configuration gap."
                )
            }
        )
    return transition.to_stage


# ---------------------------------------------------------------------------
# Reopening a closed application
# ---------------------------------------------------------------------------

#: Statuses whose journey has ended. An application in one of these belongs at
#: a terminal stage; anything else belongs at a stage that can still be acted
#: on. Keeping status and stage in agreement is the invariant this section
#: exists to hold — a reopened application parked at a terminal stage is
#: "active" but unworkable, which is worse than either state alone.
CLOSED_STATUSES = frozenset(
    {
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.HIRED,
        ApplicationStatus.OFFER_DECLINED,
    }
)


def stage_is_actionable(stage) -> bool:
    """
    Can a human record a decision here and go somewhere as a result?

    All three conditions matter. A stage with decisions but no transition is a
    dead end; a stage with transitions but no decisions cannot be triggered;
    a terminal stage is the end of the road by definition.
    """
    if stage is None or stage.is_terminal or not stage.is_active:
        return False
    if not stage.allowed_decisions:
        return False
    return StageTransition.objects.filter(
        from_stage=stage, on_decision__in=list(stage.allowed_decisions), is_active=True
    ).exists()


def resolve_reopen_stage(application: Application):
    """
    Where a closed application goes when it is reopened.

    DERIVED, NEVER HARD-CODED. Nothing here names a stage, a kind or a job
    title; every candidate comes from what the engine itself recorded or from
    the transition table, so a workflow with entirely different stages reopens
    correctly with no change to this function.

    Candidates are tried in order of how well they answer "where was this
    application when a human closed it?", and the first one that is genuinely
    actionable wins:

      1. The stage named on the rejection record. This is the strongest
         answer — the engine wrote it at the moment of the decision.
      2. The most recent stage a decision was recorded at, for closures that
         leave no rejection (withdrawal, a declined offer).
      3. The transition table walked backwards: stages configured to lead INTO
         the terminal stage the application is sitting at. Highest order wins,
         being the furthest the candidate got.

    Raises `WorkflowError` when no candidate is actionable, rather than
    guessing. A workflow whose terminal stage is unreachable from anywhere is
    misconfigured, and inventing a destination would hide that.
    """
    workflow_id = application.job_opening.workflow_id
    seen: set = set()
    candidates: list = []

    def consider(stage) -> None:
        if stage is None or stage.pk in seen:
            return
        seen.add(stage.pk)
        # A stage from another workflow would move the candidate into a
        # pipeline they never entered.
        if stage.workflow_id != workflow_id:
            return
        candidates.append(stage)

    rejection = getattr(application, "rejection", None)
    if rejection is not None:
        consider(rejection.rejection_stage)

    last_decision = (
        StageDecision.objects.filter(application=application)
        .select_related("stage")
        .order_by("-decided_at")
        .first()
    )
    if last_decision is not None:
        consider(last_decision.stage)

    if application.current_stage_id:
        incoming = (
            StageTransition.objects.filter(
                to_stage_id=application.current_stage_id, is_active=True
            )
            .select_related("from_stage")
            .order_by("-from_stage__order")
        )
        for transition in incoming:
            consider(transition.from_stage)

    for stage in candidates:
        if stage_is_actionable(stage):
            return stage

    raise WorkflowError(
        {
            "workflow": (
                f"'{application.job_opening.workflow.name}' offers no actionable stage to "
                f"reopen this application into. Every candidate stage was terminal, "
                f"had no allowed decisions, or had no outgoing transition. This is a "
                f"workflow configuration gap and must be fixed in the workflow, not "
                f"worked around here."
            )
        }
    )


def resolve_terminal_stage(application: Application, *, won: bool):
    """The workflow's own terminal stage for a won or lost outcome."""
    stage = (
        application.job_opening.workflow.stages.filter(
            is_terminal=True, is_won=won, is_active=True
        )
        .order_by("order")
        .first()
    )
    if stage is None:
        raise WorkflowError(
            {
                "workflow": (
                    f"'{application.job_opening.workflow.name}' has no "
                    f"{'won' if won else 'lost'} terminal stage configured."
                )
            }
        )
    return stage


def align_stage_with_status(application: Application, *, new_status: str):
    """
    Bring `current_stage` back into agreement with `status`.

    Returns the stage the application should sit at. The caller saves; this
    only decides, so the decision is testable on its own and the same rule
    serves both directions:

      closed status at a live stage  → the workflow's terminal stage
      live status at a terminal stage → a reopened, actionable stage

    Any other combination is already coherent and is left alone.
    """
    current = application.current_stage
    closing = new_status in CLOSED_STATUSES

    if closing and not current.is_terminal:
        return resolve_terminal_stage(application, won=new_status == ApplicationStatus.HIRED)
    if not closing and current.is_terminal:
        return resolve_reopen_stage(application)
    return current


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@transaction.atomic
def record_decision(
    *,
    application: Application,
    actor,
    decision: str,
    rationale: str = "",
    interview=None,
) -> TransitionResult:
    """
    Record a decision at the application's current stage and advance it.

    The single entry point for every stage movement in every workflow.
    """
    application = Application.objects.select_for_update().select_related(
        "current_stage", "job_opening__department", "candidate"
    ).get(pk=application.pk)

    if not application.is_open:
        raise WorkflowError(
            {
                "application": (
                    f"This application is {application.get_status_display().lower()} "
                    f"and can no longer be actioned."
                )
            }
        )

    stage = application.current_stage

    # Layer 1 — RBAC.
    resource, action = _rbac_action_for(stage, decision)
    require(actor, resource, action)

    # Layer 2 — workflow.
    assert_actor_may_act_at_stage(actor=actor, application=application, stage=stage)
    assert_stage_preconditions(application=application, stage=stage, decision=decision)

    # Terminal decisions are HR Head's alone, structurally.
    if decision in TERMINAL_DECISIONS and not stage.is_final_hr_decision:
        raise WorkflowError(
            {"decision": f"'{stage.name}' does not carry final decision authority."}
        )

    if decision == Decision.REJECT:
        return _reject(application=application, actor=actor, stage=stage, reason=rationale)
    if decision == Decision.SCREEN_OUT:
        return _screen_out(application=application, actor=actor, stage=stage, reason=rationale)

    next_stage = resolve_transition(stage, decision)

    StageDecision.objects.create(
        application=application,
        stage=stage,
        decision=decision,
        decided_by=actor,
        rationale=rationale,
        interview=interview,
    )

    if stage.kind == StageKind.HR_VERIFICATION and decision == Decision.VERIFY:
        application.is_verified = True
        application.verified_by = actor
        application.verified_at = timezone.now()

    application.current_stage = next_stage
    if decision == Decision.SELECT:
        application.status = ApplicationStatus.SELECTED
    application.save()

    event = _event(
        application,
        kind=_event_kind_for(stage, decision),
        actor=actor,
        from_stage=stage,
        to_stage=next_stage,
        decision=decision,
        note=rationale,
    )

    # A department recommendation is a HANDOVER: only HR can make it terminal,
    # so the people who can act are told. Without this the recommendation sits
    # in a queue nobody was told about.
    if decision in ADVISORY_DECISIONS:
        notify_events.department_decision_recorded_for(
            application=application, decision=decision, rationale=rationale, actor=actor
        )
    elif decision == Decision.SELECT:
        notify_events.candidate_decided(application, selected=True, actor=actor)

    # The candidate's own email, decided by the SAME (stage, decision) the
    # transition table just acted on, keyed on the event row so a replayed
    # request cannot send it twice and a genuine later transition can.
    _tell_candidate(application, stage=stage, decision=decision, event=event, actor=actor)

    # Arriving AT an interview stage does NOT contact the candidate. The
    # slot-selection link goes out only when HR configures this round's
    # available times (`slots.configure_slot_invite`) — for the first round
    # and every later one alike.

    return TransitionResult(
        application=application,
        decision=decision,
        from_stage=stage,
        to_stage=next_stage,
        event=event,
    )


def _reject(*, application, actor, stage, reason: str) -> TransitionResult:
    """
    Final rejection. HR Head only, reason mandatory.

    A snapshot of the whole history is frozen onto the rejection record so the
    justification survives any later edit to the feedback it rested on.
    """
    reason = _require_reason(reason, what="rejection")

    terminal = resolve_transition(stage, Decision.REJECT)
    last_recommendation = (
        StageDecision.objects.filter(
            application=application, decision__in=list(ADVISORY_DECISIONS)
        )
        .order_by("-decided_at")
        .first()
    )

    StageDecision.objects.create(
        application=application,
        stage=stage,
        decision=Decision.REJECT,
        decided_by=actor,
        rationale=reason,
    )

    CandidateRejection.objects.create(
        application=application,
        candidate=application.candidate,
        rejected_by=actor,
        rejection_stage=stage,
        reason=reason.strip(),
        department_recommendation=last_recommendation,
        history_snapshot=build_history_snapshot(application),
    )

    application.current_stage = terminal
    application.status = ApplicationStatus.REJECTED
    application.save()

    _stamp_final_decision(application.candidate)

    event = _event(
        application,
        kind=ApplicationEvent.Kind.REJECTED,
        actor=actor,
        from_stage=stage,
        to_stage=terminal,
        decision=Decision.REJECT,
        note=reason.strip(),
    )

    notify_events.candidate_decided(application, selected=False, actor=actor)
    _tell_candidate(application, stage=stage, decision=Decision.REJECT, event=event, actor=actor)

    return TransitionResult(
        application=application,
        decision=Decision.REJECT,
        from_stage=stage,
        to_stage=terminal,
        event=event,
    )


def _tell_candidate(application, *, stage, decision, event, actor) -> None:
    kind = comms.kind_for_decision(stage, decision)
    if decision == Decision.REJECT and not application.is_verified:
        # Refused before HR ever verified them: to the candidate that is an
        # HR-screening outcome, whichever stage the workflow lets HR Head
        # record it at.
        kind = comms.Kind.HR_VERIFICATION_REJECTED
    if kind is None:
        return
    comms.notify_candidate(
        application=application,
        kind=kind,
        dedupe_key=f"app:{application.pk}:event:{event.pk}",
        extra_context={"interview_round": stage.name},
        actor=actor,
    )


#: A rejection reason is read by the candidate's future self and by an
#: auditor; it should be a sentence, not a word, and a paragraph, not a page.
MAX_REASON_WORDS = 250


def _require_reason(reason: str, *, what: str) -> str:
    text = (reason or "").strip()
    if len(text) < MIN_REASON_LENGTH:
        raise WorkflowError(
            {"reason": f"A {what} reason of at least {MIN_REASON_LENGTH} characters is "
                       f"required and is recorded permanently."}
        )
    words = len(text.split())
    if words > MAX_REASON_WORDS:
        raise WorkflowError(
            {"reason": f"The reason is {words} words; the limit is {MAX_REASON_WORDS}."}
        )
    return text


def _screen_out(*, application, actor, stage, reason: str) -> TransitionResult:
    """
    Screening rejection by the stage's own role — the Recruiter at
    verification. Same record as a final rejection (CandidateRejection, status
    REJECTED, retention clock, rejection email), reached without the HR
    Head's terminal authority because it IS a different act: "this
    application does not qualify" rather than "this candidate is not
    selected". The transition must be configured, like every other.
    """
    reason = _require_reason(reason, what="rejection")
    terminal = resolve_transition(stage, Decision.SCREEN_OUT)

    StageDecision.objects.create(
        application=application, stage=stage, decision=Decision.SCREEN_OUT,
        decided_by=actor, rationale=reason,
    )
    CandidateRejection.objects.create(
        application=application,
        candidate=application.candidate,
        rejected_by=actor,
        rejection_stage=stage,
        reason=reason,
        history_snapshot=build_history_snapshot(application),
    )
    application.current_stage = terminal
    application.status = ApplicationStatus.REJECTED
    application.save()
    _stamp_final_decision(application.candidate)

    event = _event(
        application, kind=ApplicationEvent.Kind.REJECTED, actor=actor,
        from_stage=stage, to_stage=terminal, decision=Decision.SCREEN_OUT, note=reason,
    )
    notify_events.candidate_decided(application, selected=False, actor=actor)
    _tell_candidate(application, stage=stage, decision=Decision.SCREEN_OUT, event=event, actor=actor)
    return TransitionResult(
        application=application, decision=Decision.SCREEN_OUT,
        from_stage=stage, to_stage=terminal, event=event,
    )


def _stamp_final_decision(candidate) -> None:
    """Start the DPDP retention clock at the final decision."""
    from datetime import timedelta

    from django.conf import settings

    months = getattr(settings, "CANDIDATE_RETENTION_MONTHS", 12)
    now = timezone.now()
    candidate.final_decision_at = now
    candidate.retention_until = (now + timedelta(days=30 * months)).date()
    candidate.save(update_fields=["final_decision_at", "retention_until", "updated_at"])


def _clear_final_decision(candidate) -> None:
    """
    Stop the DPDP retention clock when a candidate returns to an open pipeline.

    Their personal data is being processed for a live application again, so the
    countdown that would eventually anonymise them must not keep running —
    otherwise a reopened candidate can become purge-eligible mid-process.
    """
    candidate.final_decision_at = None
    candidate.retention_until = None
    candidate.save(update_fields=["final_decision_at", "retention_until", "updated_at"])


def _event_kind_for(stage, decision: str) -> str:
    if stage.kind == StageKind.HR_VERIFICATION:
        return (
            ApplicationEvent.Kind.VERIFIED
            if decision == Decision.VERIFY
            else ApplicationEvent.Kind.INFO_REQUESTED
        )
    if decision in ADVISORY_DECISIONS:
        return ApplicationEvent.Kind.RECOMMENDATION
    if decision in TERMINAL_DECISIONS:
        return ApplicationEvent.Kind.HR_DECISION
    return ApplicationEvent.Kind.STAGE_CHANGED


def _event(application, *, kind, actor, from_stage=None, to_stage=None, decision="", note="", detail=None):
    return ApplicationEvent.objects.create(
        application=application,
        kind=kind,
        actor=actor,
        actor_label=getattr(actor, "email", "") or "system",
        from_stage=from_stage,
        to_stage=to_stage,
        decision=decision,
        note=note,
        detail=detail or {},
    )


def build_history_snapshot(application: Application) -> dict:
    """
    The complete candidate journey, as data.

    Used to freeze a rejection's justification, and served by the history
    endpoint so HR Head sees everything before deciding.
    """
    return {
        "candidate": application.candidate.full_name,
        "job": application.job_opening.title,
        "verified": application.is_verified,
        "decisions": [
            {
                "stage": d.stage.name,
                "decision": d.decision,
                "by": getattr(d.decided_by, "email", ""),
                "at": d.decided_at.isoformat(),
                "rationale": d.rationale,
            }
            for d in application.stage_decisions.select_related("stage", "decided_by").order_by(
                "decided_at"
            )
        ],
        "interviews": [
            {
                "stage": i.stage.name,
                "interviewer": i.interviewer.full_name,
                "scheduled_at": i.scheduled_at.isoformat(),
                "status": i.status,
                "recommendation": getattr(getattr(i, "feedback", None), "recommendation", None),
                "rating": getattr(getattr(i, "feedback", None), "overall_rating", None),
                "strengths": getattr(getattr(i, "feedback", None), "strengths", ""),
                "concerns": getattr(getattr(i, "feedback", None), "concerns", ""),
            }
            for i in application.interviews.select_related("stage", "interviewer").order_by(
                "scheduled_at"
            )
        ],
    }
