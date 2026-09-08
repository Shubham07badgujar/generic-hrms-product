"""
Who may configure payroll, per the approved split:

  - HR Head and Finance Head CONFIGURE rules (rates, components, structures);
  - Finance Head alone CERTIFIES rates and RELEASES money;
  - the Payroll Executive drafts adjustments but can approve nothing.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.employees.models import Employee

pytestmark = pytest.mark.django_db


@pytest.fixture
def people(make_user, org):
    out = {}
    for index, role in enumerate(["hr_head", "finance_head", "payroll_executive"]):
        user = make_user(role)
        Employee.objects.create(
            employee_code=f"EMP0770{index}", first_name=role.replace("_", " ").title(),
            user=user,
            department=org["departments"]["hr" if role == "hr_head" else "finance"],
            date_of_joining=dt.date(2024, 1, 1),
        )
        out[role] = user
    return out


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


PF_DRAFT = {
    "statute": "pf",
    "rule_version": "pf.v1",
    "effective_from": "2026-10-01",
    "parameters": {
        "employee_rate": "12", "employer_rate": "12", "eps_rate": "8.33",
        "wage_ceiling": "15000", "eps_wage_ceiling": "15000",
    },
    "source_citation": "EPF Act — test values",
    "retrieved_on": "2026-09-01",
}


def test_hr_head_configures_rates_but_only_finance_certifies(people):
    hr = _client(people["hr_head"])
    finance = _client(people["finance_head"])

    created = hr.post("/api/v1/payroll/rule-sets/", PF_DRAFT, format="json")
    assert created.status_code == 201, created.content
    rule_set_id = created.json()["id"]

    submitted = hr.post(f"/api/v1/payroll/rule-sets/{rule_set_id}/submit/", {}, format="json")
    assert submitted.status_code == 200, submitted.content

    # HR Head cannot certify — that authority is the Finance Head's alone.
    refused = hr.post(f"/api/v1/payroll/rule-sets/{rule_set_id}/verify/", {}, format="json")
    assert refused.status_code == 403

    verified = finance.post(
        f"/api/v1/payroll/rule-sets/{rule_set_id}/verify/",
        {"note": "Matched against the circular."}, format="json",
    )
    assert verified.status_code == 200, verified.content
    assert verified.json()["verification_status"] == "verified"


def test_hr_head_maintains_components_and_structures(people, org):
    hr = _client(people["hr_head"])

    basic = hr.post("/api/v1/payroll/components/", {
        "code": "BASIC", "name": "Basic", "component_type": "earning",
        "calc_type": "fixed", "is_wage": True,
    }, format="json")
    assert basic.status_code == 201, basic.content

    worker = Employee.objects.create(
        employee_code="EMP07710", first_name="Paid Person",
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )
    structure = hr.post("/api/v1/payroll/structures/", {
        "employee": str(worker.pk), "ctc_annual": "600000.00",
        "valid_from": "2026-09-01", "revision_reason": "Initial structure",
        "lines": [{"component": basic.json()["id"], "value": "30000"}],
    }, format="json")
    assert structure.status_code == 201, structure.content
    assert structure.json()["monthly_gross"] == "30000.00"

    # A revision keeps history: the old row closes the day before.
    revised = hr.post("/api/v1/payroll/structures/", {
        "employee": str(worker.pk), "ctc_annual": "720000.00",
        "valid_from": "2026-11-01", "revision_reason": "Raise",
        "lines": [{"component": basic.json()["id"], "value": "36000"}],
    }, format="json")
    assert revised.status_code == 201
    rows = hr.get("/api/v1/payroll/structures/", {"employee": str(worker.pk)}).json()["data"]
    assert len(rows) == 2
    closed = next(r for r in rows if r["valid_to"])
    assert closed["valid_to"] == "2026-10-31"


def test_a_previous_month_can_be_back_filled_but_history_never_rewritten(people, org):
    """
    The first structure started in September; August pay was still owed.

    A structure dated BEFORE the whole history is born closed — it ends the
    day the existing history begins — so an earlier month becomes payable
    without anything already in force moving. A date already covered is
    refused outright.
    """
    hr = _client(people["hr_head"])
    basic = hr.post("/api/v1/payroll/components/", {
        "code": "BASIC", "name": "Basic", "component_type": "earning",
        "calc_type": "fixed", "is_wage": True,
    }, format="json").json()

    worker = Employee.objects.create(
        employee_code="EMP07730", first_name="Backfill Person",
        department=org["departments"]["operations"], date_of_joining=dt.date(2026, 8, 1),
    )

    def structure(valid_from, amount):
        return hr.post("/api/v1/payroll/structures/", {
            "employee": str(worker.pk), "ctc_annual": "360000.00",
            "valid_from": valid_from, "revision_reason": "test",
            "lines": [{"component": basic["id"], "value": amount}],
        }, format="json")

    assert structure("2026-09-01", "30000").status_code == 201

    backfilled = structure("2026-08-01", "28000")
    assert backfilled.status_code == 201, backfilled.content
    assert backfilled.json()["valid_to"] == "2026-08-31"  # closes where history begins

    rows = hr.get("/api/v1/payroll/structures/", {"employee": str(worker.pk)}).json()["data"]
    current = next(r for r in rows if r["valid_to"] is None)
    assert current["valid_from"] == "2026-09-01"  # untouched by the back-fill

    # A month already covered is history and stays history.
    refused = structure("2026-09-15", "31000")
    assert refused.status_code == 201  # forward revision — allowed as before
    refused = structure("2026-09-01", "31000")
    assert refused.status_code == 400
    refused = structure("2026-08-15", "29000")
    assert refused.status_code == 400


def test_the_payroll_executive_drafts_but_heads_approve(people, org):
    executive = _client(people["payroll_executive"])
    hr = _client(people["hr_head"])

    worker = Employee.objects.create(
        employee_code="EMP07720", first_name="Bonus Person",
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )
    draft = executive.post("/api/v1/payroll/adjustments/", {
        "employee": str(worker.pk), "kind": "bonus", "label": "Festival bonus",
        "amount": "5000.00", "period_month": 10, "period_year": 2026,
    }, format="json")
    assert draft.status_code == 201, draft.content
    adjustment_id = draft.json()["id"]

    refused = executive.post(f"/api/v1/payroll/adjustments/{adjustment_id}/approve/", {}, format="json")
    assert refused.status_code == 403

    approved = hr.post(f"/api/v1/payroll/adjustments/{adjustment_id}/approve/", {}, format="json")
    assert approved.status_code == 200, approved.content
    assert approved.json()["status"] == "approved"

    # And the executive can never touch the rates.
    assert executive.post("/api/v1/payroll/rule-sets/", PF_DRAFT, format="json").status_code == 403
