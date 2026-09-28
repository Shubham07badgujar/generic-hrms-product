# Database-level tenant hardening — plan, and what was built in development

> **Status 2026-09-22:** implemented and tested on Generic HRMS's **dedicated
> development instance only** (`dev/postgres/`, 127.0.0.1:5440). Not deployed
> anywhere; no production exists. Where the build differs from the proposal
> below, §8 says how and why. Role names are `generic_hrms_owner /
> generic_hrms_app / generic_hrms_platform`, not the `hrms_*` names drafted
> below (those collided with roles on the shared cluster).

> **Scope rule (2026-09-22).** Generic HRMS is a separate project from the
> LIVE `hrms.drjoshis.in`. Nothing in this plan may touch that system — its
> server, database, roles, `.env`, services or deployment. Generic HRMS has no
> production environment yet; every step below is for Generic HRMS's own
> development/test database only. Where earlier drafts said "VPS" or
> "production", they were wrong: that VPS is the live HRMS's.

Sections 0–7 are the proposal as approved; §8 is what was built. It is defence in depth beneath the
existing application isolation (tenant manager, `scope_queryset`,
`RBACPermission`, the system checks), which stays exactly as it is.

Measured on the development database, 2026-09-22.

---

## 0. The finding that shapes everything: the app role owns every table

| Fact | Value |
|---|---|
| Runtime role | `hrms` — not superuser, not BYPASSRLS |
| Owner of all 120 `public` tables | `hrms` |
| Tables with RLS enabled | 0 |
| `hrms` privileges on `audit_auditlog` | SELECT, INSERT, UPDATE, **DELETE, TRUNCATE**, REFERENCES, TRIGGER |
| `CONN_MAX_AGE` / `ATOMIC_REQUESTS` | 60 s / False |
| Connection pooler | none (no PgBouncer) |

A table **owner** is exempt from RLS unless the table is `FORCE`d, can
`GRANT` back anything `REVOKE`d from it, and can `DROP`/`ALTER` any policy,
trigger or constraint. So while the application connects as the owner, RLS
and audit revocations are theatre. **Step 1 is a role split, and it is a
deployment change:**

