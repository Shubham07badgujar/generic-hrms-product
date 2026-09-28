# Generic HRMS

> **Multi-tenant. The backend conversion is complete; the frontend is not.**
> This branch (`saas/multi-tenant`) took the product from one-organisation-per-deployment to a
> multi-tenant SaaS platform. Every organization-owned table filters by organization at the
> manager, and the build refuses a model that does not. What remains is the employee-import
> slice and the frontend SaaS surface — setup wizard, plan page and platform console — whose
> APIs already exist. The decision record is
> [`docs/ARCHITECTURE.md` PART 13](docs/ARCHITECTURE.md), the audit is
> [`PRODUCTIZATION_AND_SAAS_AUDIT.md`](PRODUCTIZATION_AND_SAAS_AUDIT.md) (including what is
> still missing), and the single-organisation product is preserved at tag `pre-saas-baseline`.

A complete HRMS served as a **multi-tenant SaaS platform** — many companies on
one deployment and one database, with complete isolation between them —
covering recruitment, onboarding, attendance (biometric), leave, payroll with
Indian statutory computation, assets, offboarding, dashboards and a full audit
trail. It still runs as a single-company self-hosted install: that is the same
code path with one organization in it, not a separate mode.

```
React + TypeScript  →  Django + DRF  →  PostgreSQL
      Configurable RBAC · Org-Configured Policies · Audited Everything
```

**Nothing about any company is compiled in.** The company's name, logo,
departments, designations, locations, working hours, leave policy, salary
components, statutory rates, roles and permissions all live in the database
and are managed from the Organisation screens (the frontend reads its identity
from `GET /org/branding/`). Company A and Company B are two rows, provisioned
by a platform administrator — not two deployments.

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
dev/          Local Postgres for development (dedicated instance, port 5440)
docs/         Architecture, design and operations documents
render/       Render deployment: build, release and role-creation scripts
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

One company is enough to exercise the HR product and proves nothing about the
SaaS one. For that, seed three:

```bash
python manage.py seed_plans                 # what this deployment sells
python manage.py bootstrap_platform_admin   # the operator, so the console has a door
python manage.py seed_demo_platform         # three customers, three plans
```

| Company | Size | Plan | What it is for |
|---|---|---|---|
| `demo-healthcare` | 20 employees, 5 departments, 3 locations | Enterprise | Every module and one account per role — the company to test permissions, payroll and the hierarchy against. |
| `demo-technology` | 15 employees, 4 departments, 2 locations | Starter | A plan **without payroll**. Signing in as its Finance Head and finding no payroll is the point: entitlement is not permission. |
| `demo-retail` | 10 employees, 3 departments, 2 locations | Growth, trialing | A trial **days from expiry** — what a customer sees when somebody has to decide whether to buy. |

Each is created by the same atomic `provision_organization` call the platform
console makes: same validation, same configuration seeds, same first
administrator, same audit rows. Passwords are random per account and written to
a file inside that organization's own media subtree; every address is under
`.example`, which cannot resolve. `--remove` deletes a demo customer entirely,
keyed on its slug — never on an email domain, which is what once let two demo
companies delete each other's people.

Only healthcare promises an account for every role: fifteen people cannot hold
eighteen roles and ten certainly cannot, so the other two name the roles they
leave out rather than quietly missing them.

API: <http://localhost:8000> · OpenAPI schema: <http://localhost:8000/api/schema/swagger-ui/>

### Frontend

```bash
cd apps/web
npm install
npm run dev
```

App: <http://localhost:5173>

---

## What "multi-tenant" means here

One deployment, one database, many companies. Every organization-owned table
carries `organization_id` — all 94 of them — and the filter runs at the
**manager**, so a query written without `organization=` anywhere in it still
returns one customer's rows. That matters because two thirds of this codebase's
queryset call sites are in services, reached from Celery and management
commands as well as from HTTP, and a view-layer filter covers none of those.

Tenant identity is derived from the **authenticated principal** and never from
anything the client sends. With no organization bound, an organization-owned
query **raises** rather than returning an empty result: an empty queryset
inside a scheduled job is indistinguishable from "no work to do", and that is
how a silent leak survives. The deliberate escape is `Model.objects.all_orgs()`
— named and greppable, so `grep -rn all_orgs` is the audit of every place
somebody stepped outside tenancy on purpose.

A company is created by a platform administrator, who has **no access to any
customer's HR data**: they hold no role grant in any organization, so every
tenant queryset resolves to nothing. See
[`docs/NEW_COMPANY_SETUP.md`](docs/NEW_COMPANY_SETUP.md) for that flow and
[`docs/PLATFORM_ADMINISTRATION.md`](docs/PLATFORM_ADMINISTRATION.md) for the
boundary.

This is a rebuild of a mechanism that failed here once: a cross-tenant breach
in 2026 caused by tenant context that had to be *set* before it was *read*. The
proof that it is closed is generated, not asserted —
[`TENANT_ISOLATION_TEST_REPORT.md`](TENANT_ISOLATION_TEST_REPORT.md) is written
by the script that performs the attempts, and
[`PRODUCTIZATION_AND_SAAS_AUDIT.md`](PRODUCTIZATION_AND_SAAS_AUDIT.md) records
what the conversion found, including what is still missing.

## Starter templates, not assumptions

The seeds provide **templates a company edits or replaces** — none are wired
into the code as mandatory. Provisioning runs them for a new organization
automatically, from one list shared with the platform service so a real
customer cannot silently lack what a hand-seeded one gets. Run by hand, each
takes `--organization <slug>`: with exactly one organization the flag is
optional, and with several the command **refuses to guess** rather than seeding
the wrong customer's configuration, which `update_or_create` would report as
success either way.

| Seed | What it provides |
|---|---|
| `seed_roles` | 18 role templates across 5 authority layers with a reviewed permission matrix. Admins add, rename, deactivate roles and edit per-role permissions at runtime; custom roles need no code. |
| `seed_leave` | A starter leave policy (types, accrual, notice rules) to edit under Organisation → Leave. |
| `seed_demo_company` | One fictional company, built from a demo **profile** (`--profile healthcare|technology|retail`, default healthcare). Strong unique passwords, removable with `--remove`. Acts inside the named organization only: `--remove` takes that organization's members, not everybody who happens to share the email domain. |
| `seed_demo_platform` | All three demo customers at once, each **provisioned through the platform service** and each on a different plan — the command for testing the SaaS product rather than the HR one. See below. |
| `seed_demo` | DEBUG-only development fixture with known passwords and a populated recruitment pipeline. Refuses to run in production. |

Statutory payroll (PF, ESI, Professional Tax, income tax) targets **India**;
rate tables ship as fixtures that Finance must review and certify in-app
before a payroll run using them can be approved.

---

## Core architectural rules

These are load-bearing. Changing them requires updating `docs/ARCHITECTURE.md` first.

1. **Multi-tenant, failing closed.** *(Superseded the former "single organization; do not add
   an `organization` FK" rule on 2026-09-08 — see [`ARCHITECTURE.md` PART 13](docs/ARCHITECTURE.md).)*
   Every organization-owned table carries `organization_id`. Tenant identity is derived from the
   **authenticated principal**, never from middleware, a request parameter, or anything the client
   sends. Unbound organization context yields no rows and no writes — never unfiltered access.
   Tenancy is an outer predicate, **not** a new `Scope` value.
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
