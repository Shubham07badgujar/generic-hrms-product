"""
The ONLY types that cross the Tier 1 / Tier 2 boundary.

Tier 1 (statutory evaluation) receives primitive and domain attributes — never a
Django model. That is not stylistic: the same evaluation must serve a payroll
run, a salary-offer projection, an arrears recomputation for a backdated
revision, and a cost simulation. Only the first has a PayrollRun and an
Employee. Coupling Tier 1 to those would force the other three to fabricate one.

Enforced by `tests/statutory/test_tier_isolation.py`, which walks the import
graph of `apps.statutory` and fails if it reaches the application model layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping

ZERO = Decimal("0.00")


class Statute:
    """Statute keys. Plain strings so Tier 1 needs no Django import."""

    PF = "pf"
    ESI = "esi"
    PROFESSIONAL_TAX = "pt"
    GRATUITY = "gratuity"
    INCOME_TAX = "income_tax"

    ALL = (PF, ESI, PROFESSIONAL_TAX, GRATUITY, INCOME_TAX)


class TaxRegime:
    OLD = "old"
    NEW = "new"


class Gender:
    MALE = "M"
    FEMALE = "F"
    OTHER = "O"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatutoryContext:
    """
    Everything the statutes are entitled to know, and nothing else.

    Frozen: an evaluation must never mutate its own input, or replaying a
    historical run could produce a different answer than the original.
    """

    # --- period ---
    period_start: date
    """First day of the pay period. Drives rule-set resolution — NOT `today`,
    so recomputing a 2024 run in 2027 still resolves the 2024 rules."""

    financial_year: str
    """e.g. "2025-2026". Income-tax rule sets are keyed by FY, not by date,
    because a mid-year Finance Act applies to the whole year."""

    period_month: int
    """1-12. Needed for the Maharashtra February special amount and for
    ESI contribution-period arithmetic."""

    # --- wages ---
    gross_wage: Decimal = ZERO
    """Full monthly gross. ESI and Professional Tax assess on this."""

    pf_wage: Decimal = ZERO
    """Basic + DA (the Code on Wages definition). Distinct from gross: PF is
    NOT assessed on the full gross, and conflating them is a classic and
    expensive payroll bug."""

    annual_gross_projection: Decimal = ZERO
    """Projected annual earnings, for TDS."""

    last_drawn_monthly_wage: Decimal = ZERO
    """Basic + DA at exit, for gratuity."""

    # --- jurisdiction & person ---
    state: str = ""
    """State code for Professional Tax. Empty = no PT jurisdiction."""

    gender: str = Gender.OTHER
    """Required for the Maharashtra women's PT exemption."""

    date_of_joining: date | None = None
    date_of_exit: date | None = None

    is_disabled: bool = False
    """ESI applies a higher wage threshold for persons with disability."""

    is_international_worker: bool = False
    """International workers have no PF wage ceiling."""

    pf_opted_out: bool = False
    """Members earning above the ceiling at first joining may opt out."""

    esi_already_liable_this_period: bool = False
    """ESI contribution periods run Apr-Sep and Oct-Mar. Once liable at the
    start of a period, liability continues to the end of that period even if
    wages later exceed the threshold. Tier 2 supplies this because it requires
    history Tier 1 must not read."""

    # --- tax ---
    tax_regime: str | None = None
    """None means the employee has not declared; the statutory default applies."""

    declared_deductions: Mapping[str, Decimal] = field(default_factory=dict)
    """e.g. {"80C": 150000, "80D": 25000, "HRA_exempt": 120000}. Caps are
    applied by the rule, never trusted from this input."""

    # --- service ---
    completed_service_years: Decimal = ZERO
    completed_service_days: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.period_month <= 12:
            raise ValueError(f"period_month must be 1-12, got {self.period_month}")
        for name in (
            "gross_wage",
            "pf_wage",
            "annual_gross_projection",
            "last_drawn_monthly_wage",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSetRef:
    """
    Provenance for one statute's contribution to an assessment.

    Copied verbatim onto the payroll run so the exact rules are recoverable
    years later, even if the rule set is superseded or deleted.
    """

    rule_set_id: str
    statute: str
    rule_version: str
    checksum: str
    effective_from: str
    jurisdiction: str = ""
    regime: str = ""
    verification_status: str = "unverified"
    verified_by: str = ""
    verified_at: str = ""


@dataclass(frozen=True)
class PFResult:
    base_wage: Decimal = ZERO
    employee: Decimal = ZERO
    employer_eps: Decimal = ZERO
    employer_epf: Decimal = ZERO
    admin_charge: Decimal = ZERO
    applied: bool = False
    exemption_reason: str = ""

    @property
    def employer_total(self) -> Decimal:
        return self.employer_eps + self.employer_epf


@dataclass(frozen=True)
class ESIResult:
    base_wage: Decimal = ZERO
    employee: Decimal = ZERO
    employer: Decimal = ZERO
    applied: bool = False
    exemption_reason: str = ""
    continued_from_period_start: bool = False


@dataclass(frozen=True)
class PTResult:
    amount: Decimal = ZERO
    applied: bool = False
    exemption_reason: str = ""
    exemption_id: str = ""
    is_special_month: bool = False


@dataclass(frozen=True)
class GratuityResult:
    monthly_provision: Decimal = ZERO
    accrued_total: Decimal = ZERO
    is_eligible: bool = False
    capped: bool = False
    exemption_reason: str = ""


@dataclass(frozen=True)
class TDSResult:
    regime_used: str = ""
    taxable_income: Decimal = ZERO
    tax_before_rebate: Decimal = ZERO
    rebate_applied: Decimal = ZERO
    marginal_relief: Decimal = ZERO
    surcharge: Decimal = ZERO
    cess: Decimal = ZERO
    annual_tax: Decimal = ZERO
    monthly_tds: Decimal = ZERO
    deductions_allowed: Mapping[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class StatutoryAssessment:
    """The complete statutory position for one employee, one period."""

    pf: PFResult
    esi: ESIResult
    professional_tax: PTResult
    gratuity: GratuityResult
    income_tax: TDSResult

    rule_sets_used: Mapping[str, RuleSetRef] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def employee_deductions(self) -> Decimal:
        return (
            self.pf.employee
            + self.esi.employee
            + self.professional_tax.amount
            + self.income_tax.monthly_tds
        )

    @property
    def employer_contributions(self) -> Decimal:
        return (
            self.pf.employer_total
            + self.pf.admin_charge
            + self.esi.employer
            + self.gratuity.monthly_provision
        )

    @property
    def is_fully_verified(self) -> bool:
        """
        True only when EVERY rule set used was verified by Finance.

        Tier 2 gates live payroll on this. A partially verified assessment is
        not "mostly compliant" — it is unverified.
        """
        return bool(self.rule_sets_used) and all(
            ref.verification_status == "verified" for ref in self.rule_sets_used.values()
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StatutoryError(Exception):
    """Base for every Tier 1 failure."""


class RuleSetMissing(StatutoryError):
    """
    No rule set in force for a statute on a date.

    Deliberately fatal rather than defaulting to zero. A missing PF rule set
    means "we do not know what to deduct", which must stop a payroll run — not
    quietly produce a payslip with no PF on it.
    """

    def __init__(self, statute: str, on_date: date, jurisdiction: str = "", regime: str = ""):
        self.statute, self.on_date = statute, on_date
        self.jurisdiction, self.regime = jurisdiction, regime
        where = f" for {jurisdiction}" if jurisdiction else ""
        which = f" ({regime} regime)" if regime else ""
        super().__init__(
            f"No statutory rule set in force for '{statute}'{where}{which} on "
            f"{on_date.isoformat()}. Configure and verify one before running payroll."
        )


class RuleSetAmbiguous(StatutoryError):
    """More than one rule set in force. The DB exclusion constraint should make
    this unreachable; if it is raised, that constraint has been dropped."""


class RuleVersionUnknown(StatutoryError):
    """A rule set names an implementation version that does not exist — e.g.
    data restored from a newer release."""


class ParametersInvalid(StatutoryError):
    """A rule set is missing parameters its implementation requires."""
