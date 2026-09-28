# Database hardening — verification evidence (development instance)

Run 2026-09-22 on Generic HRMS's **dedicated** development instance
(`dev/postgres/`, Postgres 16.15 at 127.0.0.1:5440). Nothing was deployed.
The live `hrms.drjoshis.in`, the shared 5432 cluster and `deploy/` were not
touched. Release 1: the runtime still connects as the table owner, so the RLS
and audit tests switch their own transaction to the non-owner
`generic_hrms_app` with `SET LOCAL ROLE`.

Implementation is described in `DB_HARDENING_PLAN.md` §8.

## 1. Fresh migration from zero

Into a throwaway database (`generic_hrms_fresh`) on the same instance:

| | |
|---|---|
| `manage.py migrate` from empty | exit 0, 104 migrations, `dbguard` 0001–0006 applied |
| `manage.py check` | clean (includes `access.E018`) |
| `manage.py makemigrations --check --dry-run` | clean — no model/migration drift |

Catalog read back from that database:

| Object | Count |
|---|---|
| Composite `(fk, organization_id)` foreign keys, all `convalidated` | 151 |
| `UNIQUE (id, organization_id)` keys on referenced tables | 41 |
| Tables with row-level security enabled | 100 |
| Tables with RLS **forced** (owner would be constrained) | 0 — deliberate |
| Policies | 102 |
| Audit scrub-only `BEFORE UPDATE` trigger | present |
| `generic_hrms_app` privileges on `audit_auditlog` | INSERT, SELECT, UPDATE — no DELETE, no TRUNCATE |

## 2. Full backend suite

`pytest` (whole suite, fresh test database):

```
1 failed, 2319 passed, 10 skipped, 663 warnings in 3690.72s (1:01:30)
```

The single failure is `tests/statutory/test_disputes.py::test_no_unresolved_statutory_disputes`
— the release gate, which **must** keep failing while statutory question 3
(gazetted Finance Act 2025 text, old-regime slabs) is unresolved and the
FY 2026–27 rate set is missing. It is not a regression.

## 3. Tenancy / isolation suite

`tests/tenancy` ran inside that full-suite run and passed. A standalone rerun
afterwards could not connect (Docker Desktop had stopped on the workstation,
so the 5440 container was down); those 388 errors were environmental, not a
code result.

## 4. Route walker

378 of 378 cross-organization requests refused, 0 served.

## 5. Isolation report

`scripts/verify_tenant_isolation.py`: **311 of 311 checks passed.** Full
output in `TENANT_ISOLATION_TEST_REPORT.md`. The run creates its own fictional
organizations in the dev database (Northwind Health, Aperture Systems,
Fairhaven Retail).

## 6 and 7. New database-level tests, as the non-owner runtime role

`tests/tenancy/test_db_hardening.py` and `tests/tenancy/test_db_audit_guards.py`:
**43 passed.** Every refusal is paired with a positive control, so an empty
table or a role that sees nothing cannot pass for isolation.

Row-level security and composite keys (30):

- every manifest edge has a validated constraint in the migrated database;
- a row pointed at another organization's parent is refused, across eight
  edge classes, with `SET CONSTRAINTS ALL IMMEDIATE`; a NULL optional FK is
  still allowed;
- a raw query with the organization filter omitted sees only the bound
  organization, over every tenant table, while still seeing all of its own rows;
- the ORM escape hatch `all_orgs()` is confined the same way;
- nothing bound ⇒ nothing visible (fail closed);
- another organization's rows cannot be updated or deleted by id, and
  `WITH CHECK` refuses both an insert into another organization and moving a
  row into one;
- `Scope.ALL` principals are confined; the membership table resolves a user
  before any organization is bound, and only that user's own row;
- a customer reads its own subscription only, and cannot write it;
- a platform operator sees no tenant row until `platform_bypass`, sees both
  organizations inside it, and is confined again on exit; the bypass refuses a
  tenant principal, a missing reason, and running outside a transaction;
- a per-organization Celery subtask touches only its own organization;
- a Support Access snapshot returns only the granted organization;
- every organization-owned model is covered by RLS and a policy; the global
  tables deliberately are not;
