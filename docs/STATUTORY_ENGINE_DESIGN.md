# Statutory Engine — Design for Review

**Status:** awaiting review. No engine code until this is signed off.
**Approved inputs:** two-tier split · `UNVERIFIED` hard gate · coverage-based fixtures ·
effective-date versioning · 87A / marginal relief / MH women's exemption as versioned
eligibility rules.

---

# 1. Two-Tier Architecture

## 1.1 The boundary

```
┌──────────────────────────────────────────────────────────────────┐
│ TIER 2 — Payroll processing            apps/payroll/             │
│   PayrollRun lifecycle · Payslip generation · adjustments        │
│   loans · reimbursements · NEFT · registers · PDF                │
│   Knows: employees, periods, attendance, money movement          │
└───────────────────────────┬──────────────────────────────────────┘
                            │ calls, passing a frozen StatutoryContext
                            │ receives a StatutoryAssessment
┌───────────────────────────▼──────────────────────────────────────┐
│ TIER 1 — Statutory rule evaluation     apps/statutory/           │
│   Rule sets (versioned data) + rule implementations (versioned   │
│   code) + resolution by effective date                           │
│   Knows: wages, dates, employee ATTRIBUTES. Nothing else.        │
│   PURE: no DB writes, no payroll models, no I/O                  │
└──────────────────────────────────────────────────────────────────┘
```

**Tier 1 never imports Tier 2.** Not `PayrollRun`, not `Payslip`, not
`Employee`. It receives a plain frozen dataclass and returns one. This is
enforced by a test, not by discipline:

```python
# tests/statutory/test_tier_isolation.py
def test_statutory_tier_does_not_import_payroll():
    """Tier 1 must be reusable for what-if projections, offer letters and
    arrears recomputation — none of which involve a PayrollRun."""
    forbidden = ("apps.payroll", "apps.employees", "apps.attendance")
    for module in walk_modules("apps.statutory"):
        for imported in imports_of(module):
            assert not imported.startswith(forbidden), (
                f"{module} imports {imported} — Tier 1 must stay pure."
            )
```

**Why it matters beyond tidiness:** the same evaluation must serve a payroll run,
a salary-offer projection, an arrears recomputation for a backdated revision, and
a "what would this cost" simulation. Only the first has a `PayrollRun`. Coupling
Tier 1 to payroll would force three of those four to fake one.

## 1.2 The interface

```python
# apps/statutory/contracts.py — the ONLY types crossing the boundary

@dataclass(frozen=True)
class StatutoryContext:
    """Everything the statutes need to know. Deliberately attribute-based:
    Tier 1 receives `gender`, not an Employee — so it cannot reach for
    anything it wasn't given."""
    period_start: date            # drives rule-set resolution
    financial_year: str           # "2025-2026"
    period_month: int             # PT February special, contribution periods

    gross_wage: Decimal           # for ESI, PT
    pf_wage: Decimal              # Basic + DA (Code on Wages definition)
    annual_gross_projection: Decimal

    state: str                    # PT jurisdiction
    gender: str                   # MH women's exemption
    date_of_joining: date
    date_of_exit: date | None
    is_disabled: bool = False     # ESI higher threshold
    is_international_worker: bool = False   # PF ceiling does not apply
    pf_opted_out: bool = False
    esi_already_liable_this_period: bool = False   # contribution-period continuation

    tax_regime: str | None = None          # None = statutory default applies
    declared_deductions: Mapping[str, Decimal] = field(default_factory=dict)

@dataclass(frozen=True)
class StatutoryAssessment:
    """What the statutes produce. Carries provenance so a payslip can prove
    which rule version produced each number."""
    pf: PFResult
    esi: ESIResult
    professional_tax: PTResult
    gratuity: GratuityResult
    income_tax: TDSResult
    rule_sets_used: Mapping[str, RuleSetRef]   # statute -> (id, version, checksum)
    warnings: tuple[str, ...]
```

`rule_sets_used` is the mechanism for §4 reproducibility: Tier 2 stores it
verbatim on the run.

