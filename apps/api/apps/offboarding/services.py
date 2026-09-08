"""
The exit workflow.

TWO RULES SHAPE EVERY FUNCTION HERE.

1. EMPLOYMENT STATUS IS NOT THIS MODULE'S TO WRITE. Every status change goes
   through `employees.services.lifecycle.change_status`, which owns the
   transition table, the mandatory reason and the asset gate. Offboarding
   decides WHEN a transition is warranted; the lifecycle decides whether it is
   permitted. Duplicating that logic here is the one thing this phase was told
   not to do.

2. THE APPROVAL GATE IS SERVER-SIDE AND EXHAUSTIVE. `exit_blockers()` is the
   single place that answers "may this person leave?", and both the approval
   and the final transition consult it. The UI renders the same list, but it is
   reporting the server's answer rather than computing its own.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee, EmployeeStatus
from apps.notifications import events as notify_events
from core.access import Action, Resource, require
from core.access.catalog import Scope
from core.access.engine import AccessDenied

from .models import (
    CLOSED_STAGES,
    ClearanceCategory,
    ClearanceOwner,
    ClearanceStatus,
    ClearanceTemplate,
    ExitClearanceItem,
    ExitInterview,
    ExitStage,
    ExitType,
    ExitWorkflow,
    FinalSettlement,
    ResignationRequest,
    ResignationStatus,
    SettlementStatus,
)

MIN_REASON_LENGTH = 10
MIN_EXCEPTION_REASON_LENGTH = 20


class OffboardingError(ValidationError):
    """A refused offboarding operation."""


# ---------------------------------------------------------------------------
# Resignation
# ---------------------------------------------------------------------------


@transaction.atomic
def submit_resignation(
    *,
    employee: Employee,
    actor,
    requested_last_working_date,
    reason: str,
    comments: str = "",
    resignation_date=None,
) -> ResignationRequest:
    """
    An employee resigns.

    Creates a REQUEST and nothing else. The employee's status is untouched —
    they are still employed until HR approves, which is both legally accurate
    and the reason an employee cannot set their own status to RESIGNED.
    """
    require(actor, Resource.OFFBOARDING, Action.CREATE)

    # Someone submitting on another person's behalf needs reach beyond
    # themselves; self-service covers only your own resignation.
    actor_employee = getattr(actor, "employee", None)
    if actor_employee is None or actor_employee.pk != employee.pk:
        scope = require(actor, Resource.OFFBOARDING, Action.CREATE)
        if scope <= Scope.SELF:
            raise AccessDenied(
                Resource.OFFBOARDING,
                Action.CREATE,
                "Submitting a resignation for someone else requires authority over "
                "other people's records.",
            )

    if employee.status == EmployeeStatus.EXITED:
        raise OffboardingError({"employee": "This employee has already left."})

    if ResignationRequest.objects.filter(
        employee=employee, status=ResignationStatus.SUBMITTED
    ).exists():
        raise OffboardingError(
            {"employee": "A resignation is already submitted and awaiting review."}
        )

    today = resignation_date or timezone.localdate()
    if requested_last_working_date < today:
        raise OffboardingError(
            {"requested_last_working_date": "The last working day cannot be in the past."}
        )

    request = ResignationRequest.objects.create(
        employee=employee,
        resignation_date=today,
        requested_last_working_date=requested_last_working_date,
        reason=reason,
        employee_comments=comments,
        submitted_by=actor,
        status=ResignationStatus.SUBMITTED,
    )

    _audit(
        request,
        actor=actor,
        entity_type="offboarding.ResignationRequest",
        action_verb="create",
        after={
            "event": "resignation_submitted",
            "employee": employee.employee_code,
            "requested_last_working_date": str(requested_last_working_date),
            "reason": reason,
        },
    )

    # HR and the reporting manager need to know; the employee's own status is
    # deliberately unchanged until HR approves, so this notification IS the
    # handover.
    notify_events.resignation_submitted(request)
    return request


@transaction.atomic
def approve_resignation(
    *,
    request: ResignationRequest,
    actor,
    approved_last_working_date=None,
    notes: str = "",
    notice_days: int | None = None,
) -> ExitWorkflow:
    """
    HR accepts a resignation.

    This is where employment status finally moves — through the lifecycle
    service, so the transition table applies — and where the exit workflow and
    its clearance checklist come into being.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not request.is_open:
        raise OffboardingError(
            {"resignation": f"This resignation is already {request.get_status_display().lower()}."}
        )

    last_working = approved_last_working_date or request.requested_last_working_date

    request.status = ResignationStatus.APPROVED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.review_notes = notes
    request.approved_last_working_date = last_working
    request.save()

    workflow = start_exit(
        employee=request.employee,
        actor=actor,
        exit_type=ExitType.RESIGNATION,
        last_working_date=last_working,
        reason=f"Resignation accepted. {notes}".strip(),
        resignation=request,
        notice_days=notice_days,
    )

    _audit(
        request,
        actor=actor,
        entity_type="offboarding.ResignationRequest",
        action_verb="approve",
        after={
            "event": "resignation_approved",
            "employee": request.employee.employee_code,
            "approved_last_working_date": str(last_working),
            "notes": notes,
        },
    )
    return workflow


