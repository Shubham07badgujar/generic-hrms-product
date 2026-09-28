# Tenant Isolation Test Report

Generated 2026-09-26 06:29 UTC by `scripts/verify_tenant_isolation.py`.

This file is written by the script that performs the attempts. It is not a description of a test run; it is the output of one. Re-running the script overwrites it.

## Verdict

**PASSED.** 311 of 311 checks passed, none inconclusive.

| | |
|---|---|
| Checks passed | 311 |
| Checks failed | 0 |
| Inconclusive (positive control did not succeed) | 0 |
| Detail routes walked across organizations | 378 |
| Refused | 378 |
| Served another organization's row | 0 |
| Raised instead of answering | 0 |
| Detail routes exposed by the URL resolver, in total | 538 |
| ...of which answered for the caller's own row, and were therefore asked about | 63 |

## The organizations

Fictional, generated per run. Every domain is `.example` (RFC 2606, unroutable). Passwords are generated in memory and are not written to this file, to any file, or to the source.

| Organization | Slug | Employees | Departments | Locations |
|---|---|---|---|---|
| Northwind Health | `healthcare-320ee1` | 20 | 5 | 3 |
| Aperture Systems | `technology-320ee1` | 15 | 4 | 2 |
| Fairhaven Retail | `retail-320ee1` | 10 | 3 | 2 |

## What was attempted

Each organization's **Admin** made every attempt. Admin deliberately: they hold `Scope.ALL` on almost every resource, which is precisely the authority the organization predicate has to override. A principal with less authority proves less.

Every cross-organization attempt is paired with the same request against the caller's **own** equivalent row. An attempt whose control did not return 200 is reported as inconclusive, never as a pass -- a route that refuses everybody would otherwise read as perfect isolation.

| Attempt | Refusal required |
|---|---|
| List employees, departments, locations, leave, attendance, payroll runs, candidates, documents, audit logs | no other organization's row present |
| Read a named employee, department, location, leave request, attendance record, payroll run, candidate, document, leave type | 404 |
| Edit another organization's employee | 404 |
| Create an employee in another organization's department, with a payload valid in every other field | 400, naming the department |
| Open an exit, or file a resignation, for another organization's employee (a relation id in the body of a custom endpoint) | 400 or 404, and the employee unchanged |
| Every detail route the URL resolver exposes | 403, 404 or 405 |
| The same read as HR Head and as a plain employee | 403 or 404 |
| Scope a payroll run to another organization's location | 400, naming the location, and no run created |
| Run each scheduled subtask for one organization | no other organization's rows changed |
| Hand a subtask another organization's job opening | raises, rather than quietly finding nothing |
| Purge one organization's overdue candidates | the others' left intact |
| Plant an Admin grant for one organization's employee in another | no Admin authority anywhere |

### Why 404 and not 403

A 403 answers "that record exists and you may not see it", which tells one customer that a record of another customer's exists. The requirement is that another organization's row is indistinguishable from a row that was never there. Detail reads that answered 403 are listed below separately rather than counted as passes.

No detail read answered 403. Every refusal was a 404.

## The full route walk

The nine resources above are a list somebody chose. This walk is derived from the URL resolver instead, so a route added next year is covered the day it is added. Each route is filled with a **real id of the right model** belonging to another organization -- a random UUID would 404 for absence and prove nothing.

| Caller | Target | Routes that answered for the caller's own row | Refused | Served |
|---|---|---|---|---|
| Northwind Health | Aperture Systems | 63 | 63 | 0 |
| Northwind Health | Fairhaven Retail | 63 | 63 | 0 |
| Aperture Systems | Northwind Health | 63 | 63 | 0 |
| Aperture Systems | Fairhaven Retail | 63 | 63 | 0 |
| Fairhaven Retail | Northwind Health | 63 | 63 | 0 |
| Fairhaven Retail | Aperture Systems | 63 | 63 | 0 |

## Routes that raised instead of answering

An exception is not a refusal. Nothing was served, so no data crossed a boundary, but a view that cannot render a row is a bug and is recorded here rather than folded into the refused column.

None. Every route answered with a status code.

## Underneath the HTTP surface: the manager

Sections 1-8 above are the request surface. This is the layer beneath it, and the one that covers what a view mixin cannot: two thirds of this codebase's queryset call sites are in services, reached from management commands and Celery as well as from requests, and DRF builds an unfiltered queryset behind every writable relational serializer field. Those never pass through a view. They do pass through the manager.

