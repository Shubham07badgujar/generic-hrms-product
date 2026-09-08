"""
The payroll run lifecycle, and the two gates on it.

The gates are the point of this file. A payroll system that computes correctly
but lets anyone approve anything is not a payroll system, it is a calculator —
so most of what follows is about what the service REFUSES to do.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.payroll import services
from apps.payroll.models import (
    AdjustmentKind,
    AdjustmentStatus,
    ComponentType,
    EmployeeLoan,
    LoanStatus,
    PayrollAdjustment,
    PayrollRun,
    PayrollRunLocked,
    PayrollRunStatus,
    Payslip,
    StatutoryKind,
)

pytestmark = pytest.mark.django_db

PERIOD = (2025, 6)


# ---------------------------------------------------------------- processing


def test_processing_computes_a_payslip_for_every_salaried_employee(processed_run, salaried):
    assert processed_run.status == PayrollRunStatus.REVIEW
    assert processed_run.payslips.count() == len(salaried)


def test_a_payslip_lines_add_up_to_its_totals(processed_run):
    """
    The lines ARE the payslip; the totals summarise them.

    If these ever disagree, the employee's payslip does not explain the money
    they were paid — which is the one thing a payslip has to do.
    """
    payslip = processed_run.payslips.first()

    earnings = sum(
        line.amount for line in payslip.lines.all()
        if not line.is_employer_side
        and line.component_type in (ComponentType.EARNING, ComponentType.REIMBURSEMENT)
    )
    deductions = sum(
        line.amount for line in payslip.lines.all()
        if not line.is_employer_side
        and line.component_type in (ComponentType.DEDUCTION, ComponentType.STATUTORY_DEDUCTION)
    )
    employer = sum(line.amount for line in payslip.lines.all() if line.is_employer_side)

    assert earnings == payslip.gross_earnings
    assert deductions == payslip.total_deductions
    assert employer == payslip.employer_contributions
    assert payslip.net_pay == payslip.gross_earnings - payslip.total_deductions


def test_pf_is_assessed_on_wage_not_gross(processed_run):
    """
    The classic payroll bug, pinned.

    Gross is 43,000 and the PF wage (Basic only, here) is 25,000, which the
    15,000 ceiling then caps. Assessing PF on gross would produce a base of
    15,000 too — so the case that actually distinguishes them is the CONTRIBUTION
    against an uncapped wage, which the statutory suite covers. Here we assert
    the base is the ceiling and never the gross.
    """
    payslip = processed_run.payslips.first()
    pf = payslip.statutory_contributions.get(kind=StatutoryKind.PF)

    assert pf.base_wage == Decimal("15000.00")
    assert pf.base_wage != payslip.gross_earnings


def test_esi_does_not_apply_above_the_wage_limit(processed_run):
    payslip = processed_run.payslips.first()
    esi = payslip.statutory_contributions.get(kind=StatutoryKind.ESI)

    assert esi.applied is False
    assert esi.exemption_reason == "above_wage_threshold"
    assert esi.employee_amount == Decimal("0.00")


def test_reprocessing_replaces_payslips_rather_than_duplicating_them(processed_run, finance):
    """
    Re-processing must be idempotent.

    Payslips are soft-deletable, and a soft-deleted row still occupies the
    unique constraint — so a naive delete would make the second processing fail
    on a constraint nobody could see from the code.
    """
    first = set(processed_run.payslips.values_list("employee_id", flat=True))

    again = services.process_run(processed_run, actor=finance["payroll_executive"].user)

    assert again.payslips.count() == len(first)
    assert set(again.payslips.values_list("employee_id", flat=True)) == first


def test_an_employee_without_a_salary_structure_is_skipped_and_reported(
    db, finance, salaried, rate_sets, org, roles
):
    """A missing structure must not silently pay someone zero."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    user = User.objects.create_user(email="nostructure@pay.test", password="x-12345678")
    UserRole.objects.create(user=user, role=roles["employee"])
    Employee.objects.create(
        employee_code="EMP09999", user=user, first_name="No", last_name="Structure",
        department=org["departments"]["operations"], level=org["levels"][5],
        location=org["location"], date_of_joining=dt.date(2024, 1, 1),
    )

    run = services.create_run(
        actor=finance["payroll_executive"].user, period_year=2025, period_month=7
    )
    run = services.process_run(run, actor=finance["payroll_executive"].user)

    assert run.totals["skipped"] == 1
    assert not run.payslips.filter(employee__employee_code="EMP09999").exists()


