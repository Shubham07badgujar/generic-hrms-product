# Payroll Golden-Master Fixtures — Proposal for Approval

**Status:** awaiting approval. No statutory engine code will be written until the
categories and sources below are signed off.

---

## 1. Finding that changes the plan

The previous system's `test_payroll_compute.py` was described in our architecture
document as a "compliance oracle". **It is not.** It is an *arithmetic* oracle,
and the distinction matters.

Every test there builds its own configuration fixture and then asserts the engine
computes correctly against it:

```python
@pytest.fixture
def pf_cfg(db):
    PFConfig.objects.create(employee_rate=Decimal("12.0000"),
                            wage_ceiling=Decimal("15000.00"), ...)
```

That proves *"12% of ₹15,000 is ₹1,800"*. It does not prove *"12% and ₹15,000 are
the legally correct values"*. The statutory numbers came from
`seed_statutory.py`, whose own docstring says:

> These rates were correct as of early-2025 public sources … **HR MUST verify each
> row against the latest gazette before any production payroll run.**

That verification was never done. So porting those values forward as "golden
master" would launder unverified numbers into something that *looks* authoritative.
I'm not willing to do that silently.

### Defects found in the old reference data

| # | Issue | Impact |
|---|---|---|
| 1 | The fixture labelled `financial_year="2025-2026"` uses the **FY 2024-25** new-regime slabs (0–3L/3–7L/7–10L/10–12L/12–15L/15L+, ₹75k standard deduction). Budget 2025 restructured these for FY 2025-26. | Wrong tax for every employee |
| 2 | **Section 87A rebate is entirely absent** — `grep -i "87a\|rebate"` returns **0 matches** across the whole engine. Under the FY 2025-26 new regime the rebate makes income up to ~₹12L effectively tax-free. | Massive over-deduction for most staff |
| 3 | **Marginal relief** on the 87A rebate: absent | Cliff-edge over-taxation just above the threshold |
| 4 | **Surcharge** on high incomes (₹50L+): absent | Under-deduction for senior staff |
| 5 | Maharashtra PT **gender-based exemption** (women up to a monthly-wage threshold): `grep -i gender` returns **0 matches** | Over-deduction from women employees; state-law non-compliance |
| 6 | ESI **contribution-period continuation** (once ESI applies at the start of a contribution period it continues to its end even if wages rise above the threshold): absent | Mid-period drop-off that ESIC does not permit |
| 7 | EPS monthly cap (₹1,250 at the ₹15,000 ceiling) is *implied* by arithmetic rather than stated, and international workers (no ceiling) are unmodelled | Edge-case errors |

Items 2, 3 and 5 are the ones I would consider blocking for a production payroll run.

---

## 2. Proposed structure — two tiers, deliberately separated

The core problem is that "the engine computes correctly" and "the rates are
lawful" are different claims with different verification methods. Conflating
them is what produced the situation above.

### Tier A — Statutory rate sets (reference data, **human-verified**)

Versioned by effective date. Each set is a YAML file carrying provenance:

```yaml
# fixtures/statutory/pf/2024-04-01.yaml
statute:        Employees' Provident Funds and Miscellaneous Provisions Act, 1952
instrument:     EPFO contribution rates
effective_from: 2024-04-01
effective_to:   null            # null = still in force
financial_year: "2024-2025"
source:
  citation:     "<gazette / EPFO circular reference>"
  url:          "<official source URL>"
  retrieved_on: "<date>"
verification:
  status:       UNVERIFIED      # UNVERIFIED | VERIFIED
  verified_by:  null            # named human
  verified_on:  null
values:
  employee_rate:      12.0000
  employer_rate:      12.0000
  eps_rate:            8.3300
  eps_monthly_cap:  1250.00
  wage_ceiling:    15000.00
  admin_charge_rate:   0.5000
assumptions:
  - "Domestic employees only. International workers have no wage ceiling."
  - "EPS applies only to members who joined before the applicable cut-off."
```

**A rate set with `status: UNVERIFIED` cannot be used by a production payroll
run.** The engine will refuse it — this is a hard gate, not a warning, because a
warning is exactly what the previous system had and it was ignored for a year.

I will populate these files with my best understanding of current values, **every
one marked `UNVERIFIED`**, as a working starting point for your finance/HR
reviewer to correct and sign off. I am explicitly **not** the source of truth for
Indian statutory rates: I cannot verify a gazette from memory to the standard
payroll compliance requires, and a confident-sounding wrong rate is worse than an
obviously unverified one.

### Tier B — Calculation fixtures (arithmetic, machine-checkable)

These I can author with full confidence, because they test *formula application*,
which is stable and defined in the Acts themselves. Each fixture is a table of
inputs and hand-computed expected outputs, with the arithmetic shown:

```yaml
# fixtures/calculations/pf/boundary.yaml
- id: pf-boundary-at-ceiling
  description: Wage exactly at the ceiling — contribution computed on the ceiling
  rate_set: pf/2024-04-01
  given:
    wage: 15000.00
  expect:
    pf_base:          15000.00
    employee:          1800.00   # 15000 x 12%
    employer_eps:      1249.50   # 15000 x 8.33%
    employer_epf:       550.50   # 1800 - 1249.50
    admin_charge:        75.00   # 15000 x 0.5%
  reasoning: >
    At exactly the ceiling the base is the wage itself. EPS is 8.33% of base,
    and the employer's EPF share is the remainder of the 12% employer
    contribution after EPS.
```

Expected values are **derived by hand from the statutory formula**, never by
running the engine and recording what it printed. A fixture whose expected value
came from the implementation tests nothing.

---

## 3. Fixture categories

Four categories per statute. Counts are the minimum I propose to author.

### PF — Employees' Provident Funds Act, 1952

