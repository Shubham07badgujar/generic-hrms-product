"""
Open statutory interpretation questions.

`test_no_unresolved_statutory_disputes` FAILS WHILE ANY DISPUTE IS OPEN. That is
deliberate, not a bug to be worked around.

The failure mode this prevents is specific and, in the previous system, real:
its statutory values carried a loud "HR MUST verify against the gazette" comment
that sat unread for a year while payroll ran on unverified rates. A comment is
easy to not read. A red test is not.

    pytest -m "not dispute"     # day-to-day work; everything else must be green
    pytest                      # release gate; disputes must be resolved

Resolving a dispute means answering the question and updating the rate set to
match — never editing a fixture until the tests go green.
"""

from __future__ import annotations

import pytest

from .loader import load_disputes, load_rate_sets


@pytest.mark.dispute
def test_no_unresolved_statutory_disputes():
    """Standing red while any statutory question is unanswered."""
    open_disputes = [d for d in load_disputes() if d.is_open]
    if not open_disputes:
        return

    lines = [
        "",
        f"{len(open_disputes)} statutory interpretation question(s) are unresolved.",
        "Payroll cannot be verified until Finance answers each one.",
        "",
    ]
    for dispute in open_disputes:
        lines += [
            f"  [{dispute.id}]  ({dispute.statute})",
            f"    Q: {' '.join(dispute.question.split())}",
            f"    Impact: {' '.join(dispute.impact.split())}",
            f"    Resolve: {' '.join(dispute.how_to_resolve.split())}",
            f"    Blocks: {', '.join(dispute.blocking) or '-'}",
            "",
        ]
    lines += [
        "This test is EXPECTED to fail until these are answered. Do not edit a",
        "fixture to make it pass — record the decision in disputes.yaml and update",
        "the rate set to match.",
    ]
    pytest.fail("\n".join(lines))


def test_a_disputed_rate_set_can_never_be_verified():
    """
    A rate set with an open dispute must not be marked VERIFIED.

    Guards the ordering: the question is answered first, THEN the numbers are
    signed off. Verifying while a question is open would mean certifying values
    whose meaning is still undecided.
    """
    rate_sets = load_rate_sets()
    violations = []
    for dispute in load_disputes():
        if not dispute.is_open:
            continue
        for key in dispute.blocking:
            rate_set = rate_sets.get(key)
            if rate_set is not None and rate_set.is_verified:
                violations.append(f"  {key} is VERIFIED but dispute '{dispute.id}' is open")

    assert not violations, (
        "Rate sets verified while a statutory question is still open:\n"
        + "\n".join(violations)
    )


def test_every_dispute_states_impact_and_how_to_resolve():
    """
    A dispute nobody can act on is just an excuse.

    Each must say what breaks if it is wrong, and what a reviewer should read to
    settle it.
    """
    thin = [
        f"  {d.id}: {'no impact stated' if len(d.impact) < 30 else 'no resolution path'}"
        for d in load_disputes()
        if len(d.impact) < 30 or len(d.how_to_resolve) < 30
    ]
    assert not thin, "Disputes lacking actionable detail:\n" + "\n".join(thin)


def test_every_dispute_blocks_a_rate_set_that_exists():
    rate_sets = load_rate_sets()
    unknown = sorted(
        {
            f"{d.id} -> {key}"
            for d in load_disputes()
            for key in d.blocking
            if key not in rate_sets
        }
    )
    assert not unknown, f"Disputes blocking non-existent rate sets: {unknown}"


def test_resolved_disputes_record_who_decided():
    """A resolution without an owner and evidence is not a resolution."""
    incomplete = []
    for dispute in load_disputes():
        if dispute.is_open:
            continue
        resolution = dispute.resolution or {}
        missing = [
            field
            for field in ("decision", "decided_by", "decided_on", "evidence")
            if not resolution.get(field)
        ]
        if missing:
            incomplete.append(f"  {dispute.id}: missing {missing}")
    assert not incomplete, (
        "Disputes marked resolved without a complete record:\n" + "\n".join(incomplete)
    )


def test_disputed_cases_declare_which_dispute_blocks_them():
    """
    A case with a DISPUTED expectation must name the question it is waiting on,
    and that question must actually be open. Otherwise `DISPUTED` degrades into
    "we could not be bothered to work this out".
    """
    from .loader import load_cases, load_disputes

    known = {d.id: d for d in load_disputes()}
    problems = []

    for case in load_cases():
        has_disputed_value = bool(case.disputed_fields)
        if has_disputed_value and not case.disputes:
            problems.append(
                f"  {case.id}: expects DISPUTED for {list(case.disputed_fields)} "
                f"but names no dispute"
            )
        for dispute_id in case.disputes:
            dispute = known.get(dispute_id)
            if dispute is None:
                problems.append(f"  {case.id}: references unknown dispute '{dispute_id}'")
            elif not dispute.is_open and has_disputed_value:
                problems.append(
                    f"  {case.id}: dispute '{dispute_id}' is resolved — replace the "
                    f"DISPUTED placeholder with the decided value"
                )

    assert not problems, "Disputed-case bookkeeping problems:\n" + "\n".join(problems)
