"""
A create payload cannot smuggle in another organization's row.

The read matrix asks whether A can SEE B's data. This asks the harder question:
whether A can ATTACH itself to B's data by naming it.

The vector is DRF's own convenience. `ModelSerializer` builds a
`PrimaryKeyRelatedField(queryset=Model.objects.all())` for every writable
foreign key, automatically and invisibly -- roughly 77 of them across this
codebase. Grepping for `queryset=` finds a fraction, because the dangerous ones
are never written down. Each is a lookup over EVERY organization's rows, so a
POST naming another company's department id would be accepted, and the row
created in A would point at B.
"""

from __future__ import annotations

import datetime as dt

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def _employee_payload(world, **overrides):
    """
    A payload that is valid in every respect EXCEPT the field under test.

    That completeness is the whole point. An incomplete payload is refused for
    the wrong reason, and a test asserting "refused" would then pass whether or
    not cross-tenant references are checked at all -- which is precisely what
    the positive control below caught when `personal_email` was missing.
    """
    payload = {
        "first_name": "Injected",
        "last_name": "Row",
        "email": "injected@acme-health.example",
        "personal_email": "injected.personal@example.test",
        "role_code": "employee",
        "department_id": str(world.department.pk),
        "designation_id": str(world.designation.pk),
        "location_id": str(world.location.pk),
        "level_id": str(world.level.pk),
        # Staff-level hires must report to somebody; only department heads may
        # not. Supplied so the payload is rejected for the field under test and
        # nothing else.
        "reporting_manager_id": str(world.hr_employee.pk),
        "date_of_joining": "2025-01-06",
    }
    payload.update(overrides)
    return payload


def test_creating_an_employee_in_another_organizations_department_is_refused(
    org_a, org_b, api_for
):
    """
    The clearest case: A's HR names B's department. Accepting it would place a
    person in a company that never hired them, and -- because department drives
    department-scoped visibility -- hand B's department head a stranger.
    """
    client = api_for(org_a.hr)

    response = client.post(
        "/api/v1/employees/",
        _employee_payload(org_a, department_id=str(org_b.department.pk)),
        format="json",
    )

    assert response.status_code in (400, 404), (
        f"expected refusal, got {response.status_code}: {response.content[:300]}"
    )
    # Refused for the RIGHT reason: the department, not some other missing
    # field. Otherwise this test would pass on an invalid payload while
    # proving nothing about tenancy.
    body = response.content.decode()
    assert "department" in body.lower(), (
        f"refused, but not because of the department: {body[:300]}"
    )
    from apps.employees.models import Employee

    assert not Employee.objects.all_orgs().filter(first_name="Injected").exists()


@pytest.mark.parametrize("field", ["designation_id", "location_id", "level_id"])
def test_every_structural_reference_is_checked_not_just_the_first(
    org_a, org_b, api_for, field
):
    """
    One validated foreign key proves nothing about the others. Each is a
    separate auto-generated lookup over every organization's rows.
    """
    client = api_for(org_a.hr)
    payload = _employee_payload(org_a)
    payload[field] = str(
        {
            "designation_id": org_b.designation,
            "location_id": org_b.location,
            "level_id": org_b.level,
        }[field].pk
    )
    payload["email"] = f"injected-{field}@acme-health.example"
    payload["personal_email"] = f"injected-{field}@example.test"

    response = client.post("/api/v1/employees/", payload, format="json")

    assert response.status_code in (400, 404), (
        f"{field}: expected refusal, got {response.status_code}: "
        f"{response.content[:300]}"
    )


def test_a_leave_request_cannot_borrow_another_organizations_leave_type(
    org_a, org_b, api_for
):
    client = api_for(org_a.worker)

    response = client.post(
        "/api/v1/leave-requests/",
        {
            "leave_type": str(org_b.rows["leave_type"].pk),
            "start_date": "2025-07-01",
            "end_date": "2025-07-01",
            "reason": "Borrowed another company's leave type",
        },
        format="json",
    )

    assert response.status_code in (400, 404), (
        f"expected refusal, got {response.status_code}: {response.content[:300]}"
    )
    # Refused for the RIGHT reason. This case used to pass on a coincidence:
    # the serializer resolved the other company's leave type without complaint,
    # and the request only failed later because no leave policy linked it to
    # this employee. A business rule was standing in for a tenancy check, and
    # would have stopped standing in the first time the data lined up.
    assert "leave_type" in response.content.decode().lower(), (
        f"refused, but not because of the leave type: {response.content[:300]}"
    )
    assert "policy" not in response.content.decode().lower(), (
        "refused by the leave-policy rule, not by the tenant scope"
    )


def test_the_same_payload_succeeds_with_its_own_organizations_rows(org_a, api_for):
    """
    Pairs every refusal above with the permission it must leave intact -- a
    system that rejects all writes would pass this file while being broken.
    """
    client = api_for(org_a.hr)

    response = client.post(
        "/api/v1/employees/",
        _employee_payload(org_a),
        format="json",
    )

    assert response.status_code == 201, (
        f"A's HR cannot create an employee in A: {response.status_code} "
        f"{response.content[:300]}"
    )
    from apps.employees.models import Employee

    from .conftest import across_organizations

    with across_organizations():
        created = Employee.objects.all_orgs().get(first_name="Injected")
    assert created.organization_id == org_a.organization.pk
    assert dt.date(2025, 1, 6) == created.date_of_joining


def test_a_payroll_run_cannot_be_scoped_to_another_organizations_location(
    org_a, org_b, api_for
):
    """
    `location` on run creation is read by hand in the view, not by a serializer
    field, so the relation scoping mixin never saw it.

    Before the organization app filtered at the manager it resolved another
    company's location and stored it on A's run. After, the lookup finds
    nothing -- and `None` means an ORGANIZATION-WIDE run, so an unchecked miss
    would silently widen a location run to every employee. Both are wrong; the
    only right answer is a refusal naming the field.
    """
    from apps.payroll.models import PayrollRun

    client = api_for(org_a.admin)

    foreign = client.post(
        "/api/v1/payroll/runs/",
        {"period_year": 2025, "period_month": 8, "location": str(org_b.location.pk)},
        format="json",
    )

    assert foreign.status_code == 400, (
        f"expected refusal, got {foreign.status_code}: {foreign.content[:300]}"
    )
    assert "location" in foreign.content.decode().lower()
    from .conftest import across_organizations

    with across_organizations():
        assert not PayrollRun.objects.all_orgs().filter(
            organization=org_a.organization, period_year=2025, period_month=8
        ).exists(), "a run was created despite the refusal"

    # Positive control: the same request with A's own location.
    own = client.post(
        "/api/v1/payroll/runs/",
        {"period_year": 2025, "period_month": 8, "location": str(org_a.location.pk)},
        format="json",
    )
    assert own.status_code == 201, (
        f"A cannot create a run for its own location: {own.status_code} {own.content[:300]}"
    )
    with across_organizations():
        run = PayrollRun.objects.all_orgs().get(pk=own.json()["id"])
    assert run.location_id == org_a.location.pk
