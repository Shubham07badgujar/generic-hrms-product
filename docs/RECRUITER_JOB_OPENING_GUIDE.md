# Creating a job opening

How a job opening gets created in the HRMS, what every field means, where the
data behind each dropdown comes from, and what happens after you publish.

**Environment:** <https://<your-hrms-domain>> → **Recruitment → Jobs**

---

## Read this first

**Recruiters can now create job openings independently.** The Recruiter role
holds view-only access to the reference lists the form needs, and the whole
flow — create, edit, publish, close — works end to end.

**HR Manager is still affected.** It also holds `job_opening: create` and still
cannot read any reference list, so the form remains uncompletable for that role;
it was outside the scope of this change. Use Recruiter, HR Head or Admin.

Verified against production, signed in as each role:

| Endpoint (feeds…) | Recruiter | HR Manager | HR Head | Admin |
|---|---|---|---|---|
| `/api/v1/departments/` — Department | 200 | **403** | 200 | 200 |
| `/api/v1/workflows/` — Hiring workflow | 200 | **403** | 200 | 200 |
| `/api/v1/roles/` — Role on hire | 200 | **403** | 200 | 200 |
| `/api/v1/designations/` — Designation | 200 | **403** | 200 | 200 |
| `/api/v1/locations/` — Location | 200 | **403** | 200 | 200 |
| `/api/v1/levels/` — Seniority level | 200 | **403** | 200 | 200 |
| `/api/v1/jobs/` — the job itself | 200 | 200 | 200 | 200 |

The Recruiter's access to those lists is **view-only** — it can read them to
fill the form, and cannot create, edit or delete any of them.

---

## What you fill in

Eleven fields. Four are required; the rest are optional and can be added later
while the job is still a draft.

| # | Field | Required | Type | What it is |
|---|---|---|---|---|
| 1 | **Job title** | **Yes** | Free text, ≤160 chars | What the role is called, e.g. "Senior Physiotherapist" |
| 2 | **Department** | **Yes** | Pick from list | Which department the role sits in. Also decides which workflows you may choose |
| 3 | **Hiring workflow** | **Yes** | Pick from list | The interview pipeline this job runs. See below — this is the important one |
| 4 | **Role on hire** | **Yes** | Pick from list | The HRMS role the person is granted when they are eventually hired |
| 5 | Designation | No | Pick from list | Job designation, e.g. "Clinic Doctor" |
| 6 | Location | No | Pick from list | Which office/site |
| 7 | Seniority level | No | Pick from list | Organisational layer (Manager, Executive, Staff…) |
| 8 | Openings | No | Number, min 1 | How many people you are hiring. Defaults to 1 |
| 9 | Description | No | Long text | The advert body |
| 10 | Requirements | No | Long text | Qualifications, experience |
| 11 | Employment type | No | Fixed | Defaults to `full_time`. **Not exposed in the UI** — only changeable via the API |

### The three that trip people up

**Hiring workflow** is the only thing that decides how this role is hired. A
Therapist pipeline and an Office Boy pipeline differ in nothing else — the
stages, who is responsible for each, and the feedback forms all come from the
workflow you pick here. Choose the wrong one and the job runs the wrong
interview process from the first application.

Two rules are enforced by the server, not just the form:

- The workflow must be **published**. A draft workflow is refused: *"'X' is a
  draft and cannot run a job."*
- The workflow's function must **match the department's function**. A workflow
  serving the medical function cannot run a job in an operations department.
  The form pre-filters the list once you pick a department, so ordinarily you
  never see the invalid combinations — the server check is the backstop.

**Because the workflow list is filtered by department, pick the department
first.** Until you do, the workflow dropdown tells you so rather than offering
you everything.

**Role on hire** is not the same as Designation. Designation is a label;
"Role on hire" grants actual permissions when a candidate is converted into an
employee. The list is deliberately narrowed to roles that are grantable in-app
*and* require an employee record — which is why CEO and Admin never appear.
It is validated again, against the department, at conversion time.

---

## Where the dropdown data comes from

Every dropdown is reference data maintained elsewhere in the system. If a list
is missing the entry you need, it has to be created there first — you cannot
add one from inside the job form.

| Dropdown | Comes from | Where to add entries | Who can add |
|---|---|---|---|
| Department | `/api/v1/departments/` | Organization → Departments | Admin |
| Hiring workflow | `/api/v1/workflows/` | Recruitment → Workflows | HR Head, Admin |
| Role on hire | `/api/v1/roles/` | Administration → Roles | Admin |
| Designation | `/api/v1/designations/` | Organization → Designations | Admin |
| Location | `/api/v1/locations/` | Organization → Locations | Admin |
| Seniority level | `/api/v1/levels/` | Organization → Levels | Admin |

The four departments currently seeded are Medical, Operations, Human Resources,
and Finance & Accounts.

**A new workflow must be published before any job can use it.** Building one and
leaving it in draft is the single most common reason the workflow dropdown looks
emptier than expected.

---

## Step by step, in the app

1. Sign in as **Recruiter**, **HR Head** or **Admin** (HR Manager still cannot).
2. Go to **Recruitment → Jobs**.
3. Click **New job opening**.
4. Fill in **Job title**.
5. Choose the **Department** — do this before the workflow.
6. Choose the **Hiring workflow** from the now-filtered list.
7. Choose the **Role on hire**.
8. Optionally add designation, location, level, openings count, description and
   requirements.