Asked per model rather than once. "The mechanism exists" and "the mechanism is attached to this table" are different claims, and the first was true for a whole stage while the second was false of every table in the product.

| | |
|---|---|
| Organization-owned models found in the app registry | 95 |
| Apps owning them | 16 |
| Whose managers filter, refuse an unbound read, and keep `all_orgs()` | 95 |
| Apps | accounts, assets, attendance, employees, imports, itaccounts, leave, notifications, offboarding, onboarding, organization, payroll, platform, recruitment, reporting, workflows |

Refusing is the requirement, not returning nothing: an empty queryset inside a Celery task is indistinguishable from "no work to do", so the manager raises. `all_orgs()` is the deliberate, greppable escape for platform-wide work, and it must keep working or platform code has no way out.

## Scheduled work

Every per-organization subtask the product ships, run for one organization while the other two are watched for changes. The dispatchers themselves are not invoked here: they queue for every running organization on the deployment, which on a development database means somebody else's demo data. The subtask is where the tenancy lives.

The case that matters is the last one. A task bound to one tenant and handed another's object id must RAISE. Silently finding nothing is indistinguishable from having no work to do, which is how a customer stops being processed for a month with nothing in any log -- and it is why the manager raises rather than returning an empty queryset.

- `attendance.sync_essl` — 4/4 checks passed
- `leave.monthly_accrual` — 4/4 checks passed
- `leave.convert_short_leave` — 4/4 checks passed
- `leave.flag_absences` — 4/4 checks passed
- `imports.purge_staging_pii` — 4/4 checks passed
- `reporting.refresh_snapshots` — 4/4 checks passed
- `recruitment.purge_expired_candidates` — 4/4 checks passed
- `recruitment.sync_google_form_responses` — 4/4 checks passed

## Dashboard aggregates

A metric snapshot is an organisation-wide total, materialised nightly. The three organizations are deliberately different sizes, so a snapshot that summed two tenants is visible as a WRONG NUMBER rather than only as row ownership.

| Organization | Active employees | Headcount stored | Headcount served |
|---|---|---|---|
| Northwind Health | 23 | 23 | 23 |
| Aperture Systems | 18 | 18 | 18 |
| Fairhaven Retail | 13 | 13 | 13 |

## The defects this exercise found, re-attempted

Each of these was a real cross-tenant breach, found by probing of this kind during the manager rollout and fixed. A fix with no standing attempt against it is a fix that can be undone silently, so each is re-run here against the live system rather than described.

| Defect | What it did before | Re-attempted here |
|---|---|---|
| Payroll run location | `POST /payroll/runs/` read `location` by hand, so relation scoping never saw it: one organization's run was created carrying another's location, and the response echoed its code. An id that fails to resolve means "organization-wide", so a quiet miss would widen the run to every employee | 12a |
| Candidate retention purge | `purge_candidates --organization <slug> --apply` anonymised every organization's overdue candidates | 12b |
| Authority from anywhere | permissions were built from a user's role grants in EVERY organization, so one Admin grant row planted elsewhere made an ordinary employee an Admin at home, and the admin sign-in door let them in | 12c |
| Unaudited updates | the audit trail re-read a row through the tenant manager before an update: with nothing bound it raised, and with another organization bound it found nothing and wrote no audit record | 12d |

## Every check

