"""
Payroll: salary structures, runs, payslips and the money that moves.

Two invariants shape almost everything here.

FIRST — an approved payroll run is EVIDENCE, not a working document. Once
`locked` is set, the only fields that may change are the ones describing what
happened to the run afterwards (status, payment date, notes). Amounts, periods
and payslips are frozen. This is enforced in `save()` with an explicit
allowlist rather than by convention, because "we agreed not to edit approved
runs" is not a control an auditor can test.

SECOND — a payslip's LINES are the record; its totals are a summary of them.
Every amount that appears in a total also appears as a line, so a payslip can
always be explained to the employee who received it, and a statutory return can
always be traced back to the components that produced it.

Statutory rates are deliberately NOT modelled here. They live in
`apps.statutory` as verified, effective-dated rule sets with their own
provenance and four-eyes verification. Payroll consumes an assessment; it never
holds a rate.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from core.models import OrgOwnedModel

MONEY = {"max_digits": 14, "decimal_places": 2}
RATE = {"max_digits": 9, "decimal_places": 4}
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Salary structure
# ---------------------------------------------------------------------------


class ComponentType(models.TextChoices):
    EARNING = "earning", "Earning"
    DEDUCTION = "deduction", "Deduction"
    STATUTORY_DEDUCTION = "statutory_deduction", "Statutory deduction"
    EMPLOYER_CONTRIBUTION = "employer_contribution", "Employer contribution"
    REIMBURSEMENT = "reimbursement", "Reimbursement"


class CalcType(models.TextChoices):
    FIXED = "fixed", "Fixed amount"
    PERCENT_OF = "percent_of", "Percentage of another component"


class Rounding(models.TextChoices):
    NONE = "none", "No rounding"
    NEAREST = "nearest", "Nearest rupee"
    UP = "up", "Round up"
    DOWN = "down", "Round down"


class SalaryComponent(OrgOwnedModel):
    """
    A nameable part of pay — BASIC, HRA, a transport allowance.

    `is_wage` marks the components that make up "wages" under the Code on
    Wages, which is what PF is assessed on. It is a separate flag from
    `is_part_of_ctc` and from `is_taxable` because the three genuinely differ:
    an allowance can be taxable, inside CTC, and still not be a wage.
    """

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    component_type = models.CharField(max_length=30, choices=ComponentType.choices)
    calc_type = models.CharField(
        max_length=20, choices=CalcType.choices, default=CalcType.FIXED
    )
    #: For PERCENT_OF: the code of the component this is a percentage of.
    percent_of_code = models.SlugField(max_length=30, blank=True)

    is_taxable = models.BooleanField(default=True)
    is_part_of_ctc = models.BooleanField(default=True)
    #: Counts toward Basic + DA for PF. The single most consequential flag here.
    is_wage = models.BooleanField(default=False)

    rounding = models.CharField(
        max_length=10, choices=Rounding.choices, default=Rounding.NONE
    )
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "code"]
        indexes = [models.Index(fields=["component_type"])]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def clean(self) -> None:
        if self.calc_type == CalcType.PERCENT_OF and not self.percent_of_code:
            raise ValidationError(
                {"percent_of_code": "A percentage component must name the component "
                                    "it is a percentage of."}
            )
        if self.percent_of_code and self.percent_of_code == self.code:
            raise ValidationError({"percent_of_code": "A component cannot reference itself."})


class SalaryStructure(OrgOwnedModel):
    """
    What an employee is paid, effective-dated.

    Structures are never edited in place once payroll has run against them —
    a revision closes the current row and opens a new one, so a payslip issued
    last March can still be explained by the structure that was in force then.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="salary_structures"
    )
    ctc_annual = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])

    #: Which statutes this employee is under, decided by HR Head / Finance
    #: Head on the structure itself — enrolment is a fact about the person's
    #: employment terms, not something the engine may presume. A flag switched
    #: off makes the run record that statute as "not enrolled" and put NOTHING
    #: on the payslip for it, employer side included. Effective-dated for free,
    #: because the flags live on the structure and structures are versioned.
    pf_applicable = models.BooleanField(default=True)
    esi_applicable = models.BooleanField(default=True)
    pt_applicable = models.BooleanField(default=True)
    tds_applicable = models.BooleanField(default=True)
    gratuity_applicable = models.BooleanField(default=True)

    valid_from = models.DateField()
    #: Null means "in force". Closed when a revision supersedes it.
    valid_to = models.DateField(null=True, blank=True)

    revision_reason = models.CharField(max_length=255, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-valid_from"]
        indexes = [
            models.Index(fields=["employee", "-valid_from"]),
            models.Index(fields=["valid_from", "valid_to"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="ck_salary_structure_dates_ordered",
            ),
            #: One open structure per employee. Two would make "current salary"
            #: ambiguous, and payroll would silently pick whichever sorted first.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(valid_to__isnull=True, is_active=True),
                name="uniq_open_salary_structure_per_employee",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} from {self.valid_from}"

    @property
    def monthly_gross(self) -> Decimal:
        return sum((line.monthly_amount for line in self.lines.all()
                    if line.component.component_type == ComponentType.EARNING), ZERO)

    @property
    def monthly_wage(self) -> Decimal:
        """Basic + DA — the PF assessment base, not the gross."""
        return sum((line.monthly_amount for line in self.lines.all()
                    if line.component.is_wage), ZERO)

    def wage_share(self) -> Decimal:
        """
        Wages as a fraction of gross.

        The Code on Wages requires wages to be at least half of total
        remuneration; a structure that pushes Basic down to shrink PF liability
        fails that test. Surfaced rather than enforced here because the
        threshold is a policy question Finance owns.
        """
        gross = self.monthly_gross
        return (self.monthly_wage / gross) if gross else ZERO


class SalaryStructureLine(OrgOwnedModel):

    #: Inherits its organization from `salary_structure` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "salary_structure"
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name="+")
    salary_structure = models.ForeignKey(
        SalaryStructure, on_delete=models.CASCADE, related_name="lines"
    )
    #: The configured input: an amount for FIXED, a percentage for PERCENT_OF.
    value = models.DecimalField(**MONEY, default=ZERO)
    #: The resolved monthly figure. Stored because it is what payroll reads,
    #: and recomputing it later against edited components would change history.
    monthly_amount = models.DecimalField(**MONEY, default=ZERO)

    class Meta:
        ordering = ["component__display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["salary_structure", "component"],
                name="uniq_structure_line_per_component",
            )
        ]

    def __str__(self) -> str:
        return f"{self.component.code} = {self.monthly_amount}"


