"""
Employees' State Insurance — `esi.v1`.

ESI Act 1948 s.39 and s.2(9); ESI (Central) Rules 1950 Rule 50.

ESI assesses on GROSS wage — the mirror image of PF, and assessing either on
the other's base is the classic pair of payroll bugs.

Two behaviours that look like edge cases and are not:

  * The wage limit is a coverage test, NOT a contribution ceiling. An employee
    inside the limit contributes on their full wage; there is no capping.
  * Coverage that begins at the start of a contribution period continues to the
    end of that period even if wages later exceed the limit. Establishing that
    requires wage history, which Tier 1 must not read, so Tier 2 supplies it as
    `esi_already_liable_this_period`.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts import ESIResult, StatutoryContext
from . import params as p
from .money import money, percent_of

RULE = "esi.v1"

NOT_APPLICABLE = ESIResult(applied=False, exemption_reason="scheme_not_applicable")
ABOVE_THRESHOLD = ESIResult(applied=False, exemption_reason="above_wage_threshold")


def evaluate(ctx: StatutoryContext, parameters: Mapping[str, Any]) -> ESIResult:
    if not p.flag(parameters, "applies", rule=RULE):
        return NOT_APPLICABLE

    threshold = p.decimal(
        parameters,
        "disability_wage_threshold" if ctx.is_disabled else "gross_wage_threshold",
        rule=RULE,
    )
    inclusive = p.flag(parameters, "threshold_is_inclusive", rule=RULE)
    within_threshold = ctx.gross_wage <= threshold if inclusive else ctx.gross_wage < threshold

    if not within_threshold and not ctx.esi_already_liable_this_period:
        return ABOVE_THRESHOLD

    return ESIResult(
        base_wage=money(ctx.gross_wage),
        employee=percent_of(ctx.gross_wage, p.decimal(parameters, "employee_rate", rule=RULE)),
        employer=percent_of(ctx.gross_wage, p.decimal(parameters, "employer_rate", rule=RULE)),
        applied=True,
        continued_from_period_start=not within_threshold,
    )


def contribution_period(month: int, parameters: Mapping[str, Any]) -> tuple[int, int] | None:
    """
    The (start_month, end_month) contribution period containing `month`.

    Tier 2 needs this to decide whether liability carries over from the previous
    payslip or resets. Periods are data rather than a hardcoded Apr-Sep/Oct-Mar
    pair so a change to the calendar is a rule-set edit.
    """
    for period in p.rows(parameters, "contribution_periods", rule=RULE):
        start, end = int(period["start_month"]), int(period["end_month"])
        # A period may wrap the calendar year, e.g. October to March.
        contains = start <= month <= end if start <= end else month >= start or month <= end
        if contains:
            return start, end
    return None