| Category | Cases | Examples |
|---|---|---|
| Normal | 3 | Wage below ceiling; wage above ceiling; typical ₹30k salary |
| Boundary | 4 | Exactly at ceiling; ₹1 below; ₹1 above; EPS cap exactly reached |
| Exemption | 3 | `applies=false`; no config for the period; employee opted out |
| Edge | 4 | Zero wage; mid-month joiner (part-period); wage ≤ 0 after LOP; rounding to the paisa |

### ESI — Employees' State Insurance Act, 1948

| Category | Cases | Examples |
|---|---|---|
| Normal | 2 | Gross well below threshold; gross well above |
| Boundary | 4 | Exactly at threshold (**inclusive** — must be asserted, it is a real ambiguity); ₹1 below; ₹1 above; disability threshold |
| Exemption | 2 | `applies=false`; no config |
| Edge | 3 | **Contribution-period continuation** (crosses the threshold mid-period → still liable to period end); joiner mid-period; rounding |

### Professional Tax — per-state Acts

| Category | Cases | Examples |
|---|---|---|
| Normal | 4 | Maharashtra mid-band; Karnataka mid-band; a nil-PT state (Delhi); a third state |
| Boundary | 5 | Each band edge in MH and KA, above and below by ₹1 |
| Exemption | 3 | **MH women's exemption**; below lowest band; state with no slabs configured |
| Edge | 3 | **MH February special amount**; slab valid-window expiry; unknown state code |

### Gratuity — Payment of Gratuity Act, 1972

| Category | Cases | Examples |
|---|---|---|
| Normal | 2 | 10 years' service; 7 years' service |
| Boundary | 4 | Exactly 5 years (eligibility); 4y 11m (ineligible); 4y 240d rule; statutory cap reached |
| Exemption | 2 | Below eligibility; not applicable |
| Edge | 3 | Monthly provisioning accrual; part-year rounding (≥6 months rounds up); cap interaction with tax exemption |

### TDS — Income Tax Act / Finance Act, per FY

| Category | Cases | Examples |
|---|---|---|
| Normal | 4 | New regime mid-slab; old regime mid-slab; each at two income levels |
| Boundary | 6 | Each slab edge; **87A rebate threshold**; **marginal relief band**; surcharge threshold |
| Exemption | 4 | Income below standard deduction; 80C at cap; 80C over cap (clamped); 80D |
| Edge | 5 | Regime default when undeclared; HRA exemption; mid-year joiner projection; cess application order; negative taxable income |

**Total: ~60 calculation fixtures across 5 statutes.**

---

## 4. Effective-date versioning

Rate sets are immutable once verified. A rate change closes the old set and opens
a new one; historical payroll runs continue resolving the set that was in force
on their period date.

```
fixtures/statutory/
  pf/    2024-04-01.yaml   2025-04-01.yaml
  esi/   2024-04-01.yaml
  pt/    MH/2023-04-01.yaml   MH/2025-04-01.yaml
         KA/2024-04-01.yaml
  gratuity/  2024-04-01.yaml
  incometax/ 2024-2025-new.yaml  2024-2025-old.yaml
             2025-2026-new.yaml  2025-2026-old.yaml
```

Resolution is `effective_from <= period_date AND (effective_to IS NULL OR
effective_to >= period_date)` — the same pattern already used for leave policies
and salary structures. **Reprocessing a FY 2024-25 payroll run in 2027 must
produce the FY 2024-25 result**, and there will be a regression test asserting
exactly that.

Calculation fixtures name the rate set they were computed against, so when a new
rate set lands the old fixtures keep passing against the old set rather than
needing rewrites.

---

## 5. Sources

### Primary (authoritative — what verification must cite)

| Statute | Authority |
|---|---|
| PF | EPFO — epfindia.gov.in; EPF & MP Act 1952; current contribution circulars |
| ESI | ESIC — esic.gov.in; ESI Act 1948; Rule 50 wage-limit notifications |
| PT | Each state's Professional Tax Act and its Schedule I (MH: Maharashtra State Tax on Professions, Trades, Callings and Employments Act 1975) |
| Gratuity | Payment of Gratuity Act 1972; s.4(2) formula; s.4(3) cap |
| TDS | Income Tax Act 1961 ss.87A, 192; the Finance Act for the relevant year; CBDT circulars |

### What I can and cannot vouch for

| | |
|---|---|
| ✅ **Formula application** — the arithmetic in Tier B | The formulae are stable statute text (`15/26 × last drawn × years`, EPS = 8.33% of base, cess on tax). I will hand-derive every expected value and show the working. |
| ✅ **Structural rules** — thresholds are inclusive/exclusive, contribution periods, effective-date resolution | Derivable from the Acts' own wording. |
| ⚠️ **Current numeric rates and slabs** | Marked `UNVERIFIED`, requiring named human sign-off. My training has a cutoff, Indian statutory rates change with each Finance Act and state notification, and the four Labour Codes are mid-rollout. **This is the one place I should not be trusted, and the system will enforce that.** |

---

## 6. What I need approved

1. **The two-tier split** — rate sets (human-verified) separate from calculation fixtures (machine-checkable).
2. **The hard gate** — production payroll refuses an `UNVERIFIED` rate set. This will block your first live run until finance signs off. That is the intent.
3. **The ~60 fixture categories** in §3.
4. **The effective-date versioning layout** in §4.
5. **Confirmation on the seven defects** in §1 — specifically whether 87A rebate, marginal relief and the Maharashtra women's exemption are in scope for V1. My recommendation: **yes, all three**, because without them the engine computes materially wrong take-home pay.

Once approved I will author Tier B fixtures first, then the engine to satisfy
them, then populate Tier A as `UNVERIFIED` drafts for your reviewer.
