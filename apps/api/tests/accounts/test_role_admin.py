"""
Runtime role and permission administration.

The claims under test:

  1. A permission edit made through the service takes effect in `can()` —
     the matrix in the database is the live truth, not the code defaults.
  2. `is_customized` is honoured: a re-seed never reverts an admin's edit.
  3. The operations that would change what the ENGINE means — minting user
     managers, granting writes to a read-only role, touching system-role
     structure — are refused loudly rather than written and silently ignored.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.accounts.models import Role, RolePermission, UserRole
from apps.accounts.services import role_admin
from core.access import Action, Resource, Scope, can
from core.access.context import invalidate

pytestmark = pytest.mark.django_db

BASE = "/api/v1/roles/"


@pytest.fixture
def admin(make_user):
    return make_user("admin")


@pytest.fixture
def custom_role(admin):
    return role_admin.create_role(
        actor=admin, code="lab_technician", name="Lab Technician", layer=5
    )


# ------------------------------------------------------------- role CRUD


def test_admin_can_create_a_custom_role(custom_role):
    assert custom_role.code == "lab_technician"
    assert custom_role.is_system is False
    # Born with none of the engine-shaping flags, whatever anyone posts.
    assert custom_role.is_read_only is False
    assert custom_role.can_manage_users is False
    # And with no permissions at all — grants are explicit acts.
    assert custom_role.permissions.count() == 0


def test_a_role_without_user_management_cannot_author_roles(make_user):
    """The engine's gate, not a view check: recruiter has no ROLE writes."""
    with pytest.raises(Exception):
        role_admin.create_role(
            actor=make_user("recruiter"), code="x", name="X", layer=5
        )


def test_a_system_roles_structure_is_immutable(admin, roles):
    with pytest.raises(ValidationError):
        role_admin.update_role(actor=admin, role=roles["hr_head"], layer=5)


def test_a_system_roles_description_may_change(admin, roles):
    updated = role_admin.update_role(
        actor=admin, role=roles["recruiter"], description="Hires people."
    )
    assert updated.description == "Hires people."


def test_a_custom_role_may_be_renamed(admin, custom_role):
    updated = role_admin.update_role(actor=admin, role=custom_role, name="Senior Lab Tech")
    assert updated.name == "Senior Lab Tech"


def test_a_system_role_cannot_be_deactivated(admin, roles):
    with pytest.raises(ValidationError):
        role_admin.deactivate_role(actor=admin, role=roles["employee"])


def test_a_held_role_cannot_be_deactivated(admin, custom_role, make_user):
    holder = make_user("employee", email="holder@example.test")
    UserRole.objects.create(user=holder, role=custom_role)

    with pytest.raises(ValidationError):
        role_admin.deactivate_role(actor=admin, role=custom_role)


def test_an_unheld_custom_role_deactivates(admin, custom_role):
    role_admin.deactivate_role(actor=admin, role=custom_role)
    custom_role.refresh_from_db()
    assert custom_role.is_active is False


# ------------------------------------------------------- permission cells


def test_a_granted_cell_is_live_in_can(admin, custom_role, make_user, org):
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    holder = make_user("employee", email="cellholder@example.test")
    UserRole.objects.filter(user=holder).delete()
    UserRole.objects.create(user=holder, role=custom_role)
    # Custom roles require an employee record; without one the engine
    # correctly resolves the holder to nothing at all.
    Employee.objects.create(
        employee_code="EMP07777",
        user=holder,
        first_name="Cell",
        department=org["departments"][DepartmentKind.MEDICAL],
        date_of_joining="2024-01-01",
    )

    role_admin.set_permissions(
        actor=admin,
        role=custom_role,
        cells=[{"resource": "candidate", "action": "view", "scope": 4}],
    )
    invalidate()

    assert can(holder, Resource.CANDIDATE, Action.VIEW) == Scope.ALL


def test_scope_zero_removes_the_cell(admin, custom_role):
    role_admin.set_permissions(
        actor=admin,
        role=custom_role,
        cells=[{"resource": "candidate", "action": "view", "scope": 4}],
    )
    role_admin.set_permissions(
        actor=admin,
        role=custom_role,
        cells=[{"resource": "candidate", "action": "view", "scope": 0}],
    )

    assert not custom_role.permissions.filter(is_active=True).exists()


