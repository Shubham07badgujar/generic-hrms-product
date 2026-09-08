# Setting Up a New Company

This takes one fresh deployment from nothing to a working HRMS for a new
company, **without editing source code at any step**. Everything
company-specific is either an environment variable (secrets, endpoints) or a
database record you create through the app (structure, policy, branding).

Time budget: an hour for the infrastructure, then as long as your HR takes to
enter the org structure.

---

## Part 1 — Infrastructure (done once per company, by an operator)

### 1. Install

```bash
git clone <this repository> company-a-hrms && cd company-a-hrms
```

Each company gets its **own clone, own database, own `.env`, own domain**.
Nothing is shared between deployments.

### 2. Configure the database

Create an empty PostgreSQL 16 database and a user that owns it. Nothing else
touches this database.

### 3. Configure environment variables

```bash
cd apps/api && cp .env.example .env
```

Fill in every value the file names. The critical ones:

| Variable | What it is |
|---|---|
| `SECRET_KEY`, `JWT_SIGNING_KEY` | Fresh random values per company — the file shows the generation commands. Never reuse across companies. |
| `FIELD_ENCRYPTION_KEY` | Encrypts PAN/Aadhaar/bank numbers at rest. **Back it up outside the database**; losing it makes that data permanently unreadable. |
| `DATABASE_URL` | This company's database. |
| `FRONTEND_URL` | The https URL employees will use. Appears in every email link. |
| `EMAIL_*` | The company's SMTP. Use a mailbox on a domain whose SPF, DKIM and DMARC are set up, or credential emails will land in spam. |
| `ADMIN_BOOTSTRAP_TOKEN` | A one-time token for step 6. **Remove it after bootstrap** — while unset, the bootstrap URL does not exist. |

Integrations (eSSL biometric devices, Google Forms/Calendar) stay off until
configured — the system runs fully without them.

### 4. Start the application

```bash
cd deploy && docker compose up -d --build
```

(or run api + web + worker directly as in the README's Quick start).
Migrations run automatically on API start. Then seed the role templates:

```bash
docker compose exec api python manage.py seed_roles       # role templates + permission matrix
docker compose exec api python manage.py seed_leave       # starter leave types & policy
docker compose exec api python manage.py seed_statutory   # Indian rate sets, as DRAFTS to certify
docker compose exec api python manage.py seed_onboarding  # document types, checklist, letters
docker compose exec api python manage.py seed_workflows   # example hiring pipelines
docker compose exec api python manage.py seed_offboarding # exit clearance template
```

Every one of these seeds **templates the company edits in the app** — nothing
about them is mandatory. After bootstrap (next step), verify the install:

```bash
docker compose exec api python - <<'PY'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()
exec(open("scripts/verify_fresh_install.py").read())
PY
```

### 5. (Optional) explore with a fictional company first

```bash
docker compose exec api python manage.py seed_demo_company
```

creates "Demo Healthcare Pvt Ltd" with one login per template role
(credentials written to `demo-credentials.txt` under the media directory).
When done: `manage.py seed_demo_company --remove`. Skip this entirely for a
real rollout.

### 6. Create the administrator

```bash
docker compose exec api python manage.py bootstrap_admin --email admin@company-a.example
```

Then **remove `ADMIN_BOOTSTRAP_TOKEN` from `.env`** and restart.

---

## Part 2 — Company configuration (done in the app, by the Admin)

Sign in as the administrator. Everything below is under **Organisation** in
the left menu unless noted.

### 7. Company identity

Organisation → **Settings**: display name, legal name, GSTIN/PAN/TAN/CIN,
EPF and ESI registration numbers, financial-year start month, currency,
timezone, employee-code prefix and starting number.

### 8. Upload the logo

Same screen: logo and authorised-signatory signature images. The logo appears
on the login screen, the app sidebar, the public job-application page, offer
letters and payslips — all from this one upload. The signatory appears on
generated letters.

### 9. Departments — 10. Designations — 11. Locations — 12. Levels

Create the company's own structure. Levels carry the authority **layer**
(1 Leadership … 5 Staff) that the permission scopes and reporting-manager
rules read; name them whatever the company likes.

### 13. Roles and permissions

Organisation → **Roles**. The seeded 18 roles are a starting template:
rename, deactivate, or ignore them and create your own. Each role carries
per-resource, per-action permission scopes (none / self / team / department /
organisation) editable here. Two invariants the product enforces regardless
of configuration: nobody both hires and releases pay, and payroll approval
always requires a second person.

### 14. Leave policy

Organisation → **Leave**: leave types (paid/unpaid), annual entitlement,
accrual, carry-forward, notice-period rules, the leave-year start, holiday
calendars per location, weekly-off days.

### 15. Attendance rules

Attendance → **eSSL / shift rules**: per-location working hours, full-day
hours, grace minutes, monthly late allowance, half-day threshold. If the
company uses eSSL biometric devices, set the `ESSL_*` variables in `.env`,
then register devices and map employee device-IDs (one per site an employee
works at). Leave `ATTENDANCE_AFFECTS_PAYROLL=false` until attendance data is
trusted; flip it deliberately.

### 16. Payroll

Payroll → **Settings**: salary components (earnings, and which count as PF
wage), statutory rate sets. Rate tables for PF/ESI/PT/income-tax ship as
fixtures; **Finance must review and certify each one in-app** — a run
computed on uncertified rates cannot be approved. Per-employee statutory
enrolment (PF/ESI/PT/TDS) is set on each salary structure.

### 17. Email

Already configured in `.env` (step 3). Optionally set the `HR_*` variables
for a separate HR mailbox for credential emails. Sender identity should match
a domain the company controls with SPF + DKIM + DMARC published.

### 18. Integrations

All optional, all env-driven: eSSL (`ESSL_*`), Google Forms application
intake (`GOOGLE_FORMS_*`), Calendar/Meet interview invites
(`GOOGLE_CALENDAR_*`). Each degrades gracefully when unset — recruitment
falls back to the built-in public application link, which needs nothing.

### 19. Administrator hygiene

Confirm the bootstrap token is removed (step 6), and that
`demo-credentials.txt` is deleted if the demo seed was ever run on this
instance.

### 20. Start adding employees

People → Employees → **Add employee**. Creating an employee atomically
creates their login, assigns their role, issues an onboarding checklist and
emails credentials to their personal address. From here the system runs
itself: onboarding → attendance → leave → payroll.

---

## Re-deploying for another company

Repeat this document with a new clone, new database, new `.env`, new domain.
No step above ever required editing a source file — if you find one that
does, that is a product bug; please report it.