# ------------------------------------------------------- adjustments & loans


def test_only_approved_adjustments_are_paid(db, draft_run, finance, salaried):
    """
    A draft bonus is a proposal, not a payment.

    Without this, whichever run happened to execute next would pay whatever
    anyone had typed into the adjustments screen.
    """
    employee = finance["therapist"]
    for status, label in [(AdjustmentStatus.DRAFT, "Draft bonus"),
                          (AdjustmentStatus.APPROVED, "Approved bonus")]:
        PayrollAdjustment.objects.create(
            employee=employee, kind=AdjustmentKind.BONUS, label=label,
            amount=Decimal("5000.00"), period_year=PERIOD[0], period_month=PERIOD[1],
            status=status,
        )

    run = services.process_run(draft_run, actor=finance["payroll_executive"].user)
    payslip = run.payslips.get(employee=employee)
    labels = [line.label for line in payslip.lines.all()]

    assert "Approved bonus" in labels
    assert "Draft bonus" not in labels


def test_a_loan_instalment_is_recovered_and_the_balance_falls(db, draft_run, finance, salaried):
    employee = finance["therapist"]
    loan = EmployeeLoan.objects.create(
        employee=employee, reference="LN-1", principal=Decimal("30000.00"),
        monthly_installment=Decimal("5000.00"), balance=Decimal("30000.00"),
        start_month=1, start_year=2025,
    )

    run = services.process_run(draft_run, actor=finance["payroll_executive"].user)
    payslip = run.payslips.get(employee=employee)
    loan.refresh_from_db()

    assert any("Loan recovery" in line.label for line in payslip.lines.all())
    assert loan.balance == Decimal("25000.00")


def test_a_final_instalment_never_recovers_more_than_is_owed(db, draft_run, finance, salaried):
    employee = finance["therapist"]
    loan = EmployeeLoan.objects.create(
        employee=employee, reference="LN-2", principal=Decimal("30000.00"),
        monthly_installment=Decimal("5000.00"), balance=Decimal("1200.00"),
        start_month=1, start_year=2025,
    )

    services.process_run(draft_run, actor=finance["payroll_executive"].user)
    loan.refresh_from_db()

    assert loan.balance == Decimal("0.00")
    assert loan.status == LoanStatus.CLOSED


# ------------------------------------------------------- THE STATUTORY GATE


def test_a_run_computed_against_unverified_rates_cannot_be_approved(processed_run, finance):
    """
    The gate this whole system exists for.

    Processing against draft rates is allowed — that is how finance sees what a
    proposed rate would produce. Approving is not, because approval releases
    money computed from figures nobody has checked.
    """
    with pytest.raises(ValidationError) as exc:
        services.approve_run(processed_run, actor=finance["finance_head"].user)

    message = " ".join(exc.value.messages)
    assert "not verified" in message


def test_the_gate_names_every_unverified_rate_set_not_just_the_first(processed_run):
    """Finance should be able to fix them in one pass, not one failed run at a time."""
    blockers = services.unverified_rule_sets(processed_run)

    statutes = {blocker.split()[0].split("/")[0] for blocker in blockers}
    assert {"pf", "esi", "gratuity", "income_tax"} <= statutes


