"""
Provident Fund — `pf.v1`.

EPF & MP Act 1952 s.6; Employees' Pension Scheme 1995 para 3.

Three things here are easy to get wrong and are pinned by fixtures:

  * PF is assessed on Basic + DA (`pf_wage`), NEVER on gross. The context keeps
    the two apart so the mistake needs a deliberate edit rather than a slip.
  * The employer's EPF share is derived by SUBTRACTING the rounded EPS from the
    rounded employer total, not by computing 3.67% independently. The two agree
    at some wages and diverge at others; only subtraction guarantees that
    EPS + EPF equals the 12% employer contribution exactly.
  * The PF ceiling and the EPS ceiling are separate parameters. An international
    worker contributes on full wage while their EPS stays capped, so a single
    shared ceiling cannot express the position.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from ..contracts import PFResult, StatutoryContext
from . import params as p
from .money import ZERO, money, percent_of

RULE = "pf.v1"

#: Recorded when the charge's scope has not been settled. The verification gate
#: refuses live payroll on an unverified rule set, so this surfaces the reason
#: rather than standing in for the decision.
UNDETERMINED = "UNDETERMINED"

NOT_APPLICABLE = PFResult(applied=False, exemption_reason="scheme_not_applicable")
OPTED_OUT = PFResult(applied=False, exemption_reason="member_opted_out")


def evaluate(ctx: StatutoryContext, parameters: Mapping[str, Any]) -> PFResult:
    if not p.flag(parameters, "applies", rule=RULE):
        return NOT_APPLICABLE
    if ctx.pf_opted_out:
        return OPTED_OUT

    wage_ceiling = p.decimal(parameters, "wage_ceiling", rule=RULE)
    eps_ceiling = p.decimal(parameters, "eps_wage_ceiling", rule=RULE)

    # The ceiling does not bind international workers (EPF Scheme para 83), but
    # their EPS stays capped — which is the entire reason these are two params.
    base = ctx.pf_wage if ctx.is_international_worker else min(ctx.pf_wage, wage_ceiling)

    employee = percent_of(base, p.decimal(parameters, "employee_rate", rule=RULE))
    employer_total = percent_of(base, p.decimal(parameters, "employer_rate", rule=RULE))

    eps = percent_of(min(base, eps_ceiling), p.decimal(parameters, "eps_rate", rule=RULE))
    eps_cap = p.optional_decimal(parameters, "eps_monthly_cap", rule=RULE)
    if eps_cap is not None:
        eps = min(eps, eps_cap)

    return PFResult(
        base_wage=money(base),
        employee=employee,
        employer_eps=eps,
        employer_epf=employer_total - eps,
        admin_charge=_admin_charge(base, parameters),
        applied=True,
    )


def _admin_charge(base: Decimal, parameters: Mapping[str, Any]) -> Decimal:
    """
    Employer-borne administrative charge, over and above the 12%.

    The monthly minimum is applied per employee here. Whether that is the
    correct scope is an open question (`pf-admin-charge-scope`); if the answer
    is per-establishment the floor belongs in Tier 2, applied once to the run
    total, and this becomes the bare percentage. `admin_charge_minimum_scope`
    carries the answer so settling it is a rule-set edit, not a rewrite.
    """
    if base <= ZERO:
        # A floor is a minimum charge for participating, not a levy on someone
        # who earned nothing this month.
        return ZERO

    charge = percent_of(base, p.decimal(parameters, "admin_charge_rate", rule=RULE))
    minimum = p.optional_decimal(parameters, "admin_charge_minimum", rule=RULE)
    if minimum is None:
        return charge

    if str(parameters.get("admin_charge_minimum_scope", UNDETERMINED)) == "per_establishment":
        return charge
    return max(charge, minimum)


def admin_charge_scope_is_settled(parameters: Mapping[str, Any]) -> bool:
    """Tier 2 uses this to warn that a run carries an unsettled interpretation."""
    return str(parameters.get("admin_charge_minimum_scope", UNDETERMINED)) in {
        "per_employee",
        "per_establishment",
    }
