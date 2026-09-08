"""
Onboarding: build a checklist from a template, work it, close it.

The template is chosen by MATCHING, not by naming: the most specific template
whose department and employment type fit the new joiner wins, falling back to
the organisation default. Nothing here asks what job someone was hired into, so
a new department gets its own onboarding by adding a template row.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee
from core.access import Action, Resource, require

from .models import (
    EmployeeOnboarding,
    ItemKind,
    ItemOwner,
    ItemStatus,
    OnboardingItem,
    OnboardingStatus,
    OnboardingTemplate,
)


class OnboardingError(ValidationError):
    """A refused onboarding operation."""


def resolve_template(employee: Employee) -> OnboardingTemplate | None:
    """
    The best-fitting template for this joiner.

    Specificity order — department AND employment type, then department, then
    employment type, then the default. Returns None when nothing is configured,
    which is a legitimate state: an organisation that has not set up onboarding
    should not have hiring blocked by its absence.
    """
    candidates = OnboardingTemplate.objects.filter(is_active=True)

    both = candidates.filter(
        department_id=employee.department_id, employment_type=employee.employment_type
    ).first()
    if both:
        return both

    by_department = candidates.filter(
        department_id=employee.department_id, employment_type=""
    ).first()
    if by_department:
        return by_department

    by_type = candidates.filter(
        department__isnull=True, employment_type=employee.employment_type
    ).first()
    if by_type:
        return by_type

    return candidates.filter(is_default=True).first()


def handbook_acknowledgement_pending(user) -> bool:
    """
    Whether this user still owes the handbook acknowledgement.

    True only while their own checklist is in progress with an open
    ACKNOWLEDGEMENT line. Drives the SPA's post-first-login handbook screen;
    the acknowledgement itself is the ordinary item completion, which the
    employee performs on their own item (ONBOARDING EDIT at SELF).
    """
    employee = getattr(user, "employee", None)
    if employee is None:
        return False
    return OnboardingItem.objects.filter(
        onboarding__employee=employee,
        onboarding__status=OnboardingStatus.IN_PROGRESS,
        kind=ItemKind.ACKNOWLEDGEMENT,
        status__in=(
            ItemStatus.PENDING,
            ItemStatus.IN_PROGRESS,
            ItemStatus.SUBMITTED,
            ItemStatus.BLOCKED,
        ),
        is_active=True,
    ).exists()


def _resolve_assignee(owner: str, employee: Employee) -> Employee | None:
    """
    Turn an owner BUCKET into the person actually responsible.

    Returns None when nobody fits — an unassigned item still appears on the
    checklist and in HR's queue, which is better than silently dropping it or
    assigning it to whoever happens to be first alphabetically.
    """
    if owner == ItemOwner.EMPLOYEE:
        return employee
    if owner == ItemOwner.MANAGER:
        return employee.reporting_manager
    if owner == ItemOwner.DEPARTMENT_HEAD:
        return getattr(employee.department, "head_employee", None)
    return None  # HR and ADMIN are queues, not individuals


@transaction.atomic
def start_onboarding(
    *, employee: Employee, actor=None, template: OnboardingTemplate | None = None
) -> EmployeeOnboarding:
    """
    Create an employee's checklist by COPYING a template.

    Called from employee creation, so it runs inside that transaction and an
    onboarding that cannot be built rolls the whole hire back rather than
    leaving a person with no checklist.

    Idempotent: re-running returns the existing checklist rather than issuing a
    second one.
    """
    existing = EmployeeOnboarding.objects.filter(employee=employee).first()
    if existing:
        return existing

    chosen = template or resolve_template(employee)

    onboarding = EmployeeOnboarding.objects.create(
        employee=employee,
        template=chosen,
        template_name=chosen.name if chosen else "",
        joining_date=employee.date_of_joining,
    )

    if chosen is None:
        return onboarding

    for source in chosen.items.filter(is_active=True).order_by("order", "title"):
        OnboardingItem.objects.create(
            onboarding=onboarding,
            source_item=source,
            title=source.title,
            description=source.description,
            kind=source.kind,
            owner=source.owner,
            assigned_to=_resolve_assignee(source.owner, employee),
            document_type=source.document_type,
            is_mandatory=source.is_mandatory,
            due_date=employee.date_of_joining + dt.timedelta(days=source.due_offset_days),
            order=source.order,
        )

    return onboarding


def _assert_actor_may_act(item: OnboardingItem, actor, *, waiving: bool = False) -> None:
    """
    The checklist's SUBJECT may finish their own lines and nothing else.

    Their EDIT grant is SELF-scoped so the endpoint admits them — that is what
    lets a joiner acknowledge the handbook — but scope alone would also let
    them tick the manager's induction tasks or waive a requirement away, which
    is self-certification. Anyone holding EDIT at ALL (HR Head, HR Manager,
    Admin) runs onboarding for a living and is exempt, their own checklist
    included.
    """
    from core.access import Scope
    from core.access.engine import can

    if can(actor, Resource.ONBOARDING, Action.EDIT) >= Scope.ALL:
        return
    employee = getattr(actor, "employee", None)
    if employee is None or employee.pk != item.onboarding.employee_id:
        # A manager acting on a team member's checklist — the scoped EDIT
        # already vetted that relationship.
        return
    if waiving:
        raise OnboardingError(
            {"item": "Your own checklist items can only be waived by HR."}
        )
    if item.owner != ItemOwner.EMPLOYEE:
        raise OnboardingError(
            {
                "item": (
                    f"'{item.title}' is completed by {item.get_owner_display()}, "
                    f"not by the joining employee."
                )
            }
        )


@transaction.atomic
def complete_item(
    *, item: OnboardingItem, actor, notes: str = "", document=None, file=None
) -> OnboardingItem:
    """
    Mark one checklist line done.

    A DOCUMENT item cannot be completed on someone's word: it must point at an
    uploaded document or carry a file. That is the difference between a
    checklist that records reality and one that records optimism.
    """
    require(actor, Resource.ONBOARDING, Action.EDIT)
    _assert_actor_may_act(item, actor)

    if item.is_done:
        raise OnboardingError({"item": f"'{item.title}' is already {item.status}."})

    if item.onboarding.status != OnboardingStatus.IN_PROGRESS:
        raise OnboardingError(
            {"onboarding": "This onboarding is closed and its items cannot be changed."}
        )

    if item.kind == ItemKind.DOCUMENT and document is None and file is None and not item.document_id:
        raise OnboardingError(
            {
                "document": (
                    f"'{item.title}' collects a document, so completing it requires the "
                    f"uploaded file or a link to an existing employee document."
                )
            }
        )

    # Upload is not approval. A DOCUMENT item is normally satisfied by the
    # verification flow — the employee uploads, HR verifies, and verification
    # completes the item. Completing one directly is therefore an act of
    # ATTESTATION and demands the same authority verification does; without
    # this, an employee could tick their own PAN "collected" and bypass HR
    # review entirely.
    if item.kind == ItemKind.DOCUMENT:
        from core.access.engine import can

        if not can(actor, Resource.EMPLOYEE_DOCUMENT, Action.APPROVE):
            raise OnboardingError(
                {
                    "item": (
                        f"'{item.title}' is completed through document verification: "
                        f"upload the document from your Documents section and HR will "
                        f"review it. Approval by HR is what completes this item."
                    )
                }
            )

    if document is not None:
        item.document = document
    if file is not None:
        item.file = file

    item.status = ItemStatus.COMPLETED
    item.completed_at = timezone.now()
    item.completed_by = actor
    if notes:
        item.notes = notes
    item.save()

    _maybe_close(item.onboarding, actor=actor)
    return item


@transaction.atomic
def waive_item(*, item: OnboardingItem, actor, reason: str) -> OnboardingItem:
    """
    Excuse an item.

    Requires a reason, and is a distinct state from COMPLETED so a waived
    mandatory item never reads as satisfied in a later review.
    """
    require(actor, Resource.ONBOARDING, Action.EDIT)
    _assert_actor_may_act(item, actor, waiving=True)

    if not reason.strip():
        raise OnboardingError({"reason": "Waiving a checklist item requires a reason."})
    if item.is_done:
        raise OnboardingError({"item": f"'{item.title}' is already {item.status}."})

    item.status = ItemStatus.WAIVED
    item.completed_at = timezone.now()
    item.completed_by = actor
    item.notes = reason.strip()
    item.save()

    _maybe_close(item.onboarding, actor=actor)
    return item


@transaction.atomic
def reopen_item(*, item: OnboardingItem, actor, reason: str = "") -> OnboardingItem:
    """Undo a completion — a document rejected on verification, typically."""
    require(actor, Resource.ONBOARDING, Action.EDIT)
    _assert_actor_may_act(item, actor)

    item.status = ItemStatus.PENDING
    item.completed_at = None
    item.completed_by = None
    if reason:
        item.notes = reason
    item.save()

    onboarding = item.onboarding
    if onboarding.status == OnboardingStatus.COMPLETED:
        # Reopening a line reopens the checklist; leaving it "completed" with an
        # outstanding item is the kind of quiet inconsistency nobody notices
        # until an audit.
        onboarding.status = OnboardingStatus.IN_PROGRESS
        onboarding.completed_at = None
        onboarding.save(update_fields=["status", "completed_at", "updated_at"])
    return item


def _maybe_close(onboarding: EmployeeOnboarding, *, actor) -> None:
    """Close the checklist once nothing MANDATORY is outstanding."""
    if onboarding.status != OnboardingStatus.IN_PROGRESS or not onboarding.is_complete:
        return

    onboarding.status = OnboardingStatus.COMPLETED
    onboarding.completed_at = timezone.now()
    onboarding.save(update_fields=["status", "completed_at", "updated_at"])

    _audit(
        onboarding,
        actor=actor,
        after={"event": "onboarding_completed", "employee": onboarding.employee.employee_code},
    )


@transaction.atomic
def complete_onboarding(*, onboarding: EmployeeOnboarding, actor, force: bool = False):
    """
    Close a checklist by hand.

    `force` exists for the genuine edge case where a mandatory item cannot be
    satisfied and waiving each one individually is noise — it demands
    ONBOARDING/APPROVE rather than EDIT, so it is not the same authority as
    ticking a box.
    """
    require(actor, Resource.ONBOARDING, Action.EDIT)

    outstanding = list(onboarding.outstanding_mandatory)
    if outstanding and not force:
        titles = ", ".join(item.title for item in outstanding[:5])
        raise OnboardingError(
            {
                "items": (
                    f"{len(outstanding)} mandatory item(s) are outstanding: {titles}. "
                    f"Complete or waive them first."
                )
            }
        )
    if outstanding and force:
        require(actor, Resource.ONBOARDING, Action.APPROVE)

    onboarding.status = OnboardingStatus.COMPLETED
    onboarding.completed_at = timezone.now()
    onboarding.save(update_fields=["status", "completed_at", "updated_at"])

    _audit(
        onboarding,
        actor=actor,
        after={
            "event": "onboarding_completed",
            "forced": bool(outstanding),
            "outstanding": [item.title for item in outstanding],
        },
    )
    return onboarding


def _audit(onboarding, *, actor, after: dict) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.UPDATE,
        resource=Resource.ONBOARDING,
        entity_type="onboarding.EmployeeOnboarding",
        entity_id=str(onboarding.pk),
        entity_label=str(onboarding),
        after=after,
        request_id=get_request_id(),
    )