| Result | Check | Detail |
|---|---|---|
| PASS | 1. Northwind Health provisioned (20 employees, 5 departments, 3 locations) |  |
| PASS | 1. Northwind Health carries every configuration seed provisioning applies | seeded ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workflows'], expected ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workf |
| PASS | 1. Northwind Health's creation is in its own audit trail |  |
| PASS | 1. Northwind Health is on a plan that disables nothing (enterprise) |  |
| PASS | 1. Northwind Health's administrator invitation was attempted (in-memory outbox) | 1 invitation(s) |
| PASS | 1. Northwind Health went live through finish_setup (trial) | trial |
| PASS | 1. Aperture Systems provisioned (15 employees, 4 departments, 2 locations) |  |
| PASS | 1. Aperture Systems carries every configuration seed provisioning applies | seeded ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workflows'], expected ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workf |
| PASS | 1. Aperture Systems's creation is in its own audit trail |  |
| PASS | 1. Aperture Systems is on a plan that disables nothing (enterprise) |  |
| PASS | 1. Aperture Systems's administrator invitation was attempted (in-memory outbox) | 1 invitation(s) |
| PASS | 1. Aperture Systems went live through finish_setup (trial) | trial |
| PASS | 1. Fairhaven Retail provisioned (10 employees, 3 departments, 2 locations) |  |
| PASS | 1. Fairhaven Retail carries every configuration seed provisioning applies | seeded ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workflows'], expected ['attendance', 'leave', 'offboarding', 'onboarding', 'roles', 'workf |
| PASS | 1. Fairhaven Retail's creation is in its own audit trail |  |
| PASS | 1. Fairhaven Retail is on a plan that disables nothing (enterprise) |  |
| PASS | 1. Fairhaven Retail's administrator invitation was attempted (in-memory outbox) | 1 invitation(s) |
| PASS | 1. Fairhaven Retail went live through finish_setup (trial) | trial |
| PASS | 2. Northwind Health / admin signs in |  |
| PASS | 2. Northwind Health / hr signs in |  |
| PASS | 2. Northwind Health / worker signs in |  |
| PASS | 2. Aperture Systems / admin signs in |  |
| PASS | 2. Aperture Systems / hr signs in |  |
| PASS | 2. Aperture Systems / worker signs in |  |
| PASS | 2. Fairhaven Retail / admin signs in |  |
| PASS | 2. Fairhaven Retail / hr signs in |  |
| PASS | 2. Fairhaven Retail / worker signs in |  |
| PASS | 3. Northwind Health admin's own organization answers | b'{"name":"Northwind Health","legal_name":"Northwind Health Private Limited","logo":null}' |
| PASS | 3. Aperture Systems admin's own organization answers | b'{"name":"Aperture Systems","legal_name":"Aperture Systems Private Limited","logo":null}' |
| PASS | 3. Fairhaven Retail admin's own organization answers | b'{"name":"Fairhaven Retail","legal_name":"Fairhaven Retail Private Limited","logo":null}' |
| PASS | 4. Northwind Health's employees list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's departments list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's locations list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's leave requests list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's attendance list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's payroll runs list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's candidates list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's documents list contains no other organization's rows | [] |
| PASS | 4. Northwind Health's audit logs list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's employees list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's departments list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's locations list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's leave requests list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's attendance list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's payroll runs list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's candidates list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's documents list contains no other organization's rows | [] |
| PASS | 4. Aperture Systems's audit logs list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's employees list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's departments list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's locations list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's leave requests list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's attendance list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's payroll runs list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's candidates list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's documents list contains no other organization's rows | [] |
| PASS | 4. Fairhaven Retail's audit logs list contains no other organization's rows | [] |
| PASS | 5. Northwind Health reading Aperture Systems's employee | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's department | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's location | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's leave_request | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's attendance_record | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's payroll_run | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's candidate | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's employee_document | 404 |
| PASS | 5. Northwind Health reading Aperture Systems's leave_type | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's employee | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's department | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's location | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's leave_request | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's attendance_record | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's payroll_run | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's candidate | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's employee_document | 404 |
| PASS | 5. Northwind Health reading Fairhaven Retail's leave_type | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's employee | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's department | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's location | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's leave_request | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's attendance_record | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's payroll_run | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's candidate | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's employee_document | 404 |
| PASS | 5. Aperture Systems reading Northwind Health's leave_type | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's employee | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's department | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's location | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's leave_request | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's attendance_record | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's payroll_run | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's candidate | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's employee_document | 404 |
| PASS | 5. Aperture Systems reading Fairhaven Retail's leave_type | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's employee | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's department | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's location | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's leave_request | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's attendance_record | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's payroll_run | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's candidate | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's employee_document | 404 |
| PASS | 5. Fairhaven Retail reading Northwind Health's leave_type | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's employee | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's department | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's location | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's leave_request | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's attendance_record | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's payroll_run | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's candidate | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's employee_document | 404 |
| PASS | 5. Fairhaven Retail reading Aperture Systems's leave_type | 404 |
| PASS | 6. Northwind Health cannot edit Aperture Systems's employee | 404 |
| PASS | 6b. Northwind Health cannot create an employee in Aperture Systems's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'dabbdc0c-ff17-4bb2-8b' |
| PASS | 6c. Northwind Health cannot offboard Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"eebfeeeb-cd60-406d-801a-13542c3e235d\\" - obje' |
| PASS | 6d. Northwind Health cannot file a resignation for Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"eebfeeeb-cd60-406d-801a-13542c3e235d\\" - obje' |
| PASS | 6. Northwind Health cannot edit Fairhaven Retail's employee | 404 |
| PASS | 6b. Northwind Health cannot create an employee in Fairhaven Retail's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'02d40d7f-0150-4ec3-ae' |
| PASS | 6c. Northwind Health cannot offboard Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"ce39b408-a711-42f4-b5fb-52722731ff25\\" - obje' |
| PASS | 6d. Northwind Health cannot file a resignation for Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"ce39b408-a711-42f4-b5fb-52722731ff25\\" - obje' |
| PASS | 6. Aperture Systems cannot edit Northwind Health's employee | 404 |
| PASS | 6b. Aperture Systems cannot create an employee in Northwind Health's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'a844600d-a05f-4615-96' |
| PASS | 6c. Aperture Systems cannot offboard Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"a1fa5448-3814-461a-b314-f5f1e66b6016\\" - obje' |
| PASS | 6d. Aperture Systems cannot file a resignation for Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"a1fa5448-3814-461a-b314-f5f1e66b6016\\" - obje' |
| PASS | 6. Aperture Systems cannot edit Fairhaven Retail's employee | 404 |
| PASS | 6b. Aperture Systems cannot create an employee in Fairhaven Retail's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'02d40d7f-0150-4ec3-ae' |
| PASS | 6c. Aperture Systems cannot offboard Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"ce39b408-a711-42f4-b5fb-52722731ff25\\" - obje' |
| PASS | 6d. Aperture Systems cannot file a resignation for Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"ce39b408-a711-42f4-b5fb-52722731ff25\\" - obje' |
| PASS | 6. Fairhaven Retail cannot edit Northwind Health's employee | 404 |
| PASS | 6b. Fairhaven Retail cannot create an employee in Northwind Health's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'a844600d-a05f-4615-96' |
| PASS | 6c. Fairhaven Retail cannot offboard Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"a1fa5448-3814-461a-b314-f5f1e66b6016\\" - obje' |
| PASS | 6d. Fairhaven Retail cannot file a resignation for Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"a1fa5448-3814-461a-b314-f5f1e66b6016\\" - obje' |
| PASS | 6. Fairhaven Retail cannot edit Aperture Systems's employee | 404 |
| PASS | 6b. Fairhaven Retail cannot create an employee in Aperture Systems's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'dabbdc0c-ff17-4bb2-8b' |
| PASS | 6c. Fairhaven Retail cannot offboard Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"eebfeeeb-cd60-406d-801a-13542c3e235d\\" - obje' |
| PASS | 6d. Fairhaven Retail cannot file a resignation for Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"eebfeeeb-cd60-406d-801a-13542c3e235d\\" - obje' |
| PASS | 7. Northwind Health -> Aperture Systems: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 7. Northwind Health -> Fairhaven Retail: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 7. Aperture Systems -> Northwind Health: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 7. Aperture Systems -> Fairhaven Retail: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 7. Fairhaven Retail -> Northwind Health: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 7. Fairhaven Retail -> Aperture Systems: 63/63 detail routes refused | no route served the caller's own row |
| PASS | 8. Northwind Health / hr cannot read Aperture Systems's employee | 404 |
| PASS | 8. Northwind Health / hr cannot read Fairhaven Retail's employee | 404 |
| PASS | 8. Northwind Health / worker cannot read Aperture Systems's employee | 404 |
| PASS | 8. Northwind Health / worker cannot read Fairhaven Retail's employee | 404 |
| PASS | 8. Aperture Systems / hr cannot read Northwind Health's employee | 404 |
| PASS | 8. Aperture Systems / hr cannot read Fairhaven Retail's employee | 404 |
| PASS | 8. Aperture Systems / worker cannot read Northwind Health's employee | 404 |
| PASS | 8. Aperture Systems / worker cannot read Fairhaven Retail's employee | 404 |
| PASS | 8. Fairhaven Retail / hr cannot read Northwind Health's employee | 404 |
| PASS | 8. Fairhaven Retail / hr cannot read Aperture Systems's employee | 404 |
| PASS | 8. Fairhaven Retail / worker cannot read Northwind Health's employee | 404 |
| PASS | 8. Fairhaven Retail / worker cannot read Aperture Systems's employee | 404 |
| PASS | 9. the app registry offers 95 organization-owned models across 16 apps | 95 |
| PASS | 9. accounts.Role filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. accounts.RolePermission filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. accounts.UserPermissionOverride filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. accounts.UserRole filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. assets.Asset filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. assets.AssetAllocation filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. assets.AssetCategory filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. assets.AssetMaintenanceLog filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.AttendanceDevice filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.AttendanceRecord filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.EsslEmployeeLink filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.EsslSyncRun filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.OrgAttendanceIntegration filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.RawPunch filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.RegularizationRequest filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. attendance.ShiftRule filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.DocumentType filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.EmergencyContact filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.Employee filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.EmployeeAddress filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.EmployeeDocument filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.EmployeeEducation filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.EmployeeExperience filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. employees.ProbationReview filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. imports.ImportBatch filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. imports.ImportRow filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. itaccounts.CompanyEmailAccount filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.Holiday filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.HolidayCalendar filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.HolidayWork filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeaveBalance filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeavePolicy filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeaveRequest filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeaveSettings filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeaveTransaction filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.LeaveType filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.ShortLeave filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. leave.ShortLeaveConversion filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. notifications.Notification filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. notifications.NotificationDelivery filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. notifications.NotificationPreference filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ClearanceTemplate filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ClearanceTemplateItem filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ExitClearanceItem filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ExitInterview filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ExitWorkflow filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.FinalSettlement filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. offboarding.ResignationRequest filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.EmployeeLetter filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.EmployeeOnboarding filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.LetterTemplate filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.OnboardingItem filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.OnboardingTemplate filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. onboarding.OnboardingTemplateItem filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.Department filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.Designation filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.EmployeeLevel filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.Location filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.OrgEmailTemplate filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. organization.Team filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.EmployeeLoan filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.EmployeePackage filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.InvestmentDeclaration filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.PackageDeferral filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.PackagePeriod filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.PayrollAdjustment filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.PayrollRun filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.Payslip filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.PayslipLine filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.ReimbursementClaim filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.SalaryComponent filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.SalaryStructure filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.SalaryStructureLine filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. payroll.StatutoryContribution filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. platform.SupportGrant filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.Application filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.ApplicationEvent filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.Candidate filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.CandidateExternalRef filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.CandidateNotification filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.CandidateRejection filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.ConsentRecord filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.DecisionOverride filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.Interview filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.InterviewFeedback filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.InterviewSlotInvite filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.JobOpening filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.Offer filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. recruitment.StageDecision filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. reporting.MetricSnapshot filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. workflows.FeedbackField filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. workflows.FeedbackForm filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. workflows.HiringWorkflow filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. workflows.StageTransition filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 9. workflows.WorkflowStage filters, refuses an unbound read, and keeps all_orgs() |  |
| PASS | 10. attendance.sync_essl for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. attendance.sync_essl for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. attendance.sync_essl for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. attendance.sync_essl refuses an unknown organization |  |
| PASS | 10. leave.monthly_accrual for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.monthly_accrual for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.monthly_accrual for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.monthly_accrual refuses an unknown organization |  |
| PASS | 10. leave.convert_short_leave for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.convert_short_leave for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.convert_short_leave for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.convert_short_leave refuses an unknown organization |  |
| PASS | 10. leave.flag_absences for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.flag_absences for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.flag_absences for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. leave.flag_absences refuses an unknown organization |  |
| PASS | 10. imports.purge_staging_pii for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. imports.purge_staging_pii for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. imports.purge_staging_pii for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. imports.purge_staging_pii refuses an unknown organization |  |
| PASS | 10. reporting.refresh_snapshots for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. reporting.refresh_snapshots for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. reporting.refresh_snapshots for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. reporting.refresh_snapshots refuses an unknown organization |  |
| PASS | 10. recruitment.purge_expired_candidates for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.purge_expired_candidates for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.purge_expired_candidates for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.purge_expired_candidates refuses an unknown organization |  |
| PASS | 10. recruitment.sync_google_form_responses for Northwind Health changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.sync_google_form_responses for Aperture Systems changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.sync_google_form_responses for Fairhaven Retail changes no other organization's rows | rows changed in another organization |
| PASS | 10. recruitment.sync_google_form_responses refuses an unknown organization |  |
| PASS | 10. a task for Northwind Health refuses Aperture Systems's job opening |  |
| PASS | 10. a task for Northwind Health refuses Fairhaven Retail's job opening |  |
| PASS | 10. a task for Aperture Systems refuses Northwind Health's job opening |  |
| PASS | 10. a task for Aperture Systems refuses Fairhaven Retail's job opening |  |
| PASS | 10. a task for Fairhaven Retail refuses Northwind Health's job opening |  |
| PASS | 10. a task for Fairhaven Retail refuses Aperture Systems's job opening |  |
| PASS | 11. Northwind Health's stored headcount is its own (23) | stored 23.0000, own headcount 23 |
| PASS | 11. every snapshot row written for Northwind Health belongs to it | 13 |
| PASS | 11. Aperture Systems's stored headcount is its own (18) | stored 18.0000, own headcount 18 |
| PASS | 11. every snapshot row written for Aperture Systems belongs to it | 13 |
| PASS | 11. Fairhaven Retail's stored headcount is its own (13) | stored 13.0000, own headcount 13 |
| PASS | 11. every snapshot row written for Fairhaven Retail belongs to it | 13 |
| PASS | 11. Northwind Health's dashboard reports its own headcount, not a sum | 200 served [23, 23, 23], own 23, others [18, 13] |
| PASS | 11. Aperture Systems's dashboard reports its own headcount, not a sum | 200 served [18, 18, 18], own 18, others [23, 13] |
| PASS | 11. Fairhaven Retail's dashboard reports its own headcount, not a sum | 200 served [13, 13, 13], own 13, others [23, 18] |
| PASS | 12a. Northwind Health cannot scope a payroll run to Aperture Systems's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"0c9e3196-e910-4425-b578-6' |
| PASS | 12a. Northwind Health cannot scope a payroll run to Fairhaven Retail's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"82c8f9db-754f-491a-867c-7' |
| PASS | 12a. Aperture Systems cannot scope a payroll run to Northwind Health's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"f753fe34-cc8c-4216-8668-a' |
| PASS | 12a. Aperture Systems cannot scope a payroll run to Fairhaven Retail's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"38de62c6-90da-493c-b4cf-7' |
| PASS | 12a. Fairhaven Retail cannot scope a payroll run to Northwind Health's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"259d798b-88d7-4488-86e4-3' |
| PASS | 12a. Fairhaven Retail cannot scope a payroll run to Aperture Systems's location | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"location":"No such location."},"request_id":"eff4cf66-f327-4486-bef9-5' |
| PASS | 12b. purging Northwind Health anonymised its own overdue candidate | Redacted |
| PASS | 12b. purging Northwind Health left Aperture Systems's candidate intact | Overdue |
| PASS | 12b. purging Northwind Health left Fairhaven Retail's candidate intact | Overdue |
| PASS | 12c. a grant row in Aperture Systems makes Northwind Health's employee no Admin | ['employee'] |
| PASS | 12c. a grant row in Northwind Health makes Aperture Systems's employee no Admin | ['employee'] |
| PASS | 12c. a grant row in Northwind Health makes Fairhaven Retail's employee no Admin | ['employee'] |
| PASS | 12d. an update to Northwind Health's employee is saved and audited without the re-read imposing a tenant question | no audit row was written |
| PASS | 12d. an update to Aperture Systems's employee is saved and audited without the re-read imposing a tenant question | no audit row was written |
| PASS | 12d. an update to Fairhaven Retail's employee is saved and audited without the re-read imposing a tenant question | no audit row was written |

