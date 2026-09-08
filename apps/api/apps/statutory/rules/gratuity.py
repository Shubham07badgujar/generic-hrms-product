"""
Gratuity — `gratuity.v1`.

Payment of Gratuity Act 1972 — s.4(1) eligibility, s.4(2) formula, s.4(3) cap.

    gratuity = last drawn (Basic + DA) x 15 / 26 x completed years

Two distinctions this keeps apart that are routinely conflated:

  * PROVISION vs ENTITLEMENT. The monthly provision is an accounting accrual
    that starts in month one; the payable amount is zero until five years of
    continuous service. Reporting one number for both either overstates a
    liability or ambushes finance at exit.
  * Rounding happens ONCE, on the final figure. Rounding the daily wage first
    loses paise that compound with every year of service.

Completed years are supplied by the caller. The s.2A rule that 240 days counts
as a year needs attendance history, which is a Tier 2 concern — re-deriving it
here would give two answers that eventually disagree.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from ..contracts import GratuityResult, StatutoryContext
from . import params as p
from .money import ZERO, money

RULE = "gratuity.v1"

MONTHS_PER_YEAR = Decimal("12")


def evaluate(ctx: StatutoryContext, parameters: Mapping[str, Any]) -> GratuityResult:
    numerator = p.decimal(parameters, "formula_numerator", rule=RULE)
    denominator = p.decimal(parameters, "formula_denominator", rule=RULE)
    if denominator == ZERO:
        raise p.ParametersInvalid(f"{RULE}: parameter 'formula_denominator' cannot be zero.")

    # One completed year's entitlement, deliberately UNROUNDED so the final
    # figure rounds once rather than once per step.
    per_year = ctx.last_drawn_monthly_wage * numerator / denominator

    provision_months = p.decimal(parameters, "provision_months_per_year", rule=RULE)
    monthly_provision = money(per_year / provision_months) if provision_months else ZERO

    eligibility_years = p.decimal(parameters, "eligibility_years", rule=RULE)
    is_eligible = ctx.completed_service_years >= eligibility_years

    if not is_eligible:
        return GratuityResult(
            monthly_provision=monthly_provision,
            is_eligible=False,
            exemption_reason="below_eligibility_period",
        )

    payable = per_year * _reckonable_years(ctx.completed_service_years, parameters)
    maximum = p.optional_decimal(parameters, "statutory_maximum", rule=RULE)
    capped = maximum is not None and payable > maximum

    return GratuityResult(
        monthly_provision=monthly_provision,
        accrued_total=money(maximum if capped else payable),
        is_eligible=True,
        capped=capped,
    )


def _reckonable_years(service_years: Decimal, parameters: Mapping[str, Any]) -> Decimal:
    """
    Completed years for the formula, with a part-year over six months counting
    as a full one (s.4(2)).

    Rounding by year rather than pro-rating is the point: 7 years 8 months earns
    8 years' gratuity, not 7.67 years' worth. The pivot month is a parameter
    because it is a statutory figure, not an arithmetic convention.
    """
    pivot = p.decimal(parameters, "months_for_year_rounding", rule=RULE)
    whole = Decimal(int(service_years))
    part_months = (service_years - whole) * MONTHS_PER_YEAR
    return whole + 1 if part_months >= pivot else whole