@transaction.atomic
def reject_resignation(*, request: ResignationRequest, actor, reason: str) -> ResignationRequest:
    """Refuse a resignation. The reason is mandatory and permanently recorded."""
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not request.is_open:
        raise OffboardingError({"resignation": "This resignation has already been reviewed."})
    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise OffboardingError(
            {
                "reason": (
                    f"Rejecting a resignation requires a reason of at least "
                    f"{MIN_REASON_LENGTH} characters; the employee is entitled to know why."
                )
            }
        )

    request.status = ResignationStatus.REJECTED
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.review_notes = reason.strip()
    request.save()

    _audit(
        request,
        actor=actor,
        entity_type="offboarding.ResignationRequest",
        action_verb="reject",
        after={
            "event": "resignation_rejected",
            "employee": request.employee.employee_code,
            "reason": reason.strip(),
        },
    )
    return request


@transaction.atomic
def withdraw_resignation(*, request: ResignationRequest, actor) -> ResignationRequest:
    """The employee changes their mind before HR has acted."""
    require(actor, Resource.OFFBOARDING, Action.CREATE)

    actor_employee = getattr(actor, "employee", None)
    is_own = actor_employee is not None and actor_employee.pk == request.employee_id
    if not is_own:
        require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not request.is_open:
        raise OffboardingError(
            {"resignation": "This resignation has already been reviewed and cannot be withdrawn."}
        )

    request.status = ResignationStatus.WITHDRAWN
    request.reviewed_at = timezone.now()
    request.save()

    _audit(
        request,
        actor=actor,
        entity_type="offboarding.ResignationRequest",
        action_verb="update",
        after={"event": "resignation_withdrawn", "employee": request.employee.employee_code},
    )
    return request


# ---------------------------------------------------------------------------
# Starting an exit
# ---------------------------------------------------------------------------


def resolve_clearance_template(employee: Employee, exit_type: str) -> ClearanceTemplate | None:
    """Most specific fit first, then the organisation default."""
    candidates = ClearanceTemplate.objects.filter(is_active=True)

    both = candidates.filter(department_id=employee.department_id, exit_type=exit_type).first()
    if both:
        return both
    by_department = candidates.filter(department_id=employee.department_id, exit_type="").first()
    if by_department:
        return by_department
    by_type = candidates.filter(department__isnull=True, exit_type=exit_type).first()
    if by_type:
        return by_type
    return candidates.filter(is_default=True).first()