# ---------------------------------------------------------------------------
# Investment declarations
# ---------------------------------------------------------------------------


class DeclarationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class TaxRegimeChoice(models.TextChoices):
    OLD = "old", "Old regime"
    NEW = "new", "New regime"


class InvestmentDeclaration(OrgOwnedModel):
    """
    The employee's tax election and Chapter VI-A declarations for a year.

    `regime` is deliberately nullable: not declaring is a real state with a real
    consequence (the statutory default applies), and inferring an election from
    the presence of 80C figures would be helpful and wrong.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="tax_declarations"
    )
    financial_year = models.CharField(max_length=9, help_text="e.g. 2025-2026")
    regime = models.CharField(
        max_length=10, choices=TaxRegimeChoice.choices, blank=True,
        help_text="Blank means no election — the statutory default applies.",
    )
    #: {"80C": "150000.00", "80D": "25000.00", ...} as declared by the employee.
    #: Retained AS DECLARED; the computation applies the caps separately, so the
    #: employee's own record is never quietly rewritten to the allowed figure.
    declarations = models.JSONField(default=dict, blank=True)
    proofs = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20, choices=DeclarationStatus.choices, default=DeclarationStatus.DRAFT
    )
    verified_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-financial_year"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "financial_year"], name="uniq_declaration_per_employee_year"
            )
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.financial_year}"

    def as_decimals(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for section, amount in (self.declarations or {}).items():
            try:
                out[section] = Decimal(str(amount))
            except (TypeError, ArithmeticError, ValueError):
                continue
        return out


# ---------------------------------------------------------------------------
# Payroll run
# ---------------------------------------------------------------------------


class RunType(models.TextChoices):
    REGULAR = "regular", "Regular"
    OFF_CYCLE = "off_cycle", "Off-cycle"
    SUPPLEMENTARY = "supplementary", "Supplementary"


class PayrollRunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PROCESSING = "processing", "Processing"
    REVIEW = "review", "In review"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REVERSED = "reversed", "Reversed"


#: Fields that may still change after a run is locked. Everything else is
#: frozen. Kept narrow and explicit: this list IS the immutability guarantee,
#: so widening it is a deliberate, reviewable act rather than a slip.
MUTABLE_WHEN_LOCKED = frozenset({
    "status", "locked", "notes", "paid_at", "reversed_at", "reversal_reason",
    "approved_by", "approved_at", "updated_at", "updated_by", "is_active",
})


class PayrollRunLocked(Exception):
    """Raised when something tries to change a locked run's substance."""


