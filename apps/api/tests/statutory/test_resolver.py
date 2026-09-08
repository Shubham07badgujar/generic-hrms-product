"""
Rule-set resolution and the Tier 2 assessment adapter.

These need a database, which is exactly why they are separate from the golden
master: the arithmetic is pure and testable without one, and only the loading
of rules is not. The split is the tier boundary showing up in the test layout.

This file also covers the fixture cases carrying `_raises`, which assert
resolver behaviour rather than arithmetic.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.statutory.contracts import RuleSetMissing, Statute, StatutoryContext
from apps.statutory.models import StatutoryRuleSet, VerificationStatus
from apps.statutory.resolver import resolve, unverified_blockers
from apps.statutory.services import assessment

pytestmark = pytest.mark.django_db

PERIOD_START = dt.date(2025, 6, 1)
FY = "2025-2026"


@pytest.fixture
def loaded(db):
    from django.core.management import call_command

    call_command("seed_statutory", verbosity=0)
    return StatutoryRuleSet.objects.all()


@pytest.fixture
def verified(loaded):
    from django.utils import timezone

    for rule_set in StatutoryRuleSet.objects.all():
        StatutoryRuleSet.objects.filter(pk=rule_set.pk).update(
            verification_status=VerificationStatus.VERIFIED,
            verified_checksum=rule_set.checksum,
            verified_at=timezone.now(),
        )
    return StatutoryRuleSet.objects.all()


def context(**overrides) -> StatutoryContext:
    fields = {
        "period_start": PERIOD_START,
        "financial_year": FY,
        "period_month": 6,
        "gross_wage": Decimal("18000.00"),
        "pf_wage": Decimal("12000.00"),
        "annual_gross_projection": Decimal("216000.00"),
        "state": "MH",
    }
    fields.update(overrides)
    return StatutoryContext(**fields)


# ------------------------------------------------------------- the seeder


def test_the_seeder_can_never_produce_a_verified_rate_set(loaded):
    """
    Verification is a human act recorded against a named person.

    A seeder that could produce verified rates would make the whole workflow
    decorative, and payroll would once again run on figures nobody checked.
    """
    assert loaded.exists()
    assert not loaded.filter(verification_status=VerificationStatus.VERIFIED).exists()
    assert set(loaded.values_list("verification_status", flat=True)) == {
        VerificationStatus.DRAFT
    }


def test_reseeding_refuses_to_overwrite_a_verified_rate_set(verified, capsys):
    from django.core.management import call_command

    before = {rs.pk: rs.checksum for rs in StatutoryRuleSet.objects.all()}
    call_command("seed_statutory", "--replace", verbosity=0)

    after = {rs.pk: rs.checksum for rs in StatutoryRuleSet.objects.all()}
    assert after == before


# ---------------------------------------------------------------- resolution


def test_an_unconfigured_state_raises_rather_than_returning_zero(loaded):
    """
    Fixture case `pt-mh-exemption-no-slabs-for-state`.

    "Delhi levies no Professional Tax" and "nobody has configured Delhi" are
    different statements, and only the first should produce a zero-PT payslip.
    Configuring an explicit nil rate set is how the first is expressed.
    """
    with pytest.raises(RuleSetMissing):
        resolve(
            Statute.PROFESSIONAL_TAX,
            on_date=PERIOD_START,
            jurisdiction="DL",
            require_verified=False,
        )


def test_a_draft_rate_set_is_refused_in_production_posture(loaded):
    with pytest.raises(RuleSetMissing):
        resolve(Statute.PF, on_date=PERIOD_START, require_verified=True)


def test_a_draft_rate_set_resolves_when_verification_is_not_required(loaded):
    rule_set = resolve(Statute.PF, on_date=PERIOD_START, require_verified=False)
    assert rule_set.statute == Statute.PF


def test_a_date_before_any_rate_set_takes_effect_raises(verified):
    with pytest.raises(RuleSetMissing):
        resolve(Statute.PF, on_date=dt.date(2020, 1, 1), require_verified=True)


def test_blockers_report_every_problem_not_just_the_first(loaded):
    problems = unverified_blockers(
        on_date=PERIOD_START, state="MH", regime="new", financial_year=FY
    )

    assert len(problems) >= 4
    assert all("must be verified by Finance" in p for p in problems)


# --------------------------------------------------------------- assessment


def test_an_assessment_records_which_rule_sets_produced_it(verified):
    result = assessment.assess(context(), require_verified=True)

    assert set(result.rule_sets_used) == {
        Statute.PF, Statute.ESI, Statute.PROFESSIONAL_TAX,
        Statute.GRATUITY, Statute.INCOME_TAX,
    }
    for ref in result.rule_sets_used.values():
        assert ref.checksum
        assert ref.rule_version


def test_is_fully_verified_is_false_while_any_rule_set_is_draft(loaded):
    """A partially verified assessment is not "mostly compliant" — it is unverified."""
    result = assessment.assess(context(), require_verified=False)

    assert result.is_fully_verified is False
    assert result.warnings


def test_is_fully_verified_is_true_once_every_rule_set_is_verified(verified):
    result = assessment.assess(context(), require_verified=True)
    assert result.is_fully_verified is True


def test_pf_and_esi_are_assessed_on_different_wages(verified):
    """
    The pair of bugs this design exists to prevent.

    PF on Basic+DA, ESI on gross. A context where the two differ is the only
    way to catch an implementation that conflated them.
    """
    result = assessment.assess(context(), require_verified=True)

    assert result.pf.base_wage == Decimal("12000.00")     # pf_wage
    assert result.esi.base_wage == Decimal("18000.00")    # gross_wage


def test_a_missing_jurisdiction_is_reported_rather_than_silently_zero(verified):
    """
    An employee in an unconfigured state produces a WARNING, not a quiet zero.

    The run-level gate then refuses to approve while any warning stands, so the
    problem surfaces before money moves rather than after.
    """
    result = assessment.assess(context(state="DL"), require_verified=True)

    assert result.professional_tax.amount == Decimal("0.00")
    assert any("pt" in warning for warning in result.warnings)


def test_no_state_means_no_pt_jurisdiction_which_is_not_an_error(verified):
    result = assessment.assess(context(state=""), require_verified=True)

    assert result.professional_tax.applied is False
    assert result.professional_tax.exemption_reason == "no_pt_jurisdiction"
    assert not any("pt" in warning for warning in result.warnings)


def test_an_employee_who_declared_no_regime_falls_to_the_statutory_default(verified):
    """
    Declaring investments is not an election.

    An implementation that inferred "they declared 80C, so they want the old
    regime" would be helpful and wrong — the election is a formal act, and
    guessing it produces a TDS figure that disagrees with their eventual return.
    """
    result = assessment.assess(
        context(tax_regime=None, declared_deductions={"80C": Decimal("150000.00")}),
        require_verified=True,
    )

    assert result.income_tax.regime_used == "new"
    assert dict(result.income_tax.deductions_allowed) == {}


def test_an_explicit_old_regime_election_allows_chapter_via_deductions(verified):
    result = assessment.assess(
        context(
            tax_regime="old",
            annual_gross_projection=Decimal("1000000.00"),
            declared_deductions={"80C": Decimal("500000.00")},
        ),
        require_verified=True,
    )

    assert result.income_tax.regime_used == "old"
    # Clamped to the cap, never rejected — employees over-declare routinely and
    # a hard failure would block the whole payroll run for one optimistic figure.
    assert result.income_tax.deductions_allowed["80C"] == Decimal("150000.00")


def test_the_unsettled_pf_admin_charge_scope_is_surfaced_as_a_warning(verified):
    """
    An open statutory question must be visible on every assessment that depends
    on it, not only in a fixture file nobody reads during a payroll run.
    """
    result = assessment.assess(context(), require_verified=True)

    assert any("pf-admin-charge-scope" in warning for warning in result.warnings)


# ------------------------------------------------- ESI contribution periods


def test_esi_contribution_period_is_read_from_the_rate_set(verified):
    from apps.statutory import rules

    rule_set = resolve(Statute.ESI, on_date=PERIOD_START, require_verified=True)

    assert rules.esi.contribution_period(6, rule_set.parameters) == (4, 9)
    assert rules.esi.contribution_period(11, rule_set.parameters) == (10, 3)
    # A period that wraps the calendar year must still contain January.
    assert rules.esi.contribution_period(1, rule_set.parameters) == (10, 3)
