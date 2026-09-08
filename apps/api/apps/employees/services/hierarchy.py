"""
Hierarchy and role-compatibility rules.

Pure validation: no writes, no side effects. Enforced at the SERVICE layer, so
it applies identically to the API, a management command, a bulk import and the
Django admin — anything that goes through `create_employee`. A rule that lives
only in a serializer is a rule the next caller forgets.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from core.access.catalog import DepartmentKind, Layer, RoleCode

#: Roles that exist WITHOUT an Employee record. They are system-level: authority
#: over the organization rather than a position within it. They cannot be
#: created through the employee flow at all.
SYSTEM_LEVEL_ROLES = frozenset({RoleCode.CEO, RoleCode.ADMIN})

#: Which department kinds each role may belong to. A role absent from this map
#: is department-agnostic — `employee`, `executive` and `office_boy` exist in
#: every function.
ROLE_DEPARTMENT_KINDS: dict[str, frozenset[str]] = {
    # Layer 2 — department heads. Each heads exactly one function.
    RoleCode.MEDICAL_DIRECTOR: frozenset({DepartmentKind.MEDICAL}),
    RoleCode.OPERATIONAL_HEAD: frozenset({DepartmentKind.OPERATIONS}),
    RoleCode.HR_HEAD: frozenset({DepartmentKind.HR}),
    RoleCode.FINANCE_HEAD: frozenset({DepartmentKind.FINANCE}),
    # Layer 3 — managers.
    RoleCode.SENIOR_DOCTOR: frozenset({DepartmentKind.MEDICAL}),
    RoleCode.OPERATIONS_MANAGER: frozenset({DepartmentKind.OPERATIONS}),
    RoleCode.HR_MANAGER: frozenset({DepartmentKind.HR}),
    RoleCode.ACCOUNTS_MANAGER: frozenset({DepartmentKind.FINANCE}),
    # Layer 4 — executives.
    RoleCode.CLINIC_DOCTOR: frozenset({DepartmentKind.MEDICAL}),
    RoleCode.CRE: frozenset({DepartmentKind.OPERATIONS}),
    RoleCode.RECRUITER: frozenset({DepartmentKind.HR}),
    RoleCode.PAYROLL_EXECUTIVE: frozenset({DepartmentKind.FINANCE}),
    # Layer 5 — clinical staff sit in the medical function.
    RoleCode.THERAPIST: frozenset({DepartmentKind.MEDICAL}),
}


class HierarchyError(ValidationError):
    """A hierarchy or role-compatibility rule was violated."""


def assert_role_is_assignable_to_an_employee(role) -> None:
    """
    Refuse system-level roles and any role marked ungrantable.

    CEO and Admin are authority OVER the organization rather than positions
    within it, and `requires_employee=False` says so. Creating an Employee for
    one would produce a person with a department and a manager whose access
    ignores both — an org chart that lies.
    """
    if role.code in SYSTEM_LEVEL_ROLES:
        raise HierarchyError(
            {
                "role": (
                    f"'{role.name}' is a system-level role and has no place in the "
                    f"employee hierarchy. It is granted directly to a login — via "
                    f"bootstrap for Admin, or by an Admin for CEO."
                )
            }
        )
    if not role.is_grantable:
        raise HierarchyError(
            {"role": f"'{role.name}' cannot be granted through the application."}
        )
    if not role.is_active:
        raise HierarchyError({"role": f"'{role.name}' is not active."})


def assert_role_matches_department(role, department) -> None:
    """
    A functional role must sit in its own function.

    An HR Head in the Medical department would hold HR authority while their
    department-scoped permissions resolved to clinical staff — a mismatch that
    produces surprising access rather than an error.
    """
    allowed = ROLE_DEPARTMENT_KINDS.get(role.code)
    if allowed is None:
        return  # department-agnostic role

    if department.kind not in allowed:
        expected = ", ".join(sorted(DepartmentKind(k).label for k in allowed))
        raise HierarchyError(
            {
                "department": (
                    f"'{role.name}' belongs to the {expected} function, but "
                    f"'{department.name}' is a {DepartmentKind(department.kind).label} "
                    f"department."
                )
            }
        )


def assert_role_matches_level(role, level) -> None:
    """A seniority band must agree with the role's layer, when one is given."""
    if level is None:
        return
    if level.layer != role.layer:
        raise HierarchyError(
            {
                "level": (
                    f"'{role.name}' sits at {Layer(role.layer).label}, but "
                    f"'{level.name}' is a {Layer(level.layer).label} band."
                )
            }
        )