def _resolve_assignee(owner: str, employee: Employee) -> Employee | None:
    """Turn an owner bucket into the person actually responsible, where one exists."""
    if owner == ClearanceOwner.EMPLOYEE:
        return employee
    if owner == ClearanceOwner.MANAGER:
        return employee.reporting_manager
    if owner == ClearanceOwner.DEPARTMENT:
        return getattr(employee.department, "head_employee", None)
    return None  # HR, IT and FINANCE are queues, not individuals


@transaction.atomic
def start_exit(
    *,
    employee: Employee,
    actor,
    exit_type: str,
    last_working_date,
    reason: str = "",
    resignation: ResignationRequest | None = None,
    notice_days: int | None = None,
    template: ClearanceTemplate | None = None,
) -> ExitWorkflow:
    """
    Open an exit and issue its clearance checklist.

    Moves employment status to RESIGNED or TERMINATED through the lifecycle
    service — never by assignment here. A termination started this way is the
    same code path as a resignation approval, which is why the two lifecycles
    in the spec share one implementation.
    """
    require(actor, Resource.OFFBOARDING, Action.CREATE)

    if ExitWorkflow.objects.filter(employee=employee).exists():
        raise OffboardingError({"employee": "An exit is already under way for this employee."})
    if employee.status == EmployeeStatus.EXITED:
        raise OffboardingError({"employee": "This employee has already left."})

    from apps.employees.services.lifecycle import change_status

    target = (
        EmployeeStatus.RESIGNED
        if exit_type == ExitType.RESIGNATION
        else EmployeeStatus.TERMINATED
    )
    change_status(
        employee=employee,
        actor=actor,
        new_status=target,
        reason=reason or f"Exit initiated ({exit_type}).",
    )
    employee.refresh_from_db()

    days = notice_days if notice_days is not None else employee.notice_period_days
    workflow = ExitWorkflow.objects.create(
        employee=employee,
        exit_type=exit_type,
        stage=ExitStage.NOTICE_PERIOD,
        resignation=resignation,
        initiated_by=actor,
        reason=reason,
        notice_start_date=timezone.localdate(),
        notice_days=days,
        expected_last_working_date=last_working_date,
    )

    _issue_clearance(workflow, actor=actor, template=template)
    FinalSettlement.objects.create(
        exit_workflow=workflow, final_working_date=last_working_date, prepared_by=None
    )

    # The company account is flagged for deprovisioning now, not on the last
    # day: IT needs the lead time, and the record makes the task visible.
    _flag_account_for_deprovisioning(workflow, actor=actor)

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="create",
        after={
            "event": "exit_initiated",
            "employee": employee.employee_code,
            "exit_type": exit_type,
            "expected_last_working_date": str(last_working_date),
            "notice_days": days,
        },
    )
    return workflow


def _issue_clearance(
    workflow: ExitWorkflow, *, actor, template: ClearanceTemplate | None = None
) -> None:
    chosen = template or resolve_clearance_template(workflow.employee, workflow.exit_type)
    if chosen is None:
        return

    for source in chosen.items.filter(is_active=True).order_by("order", "title"):
        item = ExitClearanceItem.objects.create(
            exit_workflow=workflow,
            source_item=source,
            title=source.title,
            description=source.description,
            category=source.category,
            owner=source.owner,
            assigned_to=_resolve_assignee(source.owner, workflow.employee),
            is_required=source.is_required,
            requires_evidence=source.requires_evidence,
            due_date=workflow.expected_last_working_date
            + dt.timedelta(days=source.due_offset_days),
            order=source.order,
        )
        # The assignee is the exit's blocker and usually does not know it. An
        # unassigned item has nobody to tell — it surfaces on the exit page
        # instead, and `notify()` no-ops on a None recipient.
        if item.assigned_to_id:
            notify_events.clearance_task_assigned(item)


