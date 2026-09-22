# Platform Administration

**Status:** current as of 2026-09-10. Covers the platform security domain: what
it is, who may enter it, and what they may and may not do.

---

## 0. The governing sentence

> The system consists of two clearly separated security domains: **Platform**
> and **Organization**. Platform functionality manages the SaaS itself.
> Organization functionality manages customer HRMS data. No organization role
> may access another organization's data, and **Platform Admin does not
> receive implicit access to customer HR data.**

Everything in this document is an implementation of that sentence. Where a
question is not answered here, it is the tie-breaker.

```
                         HRMS SaaS
                             │
              ┌──────────────┴──────────────┐
              │                             │
       Platform Layer                Organization Layer
              │                             │
       Platform Admin              Organization Admin
                                            │
                            ┌───────────────┼───────────────┐
                            │               │               │
                        HR roles      Finance roles      Employees
```

The two domains are **disjoint**, not nested. A Platform Admin is not a
super-user of the organization layer; they are a principal of a different
system that happens to share a deployment.

---

## 1. The principals

### Platform Admin — exactly one principal type

| | |
|---|---|
| How it exists | `User.is_platform_admin`, a boolean column |
| How it is granted | `manage.py bootstrap_platform_admin` and nothing else |
| Roles held in any organization | **None.** Not "few" — none |
| Organization membership | **None**, and the command refuses to promote an account that has one |
| Signs in at | `/api/v1/auth/login/platform/` |
| Reaches | `/api/v1/platform/**` |

**Why a column and not a role.** Roles are rows, and rows are editable at
runtime by an organization's own Admin through the permission matrix. If
platform authority were a role, or if PLAN and ORGANIZATION were resources in
the matrix, a customer's Admin could grant themselves platform authority by
editing their own roles. It is a column for the same reason `is_read_only`
is a column: it is a property of the account, not a preference of the tenant.

### Organization roles — defaults, not system roles

Each organization is provisioned with a starting role set **which it then
owns**:

| Default role | Purpose |
|---|---|
| Organization Admin | The customer's own top-level administrator |
| HR Head / HR Admin | People operations |
| Finance Head / Finance Admin | Payroll and compliance |
| Employee | Self-service baseline |
| *anything else* | Created and configured by the Organization Admin at runtime |

The load-bearing property: **these are seeded defaults, not hard-coded system
roles.** No code path tests for `role.code == "hr_head"`. An organization may
rename them, deactivate them, edit their permission scopes, or create entirely
different ones, and the authorization engine keeps working because it reads
rows rather than constants.

What stays in code, deliberately, is the five-layer authority *number* and the
segregation-of-duties invariants — hiring and paying disjoint, a payroll run's
preparer never its approver. Those are control design, not preference, and a
customer must not be able to configure them away.

---

## 2. Who may do what

| | **Platform Admin** | **Organization Admin** |
|---|---|---|
| Belongs to | The SaaS operator; **no organization** | Exactly one organization |
| Creates organizations | **Yes** | No |
| Manages plans and subscriptions | **Yes** | No (reads own plan and usage) |
| Enables/disables features | **Yes** (per plan, or per-org override with a reason) | No |
| Suspends / restores / cancels an organization | **Yes** | No |
| Platform-level support access | **Yes**, only via an approved, time-limited, audited grant | n/a |
| Employees, payroll, attendance, leave, recruitment, documents | **No implicit access — none** | **Yes**, own organization only |
| Departments, designations, locations, levels | No | **Yes** |
| Roles and permissions | No | **Yes** (create, rename, deactivate, edit scopes) |
| Organization settings, branding, integrations, email | No | **Yes** |
| Another organization's anything | **No** | **No** |

The two "no implicit access" cells are the ones that matter, and they are
enforced structurally rather than by policy. A platform admin resolves to a
context with **no grants and no organization**, so `RBACPermission` refuses
every organization route and every tenant queryset resolves to nothing. There
is no matrix edit, in any organization, that changes this.

### What a Platform Admin *can* see about a customer

Commercial metadata, and only that:

- the organization's identity and contact details, as the customer entered them
- its lifecycle status
- **how many** employees and user accounts it has

A seat count is what a plan's limit is measured against, and an operator who
cannot see it cannot answer "is this customer about to exceed their plan".
Knowing that a customer employs 118 people tells you nothing about any of them.

---

## 3. How the boundary is enforced

Four mechanisms, deliberately independent. Any one of them failing leaves the
others standing.

| # | Mechanism | Enforces |
|---|---|---|
| 1 | `platform_only = True` on the view, checked by `RBACPermission` **before anything else** | Only a platform admin reaches a platform route |
| 2 | An explicit refusal in `RBACPermission` for a platform principal on an organization route | A platform admin reaches no organization route, even if somebody later hands them a role |
| 3 | `resolve_context()` returning a context with no grants and no organization | Every tenant queryset resolves to nothing for them, and every permission check fails |
| 4 | Build-time system checks `access.E011` / `E012` / `E013` | A future view cannot join the platform tree without the declaration, or carry the declaration outside it, or disable RBAC on it |