def assert_reporting_manager_is_valid(*, manager, role, department, employee=None) -> None:
    """
    Validate the reporting line.

    The rules:
      1. The manager must be an active employee with a login and a role —
         otherwise approvals routed to them go nowhere.
      2. The manager must be at the SAME layer or a more senior one. Only a
         strictly JUNIOR manager is refused, because a line that runs downward
         inverts every approval the org chart is used for.
      3. Nobody reports to themselves, and no line may close into a cycle.

    Same-layer reporting is deliberate. Real structures are not a strict
    ladder: one department head coordinates another, a second HR Head reports
    to the first, a Senior Doctor reports to a peer who runs the rota. Refusing
    that forced HR to leave the field empty and lose the line entirely, which
    is worse than recording it. Peer lines are safe here because seniority is
    NOT what grants permission — the role does — so a peer manager gains no
    authority over their report beyond the reporting-tree visibility their own
    role already carries. What genuinely breaks things is a cycle, and rule 3
    is what prevents that.

    Cross-department reporting is permitted at any seniority: the clinic
    deliberately runs cross-functional lines (its own workflows assign a
    Senior Doctor to operations hiring rounds). Department heads and above may
    still have no manager at all — they answer to the CEO, who has no
    Employee record.

    `employee` is the person being placed. It is None at creation (they do not
    exist yet, so neither self-reporting nor a cycle is reachable) and is
    passed on every edit, where both are.
    """
    if manager is None:
        # Layer 2 heads legitimately have no internal manager — they answer to
        # the CEO, who has no Employee record to point at.
        if role.layer > Layer.DEPARTMENT_HEAD:
            raise HierarchyError(
                {
                    "reporting_manager": (
                        f"A {Layer(role.layer).label} employee must have a reporting "
                        f"manager. Only department heads may have none."
                    )
                }
            )
        return

    if not manager.is_active or manager.status == "exited":
        raise HierarchyError(
            {"reporting_manager": f"{manager.full_name} is not an active employee."}
        )

    manager_role = _primary_role_of(manager)
    if manager_role is None:
        raise HierarchyError(
            {
                "reporting_manager": (
                    f"{manager.full_name} has no active role, so cannot be a "
                    f"reporting manager. Approvals routed to them would go nowhere."
                )
            }
        )

    if manager_role.layer > role.layer:
        raise HierarchyError(
            {
                "reporting_manager": (
                    f"A {Layer(role.layer).label} employee cannot report to "
                    f"{manager.full_name}, who is {Layer(manager_role.layer).label} "
                    f"— more junior. A reporting manager must be at the same "
                    f"level or higher."
                )
            }
        )

    if employee is None or employee.pk is None:
        return

    if manager.pk == employee.pk:
        raise HierarchyError(
            {"reporting_manager": "An employee cannot report to themselves."}
        )

    # Walk UP from the proposed manager. Meeting this employee anywhere on the
    # way means the line closes into a loop, and "who approves this?" stops
    # having an answer for everyone caught in it.
    seen = {employee.pk}
    node = manager
    while node is not None:
        if node.pk in seen:
            chain = " → ".join(_chain_names(manager, employee))
            raise HierarchyError(
                {
                    "reporting_manager": (
                        f"This would create a reporting loop: {chain}. "
                        f"Point one of these lines elsewhere first."
                    )
                }
            )
        seen.add(node.pk)
        node = node.reporting_manager


