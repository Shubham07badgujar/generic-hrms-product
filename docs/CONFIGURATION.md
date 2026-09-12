# What Is Configurable, and Where

The rule this product lives by: **identity, structure and policy live in the
database and are edited in the app; secrets and endpoints live in `.env`;
source code contains neither.** This page maps every kind of company-specific
thing to its home.

## In the database, edited in the app

| Thing | Where it is edited | Where it takes effect |
|---|---|---|
| Company display name, legal name | Organisation → Settings | Login screen, sidebar, public application page, emails, letters, payslips — all via `GET /org/branding/` and the letterhead renderer |
| Logo, signatory name + signature image | Organisation → Settings | Same surfaces as above |
| Statutory registrations (GSTIN, PAN, TAN, CIN, EPF, ESI numbers) | Organisation → Settings | Letters and payslips |
| Financial-year start, currency, timezone | Organisation → Settings | Payroll periods, formatting |
| Employee code prefix and next number | Organisation → Settings | Every new employee record |
| Departments (with functional kind), designations, teams | Organisation | Employee placement, recruitment, scoped visibility |
| Locations / branches (with state) | Organisation | Attendance shift rules, holiday calendars, Professional Tax (a state levy) |
| Levels (name + authority layer 1–5) | Organisation | Reporting-manager rules, creation authority |
| Roles and their permission scopes | Organisation → Roles | Everything — the entire RBAC reads these rows |
| Leave types, entitlements, accrual, notice rules | Organisation → Leave | Leave application and approval |
| Holiday calendars, weekly offs | Organisation → Leave | Attendance day resolution, leave spans |
| Shift rules per location (hours, grace, late allowance, half-day threshold) | Attendance → eSSL | Attendance computation |
| eSSL devices and employee device-ID mappings (one per site) | Attendance → eSSL | Punch resolution |
| Salary components and per-employee structures | Payroll | Payroll computation |
| Statutory rate sets (PF, ESI, PT, income tax) | Payroll → Settings | Deductions; Finance certifies each set in-app before use |
| Compensation packages | Payroll → Packages | Deferred-release schedules |
| Hiring workflows (stages, interviewer roles) | Recruitment → Workflows | The pipeline every application follows |
| Onboarding checklist templates, document types | Organisation | Every new joiner's gate |
| Letter templates | Organisation | Generated documents |
| Notification preferences | Settings → Notifications | Per-user delivery |
| Outbound mail server, sender address, reply-to | Organisation → Settings | Every email this organisation sends; unset fields fall back to the deployment's, field by field |
| Email wording overrides | Organisation (model in place; no screen yet) | Replaces one shipped message for this organisation only — see `docs/MESSAGING_ISOLATION.md` |

## In `.env` (secrets and endpoints — see `apps/api/.env.example`)

Since the multi-tenant conversion, the mail and eSSL groups below are
**deployment defaults, not the value**. `core.config` resolves each one from
the organisation's own row and falls back here field by field — never to
another organisation. A single-company self-hosted installation leaves those
tables empty and behaves exactly as this table describes.

| Group | Variables |
|---|---|
| Core secrets | `SECRET_KEY`, `JWT_SIGNING_KEY`, `FIELD_ENCRYPTION_KEY` |
| Infrastructure | `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`, S3/Sentry |
| Mail | `EMAIL_HOST/PORT/USER/PASSWORD/USE_TLS`, `DEFAULT_FROM_EMAIL`, optional `HR_*` mailbox for credential mail |
| Bootstrap | `ADMIN_BOOTSTRAP_TOKEN` (+ allowed IPs) — delete after first admin exists |
| eSSL biometric | `ESSL_INTEGRATION_ENABLED`, `ESSL_BASE_URL/USERNAME/PASSWORD/TIMEZONE/SYNC_INTERVAL` |
| Google (optional) | `GOOGLE_FORMS_*`, `GOOGLE_CALENDAR_*`, `GOOGLE_MEET_ENABLED` |
| Behaviour switches | `ATTENDANCE_AFFECTS_PAYROLL`, `PAYSLIP_DELETE_WINDOW_DAYS`, `CANDIDATE_RETENTION_MONTHS` |
| Fallbacks | `ORG_DISPLAY_NAME` — used only before the Organisation Settings row exists |

## In source code, deliberately

These are the product's opinions — the same for every company, because they
are control design rather than preference:

* The five-layer authority model (a layer number on levels/roles; the names on
  top of it are yours).
* Segregation of duties: hiring and paying are disjoint capabilities; a
  payroll run's preparer can never approve it; statutory rates must be
  certified by Finance before approval.
* The onboarding gate: mandatory documents approved before full access opens.
* Audit-everything; out-of-scope reads answer 404; credentials are never
  stored or shown.
* Indian statutory payroll semantics (PF wage basis, ESI thresholds, PT as a
  state levy, TDS regimes). The **rates** are data; the **law's shape** is code.

## Starter content shipped as data (edit or discard freely)

* 18 role templates with a reviewed permission matrix (`seed_roles`).
* A starter leave policy (`seed_leave`).
* Indian statutory rate-set fixtures — uncertified until Finance reviews them.
* An employee-handbook page whose company name is live branding; its body text
  is starter content to replace with the company's own policy
  (`apps/web/src/features/employees/HandbookPage.tsx`).
* Role display labels in the frontend (`ROLE_LABELS` in `AppShell.tsx`) —
  labels for the template roles only; custom roles are auto-humanised.

## The one identity endpoint

`GET /api/v1/org/branding/` (public) returns `{name, legal_name, logo}` from
Organisation → Settings. It is the only place the frontend learns whose
system it is; there is no compiled-in company anywhere in `apps/web`.
