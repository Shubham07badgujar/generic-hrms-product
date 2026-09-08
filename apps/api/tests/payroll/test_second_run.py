"""
Two regular runs in one period: allowed, and nobody is paid twice.

The second run of a month exists to pay the people the first one missed —
an employee already holding a live payslip for the period is skipped with a
visible warning, off-cycle runs stay exempt (paying a bonus to someone
already paid their salary is their purpose), and approval re-checks the rule
at the point money actually moves.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.payroll import services
from apps.payroll.services.runs import PayrollError

pytestmark = pytest.mark.django_db

L = Decimal


@pytest.fixture
def first_run_paid_anil(finance, components, verified_rate_sets):
    """Run #1 pays only Anil (the only structure); approved and locked."""
    actor = finance["finance_head"].user
    services.create_salary_structure(
        actor=actor, employee=finance["accounts_manager"],
        ctc_annual=L("600000.00"), valid_from=dt.date(2024, 4, 1),
        revision_reason="first",
        lines=[{"component": components["BASIC"].pk, "value": L("25000.00")}],
    )
    run = services.create_run(actor=finance["payroll_executive"].user,
                              period_year=2024, period_month=6)
    services.process_run(run, actor=finance["payroll_executive"].user)
    services.approve_run(run, actor=actor)
    return run


def test_a_second_regular_run_pays_only_the_missed_people(finance, components,
                                                          first_run_paid_anil):
    actor = finance["finance_head"].user
    # Tara joined the system after run #1 — her structure appears now.
    services.create_salary_structure(
        actor=actor, employee=finance["therapist"],
        ctc_annual=L("360000.00"), valid_from=dt.date(2024, 4, 1),
        revision_reason="late addition",
        lines=[{"component": components["BASIC"].pk, "value": L("15000.00")}],
    )

    # Creating a second run for the SAME period is allowed now.
    second = services.create_run(actor=finance["payroll_executive"].user,
                                 period_year=2024, period_month=6)
    assert second.sequence == 2
    services.process_run(second, actor=finance["payroll_executive"].user)

    # Only Tara got a payslip; Anil was skipped with a visible reason.
    slips = list(second.payslips.select_related("employee"))
    assert [s.employee.pk for s in slips] == [finance["therapist"].pk]
    warnings = second.totals.get("warnings", [])
    assert any("already has a payslip for this period" in w for w in warnings)

    # And the second run approves cleanly — no double payment exists.
    services.approve_run(second, actor=actor)
    assert second.locked is True


def test_off_cycle_runs_still_include_already_paid_employees(finance, components,
                                                             first_run_paid_anil):
    bonus_run = services.create_run(
        actor=finance["finance_head"].user, period_year=2024, period_month=6,
        run_type="off_cycle",
    )
    services.process_run(bonus_run, actor=finance["finance_head"].user)
    # Anil appears again: off-cycle pay on top of a regular slip is the point.
    assert bonus_run.payslips.filter(employee=finance["accounts_manager"]).exists()


def test_approval_recheck_blocks_a_parallel_duplicate(finance, components,
                                                      first_run_paid_anil):
    """Simulate the race: a second run processed while #1 was still open."""
    from apps.payroll.models import Payslip, PayrollRun

    second = services.create_run(actor=finance["payroll_executive"].user,
                                 period_year=2024, period_month=6)
    services.process_run(second, actor=finance["payroll_executive"].user)
    # Force the race outcome: copy Anil's payslip into run #2 as if both runs
    # had processed him before either could see the other.
    template = first_run_paid_anil.payslips.get()
    PayrollRun.objects.filter(pk=second.pk).update(status="review")
    Payslip.objects.create(
        payroll_run=second, employee=template.employee,
        salary_structure=template.salary_structure,
        paid_days=template.paid_days, gross_earnings=template.gross_earnings,
        total_deductions=template.total_deductions, net_pay=template.net_pay,
    )

    second.refresh_from_db()
    with pytest.raises(PayrollError, match="already have a payslip for this period"):
        services.approve_run(second, actor=finance["finance_head"].user)