def _chain_names(manager, employee) -> list[str]:
    """The loop as people read it, for an error message that can be acted on."""
    names = [employee.full_name, manager.full_name]
    seen = {employee.pk, manager.pk}
    node = manager.reporting_manager
    while node is not None and node.pk not in seen:
        names.append(node.full_name)
        seen.add(node.pk)
        node = node.reporting_manager
    names.append(employee.full_name)
    return names


def assert_creator_may_grant(*, actor, role) -> None:
    """
    A creator may not mint someone more senior than themselves.

    The permission matrix already decides WHO may create users; this decides
    HOW SENIOR the result may be. Without it an HR Manager holding
    `USER/CREATE` could create a Medical Director and thereby manufacture
    authority they do not have.

    Equal-layer creation IS allowed: the approved hierarchy has HR Head
    (Layer 2) creating other Layer 2 department heads.
    """
    from core.access import get_context

    context = get_context(actor)

    if RoleCode.ADMIN in context.role_codes or getattr(actor, "is_superuser", False):
        return

    if role.layer < context.min_layer:
        raise HierarchyError(
            {
                "role": (
                    f"You cannot create a {Layer(role.layer).label} employee: your "
                    f"own authority is {Layer(context.min_layer).label}."
                )
            }
        )


def _primary_role_of(employee):
    """The most senior active role held by an employee's login, if any."""
    if employee.user_id is None:
        return None
    grant = (
        employee.user.user_roles.filter(is_active=True, role__is_active=True)
        .select_related("role")
        .order_by("role__layer")
        .first()
    )
    return grant.role if grant else None


def eligible_reporting_managers(*, role, employee=None):
    """
    Everyone who may be this person's reporting manager, as a queryset.

    The dropdown and the validator must agree, so both read this: the form
    cannot offer a manager the service will refuse, and cannot hide one it
    would accept. Applies the same three rules — active with a live role, at
    the same layer or more senior, and not creating a loop.
    """
    from apps.employees.models import Employee

    candidates = (
        Employee.objects.active()
        .exclude(status__in=("exited", "terminated"))
        .filter(user__isnull=False, user__is_active=True)
        .select_related("user", "department")
        .prefetch_related("user__user_roles__role")
    )
    if employee is not None and employee.pk is not None:
        # Never offer the person themselves, nor anyone who reports (directly
        # or indirectly) to them — that is precisely the set that would loop.
        candidates = candidates.exclude(pk__in=employee.reporting_tree_ids())

    keep = []
    for candidate in candidates:
        candidate_role = _primary_role_of(candidate)
        if candidate_role is None:
            continue
        if candidate_role.layer > role.layer:
            continue
        keep.append(candidate.pk)
    return candidates.filter(pk__in=keep)


def set_reporting_manager(*, employee, manager, actor, reason: str = ""):
    """
    Repoint one employee's reporting line, validated and audited.

    The employee record is otherwise read-only over the API — writes go
    through narrow, audited services like this one rather than a general PATCH,
    so every change to the org chart has an author and a before/after.
    """
    from apps.audit.events import record_event
    from core.access import Resource

    role = _primary_role_of(employee)
    if role is None:
        raise HierarchyError(
            {
                "reporting_manager": (
                    f"{employee.full_name} has no active role, so their reporting "
                    f"line cannot be set."
                )
            }
        )

    assert_reporting_manager_is_valid(
        manager=manager, role=role, department=employee.department, employee=employee
    )

    before = employee.reporting_manager
    if before is not None and manager is not None and before.pk == manager.pk:
        return employee

    employee.reporting_manager = manager
    employee.updated_by = actor
    employee.save(update_fields=["reporting_manager", "updated_by", "updated_at"])

    record_event(
        employee,
        actor=actor,
        entity_type="employees.Employee",
        verb="update",
        resource=Resource.EMPLOYEE,
        before={"reporting_manager": before.employee_code if before else None},
        after={"reporting_manager": manager.employee_code if manager else None},
        reason=reason or (
            f"Reporting manager set to {manager.full_name}" if manager
            else "Reporting manager cleared"
        ),
    )
    return employee
