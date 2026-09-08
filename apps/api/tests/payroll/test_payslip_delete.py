"""
The Finance Head's five-day payslip correction window.

Within PAYSLIP_DELETE_WINDOW_DAYS of generation the Finance Head may delete
a payslip with a recorded reason; the run's totals are restated and the
employee becomes payable again by a later run. Past the window the payslip
is locked for everyone — Finance included — and nobody else ever holds the
delete at all.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payroll import services
from apps.payroll.models import Payslip

pytestmark = pytest.mark.django_db

L = Decimal


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def paid_run(finance, components, verified_rate_sets):
    actor = finance["finance_head"].user
    services.create_salary_structure(
        actor=actor, employee=finance["accounts_manager"],
        ctc_annual=L("600000.00"), valid_from=dt.date(2024, 4, 1),
        revision_reason="x",
        lines=[{"component": components["BASIC"].pk, "value": L("25000.00")}],
    )
    run = services.create_run(actor=finance["payroll_executive"].user,
                              period_year=2024, period_month=6)
    services.process_run(run, actor=finance["payroll_executive"].user)
    services.approve_run(run, actor=actor)
    return run


def test_finance_head_deletes_within_the_window(finance, paid_run):
    payslip = paid_run.payslips.get()
    response = _client(finance["finance_head"].user).delete(
        f"/api/v1/payslips/{payslip.pk}/",
        {"reason": "Wrong structure applied; will re-run for this employee."},
        format="json",
    )
    assert response.status_code == 204, response.content

    payslip.refresh_from_db()
    assert payslip.is_active is False
    paid_run.refresh_from_db()
    # The run keeps telling the truth about what it now pays — while staying
    # approved and locked.
    assert paid_run.totals["net_pay"] == "0.00"
    assert paid_run.totals["employee_count"] == 0
    assert paid_run.locked is True

    from apps.audit.models import AuditLog

    row = AuditLog.objects.filter(
        entity_type="Payslip", entity_id=str(payslip.pk), action="delete"
    ).first()
    assert row is not None and "re-run" in (row.reason or "")

    # …and the employee is payable again: a second run no longer skips them.
    second = services.create_run(actor=finance["payroll_executive"].user,
                                 period_year=2024, period_month=6)
    services.process_run(second, actor=finance["payroll_executive"].user)
    assert second.payslips.filter(employee=finance["accounts_manager"]).exists()


def test_after_five_days_the_payslip_is_locked_for_everyone(finance, paid_run):
    payslip = paid_run.payslips.get()
    Payslip.objects.filter(pk=payslip.pk).update(
        created_at=timezone.now() - dt.timedelta(days=6)
    )
    response = _client(finance["finance_head"].user).delete(
        f"/api/v1/payslips/{payslip.pk}/", {"reason": "Too late anyway."}, format="json"
    )
    assert response.status_code == 400
    assert b"locked" in response.content
    payslip.refresh_from_db()
    assert payslip.is_active is True


def test_a_reason_is_mandatory(finance, paid_run):
    payslip = paid_run.payslips.get()
    response = _client(finance["finance_head"].user).delete(
        f"/api/v1/payslips/{payslip.pk}/", {}, format="json"
    )
    assert response.status_code == 400
    assert b"requires a reason" in response.content


def test_nobody_but_the_finance_head_holds_the_delete(finance, paid_run):
    payslip = paid_run.payslips.get()
    for who in ("hr_head", "payroll_executive", "accounts_manager"):
        response = _client(finance[who].user).delete(
            f"/api/v1/payslips/{payslip.pk}/", {"reason": "no"}, format="json"
        )
        assert response.status_code == 403, who
    payslip.refresh_from_db()
    assert payslip.is_active is True