def _flag_account_for_deprovisioning(workflow: ExitWorkflow, *, actor) -> None:
    from apps.itaccounts.models import AccountStatus, CompanyEmailAccount

    account = CompanyEmailAccount.objects.filter(employee=workflow.employee).first()
    if account is None or account.status in {
        AccountStatus.DEPROVISIONED,
        AccountStatus.SUSPENDED,
    }:
        return
    account.status = AccountStatus.SUSPENDED
    account.suspended_at = timezone.now()
    account.notes = "Pending deprovisioning: employee is exiting."
    account.save(update_fields=["status", "suspended_at", "notes", "updated_at"])


# ---------------------------------------------------------------------------
# Notice period
# ---------------------------------------------------------------------------


@transaction.atomic
def waive_notice(*, workflow: ExitWorkflow, actor, reason: str, new_last_working_date=None):
    """
    Release the employee from serving notice.

    An exception, so it takes APPROVE rather than EDIT and demands a written
    reason. Waiving notice usually has a financial consequence, and the
    settlement is where that surfaces.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not workflow.is_open:
        raise OffboardingError({"exit": "This exit is closed."})
    if len(reason.strip()) < MIN_EXCEPTION_REASON_LENGTH:
        raise OffboardingError(
            {
                "reason": (
                    f"Waiving notice is an exception and requires a reason of at least "
                    f"{MIN_EXCEPTION_REASON_LENGTH} characters."
                )
            }
        )

    workflow.notice_waived = True
    workflow.notice_waived_by = actor
    workflow.notice_waived_at = timezone.now()
    workflow.notice_waiver_reason = reason.strip()
    if new_last_working_date:
        workflow.expected_last_working_date = new_last_working_date
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="override",
        after={
            "event": "notice_waived",
            "employee": workflow.employee.employee_code,
            "reason": reason.strip(),
            "expected_last_working_date": str(workflow.expected_last_working_date),
        },
    )
    return workflow


@transaction.atomic
def approve_early_release(
    *, workflow: ExitWorkflow, actor, new_last_working_date, reason: str
):
    """Let someone leave before their notice runs out."""
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not workflow.is_open:
        raise OffboardingError({"exit": "This exit is closed."})
    if len(reason.strip()) < MIN_EXCEPTION_REASON_LENGTH:
        raise OffboardingError(
            {
                "reason": (
                    f"An early release is an exception and requires a reason of at least "
                    f"{MIN_EXCEPTION_REASON_LENGTH} characters."
                )
            }
        )
    if new_last_working_date >= workflow.expected_last_working_date:
        raise OffboardingError(
            {
                "new_last_working_date": (
                    "An early release must bring the last working day forward. Use the "
                    "notice extension to move it later."
                )
            }
        )

    previous = workflow.expected_last_working_date
    workflow.early_release_approved = True
    workflow.early_release_by = actor
    workflow.early_release_reason = reason.strip()
    workflow.expected_last_working_date = new_last_working_date
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="override",
        before={"expected_last_working_date": str(previous)},
        after={
            "event": "early_release_approved",
            "employee": workflow.employee.employee_code,
            "reason": reason.strip(),
            "expected_last_working_date": str(new_last_working_date),
        },
    )
    return workflow


@transaction.atomic
def update_notice(*, workflow: ExitWorkflow, actor, last_working_date=None, notice_days=None):
    """Ordinary adjustment — extending notice, or correcting a date."""
    require(actor, Resource.OFFBOARDING, Action.EDIT)

    if not workflow.is_open:
        raise OffboardingError({"exit": "This exit is closed."})

    before = {
        "expected_last_working_date": str(workflow.expected_last_working_date),
        "notice_days": workflow.notice_days,
    }
    if last_working_date:
        if last_working_date < workflow.expected_last_working_date:
            # Bringing the date forward is an early release, which is an
            # exception with its own authority and reason.
            raise OffboardingError(
                {
                    "last_working_date": (
                        "Bringing the last working day forward is an early release, which "
                        "requires approval and a recorded reason."
                    )
                }
            )
        workflow.expected_last_working_date = last_working_date
    if notice_days is not None:
        workflow.notice_days = notice_days
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="update",
        before=before,
        after={
            "event": "notice_updated",
            "expected_last_working_date": str(workflow.expected_last_working_date),
            "notice_days": workflow.notice_days,
        },
    )
    return workflow


# ---------------------------------------------------------------------------
# Clearance
# ---------------------------------------------------------------------------

#: Which role codes satisfy each owner bucket. The DEPARTMENT and MANAGER
#: buckets are resolved per-employee instead, since they name a person.
OWNER_ROLES: dict[str, frozenset[str]] = {
    ClearanceOwner.HR: frozenset({"hr_head", "hr_manager", "admin"}),
    ClearanceOwner.IT: frozenset({"admin", "hr_head"}),
    ClearanceOwner.FINANCE: frozenset({"finance_head", "accounts_manager", "admin"}),
}


def assert_may_action_item(*, item: ExitClearanceItem, actor) -> None:
    """
    Layer 2 of the clearance check.

    RBAC says whether the principal may touch offboarding at all; this says
    whether they own THIS line. Without it a finance officer could sign off
    HR's exit interview, because both hold OFFBOARDING/EDIT — the same reason
    the recruitment engine checks the stage's responsible role as well as the
    permission matrix.
    """
    roles = set(
        actor.user_roles.filter(is_active=True, role__is_active=True).values_list(
            "role__code", flat=True
        )
    )
    if "admin" in roles:
        return

    expected = OWNER_ROLES.get(item.owner)
    if expected is not None:
        if roles & expected:
            return
        raise AccessDenied(
            Resource.OFFBOARDING,
            Action.EDIT,
            f"'{item.title}' is owned by {item.get_owner_display()}, and your role does "
            f"not carry that responsibility.",
        )

    # DEPARTMENT / MANAGER / EMPLOYEE name a person.
    actor_employee = getattr(actor, "employee", None)
    if item.assigned_to_id and actor_employee and actor_employee.pk == item.assigned_to_id:
        return
    # HR may always act as a backstop; somebody has to be able to close an exit
    # when the named person has themselves left.
    if roles & {"hr_head", "hr_manager"}:
        return

    raise AccessDenied(
        Resource.OFFBOARDING,
        Action.EDIT,
        f"'{item.title}' is assigned to "
        f"{item.assigned_to.full_name if item.assigned_to else item.get_owner_display()}.",
    )


@transaction.atomic
def complete_clearance_item(
    *, item: ExitClearanceItem, actor, notes: str = "", evidence=None
) -> ExitClearanceItem:
    """Sign off one clearance line."""
    require(actor, Resource.OFFBOARDING, Action.EDIT)
    assert_may_action_item(item=item, actor=actor)

    if item.is_done:
        raise OffboardingError({"item": f"'{item.title}' is already {item.status}."})
    if not item.exit_workflow.is_open:
        raise OffboardingError({"exit": "This exit is closed and its clearance cannot change."})
    if item.requires_evidence and evidence is None and not item.evidence:
        raise OffboardingError(
            {"evidence": f"'{item.title}' must attach evidence before it can be completed."}
        )

    if evidence is not None:
        item.evidence = evidence
    item.status = ClearanceStatus.COMPLETED
    item.completed_at = timezone.now()
    item.completed_by = actor
    if notes:
        item.notes = notes
    item.save()

    _audit(
        item,
        actor=actor,
        entity_type="offboarding.ExitClearanceItem",
        action_verb="update",
        after={
            "event": "clearance_completed",
            "employee": item.exit_workflow.employee.employee_code,
            "item": item.title,
            "category": item.category,
        },
    )
    _advance_to_clearance(item.exit_workflow)
    return item


@transaction.atomic
def waive_clearance_item(*, item: ExitClearanceItem, actor, reason: str) -> ExitClearanceItem:
    """Excuse a clearance line. Distinct from completion, and always explained."""
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not reason.strip():
        raise OffboardingError({"reason": "Waiving a clearance item requires a reason."})
    if item.is_done:
        raise OffboardingError({"item": f"'{item.title}' is already {item.status}."})

    item.status = ClearanceStatus.WAIVED
    item.completed_at = timezone.now()
    item.completed_by = actor
    item.notes = reason.strip()
    item.save()

    _audit(
        item,
        actor=actor,
        entity_type="offboarding.ExitClearanceItem",
        action_verb="override",
        after={
            "event": "clearance_waived",
            "employee": item.exit_workflow.employee.employee_code,
            "item": item.title,
            "reason": reason.strip(),
        },
    )
    _advance_to_clearance(item.exit_workflow)
    return item


def _advance_to_clearance(workflow: ExitWorkflow) -> None:
    """Move out of NOTICE_PERIOD once clearance work has actually begun."""
    if workflow.stage == ExitStage.NOTICE_PERIOD:
        workflow.stage = ExitStage.CLEARANCE
        workflow.save(update_fields=["stage", "updated_at"])


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


@transaction.atomic
def update_settlement(*, settlement: FinalSettlement, actor, **amounts) -> FinalSettlement:
    """
    Record the settlement figures.

    Arithmetic only — statutory computation belongs to the payroll engine,
    which has its own compliance oracle. See the model docstring.
    """
    require(actor, Resource.OFFBOARDING, Action.EDIT)

    if settlement.is_cleared:
        raise OffboardingError(
            {"settlement": "This settlement is already cleared and cannot be edited."}
        )

    editable = {
        "final_working_date",
        "pending_salary",
        "leave_encashment",
        "bonus_or_incentive",
        "other_earnings",
        "outstanding_advances",
        "notice_shortfall_recovery",
        "asset_recovery",
        "other_deductions",
        "notes",
    }
    for field, value in amounts.items():
        if field in editable and value is not None:
            setattr(settlement, field, value)

    settlement.prepared_by = actor
    settlement.status = SettlementStatus.IN_REVIEW
    settlement.save()
    return settlement


@transaction.atomic
def clear_settlement(*, settlement: FinalSettlement, actor, notes: str = "") -> FinalSettlement:
    """
    Finance signs off.

    Takes APPROVE, not EDIT: preparing the numbers and accepting them are
    different acts, which is the same segregation payroll applies between
    processing a run and approving it.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if settlement.is_cleared:
        raise OffboardingError({"settlement": "This settlement is already cleared."})

    settlement.status = SettlementStatus.CLEARED
    settlement.cleared_by = actor
    settlement.cleared_at = timezone.now()
    if notes:
        settlement.notes = notes
    settlement.save()

    _audit(
        settlement,
        actor=actor,
        entity_type="offboarding.FinalSettlement",
        action_verb="approve",
        after={
            "event": "settlement_cleared",
            "employee": settlement.exit_workflow.employee.employee_code,
            "net_payable": str(settlement.net_payable),
        },
    )
    return settlement