class PayrollRun(OrgOwnedModel):
    """
    One payroll cycle for a period, optionally scoped to a location.

    The location split exists because Professional Tax is a state levy: a run
    covering two states needs two sets of PT rules, and keeping runs per
    location keeps each one under a single jurisdiction.
    """

    period_month = models.PositiveSmallIntegerField()
    period_year = models.PositiveSmallIntegerField()
    location = models.ForeignKey(
        "organization.Location", on_delete=models.PROTECT, null=True, blank=True,
        related_name="payroll_runs", help_text="Null covers the whole organisation.",
    )
    run_type = models.CharField(
        max_length=20, choices=RunType.choices, default=RunType.REGULAR
    )
    #: Distinguishes repeated off-cycle runs in the same period. The previous
    #: system's unique key had no sequence, which structurally prevented paying
    #: a second bonus in a month.
    sequence = models.PositiveSmallIntegerField(default=1)

    status = models.CharField(
        max_length=20, choices=PayrollRunStatus.choices, default=PayrollRunStatus.DRAFT
    )
    locked = models.BooleanField(default=False)

    run_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_payroll_runs",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(blank=True)

    totals = models.JSONField(default=dict, blank=True)
    #: Which statutory rule sets produced these figures, frozen at process time.
    #: Copied rather than referenced so the run stays explicable even if a rule
    #: set is later superseded, corrected or deleted.
    rule_sets_used = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_year", "-period_month", "-sequence"]
        indexes = [
            models.Index(fields=["-period_year", "-period_month"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1, period_month__lte=12),
                name="ck_payroll_run_month_range",
            ),
            models.UniqueConstraint(
                fields=["period_year", "period_month", "location", "run_type", "sequence"],
                name="uniq_payroll_run_per_period",
            ),
        ]

    def __str__(self) -> str:
        where = self.location.code if self.location else "all"
        return f"{self.period_year}-{self.period_month:02d} {where} ({self.run_type})"

    @property
    def period_start(self) -> dt.date:
        return dt.date(self.period_year, self.period_month, 1)

    @property
    def financial_year(self) -> str:
        """Indian FY label: April starts the year, so January-March belong to the previous one."""
        start = self.period_year if self.period_month >= 4 else self.period_year - 1
        return f"{start}-{start + 1}"

    @property
    def is_editable(self) -> bool:
        return not self.locked and self.status in {
            PayrollRunStatus.DRAFT, PayrollRunStatus.REVIEW, PayrollRunStatus.PROCESSING
        }

    def save(self, *args, **kwargs):
        """
        Refuse to change a locked run's substance.

        Checked against the DATABASE's current state rather than an in-memory
        flag, so an instance loaded before approval cannot be saved over one
        that has since been approved.
        """
        if self.pk:
            stored = (
                PayrollRun.objects.filter(pk=self.pk)
                .values("locked", *[f for f in _SUBSTANTIVE_FIELDS])
                .first()
            )
            if stored and stored["locked"]:
                update_fields = set(kwargs.get("update_fields") or [])
                if update_fields:
                    forbidden = update_fields - MUTABLE_WHEN_LOCKED
                else:
                    forbidden = {
                        field for field in _SUBSTANTIVE_FIELDS
                        if getattr(self, field) != stored[field]
                    }
                if forbidden:
                    raise PayrollRunLocked(
                        f"Payroll run {self} is approved and locked. "
                        f"Cannot change {', '.join(sorted(forbidden))}. "
                        f"Reverse the run if a correction is genuinely required — "
                        f"that is recorded, whereas an edit would not be."
                    )
        return super().save(*args, **kwargs)


