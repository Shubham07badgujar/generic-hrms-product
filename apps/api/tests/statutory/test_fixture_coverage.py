"""
META-TEST: every statutory parameter must be exercised by a golden-master case.

The coverage rule, stated once:

    Every parameter in a rate set is named by at least one case's `covers`;
    every statute has all four categories represented; every case is
    well-formed and hand-derived.

This converts "we think we covered it" into a build failure. Adding a parameter
to a rate set without adding a case that exercises it breaks the build — the
same mechanism as the unmapped-view check in the access layer.

It also guards the property that makes these fixtures worth having at all:
every case must carry a `derivation`. A case without shown arithmetic cannot be
reviewed by a human, and an unreviewable expectation is indistinguishable from
one copied out of the implementation.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from .loader import CATEGORIES, load_cases, load_rate_sets

#: Parameters that are metadata rather than inputs to a calculation, so no case
#: needs to exercise them. Each entry is a deliberate exemption, not an oversight.
NON_CALCULATED_PARAMETERS: dict[str, set[str]] = {
    # Nothing yet. Entries here must be justified in review.
}

#: Parameters that turn a rule on or off, and so must have a case naming them
#: directly. `rebate.marginal_relief_enabled` is the clearest example: it is
#: true in the new regime and false in the old, and that divergence is the
#: whole reason it is a parameter rather than a constant. Letting it hide under
#: a `rebate` reference would allow the difference to go untested.
REQUIRE_EXPLICIT_COVERAGE: dict[str, set[str]] = {
    "income_tax/2025-2026-new": {
        "rebate.marginal_relief_enabled",
        "surcharge.marginal_relief_enabled",
    },
    "income_tax/2025-2026-old": {
        "rebate.marginal_relief_enabled",
        "surcharge.marginal_relief_enabled",
    },
}


@pytest.mark.meta
def test_every_rate_set_parameter_is_exercised_by_a_case():
    """A parameter no case touches is a parameter nothing verifies."""
    rate_sets = load_rate_sets()
    cases = load_cases()

    covered: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        covered[case.rule_set_key] |= set(case.covers)

    def is_covered(parameter: str, names: set[str]) -> bool:
        """
        A parameter is covered by its own name or by any ancestor.

        Naming `rebate` means "this case exercises the rebate rule", which
        reasonably covers `rebate.max_amount` and its siblings. Requiring every
        leaf to be listed produces long, unread `covers` lists — and a list
        nobody reads stops being a control.

        The trade-off is that a sub-parameter with genuinely distinct behaviour
        can hide under its parent. Those are called out explicitly instead:
        see REQUIRE_EXPLICIT_COVERAGE.
        """
        if parameter in names:
            return True
        parts = parameter.split(".")
        return any(".".join(parts[:i]) in names for i in range(1, len(parts)))

    failures: list[str] = []
    for key, rate_set in rate_sets.items():
        expected = rate_set.parameter_names - NON_CALCULATED_PARAMETERS.get(key, set())
        missing = {p for p in expected if not is_covered(p, covered[key])}
        if missing:
            failures.append(f"  {key}: no case exercises {sorted(missing)}")

    # Parameters that switch behaviour on or off must be named directly — a
    # parent reference is not enough, because the whole point is that they are
    # set differently in different rate sets.
    for key, required in REQUIRE_EXPLICIT_COVERAGE.items():
        if key not in rate_sets:
            continue
        present = rate_sets[key].parameter_names
        for parameter in required:
            if parameter in present and parameter not in covered[key]:
                failures.append(
                    f"  {key}: '{parameter}' switches behaviour and must be named "
                    f"explicitly in a case's `covers`, not inherited from a parent"
                )

    if failures:
        pytest.fail(
            "Statutory parameters with no golden-master coverage:\n"
            + "\n".join(failures)
            + "\n\nAdd a case naming each parameter in its `covers` list, or justify "
            "an exemption in NON_CALCULATED_PARAMETERS."
        )


@pytest.mark.meta
def test_every_case_references_a_rate_set_that_exists():
    rate_sets = load_rate_sets()
    unknown = sorted(
        {c.rule_set_key for c in load_cases() if c.rule_set_key not in rate_sets}
    )
    assert not unknown, f"Cases reference rate sets that do not exist: {unknown}"


@pytest.mark.meta
def test_every_case_covers_parameters_that_actually_exist():
    """
    Catches a `covers` entry that names a parameter the rate set does not have —
    typically a typo, which would otherwise silently create a coverage hole
    while appearing to fill one.
    """
    rate_sets = load_rate_sets()
    failures = []
    for case in load_cases():
        rate_set = rate_sets.get(case.rule_set_key)
        if rate_set is None:
            continue
        bogus = set(case.covers) - rate_set.parameter_names
        if bogus:
            failures.append(f"  {case.id}: covers unknown parameter(s) {sorted(bogus)}")
    assert not failures, "Cases naming non-existent parameters:\n" + "\n".join(failures)


@pytest.mark.meta
def test_case_ids_are_unique():
    ids = [c.id for c in load_cases()]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"Duplicate case ids: {duplicates}"


@pytest.mark.meta
def test_every_case_shows_its_derivation():
    """
    The rule this whole directory exists to enforce.

    An expectation without shown arithmetic cannot be checked by a reviewer,
    and an unreviewable expectation is indistinguishable from one generated by
    the implementation it is supposed to be testing.
    """
    thin = [
        f"  {c.id} ({c.path.name})"
        for c in load_cases()
        if len(c.derivation.strip()) < 40
    ]
    assert not thin, (
        "Cases with no meaningful derivation:\n"
        + "\n".join(thin)
        + "\n\nShow the arithmetic. A reviewer must be able to check it with a "
        "calculator and no code."
    )


@pytest.mark.meta
def test_every_case_cites_a_source_and_states_assumptions():
    missing = [
        f"  {c.id}: {'no source' if not c.source else 'no assumptions'}"
        for c in load_cases()
        if not c.source or not c.assumptions
    ]
    assert not missing, "Cases missing provenance:\n" + "\n".join(missing)


@pytest.mark.meta
def test_categories_are_valid():
    bad = sorted({c.category for c in load_cases()} - CATEGORIES)
    assert not bad, f"Unknown categories {bad}; allowed: {sorted(CATEGORIES)}"


@pytest.mark.meta
def test_every_statute_has_all_four_categories():
    """
    Normal cases alone prove very little. The interesting failures live at
    boundaries and in exemptions, so every statute must cover all four.
    """
    by_statute: dict[str, set[str]] = defaultdict(set)
    for case in load_cases():
        by_statute[case.statute].add(case.category)

    gaps = {
        statute: sorted(CATEGORIES - categories)
        for statute, categories in by_statute.items()
        if CATEGORIES - categories
    }
    assert not gaps, "Statutes missing fixture categories: " + "; ".join(
        f"{statute} lacks {missing}" for statute, missing in sorted(gaps.items())
    )


#: Fields that are genuinely integers — counts, month numbers, day counts —
#: rather than money. These may appear unquoted; forcing them through Decimal
#: would be noise, not safety.
INTEGER_FIELDS = frozenset(
    {
        "period_month",
        "completed_service_days",
        "period_year",
        "round_number",
    }
)


@pytest.mark.meta
def test_money_values_are_quoted_strings_not_floats():
    """
    A bare `1800.00` in YAML becomes a float, and float arithmetic is not exact.
    Money comparisons would then fail for reasons unrelated to the statute.

    Applies to money only — see INTEGER_FIELDS for the genuine integers.
    """
    from .loader import decimalise

    failures = []
    for case in load_cases():
        for section in ("given", "expect"):
            for field, value in getattr(case, section).items():
                if field.startswith("_") or field in INTEGER_FIELDS:
                    continue
                if isinstance(value, bool) or value is None:
                    continue
                try:
                    decimalise(value)
                except TypeError as exc:
                    failures.append(f"  {case.id}.{section}.{field}: {exc}")
    assert not failures, "Unquoted numerics in fixtures:\n" + "\n".join(failures)


def test_report_verification_status(capsys):
    """
    Not an assertion — a standing report.

    Against an UNVERIFIED rate set a passing case proves ARITHMETIC only. It
    becomes a COMPLIANCE assertion once Finance verifies the underlying numbers.
    Printing this every run keeps the distinction visible instead of letting a
    green suite imply lawfulness it has not established.
    """
    rate_sets = load_rate_sets()
    cases = load_cases()

    with capsys.disabled():
        print("\n  Statutory fixture status")
        print("  " + "-" * 62)
        for key in sorted(rate_sets):
            rate_set = rate_sets[key]
            count = sum(1 for c in cases if c.rule_set_key == key)
            if rate_set.is_verified:
                print(f"  [COMPLIANCE] {key:<28} {count:>3} cases  verified")
            else:
                print(
                    f"  [ARITHMETIC] {key:<28} {count:>3} cases  "
                    f"{rate_set.verification_status} — proves formula application only"
                )
        print("  " + "-" * 62)