# ---------------------------------------------------------------------------
# Exit interview
# ---------------------------------------------------------------------------


@transaction.atomic
def record_exit_interview(*, workflow: ExitWorkflow, actor, **fields) -> ExitInterview:
    """
    Record the exit conversation.

    Held under OFFBOARDING permissions rather than employee ones: candid
    feedback about a manager must not be readable by that manager.
    """
    require(actor, Resource.OFFBOARDING, Action.EDIT)

    interview, _ = ExitInterview.objects.get_or_create(exit_workflow=workflow)

    editable = {
        "primary_reason",
        "employee_feedback",
        "manager_feedback",
        "workplace_feedback",
        "improvement_suggestions",
        "would_recommend_employer",
        "rehire_eligibility",
        "hr_notes",
    }
    for field, value in fields.items():
        if field in editable and value is not None:
            setattr(interview, field, value)

    interview.conducted_by = actor
    interview.conducted_at = timezone.now()
    interview.save()

    _audit(
        interview,
        actor=actor,
        entity_type="offboarding.ExitInterview",
        action_verb="update",
        after={
            "event": "exit_interview_recorded",
            "employee": workflow.employee.employee_code,
            "rehire_eligibility": interview.rehire_eligibility,
        },
    )
    return interview


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------


def exit_blockers(workflow: ExitWorkflow) -> list[dict]:
    """
    Everything standing between this employee and EXITED.

    THE SINGLE SOURCE OF TRUTH for whether an exit may complete. Both
    `approve_exit` and `complete_exit` consult it, and the API serves it to the
    UI — so the frontend reports the server's answer rather than computing its
    own and disagreeing.

    Returns structured entries rather than strings, so the UI can group them by
    the gate they belong to.

    Each entry carries a unique `id` as well as its `gate`. The two differ on
    purpose: several blockers can belong to one gate — an outstanding "verify
    assets returned" TASK and the actual unreturned laptop are both under
    `assets`, and they are resolved in different ways. `gate` groups them;
    `id` identifies them, so a list keyed on it has no collisions.
    """
    blockers: list[dict] = []

    outstanding = list(workflow.outstanding_items.select_related())
    by_category: dict[str, list[str]] = {}
    for item in outstanding:
        by_category.setdefault(item.category, []).append(item.title)
    for category, titles in sorted(by_category.items()):
        blockers.append(
            {
                "id": f"clearance:{category}",
                "gate": category,
                "label": f"{ClearanceCategory(category).label} tasks",
                "detail": f"{len(titles)} required item(s) outstanding",
                "items": titles,
            }
        )

    unreturned = list(workflow.unreturned_assets)
    if unreturned:
        blockers.append(
            {
                "id": "assets:unreturned",
                "gate": "assets",
                "label": "Company property",
                "detail": f"{len(unreturned)} returnable asset(s) still allocated",
                "items": [f"{a.asset.asset_tag} ({a.asset.name})" for a in unreturned],
            }
        )

    settlement = getattr(workflow, "settlement", None)
    if settlement is None or not settlement.is_cleared:
        blockers.append(
            {
                "id": "finance:settlement",
                "gate": "finance",
                "label": "Full and final settlement",
                "detail": "The settlement has not been cleared by finance",
                "items": [],
            }
        )

    from apps.itaccounts.models import AccountStatus, CompanyEmailAccount

    account = CompanyEmailAccount.objects.filter(employee=workflow.employee).first()
    if account is not None and account.status != AccountStatus.DEPROVISIONED:
        blockers.append(
            {
                "id": "it:account",
                "gate": "it",
                "label": "Company account",
                "detail": f"{account.email_address} has not been deprovisioned",
                "items": [account.email_address],
            }
        )

    return blockers


