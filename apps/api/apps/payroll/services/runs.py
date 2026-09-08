r"""
The payroll run lifecycle.

    DRAFT -> PROCESSING -> REVIEW -> APPROVED -> PAID
                              \-> DRAFT (rejected)      APPROVED/PAID -> REVERSED

Two gates sit on this pipeline and they guard different things.

THE STATUTORY GATE, at approval. A run may be PROCESSED against unverified
rates — that is how finance sees the numbers a proposed rate set would produce
— but it may never be APPROVED against them. Approval is the act that releases
money, and releasing money computed from rates nobody has checked is precisely
the failure this system was rebuilt to prevent.

THE SEGREGATION GATE, also at approval. Whoever prepared the run cannot be the
one who releases it. The permission matrix already stops payroll staff from
approving at all; this catches the remaining case, where one person holds both
capabilities and could otherwise pay themselves unobserved.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import Employee, EmployeeStatus
from apps.notifications import events as notify_events
from apps.statutory.contracts import StatutoryContext
from apps.statutory.models import VerificationStatus
from apps.statutory.rules.money import ZERO, money
from apps.statutory.services import assessment as statutory

from ..models import (
    AdjustmentKind,
    AdjustmentStatus,
    ComponentType,
    InvestmentDeclaration,
    LoanStatus,
    PayrollAdjustment,
    PayrollRun,
    PayrollRunStatus,
    Payslip,
    PayslipLine,
    RunType,
    StatutoryContribution,
    StatutoryKind,
)
from .audit import audit_event
from .structures import structure_in_force

logger = logging.getLogger("hrms.payroll")

#: Employment statuses that earn pay.
#:
#: Stated as a set rather than "not exited", because the difference between the
#: two readings is whole categories of people. A CONFIRMED employee is the
#: ordinary case and omitting it would silently skip most of the payroll;
#: RESIGNED and TERMINATED still work their notice and must still be paid,
#: while EXITED must not be. ONBOARDING has not started, so has nothing to earn.
PAYABLE_STATUSES = [
    EmployeeStatus.ON_PROBATION,
    EmployeeStatus.ACTIVE,
    EmployeeStatus.CONFIRMED,
    EmployeeStatus.ON_LEAVE,
    EmployeeStatus.ON_NOTICE,
    EmployeeStatus.RESIGNED,
    EmployeeStatus.TERMINATED,
]


class PayrollError(ValidationError):
    """A payroll operation that cannot proceed."""


# ---------------------------------------------------------------------------
# Attendance seam
# ---------------------------------------------------------------------------


def paid_and_lop_days(employee, year: int, month: int) -> tuple[Decimal, Decimal]:
    """
    Days payable and days of loss of pay for the period.

    Two independent seams, each degrading separately:
      * LEAVE is built: `get_lop_days` supplies approved unpaid days (LWP,
        probation leave, settled short-leave shortfall) and paid days shrink
        by exactly that much. Previously both imports shared one try-block,
        so the missing attendance module silently disabled the leave
        deduction too — everyone was paid a full month regardless of
        approved unpaid leave.
      * ATTENDANCE is not built yet: until
        `attendance.services.monthly_summary` exists, presence is assumed
        for every day that is not approved unpaid leave, and the assumption
        is recorded on the run.

    Deliberately ONE function. Scattering the fallback across the run would make
    it invisible, and an invisible assumption about paid days is an invisible
    assumption about everybody's pay.
    """
    total = Decimal(calendar.monthrange(year, month)[1])

    # Days outside the employment window are never payable. Attendance cannot
    # catch these — a day before joining simply has no record, and unrecorded
    # days deliberately do not cut pay (weekends, device gaps) — so the clip
    # has to happen here, where a mid-month joiner or leaver is a payroll
    # fact rather than an attendance one.
    outside = _days_outside_employment(employee, year, month)

    lop = ZERO
    try:
        from apps.leave.services import get_lop_days  # type: ignore
    except ImportError:
        pass
    else:
        lop = Decimal(str(get_lop_days(employee, year, month)))

    try:
        from apps.attendance.services import monthly_summary  # type: ignore
    except ImportError:
        return max(total - lop - outside, ZERO), lop

    summary = monthly_summary(employee, year, month)
    return max(Decimal(str(summary.paid_days)) - outside, ZERO), lop


def attendance_breakdown(employee, year: int, month: int, paid_days, lop) -> dict:
    """
    The picture behind paid days, in the shape the payslip prints.

    Every seam degrades independently: no attendance data leaves the
    attendance keys None (printed as em-dashes), no leave module leaves the
    leave keys None. Zeroes are real zeroes; None means "not tracked".
    """
    total = calendar.monthrange(year, month)[1]
    out: dict = {
        "days_in_month": total,
        "paid_days": str(paid_days),
        "lop_days": str(lop),
        "present_days": None, "half_days": None, "absent_days": None,
        "days_attended": None,
        "paid_leave_taken": None, "leave_balance": None,
    }

    try:
        from apps.attendance.services import monthly_summary
        summary = monthly_summary(employee, year, month)
    except ImportError:
        summary = None
    if summary is not None and summary.from_attendance:
        out.update(
            present_days=summary.present_days,
            half_days=summary.half_days,
            absent_days=summary.absent_days,
            days_attended=str(Decimal(summary.present_days) + Decimal("0.5") * summary.half_days),
        )

    try:
        from apps.leave.models import LeaveRequest, LeaveStatus
        from apps.leave.services import ensure_balances, working_days
    except ImportError:
        return out

    month_start = dt.date(year, month, 1)
    month_end = dt.date(year, month, total)

    # Working days vs weekly offs, within the employment window — so the slip
    # can say "paid = attended + paid offs" instead of leaving readers to
    # reconcile 18 attended against 21 paid.
    window_start = max(month_start, employee.date_of_joining or month_start)
    window_end = min(month_end, employee.date_of_exit or month_end)
    if window_start <= window_end:
        in_window = (window_end - window_start).days + 1
        workable = working_days(employee, window_start, window_end)
        out.update(
            working_days=str(workable),
            weekly_offs=str(Decimal(in_window) - workable),
        )

    taken = ZERO
    for row in LeaveRequest.objects.filter(
        employee=employee, is_active=True, status=LeaveStatus.APPROVED,
        leave_type__is_paid=True, probation_unpaid=False,
        start_date__lte=month_end, end_date__gte=month_start,
    ).select_related("leave_type"):
        if row.half_day:
            taken += Decimal("0.5")
        else:
            taken += working_days(
                employee, max(row.start_date, month_start), min(row.end_date, month_end)
            )
    # The same source the Leave page uses — ensure_balances refreshes every
    # row against the accrued target (probation gating included) before the
    # figure lands on a payslip, so HR and payroll can never disagree.
    balance = sum(
        (row.available for row in ensure_balances(employee, year=year)
         if row.leave_type.is_paid),
        ZERO,
    )
    out.update(paid_leave_taken=str(taken), leave_balance=str(balance))
    return out


def _days_outside_employment(employee, year: int, month: int) -> Decimal:
    """Calendar days of this month before joining or after exit."""
    total = calendar.monthrange(year, month)[1]
    month_start = dt.date(year, month, 1)
    month_end = dt.date(year, month, total)

    first_payable = max(month_start, employee.date_of_joining or month_start)
    last_payable = min(month_end, employee.date_of_exit or month_end)
    if first_payable > last_payable:
        return Decimal(total)  # not employed at all this month
    return Decimal(total - ((last_payable - first_payable).days + 1))


def _assumed_full_attendance() -> bool:
    """
    True while payroll runs on assumed presence.

    The attendance module now exists, so the question moved from "is the
    module importable" to "is it allowed to drive pay" — the report-only
    switch that keeps biometric data advisory until management flips it.
    """
    try:
        from apps.attendance.services import attendance_drives_payroll
    except ImportError:
        return True
    return not attendance_drives_payroll()


def _holiday_work_adjustments(run, employee, structure, total_days: Decimal) -> list:
    """
    Working an approved declared public holiday earns that day AGAIN — the
    company's double-pay rule. The holiday itself is already a paid day, so
    one extra day's gross per worked holiday makes the day exactly double.
    Mirrors the loan-recovery pattern: unsaved adjustments, no pk, no
    double-apply risk, an explicit line on the payslip.
    """
    try:
        from apps.leave.services import holiday_work_dates  # type: ignore
    except ImportError:
        return []

    dates = holiday_work_dates(employee, run.period_year, run.period_month)
    if not dates or not total_days:
        return []

    monthly_gross = sum(
        (line.monthly_amount for line in structure.lines.select_related("component").all()
         if line.component.component_type == ComponentType.EARNING),
        ZERO,
    )
    per_day = money(monthly_gross / total_days)
    return [
        PayrollAdjustment(
            employee=employee, kind=AdjustmentKind.OTHER_EARNING,
            label=f"Worked public holiday {worked:%d %b} (double pay)",
            amount=per_day, period_year=run.period_year, period_month=run.period_month,
            is_taxable=True, status=AdjustmentStatus.APPROVED,
        )
        for worked in dates
    ]


# ---------------------------------------------------------------------------
# Creating and processing
# ---------------------------------------------------------------------------


@transaction.atomic
def create_run(
    *,
    actor,
    period_year: int,
    period_month: int,
    location=None,
    run_type: str = "regular",
    notes: str = "",
) -> PayrollRun:
    # `is_active=True` matters: BaseModel.delete() is a SOFT delete, so without
    # it an archived run would block this period forever.
    #
    # A period may hold several runs AT ONCE — the second regular run of a
    # month exists precisely to pay employees the first one missed. What makes
    # that safe is not a creation-time block but the processing rule: an
    # employee who already holds a payslip for the period in another live run
    # is SKIPPED (see `_process_employee`), and approval re-checks the same
    # thing. So creation only allocates the next sequence number.
    existing = PayrollRun.objects.filter(
        period_year=period_year, period_month=period_month,
        location=location, run_type=run_type, is_active=True,
    ).order_by("-sequence").first()

    run = PayrollRun.objects.create(
        period_year=period_year,
        period_month=period_month,
        location=location,
        run_type=run_type,
        sequence=(existing.sequence + 1) if existing else 1,
        run_by=actor,
        notes=notes,
        created_by=actor,
        updated_by=actor,
    )
    audit_event(run, actor=actor, entity_type="PayrollRun", verb="create",
                after={"period": f"{period_year}-{period_month:02d}", "type": run_type})
    return run


@transaction.atomic
def process_run(run: PayrollRun, *, actor) -> PayrollRun:
    """
    Compute every payslip in the run.

    Idempotent: re-processing discards the previous payslips and recomputes from
    current data, so a run can be re-run after a salary correction without
    leaving orphaned figures behind. A locked run refuses outright.
    """
    if run.locked:
        raise PayrollError("This run is approved and locked. Reverse it to recompute.")
    if run.status == PayrollRunStatus.PAID:
        raise PayrollError("This run has been paid and cannot be recomputed.")

    run.status = PayrollRunStatus.PROCESSING
    run.run_by = actor
    run.save(update_fields=["status", "run_by", "updated_at"])

    # hard_delete, not delete: BaseModel soft-deletes, and a soft-deleted
    # payslip still occupies `uniq_payslip_per_run_employee`, so the second
    # processing of a run would fail on a constraint nobody could see.
    # A superseded draft payslip is also not history worth keeping — the run
    # has not been approved, so nothing was ever owed on it.
    run.payslips.all().hard_delete()

    employees = Employee.objects.select_related("location", "department").filter(
        status__in=PAYABLE_STATUSES,
        is_active=True,
    )
    if run.location_id:
        employees = employees.filter(location_id=run.location_id)

    totals = {
        "employee_count": 0, "gross_earnings": ZERO, "total_deductions": ZERO,
        "net_pay": ZERO, "employer_contributions": ZERO, "skipped": 0,
    }
    rule_sets: dict[str, dict] = {}
    warnings: list[str] = []

    for employee in employees:
        payslip = _process_employee(run, employee, actor, rule_sets, warnings)
        if payslip is None:
            totals["skipped"] += 1
            continue
        totals["employee_count"] += 1
        for field in ("gross_earnings", "total_deductions", "net_pay", "employer_contributions"):
            totals[field] += getattr(payslip, field)

    if _assumed_full_attendance():
        warnings.append(
            "Attendance is running in report-only mode: presence was assumed for "
            "every day that is not approved unpaid leave. Approved LWP, probation "
            "leave and short-leave conversions HAVE been deducted. Biometric "
            "attendance starts affecting pay only when ATTENDANCE_AFFECTS_PAYROLL "
            "is enabled. Verify before approving."
        )

    run.totals = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in totals.items()}
    #: On the run itself, not only the audit trail — the reviewer of a second
    #: monthly run must SEE who was skipped as already paid, and why.
    run.totals["warnings"] = warnings
    run.rule_sets_used = rule_sets
    run.status = PayrollRunStatus.REVIEW
    run.save(update_fields=["totals", "rule_sets_used", "status", "updated_at"])

    audit_event(run, actor=actor, entity_type="PayrollRun", verb="update",
                after={"status": run.status, **run.totals, "warnings": warnings})

    # Whoever approves payroll now has something waiting, and — separately —
    # whoever can verify statutory rates is told if they are the reason it
    # cannot be approved. Two different people, two different actions.
    notify_events.payroll_processed(run)
    notify_events.statutory_verification_due(unverified_rule_sets(run))
    return run


def _process_employee(run, employee, actor, rule_sets: dict, warnings: list) -> Payslip | None:
    structure = structure_in_force(employee, run.period_start)
    if structure is None:
        warnings.append(f"{employee.employee_code}: no salary structure in force — skipped.")
        return None

    # One regular payslip per person per period, however many runs the period
    # holds. Someone already slipped in another LIVE regular run (reversed
    # runs are voided history) is skipped here, so a second run of the month
    # pays exactly the people the first one missed and nobody twice.
    # Off-cycle and supplementary runs are exempt — paying a bonus to someone
    # already paid their salary is their entire purpose.
    if run.run_type == RunType.REGULAR:
        duplicate = (
            Payslip.objects.filter(
                employee=employee, is_active=True,
                payroll_run__period_year=run.period_year,
                payroll_run__period_month=run.period_month,
                payroll_run__run_type=RunType.REGULAR,
                payroll_run__is_active=True,
            )
            .exclude(payroll_run=run)
            .exclude(payroll_run__status=PayrollRunStatus.REVERSED)
            .select_related("payroll_run")
            .first()
        )
        if duplicate:
            warnings.append(
                f"{employee.employee_code}: already has a payslip for this period "
                f"on run #{duplicate.payroll_run.sequence} "
                f"({duplicate.payroll_run.get_status_display().lower()}) — skipped, "
                f"no double payment."
            )
            return None

    paid_days, lop_days = paid_and_lop_days(employee, run.period_year, run.period_month)
    breakdown = attendance_breakdown(
        employee, run.period_year, run.period_month, paid_days, lop_days
    )
    total_days = Decimal(calendar.monthrange(run.period_year, run.period_month)[1])
    proration = (paid_days / total_days) if total_days else ZERO

    earnings, gross, pf_wage = _earnings(structure, proration)
    adjustments = _adjustments_for(run, employee)
    adjustments += _holiday_work_adjustments(run, employee, structure, total_days)
    adjusted_gross = gross + sum(
        (a.amount for a in adjustments if not a.is_deduction and not a.is_employer_side), ZERO
    )

    ctx = _context_for(run, employee, adjusted_gross, pf_wage)
    result = statutory.assess(ctx, require_verified=False)
    result = _apply_structure_applicability(result, structure)
    warnings.extend(f"{employee.employee_code}: {w}" for w in result.warnings)

    for statute, ref in result.rule_sets_used.items():
        rule_sets.setdefault(statute, {
            "rule_set_id": ref.rule_set_id,
            "rule_version": ref.rule_version,
            "checksum": ref.checksum,
            "effective_from": ref.effective_from,
            "jurisdiction": ref.jurisdiction,
            "regime": ref.regime,
            "verification_status": ref.verification_status,
        })

    return _persist(
        run, employee, structure, paid_days, lop_days,
        earnings, adjusted_gross, adjustments, result, actor,
        attendance_summary=breakdown,
    )


def _earnings(structure, proration: Decimal) -> tuple[list, Decimal, Decimal]:
    """Pro-rated earning lines, plus the gross and the PF wage they produce."""
    lines, gross, pf_wage = [], ZERO, ZERO
    for line in structure.lines.select_related("component").all():
        if line.component.component_type != ComponentType.EARNING:
            continue
        amount = money(line.monthly_amount * proration)
        lines.append((line.component, amount))
        gross += amount
        if line.component.is_wage:
            pf_wage += amount
    return lines, gross, pf_wage


def _adjustments_for(run, employee) -> list[PayrollAdjustment]:
    """
    Approved adjustments for this period, plus any loan instalment due.

    Only APPROVED ones. A draft bonus somebody was still considering must not be
    paid by whichever run happens to execute next.
    """
    adjustments = list(
        PayrollAdjustment.objects.filter(
            employee=employee,
            period_year=run.period_year,
            period_month=run.period_month,
            status=AdjustmentStatus.APPROVED,
            is_active=True,
        )
    )
    for loan in employee.loans.filter(status=LoanStatus.ACTIVE, is_active=True):
        due = loan.installment_due()
        if due > ZERO:
            adjustments.append(PayrollAdjustment(
                employee=employee, kind=AdjustmentKind.LOAN_RECOVERY,
                label=f"Loan recovery {loan.reference or ''}".strip(),
                amount=due, period_year=run.period_year, period_month=run.period_month,
                is_taxable=False, status=AdjustmentStatus.APPROVED,
            ))
    return adjustments


def _context_for(run, employee, gross: Decimal, pf_wage: Decimal) -> StatutoryContext:
    declaration = InvestmentDeclaration.objects.filter(
        employee=employee, financial_year=run.financial_year
    ).first()

    service_years = ZERO
    if employee.date_of_joining:
        days = (run.period_start - employee.date_of_joining).days
        service_years = Decimal(days) / Decimal("365.25")

    return StatutoryContext(
        period_start=run.period_start,
        financial_year=run.financial_year,
        period_month=run.period_month,
        gross_wage=gross,
        pf_wage=pf_wage,
        # Projecting the current month across the year is the standard s.192
        # approach. It is an estimate, and it self-corrects each month as
        # actual pay changes — which is why TDS is recomputed every run.
        annual_gross_projection=gross * Decimal("12"),
        last_drawn_monthly_wage=pf_wage,
        state=(employee.location.state if employee.location else ""),
        gender=employee.gender,
        date_of_joining=employee.date_of_joining,
        date_of_exit=employee.date_of_exit,
        tax_regime=(declaration.regime or None) if declaration else None,
        declared_deductions=declaration.as_decimals() if declaration else {},
        esi_already_liable_this_period=statutory.esi_liable_at_period_start(
            employee.pk,
            StatutoryContext(
                period_start=run.period_start,
                financial_year=run.financial_year,
                period_month=run.period_month,
            ),
        ),
        completed_service_years=max(ZERO, service_years),
    )


def _apply_structure_applicability(result, structure):
    """
    Statutes the structure opts out of are zeroed, employer side included.

    HR Head / Finance Head decide enrolment on the salary structure; the
    engine computes only what applies. The exemption is RECORDED on the
    contribution row rather than the statute silently vanishing, so a filing
    or an audit can still see why an employee contributed nothing. Totals need
    no separate handling — the assessment derives them from these results.
    """
    import dataclasses

    from apps.statutory.contracts import (
        ESIResult, GratuityResult, PFResult, PTResult, TDSResult,
    )

    NOT_ENROLLED = "not_enrolled_per_salary_structure"
    replacements = {}
    if not structure.pf_applicable:
        replacements["pf"] = PFResult(applied=False, exemption_reason=NOT_ENROLLED)
    if not structure.esi_applicable:
        replacements["esi"] = ESIResult(applied=False, exemption_reason=NOT_ENROLLED)
    if not structure.pt_applicable:
        replacements["professional_tax"] = PTResult(applied=False, exemption_reason=NOT_ENROLLED)
    if not structure.tds_applicable:
        replacements["income_tax"] = TDSResult()
    if not structure.gratuity_applicable:
        replacements["gratuity"] = GratuityResult(is_eligible=False, exemption_reason=NOT_ENROLLED)

    return dataclasses.replace(result, **replacements) if replacements else result


def _persist(
    run, employee, structure, paid_days, lop_days, earnings, gross, adjustments, result, actor,
    attendance_summary: dict | None = None,
) -> Payslip:
    """
    Write the payslip, its lines and its statutory contributions.

    Every amount inside a total also appears as a line, so the payslip can
    always be explained to the person who received it.
    """
    employee_deductions = result.employee_deductions + sum(
        (a.amount for a in adjustments if a.is_deduction), ZERO
    )
    employer_contributions = result.employer_contributions + sum(
        (a.amount for a in adjustments if a.is_employer_side), ZERO
    )

    payslip = Payslip.objects.create(
        payroll_run=run,
        employee=employee,
        salary_structure=structure,
        paid_days=paid_days,
        lop_days=lop_days,
        gross_earnings=gross,
        total_deductions=employee_deductions,
        employer_contributions=employer_contributions,
        net_pay=gross - employee_deductions,
        location=employee.location,
        state=(employee.location.state if employee.location else ""),
        warnings=list(result.warnings),
        attendance_summary=attendance_summary or {},
        created_by=actor,
        updated_by=actor,
    )

    lines = [
        PayslipLine(payslip=payslip, component=component, label=component.name,
                    component_type=ComponentType.EARNING, amount=amount,
                    display_order=component.display_order)
        for component, amount in earnings
    ]

    for adjustment in adjustments:
        lines.append(PayslipLine(
            payslip=payslip, label=adjustment.label,
            component_type=(
                ComponentType.DEDUCTION if adjustment.is_deduction else ComponentType.EARNING
            ),
            amount=adjustment.amount,
            is_employer_side=adjustment.is_employer_side,
            display_order=500,
        ))

    lines.extend(_statutory_lines(payslip, result))
    PayslipLine.objects.bulk_create(lines)

    StatutoryContribution.objects.bulk_create(
        _statutory_contributions(payslip, result, payslip.state)
    )

    # Adjustments are marked APPLIED so the next run cannot pay them twice.
    # Loan recoveries are synthesised per run and have no row to mark.
    applied = [a.pk for a in adjustments if a.pk]
    if applied:
        PayrollAdjustment.objects.filter(pk__in=applied).update(
            status=AdjustmentStatus.APPLIED, payroll_run=run
        )
    for loan in employee.loans.filter(status=LoanStatus.ACTIVE, is_active=True):
        due = loan.installment_due()
        if due > ZERO:
            loan.balance -= due
            loan.status = LoanStatus.CLOSED if loan.balance <= ZERO else LoanStatus.ACTIVE
            loan.save(update_fields=["balance", "status", "updated_at"])

    return payslip


def _statutory_lines(payslip, result) -> list[PayslipLine]:
    rows = [
        (result.pf.employee, "Provident Fund (employee)", ComponentType.STATUTORY_DEDUCTION, False, 600),
        (result.esi.employee, "ESI (employee)", ComponentType.STATUTORY_DEDUCTION, False, 610),
        (result.professional_tax.amount, "Professional Tax", ComponentType.STATUTORY_DEDUCTION, False, 620),
        (result.income_tax.monthly_tds, "TDS", ComponentType.STATUTORY_DEDUCTION, False, 630),
        (result.pf.employer_total, "Provident Fund (employer)", ComponentType.EMPLOYER_CONTRIBUTION, True, 700),
        (result.pf.admin_charge, "PF administrative charge", ComponentType.EMPLOYER_CONTRIBUTION, True, 710),
        (result.esi.employer, "ESI (employer)", ComponentType.EMPLOYER_CONTRIBUTION, True, 720),
        (result.gratuity.monthly_provision, "Gratuity provision", ComponentType.EMPLOYER_CONTRIBUTION, True, 730),
    ]
    return [
        PayslipLine(payslip=payslip, label=label, component_type=kind,
                    amount=amount, is_employer_side=employer, display_order=order)
        for amount, label, kind, employer, order in rows
        if amount and amount != ZERO
    ]


def _statutory_contributions(payslip, result, state: str) -> list[StatutoryContribution]:
    return [
        StatutoryContribution(
            payslip=payslip, kind=StatutoryKind.PF,
            employee_amount=result.pf.employee,
            employer_amount=result.pf.employer_total + result.pf.admin_charge,
            base_wage=result.pf.base_wage, applied=result.pf.applied,
            exemption_reason=result.pf.exemption_reason,
        ),
        StatutoryContribution(
            payslip=payslip, kind=StatutoryKind.ESI,
            employee_amount=result.esi.employee, employer_amount=result.esi.employer,
            base_wage=result.esi.base_wage, applied=result.esi.applied,
            exemption_reason=result.esi.exemption_reason,
        ),
        StatutoryContribution(
            payslip=payslip, kind=StatutoryKind.PT,
            employee_amount=result.professional_tax.amount, state=state,
            base_wage=payslip.gross_earnings, applied=result.professional_tax.applied,
            exemption_reason=result.professional_tax.exemption_reason,
        ),
        StatutoryContribution(
            payslip=payslip, kind=StatutoryKind.TDS,
            employee_amount=result.income_tax.monthly_tds,
            base_wage=payslip.gross_earnings, applied=True,
        ),
        StatutoryContribution(
            payslip=payslip, kind=StatutoryKind.GRATUITY,
            employer_amount=result.gratuity.monthly_provision,
            base_wage=result.gratuity.monthly_provision, applied=result.gratuity.is_eligible,
            exemption_reason=result.gratuity.exemption_reason,
        ),
    ]


# ---------------------------------------------------------------------------
# Approval and the gates
# ---------------------------------------------------------------------------


def payslip_deletable_until(payslip: Payslip):
    """The moment the Finance Head's correction window closes."""
    from django.conf import settings as django_settings

    window = int(getattr(django_settings, "PAYSLIP_DELETE_WINDOW_DAYS", 5))
    return payslip.created_at + dt.timedelta(days=window)