## 1.3 Rules as versioned code + versioned data

Approved constraint: *"Do not hard-code these as permanent constants; implement
them as versioned, eligibility-based statutory rules."*

Two things vary independently, so they version independently:

| | Varies when | Lives in |
|---|---|---|
| **Numbers** — rates, thresholds, slab boundaries, caps | Every Finance Act, every state notification | `StatutoryRuleSet.parameters` (JSONB, versioned by effective date) |
| **Logic** — how the numbers combine; whether a rebate exists at all | Rarely, but structurally (87A rebate introduced; marginal relief added) | Versioned Python class, selected by `rule_version` |

```python
# apps/statutory/rules/income_tax/v2.py
class IncomeTaxV2(StatutoryRule):
    """FY2023-24 onward: adds s.87A rebate with marginal relief."""
    version = "income_tax.v2"
    required_parameters = (
        "slabs", "standard_deduction", "cess_rate",
        "rebate.max_taxable_income", "rebate.max_amount",
        "rebate.marginal_relief_enabled",
        "surcharge.bands", "deduction_caps",
    )
    def evaluate(self, ctx: StatutoryContext, params: Params) -> TDSResult: ...
```

**I deliberately did not build an expression DSL for eligibility.** A mini-language
for tax law becomes an unauditable second programming environment that nobody can
review and no type checker can see. Logic stays in reviewable, testable Python;
only the *numbers and eligibility parameters* are data. The pairing is explicit
and asserted: a rule set naming `income_tax.v2` must supply exactly that class's
`required_parameters`, checked at load and by a system check.

### The three approved rules, expressed this way

**Section 87A rebate + marginal relief** — parameters, not constants:
```yaml
rebate:
  applies_to_regime: new
  max_taxable_income: 1200000.00     # ← changes with each Finance Act
  max_amount:          60000.00
  marginal_relief_enabled: true
```
Logic in `income_tax.v2`: if the regime matches and taxable income ≤ threshold,
rebate = min(tax, max_amount). If marginal relief is enabled and income sits just
above the threshold, tax is capped at `taxable_income - max_taxable_income`, so
earning ₹1 more can never cost more than ₹1 in tax.

**Maharashtra women's PT exemption** — an eligibility record on the PT rule set:
```yaml
exemptions:
  - id: mh-women-monthly-wage
    conditions: {gender: F, max_monthly_wage: 25000.00}
    result:     {monthly_amount: 0.00}
    source:     "<MH PT Act amendment reference>"
```
Evaluated by `professional_tax.v2` before slab lookup. Conditions are a fixed,
typed vocabulary (`gender`, `max_monthly_wage`, `min_age`, `is_disabled`) — not
free-form expressions — so every possible condition is enumerable and testable.

---

# 2. Effective-Date & Versioning Model

## 2.1 Schema