Mechanism 2 is redundant with 3 **on purpose**. "Holds no grants" is a property
somebody could change by giving an operator a role; the brief's rule should not
depend on nobody ever doing that.

### The URL split

Everything platform lives under `/api/v1/platform/`, and nothing else does.
`access.E011` fails the build for a view mounted there without the declaration;
`access.E012` fails it for the declaration outside that tree. Two disjoint URL
trees make the domain split legible outside Python — in a route table, an
access log, a proxy rule.

**The one thing under `/auth/` rather than `/platform/`** is the platform
sign-in entrance, `/api/v1/auth/login/platform/`. A login view cannot be
`platform_only`, because the flag it would check lives on a principal that does
not exist until the view succeeds. Putting it under the platform prefix would
force an allowlist of platform URLs that are not platform views, and an
absolute rule is worth more than a rule with one exception. It also means a
newly created operator carrying `must_change_password` can still reach
change-password, which is already inside the password gate's allowed prefixes.

### One door per domain

The entrances test an **equality**, not a one-way gate: the platform entrance
refuses organization users, and every organization entrance refuses the
operator. The second half grants nothing either way — authority is re-derived
from the flag on every request, whichever door minted the token — but without
it an operator signing in at the organization door would receive a session that
fails on every screen the customer SPA renders. That is a confusing dead end
rather than a refusal.

Both entrances return the **same generic error as a wrong password**, so
neither confirms which addresses belong to the operator.

---

## 4. Creating an operator

```bash
manage.py bootstrap_platform_admin --email ops@example.com --first-name Asha
manage.py changepassword ops@example.com
```

Then sign in at `/api/v1/auth/login/platform/`.

To remove platform authority:

```bash
manage.py bootstrap_platform_admin --email ops@example.com --revoke
```

**There is no HTTP equivalent, deliberately.** `bootstrap_admin` has one
because the approved plan called for a Postman-callable route to create a
customer's first Admin. This command creates the operator of the entire
deployment, and the right number of network-reachable ways to do that is zero.
The field is `editable=False`, so it reaches no form and no writable
serializer — a `ModelSerializer` over `User` with `fields = "__all__"` emits it
read-only, which a test asserts.

**Promoting an account that belongs to a customer is refused.** One person
holding both a membership and platform authority is the single principal the
domain split exists to prevent — and it would not even work: context resolution
short-circuits to the platform context, so their own organization would
silently go dark.

---

## 5. Support access to customer data

Built, narrow. When an operator needs to see a customer's configuration to
answer a support question, the mechanism is a **SupportGrant**:

- the operator requests it from the organization's page in the console, with a
  written reason of at least 20 characters (a database constraint agrees);
- it is approved or denied by an **Admin of that organization**, on their own
  *Support access* page — never by the operator;
- an approval lasts **24 hours**, the customer can end it sooner, and only
  **the operator who asked** can use it;
- what it shows is a **code constant**, `SUPPORT_VISIBLE`: structure, roles and
  permissions, leave and shift policies, hiring workflows, document types and
  salary components. No employees, salaries, payslips, candidates, documents
  or attendance. No settings screen can widen it;
- it is audited in the **customer's** trail at request, decision, every use
  and revocation.

| Who | Route |
|---|---|
| Operator requests | `POST /platform/organizations/{id}/support-grants/` |
| Operator views | `GET /platform/support-grants/{id}/configuration/` |
| Operator ends early | `POST /platform/support-grants/{id}/end/` |
| Customer lists | `GET /org/support-grants/` |
| Customer decides | `POST /org/support-grants/{id}/approve|deny|revoke/` |

**How it differs from the original design, and why.** The design said an
approved grant "resolves to a read-only context". Built literally, that means
`resolve_context` choosing an organization for a platform principal from
something the request carries — client-supplied tenant identity by another
name, the shape of the incident this architecture exists to prevent. So the
operator never gets a tenant context. The grant opens one platform route,
which binds the organization **from the grant row** on the server and reads
the fixed manifest; the operator's context stays grant-less everywhere else.

**Full-read support access is deferred** until there is a named need and a
customer-facing consent screen. An operator who needs to see a payslip today
asks the customer to send it.

---

## 6. The three demo customers

A single demo company proves the HR product runs and proves nothing about the
SaaS one: every question worth asking — does one customer's HR Head reach
another's payroll, does a plan without payroll actually hide it, does an
expiring trial say so — needs a second and a third company that differ from
the first.

```bash
python manage.py seed_plans
python manage.py bootstrap_platform_admin
python manage.py seed_demo_platform
```

| Company | Size | Plan | The mechanism it exercises |
|---|---|---|---|
| `demo-healthcare` | 20 employees, 5 departments, 3 locations | Enterprise | Every module, one account per role. The RBAC and payroll reference company. |
| `demo-technology` | 15 employees, 4 departments, 2 locations | Starter | Payroll, assets, biometric devices, IT accounts and reporting **disabled by plan**. Entitlement, visibly separate from permission. |
| `demo-retail` | 10 employees, 3 departments, 2 locations | Growth, `trialing` | A trial days from expiry. |

