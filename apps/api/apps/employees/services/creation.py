"""
Employee-first creation: person, placement, role and login in ONE transaction.

THE RULE
--------
A normal employee never exists without a login, and a login never exists
without an employee. Either the whole set commits or none of it does.

WHY IT MATTERS HERE SPECIFICALLY
--------------------------------
The access engine is fail-closed: a role flagged `requires_employee` resolves
to NO permissions when the login has no linked Employee. So a half-created
account is not a cosmetic inconsistency — it is a person who can sign in and
see an empty application, with no error explaining why. Wrapping the whole
thing in one transaction makes that state unreachable.

CEO and Admin are the deliberate exceptions and are refused by this path; they
are granted directly to a login.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Role, User, UserRole
from apps.accounts.services.passwords import (
    generate_temporary_password,
    send_account_created_email,
)
from apps.employees.models import Employee, next_employee_code
from apps.organization.models import (
    Department,
    Designation,
    EmployeeLevel,
    Location,
    OrganizationMembership,
    Team,
)
from core.access import Action, Resource, invalidate, require
from core.access.context import get_context

from .hierarchy import (
    assert_creator_may_grant,
    assert_reporting_manager_is_valid,
    assert_role_is_assignable_to_an_employee,
    assert_role_matches_department,
    assert_role_matches_level,
)


@dataclass
class EmployeeCreationResult:
    employee: Employee
    user: User
    role: Role
    temporary_password: str | None
    #: The checklist issued to this joiner, when onboarding is configured.
    onboarding: object | None = None
    #: Whether the welcome email actually went. None means it was not
    #: attempted. `ATOMIC_REQUESTS` is off and this function owns its own
    #: transaction, so the on_commit hook has already run by the time this
    #: returns — the caller can therefore report the TRUTH rather than
    #: assuming a send succeeded because it was scheduled.
    welcome_email_sent: bool | None = None


@transaction.atomic
def create_employee(
    *,
    actor,
    first_name: str,
    last_name: str = "",
    middle_name: str = "",
    email: str,
    role_code: str,
    department_id,
    date_of_joining,
    designation_id=None,
    location_id=None,
    level_id=None,
    team_id=None,
    reporting_manager_id=None,
    employment_type: str = "full_time",
    phone: str = "",
    personal_email: str = "",
    temporary_password: str | None = None,
    send_welcome_email: bool = True,
    require_designation: bool = True,
    employee_code: str | None = None,
    #: Issue the onboarding checklist as part of the same transaction. Off only
    #: for fixtures and back-fills that build joining state themselves.
    start_onboarding_checklist: bool = True,
    #: Probation length. 0 or None means the role carries no probation.
    probation_months: int | None = 6,
    **extra_fields,
) -> EmployeeCreationResult:
    """
    Create an employee and their login atomically.

    Order is deliberate: every check that can fail runs BEFORE the first write,
    so the common rejection path never touches the database. The transaction
    then guarantees the rest.
    """
    # --- 1. Authorisation ------------------------------------------------
    # Who may create users at all. Raises AccessDenied.
    require(actor, Resource.USER, Action.CREATE)
    require(actor, Resource.EMPLOYEE, Action.CREATE)

    # --- 2. Resolve and validate references ------------------------------
    role = _get_or_400(Role, code=role_code, label="role")
    department = _get_or_400(Department, pk=department_id, label="department")

    # Every employee created here should carry a job title: it is what the
    # welcome email announces, what appears against them in every directory,
    # and the thing HR is most often asked for. Enforced in the service and
    # not only in the serializer, because a management command reaches this
    # function directly.
    #
    # `require_designation=False` exists for ONE caller: converting a hired
    # candidate, where the title comes from the offer or the job opening and
    # some job openings genuinely have none. Refusing there would block a
    # completed hire at the last step, which is a worse failure than an
    # employee whose title is filled in afterwards.
    if require_designation and not designation_id:
        raise ValidationError({"designation": "Designation is required."})

    designation = (
        _get_or_400(Designation, pk=designation_id, label="designation")
        if designation_id
        else None
    )
    location = (
        _get_or_400(Location, pk=location_id, label="location") if location_id else None
    )
    level = _get_or_400(EmployeeLevel, pk=level_id, label="level") if level_id else None
    team = _get_or_400(Team, pk=team_id, label="team") if team_id else None
    manager = (
        _get_or_400(Employee, pk=reporting_manager_id, label="reporting_manager")
        if reporting_manager_id
        else None
    )

    # --- 3. Hierarchy rules ----------------------------------------------
    assert_role_is_assignable_to_an_employee(role)
    assert_creator_may_grant(actor=actor, role=role)
    assert_role_matches_department(role, department)
    assert_role_matches_level(role, level)
    # `employee=None`: they do not exist yet, so neither self-reporting nor a
    # cycle is reachable at creation. Edits pass the record and get both checks.
    assert_reporting_manager_is_valid(
        manager=manager, role=role, department=department, employee=None
    )

    if designation and designation.department_id and designation.department_id != department.pk:
        raise ValidationError(
            {
                "designation": (
                    f"'{designation.title}' belongs to another department and cannot "
                    f"be used in '{department.name}'."
                )
            }
        )
    if team and team.department_id != department.pk:
        raise ValidationError(
            {"team": f"Team '{team.name}' does not belong to '{department.name}'."}
        )

    # --- 4. Uniqueness ----------------------------------------------------
    normalized_email = User.objects.normalize_email(email)
    if User.objects.filter(email=normalized_email).exists():
        raise ValidationError(
            {"email": f"A user with the email {normalized_email} already exists."}
        )

    # --- 5. Writes. Everything past here either all commits or all rolls back.
    #
    # A temporary password is GENERATED unless the caller supplied one. It was
    # previously optional-and-absent by default, on the theory that "no usable
    # password until set out of band" is safer — but "out of band" meant a
    # channel that did not exist, so new employees simply could not log in.
    # The generated secret is random, single-use by construction (the account
    # is flagged must_change_password, which a middleware enforces on every
    # request), and delivered by mail after commit. It is returned to the
    # caller ONCE and never persisted in clear.
    generated = temporary_password is None
    if generated:
        temporary_password = generate_temporary_password()

    user = User.objects.create_user(
        email=normalized_email,
        password=temporary_password,
        first_name=first_name,
        last_name=last_name,
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    # The new login belongs to the organization its creator acts in. This row
    # is the ONLY thing `resolve_context()` reads to decide a principal's
    # tenant, so an account created without one resolves to DENY_ALL and can
    # sign in to nothing -- which is the correct failure direction, but a
    # baffling one to debug. Created inside the same transaction as the User
    # and the role grant, so a principal is never half-provisioned.
    OrganizationMembership.objects.create(
        organization_id=get_context(actor).organization_id, user=user
    )

    employee = Employee(
        employee_code=employee_code or next_employee_code(),
        user=user,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        work_email=normalized_email,
        personal_email=personal_email,
        phone=phone,
        department=department,
        designation=designation,
        location=location,
        level=level,
        team=team,
        reporting_manager=manager,
        employment_type=employment_type,
        date_of_joining=date_of_joining,
        **extra_fields,
    )
    # full_clean runs the model's own cycle and self-reference guards. Inside
    # the transaction, so a failure here rolls back the User created above.
    employee.full_clean(exclude=["user"])
    employee.save()

    grant = UserRole(user=user, role=role, assigned_by=actor)
    grant.full_clean(exclude=["assigned_by"])  # enforces read-only exclusivity
    grant.save()

    # The actor's own cached context is unaffected, but the NEW user's must not
    # be served from a stale entry if anything resolves it later in this request.
    invalidate(user.pk)

    # --- 6. Joining state -------------------------------------------------
    # Inside the SAME transaction, so a hire either arrives complete — person,
    # login, role, probation clock and checklist — or not at all. An employee
    # with no onboarding is the partial state this architecture exists to
    # prevent, one step removed from a login with no employee.
    onboarding = None
    if start_onboarding_checklist:
        from apps.onboarding.services import start_onboarding as issue_checklist

        onboarding = issue_checklist(employee=employee, actor=actor)

    if probation_months:
        from .probation import start_probation

        start_probation(employee=employee, months=probation_months, actor=actor)
        employee.refresh_from_db()

    _audit_creation(employee=employee, user=user, role=role, actor=actor)

    # After commit, so a rolled-back creation never mails credentials for a
    # login that does not exist. The mail helper logs and audits its own
    # failure rather than raising — the account is real either way, and HR is
    # told in the response whether the mail went.
    # Built BEFORE the hook is registered, and mutated BY it. The ordering is
    # the whole trick: `@transaction.atomic` runs this function, then commits,
    # then runs the on_commit hooks, and only then hands the return value to
    # the caller — so a hook that writes to this object is seen by whoever
    # receives it. Reading a plain local here instead would always report
    # None, because nothing has been sent yet at the moment of construction.
    result = EmployeeCreationResult(
        employee=employee,
        user=user,
        role=role,
        temporary_password=temporary_password,
        onboarding=onboarding,
    )

    if send_welcome_email:

        def _mail():
            # The employee and role are passed so the message can name a real
            # position and a real full name — both live on those records, not
            # on the login.
            result.welcome_email_sent = send_account_created_email(
                user=user,
                temporary_password=temporary_password,
                employee=employee,
                role=role,
                actor=actor,
            )

        transaction.on_commit(_mail)

    return result


def _get_or_400(model, *, label: str, **lookup):
    """Resolve a reference, or raise a field-attributed validation error."""
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        raise ValidationError({label: f"No {label} matches {lookup}."})
    return obj


def _audit_creation(*, employee, user, role, actor) -> None:
    """
    Record the creation as one semantic event.

    The audit signal layer already logs the individual Employee, User and
    UserRole inserts. This adds the event that ties them together — "X hired Y
    as Z" — which is what someone reviewing later actually wants to read.
    """
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.CREATE,
        resource=Resource.EMPLOYEE,
        entity_type="employees.Employee",
        entity_id=str(employee.pk),
        entity_label=str(employee),
        after={
            "event": "employee_created",
            "employee_code": employee.employee_code,
            "email": user.email,
            "role": role.code,
            "department": employee.department.name,
            "reporting_manager": (
                employee.reporting_manager.employee_code
                if employee.reporting_manager
                else None
            ),
        },
        request_id=get_request_id(),
    )