```python
class Statute(models.TextChoices):
    PF = "pf"; ESI = "esi"; PROFESSIONAL_TAX = "pt"
    GRATUITY = "gratuity"; INCOME_TAX = "income_tax"

class VerificationStatus(models.TextChoices):
    DRAFT     = "draft"       # being edited
    PENDING   = "pending"     # submitted for verification
    VERIFIED  = "verified"    # usable in a live payroll run
    REJECTED  = "rejected"    # verifier found it wrong
    SUPERSEDED = "superseded" # closed by a later set

class StatutoryRuleSet(BaseModel):
    statute       = CharField(choices=Statute.choices, db_index=True)
    jurisdiction  = CharField(max_length=8, blank=True)   # state code; "" = national
    regime        = CharField(max_length=16, blank=True)  # old/new; "" = n/a

    effective_from = DateField(db_index=True)
    effective_to   = DateField(null=True, blank=True)     # NULL = still in force
    financial_year = CharField(max_length=9, blank=True)  # "2025-2026"

    rule_version  = CharField(max_length=40)   # selects the implementation class
    parameters    = JSONField()                # the numbers

    # --- provenance (required before verification) ---
    source_citation = CharField(max_length=500)
    source_url      = URLField(blank=True)
    retrieved_on    = DateField(null=True)
    assumptions     = JSONField(default=list)

    # --- verification ---
    verification_status = CharField(
        choices=VerificationStatus.choices, default=VerificationStatus.DRAFT, db_index=True
    )
    verified_by   = FK(User, null=True, on_delete=PROTECT, related_name="+")
    verified_at   = DateTimeField(null=True)
    verification_note = TextField(blank=True)

    # SHA-256 over (rule_version, canonicalised parameters). Recomputed on every
    # save; a mismatch against `verified_checksum` means the content changed
    # after sign-off, which silently reverts the set to DRAFT.
    checksum          = CharField(max_length=64, editable=False)
    verified_checksum = CharField(max_length=64, blank=True, editable=False)

    class Meta:
        constraints = [
            # One rule set in force per (statute, jurisdiction, regime) at a time.
            ExclusionConstraint(
                name="excl_ruleset_no_overlap",
                expressions=[
                    ("statute", RangeOperators.EQUAL),
                    ("jurisdiction", RangeOperators.EQUAL),
                    ("regime", RangeOperators.EQUAL),
                    (DateRange("effective_from", "effective_to", RangeBoundary()),
                     RangeOperators.OVERLAPS),
                ],
                condition=Q(verification_status__in=["verified", "superseded"]),
            ),
        ]
```

The `ExclusionConstraint` is the important one: PostgreSQL itself refuses two
overlapping in-force rule sets for the same statute and jurisdiction. Ambiguous
resolution becomes structurally impossible rather than something a resolver has to
tie-break. Draft sets are excluded from the constraint so alternatives can be
prepared side by side.

## 2.2 Resolution

```python
def resolve(statute, *, on_date, jurisdiction="", regime="", allow_unverified=False):
    """
    Exactly one rule set, or a hard failure. Never a silent default —
    a missing rule set must stop a payroll run, not produce zero.
    """
```

Resolution is `effective_from <= on_date AND (effective_to IS NULL OR
effective_to >= on_date)`. No fallback to "most recent", no implicit zero. If
nothing matches, `StatutoryRuleSetMissing` is raised and the run stops with a
message naming the statute, jurisdiction and date.

## 2.3 Historical reproducibility

Approved requirement: *"A payroll run must permanently reference the exact
statutory rule-set versions used."*

```python
class PayrollRun(BaseModel):
    ...
    # Frozen at process time; immutable once locked. Survives the rule set being
    # superseded, corrected, or deleted.
    statutory_snapshot = JSONField(default=dict)
    #  {"pf": {"rule_set_id": "...", "rule_version": "pf.v1",
    #          "checksum": "a3f…", "effective_from": "2024-04-01",
    #          "verified_by": "finance@…", "verified_at": "..."},
    #   "pt:MH": {...}, "income_tax:new": {...}}

class PayrollRunRuleSet(models.Model):
    """Queryable join — answers 'which runs used the rule set we just found
    to be wrong?', which a JSONB blob cannot do efficiently."""
    payroll_run = FK(PayrollRun, on_delete=PROTECT)
    rule_set    = FK(StatutoryRuleSet, on_delete=PROTECT)   # PROTECT: never orphan history
    class Meta:
        constraints = [UniqueConstraint(fields=["payroll_run", "rule_set"], name="uniq_run_ruleset")]
```

Both, deliberately: the JSONB snapshot is the immutable evidence (it holds the
checksum and survives deletion); the join table is the index for impact analysis.
`on_delete=PROTECT` means a rule set referenced by any run can never be deleted —
only superseded.

**Reproducibility test:** recompute an FY2024-25 run after FY2025-26 rules are
loaded, and assert every payslip line is identical to the original.

---

# 3. Rate-Set Verification Workflow

## 3.1 States

```
   DRAFT ──submit──> PENDING ──verify──> VERIFIED ──supersede──> SUPERSEDED
     ▲                  │                    │
     └──── reject ──────┘                    │
     └──── any parameter edit ───────────────┘   (checksum mismatch)
```

## 3.2 Authority

