"""
Payroll × leave: the seams, proven end to end through a real run.

The claims:
  1. Approved unpaid leave REDUCES PAY: paid days shrink by the LOP days and
     every earning line prorates accordingly. (Previously the missing
     attendance module silently disabled this — the two imports shared one
     try-block.)
  2. Approved probation leave is loss of pay whatever the leave type.
  3. Working a declared public holiday earns the day AGAIN — an explicit
     earning line at one day's gross, so the day is exactly double-paid.
"""

from __future__ import annotations

import calendar as cal
import datetime as dt
from decimal import Decimal

import pytest

from apps.leave.models import LeaveRequest, LeaveStatus
from apps.leave.seeds import seed_leave
from apps.payroll import services
from apps.payroll.models import Payslip, PayslipLine

pytestmark = pytest.mark.django_db

PERIOD = (2025, 6)


@pytest.fixture
def leave_seeded(db):
    seed_leave()
    from apps.leave.models import LeaveType

    return {t.code: t for t in LeaveType.objects.all()}


def _approved_leave(employee, leave_type, start, end, days, **extra):
    return LeaveRequest.objects.create(
        employee=employee, leave_type=leave_type,
        start_date=start, end_date=end, days=Decimal(days),
        reason="Payroll integration fixture.", status=LeaveStatus.APPROVED,
        **extra,
    )


def test_approved_unpaid_leave_reduces_pay(finance, salaried, rate_sets, leave_seeded):
    year, month = PERIOD
    therapist = finance["therapist"]
    # Two working days of LWP inside the period (Mon 2025-06-02, Tue 06-03).
    _approved_leave(
        therapist, leave_seeded["lwp"], dt.date(2025, 6, 2), dt.date(2025, 6, 3), "2"
    )

    run = services.create_run(
        actor=finance["payroll_executive"].user, period_year=year, period_month=month
    )
    services.process_run(run, actor=finance["payroll_executive"].user)

    slip = Payslip.objects.get(payroll_run=run, employee=therapist)
    total = Decimal(cal.monthrange(year, month)[1])
    assert slip.lop_days == Decimal("2")
    assert slip.paid_days == total - 2
    # Pay is prorated: strictly less than a colleague with a full month.
    full = Payslip.objects.get(payroll_run=run, employee=finance["hr_head"])
    assert slip.gross_earnings < full.gross_earnings


def test_probation_leave_is_lop_even_on_a_paid_type(finance, salaried, rate_sets, leave_seeded):
    year, month = PERIOD
    therapist = finance["therapist"]
    _approved_leave(
        therapist, leave_seeded["paid"], dt.date(2025, 6, 4), dt.date(2025, 6, 4), "1",
        probation_unpaid=True,
    )

    run = services.create_run(
        actor=finance["payroll_executive"].user, period_year=year, period_month=month
    )
    services.process_run(run, actor=finance["payroll_executive"].user)
    slip = Payslip.objects.get(payroll_run=run, employee=therapist)
    assert slip.lop_days == Decimal("1")


def test_holiday_work_is_double_paid_as_an_explicit_line(
    finance, salaried, rate_sets, leave_seeded
):
    from apps.leave.models import Holiday, HolidayCalendar, HolidayWork

    year, month = PERIOD
    therapist = finance["therapist"]
    calendar_row = HolidayCalendar.objects.get(location__isnull=True)
    Holiday.objects.get_or_create(
        calendar=calendar_row, date=dt.date(2025, 6, 6), defaults={"name": "Clinic festival"}
    )
    HolidayWork.objects.create(
        employee=therapist, date=dt.date(2025, 6, 6), holiday_name="Clinic festival"
    )

    run = services.create_run(
        actor=finance["payroll_executive"].user, period_year=year, period_month=month
    )
    services.process_run(run, actor=finance["payroll_executive"].user)

    slip = Payslip.objects.get(payroll_run=run, employee=therapist)
    line = PayslipLine.objects.filter(
        payslip=slip, label__startswith="Worked public holiday"
    ).first()
    assert line is not None
    # One extra day's gross: monthly gross 43,000 over 30 days.
    total = Decimal(cal.monthrange(year, month)[1])
    assert line.amount == (Decimal("43000") / total).quantize(Decimal("0.01"))
    # The extra day raises this slip above an otherwise identical colleague's.
    plain = Payslip.objects.get(payroll_run=run, employee=finance["hr_head"])
    assert slip.gross_earnings > plain.gross_earnings
