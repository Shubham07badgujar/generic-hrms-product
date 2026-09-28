"""
Seat limits, where employees are actually created.

The service-level rules are covered in `test_plans.py`. This is about the call
site: does hiring the twenty-sixth employee on a twenty-five seat plan actually
fail, does it fail with something the SPA can act on, and does the lock that
prevents two concurrent hires from both succeeding actually get taken.

WHY 422 AND NOT 403

Nobody typed anything wrong, so it is not a validation error. The caller is
fully entitled to hire, so it is not a permission denial. The system will not
allow it YET, which is what 422 means -- and running out of seats is the one
business rule with an obvious next action, so it carries its own code rather
than the generic one.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.platform.models import Plan
from apps.platform.services.provisioning import provision_organization
from apps.platform.services.subscriptions import SeatLimitReached, reserve_seats

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "seat-password-12345"


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)
    return {p.code: p for p in Plan.objects.all()}


@pytest.fixture
def company(plans):
    """Starter: 25 seats, with the structure a hire needs."""
    from apps.organization.models import Department, Designation, EmployeeLevel, Location
    from core.access.catalog import DepartmentKind, Layer
    from core.middleware import acting_as

    result = provision_organization(
        name="Northwind Health",
        slug="northwind",
        admin_email="admin@northwind.example",
        plan=plans["starter"],
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])

    with acting_as(None, organization=result.organization):
        result.department = Department.objects.create(
            name="People", code="HR", kind=DepartmentKind.HR
        )
        result.location = Location.objects.create(name="HQ", code="HO")
        result.designation = Designation.objects.create(
            title="Officer", department=None
        )
        result.level = EmployeeLevel.objects.create(
            name="Staff", code="L5", layer=Layer.STAFF
        )
        # A staff-level hire must report to somebody -- only department heads
        # may have no manager -- and that somebody must hold an ACTIVE ROLE,
        # because approvals routed to a role-less manager would go nowhere. So
        # the fixture builds a real principal rather than a bare Employee row.
        # They occupy a seat like anyone else, which is why the arithmetic in
        # each test allows for them.
        head_level = EmployeeLevel.objects.create(
            name="Head", code="L2", layer=Layer.DEPARTMENT_HEAD
        )
        from apps.accounts.models import Role, User, UserRole
        from apps.employees.models import Employee
        from apps.organization.models import OrganizationMembership

        head_user = User.objects.create_user(
            email="head@northwind.example", password=PASSWORD, first_name="Dept"
        )
        UserRole.objects.create(
            user=head_user,
            role=Role.objects.get(organization=result.organization, code="hr_head"),
        )
        OrganizationMembership.objects.create(
            organization=result.organization, user=head_user
        )
        result.manager = Employee.objects.create(
            employee_code="MGR001",
            user=head_user,
            first_name="Dept",
            last_name="Head",
            department=result.department,
            designation=result.designation,
            location=result.location,
            level=head_level,
            date_of_joining=dt.date(2024, 1, 1),
        )
    return result


def _fill(company, count):
    """Put `count` active employees in, without going through the service."""
    from apps.employees.models import Employee
    from core.middleware import acting_as

    with acting_as(None, organization=company.organization):
        for i in range(count):
            Employee.objects.create(
                employee_code=f"F{i:04d}",
                first_name="Filler",
                last_name=f"Person{i}",
                department=company.department,
                designation=company.designation,
                location=company.location,
                level=company.level,
                date_of_joining=dt.date(2024, 1, 1),
            )


def _hire(company, *, local="newjoiner"):
    """
    Hire through the real service, with the organization BOUND.

    A request binds it while resolving the caller's context; a direct service
    call does not, and this file opts out of the suite's autouse binding
    because a test about tenancy must not inherit its tenant. So the binding
    is explicit here, exactly as a management command would have to do it.
    """
    from apps.employees.services.creation import create_employee
    from core.middleware import acting_as

    with acting_as(company.admin, organization=company.organization):
        return create_employee(
            actor=company.admin,
            first_name="New",
            last_name="Joiner",
            email=f"{local}@northwind.example",
            role_code="employee",
            department_id=company.department.pk,
            designation_id=company.designation.pk,
            location_id=company.location.pk,
            level_id=company.level.pk,
            reporting_manager_id=company.manager.pk,
            date_of_joining=dt.date(2025, 1, 1),
            send_welcome_email=False,
            start_onboarding_checklist=False,
        )


def test_hiring_under_the_limit_works(company):
    """The positive control. Without it the refusal below proves nothing."""
    _fill(company, 10)
    result = _hire(company)
    assert result.employee.pk


def test_hiring_at_the_limit_is_refused(company):
    _fill(company, 24)  # + the fixture's manager = 25
    with pytest.raises(SeatLimitReached) as excinfo:
        _hire(company)

    assert excinfo.value.limit == 25
    assert excinfo.value.current == 25


def test_the_refusal_is_422_with_its_own_code(company):
    """
    Over HTTP, because the status code and the error code are the whole point:
    the SPA shows an upgrade prompt for this and a field error for a 400.
    """
    _fill(company, 24)  # + the fixture's manager = 25

    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/auth/login/",
        {"email": company.admin.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])

    created = client.post(
        "/api/v1/employees/",
        {
            "first_name": "New",
            "last_name": "Joiner",
            "email": "overflow@northwind.example",
            "personal_email": "overflow.personal@example.com",
            "role_code": "employee",
            "department_id": str(company.department.pk),
            "designation_id": str(company.designation.pk),
            "location_id": str(company.location.pk),
            "level_id": str(company.level.pk),
            "reporting_manager_id": str(company.manager.pk),
            "date_of_joining": "2025-01-01",
        },
        format="json",
    )
    assert created.status_code == 422, created.content[:300]
    assert created.json()["error"]["code"] == "seat_limit_reached"


def test_the_seat_check_runs_before_the_references_are_validated(company):
    """
    Running out of seats is not a data problem, and the caller should hear
    about it without first being told their designation id is wrong.
    """
    _fill(company, 24)  # + the fixture's manager = 25
    from apps.employees.services.creation import create_employee
    from core.middleware import acting_as

    with pytest.raises(SeatLimitReached), acting_as(
        company.admin, organization=company.organization
    ):
        create_employee(
            actor=company.admin,
            first_name="New",
            last_name="Joiner",
            email="badrefs@northwind.example",
            role_code="employee",
            department_id="00000000-0000-0000-0000-000000000000",
            date_of_joining=dt.date(2025, 1, 1),
            send_welcome_email=False,
        )


def test_a_refused_hire_creates_nothing(company):
    """
    `create_employee` is atomic, so a refusal must leave no login behind --
    otherwise the email is taken and the retry after an upgrade fails too.
    """
    from apps.accounts.models import User

    _fill(company, 24)  # + the fixture's manager = 25
    before = User.objects.count()

    with pytest.raises(SeatLimitReached):
        _hire(company, local="ghost")

    assert User.objects.count() == before
    assert not User.objects.filter(email="ghost@northwind.example").exists()


def test_the_subscription_row_is_actually_locked(company):
    """
    Guards the mechanism, not just the arithmetic.

    The lock is the entire reason this is a service rather than a `count()` at
    the call site: without it two concurrent hires both read limit-1, both
    decide there is room, and both commit. A refactor could keep every
    assertion above passing while dropping `select_for_update`, and the only
    symptom would be an occasional over-limit organization nobody can explain.
    """
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured, transaction.atomic():
        reserve_seats(company.organization)

    locked = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"]]
    assert locked, (
        "reserve_seats issued no locking read. Two concurrent hires can now "
        "both pass the seat check.\n"
        + "\n".join(q["sql"][:120] for q in captured.captured_queries)
    )
    assert any("subscription" in sql.lower() for sql in locked), locked


def test_an_unlimited_plan_does_not_refuse(company, plans):
    from apps.platform.services.subscriptions import change_plan

    _fill(company, 24)  # + the fixture's manager = 25
    change_plan(company.organization, plan=plans["enterprise"])
    assert _hire(company).employee.pk


def test_a_deployment_with_no_plans_does_not_refuse(company):
    """
    The self-hosted case. Deleting the subscription leaves the organization
    unlimited rather than unable to hire.
    """
    from apps.platform.models import Subscription

    from tests.conftest import across_organizations

    # Removing the customer's subscription is billing's doing, not theirs.
    with across_organizations():
        Subscription.objects.filter(organization=company.organization).delete()
    _fill(company, 200)
    assert _hire(company).employee.pk


def test_a_removed_employee_frees_their_seat(company):
    """
    The limit counts ACTIVE employees. A company at its limit that offboards
    somebody can hire their replacement -- otherwise the only way to backfill
    would be to upgrade.
    """
    from apps.employees.models import Employee
    from core.models import org_scoped

    _fill(company, 24)  # + the fixture's manager = 25
    with pytest.raises(SeatLimitReached):
        _hire(company, local="blocked")

    # Not the manager: deactivating them would make the backfill fail for a
    # hierarchy reason and quietly stop testing the seat rule.
    from core.middleware import acting_as

    with acting_as(None, organization=company.organization):
        leaver = (
            org_scoped(Employee, company.organization)
            .filter(is_active=True)
            .exclude(pk=company.manager.pk)
            .first()
        )
        assert leaver is not None, "nobody to offboard, so the backfill proves nothing"
        leaver.is_active = False
        leaver.save(update_fields=["is_active"])

    assert _hire(company, local="backfill").employee.pk


def test_one_organizations_headcount_does_not_consume_anothers_seats(
    company, plans
):
    """
    The count is organization-scoped. Unscoped, the first customer to reach 25
    employees would exhaust every customer's Starter plan at once.
    """
    other = provision_organization(
        name="Aperture Systems",
        slug="aperture",
        admin_email="admin@aperture.example",
        plan=plans["starter"],
    )
    _fill(company, 24)  # + the fixture's manager = 25

    # The crowded organization is refused...
    with pytest.raises(SeatLimitReached):
        reserve_seats(company.organization)

    # ...and the empty one is not.
    assert reserve_seats(other.organization) is not None
