"""
Tier 2 adapter: turn a database of rule sets into a statutory assessment.

This is the only place the two tiers meet. It resolves the rule set in force
for each statute, hands the pure evaluator plain parameters, and records WHICH
rule sets produced the answer so the result stays explicable years later.

It also owns the two decisions Tier 1 is deliberately not allowed to make,
because both need history or configuration the evaluation core must not read:

  * which tax regime applies when the employee has not elected one, and
  * whether ESI liability carries over from earlier in the contribution period.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from apps.statutory import rules
from apps.statutory.contracts import (
    ESIResult,
    GratuityResult,
    PFResult,
    PTResult,
    RuleSetMissing,
    Statute,
    StatutoryAssessment,
    StatutoryContext,
    TaxRegime,
    TDSResult,
)
from apps.statutory.models import VerificationStatus

from ..resolver import allow_unverified, resolve, to_ref

logger = logging.getLogger("hrms.statutory")


def assess(ctx: StatutoryContext, *, require_verified: bool | None = None) -> StatutoryAssessment:
    """
    The complete statutory position for one employee, one period.

    `require_verified` defaults to the environment's posture: production always
    demands verified rule sets, dev and staging may opt out. A caller can force
    it on but never quietly off — an unverified assessment is not "mostly
    compliant", and `StatutoryAssessment.is_fully_verified` reports the truth
    regardless of which flag produced it.
    """
    if require_verified is None:
        require_verified = not allow_unverified()

    refs: dict[str, object] = {}
    warnings: list[str] = []

    pf = _pf(ctx, refs, warnings, require_verified)
    esi = _esi(ctx, refs, warnings, require_verified)
    pt = _professional_tax(ctx, refs, warnings, require_verified)
    gratuity = _gratuity(ctx, refs, warnings, require_verified)
    income_tax = _income_tax(ctx, refs, warnings, require_verified)

    for statute, ref in refs.items():
        if getattr(ref, "verification_status", "") != VerificationStatus.VERIFIED:
            warnings.append(
                f"{statute}: computed against a rule set that Finance has not verified."
            )

    return StatutoryAssessment(
        pf=pf,
        esi=esi,
        professional_tax=pt,
        gratuity=gratuity,
        income_tax=income_tax,
        rule_sets_used=refs,
        warnings=tuple(warnings),
    )


def _evaluate(
    statute: str,
    ctx: StatutoryContext,
    refs: dict,
    warnings: list[str],
    require_verified: bool,
    *,
    fallback,
    jurisdiction: str = "",
    regime: str = "",
    financial_year: str = "",
    rule_kwargs: dict | None = None,
):
    """
    Resolve one statute's rule set and evaluate it. Returns `(result, rule_set)`.

    A missing rule set is NOT fatal here — it is recorded as a warning and the
    statute contributes nothing. That looks like the "silently produce a payslip
    with no PF" failure the design warns against, and would be, except that
    `process_run` refuses to persist a run carrying any such warning. The gate
    is at the run, not the individual assessment, so finance sees every missing
    rule set at once instead of one failed run at a time.

    `rule_kwargs` is kept separate from the resolution arguments deliberately:
    `regime` selects WHICH rule set to load and is also an input the income-tax
    rule needs, and collapsing the two into one parameter is how it ends up
    passed twice.
    """
    try:
        rule_set = resolve(
            statute,
            on_date=ctx.period_start,
            jurisdiction=jurisdiction,
            regime=regime,
            financial_year=financial_year,
            require_verified=require_verified,
        )
    except RuleSetMissing as exc:
        warnings.append(str(exc))
        return fallback, None

    refs[statute] = to_ref(rule_set)
    result = rules.evaluate(
        statute, rule_set.rule_version, ctx, rule_set.parameters, **(rule_kwargs or {})
    )
    return result, rule_set


def _pf(ctx, refs, warnings, require_verified) -> PFResult:
    result, rule_set = _evaluate(
        Statute.PF, ctx, refs, warnings, require_verified, fallback=PFResult()
    )
    if rule_set is not None and not rules.pf.admin_charge_scope_is_settled(rule_set.parameters):
        warnings.append(
            "pf: the administrative-charge minimum is applied per employee, but whether "
            "it is a per-employee or per-establishment floor is unsettled (dispute "
            "pf-admin-charge-scope). Across a large establishment the two readings "
            "differ materially."
        )
    return result


def _esi(ctx, refs, warnings, require_verified) -> ESIResult:
    result, _ = _evaluate(
        Statute.ESI, ctx, refs, warnings, require_verified, fallback=ESIResult()
    )
    return result


def _professional_tax(ctx, refs, warnings, require_verified) -> PTResult:
    if not ctx.state:
        # No jurisdiction means no levy to assess — different from a state whose
        # rules nobody has configured, which is reported as a missing rule set.
        return PTResult(applied=False, exemption_reason="no_pt_jurisdiction")
    result, _ = _evaluate(
        Statute.PROFESSIONAL_TAX,
        ctx,
        refs,
        warnings,
        require_verified,
        jurisdiction=ctx.state,
        fallback=PTResult(),
    )
    return result


def _gratuity(ctx, refs, warnings, require_verified) -> GratuityResult:
    result, _ = _evaluate(
        Statute.GRATUITY, ctx, refs, warnings, require_verified, fallback=GratuityResult()
    )
    return result


def _income_tax(ctx, refs, warnings, require_verified) -> TDSResult:
    regime = ctx.tax_regime or default_regime(
        ctx.period_start, ctx.financial_year, require_verified=require_verified
    )
    if regime is None:
        warnings.append(
            "income_tax: no rule set is marked as the default regime, and the employee "
            "has not elected one. TDS cannot be computed."
        )
        return TDSResult()

    result, _ = _evaluate(
        Statute.INCOME_TAX,
        ctx,
        refs,
        warnings,
        require_verified,
        regime=regime,
        financial_year=ctx.financial_year,
        fallback=TDSResult(regime_used=regime),
        # The rule needs the regime too — it decides whether the s.87A rebate
        # applies — and must not infer it from which file it was loaded from.
        rule_kwargs={"regime": regime},
    )
    return result


def default_regime(
    on_date: date, financial_year: str, *, require_verified: bool = True
) -> str | None:
    """
    The regime that applies to an employee who has not elected one.

    Read from the rule sets themselves rather than hardcoded, because which
    regime is the default is a statutory fact that has changed and will change
    again. Declaring investments is NOT an election — an employee who declares
    80C and elects nothing falls to the default and loses those deductions,
    which is the correct and expensive answer.
    """
    for regime in (TaxRegime.NEW, TaxRegime.OLD):
        try:
            rule_set = resolve(
                Statute.INCOME_TAX,
                on_date=on_date,
                regime=regime,
                financial_year=financial_year,
                require_verified=require_verified,
            )
        except RuleSetMissing:
            continue
        if rule_set.parameters.get("is_default_regime"):
            return regime
    return None


def esi_liable_at_period_start(employee_id, ctx: StatutoryContext) -> bool:
    """
    Whether ESI liability carries over from earlier in the contribution period.

    Once covered at the start of a contribution period, an employee stays
    covered to the end of it even if wages later exceed the limit. Establishing
    that needs the payslip history Tier 1 must not read, so it is answered here
    and passed in as a plain flag.
    """
    from apps.payroll.models import Payslip, PayrollRunStatus, StatutoryKind

    period = _contribution_period_months(ctx)
    if not period:
        return False

    return Payslip.objects.filter(
        employee_id=employee_id,
        payroll_run__period_month__in=period,
        payroll_run__period_year=_period_year_for(ctx, period),
        payroll_run__status__in=[PayrollRunStatus.APPROVED, PayrollRunStatus.PAID],
        statutory_contributions__kind=StatutoryKind.ESI,
        statutory_contributions__applied=True,
    ).exists()


def _contribution_period_months(ctx: StatutoryContext) -> list[int]:
    """Months of the contribution period containing this one, up to but excluding it."""
    # April-September and October-March, per the ESI rule set. Read from the
    # rule set rather than hardcoded so a calendar change is a data change.
    try:
        rule_set = resolve(Statute.ESI, on_date=ctx.period_start, require_verified=False)
    except RuleSetMissing:
        return []

    period = rules.esi.contribution_period(ctx.period_month, rule_set.parameters)
    if period is None:
        return []

    start, end = period
    months = (
        list(range(start, end + 1))
        if start <= end
        else list(range(start, 13)) + list(range(1, end + 1))
    )
    return [m for m in months if m != ctx.period_month]


def _period_year_for(ctx: StatutoryContext, months: list[int]) -> int:
    """
    The calendar year those earlier months fall in.

    A period that wraps the year (October-March) puts January-March in the
    following year, so a March payslip looking back at October must look at the
    previous one.
    """
    if ctx.period_month <= 3 and any(m >= 10 for m in months):
        return ctx.period_start.year - 1
    return ctx.period_start.year


def zero_context(period_start: date, financial_year: str) -> StatutoryContext:
    """A context with no wages — used to probe rule-set availability."""
    return StatutoryContext(
        period_start=period_start,
        financial_year=financial_year,
        period_month=period_start.month,
        gross_wage=Decimal("0.00"),
        pf_wage=Decimal("0.00"),
    )
