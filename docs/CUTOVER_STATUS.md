# Cutover status

Assessed at Phase 12. **The system is not cleared for production.** Two
categories block it, and they are different in kind: one is a deliberate
compliance hold, the other is missing deployment infrastructure.

---

## 1. Statutory compliance — BLOCKING, by design

Four statutory questions are unresolved. Until they are answered, no payroll
run can be approved, because `approve_run()` refuses any run computed against
an unverified rate set.

| Dispute | Statute | What it blocks |
|---|---|---|
| `pf-admin-charge-scope` | PF | Whether the administrative-charge minimum is per employee or per establishment |
| `esi-contribution-period-edge` | ESI | Mid-period wage-crossing behaviour |
| `itax-fy2025-26-slabs` | Income tax | Slab boundaries, standard deduction, s.87A threshold and cap |
| `mh-pt-women-exemption-threshold` | Professional Tax | Maharashtra women's exemption threshold and scope |

`tests/statutory/test_disputes.py::test_no_unresolved_statutory_disputes`
**fails, and must keep failing** until each is answered against its gazette
source and recorded in `disputes.yaml`. It is the release gate. Do not skip it,
mark it xfail, or edit a fixture to make it pass.

All six rate sets are currently `DRAFT`. Verification is a human act performed
by the Finance Head against the source; the seeder cannot produce a verified
row, deliberately.

**Consequence:** the system can compute and display payroll. It cannot pay
anyone. That is the intended behaviour, not a defect.

---

## 2. Deployment infrastructure — BLOCKING, and absent

Phase 0 of the approved plan called for a Docker dev environment, CI, and a
staging environment. None was built. The application has only ever run from a
local virtualenv against a local PostgreSQL.

| Requirement | Status |
|---|---|
| Dependency manifest | **Added at Phase 12** — `requirements.txt` + `requirements.lock.txt` (108 packages) |
| Dockerfile | **Missing** |
| docker-compose | **Missing** |
| CI pipeline | **Missing** — no `.github/`, no config of any kind |
| Staging environment | **Missing** |
| Version control history | **Missing** — the git repository has **0 commits and 0 tracked files** |
| Backup / restore procedure | **Missing** — `.backups/` holds one stale dump from an abandoned Prisma attempt |
| Rollback procedure | **Impossible** — follows from no commits and no images |

### Why the version-control gap is the most serious of these

Everything else can be written in a day. With no commit history there is no
diff to review, no tag to roll back to, and no record of how the system reached
its current state. **Rollback is not "not yet configured" — it is unavailable.**

This should be fixed before anything else: initialise, commit, and tag.

### What IS ready

- `config/settings/prod.py` refuses to boot on misconfiguration, and sets
  HTTPS redirect, HSTS (1 year, preload), secure cookies, nosniff, and
  `X-Frame-Options: DENY`
- Uploads go to private S3 with 15-minute signed URLs; nothing user-supplied
  touches application disk
- `.env.example` documents every variable, including the warning that losing
  `FIELD_ENCRYPTION_KEY` makes encrypted PII permanently unreadable
- `.gitignore` excludes `.env`, `.env.*`, and `secrets/`
- `/api/v1/health/` performs a real database round-trip and returns 503 when
  the database is unreachable — not merely "the process is up"
- Migrations are consistent: `makemigrations --check` reports no drift
- Sentry is wired with `send_default_pii=False`

### Deployment constraint that must not be forgotten

The VPS runs ~10 unrelated services behind a **shared Caddy proxy** which owns
ports 80/443. `deploy/Caddyfile` and `deploy/docker-compose.yml` are
hand-maintained **on the server**. Overwriting them took seven unrelated
production domains down on 2026-08-11. Any deployment must add its own compose
project and one hand-added route block — never replace the shared files.

---

## 3. Attendance and leave — ABSENT, and payroll depends on it

**Neither module is built.** `apps/attendance` and `apps/leave` are one-line
model stubs. This is stated plainly here because payroll silently depends on
it.

`payroll.services.runs.paid_and_lop_days()` is the single seam. It tries to
import `attendance.services.monthly_summary` and `leave.services.get_lop_days`;
when the import fails it **assumes a full month with zero loss of pay for every
employee**, and records that assumption as a warning on the run:

> Attendance and leave are not yet integrated, so every employee was paid for a
> full month with no loss of pay. Verify before approving.

**What this means in practice:** until attendance and leave exist, every
payslip is computed as though the employee worked every day of the period.
Unpaid leave, part-month joiners and part-month leavers will all be **overpaid**
unless someone adjusts them by hand.

The assumption is deliberately confined to one function and surfaced on the run
rather than hidden. It is not a substitute for the modules.

---

## Cutover gates

| Gate | Result |
|---|---|
| RBAC matrix verified against implementation, all 18 roles | **PASS** (50 tests) |
| CEO write-walker over every route | **PASS** (66 routes, 0 writes permitted) |
| Security: auth, JWT, rotation, IDOR, escalation, PII, injection | **PASS** (34 tests) |
| Scope isolation: department, team, self | **PASS** (13 tests) |
| End-to-end, both pipelines | **PASS** (13 tests) |
| Statutory compliance | **FAIL — 4 disputes open (intended)** |
| Deployment infrastructure | **FAIL — absent** |
| Version control / rollback | **FAIL — 0 commits** |

**Assessment: not production-ready.** The application logic and its access
controls are in good order and independently verified. What is missing is the
compliance sign-off and the entire deployment substrate.