@transaction.atomic
def approve_exit(*, workflow: ExitWorkflow, actor, notes: str = "") -> ExitWorkflow:
    """
    Sign off the exit as ready.

    Refuses while any gate is open. This is the check the spec asks for, and it
    lives here rather than in a view so a management command or a future task
    cannot route around it.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if not workflow.is_open:
        raise OffboardingError({"exit": "This exit is closed."})

    blockers = exit_blockers(workflow)
    if blockers:
        raise OffboardingError(
            {
                "blockers": [
                    f"{blocker['label']}: {blocker['detail']}" for blocker in blockers
                ]
            }
        )

    workflow.stage = ExitStage.APPROVED
    workflow.approved_by = actor
    workflow.approved_at = timezone.now()
    workflow.approval_notes = notes
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="approve",
        after={
            "event": "exit_approved",
            "employee": workflow.employee.employee_code,
            "notes": notes,
        },
    )
    return workflow


@transaction.atomic
def complete_exit(*, workflow: ExitWorkflow, actor, actual_last_working_date=None) -> ExitWorkflow:
    """
    The final act: the employee becomes EXITED.

    The status change goes through the lifecycle service, which re-runs the
    asset gate independently. Two checks of the same rule is deliberate — this
    one produces a readable list of everything outstanding, that one is the
    guarantee that no caller reaches EXITED around the side.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if workflow.stage == ExitStage.COMPLETED:
        raise OffboardingError({"exit": "This exit is already complete."})
    if workflow.stage != ExitStage.APPROVED:
        raise OffboardingError(
            {"exit": "The exit must be approved before the employee can be marked as exited."}
        )

    blockers = exit_blockers(workflow)
    if blockers:
        raise OffboardingError(
            {"blockers": [f"{blocker['label']}: {blocker['detail']}" for blocker in blockers]}
        )

    from apps.employees.services.lifecycle import change_status

    last_working = actual_last_working_date or workflow.expected_last_working_date

    change_status(
        employee=workflow.employee,
        actor=actor,
        new_status=EmployeeStatus.EXITED,
        reason=f"Exit completed ({workflow.exit_type}).",
        effective_date=last_working,
    )

    workflow.stage = ExitStage.COMPLETED
    workflow.actual_last_working_date = last_working
    workflow.completed_at = timezone.now()
    workflow.completed_by = actor
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="update",
        after={
            "event": "exit_completed",
            "employee": workflow.employee.employee_code,
            "actual_last_working_date": str(last_working),
        },
    )
    return workflow