@transaction.atomic
def delete_payslip(*, payslip: Payslip, actor, reason: str) -> None:
    """
    Withdraw one generated payslip — the Finance Head's five-day correction.

    Within the window (PAYSLIP_DELETE_WINDOW_DAYS from generation) the slip is
    soft-deleted with the reason on record; its run's totals are restated so
    the run keeps telling the truth about what it now pays. Past the window
    the payslip is locked for everyone — Finance included — and the remedy
    becomes a run reversal, which is the fully recorded path.

    Deliberately a QUERYSET update: a payslip on an approved run refuses
    ordinary saves (that guard is correct), and this is the one sanctioned,
    audited amendment that may pass it.
    """
    if not reason.strip():
        raise PayrollError("Deleting a payslip requires a reason.")

    deadline = payslip_deletable_until(payslip)
    if timezone.now() > deadline:
        raise PayrollError(
            f"This payslip was generated on {timezone.localtime(payslip.created_at):%d %b %Y} "
            f"and its correction window closed on {timezone.localtime(deadline):%d %b %Y}. "
            f"It is locked now — if it is genuinely wrong, reverse the payroll run instead."
        )

    run = payslip.payroll_run
    snapshot = {
        "employee": payslip.employee.employee_code,
        "period": f"{run.period_year}-{run.period_month:02d}",
        "run_sequence": run.sequence,
        "gross_earnings": str(payslip.gross_earnings),
        "net_pay": str(payslip.net_pay),
    }

    Payslip.objects.filter(pk=payslip.pk).update(is_active=False, updated_by=actor)

    # Restate the run's summary from what it still actually pays. Queryset
    # update on purpose — the locked-run guard rightly refuses ordinary saves,
    # and this amendment is the audited exception travelling with the delete.
    remaining = run.payslips.filter(is_active=True)
    totals = dict(run.totals or {})
    totals["employee_count"] = remaining.count()
    for field in ("gross_earnings", "total_deductions", "net_pay", "employer_contributions"):
        totals[field] = str(sum((getattr(s, field) for s in remaining), ZERO))
    PayrollRun.objects.filter(pk=run.pk).update(totals=totals)

    audit_event(
        payslip, actor=actor, entity_type="Payslip", verb="delete",
        before=snapshot, after={"deleted": True},
        reason=reason,
    )


