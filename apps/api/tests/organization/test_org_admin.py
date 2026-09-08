"""
Managing the organisation catalogues from the Admin Panel.

Two properties matter more than the CRUD itself: nothing is ever hard-deleted
(scoping and history point into these tables), and nothing in use can be
retired (a department that still employs people cannot quietly vanish from
under their scope).
"""

from __future__ import annotations

import pytest

from apps.organization.models import Department, Designation, Location

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(make_user):
    return make_user("admin")


def _auth(api, user):
    api.force_authenticate(user=user)
    return api


# ------------------------------------------------------------- department


def test_admin_creates_a_department(api, admin, org):
    response = _auth(api, admin).post(
        "/api/v1/departments/",
        {"name": "Radiology", "code": "RADIO", "kind": "medical", "description": ""},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Department.objects.filter(code="RADIO", is_active=True).exists()


def test_admin_edits_a_department(api, admin, org):
    from core.access.catalog import DepartmentKind

    department = org["departments"][DepartmentKind.FINANCE]
    response = _auth(api, admin).patch(
        f"/api/v1/departments/{department.pk}/", {"description": "Money."}, format="json"
    )

    assert response.status_code == 200
    department.refresh_from_db()
    assert department.description == "Money."


def test_a_department_with_employees_cannot_be_retired(api, admin, org, make_user):
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    department = org["departments"][DepartmentKind.MEDICAL]
    Employee.objects.create(
        employee_code="EMP07701",
        first_name="Blocker",
        department=department,
        date_of_joining="2024-01-01",
    )

    response = _auth(api, admin).delete(f"/api/v1/departments/{department.pk}/")

    assert response.status_code == 422
    assert "employees" in str(response.data)
    department.refresh_from_db()
    assert department.is_active is True


def test_an_unused_department_retires_softly(api, admin, org):
    empty = Department.objects.create(name="Vacant Wing", code="VACANT", kind="operations")

    response = _auth(api, admin).delete(f"/api/v1/departments/{empty.pk}/")

    assert response.status_code == 204
    empty.refresh_from_db()
    # Deactivated, never removed: history and audit rows point here.
    assert empty.is_active is False


# ------------------------------------------- designations, locations, levels


def test_admin_manages_a_designation(api, admin, org):
    created = _auth(api, admin).post(
        "/api/v1/designations/", {"title": "Radiologist"}, format="json"
    )
    assert created.status_code == 201

    updated = api.patch(
        f"/api/v1/designations/{created.data['id']}/", {"title": "Senior Radiologist"},
        format="json",
    )
    assert updated.status_code == 200

    removed = api.delete(f"/api/v1/designations/{created.data['id']}/")
    assert removed.status_code == 204
    assert not Designation.objects.filter(pk=created.data["id"], is_active=True).exists()


def test_admin_manages_a_location(api, admin, org):
    created = _auth(api, admin).post(
        "/api/v1/locations/", {"name": "Pune Clinic", "code": "PUNE01", "city": "Pune"},
        format="json",
    )
    assert created.status_code == 201
    assert Location.objects.filter(code="PUNE01", is_active=True).exists()


def test_admin_creates_a_level(api, admin, org):
    response = _auth(api, admin).post(
        "/api/v1/levels/", {"name": "Trainee", "code": "L6", "layer": 5, "rank": 60},
        format="json",
    )
    assert response.status_code == 201


# --------------------------------------------------------------- authority


def test_a_recruiter_reads_but_cannot_write(api, make_user, org):
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    recruiter = make_user("recruiter")
    # The role requires an employee record; without one it resolves to nothing
    # and every check would 403 for the wrong reason.
    Employee.objects.create(
        employee_code="EMP07750",
        user=recruiter,
        first_name="Ria",
        department=org["departments"][DepartmentKind.HR],
        date_of_joining="2024-01-01",
    )

    listed = _auth(api, recruiter).get("/api/v1/departments/")
    assert listed.status_code == 200

    created = api.post(
        "/api/v1/departments/", {"name": "Nope", "code": "NOPE", "kind": "hr"}, format="json"
    )
    assert created.status_code == 403


def test_the_ceo_cannot_write_org_structure(api, make_user, org):
    from core.access.catalog import DepartmentKind

    ceo = make_user("ceo")
    department = org["departments"][DepartmentKind.HR]

    response = _auth(api, ceo).patch(
        f"/api/v1/departments/{department.pk}/", {"description": "x"}, format="json"
    )

    assert response.status_code == 403
