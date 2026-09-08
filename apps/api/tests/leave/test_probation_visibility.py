"""
What a probationer's leave page OFFERS.

The service has always coerced a probationer's leave to unpaid — but the API
still offered them paid types and "available" paid balances, a promise the
policy does not keep. The rule now: the applicant on probation is shown only
what they can actually take; HR's view of the same person is untouched.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.employees.models import Employee, EmployeeStatus
from apps.leave.seeds import seed_leave

pytestmark = pytest.mark.django_db

TYPES = "/api/v1/leave-types/"
BALANCES = "/api/v1/leave-balances/"


@pytest.fixture
def leave_config(db):
    return seed_leave()


def _person(make_user, org, roles, *, code, role, status):
    user = make_user(role, email=f"{code.lower()}@example.test")
    return Employee.objects.create(
        employee_code=code,
        first_name=code,
        user=user,
        department=org["departments"]["operations"],
        location=org["location"],
        date_of_joining=dt.date(2026, 7, 1),
        status=status,
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_a_probationer_is_offered_only_unpaid_leave(leave_config, make_user, org, roles):
    prob = _person(make_user, org, roles, code="EMP09101", role="employee",
                   status=EmployeeStatus.ON_PROBATION)
    client = _client(prob.user)

    types = client.get(TYPES).json()["data"]
    assert types, "the apply form still needs at least one option"
    assert all(row["is_paid"] is False for row in types)

    balances = client.get(BALANCES).json()["data"]
    # Nothing paid is shown as "available" to a person whose leave is unpaid.
    assert all(row["is_paid"] is False for row in balances)


def test_a_confirmed_employee_still_sees_paid_types_and_balances(
    leave_config, make_user, org, roles
):
    emp = _person(make_user, org, roles, code="EMP09102", role="employee",
                  status=EmployeeStatus.CONFIRMED)
    client = _client(emp.user)

    types = client.get(TYPES).json()["data"]
    assert any(row["is_paid"] for row in types)

    balances = client.get(BALANCES).json()["data"]
    names = {row["leave_type_name"] for row in balances}
    assert any("Paid" in n or "Sick" in n for n in names)


def test_hr_still_sees_the_probationers_paid_balances(leave_config, make_user, org, roles):
    """The rows exist and accrue; only the PROBATIONER's own view hides them."""
    prob = _person(make_user, org, roles, code="EMP09103", role="employee",
                   status=EmployeeStatus.ON_PROBATION)
    hr = _person(make_user, org, roles, code="EMP09104", role="hr_head",
                 status=EmployeeStatus.CONFIRMED)

    # Materialise the probationer's rows by touching their own list first.
    _client(prob.user).get(BALANCES)

    rows = _client(hr.user).get(BALANCES, {"employee": str(prob.pk)}).json()["data"]
    names = {row["leave_type_name"] for row in rows}
    assert any("Paid" in n or "Sick" in n for n in names)