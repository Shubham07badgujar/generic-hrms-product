# Generic HRMS

A complete, self-hosted HRMS for a **single organisation per deployment** —
recruitment, onboarding, attendance (biometric), leave, payroll with Indian
statutory computation, assets, offboarding, dashboards and a full audit trail.

```
React + TypeScript  →  Django + DRF  →  PostgreSQL
      Configurable RBAC · Org-Configured Policies · Audited Everything
```

**Nothing about any company is compiled in.** The company's name, logo,
departments, designations, locations, working hours, leave policy, salary
components, statutory rates, roles and permissions all live in the database
and are managed from the Organisation screens (the frontend reads its identity
from `GET /org/branding/`). Deploy the same code for Company A and Company B;
only the database and `.env` differ.

* **Setting up on a new PC (with test data):** [`docs/SETUP_ON_A_NEW_PC.md`](docs/SETUP_ON_A_NEW_PC.md)
* **Running it in VS Code:** [`docs/RUNNING_IN_VS_CODE.md`](docs/RUNNING_IN_VS_CODE.md)
* Architecture and database design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* **Setting up a new company:** [`docs/NEW_COMPANY_SETUP.md`](docs/NEW_COMPANY_SETUP.md)
* What is configurable, and where: [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)
* How this product was extracted from its first deployment:
  [`docs/EXTRACTION_REPORT.md`](docs/EXTRACTION_REPORT.md)

---

## Layout

```
apps/
  api/        Django 5 + DRF backend
  web/        React 18 + TypeScript (Vite) frontend
deploy/       docker-compose stack (api, web, worker, beat, postgres, redis)
docs/         Architecture, design and operations documents
scripts/      Dev and ops helper scripts
```

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.12 |
| PostgreSQL | 16 |
| Node.js | 20+ |

---

## Quick start (development)

### Backend

```bash
cd apps/api
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements/dev.txt
cp .env.example .env          # then fill in the values it names
python manage.py migrate
python manage.py seed_all         # every seed + a demo company full of test data
python manage.py runserver
```

API: <http://localhost:8000> · OpenAPI schema: <http://localhost:8000/api/schema/swagger-ui/>

### Frontend

```bash
cd apps/web
npm install
npm run dev
```

App: <http://localhost:5173>

---

## What "single organisation" means

Each deployment serves ONE company: its own database, its own `.env`, its own
domain. There is deliberately no multi-tenancy — no `organization_id` on every
row, no tenant switching, none of the failure modes that come with them. To
serve three companies, run the stack three times.
[`docs/NEW_COMPANY_SETUP.md`](docs/NEW_COMPANY_SETUP.md) takes a fresh
deployment from empty database to first employee **without touching source
code**.

## Starter templates, not assumptions

The seeds provide **templates a company edits or replaces** — none are wired
into the code as mandatory:

| Seed | What it provides |
|---|---|
| `seed_roles` | 18 role templates across 5 authority layers with a reviewed permission matrix. Admins add, rename, deactivate roles and edit per-role permissions at runtime; custom roles need no code. |
| `seed_leave` | A starter leave policy (types, accrual, notice rules) to edit under Organisation → Leave. |
| `seed_demo_company` | A fully fictional company — "Demo Healthcare Pvt Ltd" by default; `--company/--legal-name/--domain` to change — one account per template role, strong unique passwords, removable with `--remove`. |
| `seed_demo` | DEBUG-only development fixture with known passwords and a populated recruitment pipeline. Refuses to run in production. |

Statutory payroll (PF, ESI, Professional Tax, income tax) targets **India**;
rate tables ship as fixtures that Finance must review and certify in-app
before a payroll run using them can be approved.

---

## Core architectural rules

These are load-bearing. Changing them requires updating `docs/ARCHITECTURE.md` first.

1. **Single organization.** There is no tenant/organization scoping anywhere. Do not add an `organization` FK.
2. **Authorization returns a scope, not a boolean.** `Scope.NONE(0) < SELF(1) < TEAM(2) < DEPARTMENT(3) < ALL(4)`.
3. **`scope_queryset()` is the only sanctioned path to a scoped queryset.** Never hand-roll a scope filter.
4. **Business logic lives in `services.py`** — never in views or serializers.
5. **Every API view declares `access_resource`.** `manage.py check` fails the build otherwise.
6. **Out-of-scope object reads return 404, not 403** — existence must not leak.
7. **CEO is read-only**, enforced four independent ways. Never rely on a single check.
8. **Only HR Head performs final candidate rejection.** Department Heads recommend; Admin overrides via a separate audited action.
9. **No credential is ever stored or returned.** Not in the database, not in any API response.
10. **Money is `Decimal`, never float.** All currency INR, rounded half-up.
11. **No company identity in source.** Name, logo and policy come from the database; secrets and endpoints from `.env`. A grep for any real company's name in `apps/` must come back empty.

---

## Testing

```bash
cd apps/api
pytest                        # full suite
pytest -m golden              # payroll statutory golden master (compliance oracle)
pytest -m rbac                # permission matrix
```

The payroll golden-master suite is the **correctness oracle for Indian
statutory compliance** (PF, ESI, Professional Tax, Gratuity, TDS). It must
pass unchanged.