9. Click **Create draft**. The job is saved as a **draft** — it is not live and
   nobody can apply to it yet.
10. Review it, then click **Publish**. Only a draft can be published; the
    attempt is refused otherwise.

Editing stays available after publishing. Deleting does not — see the lifecycle
below.

---

## Doing it over the API

Useful for bulk-loading or scripting. Log in first:

```bash
curl -s -X POST https://<your-hrms-domain>/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"YOUR_EMAIL","password":"YOUR_PASSWORD"}'
```

Take the `access` token from the response and send it as a bearer token. Fetch
the reference lists to get the IDs you need:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://<your-hrms-domain>/api/v1/departments/
```

Then create the job. The four required keys are `title`, `department`,
`workflow` and `target_role`; everything else is optional:

```bash
curl -s -X POST https://<your-hrms-domain>/api/v1/jobs/ \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Senior Physiotherapist",
    "department": "<department-id>",
    "workflow": "<published-workflow-id>",
    "target_role": "<role-id>",
    "designation": null,
    "location": null,
    "level": null,
    "openings_count": 2,
    "employment_type": "full_time",
    "description": "…",
    "requirements": "…"
  }'
```

Publish it:

```bash
curl -s -X POST https://<your-hrms-domain>/api/v1/jobs/<job-id>/publish/ \
  -H "Authorization: Bearer $TOKEN"
```

Close it when hiring finishes:

```bash
curl -s -X POST https://<your-hrms-domain>/api/v1/jobs/<job-id>/close/ \
  -H "Authorization: Bearer $TOKEN"
```

`published_at` and `closed_at` are set by the server and cannot be written
directly. `employment_type` is the one field you can only set this way.

---

## Lifecycle

```
draft ──publish──► published ──close──► closed
                       │
                       ├──► on_hold
                       └──► filled
```

| Status | Meaning |
|---|---|
| `draft` | Created but not live. Nobody can apply |
| `published` | Live and accepting applications |
| `on_hold` | Paused |
| `closed` | Hiring finished or abandoned |
| `filled` | All openings taken |

Only `draft → published` and `→ closed` have dedicated buttons. The others are
set by editing the job's status field.

There is **no delete** for a Recruiter or HR Manager — only Admin and HR Head
hold the delete grant, and even then jobs are deactivated rather than erased,
because applications, interviews and offers hang off them.

---

## After you publish

The job becomes visible in the pipeline, and candidates can be attached to it as
applications. Each application then walks the stages defined by the workflow you
chose — which is why picking the right workflow at creation time matters more
than anything else on the form.

A Recruiter can then create candidates, create applications, and schedule
interviews. A Recruiter **cannot** create offers — offers are view-only for that
role, and are made by HR Head or Admin.

---

## Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| Department / Role / workflow dropdowns are empty, save stays disabled | You are signed in as Recruiter or HR Manager, and the reference endpoints return 403 | Use HR Head or Admin, or apply the permission change below |
| Workflow dropdown is empty but department is chosen | No **published** workflow serves that department's function | Publish the workflow in Recruitment → Workflows |
| *"'X' is a draft and cannot run a job."* | The workflow is still a draft | Publish the workflow first |
| *"'X' serves the medical function, but this job is in a operations department."* | Workflow and department functions disagree | Pick a workflow matching the department, or a department matching the workflow |
| *"Only a draft job can be published."* | The job is already published, on hold, closed or filled | Nothing to do — it is already live |
| `429 Too Many Requests` on sign-in | Rate limit, currently 150 sign-ins per hour per machine and per account | Wait, or spread testing across the hour |

---

## How the Recruiter's permissions are set

The Recruiter holds `job_opening: create, edit, view` plus organisation-wide
**view** on `department`, `designation`, `location`, `role` and
`hiring_workflow`. Those five reads are what make the create grant usable.

| Resource | Recruiter holds | Recruiter cannot |
|---|---|---|
| `job_opening` | view, create, edit | delete |
| `department` | view | create, edit, delete |
| `designation` (also serves levels) | view | create, edit, delete |
| `location` | view | create, edit, delete |
| `role` | view | create, edit, delete, assign |
| `hiring_workflow` | view | create, edit, delete, publish |

`designation` covers the seniority bands too — the levels endpoint is served
under the same resource. `role` read is safe without `can_manage_users`, because
the engine's user-management gate strips *write* actions on users and roles and
deliberately leaves reads alone: the same rule that lets the CEO see accounts
without touching them.

Two things enforce the read/write split rather than relying on grants alone.
The matrix asserts at import that no role outside Admin and HR Head holds any
write on organisation configuration, so a future edit letting a write ride along
with a read fails immediately. And the endpoints are read-only viewsets for
every role including Admin, so the write methods are not routed at all —
confirmed in production, where a Recruiter's POST and DELETE against all five
return 403.

**HR Manager is still blocked.** It holds `job_opening: create` and cannot read
any reference list, including `hiring_workflow` which the Recruiter always had.
The same view-only grants would fix it if wanted.