@transaction.atomic
def cancel_exit(*, workflow: ExitWorkflow, actor, reason: str) -> ExitWorkflow:
    """
    Call the whole thing off — a withdrawn resignation, typically.

    Returns the employee to ACTIVE through the lifecycle service. Only possible
    before completion: EXITED is terminal, and nothing here can undo it.
    """
    require(actor, Resource.OFFBOARDING, Action.APPROVE)

    if workflow.stage == ExitStage.COMPLETED:
        raise OffboardingError(
            {
                "exit": (
                    "This exit is complete and cannot be cancelled. A returning employee "
                    "is represented by a new employee record."
                )
            }
        )
    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise OffboardingError({"reason": "Cancelling an exit requires a recorded reason."})

    from apps.employees.services.lifecycle import change_status

    change_status(
        employee=workflow.employee,
        actor=actor,
        new_status=EmployeeStatus.ACTIVE,
        reason=f"Exit cancelled: {reason.strip()}",
    )

    workflow.stage = ExitStage.CANCELLED
    workflow.cancelled_reason = reason.strip()
    workflow.save()

    _audit(
        workflow,
        actor=actor,
        entity_type="offboarding.ExitWorkflow",
        action_verb="update",
        after={
            "event": "exit_cancelled",
            "employee": workflow.employee.employee_code,
            "reason": reason.strip(),
        },
    )
    return workflow


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

_VERBS = {
    "create": "create",
    "update": "update",
    "approve": "approve",
    "reject": "reject",
    "override": "override",
}


def _audit(instance, *, actor, entity_type: str, action_verb: str, after: dict, before=None):
    """
    Every offboarding act names its actor and its moment.

    The verbs matter: a notice waiver and an early release are recorded as
    OVERRIDE rather than UPDATE, because they are exceptions to the agreed
    notice and an auditor looks for them specifically.
    """
    from apps.audit.models import AuditAction, AuditLog
    from apps.audit.signals import extract_reason, resolve_subject
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=getattr(AuditAction, _VERBS[action_verb].upper()),
        resource=Resource.OFFBOARDING,
        entity_type=entity_type,
        entity_id=str(instance.pk),
        entity_label=str(instance),
        # Same resolver the signal auditor uses, so an explicit event scopes
        # exactly like an automatic one — an exit is ABOUT the leaver, and
        # their Department Head should see it even though HR performed it.
        subject_employee=resolve_subject(instance),
        reason=extract_reason(after),
        before=before or {},
        after=after,
        request_id=get_request_id(),
    )