def unverified_rule_sets(run: PayrollRun) -> list[str]:
    """
    Which of the rule sets this run used are not verified by Finance.

    Returns every one, not the first: finance should be able to fix them in a
    single pass rather than discovering them one refused approval at a time.
    """
    return [
        f"{statute}"
        f"{'/' + ref['jurisdiction'] if ref.get('jurisdiction') else ''}"
        f"{' (' + ref['regime'] + ')' if ref.get('regime') else ''}"
        f" — status '{ref.get('verification_status', 'unknown')}'"
        for statute, ref in (run.rule_sets_used or {}).items()
        if ref.get("verification_status") != VerificationStatus.VERIFIED
    ]


@transaction.atomic
def approve_run(run: PayrollRun, *, actor, notes: str = "") -> PayrollRun:
    """
    Release the run. Locks it against any further change of substance.

    Both gates are enforced here rather than in the view, so a direct service
    call, a management command and an API request all face the same refusal.
    """
    if run.status != PayrollRunStatus.REVIEW:
        raise PayrollError(
            f"Only a run in review can be approved; this one is "
            f"{run.get_status_display().lower()}."
        )
    if not run.payslips.exists():
        raise PayrollError("This run has no payslips. Process it before approving.")

    blockers = unverified_rule_sets(run)
    if blockers:
        raise PayrollError(
            "This run was computed against statutory rates that Finance has not "
            "verified, so it cannot be approved:\n  - "
            + "\n  - ".join(blockers)
            + "\nVerify each rate set against its gazette source, then re-process "
              "the run so it picks up the verified figures."
        )

    if run.run_by_id and run.run_by_id == getattr(actor, "pk", None):
        raise PermissionDenied(
            "You processed this run, so you cannot also approve it. Payroll "
            "approval requires a second person — whoever prepares the money must "
            "not be the one who releases it."
        )

    # Re-checked at the release point, not only at process time: two runs of
    # the same period processed in parallel could each have missed the other's
    # payslips. Money moves on approval, so approval is where the one-slip-
    # per-person-per-period rule is final.
    if run.run_type == RunType.REGULAR:
        duplicated = list(
            Payslip.objects.filter(
                employee__in=run.payslips.values("employee"),
                payroll_run__period_year=run.period_year,
                payroll_run__period_month=run.period_month,
                payroll_run__run_type=RunType.REGULAR,
                payroll_run__is_active=True,
                is_active=True,
            )
            .exclude(payroll_run=run)
            .exclude(payroll_run__status=PayrollRunStatus.REVERSED)
            .values_list("employee__employee_code", flat=True)
            .distinct()
        )
        if duplicated:
            raise PayrollError(
                "These employees already have a payslip for this period on "
                "another run: " + ", ".join(sorted(duplicated)) + ". Re-process "
                "this run (they will be skipped) before approving — nobody is "
                "paid twice for one period."
            )

    run.status = PayrollRunStatus.APPROVED
    run.locked = True
    run.approved_by = actor
    run.approved_at = timezone.now()
    if notes:
        run.notes = f"{run.notes}\n{notes}".strip()
    run.save(update_fields=[
        "status", "locked", "approved_by", "approved_at", "notes", "updated_at"
    ])

    audit_event(run, actor=actor, entity_type="PayrollRun", verb="approve",
                before={"status": PayrollRunStatus.REVIEW},
                after={"status": run.status, "locked": True, "totals": run.totals})

    notify_events.payroll_approved(run)
    for payslip in run.payslips.select_related("employee__user"):
        notify_events.payslip_available(payslip)
    return run