#: Fields whose change after locking would alter what was approved.
_SUBSTANTIVE_FIELDS = (
    "period_month", "period_year", "location_id", "run_type", "sequence",
    "totals", "rule_sets_used", "run_by_id",
)


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------


class Payslip(OrgOwnedModel):
    """
    One employee's pay for one run.

    Totals are stored rather than derived on read: they are what was actually
    paid, and recomputing them later from live components would silently
    restate history the moment a component's configuration changed.
    """

    #: Inherits its organization from `payroll_run` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "payroll_run"

    payroll_run = models.ForeignKey(
        PayrollRun, on_delete=models.CASCADE, related_name="payslips"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="payslips"
    )
    salary_structure = models.ForeignKey(
        SalaryStructure, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    paid_days = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    lop_days = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)

    gross_earnings = models.DecimalField(**MONEY, default=ZERO)
    total_deductions = models.DecimalField(**MONEY, default=ZERO)
    employer_contributions = models.DecimalField(**MONEY, default=ZERO)
    net_pay = models.DecimalField(**MONEY, default=ZERO)

    #: Snapshotted so a payslip stays explicable after an employee transfers.
    location = models.ForeignKey(
        "organization.Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    state = models.CharField(max_length=8, blank=True)

    #: Anything the assessment could not settle — an unverified rule set, a
    #: missing declaration. Kept on the payslip so review sees it per employee.
    warnings = models.JSONField(default=list, blank=True)

    #: The attendance and leave picture behind paid_days, frozen at process
    #: time: days in month, days attended, absent/half days, paid leave taken,
    #: leave balance. Stored — not recomputed at render — so the printed slip
    #: still explains itself after attendance corrections change the source.
    attendance_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["employee__employee_code"]
        indexes = [models.Index(fields=["employee", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "employee"], name="uniq_payslip_per_run_employee"
            )
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} {self.payroll_run}"

    def save(self, *args, **kwargs):
        if self.payroll_run_id:
            locked = (
                PayrollRun.objects.filter(pk=self.payroll_run_id)
                .values_list("locked", flat=True)
                .first()
            )
            if locked:
                raise PayrollRunLocked(
                    f"Payroll run {self.payroll_run_id} is locked; its payslips are final."
                )
        return super().save(*args, **kwargs)


class PayslipLine(OrgOwnedModel):
    """
    One line on a payslip.

    `label` is stored rather than read from the component, so a payslip reads
    the same in five years even if the component has since been renamed.
    """

    #: Inherits its organization from `payslip` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "payslip"

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(
        SalaryComponent, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    label = models.CharField(max_length=120)
    component_type = models.CharField(max_length=30, choices=ComponentType.choices)
    amount = models.DecimalField(**MONEY, default=ZERO)
    #: Employer-side lines are a cost to the company, not a deduction from pay.
    #: Mixing the two is how "net pay" quietly becomes wrong.
    is_employer_side = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "label"]

    def __str__(self) -> str:
        return f"{self.label}: {self.amount}"


class StatutoryKind(models.TextChoices):
    PF = "pf", "Provident Fund"
    ESI = "esi", "ESI"
    PT = "pt", "Professional Tax"
    TDS = "tds", "TDS"
    GRATUITY = "gratuity", "Gratuity provision"


class StatutoryContribution(OrgOwnedModel):
    """
    Per-payslip statutory position, in the shape the returns and challans want.

    Duplicates figures that also appear as payslip lines, deliberately: a
    filing is prepared per statute across all employees, and deriving it by
    parsing line labels would break the first time a label changed.
    """

    #: Inherits its organization from `payslip` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "payslip"

    payslip = models.ForeignKey(
        Payslip, on_delete=models.CASCADE, related_name="statutory_contributions"
    )
    kind = models.CharField(max_length=20, choices=StatutoryKind.choices)
    employee_amount = models.DecimalField(**MONEY, default=ZERO)
    employer_amount = models.DecimalField(**MONEY, default=ZERO)
    #: The wage this statute was actually assessed on — PF wage for PF, gross
    #: for ESI. Stored because the two differ and a return must show its base.
    base_wage = models.DecimalField(**MONEY, default=ZERO)
    state = models.CharField(max_length=8, blank=True)
    applied = models.BooleanField(default=True)
    exemption_reason = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["payslip", "kind"], name="uniq_statutory_contribution_per_kind"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.payslip.employee.employee_code}"