- the session setting follows the bound organization and survives a
  rolled-back savepoint.

Audit trail (13):

- identity columns (action, resource, entity type, entity id, occurred at,
  organization) are immutable;
- payload may be erased but not rewritten; a real scrub is allowed, and
  `apps/audit/purge.py` still works as the runtime role;
- the runtime role cannot DELETE or TRUNCATE the trail, and the platform role
  cannot either — BYPASSRLS widens visibility, not privilege;
- append still works.

Two of the 43 failed on their first run through faults in the tests
themselves (one checked results while still switched to the runtime role, one
called a manager method the audit table does not have). Both were fixed; the
numbers above are the run after the fix.

---

# Release 2 — the runtime connects as `generic_hrms_app`

Development only, 2026-09-24 to 2026-09-26. The application now connects as
the unprivileged role, so row-level security applies to it for the first time;
the owner is reserved for migrations. See `DB_HARDENING_PLAN.md` §9 for the
design and the file-by-file list.

## What it found

Five real bugs, four of them FAIL-OPEN — the product read "nothing came back"
as a sold answer. None was reachable while the runtime owned the tables, which
is the argument for the release in one line.

| # | Bug | Why it mattered |
|---|---|---|
| 1 | **The seat limit stopped being enforced.** `reserve_seats` reads the subscription with `SELECT ... FOR UPDATE`, and PostgreSQL applies the UPDATE policy to any LOCKING read. The table had a SELECT policy only, so the locking read matched nothing, and "no subscription" means unlimited | A customer could hire past their plan, silently. Measured: plain select 1 row, `FOR UPDATE` 0 rows. Fixed by `dbguard.0007` — an UPDATE policy admitting the organization's own row with `WITH CHECK (false)`, so the lock is permitted and a real write is refused loudly |
| 2 | **`plan_purge` returned an empty plan** | A purge that counts nothing is the worst way for a purge to be wrong |
| 3 | **`email_config` fell back to the deployment's mail identity** | A customer's mail would leave with the platform's from-address and SMTP server instead of their own |
| 4 | **`status_after_setup` read no subscription** | A customer finishing setup mid-trial was marked ACTIVE while their subscription said `trialing` — the exact disagreement that function exists to end |
| 5 | **`_deliver` lost the delivery outcome** | It already took the sender identity from the notification's row; the write-back was unbound, so the mail went out and the record of it did not |

## Two behaviours became stricter

Neither is a regression; both are the database refusing what the application
merely declined to ask for.

- A reverse accessor read while ANOTHER organization is bound returns nothing,
  where the manager would return the parent's rows. Both refuse to leak.
- An update with NOTHING bound matches no rows: fail-closed.

## Verification

| Check | Result |
|---|---|
| Fresh migration from zero | 104 migrations + `dbguard` 0001–0007; `check` and `makemigrations --check` clean |
| Isolation report | **311/311**; route walk **378/378 refused, 0 served** |
| Tenancy/isolation suite | 409 passed, 1 skipped |
| Platform suite | 256 passed |
| Accounts / reporting | 119 / 67 passed |
| RLS + audit tests (`test_db_hardening`, `test_db_audit_guards`) | 43 passed |
| Runtime-role suite (`test_runtime_role`) | 19 passed |
| `migrate` refused as the app role | Confirmed |
| **Full suite in one run** | **Not completed since the final fixes.** Every suite passes individually; the last complete full-suite figure predates them |

## Costs and caveats

- **The suite is materially slower**: ~61 minutes before, and a run stopped at
  1h20m with roughly a third left. Row-level security evaluates a policy per
  row across ~100 tables. Request latency is unmeasured.
- **A platform request is now one transaction** (`PlatformOnlyMixin` enters the
  bypass in `initial()`, after the platform-admin check). Fine for the
  console's short operations; worth knowing before a long-running platform
  endpoint is added.
- **The test harness had to learn the difference** between a probe and a
  ground-truth read. `across_organizations()` is the god's-eye view, used only
  for assertions about what is really in the database, never around the
  behaviour under test. The isolation report needed the same treatment.
