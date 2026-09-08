"""
GOLDEN MASTER: the engine must reproduce every hand-derived fixture exactly.

The fixtures came first and none of their expected values came from running the
engine — each carries a `derivation` block showing the arithmetic from the
rate-set parameters. That ordering is what makes them an oracle rather than a
regression snapshot of whatever the code happened to do. The previous system's
"golden master" was generated from its own output, so it locked in its bugs and
proved only that they had not changed.

DISPUTED expectations are NOT asserted. A case with an unresolved statutory
question still tests everything around it — `settled_expectations` is the part
that can be checked today — but the contested value is withheld, because
asserting either candidate would silently pick an answer the organisation has
not chosen. `test_disputes.py` keeps the open questions visible as a failure.

These tests need no database: Tier 1 takes parameters as plain data, so the
fixtures are fed straight in. That is the tier boundary paying for itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from apps.statutory import rules
from apps.statutory.contracts import Statute, StatutoryContext

from .loader import Case, RateSet, decimalise, load_cases, load_rate_sets

# ---------------------------------------------------------------------------
# Translating a fixture case into a call
# ---------------------------------------------------------------------------

#: `given` keys that configure the harness rather than describing the employee.
CONTROL_KEYS = frozenset({"_rate_set_override", "_raises"})

#: Context fields that are dates or plain values rather than money.
NON_DECIMAL_FIELDS = frozenset(
    {
        "state",
        "gender",
        "tax_regime",
        "period_month",
        "completed_service_days",
        "is_disabled",
        "is_international_worker",
        "pf_opted_out",
        "esi_already_liable_this_period",
        "declared_deductions",
    }
)


def build_context(case: Case) -> StatutoryContext:
    """
    A StatutoryContext from the case's `given` block.

    Defaults are deliberately neutral — a case that does not mention gender must
    not accidentally exercise a gender-conditioned exemption.
    """
    given = {k: v for k, v in case.given.items() if k not in CONTROL_KEYS}

    fields: dict[str, Any] = {
        "period_start": date(2025, 6, 1),
        "financial_year": "2025-2026",
        "period_month": int(given.pop("period_month", 6)),
    }

    for name, value in given.items():
        if name == "declared_deductions":
            fields[name] = {k: Decimal(str(v)) for k, v in (value or {}).items()}
        elif name in NON_DECIMAL_FIELDS:
            fields[name] = value
        else:
            fields[name] = decimalise(value)

    return StatutoryContext(**fields)


def rate_set_parameters(case: Case, rate_sets: dict[str, RateSet]) -> dict[str, Any]:
    """
    The rate set's parameters, with any per-case override applied.

    `_rate_set_override` exists so a case can exercise a parameter combination
    (an establishment where the scheme does not apply, say) without a whole
    extra fixture file that would then drift from the real one.
    """
    parameters = dict(rate_sets[case.rule_set_key].parameters)
    parameters.update(case.given.get("_rate_set_override") or {})
    return parameters


def evaluate_case(case: Case, rate_sets: dict[str, RateSet]) -> Any:
    rate_set = rate_sets[case.rule_set_key]
    parameters = rate_set_parameters(case, rate_sets)
    ctx = build_context(case)

    extra: dict[str, Any] = {}
    if case.statute == Statute.INCOME_TAX:
        extra["regime"] = resolve_regime(ctx, rate_sets)

    return rules.evaluate(case.statute, rate_set.rule_version, ctx, parameters, **extra)


def resolve_regime(ctx: StatutoryContext, rate_sets: dict[str, RateSet]) -> str:
    """
    Which regime applies when the employee has not elected one.

    Mirrors what Tier 2 does in production: read `is_default_regime` off the
    candidate rate sets rather than inferring an election from the presence of
    deductions. Declaring 80C is not an election, and treating it as one would
    be helpful and wrong.
    """
    if ctx.tax_regime:
        return ctx.tax_regime
    defaults = [
        rs.regime
        for rs in rate_sets.values()
        if rs.statute == Statute.INCOME_TAX and rs.parameters.get("is_default_regime")
    ]
    assert len(defaults) == 1, f"expected exactly one default regime, found {defaults}"
    return defaults[0]


def rate_set_for_regime(regime: str, rate_sets: dict[str, RateSet]) -> RateSet:
    matches = [
        rs for rs in rate_sets.values()
        if rs.statute == Statute.INCOME_TAX and rs.regime == regime
    ]
    assert len(matches) == 1, f"expected one {regime}-regime rate set, found {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# The golden master itself
# ---------------------------------------------------------------------------


def case_ids(cases: tuple[Case, ...]) -> list[str]:
    return [c.id for c in cases]


ALL_CASES = load_cases()

#: Cases expecting an exception assert resolver behaviour, not arithmetic, so
#: they are exercised in `test_resolver.py` where a database is available.
ASSERTABLE = tuple(c for c in ALL_CASES if "_raises" not in c.expect)


@pytest.mark.parametrize("case", ASSERTABLE, ids=case_ids(ASSERTABLE))
def test_case_matches_its_hand_derived_expectations(case: Case) -> None:
    """
    Every settled expectation, for every case, exactly.

    Failure output includes the derivation so the reviewer compares the engine
    against the stated arithmetic rather than against a bare number.
    """
    rate_sets = load_rate_sets()
    settled = case.settled_expectations
    if not settled:
        pytest.skip(f"{case.id}: every expectation awaits {', '.join(case.disputes)}")

    # An income-tax case may name one regime's rate set while asserting that a
    # non-declaring employee falls to the other — so evaluate against whichever
    # regime actually applies, not whichever file the case happens to live in.
    if case.statute == Statute.INCOME_TAX:
        ctx = build_context(case)
        regime = resolve_regime(ctx, rate_sets)
        rate_set = rate_set_for_regime(regime, rate_sets)
        parameters = dict(rate_set.parameters)
        parameters.update(case.given.get("_rate_set_override") or {})
        result = rules.evaluate(case.statute, rate_set.rule_version, ctx, parameters, regime=regime)
    else:
        result = evaluate_case(case, rate_sets)

    for field, expected_raw in settled.items():
        actual = getattr(result, field)
        expected = _coerce(expected_raw, actual)
        assert actual == expected, (
            f"\n{case.id}: {field}\n"
            f"  expected {expected!r}\n"
            f"  actual   {actual!r}\n"
            f"  source   {case.source}\n"
            f"  fixture  {case.path.name}\n"
            f"--- derivation ---\n{case.derivation}"
        )


def _coerce(expected: Any, actual: Any) -> Any:
    """Fixture values are strings; compare them as whatever the result field is."""
    if isinstance(actual, Decimal):
        return Decimal(str(expected))
    if isinstance(expected, dict):
        return {k: Decimal(str(v)) for k, v in expected.items()}
    return expected


@pytest.mark.parametrize(
    "case",
    [c for c in ALL_CASES if c.disputed],
    ids=case_ids(tuple(c for c in ALL_CASES if c.disputed)),
)
def test_disputed_values_are_never_asserted(case: Case) -> None:
    """
    A disputed case must actually withhold something.

    Guards the failure mode this whole arrangement exists to prevent: someone
    resolves a red test by filling in the number the engine produces, leaves the
    `disputed: true` marker in place, and the dispute becomes invisible while
    looking documented.
    """
    assert case.disputed_fields or case.disputes, (
        f"{case.id} is marked disputed but asserts every value. Either remove the "
        f"marker (the question is settled — record the decision in disputes.yaml) "
        f"or withhold the contested value with DISPUTED."
    )


# ---------------------------------------------------------------------------
# Structural properties — true whatever the disputed figures turn out to be
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regime", ["new", "old"])
def test_slab_tax_is_marginal_not_flat(regime: str) -> None:
    """
    Crossing a band taxes only the slice above it, never the whole income.

    Asserted on `tax_before_rebate` so the rebate and cess cannot mask the slab
    arithmetic. The flat-rate-on-total bug passes any single mid-band case and
    shows up only across a boundary, so it is a property rather than a fixture.
    """
    rate_set = rate_set_for_regime(regime, load_rate_sets())
    slabs = rate_set.parameters["slabs"]
    boundary = Decimal(str(slabs[1]["max_income"]))
    next_rate = Decimal(str(slabs[2]["rate"]))
    step = Decimal("100.00")

    at_boundary = _assess(rate_set, boundary).tax_before_rebate
    just_above = _assess(rate_set, boundary + step).tax_before_rebate

    assert just_above - at_boundary == step * next_rate / 100, (
        "crossing into a band must tax only the slice inside it"
    )
    assert at_boundary < boundary * next_rate / 100, (
        "a flat rate applied to the whole income would exceed the marginal result"
    )


@pytest.mark.parametrize("regime", ["new", "old"])
def test_surcharge_marginal_relief_caps_extra_tax_at_extra_income(regime: str) -> None:
    """
    THE invariant marginal relief exists to guarantee, swept across the band.

    Above a surcharge threshold, extra income must never cost more in tax than
    the income itself. Measured on tax + surcharge EXCLUDING cess, which is how
    the relief is statutorily computed — cess is levied on the relieved figure
    afterwards and can carry the all-in total a few percent past the extra
    income without the relief being wrong.

    A single fixture at one income can pass while the formula is wrong a few
    thousand higher, so this walks the band.
    """
    rate_set = rate_set_for_regime(regime, load_rate_sets())
    surcharge = rate_set.parameters.get("surcharge") or {}
    if not surcharge.get("marginal_relief_enabled"):
        pytest.skip(f"{regime} regime does not enable surcharge marginal relief")

    threshold = Decimal(str(surcharge["bands"][0]["min_income"])) - Decimal("0.01")
    baseline = _tax_and_surcharge(rate_set, threshold)

    for step in ("0.01", "1000", "25000", "100000", "250000", "500000"):
        extra_tax = _tax_and_surcharge(rate_set, threshold + Decimal(step)) - baseline
        assert extra_tax <= Decimal(step), (
            f"{regime} regime: earning {step} above the surcharge threshold costs "
            f"{extra_tax} in tax and surcharge. Marginal relief must cap the increase "
            f"at the extra income itself, or crossing the threshold leaves the "
            f"employee worse off for earning more."
        )


def _assess(rate_set: RateSet, taxable: Decimal):
    """Evaluate the regime at a given TAXABLE income, by grossing back up."""
    ctx = StatutoryContext(
        period_start=date(2025, 6, 1),
        financial_year="2025-2026",
        period_month=6,
        annual_gross_projection=taxable + Decimal(str(rate_set.parameters["standard_deduction"])),
        tax_regime=rate_set.regime,
    )
    return rules.income_tax.evaluate(ctx, rate_set.parameters, regime=rate_set.regime)


def _tax_and_surcharge(rate_set: RateSet, taxable: Decimal) -> Decimal:
    result = _assess(rate_set, taxable)
    return result.annual_tax - result.cess


def test_every_rate_set_names_an_implementation_this_release_provides() -> None:
    """
    A rate set pointing at a version that does not exist must fail loudly.

    This is how data restored from a newer release is caught — the alternative
    is computing it under whatever rule happens to be current, which produces a
    plausible number under rules the rate set was never written for.
    """
    for key, rate_set in load_rate_sets().items():
        implementation = rules.implementation(rate_set.rule_version, statute=rate_set.statute)
        assert callable(implementation), f"{key} names {rate_set.rule_version}"


def test_an_unknown_rule_version_is_refused() -> None:
    from apps.statutory.contracts import RuleVersionUnknown

    with pytest.raises(RuleVersionUnknown):
        rules.implementation("pf.v99")


def test_a_rule_version_belonging_to_another_statute_is_refused() -> None:
    """A PF rate set naming the ESI implementation must not quietly compute ESI."""
    from apps.statutory.contracts import RuleVersionUnknown

    with pytest.raises(RuleVersionUnknown):
        rules.implementation(rules.esi.RULE, statute=Statute.PF)