| Action | Permission | Roles |
|---|---|---|
| Create / edit a draft | `STATUTORY_CONFIG.EDIT` | Admin, Finance Head, Accounts Manager |
| Submit for verification | `STATUTORY_CONFIG.EDIT` | same |
| **Verify** | **`STATUTORY_CONFIG.APPROVE`** | **Finance Head only** |
| View | `STATUTORY_CONFIG.VIEW` | Finance roles, Admin, CEO (read) |

Approved constraint: *"Only an authorized Finance role can verify."* Note this
means **Admin cannot verify** — deliberately. Admin can edit a draft, but signing
off that a rate matches the gazette is a finance-competence act, not a systems-
administration one. Same reasoning as HR Head owning candidate rejection.

**Self-verification is blocked:** the user who last edited a rule set cannot be
the one who verifies it, unless they hold an explicit, audited override. Two
pairs of eyes on the numbers that determine everyone's pay.

## 3.3 Verification requires evidence

Submission is refused unless `source_citation` and `retrieved_on` are present.
Verification is refused unless the verifier supplies `verification_note` — a free-
text statement of what they checked against. "Verified" with no evidence is
indistinguishable from "clicked the button".

## 3.4 Tamper detection

`checksum` is recomputed on every save over the canonicalised parameters. If a
VERIFIED set's checksum diverges from `verified_checksum`, the set **reverts to
DRAFT automatically** and an audit entry records it. Editing a verified rate
silently is the failure mode this prevents.

## 3.5 The hard gate

```python
def assert_ready_for_payroll(run) -> None:
    """Raises StatutoryNotVerified listing every offending rule set.
    Called by process_run BEFORE any payslip is computed — a partially
    computed run against unverified rates is worse than no run."""
```

A run cannot leave `DRAFT` if any required rule set is not VERIFIED. The error
names each statute, jurisdiction and status so finance knows exactly what to sign
off. Dev and staging may set `STATUTORY_ALLOW_UNVERIFIED=True`; **production
refuses to boot with that flag set.**

## 3.6 Audit

Every transition writes an `AuditLog` row: submit, verify, reject, supersede,
auto-revert. `StatutoryRuleSet` is registered with the audit signal layer, so
parameter diffs are captured field by field. Verification records who, when, what
citation, and the checksum at the moment of sign-off.

---

# 4. Fixture Categories

Approved framing: *coverage, not the number 60.*

## 4.1 Coverage rule, enforced mechanically

> Every parameter in a verified rule set must be exercised by at least one
> fixture; every threshold must have below / at / above; every eligibility
> condition must have a met case and a not-met case.

This is checkable, so it is checked:

```python
# tests/statutory/test_fixture_coverage.py
def test_every_parameter_is_exercised():
    """Fails naming any parameter no fixture touches. Adding a parameter
    without a fixture breaks the build."""
```

That converts "we think we covered it" into a build failure, the same pattern as
the unmapped-view check in the access layer.

## 4.2 Categories per statute

Every cell below is a *category*, expanded to as many fixtures as the parameters
demand.

### PF
| Category | Coverage |
|---|---|
| Normal | Wage below ceiling; wage above ceiling; typical mid salary |
| Boundary | Exactly at ceiling; ±₹1; EPS monthly cap exactly reached; EPS cap exceeded |
| Exemption | `applies=false`; employee opted out; no rule set (must raise, not zero) |
| Edge | International worker (no ceiling); zero wage; wage ≤ 0 after LOP; part-period joiner; paisa rounding |

### ESI
| Category | Coverage |
|---|---|
| Normal | Well below threshold; well above |
| Boundary | Exactly at threshold (**inclusivity asserted explicitly**); ±₹1; disability threshold at/±₹1 |
| Exemption | `applies=false`; no rule set |
| Edge | **Contribution-period continuation** — crosses threshold mid-period, remains liable to period end; joiner mid-period; both halves (Apr–Sep, Oct–Mar); rounding |

