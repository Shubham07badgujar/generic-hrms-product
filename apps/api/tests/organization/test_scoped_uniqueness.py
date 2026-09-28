"""
Business keys repeat across organizations, and never within one.

Two companies must both be able to have an EMP001, a department coded HR and a
leave type called CL. This is not a convenience: a uniqueness rule that spans
organizations tells the second customer the value is taken, which leaks the
existence of another tenant's data through an error message on a form.

`access.E008` proves the constraints are DECLARED correctly. These prove the
database actually behaves that way -- a constraint can be shaped right and
still be wrong about which rows it compares.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from apps.organization.models import Organization, OrgStatus
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def two_orgs(db):
    a = Organization.objects.create(name="Acme", slug="acme-u", status=OrgStatus.ACTIVE)
    b = Organization.objects.create(name="Globex", slug="globex-u", status=OrgStatus.ACTIVE)
    return a, b


def _department(code="HR"):
    from apps.organization.models import Department
    from core.access.catalog import DepartmentKind

    return Department.objects.create(name=code, code=code, kind=DepartmentKind.HR)


# ------------------------------------------------------- the same key, twice


@pytest.mark.parametrize(
    "make,label",
    [
        (lambda: _department("HR"), "department code"),
        (
            lambda: __import__("apps.organization.models", fromlist=["Location"]).Location.objects.create(
                name="Head Office", code="HO", city="Pune", state="MH"
            ),
            "location code",
        ),
        (
            lambda: __import__("apps.leave.models", fromlist=["LeaveType"]).LeaveType.objects.create(
                name="Casual Leave", code="CL"
            ),
            "leave type code",
        ),
        (
            lambda: __import__("apps.payroll.models", fromlist=["SalaryComponent"]).SalaryComponent.objects.create(
                name="Basic", code="BASIC", component_type="earning"
            ),
            "salary component code",
        ),
        (
            lambda: __import__("apps.assets.models", fromlist=["AssetCategory"]).AssetCategory.objects.create(
                name="Laptops", code="LAPTOP"
            ),
            "asset category code",
        ),
    ],
)
def test_two_organizations_can_use_the_same_key(two_orgs, make, label):
    a, b = two_orgs

    with acting_as(None, organization=a):
        make()
    with acting_as(None, organization=b):
        make()  # must not raise

    assert True, label


def test_two_organizations_can_both_have_employee_one(two_orgs):
    """
    The example from the brief, and the one a customer notices first.
    """
    from apps.employees.models import Employee

    a, b = two_orgs
    made = []
    for org in (a, b):
        with acting_as(None, organization=org):
            dept = _department("HR")
            made.append(
                Employee.objects.create(
                    employee_code="EMP001",
                    first_name="Sam",
                    department=dept,
                    date_of_joining=dt.date(2025, 1, 1),
                )
            )

    assert [e.employee_code for e in made] == ["EMP001", "EMP001"]
    assert made[0].organization_id != made[1].organization_id


# ----------------------------------------------- and still unique within one


def test_one_organization_still_cannot_repeat_a_key(two_orgs):
    """The scoping must not have loosened the rule, only narrowed it."""
    a, _ = two_orgs

    with acting_as(None, organization=a):
        _department("HR")
        with pytest.raises(IntegrityError), transaction.atomic():
            _department("HR")


def test_one_organization_still_cannot_repeat_an_employee_code(two_orgs):
    from apps.employees.models import Employee

    a, _ = two_orgs
    with acting_as(None, organization=a):
        dept = _department("HR")
        Employee.objects.create(
            employee_code="EMP001", first_name="Sam", department=dept,
            date_of_joining=dt.date(2025, 1, 1),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Employee.objects.create(
                employee_code="EMP001", first_name="Other", department=dept,
                date_of_joining=dt.date(2025, 1, 1),
            )


def test_each_organization_gets_its_own_default_shift_rule(two_orgs):
    """
    The old constraint allowed ONE default shift rule in the whole database,
    because it was unique over `location` where location IS NULL. Every
    organization needs its own.
    """
    from apps.attendance.models import ShiftRule

    a, b = two_orgs
    for org in (a, b):
        with acting_as(None, organization=org):
            ShiftRule.objects.create(
                location=None, start_time=dt.time(9, 0), end_time=dt.time(18, 0)
            )

    # Counted ACROSS organizations on purpose -- the whole claim is that two
    # rows now coexist where the old constraint allowed one. Attendance filters
    # at the manager AND the database confines the connection, so a deliberate
    # cross-organization count has to say so twice over.
    from tests.conftest import across_organizations

    with across_organizations():
        assert ShiftRule.objects.all_orgs().filter(location__isnull=True).count() == 2

    with acting_as(None, organization=a):
        with pytest.raises(IntegrityError), transaction.atomic():
            ShiftRule.objects.create(
                location=None, start_time=dt.time(10, 0), end_time=dt.time(19, 0)
            )
