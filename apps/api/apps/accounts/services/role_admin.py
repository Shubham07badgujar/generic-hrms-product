"""
Runtime role and permission administration.

`RolePermission.is_customized` has existed since the matrix was first seeded,
for precisely this: an administrator edits a cell, the flag is set, and
`seed_roles` leaves that cell alone forever after. This module is the missing
half — the guarded way to do the editing.

WHAT THIS CAN NEVER DO
----------------------
The engine's hard rules run AFTER anything written here, so no edit made
through this service can:

  - give a read-only principal (CEO) a write — the clamp strips it,
  - give USER/ROLE writes to a role without `can_manage_users` — the
    user-management gate strips them,

and this service refuses on top, loudly, rather than letting an administrator
write a cell the engine will silently ignore. It also refuses to mint the
flags themselves: a role created here is never read-only, never a user
manager, never a system role. Those three change meaning for the whole
engine, and changing them stays a code review, not a form submission.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Role, RolePermission, UserRole
from core.access import Action, Resource, require
from core.access.context import invalidate
from core.access.catalog import WRITE_ACTIONS, Scope

#: Resources whose WRITE actions are the user-management surface. Cells here
#: are refused for any role that cannot manage users — the engine would strip
#: them anyway, and a cell that silently does nothing is a lie in a matrix.
_USER_MANAGEMENT = frozenset({str(Resource.USER), str(Resource.ROLE)})

#: The audit trail must stay readable by exactly whom the code says. Making it
#: writable is meaningless (nothing routes writes), but a stray cell would
#: still be confusing evidence in the table that defines everyone's access.
_NEVER_WRITABLE = frozenset({str(Resource.AUDIT_LOG)})


class RoleAdminError(ValidationError):
    """A refused role-administration operation."""


def _refuse(field: str, message: str):
    raise RoleAdminError({field: message})


@transaction.atomic
def create_role(
    *,
    actor,
    code: str,
    name: str,
    layer: int,
    description: str = "",
    is_grantable: bool = True,
    department_kind: str = "",
) -> Role:
    """A custom role. Born with no permissions — grant them cell by cell."""
    require(actor, Resource.ROLE, Action.CREATE)

    return Role.objects.create(
        code=code,
        name=name,
        layer=layer,
        description=description,
        is_grantable=is_grantable,
        department_kind=department_kind,
        # Never negotiable from a form; see the module docstring.
        is_system=False,
        is_read_only=False,
        can_manage_users=False,
        requires_employee=True,
    )


@transaction.atomic
def update_role(*, actor, role: Role, **fields) -> Role:
    """
    Edit a role's presentation. A SYSTEM role's structure is immutable — its
    layer and flags are what the seeded hierarchy invariants were proven
    against — so only its description and grantability may move.
    """
    require(actor, Resource.ROLE, Action.EDIT)

    editable = {"description", "is_grantable"}
    if not role.is_system:
        editable |= {"name", "layer", "department_kind"}

    refused = set(fields) - editable
    if refused:
        _refuse(
            "role",
            f"{', '.join(sorted(refused))} cannot be changed on "
            f"{'a system role' if role.is_system else 'a role'} through the API.",
        )

    for field, value in fields.items():
        setattr(role, field, value)
    role.full_clean(exclude=["code"])
    role.save(update_fields=[*fields, "updated_at"])
    return role


@transaction.atomic
def deactivate_role(*, actor, role: Role) -> Role:
    require(actor, Resource.ROLE, Action.DELETE)

    if role.is_system:
        _refuse("role", "System roles cannot be deactivated. Revoke their permissions instead.")
    if UserRole.objects.filter(role=role, is_active=True).exists():
        _refuse(
            "role",
            "People still hold this role. Reassign them first — deactivating it "
            "now would strand their access mid-session.",
        )

    role.delete()  # soft
    return role


@transaction.atomic
def set_permissions(*, actor, role: Role, cells: list[dict]) -> list[RolePermission]:
    """
    Write cells of one role's matrix. `scope=0` removes the cell — absence is
    deny, exactly as the seeder stores it.

    Every touched row is marked `is_customized`, which is the contract with
    `seed_roles`: a re-seed updates only cells no administrator has claimed.
    """
    require(actor, Resource.ROLE, Action.EDIT)

    validated = []
    for index, cell in enumerate(cells):
        resource = str(cell.get("resource", ""))
        action = str(cell.get("action", ""))
        scope = cell.get("scope")

        if resource not in Resource.values:
            _refuse(f"cells[{index}]", f"'{resource}' is not a resource.")
        if action not in Action.values:
            _refuse(f"cells[{index}]", f"'{action}' is not an action.")
        if not isinstance(scope, int) or scope not in Scope.values:
            _refuse(f"cells[{index}]", "scope must be one of 0 (none) to 4 (organisation).")

        is_write = action in WRITE_ACTIONS
        if scope and is_write:
            if role.is_read_only:
                _refuse(
                    f"cells[{index}]",
                    f"'{role.name}' is a read-only role. The engine strips every "
                    "write unconditionally, so this cell could never take effect.",
                )
            if resource in _USER_MANAGEMENT and not role.can_manage_users:
                _refuse(
                    f"cells[{index}]",
                    f"'{role.name}' cannot manage users, so writes on "
                    f"'{resource}' would be stripped by the engine. Granting "
                    "user management is a code change, not a matrix edit.",
                )
            if resource in _NEVER_WRITABLE:
                _refuse(f"cells[{index}]", "The audit log is never writable by anyone.")
            if action == Action.IMPORT and resource != str(Resource.CANDIDATE):
                _refuse(
                    f"cells[{index}]",
                    "Bulk import exists only for candidates. Nothing else has "
                    "an importer, and organisation configuration never will.",
                )
        validated.append((resource, action, scope))

    touched = []
    for resource, action, scope in validated:
        if scope == 0:
            RolePermission.objects.filter(role=role, resource=resource, action=action).delete()
            continue
        row, _ = RolePermission.objects.update_or_create(
            role=role,
            resource=resource,
            action=action,
            defaults={"scope": scope, "is_customized": True, "is_active": True},
        )
        touched.append(row)

    # Memoized contexts predate the edit; drop them so the change binds within
    # this request rather than the next.
    invalidate()
    return touched
