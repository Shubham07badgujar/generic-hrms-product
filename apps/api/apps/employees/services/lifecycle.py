"""
Employment status transitions.

A status column with a serializer over it is not a lifecycle — it lets anyone
with EDIT move anyone from ONBOARDING straight to EXITED, or resurrect a
terminated employee, with no record of why. This module owns the permitted
moves and refuses the rest.

THE TRANSITION TABLE IS DATA, deliberately, so the rules are readable in one
place rather than scattered through `if` statements in views.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee, EmployeeStatus
from core.access import Action, Resource, require
from core.access.catalog import RoleCode, Scope
from core.access.engine import AccessDenied

S = EmployeeStatus

#: `from -> {allowed to}`. Absence is denial.
#:
#: Notes on the shape:
#:   - EXITED is reachable only from a notice/exit state, never straight from
#:     ACTIVE. Somebody must have resigned or been terminated first, and that
#:     act is what carries the reason.
#:   - Nothing leaves EXITED. A returning employee is a new employment record,
#:     not an edit to the old one — their service dates, statutory history and
#:     letters all belong to the period they cover.
#:   - CONFIRMED is not reachable here at all. It is written exclusively by the
#:     probation service on an explicit HR confirmation.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    S.ONBOARDING: frozenset({S.ON_PROBATION, S.ACTIVE, S.TERMINATED}),
    S.ON_PROBATION: frozenset({S.CONFIRMED, S.ACTIVE, S.ON_LEAVE, S.ON_NOTICE, S.TERMINATED, S.RESIGNED}),
    S.ACTIVE: frozenset({S.ON_PROBATION, S.CONFIRMED, S.ON_LEAVE, S.ON_NOTICE, S.RESIGNED, S.TERMINATED}),
    S.CONFIRMED: frozenset({S.ACTIVE, S.ON_LEAVE, S.ON_NOTICE, S.RESIGNED, S.TERMINATED}),
    S.ON_LEAVE: frozenset({S.ACTIVE, S.CONFIRMED, S.ON_NOTICE, S.RESIGNED, S.TERMINATED}),
    S.ON_NOTICE: frozenset({S.ACTIVE, S.CONFIRMED, S.RESIGNED, S.TERMINATED, S.EXITED}),
    S.RESIGNED: frozenset({S.EXITED, S.ACTIVE}),
    S.TERMINATED: frozenset({S.EXITED}),
    S.EXITED: frozenset(),
}

#: Ending someone's employment demands a written reason. The others do not.
REASON_REQUIRED = frozenset({S.RESIGNED, S.TERMINATED, S.EXITED})

MIN_REASON_LENGTH = 10


class LifecycleError(ValidationError):
    """A refused status transition."""


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def allowed_targets(current: str) -> list[str]:
    """What this employee could move to next. Drives the UI's picker."""
    return sorted(ALLOWED_TRANSITIONS.get(current, frozenset()))


@transaction.atomic
def change_status(
    *,
    employee: Employee,
    actor,
    new_status: str,
    reason: str = "",
    effective_date=None,
) -> Employee:
    """
    Move an employee to a new employment status.

    The single sanctioned path. Views, commands and future Celery tasks all
    come through here, so the transition table and the audit entry cannot be
    sidestepped by whoever writes the next caller.
    """
    # EDIT alone is not enough. Self-service grants every employee
    # EMPLOYEE/EDIT at SELF scope so they can maintain their own contact
    # details — and a bare `require` would let them resign, put themselves on
    # notice, or mark themselves confirmed. Moving someone through the
    # lifecycle is a management act, so it demands reach beyond oneself.
    scope = require(actor, Resource.EMPLOYEE, Action.EDIT)
    if scope <= Scope.SELF:
        raise AccessDenied(
            Resource.EMPLOYEE,
            Action.EDIT,
            "Changing an employment status requires authority over other people's "
            "records, not only your own.",
        )

    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    current = employee.status

    if new_status not in EmployeeStatus.values:
        raise LifecycleError({"status": f"Unknown status '{new_status}'."})

    if new_status == current:
        raise LifecycleError({"status": f"This employee is already {current}."})

    if new_status == S.CONFIRMED:
        # Confirmation is a probation decision, and routing it here would let
        # someone confirm an employee without a review ever being recorded.
        raise LifecycleError(
            {
                "status": (
                    "Confirmation is recorded through the probation decision, not as a "
                    "status change, so that it always rests on an explicit HR decision."
                )
            }
        )

    if not can_transition(current, new_status):
        permitted = allowed_targets(current) or ["nothing - this is a final state"]
        raise LifecycleError(
            {
                "status": (
                    f"An employee who is '{current}' cannot become '{new_status}'. "
                    f"Permitted next states: {', '.join(permitted)}."
                )
            }
        )

    if new_status in REASON_REQUIRED and len(reason.strip()) < MIN_REASON_LENGTH:
        raise LifecycleError(
            {
                "reason": (
                    f"Ending employment requires a recorded reason of at least "
                    f"{MIN_REASON_LENGTH} characters."
                )
            }
        )

    if new_status == S.EXITED:
        _assert_company_property_returned(employee)

    employee.status = new_status
    fields = ["status", "updated_at"]

    if new_status == S.EXITED:
        employee.date_of_exit = effective_date or timezone.localdate()
        fields.append("date_of_exit")

    employee.save(update_fields=fields)

    _audit_status_change(
        employee=employee, actor=actor, previous=current, new_status=new_status, reason=reason
    )
    return employee


