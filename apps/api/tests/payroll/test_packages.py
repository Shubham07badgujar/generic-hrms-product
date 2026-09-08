"""
Custom package schedules — the twelve behaviours the specification demands.

The package is the agreement; payroll is the only thing that pays. These
tests hold that line: validation to the rupee, deferred amounts entering
payroll only as an approved DEFERRED_RELEASE line, and history that no
later edit can restate.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.payroll.models import (
    DeferralStatus, EmployeePackage, PackageStatus, PayrollAdjustment,
)
from apps.payroll.services import packages as svc

pytestmark = pytest.mark.django_db

L = Decimal


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def actors(finance):
    return {
        "hr": finance["hr_head"].user,
        "fin": finance["finance_head"].user,
        "exec": finance["payroll_executive"].user,
        "worker": finance["therapist"],
    }


def _employee_b_payload(worker, **overrides):
    """The spec's Employee B: 24L = 18L monthly (1.5L x 12) + 6L deferred."""
    payload = {
        "employee": str(worker.pk),
        "total_amount": "2400000.00",
        "package_type": "monthly_plus_deferred",
        "start_date": "2026-04-01",
        "end_date": "2027-03-31",
        "notes": "Internal: negotiated with a service expectation.",
        "periods": [{
            "label": "Months 1-12", "start_date": "2026-04-01",
            "end_date": "2027-03-31", "amount": "1800000.00",
        }],
        "deferrals": [{
            "label": "Completion amount", "amount": "600000.00",
            "condition_type": "after_months", "condition_months": 12,
        }],
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------- 1 & 2: period arithmetic


def test_year_wise_package_computes_each_years_monthly(actors):
    """Employee A: 24L over 2 years — 10L then 14L, never total/24."""
    client = _client(actors["hr"])
    response = client.post("/api/v1/payroll/packages/", {
        "employee": str(actors["worker"].pk),
        "total_amount": "2400000.00", "package_type": "year_wise",
        "start_date": "2026-04-01", "end_date": "2028-03-31",
        "periods": [
            {"label": "Year 1", "start_date": "2026-04-01", "end_date": "2027-03-31",
             "amount": "1000000.00"},
            {"label": "Year 2", "start_date": "2027-04-01", "end_date": "2028-03-31",
             "amount": "1400000.00"},
        ],
    }, format="json")
    assert response.status_code == 201, response.content
    periods = {p["label"]: p for p in response.json()["periods"]}
    assert periods["Year 1"]["monthly_amount"] == "83333.33"
    assert periods["Year 2"]["monthly_amount"] == "116666.67"
    assert response.json()["status"] == "draft"


def test_any_number_of_custom_periods(actors):
    client = _client(actors["fin"])
    response = client.post("/api/v1/payroll/packages/", {
        "employee": str(actors["worker"].pk),
        "total_amount": "300000.00", "package_type": "period_wise",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "periods": [
            {"label": f"Q{q}", "start_date": f"2026-{q * 3 - 2:02d}-01",
             "end_date": f"2026-{q * 3:02d}-28", "amount": "75000.00"}
            for q in (1, 2, 3, 4)
        ],
    }, format="json")
    assert response.status_code == 201
    assert len(response.json()["periods"]) == 4


# ------------------------------------------- 3, 10, 11: allocation guard


def test_activation_refuses_over_and_under_allocation(actors):
    client = _client(actors["hr"])
    over = client.post("/api/v1/payroll/packages/",
                       _employee_b_payload(actors["worker"], deferrals=[{
                           "label": "Completion", "amount": "800000.00",
                           "condition_type": "after_months", "condition_months": 12,
                       }]), format="json")
    package_id = over.json()["id"]
    refused = client.post(f"/api/v1/payroll/packages/{package_id}/activate/", {}, format="json")
    assert refused.status_code == 400
    assert b"exceeds total package amount" in refused.content

    under = client.patch(f"/api/v1/payroll/packages/{package_id}/", {
        **_employee_b_payload(actors["worker"], deferrals=[{
            "label": "Completion", "amount": "400000.00",
            "condition_type": "after_months", "condition_months": 12,
        }]),
    }, format="json")
    assert under.status_code == 200
    refused = client.post(f"/api/v1/payroll/packages/{package_id}/activate/", {}, format="json")
    assert refused.status_code == 400
    assert b"has not been allocated" in refused.content

    balanced = client.patch(f"/api/v1/payroll/packages/{package_id}/",
                            _employee_b_payload(actors["worker"]), format="json")
    assert balanced.status_code == 200
    activated = client.post(f"/api/v1/payroll/packages/{package_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    body = activated.json()
    assert body["status"] == "active"
    # Eligibility computed from start date + 12 months.
    assert body["deferrals"][0]["eligible_on"] == "2027-04-01"
    # The summary keeps the concepts separate — nothing counted twice.
    assert body["summary"]["deferred_total"] == "600000.00"
    assert body["summary"]["released_total"] == "0.00"
    assert body["summary"]["paid_so_far"] == "0.00"


# ------------------------------------------------ permissions (who may what)


def test_only_hr_and_finance_heads_configure_packages(actors):
    payload = _employee_b_payload(actors["worker"])
    assert _client(actors["exec"]).post(
        "/api/v1/payroll/packages/", payload, format="json"
    ).status_code == 403

    created = _client(actors["fin"]).post("/api/v1/payroll/packages/", payload, format="json")
    assert created.status_code == 201
    package_id = created.json()["id"]

    # The executive VIEWS the schedule but cannot activate or decide.
    listed = _client(actors["exec"]).get("/api/v1/payroll/packages/")
    assert listed.status_code == 200 and len(listed.json()["data"]) >= 1
    assert _client(actors["exec"]).post(
        f"/api/v1/payroll/packages/{package_id}/activate/", {}, format="json"
    ).status_code == 403


def test_employee_sees_own_package_without_internal_notes(actors):
    client = _client(actors["hr"])
    created = client.post("/api/v1/payroll/packages/", _employee_b_payload(actors["worker"]),
                          format="json")
    client.post(f"/api/v1/payroll/packages/{created.json()['id']}/activate/", {}, format="json")

    own = _client(actors["worker"].user).get("/api/v1/payroll/me/")
    package = own.json()["package"]
    assert package is not None
    assert package["total_amount"] == "2400000.00"
    assert "notes" not in package  # internal HR/Finance notes never leak

    # And the list endpoint scopes to self: another employee's package invisible.
    rows = _client(actors["worker"].user).get("/api/v1/payroll/packages/").json()["data"]
    assert all(r["employee_code"] == actors["worker"].employee_code for r in rows)


# ------------------------------------ 8 & 9: eligibility and release approval


@pytest.fixture
def eligible_package(actors):
    client = _client(actors["hr"])
    payload = _employee_b_payload(actors["worker"],
                                  start_date="2025-04-01", end_date="2026-03-31")
    payload["periods"][0].update(start_date="2025-04-01", end_date="2026-03-31")
    created = client.post("/api/v1/payroll/packages/", payload, format="json")
    client.post(f"/api/v1/payroll/packages/{created.json()['id']}/activate/", {}, format="json")
    return EmployeePackage.objects.get(pk=created.json()["id"])


def test_a_completed_period_becomes_eligible_not_paid(eligible_package):
    deferral = eligible_package.deferrals.get()
    assert deferral.eligible_on == dt.date(2026, 4, 1)  # 12 months after start
    assert deferral.status == DeferralStatus.ELIGIBLE   # date has passed
    # Nothing was paid: no adjustment exists until a head approves.
    assert deferral.released_in_adjustment_id is None
    assert PayrollAdjustment.objects.filter(
        employee=eligible_package.employee, kind="deferred_release"
    ).count() == 0


def test_release_approval_is_heads_only_and_lands_as_an_adjustment(actors, eligible_package):
    deferral = eligible_package.deferrals.get()
    url = f"/api/v1/payroll/packages/{eligible_package.pk}/deferrals/{deferral.pk}/decide/"

    refused = _client(actors["exec"]).post(url, {"decision": "approve",
                                                 "period_year": 2026, "period_month": 9},
                                           format="json")
    assert refused.status_code == 403

    approved = _client(actors["fin"]).post(url, {
        "decision": "approve", "period_year": 2026, "period_month": 9,
        "reason": "Service period completed.",
    }, format="json")
    assert approved.status_code == 200, approved.content
    deferral.refresh_from_db()
    assert deferral.status == DeferralStatus.APPROVED
    adjustment = deferral.released_in_adjustment
    assert adjustment.kind == "deferred_release"
    assert adjustment.status == "approved"
    assert adjustment.label.startswith("Deferred Package Release")
    assert adjustment.amount == L("600000.00")

    # Approving twice can never create a second payment.
    again = _client(actors["hr"]).post(url, {"decision": "approve",
                                             "period_year": 2026, "period_month": 10},
                                       format="json")
    assert again.status_code == 400


def test_reject_and_reschedule_are_recorded(actors, eligible_package):
    deferral = eligible_package.deferrals.get()
    base = f"/api/v1/payroll/packages/{eligible_package.pk}/deferrals/{deferral.pk}"

    moved = _client(actors["hr"]).post(f"{base}/reschedule/", {
        "eligible_on": "2027-01-01", "reason": "Completion period extended by agreement.",
    }, format="json")
    assert moved.status_code == 200
    deferral.refresh_from_db()
    assert deferral.eligible_on == dt.date(2027, 1, 1)
    assert deferral.status == DeferralStatus.PENDING  # future again

    deferral.status = DeferralStatus.ELIGIBLE
    deferral.save(update_fields=["status"])
    rejected = _client(actors["fin"]).post(f"{base}/decide/", {
        "decision": "reject", "reason": "Bond terms not met.",
    }, format="json")
    assert rejected.status_code == 200
    deferral.refresh_from_db()
    assert deferral.status == DeferralStatus.REJECTED

    from apps.audit.models import AuditLog

    assert AuditLog.objects.filter(
        entity_type="PackageDeferral", after__event="release_reject"
    ).exists()


# --------------------- 10 & 11: payroll pays the release once, separately


def test_the_release_reaches_the_payslip_as_its_own_line(
    actors, eligible_package, components, rate_sets
):
    from apps.payroll import services

    worker = eligible_package.employee
    services.create_salary_structure(
        actor=actors["hr"], employee=worker, ctc_annual=L("2400000.00"),
        valid_from=dt.date(2025, 4, 1), revision_reason="Package monthly salary",
        lines=[{"component": components["BASIC"].pk, "value": L("150000.00")}],
    )
    deferral = eligible_package.deferrals.get()
    _client(actors["fin"]).post(
        f"/api/v1/payroll/packages/{eligible_package.pk}/deferrals/{deferral.pk}/decide/",
        {"decision": "approve", "period_year": 2026, "period_month": 6,
         "reason": "Completed."}, format="json")

    run = services.create_run(actor=actors["fin"], period_year=2026, period_month=6)
    services.process_run(run, actor=actors["fin"])
    slip = run.payslips.get(employee=worker)
    labels = {line.label: line.amount for line in slip.lines.all()}

    # A separate line — never merged invisibly into Basic/HRA.
    assert labels["Deferred Package Release — Completion amount"] == L("600000.00")
    assert labels["Basic"] == L("150000.00")
    assert slip.gross_earnings == L("750000.00")

    deferral.refresh_from_db()
    assert deferral.effective_status == DeferralStatus.PAID
    # …and the dashboard reflects it without double counting.
    numbers = svc.summary(eligible_package)
    assert numbers["released_total"] == "600000.00"
    assert numbers["remaining_deferred"] == "0.00"


def test_monthly_payroll_never_pays_the_deferred_amount_early(
    actors, eligible_package, components, rate_sets
):
    from apps.payroll import services

    worker = eligible_package.employee
    services.create_salary_structure(
        actor=actors["hr"], employee=worker, ctc_annual=L("2400000.00"),
        valid_from=dt.date(2025, 4, 1), revision_reason="Package monthly salary",
        lines=[{"component": components["BASIC"].pk, "value": L("150000.00")}],
    )
    run = services.create_run(actor=actors["fin"], period_year=2025, period_month=6)
    services.process_run(run, actor=actors["fin"])
    slip = run.payslips.get(employee=worker)
    assert slip.gross_earnings == L("150000.00")  # the monthly salary, nothing more
    assert not slip.lines.filter(label__icontains="Deferred").exists()


# --------------------------- 7 & 12: revision keeps history; history frozen


def test_revision_supersedes_and_keeps_the_old_schedule(actors):
    client = _client(actors["hr"])
    created = client.post("/api/v1/payroll/packages/", _employee_b_payload(actors["worker"]),
                          format="json")
    package_id = created.json()["id"]
    client.post(f"/api/v1/payroll/packages/{package_id}/activate/", {}, format="json")

    revised = client.post(f"/api/v1/payroll/packages/{package_id}/revise/", {
        **_employee_b_payload(actors["worker"], total_amount="2600000.00", deferrals=[{
            "label": "Completion", "amount": "800000.00",
            "condition_type": "after_months", "condition_months": 12,
        }]),
        "reason": "CTC revised at appraisal.",
    }, format="json")
    assert revised.status_code == 201, revised.content
    assert revised.json()["supersedes"] == package_id
    old = EmployeePackage.objects.get(pk=package_id)
    assert old.status == PackageStatus.CANCELLED  # closed, kept, never edited
    assert old.total_amount == L("2400000.00")    # history intact

    new_id = revised.json()["id"]
    assert _client(actors["hr"]).post(
        f"/api/v1/payroll/packages/{new_id}/activate/", {}, format="json"
    ).status_code == 200


def test_editing_the_package_never_restates_issued_payroll(
    actors, eligible_package, components, rate_sets, verified_rate_sets
):
    from apps.payroll import services

    worker = eligible_package.employee
    services.create_salary_structure(
        actor=actors["hr"], employee=worker, ctc_annual=L("2400000.00"),
        valid_from=dt.date(2025, 4, 1), revision_reason="Package monthly salary",
        lines=[{"component": components["BASIC"].pk, "value": L("150000.00")}],
    )
    run = services.create_run(actor=actors["exec"], period_year=2025, period_month=7)
    services.process_run(run, actor=actors["exec"])
    services.approve_run(run, actor=actors["fin"])
    slip = run.payslips.get(employee=worker)
    gross_before = slip.gross_earnings

    response = _client(actors["fin"]).patch(
        f"/api/v1/payroll/packages/{eligible_package.pk}/",
        _employee_b_payload(worker, total_amount="3000000.00",
                            start_date="2025-04-01", end_date="2026-03-31",
                            periods=[{
                                "label": "Months 1-12", "start_date": "2025-04-01",
                                "end_date": "2026-03-31", "amount": "2400000.00",
                            }]),
        format="json")
    assert response.status_code == 200, response.content

    slip.refresh_from_db()
    assert slip.gross_earnings == gross_before  # locked history stays history


# --------------------------------- 5 & 6: joins and exits do not break it


def test_mid_period_joining_and_early_exit_change_nothing_automatically(actors):
    """Payroll prorates by employment window on its own; a package neither
    pays nor recovers anything by itself when someone joins late or leaves."""
    client = _client(actors["hr"])
    worker = actors["worker"]
    created = client.post("/api/v1/payroll/packages/", _employee_b_payload(worker),
                          format="json")
    client.post(f"/api/v1/payroll/packages/{created.json()['id']}/activate/", {}, format="json")

    worker.date_of_exit = dt.date(2026, 10, 31)
    worker.save(update_fields=["date_of_exit"])

    package = EmployeePackage.objects.get(pk=created.json()["id"])
    deferral = package.deferrals.get()
    assert package.status == PackageStatus.ACTIVE       # no automatic cancellation
    assert deferral.status == DeferralStatus.PENDING    # and no automatic recovery
    assert PayrollAdjustment.objects.filter(employee=worker).count() == 0

    # Ending it is an explicit, reasoned HR/Finance act.
    cancelled = client.post(f"/api/v1/payroll/packages/{package.pk}/set-status/", {
        "status": "cancelled", "reason": "Employee separated before completion.",
    }, format="json")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