## What this does not prove

Stated because a report that only lists what passed is not evidence, it is advertising.

- **The organizations are provisioned by the platform service, but their DATA is not created through the API.** `provision_organization` builds each one exactly as the console does for a real customer -- configuration seeds, first administrator, audit row, invitation -- and each goes live through `finish_setup`. The departments, people, payroll run and candidates the walk aims at are then written directly, because they are the targets of the proof rather than its subject; whether the application's own create endpoints keep a row in its organization is the write-injection matrix's job, not this report's.
- **The walk reached 63 of the 538 routes the resolver exposes.** A route is only asserted against when it serves the CALLER's own row, because one that answers 405 or 404 for everybody refuses both organizations equally and counting it as a refusal would score the test against itself. The rest are dropped for lack of an answer, not judged and passed. Most of the gap is POST/PUT-only actions, file downloads with nothing uploaded, and the `.json` suffix duplicates of routes already walked.
- **The dispatchers are not run, only the subtasks they queue.** `fan_out` queues for every running organization on the deployment, which on a development database means data this script did not create. The pairing between a beat row and its subtask is asserted by the suite instead.
- **Per-organization email, files and configuration are not covered here.** They have their own tests: no message addressed outside the acting organization, every upload under its own organization's subtree, and every resolver taking an explicit organization.
- **The platform boundary is not covered here.** Whether a platform administrator is kept out of customer HR data is asserted by the platform layer's own tests: they hold no role grant in any organization, so every tenant queryset resolves to nothing and every tenant route refuses them.
- **A passing report is evidence about this build, not a proof of the design.** It re-runs on demand; it does not run on every commit. The gate that does is the suite.