| Role | Owns tables | Used by | RLS | Audit DELETE/TRUNCATE |
|---|---|---|---|---|
| `hrms_owner` (today's `hrms`, renamed or kept) | yes | `manage.py migrate` only (entrypoint), break-glass | exempt (owner, not FORCEd) | yes (schema changes need it) |
| `hrms_app` (new) | **nothing** | Django web, Celery workers, management commands at runtime | **subject to it** | **no** |
| `hrms_platform` (new, `NOLOGIN`, `BYPASSRLS`) | nothing | entered with `SET LOCAL ROLE` inside one named context manager only | bypass | no |

Requires two connection settings (`DATABASE_URL` → `hrms_app`,
`DATABASE_MIGRATE_URL` → `hrms_owner`), `entrypoint.sh` to migrate with the
owner URL, and a one-time grant script on each environment. `deploy/Caddyfile`
and `deploy/docker-compose.yml` are **not** touched (shared proxy — see memory
rule); only the app's own env and entrypoint change.

---

## 1. Table classification (all 120)

| Class | Count | Tables | Composite FK | RLS |
|---|---|---|---|---|
| Tenant-owned (`OrgOwnedModel`) | 95 | Appendix C | as source and/or target | **yes** — `organization_id = app.org_id` |
| Tenant-owned, organization-level | 3 | `organization_orgsettings`, `organization_orgemailconfig`, `organization_organizationmembership` | — | **yes**; membership gets an extra `user_id = app.user_id` arm, because it is the table that *resolves* the organization |
| Global (deliberate, `TENANT_EXEMPT`) | 4 | `organization_organization`, `accounts_user`, `platform_plan`, `statutory_statutoryruleset` | — | **no** — they are shared by design; RLS would break login, plan lookup and national rate sets |
| Platform-owned | 1 | `platform_subscription` | — | **yes, platform-only**: readable by `app.org_id` owner (the plan page) and by the platform role |
| Audit / system | 1 | `audit_auditlog` | optional (`subject_employee_id`) | **yes** — `organization_id = app.org_id` (NULL rows are platform events: invisible to tenants, as today) |
| Framework | 17 | auth_*, django_*, celery beat, token_blacklist, sessions, admin log | — | **no** — no tenant data |

---

## 2. Composite foreign keys

**Scope (measured):** 151 FKs from an organization-owned table to another,
across 75 source tables, pointing at 41 target tables (Appendix A/B). 63 are
nullable. **Existing cross-organization rows on dev: 0.** A future Generic HRMS production database (not yet chosen) must be
checked with the same query before applying (step 5.1).

**Mechanism, one migration app (`apps/dbguard`), pure `RunSQL`:**

1. For each of the 41 targets:
   `ALTER TABLE t ADD CONSTRAINT uq_<t>_id_org UNIQUE (id, organization_id);`
2. For each of the 151 edges:
   `ALTER TABLE s ADD CONSTRAINT fkorg_<s>_<col> FOREIGN KEY (<col>, organization_id) REFERENCES t (id, organization_id) DEFERRABLE INITIALLY DEFERRED NOT VALID;`
   then, in a second migration, `ALTER TABLE s VALIDATE CONSTRAINT …;`
   (`NOT VALID` + separate `VALIDATE` keeps the lock short on large tables).
3. The existing single-column Django FKs **stay**; the composite constraint is
   added beside them. Deferred like every other FK here, so purge's
   delete-in-any-order transaction still commits.
4. A system check (new `access.E018`) derived from the model registry fails the
   build when an org→org FK exists without its composite constraint — so a
   model added next year is covered or the build is red.

**Reversible:** each constraint has a `DROP CONSTRAINT` reverse.

---

## 3. Row-level security

### 3.1 The session variables

| GUC | Set when | Cleared when |
|---|---|---|
| `app.user_id` | JWT authentication resolves a user | request end, `acting_as` exit |
| `app.org_id` | organization bound (the four existing binding points below) | request end, `acting_as` exit |

Set with `set_config(name, value, false)` on the **same four code paths that
already bind `_current_org`** — `RequestContextMiddleware`, the JWT
authenticator's `_bind_organization`, `acting_as`, `set_current_org_id` — via
one function, so the database and the application cannot disagree about the
bound organization. Every request **starts by clearing both** (a reused
connection from `CONN_MAX_AGE` must never carry the last request's
organization — this is the incident's shape, and the clear-first rule is its
answer). Unset ⇒ `current_setting('app.org_id', true)` is NULL ⇒ every
tenant policy matches **no rows**: fail closed.

### 3.2 Policy (per tenant table)

```sql
ALTER TABLE t ENABLE ROW LEVEL SECURITY;          -- not FORCE: the owner (migrations) stays exempt
CREATE POLICY tenant_isolation ON t
  USING      (organization_id = nullif(current_setting('app.org_id', true), '')::uuid)
  WITH CHECK (organization_id = nullif(current_setting('app.org_id', true), '')::uuid);
```

`USING` stops reads/updates/deletes of other organizations' rows; `WITH
CHECK` stops writing a row into another organization.

### 3.3 The six paths, kept separate

| Path | How it reaches data | Can it cross organizations? |
|---|---|---|
| **Normal tenant request** | `hrms_app`, `app.org_id` = the principal's own organization (derived from membership, never from the request) | **No.** No code path in a tenant request sets the platform role; a test asserts it |
| **Platform/admin operations** (console lists, headcount, provisioning, purge, support-grant bookkeeping) | `with platform_bypass(reason=…)`: `SET LOCAL ROLE hrms_platform` inside a transaction, only callable when the principal `is_platform_admin` or from a management command/platform task; logs every entry | Yes — deliberately, named, greppable, audited. The 16 `all_orgs()` sites map onto this |
| **Background jobs** | Per-organization fan-out subtasks already bind the organization → `app.org_id` set per task. Deployment-wide sweeps (`purge_staging_pii`) use `platform_bypass` | Only the named sweeps |
| **Migrations** | `hrms_owner`, which owns the tables and is exempt (RLS enabled, not forced) | Yes — schema work must be able to |
| **Deliberately global tables** | No RLS (§1) | n/a — shared by design |
| **Support Access** | Grant row read via `platform_bypass`; the configuration snapshot then binds `app.org_id` **from the grant row** and reads under normal RLS | Only the one granted organization, only the fixed manifest |

**What the platform bypass is and is not.** It defends against *accidental*
omission — a query that forgets its filter still sees one organization. It is
not a defence against malicious code already executing inside the app process,
which could `SET ROLE` itself; the application layer and code review remain
the control for that. It is stated plainly so it is not over-claimed.

### 3.4 Order of adoption

RLS goes on in two releases: first `ENABLE` + policies with **the runtime
still connecting as the owner** (so nothing changes behaviour, but the policies
exist and are tested against a non-owner role in CI), then flip runtime to
`hrms_app`. The flip is the moment of risk and is a single env change to roll
back.

---

## 4. Audit-table hardening

- `hrms_app`: `GRANT SELECT, INSERT, UPDATE ON audit_auditlog`; **no DELETE, no
  TRUNCATE**. UPDATE is kept because purge scrubs payloads
  (`apps/audit/purge.py`) and user deletion sets `actor_id` NULL.
- A `BEFORE UPDATE` trigger makes UPDATE **scrub-only**: identity columns
  (`organization_id, action, resource, entity_type, entity_id, occurred_at,
  request_id`) may not change; payload columns (`before, after, metadata,
  entity_label, reason, ip, user_agent, actor_email, subject_employee_id,
  actor_id`) may only change **to NULL/empty**. So even the permitted UPDATE
  cannot rewrite history, only erase personal content.
- `BEFORE DELETE` and `BEFORE TRUNCATE` triggers raise, as a second mechanism
  that also catches the owner by accident (the owner could drop them
  deliberately; that is a migration, reviewed).
- Tenant admins, platform operators and Celery all run as `hrms_app`, so none
  can DELETE or rewrite; tests assert each.

---

## 5. Migration and rollout steps

1. **Pre-flight on each environment** (read-only): the cross-org query from §2
   on all 151 edges; `SELECT` of any audit row a trigger would reject; row
   counts of the 41 targets (index build time).
2. Migration `dbguard.0001`: 41 unique constraints + 151 composite FKs `NOT VALID`.
3. Migration `dbguard.0002`: `VALIDATE` all 151.
4. Migration `dbguard.0003`: audit triggers (scrub-only UPDATE, no DELETE/TRUNCATE).
5. Migration `dbguard.0004`: RLS `ENABLE` + policies on 99 tables (95 + 3 + audit), subscription policy.
6. Out-of-band SQL script (not a migration — roles are cluster objects): create
   `hrms_app`, `hrms_platform`; grants; `REVOKE DELETE, TRUNCATE ON audit_auditlog FROM hrms_app`.
7. Code: the GUC sync function at the four binding points; `platform_bypass`;
   the `all_orgs()` sites routed through it; `access.E018`.
8. Flip runtime env to `hrms_app`.

Each migration has a reverse; step 8 is reverted by pointing `DATABASE_URL` back.

---

## 6. Verification (all executed, not claimed)

- Fresh database: `migrate` from zero (the pytest DB), full suite.
- Existing database: `migrate` on a copy of the dev DB with data.
- New tests, run as a **non-owner role** (a test-only `hrms_app_test`), with
  **real ids from another organization** from the tenancy builder:
  - composite FK: raw `INSERT`/`UPDATE` pointing a row at another org's parent → `IntegrityError`, per edge class;
  - RLS read: `Model.objects.all_orgs()` (the filter deliberately omitted) under org A returns zero of B's rows, for every tenant table;
  - RLS write: raw `UPDATE`/`DELETE … WHERE id = <B's id>` under A affects 0 rows; `INSERT` with B's organization_id fails `WITH CHECK`;
  - no GUC set → zero rows everywhere (fail closed);
  - `Scope.ALL` admin, CEO, platform operator, a Celery fan-out subtask, a Support Access snapshot — each confined as §3.3 says;
  - audit: DELETE, TRUNCATE, identity-column UPDATE all refused for `hrms_app`; scrub UPDATE allowed;
- Existing: tenancy suite, route walker, isolation report (310 checks), full suite.

---

## 7. Decisions needed from you

1. **Role split** (§0) — required for RLS and audit hardening to mean anything.
   Development only, on a database and roles dedicated to Generic HRMS
   (see "Database separation" below) — never the live HRMS's.
2. **Composite FKs** (§2) — safe to apply first and independently; no
   behaviour change unless a cross-org write already exists.
3. **RLS rollout in two releases** (§3.4) vs one.
4. **Audit scrub-only UPDATE trigger** (§4) vs a plain `REVOKE DELETE, TRUNCATE`.
5. **Production pre-flight** (§5.1) — deferred: Generic HRMS has no production
   environment yet. The read-only preflight SQL will be written and shown, and
   run only against a Generic HRMS production database once one exists and you
   approve. It is never run against the live HRMS.

### Database separation (blocker found 2026-09-22)

Generic HRMS's dev database `generic_hrms` shares a local Postgres cluster
(127.0.0.1:5432) with databases `hrms`, `hrms_dev` and `hrms_test`, and
connects as role `hrms`, which also owns the `hrms` database; `hrms_app` owns
`hrms_dev`/`hrms_test`. Those look like the existing HRMS project's local
databases and roles. Generic HRMS must get its own database and roles before
any role, grant or RLS work proceeds.

**Resolved:** Generic HRMS now has its own Postgres 16 container
(`dev/postgres/docker-compose.yml`, 127.0.0.1:5440, volume
`generic_hrms_pgdata`). The shared 5432 cluster, its old `generic_hrms`
database and all its roles were left untouched.

---

## 8. As implemented (development instance only)

| Piece | Where | Differs from the proposal |
|---|---|---|
| Roles | `dev/postgres/init/01_roles.sh` (container first-boot) | `generic_hrms_app` is NOINHERIT and a member of `generic_hrms_platform`, so it gains BYPASSRLS **only** via `SET LOCAL ROLE`. The owner is the container's bootstrap superuser (dev only). |
| Unique keys, composite FKs, validate | `apps/dbguard/migrations/0001–0003` | As proposed. 41 keys, 151 FKs, DEFERRABLE INITIALLY DEFERRED like every Django FK. |
| RLS | `0004_rls_policies` | 100 tables (95 org-owned + OrgSettings + OrgEmailConfig + membership + audit + subscription), `ENABLE` not `FORCE`, 102 policies. Membership also admits the user's own rows by `app.user_id`; audit allows org-less INSERTs; subscription is read-only to the runtime role. |
| Audit guards | `0005_audit_guards` | Scrub-only UPDATE trigger as proposed. **No DELETE/TRUNCATE trigger:** the test harness TRUNCATEs as owner, so deletion is refused by privilege (0006) rather than trigger. |
| Grants | `0006_runtime_grants` (new) | A migration rather than an out-of-band script, but conditional: it grants only if the roles exist and otherwise logs a NOTICE, so `migrate` still works on a database without them. |
| Session sync | `core/db_context.py` | Not four call sites: one execute wrapper that reads the same ContextVars just before each statement and sends `set_config` only when the bound (org, user) changed **or** a transaction/savepoint it was sent in has since ended (set_config is transactional, so a rollback reverts it). |
| Bypass | `core/access/platform_bypass.py` | As proposed; on exit restores the role that was active before, not the login role. Support Access's grant lookup routed through it; the remaining `all_orgs()` call sites are release-2 work. |
| Check | `access.E018` in `core/access/checks.py` | As proposed. |

Runtime still connects as the owner (release 1), so the application's
behaviour is unchanged; the RLS and audit tests switch their own transaction to
`generic_hrms_app` with `SET LOCAL ROLE`. Tests:
`tests/tenancy/test_db_hardening.py`, `tests/tenancy/test_db_audit_guards.py`.

---

## Appendix A — the 151 organization-aware foreign keys

Every FK from an organization-owned table to another. Each gets a composite
`(fk_column, organization_id) REFERENCES target (id, organization_id)` constraint.
`null` = nullable FK (MATCH SIMPLE: a NULL skips the check, as it should).

| # | Source table | Column | Target table | Nullable |
|---|---|---|---|---|
| 1 | `accounts_rolepermission` | `role_id` | `accounts_role` |  |
| 2 | `accounts_userrole` | `role_id` | `accounts_role` |  |
| 3 | `assets_asset` | `category_id` | `assets_assetcategory` |  |
| 4 | `assets_asset` | `location_id` | `organization_location` | null |
| 5 | `assets_assetallocation` | `asset_id` | `assets_asset` |  |
| 6 | `assets_assetallocation` | `employee_id` | `employees_employee` |  |
| 7 | `assets_assetmaintenancelog` | `asset_id` | `assets_asset` |  |
| 8 | `attendance_attendancedevice` | `location_id` | `organization_location` | null |
| 9 | `attendance_attendancerecord` | `employee_id` | `employees_employee` |  |
| 10 | `attendance_esslemployeelink` | `employee_id` | `employees_employee` |  |
| 11 | `attendance_esslemployeelink` | `location_id` | `organization_location` | null |
| 12 | `attendance_rawpunch` | `device_id` | `attendance_attendancedevice` |  |
| 13 | `attendance_rawpunch` | `employee_id` | `employees_employee` | null |
| 14 | `attendance_regularizationrequest` | `employee_id` | `employees_employee` |  |
| 15 | `attendance_shiftrule` | `location_id` | `organization_location` | null |
| 16 | `employees_emergencycontact` | `employee_id` | `employees_employee` |  |
| 17 | `employees_employee` | `department_id` | `organization_department` |  |
| 18 | `employees_employee` | `designation_id` | `organization_designation` | null |
| 19 | `employees_employee` | `location_id` | `organization_location` | null |
| 20 | `employees_employee` | `level_id` | `organization_employeelevel` | null |
| 21 | `employees_employee` | `team_id` | `organization_team` | null |
| 22 | `employees_employee` | `reporting_manager_id` | `employees_employee` | null |
| 23 | `employees_employee` | `created_from_candidate_id` | `recruitment_candidate` | null |
| 24 | `employees_employeeaddress` | `employee_id` | `employees_employee` |  |
| 25 | `employees_employeedocument` | `employee_id` | `employees_employee` |  |
| 26 | `employees_employeedocument` | `document_type_id` | `employees_documenttype` |  |
| 27 | `employees_employeeeducation` | `employee_id` | `employees_employee` |  |
| 28 | `employees_employeeexperience` | `employee_id` | `employees_employee` |  |
| 29 | `employees_probationreview` | `employee_id` | `employees_employee` |  |
| 30 | `employees_probationreview` | `reviewer_id` | `employees_employee` | null |
| 31 | `employees_probationreview` | `confirmation_letter_id` | `onboarding_employeeletter` | null |
| 32 | `imports_importbatch` | `job_opening_id` | `recruitment_jobopening` | null |
| 33 | `imports_importrow` | `batch_id` | `imports_importbatch` |  |
| 34 | `imports_importrow` | `matched_candidate_id` | `recruitment_candidate` | null |
| 35 | `imports_importrow` | `created_employee_id` | `employees_employee` | null |
| 36 | `itaccounts_companyemailaccount` | `employee_id` | `employees_employee` |  |
| 37 | `leave_holiday` | `calendar_id` | `leave_holidaycalendar` |  |
| 38 | `leave_holidaycalendar` | `location_id` | `organization_location` | null |
| 39 | `leave_holidaywork` | `employee_id` | `employees_employee` |  |
| 40 | `leave_leavebalance` | `employee_id` | `employees_employee` |  |
| 41 | `leave_leavebalance` | `leave_type_id` | `leave_leavetype` |  |
| 42 | `leave_leavepolicy` | `leave_type_id` | `leave_leavetype` |  |
| 43 | `leave_leavepolicy` | `department_id` | `organization_department` | null |
| 44 | `leave_leaverequest` | `employee_id` | `employees_employee` |  |
| 45 | `leave_leaverequest` | `leave_type_id` | `leave_leavetype` |  |
| 46 | `leave_leaverequest` | `policy_id` | `leave_leavepolicy` | null |
| 47 | `leave_leavetransaction` | `employee_id` | `employees_employee` |  |
| 48 | `leave_leavetransaction` | `leave_type_id` | `leave_leavetype` |  |
| 49 | `leave_leavetransaction` | `request_id` | `leave_leaverequest` | null |
| 50 | `leave_shortleave` | `employee_id` | `employees_employee` |  |
| 51 | `leave_shortleaveconversion` | `employee_id` | `employees_employee` |  |
| 52 | `notifications_notificationdelivery` | `notification_id` | `notifications_notification` |  |
| 53 | `offboarding_clearancetemplate` | `department_id` | `organization_department` | null |
| 54 | `offboarding_clearancetemplateitem` | `template_id` | `offboarding_clearancetemplate` |  |
| 55 | `offboarding_exitclearanceitem` | `exit_workflow_id` | `offboarding_exitworkflow` |  |
| 56 | `offboarding_exitclearanceitem` | `source_item_id` | `offboarding_clearancetemplateitem` | null |
| 57 | `offboarding_exitclearanceitem` | `assigned_to_id` | `employees_employee` | null |
| 58 | `offboarding_exitinterview` | `exit_workflow_id` | `offboarding_exitworkflow` |  |
| 59 | `offboarding_exitworkflow` | `employee_id` | `employees_employee` |  |
| 60 | `offboarding_exitworkflow` | `resignation_id` | `offboarding_resignationrequest` | null |
| 61 | `offboarding_finalsettlement` | `exit_workflow_id` | `offboarding_exitworkflow` |  |
| 62 | `offboarding_resignationrequest` | `employee_id` | `employees_employee` |  |
| 63 | `onboarding_employeeletter` | `employee_id` | `employees_employee` |  |
| 64 | `onboarding_employeeletter` | `template_id` | `onboarding_lettertemplate` | null |
| 65 | `onboarding_employeeonboarding` | `employee_id` | `employees_employee` |  |
| 66 | `onboarding_employeeonboarding` | `template_id` | `onboarding_onboardingtemplate` | null |
| 67 | `onboarding_onboardingitem` | `onboarding_id` | `onboarding_employeeonboarding` |  |
| 68 | `onboarding_onboardingitem` | `source_item_id` | `onboarding_onboardingtemplateitem` | null |
| 69 | `onboarding_onboardingitem` | `assigned_to_id` | `employees_employee` | null |
| 70 | `onboarding_onboardingitem` | `document_type_id` | `employees_documenttype` | null |
| 71 | `onboarding_onboardingitem` | `document_id` | `employees_employeedocument` | null |
| 72 | `onboarding_onboardingtemplate` | `department_id` | `organization_department` | null |
| 73 | `onboarding_onboardingtemplateitem` | `template_id` | `onboarding_onboardingtemplate` |  |
| 74 | `onboarding_onboardingtemplateitem` | `document_type_id` | `employees_documenttype` | null |
| 75 | `organization_department` | `parent_department_id` | `organization_department` | null |
| 76 | `organization_department` | `head_employee_id` | `employees_employee` | null |
| 77 | `organization_designation` | `department_id` | `organization_department` | null |
| 78 | `organization_team` | `department_id` | `organization_department` |  |
| 79 | `organization_team` | `parent_team_id` | `organization_team` | null |
| 80 | `organization_team` | `head_employee_id` | `employees_employee` | null |
| 81 | `payroll_employeeloan` | `employee_id` | `employees_employee` |  |
| 82 | `payroll_employeepackage` | `employee_id` | `employees_employee` |  |
| 83 | `payroll_employeepackage` | `supersedes_id` | `payroll_employeepackage` | null |
| 84 | `payroll_investmentdeclaration` | `employee_id` | `employees_employee` |  |
| 85 | `payroll_packagedeferral` | `package_id` | `payroll_employeepackage` |  |
| 86 | `payroll_packagedeferral` | `released_in_adjustment_id` | `payroll_payrolladjustment` | null |
| 87 | `payroll_packageperiod` | `package_id` | `payroll_employeepackage` |  |
| 88 | `payroll_payrolladjustment` | `employee_id` | `employees_employee` |  |
| 89 | `payroll_payrolladjustment` | `payroll_run_id` | `payroll_payrollrun` | null |
| 90 | `payroll_payrollrun` | `location_id` | `organization_location` | null |
| 91 | `payroll_payslip` | `payroll_run_id` | `payroll_payrollrun` |  |
| 92 | `payroll_payslip` | `employee_id` | `employees_employee` |  |
| 93 | `payroll_payslip` | `salary_structure_id` | `payroll_salarystructure` | null |
| 94 | `payroll_payslip` | `location_id` | `organization_location` | null |
| 95 | `payroll_payslipline` | `payslip_id` | `payroll_payslip` |  |
| 96 | `payroll_payslipline` | `component_id` | `payroll_salarycomponent` | null |
| 97 | `payroll_reimbursementclaim` | `employee_id` | `employees_employee` |  |
| 98 | `payroll_reimbursementclaim` | `paid_in_run_id` | `payroll_payrollrun` | null |
| 99 | `payroll_salarystructure` | `employee_id` | `employees_employee` |  |
| 100 | `payroll_salarystructureline` | `component_id` | `payroll_salarycomponent` |  |
| 101 | `payroll_salarystructureline` | `salary_structure_id` | `payroll_salarystructure` |  |
| 102 | `payroll_statutorycontribution` | `payslip_id` | `payroll_payslip` |  |
| 103 | `recruitment_application` | `candidate_id` | `recruitment_candidate` |  |
| 104 | `recruitment_application` | `job_opening_id` | `recruitment_jobopening` |  |
| 105 | `recruitment_application` | `current_stage_id` | `workflows_workflowstage` |  |
| 106 | `recruitment_applicationevent` | `application_id` | `recruitment_application` |  |
| 107 | `recruitment_applicationevent` | `from_stage_id` | `workflows_workflowstage` | null |
| 108 | `recruitment_applicationevent` | `to_stage_id` | `workflows_workflowstage` | null |
| 109 | `recruitment_candidateexternalref` | `candidate_id` | `recruitment_candidate` |  |
| 110 | `recruitment_candidatenotification` | `application_id` | `recruitment_application` |  |
| 111 | `recruitment_candidatenotification` | `candidate_id` | `recruitment_candidate` |  |
| 112 | `recruitment_candidatenotification` | `job_opening_id` | `recruitment_jobopening` |  |
| 113 | `recruitment_candidaterejection` | `application_id` | `recruitment_application` |  |
| 114 | `recruitment_candidaterejection` | `candidate_id` | `recruitment_candidate` |  |
| 115 | `recruitment_candidaterejection` | `rejection_stage_id` | `workflows_workflowstage` |  |
| 116 | `recruitment_candidaterejection` | `department_recommendation_id` | `recruitment_stagedecision` | null |
| 117 | `recruitment_consentrecord` | `candidate_id` | `recruitment_candidate` |  |
| 118 | `recruitment_consentrecord` | `origin_batch_id` | `imports_importbatch` | null |
| 119 | `recruitment_decisionoverride` | `application_id` | `recruitment_application` |  |
| 120 | `recruitment_decisionoverride` | `previous_stage_id` | `workflows_workflowstage` | null |
| 121 | `recruitment_decisionoverride` | `new_stage_id` | `workflows_workflowstage` | null |
| 122 | `recruitment_interview` | `application_id` | `recruitment_application` |  |
| 123 | `recruitment_interview` | `stage_id` | `workflows_workflowstage` |  |
| 124 | `recruitment_interview` | `interviewer_id` | `employees_employee` |  |
| 125 | `recruitment_interviewfeedback` | `interview_id` | `recruitment_interview` |  |
| 126 | `recruitment_interviewfeedback` | `form_id` | `workflows_feedbackform` | null |
| 127 | `recruitment_interviewfeedback` | `submitted_by_id` | `employees_employee` |  |
| 128 | `recruitment_interviewslotinvite` | `application_id` | `recruitment_application` |  |
| 129 | `recruitment_interviewslotinvite` | `stage_id` | `workflows_workflowstage` |  |
| 130 | `recruitment_interviewslotinvite` | `candidate_id` | `recruitment_candidate` |  |
| 131 | `recruitment_jobopening` | `workflow_id` | `workflows_hiringworkflow` |  |
| 132 | `recruitment_jobopening` | `department_id` | `organization_department` |  |
| 133 | `recruitment_jobopening` | `designation_id` | `organization_designation` | null |
| 134 | `recruitment_jobopening` | `location_id` | `organization_location` | null |
| 135 | `recruitment_jobopening` | `level_id` | `organization_employeelevel` | null |
| 136 | `recruitment_jobopening` | `target_role_id` | `accounts_role` |  |
| 137 | `recruitment_jobopening` | `recruiter_id` | `employees_employee` | null |
| 138 | `recruitment_jobopening` | `hiring_manager_id` | `employees_employee` | null |
| 139 | `recruitment_offer` | `application_id` | `recruitment_application` |  |
| 140 | `recruitment_offer` | `designation_id` | `organization_designation` | null |
| 141 | `recruitment_offer` | `level_id` | `organization_employeelevel` | null |
| 142 | `recruitment_offer` | `reporting_manager_id` | `employees_employee` | null |
| 143 | `recruitment_stagedecision` | `application_id` | `recruitment_application` |  |
| 144 | `recruitment_stagedecision` | `stage_id` | `workflows_workflowstage` |  |
| 145 | `recruitment_stagedecision` | `interview_id` | `recruitment_interview` | null |
| 146 | `workflows_feedbackfield` | `form_id` | `workflows_feedbackform` |  |
| 147 | `workflows_stagetransition` | `from_stage_id` | `workflows_workflowstage` |  |
| 148 | `workflows_stagetransition` | `to_stage_id` | `workflows_workflowstage` |  |
| 149 | `workflows_workflowstage` | `workflow_id` | `workflows_hiringworkflow` |  |
| 150 | `workflows_workflowstage` | `responsible_role_id` | `accounts_role` | null |
| 151 | `workflows_workflowstage` | `feedback_form_id` | `workflows_feedbackform` | null |

## Appendix B — the 41 referenced tables needing `UNIQUE (id, organization_id)`

`accounts_role`, `assets_asset`, `assets_assetcategory`, `attendance_attendancedevice`, `employees_documenttype`, `employees_employee`, `employees_employeedocument`, `imports_importbatch`, `leave_holidaycalendar`, `leave_leavepolicy`, `leave_leaverequest`, `leave_leavetype`, `notifications_notification`, `offboarding_clearancetemplate`, `offboarding_clearancetemplateitem`, `offboarding_exitworkflow`, `offboarding_resignationrequest`, `onboarding_employeeletter`, `onboarding_employeeonboarding`, `onboarding_lettertemplate`, `onboarding_onboardingtemplate`, `onboarding_onboardingtemplateitem`, `organization_department`, `organization_designation`, `organization_employeelevel`, `organization_location`, `organization_team`, `payroll_employeepackage`, `payroll_payrolladjustment`, `payroll_payrollrun`, `payroll_payslip`, `payroll_salarycomponent`, `payroll_salarystructure`, `recruitment_application`, `recruitment_candidate`, `recruitment_interview`, `recruitment_jobopening`, `recruitment_stagedecision`, `workflows_feedbackform`, `workflows_hiringworkflow`, `workflows_workflowstage`

## Appendix C — the 95 organization-owned tables that get RLS

`accounts_role`, `accounts_rolepermission`, `accounts_userpermissionoverride`, `accounts_userrole`, `assets_asset`, `assets_assetallocation`, `assets_assetcategory`, `assets_assetmaintenancelog`, `attendance_attendancedevice`, `attendance_attendancerecord`, `attendance_esslemployeelink`, `attendance_esslsyncrun`, `attendance_orgattendanceintegration`, `attendance_rawpunch`, `attendance_regularizationrequest`, `attendance_shiftrule`, `employees_documenttype`, `employees_emergencycontact`, `employees_employee`, `employees_employeeaddress`, `employees_employeedocument`, `employees_employeeeducation`, `employees_employeeexperience`, `employees_probationreview`, `imports_importbatch`, `imports_importrow`, `itaccounts_companyemailaccount`, `leave_holiday`, `leave_holidaycalendar`, `leave_holidaywork`, `leave_leavebalance`, `leave_leavepolicy`, `leave_leaverequest`, `leave_leavesettings`, `leave_leavetransaction`, `leave_leavetype`, `leave_shortleave`, `leave_shortleaveconversion`, `notifications_notification`, `notifications_notificationdelivery`, `notifications_notificationpreference`, `offboarding_clearancetemplate`, `offboarding_clearancetemplateitem`, `offboarding_exitclearanceitem`, `offboarding_exitinterview`, `offboarding_exitworkflow`, `offboarding_finalsettlement`, `offboarding_resignationrequest`, `onboarding_employeeletter`, `onboarding_employeeonboarding`, `onboarding_lettertemplate`, `onboarding_onboardingitem`, `onboarding_onboardingtemplate`, `onboarding_onboardingtemplateitem`, `organization_department`, `organization_designation`, `organization_employeelevel`, `organization_location`, `organization_orgemailtemplate`, `organization_team`, `payroll_employeeloan`, `payroll_employeepackage`, `payroll_investmentdeclaration`, `payroll_packagedeferral`, `payroll_packageperiod`, `payroll_payrolladjustment`, `payroll_payrollrun`, `payroll_payslip`, `payroll_payslipline`, `payroll_reimbursementclaim`, `payroll_salarycomponent`, `payroll_salarystructure`, `payroll_salarystructureline`, `payroll_statutorycontribution`, `platform_supportgrant`, `recruitment_application`, `recruitment_applicationevent`, `recruitment_candidate`, `recruitment_candidateexternalref`, `recruitment_candidatenotification`, `recruitment_candidaterejection`, `recruitment_consentrecord`, `recruitment_decisionoverride`, `recruitment_interview`, `recruitment_interviewfeedback`, `recruitment_interviewslotinvite`, `recruitment_jobopening`, `recruitment_offer`, `recruitment_stagedecision`, `reporting_metricsnapshot`, `workflows_feedbackfield`, `workflows_feedbackform`, `workflows_hiringworkflow`, `workflows_stagetransition`, `workflows_workflowstage`

---

## 9. Release 2 — the runtime connects as `generic_hrms_app`

Development/test only; nothing is deployed. `DATABASE_URL` now holds the
application role and `DATABASE_OWNER_URL` the owner, and the two must name the
same host, port and database (settings refuses otherwise, so a typo cannot
point migrations at another system).

**No new migration.** Release 2 is a credentials-and-ordering change on top of
the policies release 1 installed.

### The ordering problem, and where it is solved

Authentication reads tenant-owned tables BEFORE an organization is bound,
because resolving the membership is what establishes the organization. Under
row-level security that does not error -- it returns nothing -- so the product
would deny everything while looking healthy.

| Point | What it does now |
|---|---|
| `active_membership()` | Binds the USER it is asking about for the length of its own query. The membership policy's second arm (`app.user_id`) is what makes this the one query that works with nothing bound. |
| `resolve_context()` | Binds the organization from the membership before reading roles, permissions, overrides and the Employee record -- scoped, and restored on the way out, because a context is also resolved ABOUT other people. |
| `role_grants()` | Returns a LIST evaluated inside that binding. A lazy queryset would be evaluated after the binding had gone and come back empty. |
| `_audit_login()` | Binds the membership's organization for the sign-in audit row. |
| `AuditLog.save()` | An organization-LESS row (unknown address, bootstrap, provisioning) is written through `platform_bypass`: the INSERT is permitted, but Django reads the new id back with RETURNING, and reading NULL-organization rows is nobody's right. |

### Crossing organizations, named in each case

`platform_bypass` only -- no second mechanism:

- `PlatformOnlyMixin` enters it for the duration of a platform request, in
  `initial()` (after authentication and the platform-admin check) on an
  ExitStack owned by `dispatch`, so it unwinds on every path;
- sign-in resolving a personal email address to its login;
- the public application and interview-slot links resolving their token;
- bootstrap asking whether the deployment already has an Admin;
- the role-invariant system check reading every organization's roles.

`as_runtime_role()` is the inverse, for the one case that must stay confined
on a platform endpoint: the Support Access snapshot reads as the app role,
bound to the granted organization, so the database enforces what the feature
promises.

### The fail-open that checks introduce

A build-time check that reads tenant tables sees nothing and reports success.
`access.E019` (`check_role_reads_are_not_silently_empty`) is the positive
control: organizations present but no roles visible at check time is an error
in its own right.

### Migrations

`apps/dbguard/management/commands/migrate.py` refuses to run as a role that
does not own the tables, and names the owner URL in the message. A refusal
rather than a silent switch, so a deployment can withhold the owner password
from the application process and find out.

### Tests

`tests/conftest.py` creates and migrates the test database as the owner, then
runs the whole session as `generic_hrms_app`: every existing test is now a
test of the real runtime. `tests/tenancy/test_runtime_role.py` covers the
paths that only break under it.

### Rollback

Point `DATABASE_URL` back at the owner URL and restart. No migration to
reverse; the code changes are correct under the owner too.

### A caveat for later: connection poolers

`ARCHITECTURE.md` lists PgBouncer as a scaling option "when connection count
demands it". That decision is no longer free. `core/db_context.py` sets
`app.org_id` and `app.user_id` as SESSION settings and remembers, per Django
connection object, what it last sent -- which is sound against a real server
connection and against `CONN_MAX_AGE` reuse, because a reused connection keeps
its settings and the wrapper re-sends whenever the bound organization changes.

Put a pooler in transaction mode underneath it and that assumption breaks: the
Django connection no longer maps to one server connection, so the cache could
believe a setting is in force on a backend that never received it. The options
are session-mode pooling (which keeps the mapping), or switching the wrapper to
`set_config(..., true)` inside a transaction and requiring `ATOMIC_REQUESTS`.
Neither is needed today -- there is no pooler -- but this is the note that
stops it being discovered in production.