def test_an_edited_cell_survives_reseeding(admin, roles):
    """The is_customized contract, end to end through the real seeder."""
    role = roles["recruiter"]
    role_admin.set_permissions(
        actor=admin,
        role=role,
        cells=[{"resource": "report", "action": "view", "scope": 4}],
    )

    call_command("seed_roles")

    cell = RolePermission.objects.get(role=role, resource="report", action="view")
    assert cell.scope == 4
    assert cell.is_customized is True


def test_writes_for_a_read_only_role_are_refused(admin, roles):
    """The clamp would strip it anyway; writing a dead cell is refused instead."""
    with pytest.raises(ValidationError):
        role_admin.set_permissions(
            actor=admin,
            role=roles["ceo"],
            cells=[{"resource": "candidate", "action": "edit", "scope": 4}],
        )


def test_reads_for_a_read_only_role_are_fine(admin, roles):
    role_admin.set_permissions(
        actor=admin,
        role=roles["ceo"],
        cells=[{"resource": "import_batch", "action": "view", "scope": 4}]
        if "import_batch" in Resource.values
        else [{"resource": "candidate", "action": "view", "scope": 4}],
    )


def test_user_management_writes_are_refused_for_ordinary_roles(admin, custom_role):
    """A form must not be able to mint a user manager."""
    with pytest.raises(ValidationError):
        role_admin.set_permissions(
            actor=admin,
            role=custom_role,
            cells=[{"resource": "user", "action": "edit", "scope": 4}],
        )


def test_import_exists_only_for_candidates(admin, custom_role):
    with pytest.raises(ValidationError):
        role_admin.set_permissions(
            actor=admin,
            role=custom_role,
            cells=[{"resource": "department", "action": "import", "scope": 4}],
        )


def test_nonsense_cells_are_refused(admin, custom_role):
    for bad in (
        {"resource": "not_a_thing", "action": "view", "scope": 1},
        {"resource": "candidate", "action": "not_a_verb", "scope": 1},
        {"resource": "candidate", "action": "view", "scope": 9},
    ):
        with pytest.raises(ValidationError):
            role_admin.set_permissions(actor=admin, role=custom_role, cells=[bad])


# ------------------------------------------------------------ over HTTP


def test_the_matrix_reads_over_http(api, admin, roles):
    api.force_authenticate(user=admin)

    response = api.get(f"{BASE}{roles['recruiter'].pk}/permissions/")

    assert response.status_code == 200
    cells = {(row["resource"], row["action"]) for row in response.data}
    assert ("candidate", "import") in cells


def test_cells_write_over_http_and_read_back(api, admin, roles):
    api.force_authenticate(user=admin)

    response = api.post(
        f"{BASE}{roles['recruiter'].pk}/permissions/set/",
        {"cells": [{"resource": "report", "action": "view", "scope": 3}]},
        format="json",
    )

    assert response.status_code == 200
    cells = {(r["resource"], r["action"]): r for r in response.data}
    assert cells[("report", "view")]["scope"] == 3
    assert cells[("report", "view")]["is_customized"] is True


def test_role_creation_over_http_ignores_smuggled_flags(api, admin):
    api.force_authenticate(user=admin)

    response = api.post(
        BASE,
        {
            "code": "auditor",
            "name": "External Auditor",
            "layer": 5,
            # Smuggled: the serializer has no such fields, so they fall away.
            "is_read_only": False,
            "can_manage_users": True,
            "is_system": True,
        },
        format="json",
    )

    assert response.status_code == 201
    role = Role.objects.get(code="auditor")
    assert role.can_manage_users is False
    assert role.is_system is False


def test_an_hr_head_cannot_edit_the_matrix_over_http(api, make_user, roles):
    """hr_head holds ROLE VIEW only — assigns roles, does not define them."""
    api.force_authenticate(user=make_user("hr_head"))

    response = api.post(
        f"{BASE}{roles['recruiter'].pk}/permissions/set/",
        {"cells": [{"resource": "report", "action": "view", "scope": 4}]},
        format="json",
    )

    assert response.status_code == 403
