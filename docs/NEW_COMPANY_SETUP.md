# Adding a Company to the Platform

This takes one company from nothing to a working HRMS, **without editing source
code at any step**. Everything company-specific is a database record created
through the app, or a per-organization configuration row — never a code change,
and no longer a separate deployment.

> **This document changed meaning on 2026-09-18.** It used to say: clone the
> repository again, create another database, write another `.env`, point
> another domain at it. That was correct while each deployment served one
> company. The product is now multi-tenant — many companies share one
> deployment and one database, isolated at the query layer — so adding a
> company is a **provisioning action**, not an installation. The install steps
> below still exist; they just happen once for the platform rather than once
> per customer.

Time budget: the deployment is an hour, once. A company after that is minutes
to provision, then as long as its HR takes to enter the org structure.

---

## Part 0 — The deployment (once, for the platform)

Skip this entirely if the platform is already running; adding the second
company does not repeat any of it.

### 1. Install

```bash
git clone <this repository> hrms-platform && cd hrms-platform
```

### 2. Database

Create an empty PostgreSQL 16 database and a user that owns it. **One
database serves every customer.** Isolation is enforced by the tenant
predicate at the manager, not by separate databases — see
[`../PRODUCTIZATION_AND_SAAS_AUDIT.md`](../PRODUCTIZATION_AND_SAAS_AUDIT.md) §5
for what enforces it and
[`../TENANT_ISOLATION_TEST_REPORT.md`](../TENANT_ISOLATION_TEST_REPORT.md) for
the evidence.

### 3. Environment variables

```bash
cd apps/api && cp .env.example .env
```

`.env` now carries **deployment** settings: the database, the secret key, the
allowed hosts, the frontend URL, and the platform's own SMTP for mail sent
before any customer exists. Per-company settings that used to live here — a
company's own SMTP and sender identity, its HR mailbox, its biometric device
endpoint and credentials — are configuration rows on the organization, resolved
per organization with these as the fallback. Never another organization's.

### 4. Start it

```bash
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_statutory      # India PF/ESI/PT tables
docker compose exec api python manage.py seed_plans          # the plans you sell
docker compose exec api python manage.py sync_beat_schedule  # scheduled jobs
```

`seed_statutory` is deliberately per-deployment, not per-customer: PF, ESI and
Professional Tax tables are facts about the Republic of India, and one verified
copy is the point. They arrive as **drafts** that each customer's Finance must
certify in-app before a payroll run using them can be approved.

### 5. Create the platform administrator

```bash
docker compose exec api python manage.py bootstrap_platform_admin --email ops@yourcompany.example
```

A platform administrator runs the SaaS: organizations, plans, subscriptions,
suspension and restoration. They hold **no role grant in any organization**, so
every customer queryset resolves to nothing and every customer route refuses
them. That is structural, not a policy — see
[`PLATFORM_ADMINISTRATION.md`](PLATFORM_ADMINISTRATION.md).

---

## Part 1 — Provision the company (platform administrator)

One atomic service call creates the organization, its settings, a trial
subscription, its full configuration (roles and the permission matrix, leave,
onboarding, offboarding, workflows, attendance), the administrator's account,
their membership and role grant, and the audit trail — then schedules the
invitation email after commit. **A failure at any step rolls the whole
organization back**, so a retry is clean and there is no half-provisioned
tenant to diagnose.

> **Known gap: there is no button or command for this yet.** The service is
> complete and tested, but the console lists organizations read-only and no
> management command wraps it, so today provisioning runs from a shell. A
> `POST` route or a command is the missing piece, not the flow.

```bash
docker compose exec api python manage.py shell -c "
from apps.platform.services.provisioning import provision_organization
result = provision_organization(
    name='Company A',
    slug='company-a',
    admin_email='admin@company-a.example',
    admin_first_name='Asha',
    admin_last_name='Rao',
    city='Pune',
    state='MH',
)
print(result.organization.slug, result.admin.email)
print('temporary password:', result.temporary_password)
"
```

The new organization starts at **`PENDING_SETUP`**, which is a *working* state,
not a locked one: the administrator can read and write, because they are about
to do the setup. The temporary password forces a change at first sign-in.

Hand the administrator their address and the sign-in URL. Do not send the
temporary password over the same channel as the address.

---

## Part 2 — The setup wizard (the company's administrator)

Sign in, change the password, and the app opens the setup wizard. Ten steps,
each writing to the real domain tables through endpoints that already exist:

| # | Step | Required to finish | Done when |
|---|---|---|---|
| 1 | Company profile | Yes | Legal name, address and statutory identifiers are recorded |
| 2 | Departments | Yes | At least one exists |
| 3 | Locations | Yes | At least one exists |
| 4 | Designations | Yes | At least one exists |
| 5 | Roles and permissions | Yes | The seeded role set is reviewed — rename, deactivate, or add your own |
| 6 | Leave policy | Yes | Types and accrual rules are in place |
| 7 | Attendance policy | Yes | Working hours and shift rules are set |
| 8 | Payroll and compliance | No | Salary components and statutory configuration are set |
| 9 | Email configuration | No | The company's sender identity is configured, or the platform's is accepted |
| 10 | Employees | No | At least one employee exists |

The last three are optional on purpose: a company can go live and start using
attendance and leave before payroll is configured, before it has replaced the
platform's sender identity, and before anybody is hired into it.

**Progress is computed, never stored.** Each step's completion is derived from
the real tables, so closing the browser loses nothing and deleting the last
department honestly reopens that step. There is no wizard state to go stale.

`POST /org/setup/finish/` is the single transition out of `PENDING_SETUP` to
`ACTIVE`. It refuses while a required step is unsatisfied and **names** the
steps that are missing rather than failing vaguely.

### Roles are yours

The seeded roles are **defaults, not system roles**. Rename them, deactivate
them, edit their permission scopes, or create entirely different ones — no code
path tests for a role code. A brand-new role works through the API with no
code change.

---

## Part 3 — Running it

Adding an employee atomically creates their login, assigns their role, issues
an onboarding checklist and emails credentials to their personal address. From
there the system runs itself: onboarding → attendance → leave → payroll.

Two things worth knowing on day one:

- **One login belongs to one company.** Email is the username and is unique
  platform-wide, so if an address already has an account anywhere on the
  platform, another company cannot invite it. That is a deliberate V1 clamp —
  see [`../PRODUCTIZATION_AND_SAAS_AUDIT.md`](../PRODUCTIZATION_AND_SAAS_AUDIT.md) §7.
- **Seats are enforced at creation**, under a row lock, and refuse with a
  message naming the limit rather than failing at some later step.

---

## Adding another company

Repeat **Part 1 only**. No new clone, no new database, no new `.env`, no new
domain, no downtime for the customers already running. If you find a step that
requires editing a source file, that is a product bug; please report it.

## Running a single company on your own hardware

Unchanged, and it is the same code path rather than a separate mode: install as
in Part 0, provision one organization, and every command that takes
`--organization` accepts being told nothing at all while exactly one exists.
With several, those commands **refuse to guess** — seeding or purging the wrong
customer reports success either way, so stopping is the only safe answer.
