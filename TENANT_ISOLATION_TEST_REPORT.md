# Tenant Isolation Test Report

Generated 2026-09-15 12:31 UTC by `scripts/verify_tenant_isolation.py`.

This file is written by the script that performs the attempts. It is not a description of a test run; it is the output of one. Re-running the script overwrites it.

## Verdict

**PASSED.** 138 of 138 checks passed, none inconclusive.

| | |
|---|---|
| Checks passed | 138 |
| Checks failed | 0 |
| Inconclusive (positive control did not succeed) | 0 |
| Detail routes walked across organizations | 378 |
| Refused | 378 |
| Served another organization's row | 0 |
| Raised instead of answering | 0 |
| Detail routes exposed by the URL resolver, in total | 515 |
| ...of which answered for the caller's own row, and were therefore asked about | 63 |

## The organizations

Fictional, generated per run. Every domain is `.example` (RFC 2606, unroutable). Passwords are generated in memory and are not written to this file, to any file, or to the source.

| Organization | Slug | Employees | Departments | Locations |
|---|---|---|---|---|
| Northwind Health | `healthcare-fbbd16` | 20 | 5 | 3 |
| Aperture Systems | `technology-fbbd16` | 15 | 4 | 2 |
| Fairhaven Retail | `retail-fbbd16` | 10 | 3 | 2 |

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

## Every check

| Result | Check | Detail |
|---|---|---|
| PASS | 1. Northwind Health provisioned (20 employees, 5 departments, 3 locations) |  |
| PASS | 1. Aperture Systems provisioned (15 employees, 4 departments, 2 locations) |  |
| PASS | 1. Fairhaven Retail provisioned (10 employees, 3 departments, 2 locations) |  |
| PASS | 2. Northwind Health / admin signs in |  |
| PASS | 2. Northwind Health / hr signs in |  |
| PASS | 2. Northwind Health / worker signs in |  |
| PASS | 2. Aperture Systems / admin signs in |  |
| PASS | 2. Aperture Systems / hr signs in |  |
| PASS | 2. Aperture Systems / worker signs in |  |
| PASS | 2. Fairhaven Retail / admin signs in |  |
| PASS | 2. Fairhaven Retail / hr signs in |  |
| PASS | 2. Fairhaven Retail / worker signs in |  |
| PASS | 3. Northwind Health admin's own organization answers | b'{"name":"Northwind Health","legal_name":"","logo":null}' |
| PASS | 3. Aperture Systems admin's own organization answers | b'{"name":"Aperture Systems","legal_name":"","logo":null}' |
| PASS | 3. Fairhaven Retail admin's own organization answers | b'{"name":"Fairhaven Retail","legal_name":"","logo":null}' |
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
| PASS | 6b. Northwind Health cannot create an employee in Aperture Systems's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'386ae37d-7b0e-4c50-ad' |
| PASS | 6c. Northwind Health cannot offboard Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"7d4c110d-a46e-4171-a63d-71726ca533cc\\" - obje' |
| PASS | 6d. Northwind Health cannot file a resignation for Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"7d4c110d-a46e-4171-a63d-71726ca533cc\\" - obje' |
| PASS | 6. Northwind Health cannot edit Fairhaven Retail's employee | 404 |
| PASS | 6b. Northwind Health cannot create an employee in Fairhaven Retail's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'11321436-df17-46c8-9c' |
| PASS | 6c. Northwind Health cannot offboard Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"e00dcdbe-1b65-4283-b2f3-5e0383c6685d\\" - obje' |
| PASS | 6d. Northwind Health cannot file a resignation for Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"e00dcdbe-1b65-4283-b2f3-5e0383c6685d\\" - obje' |
| PASS | 6. Aperture Systems cannot edit Northwind Health's employee | 404 |
| PASS | 6b. Aperture Systems cannot create an employee in Northwind Health's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'a896fd91-0f6f-46d8-90' |
| PASS | 6c. Aperture Systems cannot offboard Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"048bec56-2e5b-4a02-a4d3-65b6345462f6\\" - obje' |
| PASS | 6d. Aperture Systems cannot file a resignation for Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"048bec56-2e5b-4a02-a4d3-65b6345462f6\\" - obje' |
| PASS | 6. Aperture Systems cannot edit Fairhaven Retail's employee | 404 |
| PASS | 6b. Aperture Systems cannot create an employee in Fairhaven Retail's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'11321436-df17-46c8-9c' |
| PASS | 6c. Aperture Systems cannot offboard Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"e00dcdbe-1b65-4283-b2f3-5e0383c6685d\\" - obje' |
| PASS | 6d. Aperture Systems cannot file a resignation for Fairhaven Retail's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"e00dcdbe-1b65-4283-b2f3-5e0383c6685d\\" - obje' |
| PASS | 6. Fairhaven Retail cannot edit Northwind Health's employee | 404 |
| PASS | 6b. Fairhaven Retail cannot create an employee in Northwind Health's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'a896fd91-0f6f-46d8-90' |
| PASS | 6c. Fairhaven Retail cannot offboard Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"048bec56-2e5b-4a02-a4d3-65b6345462f6\\" - obje' |
| PASS | 6d. Fairhaven Retail cannot file a resignation for Northwind Health's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"048bec56-2e5b-4a02-a4d3-65b6345462f6\\" - obje' |
| PASS | 6. Fairhaven Retail cannot edit Aperture Systems's employee | 404 |
| PASS | 6b. Fairhaven Retail cannot create an employee in Aperture Systems's department | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"department":["No department matches {\'pk\': UUID(\'386ae37d-7b0e-4c50-ad' |
| PASS | 6c. Fairhaven Retail cannot offboard Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"7d4c110d-a46e-4171-a63d-71726ca533cc\\" - obje' |
| PASS | 6d. Fairhaven Retail cannot file a resignation for Aperture Systems's employee | 400 b'{"error":{"code":"invalid","message":"Validation failed.","details":{"employee":["Invalid pk \\"7d4c110d-a46e-4171-a63d-71726ca533cc\\" - obje' |
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

## What this does not prove

Stated because a report that only lists what passed is not evidence, it is advertising.

- **The organizations are built by this script, not by the platform provisioning service**, which does not exist yet. `provision()` is the seam: when that service lands, it replaces the body of that function and this report starts proving the real creation path too.
- **The walk reached 63 of the 515 routes the resolver exposes.** A route is only asserted against when it serves the CALLER's own row, because one that answers 405 or 404 for everybody refuses both organizations equally and counting it as a refusal would score the test against itself. The rest are dropped for lack of an answer, not judged and passed. Most of the gap is POST/PUT-only actions, file downloads with nothing uploaded, and the `.json` suffix duplicates of routes already walked.
- **Celery tasks are not exercised here.** A task's isolation is a separate question with its own per-task tests, including the negative case where a task given another organization's object id must fail rather than silently do nothing.
- **Per-organization email, files and configuration are not covered.** Those arrive with the per-organization configuration work and get their own checks.
- **The platform layer is not covered**, because it does not exist yet. Whether a platform administrator is kept out of customer HR data is a question this report cannot yet answer.