# ---------------------------------------------------------------------------
# Adjustments, loans, reimbursements
# ---------------------------------------------------------------------------


class AdjustmentKind(models.TextChoices):
    BONUS = "bonus", "Bonus"
    INCENTIVE = "incentive", "Incentive"
    ARREAR = "arrear", "Arrears"
    ADVANCE_RECOVERY = "advance_recovery", "Advance recovery"
    LOAN_RECOVERY = "loan_recovery", "Loan EMI recovery"
    REIMBURSEMENT = "reimbursement", "Reimbursement"
    OTHER_EARNING = "other_earning", "Other earning"
    OTHER_DEDUCTION = "other_deduction", "Other deduction"
    #: A deferred package amount entering payroll after its release was
    #: approved. Its own kind so the payslip line says what it is — never
    #: merged invisibly into Basic/HRA.
    DEFERRED_RELEASE = "deferred_release", "Deferred package release"


class AdjustmentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    APPLIED = "applied", "Applied to a run"
    REJECTED = "rejected", "Rejected"


class PayrollAdjustment(OrgOwnedModel):
    """
    A one-off amount for a period — a bonus, an arrear, a recovery.

    An adjustment must be APPROVED before a run will pick it up, and becomes
    APPLIED when one does. That is what stops a draft bonus someone was still
    thinking about from being paid by the next run that happens to execute.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="payroll_adjustments"
    )
    payroll_run = models.ForeignKey(
        PayrollRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="adjustments"
    )
    kind = models.CharField(max_length=30, choices=AdjustmentKind.choices)
    label = models.CharField(max_length=120)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])

    period_month = models.PositiveSmallIntegerField()
    period_year = models.PositiveSmallIntegerField()

    is_taxable = models.BooleanField(default=True)
    is_employer_side = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20, choices=AdjustmentStatus.choices, default=AdjustmentStatus.DRAFT
    )
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    source_ref = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_year", "-period_month", "employee__employee_code"]
        indexes = [models.Index(fields=["status", "period_year", "period_month"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1, period_month__lte=12),
                name="ck_adjustment_month_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} {self.amount} ({self.employee.employee_code})"

    @property
    def is_deduction(self) -> bool:
        return self.kind in {
            AdjustmentKind.ADVANCE_RECOVERY,
            AdjustmentKind.LOAN_RECOVERY,
            AdjustmentKind.OTHER_DEDUCTION,
        }


class LoanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"
    WRITTEN_OFF = "written_off", "Written off"


class EmployeeLoan(OrgOwnedModel):
    """
    A salary advance or loan recovered through payroll.

    `balance` is maintained by the run that recovers an instalment, so the
    schedule and the money actually recovered cannot drift apart.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="loans"
    )
    reference = models.CharField(max_length=60, blank=True)
    principal = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])
    monthly_installment = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])
    balance = models.DecimalField(**MONEY, default=ZERO)

    start_month = models.PositiveSmallIntegerField()
    start_year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=LoanStatus.choices, default=LoanStatus.ACTIVE)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["employee", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_month__gte=1, start_month__lte=12),
                name="ck_loan_start_month_range",
            ),
            models.CheckConstraint(
                condition=models.Q(balance__gte=Decimal("0")),
                name="ck_loan_balance_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"Loan {self.reference or self.pk} — {self.employee.employee_code}"

    def installment_due(self) -> Decimal:
        """Never recover more than remains owed, even if the EMI is larger."""
        if self.status != LoanStatus.ACTIVE or self.balance <= ZERO:
            return ZERO
        return min(self.monthly_installment, self.balance)


class ReimbursementStatus(models.TextChoices):
    CLAIMED = "claimed", "Claimed"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"


class ReimbursementClaim(OrgOwnedModel):
    """An expense claim paid out through payroll rather than separately."""

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="reimbursement_claims"
    )
    claim_type = models.CharField(max_length=60)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])
    period_month = models.PositiveSmallIntegerField()
    period_year = models.PositiveSmallIntegerField()

    status = models.CharField(
        max_length=20, choices=ReimbursementStatus.choices, default=ReimbursementStatus.CLAIMED
    )
    approved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_in_run = models.ForeignKey(
        PayrollRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="reimbursements"
    )
    bill_refs = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_year", "-period_month"]
        indexes = [models.Index(fields=["status", "employee"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1, period_month__lte=12),
                name="ck_reimbursement_month_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.claim_type} {self.amount} ({self.employee.employee_code})"



# ---------------------------------------------------------------------------
# Custom packages and deferred release schedules
# ---------------------------------------------------------------------------


class PackageType(models.TextChoices):
    MONTHLY_PLUS_DEFERRED = "monthly_plus_deferred", "Monthly fixed + deferred amount"
    YEAR_WISE = "year_wise", "Year-wise package"
    PERIOD_WISE = "period_wise", "Custom period-wise package"
    MILESTONE = "milestone", "Milestone / completion-based release"


class PackageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    ON_HOLD = "on_hold", "On hold"
    CANCELLED = "cancelled", "Cancelled"


class ReleaseCondition(models.TextChoices):
    AFTER_MONTHS = "after_months", "After completing N months"
    ON_DATE = "on_date", "On a specific date"
    BOND_COMPLETION = "bond_completion", "Completion of bond/service period"
    MANUAL = "manual", "Manual approval"
    OTHER = "other", "Other configured condition"


class DeferralStatus(models.TextChoices):
    PENDING = "pending", "Pending completion"
    ELIGIBLE = "eligible", "Eligible - pending approval"
    APPROVED = "approved", "Approved - scheduled for payroll"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"
    ON_HOLD = "on_hold", "On hold"
    CANCELLED = "cancelled", "Cancelled"


class EmployeePackage(OrgOwnedModel):
    """
    An employee's agreed TOTAL package, when it is not simply CTC / 12.

    The package is the AGREEMENT layer. Monthly pay still flows exclusively
    through the salary structure and the run; deferred amounts enter payroll
    only as an approved DEFERRED_RELEASE adjustment. Nothing here is a second
    way to pay anyone — which is precisely what makes double-counting
    structurally impossible: the package records and validates, the payroll
    engine pays.
    """

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="packages"
    )
    total_amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])
    package_type = models.CharField(
        max_length=30, choices=PackageType.choices, default=PackageType.PERIOD_WISE
    )
    start_date = models.DateField()
    end_date = models.DateField()

    #: Internal HR/Finance notes. Never serialised to the employee's own view.
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20, choices=PackageStatus.choices, default=PackageStatus.DRAFT
    )
    activated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    #: Revision history (a CTC change never edits an active schedule in
    #: place): the new package supersedes the old, which stays as history.
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
    )

    # --- optional bond / service period (informational; recoveries are never
    # --- automatic and must be separately configured and approved) ---
    bond_start_date = models.DateField(null=True, blank=True)
    bond_end_date = models.DateField(null=True, blank=True)
    bond_required_months = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="ck_package_dates_ordered",
            ),
            #: One live agreement per person. Two would make "the package"
            #: ambiguous and every dashboard number contestable.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(status__in=["active", "on_hold"], is_active=True),
                name="uniq_live_package_per_employee",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.employee_code} package {self.total_amount}"

    @property
    def allocated_total(self) -> Decimal:
        periods = sum((p.amount for p in self.periods.all() if p.is_active), ZERO)
        deferrals = sum(
            (d.amount for d in self.deferrals.all()
             if d.is_active and d.status not in (DeferralStatus.CANCELLED, DeferralStatus.REJECTED)),
            ZERO,
        )
        return periods + deferrals


