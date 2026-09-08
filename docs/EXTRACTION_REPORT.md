# Productization: Extraction Report

How this generic HRMS product was extracted from its first deployment
(a private healthcare group's HRMS), what the audit found, what changed,
and what deliberately did not.

* Source: the original deployment's repository, working tree at commit `487a212`
* Product: this repository — fresh git history, **no environment files, no
  media, no credentials, no database content, no production data of any kind**
* The original application was **not modified** at any point during extraction.

---

## 1. The audit

Every module was inspected for company-specific dependence: backend apps,
frontend features, models, migrations, seeds, fixtures, tests, settings,
deploy configuration and docs, searched for the first company's name, domains,
emails, server IPs, device endpoints, credentials and hard-coded IDs.

**Finding: the architecture was already generic.** The original codebase kept
identity, structure and policy in the database (an `OrgSettings` row with
name, legal name, logo and signatory; departments, designations, locations,
levels, roles, permissions, leave policy, shift rules, holiday calendars,
salary components and statutory rate sets all as editable rows) and all
secrets/endpoints in `.env`. Real credentials were never in the repository.
There is no multi-tenancy, and no hard-coded record IDs (`company_id = 1`
style) anywhere — all relationships are proper foreign keys.

The company-specific residue was small and concentrated:

| Class | Instances found |
|---|---|
| Branding compiled into the frontend | 6 call-sites: sidebar (×2), login page wordmark, public application page, handbook constant, one input placeholder |
| No identity API | The frontend had no way to ASK whose system it is |
| Seeds/wording | UAT seed hard-coded the first company's name + email domain; leave seed's docstring cited the company handbook; a dev seed's org name |
| Docs | Deployment guide referencing the first deployment's server, proxy and domains; guides using its URLs; a generated HTML copy |
| Tests | Four fixture strings using company-flavoured names/addresses |
| Cosmetic | Example employee codes in two comments |

## 2. What was built or changed

* **`GET /api/v1/org/branding/`** — new, public, returns `{name, legal_name,
  logo}` from Organisation → Settings. Deliberately the *only* identity
  source: added to the security suite's deliberate-public allowlist with the
  rationale, and covered by its own tests (public, neutral fallback, exposes
  nothing else).
* **Frontend** — new `useBranding()` hook; login screen, sidebar, footer,
  public application page and handbook all render the configured name/logo,
  with a neutral "HRMS" fallback before setup. Zero company strings remain in
  `apps/web`.
* **`seed_demo_company`** (replaces the UAT seed) — a parameterized, fully
  fictional organisation: `--company` (default *Demo Healthcare Pvt Ltd*),
  `--legal-name`, `--domain` (default `demo-healthcare.example`, an RFC 2606
  reserved TLD). One account per template role, strong unique passwords,
  credentials written under `MEDIA_ROOT`, sign-in URL from `FRONTEND_URL`,
  clean `--remove`.
* **`.env.example`** — now documents every variable the settings read
  (verified by diffing the settings module against the template): HR mailbox,
  Google integrations, payroll switches, URLs, tuning.
* **Docs** — README rewritten as the product's; `NEW_COMPANY_SETUP.md`
  (20-step, no-source-edits company onboarding); `CONFIGURATION.md` (every
  configurable thing mapped to its home); this report. Old docs scrubbed of
  the first deployment's domains and ops specifics.
* **`scripts/verify_fresh_install.py`** — idempotent proof that a fresh
  database + seeds + bootstrap is a working HRMS wearing the demo company's
  identity (17 live checks; see §5).

## 3. What became configuration (and already was)

Company name/legal name/logo/signatory, statutory registrations, financial
year, currency, timezone, employee-code scheme — Organisation → Settings.
Departments (with functional kind), designations, teams, locations (with
state, which drives Professional Tax), levels (carrying the authority layer) —
Organisation. Roles and per-resource/per-action permission scopes — runtime
rows, Admin-managed; the 18 shipped roles are a template (verified live: a
brand-new role created through the API with no code). Leave types, policies,
holidays, weekly offs; per-location shift rules, grace, late allowance,
half-day threshold; eSSL devices and per-site employee device IDs; salary
components, per-employee structures and statutory enrolment; statutory rate
sets (fixtures arrive as **drafts**; each company's Finance must certify them
in-app before payroll can be approved); hiring workflows; onboarding
checklists, document types, letter templates. SMTP, HR mailbox, eSSL
endpoint/credentials, Google integrations, both behaviour switches — `.env`.

## 4. What stays in code, deliberately

The product's control design, identical for every company: the five-layer
authority model (names are configurable; the layer number is not), the
segregation-of-duties invariants (hiring and paying disjoint; a run's
preparer can never approve it), the onboarding gate, audit-everything,
404-for-out-of-scope, credentials-never-stored, and the *shape* of Indian
statutory law (PF wage basis, ESI thresholds, PT as a state levy, TDS
regimes) — whose *rates* are data.

## 5. Test evidence

On a **fresh database** (`migrate → seed_roles → seed_leave → seed_statutory
→ seed_onboarding → seed_workflows → seed_offboarding → bootstrap_admin →
seed_demo_company`):

* **Backend suite: passes** (≈1,240 tests) with one deliberate exception —
  the statutory-disputes guard, which is designed to fail until a company's
  finance function answers four documented rate-interpretation questions.
* **Frontend: type-check clean; 251/251 tests pass.**
* **`verify_fresh_install.py`: 17/17** — branding says *Demo Healthcare Pvt
  Ltd* to an anonymous caller; all 18 demo accounts sign in; HR creates an
  employee; starter leave policy present; Finance creates a salary component
  and sees 7 draft rate sets, none certified; Admin creates a new role at
  runtime; an employee reaches only their own data; and a repository-wide
  grep for the first company's name under `apps/` returns nothing.

## 6. Remaining limitations (honest list)

1. **Statutory payroll is India-only.** PF/ESI/PT/TDS semantics are code.
   Deploying outside India means using payroll without the statutory engine
   or porting new law. Rates are data; the law's shape is not.
2. **Department "kinds"** (medical / operations / hr / finance) are an enum
   used to classify departments and fit the *template* roles to them. Custom
   departments pick the nearest kind, and custom roles are kind-agnostic; a
   company outside healthcare simply never creates a medical-kind department.
   Making kinds fully user-defined would touch workflow routing and was
   deferred as not required for reuse.
3. **The employee handbook body** is starter prose in the frontend
   (`HandbookPage.tsx`); the company name in it is live branding, but
   replacing the policy text is an edit to one file (or hide the page).
   A CMS-style editable handbook was out of scope.
4. **Template role display-labels** live in one frontend map
   (`ROLE_LABELS`); custom roles are auto-humanised from their code. Renaming
   a template role in the DB changes it everywhere server-rendered; the
   sidebar chip label for those 18 codes comes from the map.
5. **Email/notification templates** are code (well-factored, one module),
   not database-editable templates.
6. **Migration heritage**: Django migration files carry the original app's
   development history (schema only — no data, no company references beyond
   what migrations normally contain).
