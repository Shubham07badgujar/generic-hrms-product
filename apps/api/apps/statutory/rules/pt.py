"""
Professional Tax — `pt.v2`.

A state levy, so the rule is generic and every state-specific figure lives in
its own jurisdiction-keyed rule set. `v2` adds eligibility-based exemptions.

Professional Tax is a STEP function, not a marginal rate: the matched slab's
amount is the whole liability, so a one-paisa move across a band edge changes
the deduction by the full difference between bands. It is also not pro-rated —
an employee who works one day owes the same as one who works the month.

Exemption conditions come from a fixed, typed vocabulary rather than an
expression language. A rule set is data that Finance edits and verifies; making
it executable would mean a verifier signing off on code.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from ..contracts import PTResult, StatutoryContext
from . import params as p
from .money import money

RULE = "pt.v2"

#: Condition keys a rule set may use. Anything else is a configuration error
#: rather than a condition that silently never matches — an unrecognised key
#: would otherwise read as "exemption defined" while exempting nobody.
SUPPORTED_CONDITIONS = frozenset({"gender", "max_monthly_wage", "min_monthly_wage"})


def evaluate(ctx: StatutoryContext, parameters: Mapping[str, Any]) -> PTResult:
    exemption = _matching_exemption(ctx, parameters)
    if exemption is not None:
        result = exemption.get("result") or {}
        return PTResult(
            amount=money(Decimal(str(result.get("monthly_amount", "0.00")))),
            applied=True,
            exemption_reason=str(exemption.get("id", "")),
            exemption_id=str(exemption.get("id", "")),
        )

    slabs = p.rows(parameters, "slabs", rule=RULE)
    slab = _matching_slab(ctx.gross_wage, slabs)
    if slab is None:
        # Wages below the lowest band attract nothing. Note this is a gap in the
        # slab table, not an absent rule set — a missing rule set raises during
        # resolution, because "this state levies nothing" and "nobody configured
        # this state" must not produce the same payslip.
        return PTResult(applied=True)

    amount = Decimal(str(slab["monthly_amount"]))
    special = _special_amount(slab, slabs, ctx.period_month, parameters)

    return PTResult(
        amount=money(special if special is not None else amount),
        applied=True,
        is_special_month=special is not None,
    )


def _matching_exemption(
    ctx: StatutoryContext, parameters: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """First exemption whose every condition holds. Evaluated before slab lookup."""
    for exemption in parameters.get("exemptions") or []:
        conditions = exemption.get("conditions") or {}
        unknown = set(conditions) - SUPPORTED_CONDITIONS
        if unknown:
            raise p.ParametersInvalid(
                f"{RULE}: exemption '{exemption.get('id', '?')}' uses unsupported "
                f"condition(s) {sorted(unknown)}. Supported: {sorted(SUPPORTED_CONDITIONS)}."
            )
        if _conditions_hold(ctx, conditions):
            return exemption
    return None


def _conditions_hold(ctx: StatutoryContext, conditions: Mapping[str, Any]) -> bool:
    if "gender" in conditions and ctx.gender != conditions["gender"]:
        return False
    if "max_monthly_wage" in conditions:
        if ctx.gross_wage > Decimal(str(conditions["max_monthly_wage"])):
            return False
    if "min_monthly_wage" in conditions:
        if ctx.gross_wage < Decimal(str(conditions["min_monthly_wage"])):
            return False
    return True


def _matching_slab(
    gross: Decimal, slabs: list[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    for slab in slabs:
        low = Decimal(str(slab["min_gross"]))
        high = slab.get("max_gross")
        if gross >= low and (high is None or gross <= Decimal(str(high))):
            return slab
    return None


def _special_amount(
    slab: Mapping[str, Any],
    slabs: list[Mapping[str, Any]],
    month: int,
    parameters: Mapping[str, Any],
) -> Decimal | None:
    """
    The higher levy some states charge in one month so the annual total reaches
    the statutory ceiling.

    It attaches to the TOP band — the one whose ordinary amount is the highest —
    not to every band. Treating it as a blanket month-wide override would
    over-charge every employee in a lower band once a year.
    """
    if parameters.get("special_month") is None or month != int(parameters["special_month"]):
        return None
    if parameters.get("special_amount") is None:
        return None

    top = max(slabs, key=lambda row: Decimal(str(row["monthly_amount"])))
    if Decimal(str(slab["monthly_amount"])) != Decimal(str(top["monthly_amount"])):
        return None
    return Decimal(str(parameters["special_amount"]))
