"""
Income tax / TDS on salary — `income_tax.v2`.

Income-tax Act 1961 — s.192 (TDS on salary), s.115BAC (new regime),
s.87A (rebate), Chapter VI-A (deductions), plus surcharge and cess per the
applicable Finance Act.

THE ORDER OF OPERATIONS IS THE WHOLE THING:

    1. taxable income = gross - standard deduction - allowed deductions
    2. slab tax       = marginal rates over the bands
    3. s.87A rebate   = subtracted from slab tax (with marginal relief if enabled)
    4. surcharge      = rate x tax-after-rebate, by band (with its own relief)
    5. cess           = rate x (tax-after-rebate + surcharge)
    6. annual tax     = tax-after-rebate + surcharge + cess

Applying cess before surcharge, or the rebate after surcharge, both produce
wrong answers that look plausible.

Slabs are MARGINAL — each rate applies only to its own slice of income. A flat
rate on the whole amount over-taxes dramatically and is the single most common
tax bug.

The two marginal-relief mechanisms are separate parameters because a regime can
enable one and not the other: FY 2025-26's old regime has a real cliff at the
s.87A threshold but smooths the surcharge threshold. A single global flag
cannot express that and would be wrong at one threshold or the other.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from ..contracts import StatutoryContext, TDSResult
from . import params as p
from .money import CENT, ZERO, money, percent_of

RULE = "income_tax.v2"

MONTHS_PER_YEAR = Decimal("12")


def evaluate(
    ctx: StatutoryContext, parameters: Mapping[str, Any], *, regime: str
) -> TDSResult:
    allowed = _allowed_deductions(ctx.declared_deductions, parameters)

    taxable = (
        ctx.annual_gross_projection
        - p.decimal(parameters, "standard_deduction", rule=RULE)
        - sum(allowed.values(), ZERO)
    )
    taxable = max(ZERO, taxable)

    slabs = _bands(p.rows(parameters, "slabs", rule=RULE), "min_income", "max_income")
    tax_before_rebate = _slab_tax(taxable, slabs)

    rebate, relief = _rebate(taxable, tax_before_rebate, parameters, regime)
    after_rebate = tax_before_rebate - rebate - relief

    surcharge = _surcharge(taxable, after_rebate, slabs, parameters)
    cess = percent_of(after_rebate + surcharge, p.decimal(parameters, "cess_rate", rule=RULE))
    annual = after_rebate + surcharge + cess

    return TDSResult(
        regime_used=regime,
        taxable_income=money(taxable),
        tax_before_rebate=tax_before_rebate,
        rebate_applied=rebate,
        marginal_relief=relief,
        surcharge=surcharge,
        cess=cess,
        annual_tax=annual,
        monthly_tds=money(annual / MONTHS_PER_YEAR),
        deductions_allowed=allowed,
    )


# ---------------------------------------------------------------- deductions


def _allowed_deductions(
    declared: Mapping[str, Decimal], parameters: Mapping[str, Any]
) -> dict[str, Decimal]:
    """
    Declarations clamped to their own caps.

    Over-declaration is CLAMPED, never rejected — employees over-declare
    routinely and a hard failure would block the whole payroll run for one
    person's optimistic 80C figure. A section absent from `deduction_caps` is
    not allowed at all: the new regime's empty map is how it disallows
    Chapter VI-A entirely, so "not listed" has to mean "not allowed".

    Caps apply INDEPENDENTLY per section; there is no shared pool.
    """
    caps = p.mapping(parameters, "deduction_caps", rule=RULE)
    allowed: dict[str, Decimal] = {}
    for section, amount in (declared or {}).items():
        if section not in caps:
            continue
        value = Decimal(str(amount))
        cap = caps[section]
        allowed[section] = value if cap is None else min(value, Decimal(str(cap)))
    return allowed


# --------------------------------------------------------------------- slabs


def _bands(
    rows: list[Mapping[str, Any]], low_key: str, high_key: str
) -> list[tuple[Decimal, Decimal | None, Decimal]]:
    """
    Normalise slab rows into `(floor, ceiling, rate)` bands, where `floor` is the
    EXCLUSIVE lower edge — the highest income that is NOT in this band.

    Rate sets express boundaries with a paisa gap (400000.00 / 400000.01) so the
    edge is explicit in the data rather than implied by a comparison operator.
    Slicing from the stated `min_income` would drop that paisa from every band,
    so each band's floor is the PREVIOUS band's ceiling instead. The gap is
    validated rather than assumed: a real hole in the table is a configuration
    error, not a silently untaxed strip of income.
    """
    bands: list[tuple[Decimal, Decimal | None, Decimal]] = []
    previous_ceiling: Decimal | None = None

    for row in rows:
        stated_floor = Decimal(str(row[low_key]))
        ceiling = None if row.get(high_key) is None else Decimal(str(row[high_key]))
        rate = Decimal(str(row["rate"]))

        if previous_ceiling is None:
            # A table need not start at zero: surcharge bands begin part-way up,
            # and their first floor is the last income below the threshold.
            floor = max(ZERO, stated_floor - CENT) if stated_floor > ZERO else ZERO
        else:
            gap = stated_floor - previous_ceiling
            if gap < ZERO or gap > CENT:
                raise p.ParametersInvalid(
                    f"{RULE}: band starting at {stated_floor} does not adjoin the previous "
                    f"band ending at {previous_ceiling}. Bands must be contiguous."
                )
            floor = previous_ceiling

        if ceiling is not None and ceiling < stated_floor:
            raise p.ParametersInvalid(
                f"{RULE}: band {stated_floor}-{ceiling} ends before it begins."
            )

        bands.append((floor, ceiling, rate))
        if ceiling is None:
            break
        previous_ceiling = ceiling

    return bands


def _slab_tax(taxable: Decimal, bands: list[tuple[Decimal, Decimal | None, Decimal]]) -> Decimal:
    """Marginal tax: each rate applies only to the slice of income inside its band."""
    total = ZERO
    for floor, ceiling, rate in bands:
        if taxable <= floor:
            break
        top = taxable if ceiling is None else min(taxable, ceiling)
        total += (top - floor) * rate / Decimal("100")
    return money(total)


# -------------------------------------------------------------------- rebate


def _rebate(
    taxable: Decimal,
    tax: Decimal,
    parameters: Mapping[str, Any],
    regime: str,
) -> tuple[Decimal, Decimal]:
    """
    s.87A rebate, and its marginal relief. Returns `(rebate, relief)`.

    Without the rebate every employee in the band below the threshold is
    over-deducted all year. Without its marginal relief there is a cliff where
    earning one rupee more costs tens of thousands — so where a regime enables
    relief, tax across the relief band is capped at the income above the
    threshold, making the extra tax never exceed the extra income.
    """
    config = parameters.get("rebate")
    if not isinstance(config, Mapping):
        return ZERO, ZERO
    if str(config.get("applies_to_regime", "")) != regime:
        return ZERO, ZERO

    threshold = Decimal(str(config["max_taxable_income"]))

    if taxable <= threshold:
        return min(tax, Decimal(str(config["max_amount"]))), ZERO

    if not bool(config.get("marginal_relief_enabled", False)):
        return ZERO, ZERO

    excess = taxable - threshold
    relief = tax - excess
    return ZERO, relief if relief > ZERO else ZERO


# ----------------------------------------------------------------- surcharge


def _surcharge(
    taxable: Decimal,
    tax_after_rebate: Decimal,
    slabs: list[tuple[Decimal, Decimal | None, Decimal]],
    parameters: Mapping[str, Any],
) -> Decimal:
    """
    Surcharge on high incomes, with marginal relief across each threshold.

    Relief guarantees that crossing a surcharge threshold never costs more in
    additional tax than the additional income earned — the same invariant as the
    rebate relief, computed against the tax payable at the threshold itself.
    """
    config = parameters.get("surcharge")
    if not isinstance(config, Mapping):
        return ZERO

    bands = _bands(p.rows(config, "bands", rule=RULE), "min_income", "max_income")
    band = next(
        ((floor, rate) for floor, ceiling, rate in bands
         if taxable > floor and (ceiling is None or taxable <= ceiling)),
        None,
    )
    if band is None:
        return ZERO

    threshold, rate = band
    surcharge = percent_of(tax_after_rebate, rate)

    if not bool(config.get("marginal_relief_enabled", False)):
        return surcharge

    ceiling_on_total = _slab_tax(threshold, slabs) + (taxable - threshold)
    excess = (tax_after_rebate + surcharge) - ceiling_on_total
    return max(ZERO, surcharge - excess) if excess > ZERO else surcharge


# ------------------------------------------------------------------- helpers


def is_default_regime(parameters: Mapping[str, Any]) -> bool:
    """
    Whether this rate set is the regime that applies when nobody has elected one.

    Tier 2 uses it to pick a regime for an employee who declared nothing.
    Deliberately NOT inferred from the presence of deductions: declaring 80C is
    not an election, and guessing otherwise produces a TDS figure that disagrees
    with the employee's eventual return.
    """
    return p.flag(parameters, "is_default_regime", rule=RULE)