def _assert_company_property_returned(employee: Employee) -> None:
    """
    An exit cannot complete while the company's property is in someone's house.

    Written off allocations are excluded — writing one off is itself an audited
    decision with a reason, which is the sanctioned way past this gate.
    """
    from apps.assets.models import AllocationStatus

    outstanding = list(
        employee.asset_allocations.filter(
            status=AllocationStatus.ACTIVE, asset__category__is_returnable=True
        ).select_related("asset")
    )
    if outstanding:
        held = ", ".join(f"{a.asset.asset_tag} ({a.asset.name})" for a in outstanding[:5])
        raise LifecycleError(
            {
                "assets": (
                    f"{len(outstanding)} company asset(s) are still allocated and must be "
                    f"returned or written off before this exit can complete: {held}."
                )
            }
        )


def _audit_status_change(*, employee, actor, previous, new_status, reason) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.UPDATE,
        resource=Resource.EMPLOYEE,
        entity_type="employees.Employee",
        entity_id=str(employee.pk),
        entity_label=str(employee),
        before={"status": previous},
        after={"event": "status_change", "status": new_status, "reason": reason},
        request_id=get_request_id(),
    )


#: An employee may be removed from the system only once their employment has
#: actually ended. Anyone still on the books must be offboarded first — the
#: exit workflow is where clearances, final pay and access revocation happen,
#: and "delete" must not become a way around it.
REMOVABLE_STATUSES = frozenset({EmployeeStatus.EXITED, EmployeeStatus.TERMINATED})


@transaction.atomic
def remove_employee(*, employee: Employee, actor, reason: str = "") -> Employee:
    """
    Take a former employee out of the system.

    What "removed" means here — and deliberately does not mean:

      * The employee record is soft-deleted: it leaves every list and picker,
        and cannot be found by code or name. It is NOT erased. Payslips,
        documents, audit rows and recruitment history reference it and stay
        intact; statutory retention does not end because someone pressed a
        button.
      * The login is switched off: the user is deactivated, every role
        assignment ended, every session revoked. A removed employee who still
        holds a working password and a role is the gap this closes — two
        exited employees in production could still sign in.
      * It is audited with the actor and reason, and is reversible by an
        administrator at the database (re-activate the row and the login).
      * The login EMAIL is released: the dead account's address is rewritten
        to a tombstone, so the same person can be onboarded again later with
        the same company email. Their old record keeps its history under the
        tombstoned login; the new employment is a fresh record, as the
        transition table above already insists.

    Only `EMPLOYEE/DELETE` holders (Admin, HR Head) may do it, and never to
    themselves. HR Head may remove only an exited or terminated employee —
    offboarding first. ADMIN may remove an employee in any status: the
    master key for a record created in error, without forcing a fiction of
    resignation through the offboarding pipeline first.
    """
    require(actor, Resource.EMPLOYEE, Action.DELETE)

    employee = Employee.objects.select_for_update(of=("self",)).get(pk=employee.pk)
    actor_is_admin = actor.user_roles.filter(
        is_active=True, role__is_active=True, role__code=RoleCode.ADMIN
    ).exists()
    if employee.status not in REMOVABLE_STATUSES and not actor_is_admin:
        raise ValidationError(
            {
                "employee": (
                    f"{employee.full_name} is {employee.get_status_display().lower()}. Only an "
                    f"exited or terminated employee can be removed — complete their "
                    f"offboarding first. (An administrator can remove directly.)"
                )
            }
        )
    if employee.user_id and employee.user_id == getattr(actor, "pk", None):
        raise ValidationError({"employee": "You cannot remove your own record."})

    before = {
        "status": employee.status,
        "is_active": employee.is_active,
        "login_active": employee.user.is_active if employee.user_id else None,
        "roles": (
            list(employee.user.user_roles.filter(is_active=True).values_list("role__code", flat=True))
            if employee.user_id else []
        ),
    }

    user = employee.user if employee.user_id else None
    released_email = None
    if user is not None:
        from apps.accounts.services.passwords import _revoke_all_refresh_tokens

        user.user_roles.filter(is_active=True).update(is_active=False, updated_at=timezone.now())
        user.is_active = False
        # Release the address for a future onboarding of the same person. The
        # tombstone keeps the original visible for the record and cannot be
        # logged into (the account is inactive and its sessions are revoked).
        released_email = user.email
        user.email = f"removed.{employee.employee_code}.{released_email}"[:254]
        user.save(update_fields=["is_active", "email"])
        _revoke_all_refresh_tokens(user)

    employee.delete()  # soft — BaseModel.delete()

    from apps.audit.events import record_event

    record_event(
        employee,
        actor=actor,
        entity_type="employees.Employee",
        verb="delete",
        resource=Resource.EMPLOYEE,
        before=before,
        after={
            "event": "employee_removed",
            "employee_code": employee.employee_code,
            "is_active": False,
            "login_active": False if user is not None else None,
            "roles": [],
            # The address this removal freed for re-onboarding, and what the
            # dead account is now called.
            "login_email_released": released_email,
            "login_email_tombstone": user.email if user is not None else None,
            "reason": reason.strip(),
        },
        reason=reason.strip() or None,
    )
    return employee
