# Statutory Golden-Master Fixtures

## The rule that governs this directory

> **Expected values are derived by hand from the statute and the rule-set
> parameters. They are never produced by running the engine.**

A fixture whose expectation was copied from the implementation's output asserts
only that the code agrees with itself. It would pass just as happily against a
wrong implementation, which makes it worse than no test — it manufactures
confidence.

Every fixture therefore carries a `derivation` block showing the arithmetic, so
a reviewer can check it with a calculator and no code.

## Layout

```
fixtures/statutory/
  rate_sets/          Tier A — the numbers. Human-verified.
    pf/2024-04-01.yaml
    esi/2024-04-01.yaml
    pt/MH/2024-04-01.yaml
    ...
  cases/              Tier B — hand-derived expectations.
    pf/normal.yaml  pf/boundary.yaml  pf/exemption.yaml  pf/edge.yaml
    esi/...
```

## Verification status is inherited

A case is only as authoritative as the rate set it was computed against:

| Rate set status | What a passing case proves |
|---|---|
| `unverified` | **Arithmetic only** — the formula was applied correctly to these numbers |
| `verified` | **Compliance** — the numbers are lawful *and* correctly applied |

The test report states which. This distinction is reported rather than blurred,
because "all tests green" against unverified rates is exactly the false comfort
the previous system ran on for a year.

## Case schema

```yaml
- id: pf-boundary-at-ceiling          # unique, stable, referenced in failures
  category: boundary                  # normal | boundary | exemption | edge
  rule_set: pf/2024-04-01             # the exact rate set used
  covers:                             # parameters this case exercises —
    - wage_ceiling                    # drives the coverage meta-test
    - employee_rate
  source: "EPF & MP Act 1952 s.6"
  assumptions:
    - "Domestic employee; the ceiling does not apply to international workers"
  given:
    pf_wage: "15000.00"
  expect:
    employee: "1800.00"
  derivation: |
    Wage is exactly at the ceiling, so the contribution base is the wage itself.
    Employee share = 15,000.00 x 12% = 1,800.00
```

`covers` is load-bearing: `test_fixture_coverage.py` fails the build if any
parameter in a rate set is never named by any case.