def test_a_run_against_verified_rates_can_be_approved(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    assert run.status == PayrollRunStatus.APPROVED
    assert run.locked is True
    assert run.approved_by == finance["finance_head"].user
    assert services.unverified_rule_sets(run) == []


# ---------------------------------------------------- THE SEGREGATION GATE


def test_whoever_processed_a_run_cannot_approve_it(approvable_run, finance, roles):
    """
    Even someone holding both capabilities must not do both on the same run.

    The permission matrix already stops payroll staff approving at all. This
    catches the remaining case — a Finance Head who processed the run — where
    one person would otherwise prepare and release the same payment unobserved.
    """
    from apps.accounts.models import UserRole

    processor = finance["payroll_executive"].user
    UserRole.objects.create(user=processor, role=roles["finance_head"])

    with pytest.raises(PermissionDenied) as exc:
        services.approve_run(approvable_run, actor=processor)

    assert "cannot also approve" in str(exc.value)


def test_a_second_person_can_approve_the_same_run(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)
    assert run.status == PayrollRunStatus.APPROVED


# ------------------------------------------------------------ immutability


def test_an_approved_run_refuses_changes_to_its_substance(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    run.totals = {"net_pay": "1.00"}
    with pytest.raises(PayrollRunLocked):
        run.save()


def test_an_approved_run_still_accepts_the_fields_that_describe_what_happened_next(
    approvable_run, finance
):
    """Locking freezes the amounts, not the record of payment."""
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    run = services.mark_paid(run, actor=finance["finance_head"].user)

    assert run.status == PayrollRunStatus.PAID
    assert run.paid_at is not None


def test_a_payslip_cannot_be_written_against_a_locked_run(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)
    payslip = run.payslips.first()

    payslip.net_pay = Decimal("1.00")
    with pytest.raises(PayrollRunLocked):
        payslip.save()


def test_a_locked_run_cannot_be_reprocessed(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    with pytest.raises(ValidationError):
        services.process_run(run, actor=finance["payroll_executive"].user)


# ---------------------------------------------------------------- reversal


def test_reversal_is_the_only_way_to_correct_an_approved_run(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    run = services.reverse_run(
        run, actor=finance["finance_head"].user,
        reason="The June structure revision was applied a month late and must be redone.",
    )

    assert run.status == PayrollRunStatus.REVERSED
    assert run.locked is False
    # The payslips survive: a reversal records what happened, it does not erase it.
    assert run.payslips.exists()


def test_a_reversal_demands_a_substantial_reason(approvable_run, finance):
    run = services.approve_run(approvable_run, actor=finance["finance_head"].user)

    with pytest.raises(ValidationError):
        services.reverse_run(run, actor=finance["finance_head"].user, reason="oops")


def test_only_an_approved_or_paid_run_can_be_reversed(processed_run, finance):
    with pytest.raises(ValidationError):
        services.reverse_run(
            processed_run, actor=finance["finance_head"].user,
            reason="This run has not been approved so there is nothing to reverse.",
        )


# ---------------------------------------------------------------- rejection


def test_rejecting_a_run_discards_its_payslips_and_returns_it_to_draft(processed_run, finance):
    count = processed_run.payslips.count()
    assert count > 0

    run = services.reject_run(
        processed_run, actor=finance["finance_head"].user,
        reason="Salary revisions for June were not loaded before processing.",
    )

    assert run.status == PayrollRunStatus.DRAFT
    assert run.payslips.count() == 0
    assert Payslip.objects.filter(payroll_run=run).count() == 0


def test_rejection_demands_a_reason(processed_run, finance):
    with pytest.raises(ValidationError):
        services.reject_run(processed_run, actor=finance["finance_head"].user, reason="no")


# ------------------------------------------------------------ run creation


def test_a_second_run_may_open_alongside_the_first(draft_run, finance):
    """
    The approved policy: a period holds several runs at once — the second one
    pays the people the first missed. Safety moved from a creation-time block
    to the processing skip and the approval re-check (test_second_run.py),
    which is where double payment is actually prevented.
    """
    second = services.create_run(
        actor=finance["payroll_executive"].user,
        period_year=PERIOD[0], period_month=PERIOD[1],
    )
    assert second.pk != draft_run.pk
    assert second.sequence == draft_run.sequence + 1


def test_an_off_cycle_run_can_coexist_with_the_regular_one(draft_run, finance):
    """
    The previous system's unique key had no run type or sequence, which
    structurally prevented paying a second bonus in a month.
    """
    off_cycle = services.create_run(
        actor=finance["payroll_executive"].user,
        period_year=PERIOD[0], period_month=PERIOD[1], run_type="off_cycle",
    )

    assert off_cycle.pk != draft_run.pk
    assert PayrollRun.objects.filter(
        period_year=PERIOD[0], period_month=PERIOD[1]
    ).count() == 2


def test_a_soft_deleted_run_does_not_block_the_period_forever(draft_run, finance):
    """BaseModel.delete() soft-deletes, so the uniqueness check must exclude those."""
    draft_run.delete()

    replacement = services.create_run(
        actor=finance["payroll_executive"].user,
        period_year=PERIOD[0], period_month=PERIOD[1],
    )

    assert replacement.pk != draft_run.pk


# ---------------------------------------------------------- financial year


@pytest.mark.parametrize(
    ("month", "year", "expected"),
    [(4, 2025, "2025-2026"), (12, 2025, "2025-2026"), (1, 2026, "2025-2026"),
     (3, 2026, "2025-2026"), (4, 2026, "2026-2027")],
)
def test_the_indian_financial_year_starts_in_april(month, year, expected):
    run = PayrollRun(period_month=month, period_year=year)
    assert run.financial_year == expected