class PackagePeriod(OrgOwnedModel):
    """One slice of the package paid through NORMAL monthly payroll."""

    #: Inherits its organization from `package` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "package"

    package = models.ForeignKey(EmployeePackage, on_delete=models.CASCADE, related_name="periods")
    order = models.PositiveSmallIntegerField(default=1)
    label = models.CharField(max_length=80)  # "Year 1", "Months 1-12"
    start_date = models.DateField()
    end_date = models.DateField()
    #: The TOTAL for the period; the monthly figure derives from it.
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])

    class Meta:
        ordering = ["order", "start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="ck_package_period_dates_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label}: {self.amount}"

    @property
    def months(self) -> int:
        return (
            (self.end_date.year - self.start_date.year) * 12
            + (self.end_date.month - self.start_date.month) + 1
        )

    @property
    def monthly_amount(self) -> Decimal:
        months = self.months
        if not months:
            return ZERO
        return (self.amount / Decimal(months)).quantize(Decimal("0.01"))


class PackageDeferral(OrgOwnedModel):
    """
    A portion of the package NOT paid monthly, scheduled for future release.

    Becoming ELIGIBLE never pays anything: HR Head / Finance Head approve the
    release, which creates a DEFERRED_RELEASE payroll adjustment for a chosen
    period — from there the ordinary run machinery (separate payslip line,
    statutory treatment per the configured rules, locking) applies unchanged.
    """

    #: Inherits its organization from `package` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "package"

    package = models.ForeignKey(EmployeePackage, on_delete=models.CASCADE, related_name="deferrals")
    label = models.CharField(max_length=120, default="Deferred amount")
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])

    condition_type = models.CharField(
        max_length=20, choices=ReleaseCondition.choices, default=ReleaseCondition.AFTER_MONTHS
    )
    #: For AFTER_MONTHS (years are expressed as months x 12).
    condition_months = models.PositiveSmallIntegerField(null=True, blank=True)
    #: The computed / configured date the amount becomes eligible. Null for
    #: purely manual conditions.
    eligible_on = models.DateField(null=True, blank=True)
    condition_note = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20, choices=DeferralStatus.choices, default=DeferralStatus.PENDING
    )
    decided_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=500, blank=True)

    #: The bridge into payroll: set when a release is approved. The deferral
    #: shows PAID once this adjustment has been applied by a run.
    released_in_adjustment = models.ForeignKey(
        PayrollAdjustment, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["eligible_on", "created_at"]

    def __str__(self) -> str:
        return f"{self.label}: {self.amount} [{self.status}]"

    @property
    def effective_status(self) -> str:
        """APPROVED becomes PAID the moment the linked adjustment is applied."""
        if (
            self.status == DeferralStatus.APPROVED
            and self.released_in_adjustment_id
            and self.released_in_adjustment.status == AdjustmentStatus.APPLIED
        ):
            return DeferralStatus.PAID
        return self.status
