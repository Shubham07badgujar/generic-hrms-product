"""
Per-employee statutory applicability.

Not everyone is enrolled in PF, ESIC or gratuity — that is HR Head / Finance
Head's call, recorded on the salary structure. An opted-out statute leaves no
line on the payslip (employer side included) but the contribution ROW still
records the exemption, so filings and audits can see why nothing was paid.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_structures(finance, components):
    """Anil fully enrolled; Tara outside PF/ESIC/gratuity/TDS (PT only)."""
    from apps.payroll import services

    actor = finance["finance_head"].user
    lines = [
        {"component": components["BASIC"].pk, "value": Decimal("25000.00")},
        {"component": components["HRA"].pk, "value": Decimal("40")},
        {"component": components["SPECIAL"].pk, "value": Decimal("8000.00")},
    ]
    services.create_salary_structure(
        actor=actor, employee=finance["accounts_manager"],
        ctc_annual=Decimal("600000.00"), valid_from=dt.date(2024, 4, 1),
        revision_reason="Enrolled", lines=lines,
    )
    services.create_salary_structure(
        actor=actor, employee=finance["therapist"],
        ctc_annual=Decimal("600000.00"), valid_from=dt.date(2024, 4, 1),
        revision_reason="Outside PF/ESIC/gratuity/TDS",
        lines=lines,
        pf_applicable=False, esi_applicable=False,
        gratuity_applicable=False, tds_applicable=False,
    )
    return finance


def test_opted_out_statutes_leave_no_trace_on_the_payslip(two_structures, rate_sets):
    from apps.payroll import services
    from apps.payroll.models import Payslip

    finance = two_structures
    actor = finance["finance_head"].user
    run = services.create_run(actor=actor, period_year=2024, period_month=6)
    services.process_run(run, actor=actor)

    outside = Payslip.objects.get(payroll_run=run, employee=finance["therapist"])
    enrolled = Payslip.objects.get(payroll_run=run, employee=finance["accounts_manager"])

    # The enrolled colleague keeps the employer block…
    assert enrolled.lines.filter(is_employer_side=True).exists()
    assert enrolled.employer_contributions > 0
    assert enrolled.lines.filter(label="Provident Fund (employee)").exists()

    # …the opted-out employee has NO employer contributions at all, and no
    # PF/TDS deduction — only Professional Tax remains.
    assert not outside.lines.filter(is_employer_side=True).exists()
    assert outside.employer_contributions == 0
    assert not outside.lines.filter(label__icontains="Provident Fund").exists()
    assert not outside.lines.filter(label="TDS").exists()
    pt = outside.lines.get(label="Professional Tax")
    assert pt.amount == Decimal("200.00")
    assert outside.total_deductions == Decimal("200.00")
    assert outside.net_pay == outside.gross_earnings - Decimal("200.00")

    # The exemption is recorded, not silently vanished.
    pf_row = outside.statutory_contributions.get(kind="pf")
    assert pf_row.applied is False
    assert pf_row.exemption_reason == "not_enrolled_per_salary_structure"
    assert pf_row.employee_amount == 0 and pf_row.employer_amount == 0


def test_pay_is_clipped_to_the_employment_window(finance, components, rate_sets):
    """
    A mid-month joiner is paid from the joining date, a leaver up to exit.

    Attendance cannot express this — days before joining simply have no
    records, and unrecorded days deliberately do not cut pay — so payroll
    itself clips to the employment window.
    """
    from apps.payroll import services
    from apps.payroll.models import Payslip

    actor = finance["finance_head"].user
    joiner = finance["therapist"]
    joiner.date_of_joining = dt.date(2024, 6, 10)   # June has 30 days
    joiner.save(update_fields=["date_of_joining"])
    leaver = finance["accounts_manager"]
    leaver.date_of_exit = dt.date(2024, 6, 20)
    leaver.save(update_fields=["date_of_exit"])

    lines = [{"component": components["BASIC"].pk, "value": Decimal("30000.00")}]
    for person in (joiner, leaver):
        services.create_salary_structure(
            actor=actor, employee=person, ctc_annual=Decimal("360000.00"),
            valid_from=dt.date(2024, 6, 1), revision_reason="test", lines=lines,
        )

    run = services.create_run(actor=actor, period_year=2024, period_month=6)
    services.process_run(run, actor=actor)

    joined = Payslip.objects.get(payroll_run=run, employee=joiner)
    assert joined.paid_days == Decimal("21.00")     # 10th–30th
    assert joined.gross_earnings == Decimal("21000.00")  # 30000 × 21/30

    left = Payslip.objects.get(payroll_run=run, employee=leaver)
    assert left.paid_days == Decimal("20.00")       # 1st–20th
    assert left.gross_earnings == Decimal("20000.00")


def test_the_flags_ride_the_structure_api(two_structures, api):
    """HR/Finance set the flags through the same structure endpoint."""
    from rest_framework.test import APIClient

    finance = two_structures
    client = APIClient()
    client.force_authenticate(user=finance["finance_head"].user)

    rows = client.get(
        "/api/v1/payroll/structures/", {"employee": str(finance["therapist"].pk)}
    ).json()["data"]
    assert rows[0]["pf_applicable"] is False
    assert rows[0]["gratuity_applicable"] is False
    assert rows[0]["pt_applicable"] is True