@transaction.atomic
def reject_run(run: PayrollRun, *, actor, reason: str) -> PayrollRun:
    """Send a run back to draft, discarding its payslips."""
    if run.status != PayrollRunStatus.REVIEW:
        raise PayrollError("Only a run in review can be rejected.")
    if len(reason.strip()) < 10:
        raise PayrollError("Say why the run was rejected — the next person needs to know.")

    count = run.payslips.count()
    run.payslips.all().hard_delete()
    run.status = PayrollRunStatus.DRAFT
    run.totals = {}
    run.notes = f"{run.notes}\nRejected: {reason}".strip()
    run.save(update_fields=["status", "totals", "notes", "updated_at"])

    audit_event(run, actor=actor, entity_type="PayrollRun", verb="reject",
                after={"status": run.status, "payslips_discarded": count, "reason": reason})
    return run


@transaction.atomic
def mark_paid(run: PayrollRun, *, actor, paid_at=None) -> PayrollRun:
    if run.status != PayrollRunStatus.APPROVED:
        raise PayrollError("Only an approved run can be marked paid.")
    run.status = PayrollRunStatus.PAID
    run.paid_at = paid_at or timezone.now()
    run.save(update_fields=["status", "paid_at", "updated_at"])

    audit_event(run, actor=actor, entity_type="PayrollRun", verb="approve",
                after={"status": run.status, "paid_at": run.paid_at.isoformat()})
    return run


@transaction.atomic
def reverse_run(run: PayrollRun, *, actor, reason: str) -> PayrollRun:
    """
    Undo an approved or paid run.

    Reversal UNLOCKS the run but keeps its payslips and its history. The point
    is that the correction is itself a recorded event: an approved run that
    quietly became editable again would defeat the whole lock.
    """
    if run.status not in {PayrollRunStatus.APPROVED, PayrollRunStatus.PAID}:
        raise PayrollError("Only an approved or paid run can be reversed.")
    if len(reason.strip()) < 20:
        raise PayrollError(
            "A reversal undoes a payroll that was already released. Record why, "
            "in at least 20 characters."
        )

    previous = run.status
    run.status = PayrollRunStatus.REVERSED
    run.locked = False
    run.reversed_at = timezone.now()
    run.reversal_reason = reason
    run.save(update_fields=[
        "status", "locked", "reversed_at", "reversal_reason", "updated_at"
    ])

    audit_event(run, actor=actor, entity_type="PayrollRun", verb="reverse",
                before={"status": previous},
                after={"status": run.status, "reason": reason, "totals": run.totals})

    notify_events.payroll_reversed(run, reason=reason)
    return run
