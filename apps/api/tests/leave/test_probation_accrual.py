"""
Paid leave does not accrue during probation.

Entitlement is an HR decision that lands with confirmation: nothing accrues
while probation is undecided, accrual then starts from the confirmation
month, and a balance that accrued under the old rule reconciles DOWN with a
ledger row — never below what is already used or pending. Confirmed
employees' arithmetic is untouched, and a policy can opt probationers in.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.employees.models import Employee, ProbationStatus
from apps.leave.models import LeavePolicy, LeaveTransaction, LeaveType
from apps.leave.services import accrued_target, balance_for, ensure_balances

pytestmark = pytest.mark.django_db

YEAR = 2026
TODAY = dt.date(2026, 9, 2)


@pytest.fixture
def leave_setup(db):
    pl = LeaveType.objects.create(code="PL", name="Privilege Leave", is_paid=True)
    cl = LeaveType.objects.create(code="CL", name="Casual Leave", is_paid=True)
    policies = {
        "PL": LeavePolicy.objects.create(
            leave_type=pl, name="PL policy",
            annual_allocation=Decimal("18"), accrual_per_month=Decimal("1.5"),
        ),
        "CL": LeavePolicy.objects.create(
            leave_type=cl, name="CL policy", annual_allocation=Decimal("6"),
        ),
    }
    return {"PL": pl, "CL": cl, "policies": policies}


def _employee(org, code, *, joined, probation, confirmed=None):
    return Employee.objects.create(
        employee_code=code, first_name=code,
        department=org["departments"]["operations"],
        date_of_joining=joined, probation_status=probation,
        confirmation_date=confirmed,
    )


def test_probation_accrues_nothing(leave_setup, org, monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: TODAY)
    person = _employee(org, "EMP07800", joined=dt.date(2026, 8, 10),
                       probation=ProbationStatus.ACTIVE)

    for code in ("PL", "CL"):
        assert accrued_target(leave_setup["policies"][code], person, YEAR) == 0
    balances = ensure_balances(person, year=YEAR)
    assert all(balance.available == 0 for balance in balances)


def test_confirmation_starts_accrual_from_the_confirmation_month(leave_setup, org, monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: dt.date(2026, 11, 15))
    person = _employee(org, "EMP07801", joined=dt.date(2026, 2, 1),
                       probation=ProbationStatus.CONFIRMED,
                       confirmed=dt.date(2026, 8, 1))

    # Aug–Nov = 4 months × 1.5 — NOT ten months back to February.
    assert accrued_target(leave_setup["policies"]["PL"], person, YEAR) == Decimal("6.0")
    # Up-front CL: full annual once confirmed.
    assert accrued_target(leave_setup["policies"]["CL"], person, YEAR) == Decimal("6")


def test_confirmed_and_not_applicable_employees_are_untouched(leave_setup, org, monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: TODAY)
    veteran = _employee(org, "EMP07802", joined=dt.date(2020, 1, 1),
                        probation=ProbationStatus.NOT_APPLICABLE)

    # Jan–Sep = 9 months × 1.5 — exactly the pre-change arithmetic.
    assert accrued_target(leave_setup["policies"]["PL"], veteran, YEAR) == Decimal("13.5")
    assert accrued_target(leave_setup["policies"]["CL"], veteran, YEAR) == Decimal("6")


def test_a_wrongly_accrued_balance_reconciles_down_with_a_ledger_row(
    leave_setup, org, monkeypatch
):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: TODAY)
    person = _employee(org, "EMP07803", joined=dt.date(2026, 8, 10),
                       probation=ProbationStatus.ACTIVE)

    # The balance as the old rule left it: accrued from joining.
    balance = balance_for(person, leave_setup["PL"], YEAR)
    balance.allocated = Decimal("3.0")
    balance.save(update_fields=["allocated"])

    refreshed = balance_for(person, leave_setup["PL"], YEAR)
    assert refreshed.allocated == 0
    assert refreshed.available == 0
    adjustment = LeaveTransaction.objects.filter(
        employee=person, kind="adjustment", days=Decimal("-3.0")
    ).first()
    assert adjustment is not None
    assert "probation" in adjustment.note.lower()


def test_a_policy_may_explicitly_opt_probationers_in(leave_setup, org, monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(timezone, "localdate", lambda: TODAY)
    policy = leave_setup["policies"]["PL"]
    policy.accrues_during_probation = True
    policy.save(update_fields=["accrues_during_probation"])

    person = _employee(org, "EMP07804", joined=dt.date(2026, 8, 10),
                       probation=ProbationStatus.ACTIVE)
    # Aug–Sep = 2 months × 1.5, from joining — the explicit HR choice.
    assert accrued_target(policy, person, YEAR) == Decimal("3.0")