Three properties are worth knowing before using them:

- **They go through `provision_organization`.** Not a script that writes rows:
  the same atomic call the console makes, with the same validation,
  configuration seeds, first administrator, membership, role grant and audit
  rows. A demo that bypassed provisioning would prove only that the seeding
  command can write rows; this one fails here, rather than in front of a
  customer, when the provisioning path breaks.
- **They go live through `finish_setup`.** Which refuses while a required
  wizard step is outstanding and names the ones that are — so a demo company
  reaching `active` is itself an assertion that it is completely configured.
- **Removal is keyed on the organization, never on an email domain.** Demo rows
  were once selected by address suffix, which meant two demo companies could
  delete each other's people. The profiles also hold distinct domains and
  slugs, and a test asserts it.

Only healthcare promises an account for every role. Fifteen people cannot hold
eighteen roles and ten certainly cannot, so the other two name the roles they
leave out (`DemoProfile.absent_roles`) rather than quietly missing them, and
the command prints that list.

The profiles rename some roles per industry — `operations_manager` reads as
"Store Manager" in retail and "Engineering Manager" in technology. That is a
**display rename on that organization's own rows**, which is the product's
actual stance that the seeded roles are defaults the customer owns. The
permission matrix, the five layers and the segregation-of-duties invariants are
code and identical in all three.

---

## 7. What is not built yet

Stated so the tables above are not read as a description of shipped software.

| Area | Status |
|---|---|
| Platform boundary, sign-in, organization list and summary | **Built** |
| Organization provisioning as one atomic service | **Built** — service, console form, and `manage.py provision_organization` |
| Plans, subscriptions, feature gating, seat limits | **Built** |
| The setup wizard a new Organization Admin walks through | **Built** — computed from `satisfied(org)` predicates, not stored wizard state |
| Platform console UI | **Built** — its own route tree, disjoint from the HR application |
| Suspend and cancel | **Built**, through subscription status; `Organization.status` follows it |
| Archive, purge, and their retention windows | **Built** — archive in the console (cancelled, after the 90-day export window, with a reason); purge only by `manage.py purge_organization <slug> --confirm <slug>`, one year after archive. See §8 |
| Full-organization export before cancellation | **Built** — `GET /org/export/`, the customer Admin's own, for 90 days after cancellation; the operator cannot use it |
| `SupportGrant` | **Built**, configuration-only (§5); full-read access deferred |
| Resending an invitation | **Built** — only while the administrator has never signed in; after that, recovery is the customer's |

**Three writers, one mapping.** `Organization.status` is written in exactly
three places: `subscriptions._apply_to_organization`, when commercial state
moves; `finish_setup`, when a customer leaves `pending_setup`; and
`lifecycle.archive_organization`, the one transition with no commercial
counterpart. `finish_setup` asks the subscriptions module where to land
(`subscriptions.status_after_setup`) rather than deciding for itself, so a
customer who finishes setup during a trial is on `trial`, not `active` with a
`trialing` subscription.


The boundary was built first on purpose. Everything above adds routes to the
platform tree, and adding them to a tree whose entry rule is already enforced
at build time is a different proposition from adding them and then trying to
secure them.

---

## 8. The end of a customer

```
CANCELLED --(90 days: export window)--> ARCHIVED --(365 days)--> purged
```

Neither step is automatic, and nothing removes a customer in one call.

**Archive** is a console action with a required reason, refused until the
customer's export window has closed. It changes the status and nothing else.

**Purge** is the only hard delete in the product, and it has one door:

```bash
python manage.py purge_organization <slug> --dry-run
python manage.py purge_organization <slug> --confirm <slug>
```

There is no API route and no console button for it; a test asserts no URL
contains "purge". It is refused unless the organization has been archived for
a year, and runs as one transaction — everything or nothing.

| | |
|---|---|
| **Removed** | Every row of the 94 organization-owned models; the organization's settings, email configuration and memberships; its users' logins; its files under `MEDIA_ROOT/organizations/<uuid>/` (after commit) |
| **Kept** | The organization row, as a tombstone with `purged_at`; the subscription; the audit trail |
| **Audit trail** | Every row kept — who, what, which entity, when — with its payloads scrubbed: before/after snapshots, labels, reasons, IP, user agent and customer users' addresses are blanked. One terminal `organization_purged` record holds counts, not people |
| **Spared** | A login that submitted or verified a deployment-wide statutory rate set is deactivated with an unusable password instead of deleted: that four-eyes record belongs to every customer, and the purge output names such logins |

How: every foreign key in this schema is `DEFERRABLE INITIALLY DEFERRED`, so the
rows are deleted table by table with plain SQL inside one transaction and
Postgres checks every reference once, at commit. A reference that would dangle
fails the commit and rolls the whole purge back.