### Professional Tax
| Category | Coverage |
|---|---|
| Normal | MH mid-band; KA mid-band; nil-PT state; third state |
| Boundary | Every band edge in every configured state, at and ±₹1 |
| Exemption | **MH women's exemption — met, and not met at wage +₹1**; below lowest band; state with no rule set |
| Edge | **MH February special amount**; effective-window expiry; unknown state code; exemption interaction with the February special |

### Gratuity
| Category | Coverage |
|---|---|
| Normal | 10 years; 7 years |
| Boundary | Exactly 5 years; 4y 11m; the 240-day rule; statutory cap reached and exceeded |
| Exemption | Below eligibility; not applicable |
| Edge | Monthly provisioning accrual; ≥6-month rounding up and <6-month down; cap vs tax-exemption cap interaction |

### Income Tax / TDS
| Category | Coverage |
|---|---|
| Normal | New regime mid-slab ×2; old regime mid-slab ×2 |
| Boundary | Every slab edge in both regimes; **87A rebate threshold at / ±₹1**; **marginal relief band — entry, middle, exit**; surcharge band edges |
| Exemption | Below standard deduction; 80C at cap; 80C over cap (clamped); 80D; HRA |
| Edge | Regime default when undeclared; mid-year joiner projection; cess applied after rebate and surcharge (order asserted); negative taxable income floors at zero; **rebate must not apply in the old regime** |

**Marginal relief gets its own dedicated set**, because it is the subtlest rule
here and the easiest to get wrong: the invariant is that across the entire relief
band, an extra ₹1 of income never produces more than ₹1 of extra tax. That is
expressible as a property test sweeping the band, alongside the point fixtures.

## 4.3 Provenance on every fixture

```yaml
- id: tds-87a-boundary-at-threshold
  category: boundary
  statute: income_tax
  rule_set: income_tax/2025-2026-new       # the exact version used
  source: "Income Tax Act 1961 s.87A; Finance Act <year>"
  assumptions:
    - "Salaried individual, no other income"
    - "No surcharge applicable at this level"
  given:
    annual_gross_projection: 1275000.00
    tax_regime: new
  expect:
    taxable_income: 1200000.00
    tax_before_rebate: <hand-computed>
    rebate_applied: <hand-computed>
    marginal_relief: 0.00
    cess: <hand-computed>
    annual_tax: <hand-computed>
  derivation: >
    Step-by-step arithmetic, shown, so a reviewer can check it without
    running anything.
```

**Expected values are hand-derived from the statute and the rule-set parameters.**
Never produced by running the engine — a fixture whose expectation came from the
implementation asserts only that the code agrees with itself.

Because the expectations depend on rate values that are still `UNVERIFIED`, each
fixture inherits the verification status of its rule set. A fixture against an
unverified set proves *arithmetic*; it becomes a *compliance* assertion only once
finance signs the underlying numbers off. The test report states which is which.

---

# 5. Build Order

1. Contracts + rule-set model + resolver + verification workflow *(no arithmetic)*
2. Tier-isolation test and fixture-coverage test — both failing, defining "done"
3. Tier B fixtures, hand-derived, per statute
4. Rule implementations until the fixtures pass
5. Tier A rule sets authored as `DRAFT`/`UNVERIFIED` with citations, for finance
6. Tier 2 integration: `statutory_snapshot`, the join table, the hard gate
7. Reproducibility test: recompute a prior-FY run after new rules load

---

# 6. For review

1. **Tier boundary** — Tier 1 takes attributes, not an `Employee`, and is import-isolated by test. Confirm.
2. **Versioned code + versioned data**, no eligibility DSL. Confirm you're comfortable that rule *logic* changes require a code release while rate *changes* are data-only.
3. **Finance Head alone verifies — Admin cannot.** Confirm.
4. **Self-verification blocked** (editor ≠ verifier). Confirm, or say if a single-person finance team makes this impractical.
5. **PostgreSQL exclusion constraint** preventing overlapping in-force rule sets. Confirm.
6. **Fixtures inherit verification status** — arithmetic proof now, compliance proof after sign-off. Confirm this distinction is reported rather than blurred.
