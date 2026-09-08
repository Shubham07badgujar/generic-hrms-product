"""
Money arithmetic for statutory evaluation. Pure — no Django, no settings.

Two decisions are fixed here because they are wrong in different ways
everywhere else, and both were asserted by fixtures before any code existed:

  * Rounding is HALF-UP to two decimals, never banker's rounding. Python's
    Decimal defaults to ROUND_HALF_EVEN, which would turn 0.125 into 0.12 and
    silently under-deduct on exactly the values that sit on a boundary.
  * Each component rounds independently and totals are derived from the ROUNDED
    parts, so a payslip's lines always add up to its totals. Summing unrounded
    values and rounding at the end produces totals that disagree with the lines
    by a paisa, which is indefensible on a statutory return.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def money(value: Decimal | int | str) -> Decimal:
    """Round half-up to two decimals."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def percent_of(base: Decimal, rate: Decimal) -> Decimal:
    """`rate` is expressed in percent (12 means 12%), rounded half-up to paise."""
    return money(base * rate / HUNDRED)
