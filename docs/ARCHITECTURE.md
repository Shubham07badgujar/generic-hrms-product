# HRMS — Architecture & Database Design Document (FINAL)

**Status:** For approval. No code written or modified until signed off.

**One-line architecture:**
Single Organization → React + TypeScript → Django + DRF → PostgreSQL → RBAC → Role-Based Workflows → HR-Controlled Employee Lifecycle → Department-Based Recruitment → HR Final Decisions → Finance/Payroll → Assets → BI → Audit Logs

---

# PART 0 — Final Decisions & Their Consequences

## 0.1 The eight decisions

| # | Decision | Consequence |
|---|---|---|
| 1 | **Multi-tenancy removed** | No `organization` FK anywhere. Delete `common/tenancy.py`, `TenantManager`, `CurrentOrgMiddleware`, `drf_tenancy.py`, `use_org()`. Org-switching never built. |
| 2 | **Recruiter ≠ Payroll Executive** | Two distinct roles, disjoint permission sets. |
| 3 | **Employee-first creation** | HR Head creates Employee + Department + Designation + Reporting Manager + Role + Login in **one atomic transaction**. Partial accounts structurally impossible. CEO/Admin are system-level exceptions with no Employee record. |
| 4 | **CEO exclusive, view-only** | Cannot combine with any operational role. Read + export only, everywhere. |
| 5 | **Two-level rejection** | Department Head recommends/rejects → routed to HR Head → HR Head performs the final rejection with a mandatory reason. Admin override is a separate, explicitly-labelled, fully-audited action. |
| 6 | **New auth architecture, no dual auth** | JWT only. All HTMX/session/template code deleted. No transition period running both. |
| 7 | **Polling notifications at launch** | No Channels/WebSockets initially; the notification layer is designed so a WS transport drops in later without rework. |
| 8 | **Fresh PostgreSQL database** | Old schema, orgs, admins, users, hierarchy and workflow data all discarded. New schema from scratch. Admin/CEO recreated via the bootstrap process. |

## 0.2 Two decisions that materially reduce risk

**Removing multi-tenancy eliminates your worst prior production defect.** Incident HRMS-INC-20260717-01 happened because `CurrentOrgMiddleware` set tenant context at Django-middleware time, before DRF resolved a token-authenticated user — so `current_org()` stayed unset, `TenantManager` failed *open*, and a user in one organization approved and locked another organization's payroll run. Moving a React SPA to JWT would have reintroduced exactly that ordering problem on every request. **With a single organization there is no tenant context to establish, no fail-open manager, and no ordering trap.** The entire `drf_tenancy` mixin and its two guard test-suites become unnecessary.

**A fresh database removes the migration-drift blocker.** The live DB currently does not match its own models — `makemigrations --check` reports unmigrated changes in `onboarding`, `payroll`, `policies` and `recruitment`. Reconciling that was a prerequisite phase. It no longer exists.

## 0.3 What is harvested from the old codebase

This is a new Django project. Three things are **ported as code** (de-tenanted), because they encode correctness that would be expensive and dangerous to re-derive:

| Asset | Source | Why |
|---|---|---|
| **India statutory payroll engine** | `apps/payroll/compute.py` (452 lines) | PF (12/12, EPS 8.33%, ₹15k ceiling, admin charge), ESI (0.75/3.25, ₹21k threshold), per-state Professional Tax slabs including the Maharashtra February ₹300 special case, Gratuity 15/26, TDS old-vs-new regime with 80C/80D/HRA/24b caps and cess. Errors here mean incorrect pay and statutory breach. |
| **Payroll golden-master tests** | `tests/test_payroll_compute.py` | Per-state PT golden master. **Ported first**, and the new engine must reproduce it exactly. |
| **Statutory seed data** | `apps/payroll/management/commands/seed_statutory.py` | PF/ESI/PT(MH,KA)/Gratuity/income-tax slabs from 2024-04-01. |

Two more are ported as **patterns**, rewritten: the audit signal registry with per-model field redaction (`apps/audit/`), and encrypted PII fields with masking (`common/encrypted_fields.py`).

Everything else — templates, HTMX views, context processors, the `Roles` alias tangle, `ModulePermission`, `OrgAccess`, the Django `Group` mirror — is discarded.

## 0.4 Deployment constraint (unchanged, non-negotiable)

The reference deployment runs behind a reverse proxy shared with unrelated services; the stack therefore deploys as its own compose project and NEVER writes to a shared proxy's config. If your host's proxy is shared, add the two route blocks (SPA + API) by hand and keep them out of this repo's deploys.

---

# PART 1 — System Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ React 18 + TypeScript SPA         (built to static, Caddy)    │
│  AuthProvider · PermissionProvider (GET /me/permissions)       │
│  <ProtectedRoute resource action> · <Can resource action>      │
│  TanStack Query · React Hook Form + Zod · Recharts             │
│  Notification poller (swappable for WS later)                  │
└──────────────────────────┬────────────────────────────────────┘
                           │ REST + JWT (Bearer)
┌──────────────────────────▼────────────────────────────────────┐
│ Django 5 + Django REST Framework                              │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ core/access/ — THE single authorization authority       │  │
│  │  AccessContext · can() · require() · scope_queryset()   │  │
│  │  RBACPermission · ScopedModelViewSet                    │  │
│  │  System check: boot fails on any unmapped view          │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ViewSet → Serializer → services.py → Model                   │
│  core/workflow/ (configurable pipelines) · core/bi/ (metrics) │
└──────────────────────────┬────────────────────────────────────┘
    PostgreSQL 16 · Redis (cache + Celery broker)
    Celery: payroll · email · BI rollups · interview reminders · DPDP purge
    S3-compatible storage: resumes, documents, letters, payslip PDFs
```

## 1.1 Core principle — authorization returns a *scope*, not a boolean

```python
class Scope(models.IntegerChoices):
    NONE = 0        # no access
    SELF = 1        # own records only
    TEAM = 2        # own reporting tree
    DEPARTMENT = 3  # own department + descendants
    ALL = 4         # whole organization
```

`NONE == 0`, so `if can(...)` reads naturally while carrying the breadth needed to filter querysets. **One resolved value answers both "may they?" and "how much?"** — replacing the old system's 78 scattered inline role checks across 25 files, which had no central chokepoint and two permission engines wired to nothing.

```python
def scope_queryset(qs, user, *, resource, action=Action.VIEW):
    """The single sanctioned path to a scoped queryset."""
    scope = get_context(user).scope_for(resource, action)
    if not scope:                 return qs.none()
    if scope == Scope.ALL:        return qs
    if scope == Scope.DEPARTMENT: return qs.filter(**{f"{path}__in": ctx.department_ids})
    if scope == Scope.TEAM:       return qs.filter(**{f"{path}__in": ctx.reporting_tree_ids()})
    return qs.filter(**{path: ctx.employee_id})
```

## 1.2 Authentication

`djangorestframework-simplejwt`. **No session auth, no HTMX, no dual mode.**

- Access token 15 min, held **in memory** (never `localStorage`)
- Refresh token 7 days in an **httpOnly, Secure, SameSite=Strict** cookie, rotated on use, blacklisted on reuse detection
- `POST /api/v1/auth/login/` · `/login/admin/` · `/refresh/` · `/logout/`
- **`/login/admin/`** — hardened admin entrance, rate-limited 5/5min. After authentication, a user lacking `admin` gets the *generic* "Invalid email or password" (no role-existence signal)
- **No public signup.** Accounts exist only via bootstrap or HR-controlled creation
- **Bootstrap creates the Admin account only.** `manage.py bootstrap_admin` **and** a Postman endpoint that exists only when `ADMIN_BOOTSTRAP_TOKEN` is set (unset ⇒ genuine 404, not 403), IP-allowlisted, `hmac.compare_digest`, one-shot, fully audited, **no password in the request body** — the account is created with `set_unusable_password()` and the password is set manually in the database, per your requirement. A leaked token therefore yields a login-less account.
- **The CEO account is created afterwards by Admin**, through the normal user-management UI. `ceo` is `is_grantable=True` and `requires_employee=False`, so Admin can issue it without an Employee record. It remains exclusive — `UserRole.clean()` refuses to combine it with any other role.

---

# PART 2 — Roles, Permissions & Visibility

## 2.1 Department structure

```
DepartmentKind: MEDICAL | OPERATIONS | HR | FINANCE | OTHER
```
`Department` has `parent_department` (self-FK, cycle-guarded), `head_employee`, `kind`. Department scope resolves to the department **plus all descendants**. `kind` drives dashboard/BI grouping and role seeding — **never authorization**, which comes from `Employee.department`.

## 2.2 The 18 roles

| Code | Name | Layer | Default scope | Read-only | Creates users | Needs Employee |
|---|---|---|---|---|---|---|
| `ceo` | CEO | 1 | ALL | ✔ | ✘ | ✘ |
| `admin` | Admin | 1 | ALL | ✘ | ✔ | ✘ |
| `medical_director` | Medical Director | 2 | DEPARTMENT | ✘ | ✘ | ✔ |
| `operational_head` | Operational Head | 2 | DEPARTMENT | ✘ | ✘ | ✔ |
| `hr_head` | HR Head | 2 | DEPARTMENT* | ✘ | **✔** | ✔ |
| `finance_head` | Accounts & Finance Head | 2 | DEPARTMENT | ✘ | ✘ | ✔ |
| `senior_doctor` | Senior Doctor | 3 | TEAM | ✘ | ✘ | ✔ |
| `operations_manager` | Operations Manager | 3 | TEAM | ✘ | ✘ | ✔ |
| `hr_manager` | HR Manager | 3 | DEPARTMENT | ✘ | ✔ delegated | ✔ |
| `accounts_manager` | Accounts Manager | 3 | DEPARTMENT | ✘ | ✘ | ✔ |
| `clinic_doctor` | Clinic Doctor | 4 | SELF | ✘ | ✘ | ✔ |
| `cre` | CRE | 4 | SELF | ✘ | ✘ | ✔ |
| `recruiter` | Recruiter | 4 | DEPARTMENT | ✘ | ✘ | ✔ |
| `payroll_executive` | Payroll Executive | 4 | DEPARTMENT | ✘ | ✘ | ✔ |
| `executive` | Executive | 4 | SELF | ✘ | ✘ | ✔ |
| `therapist` | Therapist | 5 | SELF | ✘ | ✘ | ✔ |
| `office_boy` | Office Boy | 5 | SELF | ✘ | ✘ | ✔ |
| `employee` | Employee | 5 | SELF | ✘ | ✘ | ✔ |

\* `hr_head` holds `EMPLOYEE/VIEW` at DEPARTMENT but `USER/CREATE` at ALL — HR hires into every department. That asymmetry is exactly what the `(resource, action) → scope` grain buys.

`admin` is `is_grantable=False` — bootstrap only, never granted in-app.

### Recruiter vs Payroll Executive (decision 2)

| | Recruiter | Payroll Executive |
|---|---|---|
| Resources | `JOB_OPENING`, `CANDIDATE`, `APPLICATION`, `INTERVIEW`, `INTERVIEW_FEEDBACK`(view), `CANDIDATE_COMMUNICATION` | `PAYROLL_RUN`, `PAYSLIP`, `SALARY_STRUCTURE`, `PAYROLL_ADJUSTMENT`, `PAYROLL_REPORT` |
| Can | Create/publish openings, manage applications, screen candidates, coordinate and schedule interviews, manage the pipeline, send candidate communication | Process payroll, maintain salary structures, manage earnings/deductions, generate payslips, produce payroll reports |
| Cannot | Reject candidates (HR Head only). Touch payroll at all. | Approve payroll runs (Finance Head/Admin only). Touch recruitment at all. |

Permission sets are **disjoint** — no resource appears in both. One person able to both hire someone and pay them is a segregation-of-duties violation.

## 2.3 Contradiction resolutions — one consistent model

| Apparent conflict | Resolution |
|---|---|
| CEO "monitors activities" vs "view-only" | CEO holds `VIEW` + `EXPORT` on every resource and **zero** write actions. Monitoring = read + export + BI. Enforced four independent ways (§3.4). |
| Layer 2 "cannot manage users" vs HR Head managing users | `can_manage_users` is a **role flag, not a layer property**. Only `admin`, `hr_head`, and delegated `hr_manager` carry it. Medical Director, Operational Head and Finance Head do not. |
| Interviewers "recommend rejection" vs HR Head "final rejection" | Interviewer `recommendation` is advisory metadata on `InterviewFeedback`. It never changes application state. |
| Department Head "can reject" vs HR Head "performs rejection" | Department Head records a **`DepartmentDecision`** (`RECOMMEND_REJECT` / `RECOMMEND_PROCEED` / `HOLD`) with mandatory rationale, which **routes to HR Head**. Only `hr_head` holds `CANDIDATE/REJECT` and executes the terminal state change. |
| Admin "full access" vs HR-Head-only rejection | Admin does **not** hold `CANDIDATE/REJECT`. Admin holds a distinct `CANDIDATE/OVERRIDE_DECISION` permission, surfaced in the UI as a clearly-labelled administrative override with a mandatory reason and full audit. |
| Finance visibility vs employee privacy | `SALARY/VIEW` at DEPARTMENT for finance roles, SELF for everyone else. `PAYROLL_RUN/APPROVE` only `finance_head` + `admin`. |
| Asset ownership (no IT role exists) | `ASSET/*` to `admin`, `hr_head`, delegated `hr_manager`. Employees get `ASSET/VIEW` at SELF. |
| Company email accounts | `EMAIL_ACCOUNT/*` only `admin`, `hr_head`, delegated `hr_manager`. |

## 2.4 Visibility matrix (abridged; the full ~250-cell matrix is a Phase-1 sign-off deliverable)

| Resource | CEO | Admin | Med Dir | Ops Head | HR Head | Fin Head | L3 | L4 | L5 |
|---|---|---|---|---|---|---|---|---|---|
| Employee | ALL (r) | ALL | DEPT | DEPT | ALL | DEPT | DEPT/TEAM | SELF | SELF |
| Salary / Payslip | ALL (r, agg) | ALL | — | — | DEPT (r) | DEPT | SELF¹ | SELF² | SELF |
| Candidate | ALL (r) | ALL | DEPT (r) | DEPT (r) | ALL | — | DEPT³ | assigned⁴ | — |
| Interview feedback | ALL (r) | ALL | DEPT | DEPT | ALL | — | own+DEPT | own | — |
| **Dept decision** | ALL (r) | ALL | **✔ own dept** | **✔ own dept** | ALL (r) | ✘ | ✘ | ✘ | ✘ |
| **Final rejection** | ✘ | override⁵ | ✘ | ✘ | **✔ only** | ✘ | ✘ | ✘ | ✘ |
| Asset | ALL (r) | ALL | DEPT (r) | DEPT (r) | ALL | — | TEAM (r) | SELF | SELF |
| Email account | ALL (r) | ALL | ✘ | ✘ | ALL | ✘ | delegated | ✘ | SELF (r) |
| Payroll run | ALL (r) | ALL | ✘ | ✘ | ✘ | **approve** | acct_mgr: process | payroll_exec: process | — |
| Audit log | ALL (r) | ALL | DEPT (r) | DEPT (r) | DEPT (r) | DEPT (r) | ✘ | ✘ | ✘ |
| Roles / settings | ✘ | ALL | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ | ✘ |

(r) read-only · ¹ `accounts_manager` DEPT · ² `payroll_executive` DEPT · ³ HR Manager only · ⁴ only candidates on interviews assigned to them · ⁵ `OVERRIDE_DECISION`, not `REJECT`.

---

# PART 3 — Backend Architecture

## 3.1 Project layout

```
config/            settings/{base,dev,prod}.py · urls.py · celery.py
core/
  access/          catalog.py · context.py · engine.py · drf.py · checks.py · middleware.py
  workflow/        engine.py · resolvers.py         (configurable recruitment pipelines)
  bi/              registry.py · hr.py · recruitment.py · finance.py · operations.py
  models.py        BaseModel (UUID pk, timestamps, created_by/updated_by, soft delete)
  fields.py        EncryptedCharField + masking helpers
  policy.py        effective-dated resolver
apps/
  accounts/        User, Role, RolePermission, UserRole, UserPermissionOverride
  organization/    Department, Designation, Location, EmployeeLevel, Team, OrgSettings
  employees/       Employee + profile sub-models, documents, lifecycle
  assets/          AssetCategory, Asset, AssetAllocation, AssetMaintenanceLog
  itaccounts/      CompanyEmailAccount, CredentialHandoff
  recruitment/     JobOpening, Candidate, Application, Interview, InterviewFeedback,
                   DepartmentDecision, CandidateRejection, CandidateDecisionOverride
  workflows/       HiringWorkflow, WorkflowStage, TransitionRule, InterviewerSpec,
                   FeedbackForm/Field, ApprovalGate
  onboarding/      templates, items, letters
  offboarding/ attendance/ leave/ payroll/ policies/
  notifications/   Notification, Preference, Delivery
  audit/           AuditLog + signal registry
  reporting/       MetricSnapshot, exports
```

**Strict layering:** ViewSet → Serializer → `services.py` → Model. All business logic lives in services; no logic in views or serializers.

**Note there is no `tenancy.py`, no `TenantManager`, no `drf_tenancy.py`.** Single-organization means every queryset starts unfiltered and is narrowed only by `scope_queryset()`.

## 3.2 The access engine — `core/access/`

```python
@dataclass(frozen=True)
class AccessContext:
    user_id
    role_codes: frozenset[str]; layers: frozenset[int]; min_layer: int
    read_only: bool; can_manage_users: bool
    employee_id: UUID | None
    department_id: UUID | None
    department_ids: frozenset      # department + descendants
    grants: Mapping[tuple[str, str], int]
    dashboard_key: str
    def scope_for(self, resource, action) -> Scope: ...
    def reporting_tree_ids(self) -> frozenset: ...   # lazy, memoized once per request
```

**Resolution order — fail closed at every step:**
1. Anonymous or inactive → `DENY_ALL`
2. Load active `UserRole` rows with `select_related("role")`
3. No roles → `DENY_ALL` (a user with no role has no access, period)
4. Resolve `employee_id` + `department_id`; compute department closure (cycle-safe BFS)
5. Aggregate `RolePermission` — **MAX scope wins** across roles
6. Apply `UserPermissionOverride` — replaces the role-derived scope (widen *or* explicitly deny); unexpired only
7. **No linked Employee and `requires_employee`** → grants cleared. Roles flagged `requires_employee=False` (`admin`, `ceo`) keep only `Scope.ALL` — a system role can never resolve a department or team scope it has no basis for
8. **Read-only clamp** — strip every write action, unconditionally, last
9. **User-management gate** — strip `USER`/`ROLE`/`INVITATION` resources unless `can_manage_users`

Cached per request; invalidated on any write to `UserRole`, `RolePermission` or `UserPermissionOverride`.

## 3.3 DRF integration

```python
class ScopedModelViewSet(ScopedQuerysetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, RBACPermission]
    access_resource = None                      # required — boot fails without it
    access_actions = {"list": Action.VIEW, "retrieve": Action.VIEW,
                      "create": Action.CREATE, "update": Action.EDIT,
                      "partial_update": Action.EDIT, "destroy": Action.DELETE}
    def get_queryset(self):
        return scope_queryset(self.queryset, self.request.user,
                              resource=self.access_resource,
                              action=self._action_for(self.action))
```
`RBACPermission` **fails closed** — a view with neither `access_resource` nor `access_exempt = True` is denied and logged at ERROR.

## 3.4 CEO read-only — four independent mechanisms

1. Engine clamp (step 8), applied after roles and overrides
2. `ReadOnlyPrincipalMiddleware` — blanket rejection of `POST/PUT/PATCH/DELETE` for read-only principals, covering any route nobody remembered to map
3. DB `CheckConstraint(~Q(is_read_only=True, can_manage_users=True))` on `Role`
4. `require()` at the top of every mutating service function

Plus `UserRole.clean()`: **a read-only role cannot be combined with any other role** (decision 4). This is what makes `ctx.read_only` unambiguous.

## 3.5 Coverage is mechanical, not disciplinary

`core/access/checks.py` registers a Django system check that walks the URL resolver and **fails `manage.py check`** for any API view that is neither RBAC-mapped nor explicitly exempt. This converts "we remembered on every endpoint" into a build-time guarantee — the single biggest structural failure of the previous system.

---

# PART 4 — Database Design (fresh PostgreSQL schema)

Conventions: UUID primary keys via `BaseModel` · `created_at` / `updated_at` / `created_by` / `updated_by` · soft delete (`is_active`) on domain tables · `NUMERIC(12,2)` money, never float · **no `organization` column anywhere** · every FK indexed.

## 4.1 Organization singleton & structure

```python
class OrgSettings(BaseModel):          # singleton row, pk enforced == 1
    name; legal_name; gstin; pan; tan; cin; epf_number; esi_number
    financial_year_start_month = 4; currency = "INR"; timezone = "Asia/Kolkata"
    working_days JSON; standard_start_time; standard_end_time
    logo; signatory_name; signatory_designation; employee_id_prefix

class Department(BaseModel):
    name; code UNIQUE; kind: MEDICAL|OPERATIONS|HR|FINANCE|OTHER
    parent_department FK(self, null); head_employee FK(Employee, null)
class Designation(BaseModel):  title; department FK(null)     # UNIQUE(title, department)
class Location(BaseModel):     name; code UNIQUE; city; state; pincode; timezone; is_head_office
class EmployeeLevel(BaseModel): name; code UNIQUE; rank
class Team(BaseModel):         name; code UNIQUE; department FK; parent_team FK(null); head_employee FK
```

## 4.2 Identity & RBAC

```python
class User(AbstractBaseUser, PermissionsMixin):
    email CITEXT UNIQUE (USERNAME_FIELD); first_name; last_name
    is_active; is_staff; must_change_password; last_login_at
    failed_login_count; locked_until; mfa_secret(null)

class Role(BaseModel):
    code SlugField UNIQUE; name CharField UNIQUE; layer PositiveSmallInt
    is_read_only; can_manage_users; requires_employee; is_grantable; is_system
    department_kind(blank); dashboard_key; max_seats(null)
    constraints = [CheckConstraint(~Q(is_read_only=True, can_manage_users=True))]

class RolePermission(BaseModel):
    role FK; resource CharField(40); action CharField(20)
    scope PositiveSmallInt (Scope); is_customized Bool
    # UNIQUE(role, resource, action). Absence == deny → ~250 rows total.

class UserRole(BaseModel):
    user FK; role FK(PROTECT); assigned_by FK
    # UNIQUE(user, role); clean(): read-only role cannot combine with any other

class UserPermissionOverride(BaseModel):
    user FK; resource; action; scope       # scope=NONE means explicit deny
    reason CharField(255)  # required
    granted_by FK(PROTECT); expires_at(null)
    # UNIQUE(user, resource, action)

class RefreshTokenRecord(BaseModel):
    user FK; token_hash; family_id; expires_at; revoked_at; user_agent; ip
```

## 4.3 Employees

```python
class Employee(BaseModel):
    employee_code UNIQUE; user OneToOne(User, null)
    first_name; middle_name; last_name; personal_email; work_email; phone
    dob; gender; marital_status; photo
    pan ENCRYPTED; aadhaar ENCRYPTED; uan; esic_number
    bank_account_number ENCRYPTED; bank_ifsc; bank_name
    department FK(PROTECT); designation FK; location FK(PROTECT); level FK(PROTECT); team FK(null)
    reporting_manager FK(self, null, related_name="direct_reports")
    employment_type; date_of_joining; probation_end_date; confirmation_date
    date_of_exit; notice_period_days
    status: ACTIVE|ON_NOTICE|ON_LEAVE|EXITED
    created_from_candidate FK(Candidate, null)
    # Index(department), Index(reporting_manager), Index(status)

class EmployeeAddress / EmployeeEmergencyContact / EmployeeEducation / EmployeeExperience
    # normalized tables, not JSON blobs
class EmployeeDocument(BaseModel):
    employee FK; document_type FK; file; file_name; mime_type; size_bytes
    expires_on; verified_by FK; verified_at; notes

class ProbationReview(BaseModel):
    """HR's decision at the end of probation. The system NEVER auto-confirms."""
    employee FK(related_name="probation_reviews")
    probation_end_date                      # snapshot of the date under review
    decision: PENDING | CONFIRMED | EXTENDED | TERMINATED
    decided_by FK(User, null, PROTECT); decided_at(null)
    rationale TextField(blank)              # required for EXTENDED and TERMINATED
    extended_to DateField(null)             # required when decision == EXTENDED
    confirmation_letter FK(EmployeeLetter, null)   # auto-generated on CONFIRMED
    notified_30d; notified_7d; notified_overdue    # reminder idempotency flags
    # Index(decision, probation_end_date)
```

## 4.4 Assets

```python
class AssetCategory(BaseModel):  name; code UNIQUE; requires_serial
class Asset(BaseModel):
    asset_tag UNIQUE; category FK; name; asset_type; serial_number; make; model
    purchase_date; purchase_cost; warranty_expires_on; vendor FK(null); location FK(null)
    status: AVAILABLE|ALLOCATED|IN_USE|RETURNED|MAINTENANCE|RETIRED|LOST
    condition: NEW|GOOD|FAIR|DAMAGED|UNUSABLE

class AssetAllocation(BaseModel):
    asset FK; employee FK
    allocated_at; allocated_by FK; condition_at_allocation; allocation_notes
    expected_return_date(null)
    returned_at(null); received_by FK(null); condition_at_return(null); return_notes
    status: ACTIVE|RETURNED|OVERDUE|LOST|WRITTEN_OFF
    constraints = [UniqueConstraint(fields=["asset"], condition=Q(status="ACTIVE"),
                                    name="one_active_allocation_per_asset")]

class AssetMaintenanceLog(BaseModel): asset FK; event; performed_at; performed_by; cost; notes
```
Lifecycle `AVAILABLE → ALLOCATED → IN_USE → RETURNED → (AVAILABLE | MAINTENANCE | RETIRED)`, enforced in `assets/services.py`; illegal transitions raise. Allocation rows are closed, never destructively updated, so history is complete. **Exit integration:** the exit checklist auto-generates a line per `ACTIVE` allocation and the exit cannot be finalised while any remain unreturned unless explicitly written off (audited).

**V1 is manual allocation.** HR Head or an authorized delegate allocates each asset explicitly; nothing is auto-assigned on hire. The architecture reserves the seam for designation-based standard kits — a future `DesignationAssetKit(designation, category, quantity)` table would feed `hire_candidate()` with a *suggested* allocation list that HR confirms. Because allocation already flows through a single `assets.services.allocate()` entry point, adding kits later changes one caller, not the model or the API.

## 4.5 Company email accounts

```python
class CompanyEmailAccount(BaseModel):
    employee OneToOne; email_address CITEXT UNIQUE; provider
    status: REQUESTED|PROVISIONING|ACTIVE|SUSPENDED|DEPROVISIONED
    requested_by FK; requested_at; assigned_by FK; activated_at
    suspended_at; deprovisioned_at; external_account_id; notes

class CredentialHandoff(BaseModel):
    email_account FK; delivery_token_hash CharField(128)   # hash only, never the secret
    created_by FK; expires_at; viewed_at(null); viewed_ip(null)
    status: PENDING|VIEWED|EXPIRED|REVOKED
```

**No plain-text credential is ever stored or returned.** The initial password is encrypted with a key derived from the one-time delivery token and held **in Redis with a hard TTL**, never in PostgreSQL. The employee opens a one-time link; the secret displays once, the Redis key is deleted, `viewed_at`/`viewed_ip` recorded. **No API response — including the full employee profile — contains a credential field, and no endpoint returns one.** Every request, view, expiry and revocation is audited. `external_account_id` reserves the seam for future Google Workspace / Microsoft Graph integration via **service-account delegation, not stored user passwords**.

## 4.6 Configurable recruitment workflow engine

```python
class HiringWorkflow(BaseModel):
    name; description; department FK(null); is_default; version; is_published
class WorkflowStage(BaseModel):
    workflow FK; name; order
    kind: SCREENING|INTERVIEW|DEPARTMENT_DECISION|APPROVAL|OFFER|ONBOARDING|TERMINAL
    is_terminal; is_won; sla_days(null); auto_advance_on_pass; auto_reject_on_fail
    # UNIQUE(workflow, order)
class StageTransitionRule(BaseModel):
    workflow FK; from_stage FK; to_stage FK; requires_role FK(null)
    # UNIQUE(from_stage, to_stage) — replaces "any stage → any stage, including backwards"
class StageInterviewerSpec(BaseModel):
    stage FK; role FK(null); employee FK(null); order; is_required; feedback_form FK(null)
    # CheckConstraint: exactly one of role/employee
class StageApprovalGate(BaseModel):
    stage FK; approver_role FK(null); approver_employee FK(null); order; is_required
class FeedbackForm(BaseModel):  name; description; pass_threshold(null)
class FeedbackField(BaseModel):
    form FK; label; help_text; order
    kind: RATING_1_5|BOOLEAN|TEXT|CHOICE|SCORE_0_100
    choices JSON; weight; is_required; is_knockout; knockout_value
```

## 4.7 Recruitment

```python
class JobOpening(BaseModel):
    title; department FK; designation FK; location FK; level FK
    workflow FK(PROTECT)                       # per-position pipeline
    employment_type; description; responsibilities; requirements; openings_count
    min_experience; max_experience; salary_min; salary_max
    status: DRAFT|OPEN|ON_HOLD|CLOSED|FILLED
    hiring_manager FK; recruiter FK
    apply_token UUID UNIQUE; apply_token_expires_at
class ScreeningQuestion(BaseModel):
    job_opening FK; kind; text; choices JSON; is_knockout; knockout_value; weight; order
class Candidate(BaseModel):
    first_name; last_name; email CITEXT; phone; dedup_hash UNIQUE
    current_company; current_designation; total_experience; current_ctc; expected_ctc
    notice_period_days; resume_file; parsed_data JSON; skills JSON; source
    consent_given; consent_at                           # DPDP Act 2023
    final_decision_at DateTimeField(null)               # set on hire OR final rejection
    retention_until DateField(null)                     # = final_decision_at + 12 months
    purge_status: RETAINED | ELIGIBLE | PURGE_APPROVED | PURGED
    purge_approved_by FK(null); purge_approved_at(null); purged_at(null)
    # Index(purge_status, retention_until)
class Application(BaseModel):
    candidate FK; job_opening FK; current_stage FK; status
    applied_at; source; assigned_recruiter FK
    overall_score; screening_score; failed_knockout
    # UNIQUE(candidate, job_opening)
class ApplicationStageHistory(BaseModel):
    application FK; from_stage(null); to_stage; moved_by FK; moved_at; reason; duration_days
class Interview(BaseModel):
    application FK; stage FK; interviewer_spec FK(null); interviewer FK(Employee)
    round_number; scheduled_at; duration_minutes; mode; location_or_link; status
    scheduled_end  = GeneratedField(scheduled_at + duration_minutes)   # stored, for overlap checks
    schedule_token UUID UNIQUE; token_expires_at; reminder_24h_sent; reminder_1h_sent
    # UNIQUE(application, round_number)
    # Index(interviewer, scheduled_at, scheduled_end)   ← drives conflict detection
class InterviewSlot(BaseModel): interview FK; starts_at; ends_at; is_selected
class InterviewFeedback(BaseModel):
    interview OneToOne; form FK; submitted_by FK; submitted_at
    total_score; passed(null)
    recommendation: STRONG_HIRE|HIRE|HOLD|NO_HIRE      # ADVISORY — never changes state
    comments
class InterviewFeedbackAnswer(BaseModel):
    feedback FK; field FK; value; passed_knockout      # UNIQUE(feedback, field)
class ApprovalRequest(BaseModel):
    application FK; gate FK; assigned_to FK; status; decided_at; decided_by FK; note
```

## 4.8 Two-level candidate rejection (decision 5)

```python
class RejectionReasonCategory(BaseModel):
    code UNIQUE; label; is_active
    # seeded: INSUFFICIENT_EXPERIENCE, SKILL_MISMATCH, FAILED_INTERVIEW,
    #         COMPENSATION_MISMATCH, FAILED_BACKGROUND_CHECK, CANDIDATE_WITHDREW,
    #         POSITION_CLOSED, DUPLICATE, OTHER

# ── LEVEL 1 — Department Head ────────────────────────────────────────────
class DepartmentDecision(BaseModel):
    application FK(related_name="department_decisions")
    department FK; stage FK
    decided_by FK(User, PROTECT)          # must hold DEPARTMENT_DECISION/CREATE
    decided_at
    decision: RECOMMEND_REJECT | RECOMMEND_PROCEED | HOLD
    reason_category FK(null)              # required when RECOMMEND_REJECT
    rationale TextField()                 # MANDATORY, >= 20 chars
    interview_summary JSON                # snapshot of departmental feedback at decision time
    routed_to_hr_at; hr_acknowledged_at(null)
    constraints = [CheckConstraint(check=Q(rationale__regex=r"\S{20,}"),
                                   name="ck_deptdecision_rationale_min_length")]
    # Index(application, decided_at)

# ── LEVEL 2 — HR Head, terminal ──────────────────────────────────────────
class CandidateRejection(BaseModel):
    application OneToOne(PROTECT); candidate FK(PROTECT)
    department_decision FK(null, PROTECT)     # the recommendation this finalises
    rejected_by FK(User, PROTECT)             # must hold CANDIDATE/REJECT → hr_head only
    rejected_at; rejection_stage FK(WorkflowStage, PROTECT)
    reason_category FK(PROTECT)
    reason_text TextField()                   # MANDATORY, >= 20 chars
    interview_summary JSON                    # full snapshot: all feedback + dept decision
    candidate_notified; notified_at
    is_overridden Bool(False)
    constraints = [CheckConstraint(check=Q(reason_text__regex=r"\S{20,}"),
                                   name="ck_rejection_reason_min_length")]

# ── EXCEPTIONAL — Admin override ─────────────────────────────────────────
class CandidateDecisionOverride(BaseModel):
    application FK; rejection FK(null)
    overridden_by FK(User, PROTECT)           # must hold CANDIDATE/OVERRIDE_DECISION → admin
    overridden_at
    previous_decision CharField; new_decision CharField
    reason TextField()                        # MANDATORY, >= 20 chars
    constraints = [CheckConstraint(check=Q(reason__regex=r"\S{20,}"),
                                   name="ck_override_reason_min_length")]
```

**Rules, enforced in `recruitment/services.py`:**
- `DEPARTMENT_DECISION/CREATE` seeded to `medical_director` and `operational_head` at DEPARTMENT scope — each can only act on applications whose `job_opening.department` is within their own department closure.
- `CANDIDATE/REJECT` seeded **only** to `hr_head`. Department Heads and interviewers cannot reach the terminal state.
- Both `rationale` and `reason_text` are mandatory, ≥20 characters, enforced at **DB CheckConstraint + serializer + UI** — three layers so no client can bypass it.
- `interview_summary` snapshots feedback at decision time so justification survives later edits.
- HR Head sees a dedicated queue of pending `DepartmentDecision` rows with `decision=RECOMMEND_REJECT`; each carries the department rationale plus every interview feedback record.
- Final rejection runs one transaction: `CandidateRejection` + `Application.status=REJECTED` + `ApplicationStageHistory` + `AuditLog(action=REJECT)` + notifications to recruiter and hiring manager + optional candidate email.
- Admin override is a **separate action, separate permission, separate table**, surfaced in the UI with explicit "Administrative Override" labelling and a confirmation dialog stating that the action is recorded. It records who, when, why, and both the previous and new decision.

## 4.9 Onboarding, letters, offboarding

```python
class DocumentType(BaseModel):     name; code UNIQUE; category; is_mandatory
class OnboardingTemplate(BaseModel): name; department FK(null); is_default
class OnboardingTemplateItem(BaseModel):
    template FK; kind: DOCUMENT|TASK; document_type FK(null); title
    is_mandatory; assigned_to_role FK(null); order
class EmployeeOnboarding(BaseModel):
    employee FK; template FK; joining_date; status; completed_at
class OnboardingItem(BaseModel):
    onboarding FK; kind; document_type FK(null); title
    status: PENDING|SUBMITTED|VERIFIED|WAIVED|REJECTED
    file; submitted_at; verified_by FK; verified_at; notes; order
class LetterTemplate(BaseModel): letter_type; name; subject; body_html; is_default
class EmployeeLetter(BaseModel):
    employee FK(null); candidate FK(null); template FK; letter_type
    subject; body_html; merge_vars JSON; status; pdf_file; sent_at; viewed_at; accepted_at
```
**One** checklist model with a `kind` discriminator and real file upload — the old system had three disconnected checklist systems, no upload on onboarding items, and zero audit coverage on the entire onboarding app.

## 4.10 Payroll & finance

```python
class SalaryComponent(BaseModel):
    code UNIQUE; name; component_type; calc_type; percent_of_code
    is_taxable; is_part_of_ctc; is_wage; rounding; display_order
class SalaryStructure(BaseModel):
    employee FK; ctc_annual; valid_from; valid_to(null); revision_reason; approved_by FK
class SalaryStructureLine(BaseModel):
    structure FK; component FK; value; monthly_amount   # UNIQUE(structure, component)

class PayrollRun(BaseModel):
    period_month; period_year; location FK(null)
    run_type: REGULAR|OFF_CYCLE|SUPPLEMENTARY; sequence
    status: DRAFT|PROCESSING|REVIEW|APPROVED|PAID|REVERSED
    locked; run_by FK; approved_by FK; approved_at; paid_at; totals JSON; notes
    # UNIQUE(period_month, period_year, location, run_type, sequence)
class Payslip(BaseModel):
    payroll_run FK; employee FK(PROTECT); structure FK; paid_days; lop_days
    gross_earnings; total_deductions; employer_contributions; net_pay
    location FK; state; pdf_file      # UNIQUE(payroll_run, employee)
class PayslipLine(BaseModel):  payslip FK; component FK(null); label; component_type; amount; is_employer_side
class StatutoryContribution(BaseModel):
    payslip FK; kind; employee_amount; employer_amount; base_wage; state  # UNIQUE(payslip, kind)

class PayrollAdjustment(BaseModel):
    employee FK; payroll_run FK(null)        # null = queued for next run
    kind: BONUS|INCENTIVE|ARREAR|ADVANCE_RECOVERY|REIMBURSEMENT|OTHER_EARNING|OTHER_DEDUCTION
    label; amount; is_taxable; is_employer_side; period_month; period_year
    status: DRAFT|APPROVED|APPLIED|REJECTED; approved_by FK; source_ref; notes
class EmployeeLoan(BaseModel):     employee FK; principal; monthly_installment; balance; start_period; status
class ReimbursementClaim(BaseModel): employee FK; type; amount; period_month; period_year;
                                     status; bill_files JSON; approved_by FK; paid_in_run FK(null)

# Statutory config — effective-dated, single-org (no nullable-org override tier needed)
PFConfig · ESIConfig · ProfessionalTaxSlab(state) · GratuityConfig
IncomeTaxRegime · IncomeTaxSlab · InvestmentDeclaration
```

**Immutability:** once `locked=True`, only `status`, `locked`, `totals`, `notes`, `approved_by`, `approved_at`, `paid_at` may change — enforced in `PayrollRun.save()` (ported from the old two-tier allow-list) **and** a `BEFORE UPDATE` trigger, so no ORM path or manual SQL bypasses it. **Segregation of duties:** Payroll Executive / Accounts Manager **process**; only Finance Head / Admin **approve**.

Gaps closed vs the old system: bonuses, incentives, arrears, off-cycle runs (the old `UNIQUE(month, year, location)` structurally blocked them), loan EMI recovery and reimbursement payout (both were dead models never read by `compute.py`), a reachable `PAID` state, and Celery-based processing (previously synchronous in-request).

## 4.11 Attendance & leave

`Shift`, `RosterAssignment`, `HolidayCalendar`, `Holiday`, `WeeklyOffRule`, `AttendanceRecord`, `RegularizationRequest`, `LeaveType`, `LeavePolicy` (effective-dated), `LeaveBalance`, `LeaveAccrualTxn`, `LeaveRequest` — carried forward in shape, de-tenanted.

## 4.12 Notifications, audit, reporting

```python
class Notification(BaseModel):
    recipient FK(User); kind; title; body; link_url
    entity_type; entity_id; priority; is_read; read_at
    # Index(recipient, is_read, -created_at)   ← the polling query
class NotificationPreference(BaseModel): user FK; kind; in_app; email  # UNIQUE(user, kind)
class NotificationDelivery(BaseModel):   notification FK; channel; status; sent_at; error

class AuditLog(models.Model):            # plain Model — append-only sink
    actor FK(User, null); actor_email; action; resource
    entity_type; entity_id; before JSON; after JSON; metadata JSON
    ip INET; user_agent; request_id UUID; occurred_at
    # Index(occurred_at desc), Index(entity_type, entity_id), Index(actor, occurred_at desc)

class MetricSnapshot(BaseModel):
    metric_key; dimension JSON; period_start; period_end; value; computed_at
    # UNIQUE(metric_key, dimension, period_start)
```
`AuditAction`: `CREATE · UPDATE · DELETE · APPROVE · REJECT · OVERRIDE · REVERSE · ALLOCATE · RETURN · LOGIN · LOGIN_FAILED · ACCESS_PII · EXPORT · PERMISSION_CHANGE · ROLE_CHANGE · CREDENTIAL_ISSUE · CREDENTIAL_VIEW`.

Append-only enforced in `save()`/`delete()` **and** by `REVOKE UPDATE, DELETE ON audit_auditlog` from the application DB role.

---

# PART 5 — API Architecture

```
POST   /api/v1/auth/{login,login/admin,refresh,logout,forgot-password,reset-password}
GET    /api/v1/me · /me/permissions · /me/dashboard
POST   /api/v1/bootstrap/admin                     (token + IP gated; 404 when disabled)

/api/v1/{departments,designations,locations,levels,teams,org-settings}
/api/v1/{users,roles,role-permissions,permission-overrides}
POST   /api/v1/employees                           ← atomic Employee+Role+User creation
/api/v1/employees/{id}/{profile,activity,assets,documents,onboarding}

/api/v1/assets · /asset-categories
POST   /api/v1/assets/{id}/allocate · /allocations/{id}/return
/api/v1/email-accounts · POST /{id}/{activate,suspend,issue-credential}
GET    /api/v1/credential-handoff/{token}          (one-time view)

/api/v1/workflows · /workflows/{id}/stages · /feedback-forms
/api/v1/jobs · /candidates · /applications
POST   /api/v1/applications/{id}/advance
POST   /api/v1/applications/{id}/department-decision   ← DEPT HEAD
POST   /api/v1/applications/{id}/reject                ← HR HEAD ONLY
POST   /api/v1/applications/{id}/override-decision     ← ADMIN ONLY
GET    /api/v1/applications/pending-hr-decision        ← HR Head queue
GET    /api/v1/candidates/{id}/history                 (stages + interviews + decisions + rejection)
/api/v1/interviews · POST /{id}/feedback · /slots

/api/v1/onboarding · /letters · /leave · /attendance · /policies · /offboarding
/api/v1/payroll/{runs,adjustments,salary-structures,components,declarations}
POST   /api/v1/payroll/runs/{id}/{process,approve,reject,reverse,mark-paid}
GET    /api/v1/payroll/runs/{id}/{register.xlsx,neft.txt} · /payslips/{id}/pdf

GET    /api/v1/bi/{metric_key}                     (scope-aware)
GET    /api/v1/notifications?since=&unread_only=   · /notifications/unread-count
POST   /api/v1/notifications/{id}/read · /read-all
GET    /api/v1/audit
```

**Standards:** `drf-spectacular` OpenAPI → **generated TypeScript client** (single source of truth for types) · cursor pagination on all lists · error envelope `{error:{code,message,details}}` · per-scope throttles · `ScopedModelViewSet` everywhere.

**Notification polling (decision 7):** the SPA polls `/notifications/unread-count` every 30s (backing off when the tab is hidden) and fetches `/notifications?since=<cursor>` on change. The client sits behind a `useNotifications()` hook and a `NotificationTransport` interface, so swapping in a WebSocket transport later touches **one module** — no API, model or component changes.

---

# PART 6 — Frontend Architecture (React + TypeScript)

```
src/
  app/           router · providers · layouts (AppShell, AuthShell)
  features/      auth · dashboard · employees · assets · email-accounts
                 recruitment · workflows · interviews · decisions · rejections
                 onboarding · payroll · attendance · leave · reports
                 notifications · audit · settings
                   → components/ hooks/ api/ types/
  components/ui/ Button Input Select DataTable Modal Drawer Card StatCard Badge
                 Tabs Timeline EmptyState FileUpload DateRangePicker Chart
                 PageHeader Breadcrumbs ConfirmDialog OverrideDialog
  lib/           apiClient (axios + refresh interceptor) · permissions · formatters
  stores/        auth · ui · notifications
```

```tsx
<Can resource="candidate" action="reject">
  <Button variant="destructive" onClick={openRejectDialog}>Reject candidate</Button>
</Can>

<ProtectedRoute resource="payroll_run" action="view" element={<PayrollRuns />} />
```
`PermissionProvider` loads `/me/permissions` once at login and caches the grant map. **Hiding is UX only** — the API enforces independently and is the security boundary. Navigation is generated from a single `NAV_SPEC` of `{label, path, icon, resource, action}` filtered through the same grant map, so the sidebar can never drift from what the API allows.

## 6.1 Dashboards

| Route | Audience | Content |
|---|---|---|
| `/dashboard/ceo` | CEO | Org KPI strip (headcount, attrition, payroll cost, open roles) · headcount trend · department comparison · hiring funnel · payroll cost trend · attendance heat · department KPIs. **No action controls rendered anywhere.** |
| `/dashboard/admin` | Admin | CEO content + system health, user/role administration, cross-module pending approvals, workflow configuration, live audit stream, override actions |
| `/dashboard/department` | Layer 2 ×4 | Department roster (L3–L5) · department BI/KPIs · open roles · **pending department decisions** · interview queue · attendance/leave · payroll cost (finance_head only) |
| `/dashboard/manager` | Layer 3 | Reports · team attendance/leave · pending approvals · assigned interviews · **HR Head: pending final-rejection queue** |
| `/dashboard/executive` | Layer 4 | Own work · interviews with feedback due · role-specific shortcuts (Recruiter: pipeline; Payroll Executive: payroll queue) |
| `/dashboard/me` | Layer 5 | Profile · attendance · leave balance + apply · payslips · **my assets** · onboarding docs · policies |

## 6.2 Employee profile — the information centre

Tabs, each independently permission-gated **server-side** (the API omits unauthorized sections entirely rather than the SPA merely hiding them):

| Tab | Contents | Visibility |
|---|---|---|
| Personal | Name, contact, DOB, address, emergency contact | Self · HR · Admin · CEO(r) |
| Employment | Code, department, designation, reporting manager, joining date, status, type | Per scope |
| HR | Documents, onboarding status, leave/attendance, recruitment history | Self(partial) · HR · Admin |
| **Financial** | Salary structure, payroll, earnings, deductions, benefits, payslips | Self(own) · Finance · Admin · CEO(aggregate) |
| **Assets** | Current allocations + full history, allocation/return details, condition | Self(r) · HR · Admin · Manager(r) |
| **Company account** | Official email, status, assigned date, assigned by. **No credential field, ever.** | Self(r) · HR · Admin |
| **Activity** | Profile changes, asset allocate/return, department/designation changes, account assignment, HR actions | HR · Admin · CEO(r) |

---

# PART 7 — Workflows

## 7.1 HR-controlled employee creation (decision 3)

```
HR Head opens "Add Employee"
   ↓  ONE atomic transaction — all or nothing
   Employee record (personal, employment, department, designation,
                    reporting manager, location, level)
 + User account (email, temporary credential, must_change_password)
 + UserRole grant (role selected by HR, validated against HR's own authority)
 + EmployeeOnboarding from the department's template
 + optional: asset allocations, company email account request
   ↓
   Welcome notification + onboarding email
```
Serializer validation **rejects any partial payload** — an Employee cannot be created without department, designation, role and login. **CEO and Admin are system-level exceptions**: created via bootstrap with `requires_employee=False`, no Employee record required.

## 7.2 Recruitment — fully configurable, no hardcoded pipeline

```
Job Opening → Application → HR Verification → [Interview stages ×N] →
Department Decision → HR Final Decision → Offer → Onboarding → Employee
                                       ↘ Final Rejection (HR Head, mandatory reason)
```

**Therapist pipeline** (data, not code):

| Order | Stage | Kind | Actor | Form |
|---|---|---|---|---|
| 10 | Applied | SCREENING | — | — |
| 20 | HR Verification | SCREENING | role=`hr_manager` | Document check |
| 30 | Clinical Round 1 | INTERVIEW | role=`clinic_doctor` | Clinical scorecard |
| 40 | Senior Review | INTERVIEW | role=`senior_doctor` | Clinical scorecard |
| 50 | **Medical Dept Decision** | DEPARTMENT_DECISION | role=`medical_director` | rationale required |
| 60 | HR Final Decision | APPROVAL | gate: role=`hr_head` | — |
| 70 | Offer | OFFER | — | — |
| 80 | Onboarding | ONBOARDING | — | — |
| 90/95 | Hired / Rejected | TERMINAL | — | — |

**Office Boy pipeline:** identical shape; stages 30–50 become CRE → Operations Manager → **Operational Head** (DEPARTMENT_DECISION). **Same engine, zero code difference.** New positions are new workflow rows.

## 7.3 Interview workflow

`StageInterviewerSpec` resolves interviewers **by role within the job's department** (or a named employee); HR picks from that shortlist. Slots proposed → candidate self-schedules via `schedule_token` → 24h/1h reminders via Celery. Interviewer submits `InterviewFeedback` against the stage's form; `total_score` is the weighted rollup and `passed` is computed from knockout fields plus threshold. `auto_advance_on_pass` / `auto_reject_on_fail` are per-stage opt-ins — and even when `auto_reject_on_fail` fires, it creates a `DepartmentDecision`, never a terminal rejection. Every move writes `ApplicationStageHistory`.

### Double-booking prevention (hard block, not a warning)

An interviewer may not hold two interviews whose time ranges overlap. Enforced in three places:

```python
# interviews/services.py — inside the scheduling transaction, with row locking
conflict = (Interview.objects
    .select_for_update()
    .filter(interviewer=interviewer,
            status__in=[SCHEDULED, RESCHEDULED],
            scheduled_at__lt=proposed_end,
            scheduled_end__gt=proposed_start)      # standard half-open overlap test
    .exclude(pk=self.pk)
    .first())
if conflict:
    raise InterviewConflict(
        f"{interviewer.full_name} already has an interview "
        f"{conflict.scheduled_at:%d %b %H:%M}–{conflict.scheduled_end:%H:%M} "
        f"for {conflict.application.candidate.full_name}.")
```

1. **Database** — a PostgreSQL `EXCLUDE USING gist` constraint on `(interviewer_id WITH =, tstzrange(scheduled_at, scheduled_end) WITH &&)` filtered to active statuses. This is the authoritative guarantee; no concurrent request can slip through.
2. **Service** — the check above, inside the transaction with `select_for_update()`, so the user gets a readable error rather than an `IntegrityError`.
3. **UI** — the scheduling form calls `GET /api/v1/interviews/check-conflict?interviewer=&start=&end=` on change and disables Save with an inline explanation.

The same check applies to candidate self-scheduling: a slot whose selection would create a conflict is filtered out of the public slot list, and re-validated on submit.

**Rescheduling** re-runs the identical check against the new time, excluding the interview being moved.

**Out of scope for V1:** working-hours calendars, leave-aware availability, capacity limits, timezone-per-interviewer. The `scheduled_end` field and the exclusion constraint are the foundation a full availability system builds on later without schema change.

## 7.4 Two-level rejection (decision 5)

```
Interviewers submit feedback + advisory recommendation
        ↓
Department Head reviews departmental feedback
        ↓
DepartmentDecision: RECOMMEND_REJECT | RECOMMEND_PROCEED | HOLD
  + mandatory rationale (≥20 chars) + reason category
  + snapshot of departmental interview feedback
        ↓  routed_to_hr_at set → appears in HR Head's queue
HR Head reviews: department rationale + every interview feedback + full candidate history
        ↓
HR Head executes final rejection
  + MANDATORY reason category + reason text (≥20 chars)
  + full interview_summary snapshot
        ↓  ONE transaction
CandidateRejection · Application.status=REJECTED · ApplicationStageHistory ·
AuditLog(REJECT) · notify recruiter + hiring manager + department head ·
optional candidate rejection email
```

**Admin override** is a separate action on a separate permission (`CANDIDATE/OVERRIDE_DECISION`), rendered in a distinctly-styled "Administrative Override" dialog that states the action is permanently recorded, requires a mandatory reason, and writes `CandidateDecisionOverride` + `AuditLog(OVERRIDE)` capturing who, when, why, and both previous and new decision. **Admin does not hold `CANDIDATE/REJECT`** and cannot perform a normal rejection.

Stored permanently and visible in the candidate's recruitment history to authorized HR users: candidate, department, recommended-by, department feedback, interview feedback, HR final decision, final reason, date/time, stage, complete history.

## 7.5 Employee lifecycle

```
Candidate → Application → Screening → Interview → Department Decision →
HR Decision → Offer → Onboarding → Employee → Active → Exit
```
`hire_candidate()` runs one transaction: Employee → User + credentials → role grant → onboarding from the department template → standard asset allocations for the designation → company email account request → appointment letter → notifications.

**Exit:** blocked while any `AssetAllocation` is `ACTIVE` unless explicitly written off (audited); triggers email deprovisioning; runs full-and-final settlement.

## 7.5b Probation & confirmation

```
Employee hired with probation_end_date
        ↓  nightly Celery job scans upcoming probation ends
Notify HR + reporting manager at T-30 days, T-7 days, and daily once overdue
        ↓  a ProbationReview row is created at T-30 with decision=PENDING
HR reviews and makes the decision — THE SYSTEM NEVER AUTO-CONFIRMS
        ↓
   ┌──────────────┬─────────────────────┬──────────────────┐
   │ CONFIRMED    │ EXTENDED            │ TERMINATED       │
   │ set          │ requires new        │ requires         │
   │ confirmation │ extended_to date    │ rationale;       │
   │ _date;       │ + rationale;        │ opens the exit   │
   │ AUTO-GENERATE│ reschedules the     │ workflow         │
   │ confirmation │ reminder cycle      │                  │
   │ letter       │                     │                  │
   └──────────────┴─────────────────────┴──────────────────┘
        ↓
   AuditLog + notification to employee and manager
```

Confirmation letter generation is the **only** automatic step, and it fires strictly *after* HR records `CONFIRMED`. `EXTENDED` and `TERMINATED` both require a rationale. Overdue reviews surface on the HR Head and Department Head dashboards until resolved.

Permission: `PROBATION_REVIEW/DECIDE` seeded to `hr_head` (ALL) and `hr_manager` (DEPARTMENT). Department Heads get `VIEW` on their own department so they can see who is due.

## 7.6 Payroll / finance

```
Employee → Salary Structure → Run (DRAFT) → Process (Celery) → Adjustments →
REVIEW → Approve (locked) → Payslips + PDF → Mark Paid → History
```
Payroll Executive / Accounts Manager process; **only** Finance Head / Admin approve. Off-cycle and supplementary runs supported. Loan EMI recovery and reimbursement payout wired into payslip generation.

## 7.6b Offer workflow (no additional approval gate)

HR Head's authority is sufficient to create and send an offer in V1 — there is deliberately **no** extra approval stage. The controls that already apply are enough:

- `OFFER/CREATE` and `OFFER/SEND` are seeded to `hr_head` only (`recruiter` gets `VIEW`).
- The offered CTC is subject to the salary permission model: a role without `SALARY/VIEW` at the relevant scope cannot see or set compensation fields, so the offer form renders them read-only or hidden.
- Offers above a configurable CTC threshold (`OrgSettings.offer_finance_review_threshold`, null = disabled) surface a **non-blocking advisory** to Finance Head rather than a gate. This is off by default.
- Every offer create / send / accept / decline / revoke writes an `AuditLog` row.

A `StageApprovalGate` on the OFFER stage remains available in the workflow engine if you later decide a particular position needs sign-off — it is configuration, not code.

## 7.7 Asset allocation & company email

```
Asset:  AVAILABLE → allocate → ALLOCATED → IN_USE → return → RETURNED
                                                  → AVAILABLE | MAINTENANCE | RETIRED
Email:  HR records account → ACTIVE → optional one-time credential handoff
        (Redis TTL, hash-only in DB) → viewed once → destroyed → audited
        Exit → DEPROVISIONED
```

**Company email is manually provisioned in V1.** HR Head or an authorized HR Executive creates the mailbox in Google Workspace / Microsoft 365 themselves, then **records** it in the HRMS — the system tracks the account, not the provisioning. No Google or Microsoft API integration ships in V1. `external_account_id` and the `provider` field reserve the seam so a future integration can adopt existing rows without migration, using **service-account delegation rather than stored user passwords**.

The password is **never stored in the HRMS database in any form**. The optional credential handoff exists only to pass an initial password to the employee once: it is encrypted with a key derived from a one-time token and held in Redis under a hard TTL, displayed exactly once, then destroyed. No API response — including the full employee profile — contains a credential field.

## 7.8 Candidate data retention (DPDP Act 2023)

```
Final decision (hired OR finally rejected) → final_decision_at set
        ↓  retention_until = final_decision_at + 12 months
Nightly job flags candidates past retention_until → purge_status = ELIGIBLE
        ↓
HR/Admin review queue at /recruitment/retention — shows what will be removed,
count, and per-candidate detail. Nothing is deleted automatically.
        ↓  explicit approval (HR Head or Admin), audited
purge_status = PURGE_APPROVED
        ↓  next purge run anonymises
Names, email, phone, resume file, parsed data, skills, current employer blanked;
dedup_hash rotated; AuditLog(action=DELETE) written per candidate
```

**Safeguards:** candidates linked to a resulting Employee are **never** eligible. Anonymisation preserves the `Application`, `InterviewFeedback`, `DepartmentDecision` and `CandidateRejection` rows — aggregate hiring analytics and the audit trail survive intact; only the personal identifiers are destroyed. Purge is anonymisation, not row deletion, so referential integrity and BI history hold. A dry-run mode reports exactly what would change without writing.

**Retention is configurable** via `OrgSettings.candidate_retention_months` (default 12) so the policy can be tightened or extended without a code change.

---

# PART 8 — BI, Notifications, Audit

**BI** — `core/bi/` metric registry. **Every metric is scope-aware**: it takes the caller's `AccessContext` and runs its aggregate through `scope_queryset()`. A Medical Director calling `headcount` gets *their department's* number from the same function the CEO calls org-wide. There is no second, unscoped query path. Expensive aggregates materialise nightly into `MetricSnapshot`; dashboards read snapshots with a live fallback. Served at `GET /api/v1/bi/{metric_key}`, rendered with Recharts. Families: HR (headcount, attrition, joiners/leavers, department distribution), recruitment (time-to-hire, funnel, offer acceptance, source of hire, stage aging), finance (payroll cost trend, department-wise cost, statutory liability), operations (attendance %, leave utilisation), medical (clinical staffing, therapist utilisation).

**Notifications** — polling at launch (§5). Events: new candidate application · candidate verification · interview scheduled · interview rescheduled · interview feedback submitted · **department rejection recommendation** · **HR final rejection** · candidate selection · offer sent/accepted · onboarding task assigned/overdue · asset allocated/returned/overdue · payroll processed/approved/paid · email account activated · document expiring · probation due · leave applied/decided. Per-user, per-kind preferences. Email delivery via Celery.

**Audit** — every sensitive mutation writes `AuditLog` with actor, action, resource, entity, before/after, IP, user agent, request id. Mandatory coverage: candidate rejection, department decisions, admin overrides, candidate status changes, interview feedback, employee creation and profile changes, asset allocation and return, email account assignment and credential view, payroll changes, permission changes, role changes, login and failed login, PII access, exports.

---

# PART 9 — Security Model

| Layer | Control |
|---|---|
| Transport | TLS via existing Caddy; HSTS |
| Auth | JWT access 15 min in memory; rotating refresh in httpOnly/Secure/SameSite=Strict cookie; blacklist on reuse; Argon2; lockout after N failed attempts |
| Route | `RBACPermission` on every view; **`manage.py check` fails if any view is unmapped** |
| Service | `require()` at the top of every mutating service function |
| Query | `scope_queryset()` — the only sanctioned path to a scoped queryset |
| Object | Out-of-scope reads return **404, not 403** — no existence leak |
| Read-only | `ReadOnlyPrincipalMiddleware` blocks unsafe methods for CEO regardless of route |
| Data | PAN/Aadhaar/bank encrypted at rest; masked in all responses; PII access audited |
| Credentials | Passwords never stored plain; email-account credentials never persisted at all (Redis TTL + one-time link) |
| Input | DRF serializers at every boundary; ORM only, no raw SQL from user input |
| Rate limit | Per-scope throttles; stricter on login, bootstrap, public apply, credential handoff |
| Audit | Append-only in Python **and** by DB privilege revocation |
| Headers | CSP, CORS allowlist for the SPA origin, `X-Frame-Options: DENY` |

---

# PART 10 — Scalability

Stateless Django behind Caddy, horizontally scalable · Redis for cache and Celery broker · permission context cached per request, invalidated on role/permission writes · Celery workers scale independently (payroll, email, BI, reminders) · composite indexes on `(department, status)`, `(reporting_manager)`, `(application, moved_at)`, `(recipient, is_read, -created_at)` · cursor pagination everywhere · `MetricSnapshot` keeps dashboards O(1) · department closure and reporting tree computed once per request (the old system re-derived the tree 22 times across 12 files) · S3 for all files · PgBouncer when connection count demands it · notification transport abstracted so WebSockets drop in without rework.

---

# PART 11 — Implementation Plan

Your required 24-step sequence, grouped into shippable phases. **This is a greenfield build — there is no migration from the old system and no dual-running period.** The old app is switched off at cutover.

| Phase | Steps | Deliverables | Exit criteria |
|---|---|---|---|
| **0. Foundation** | 1–3 | New Django project + React app scaffolding; Docker dev environment; CI; staging environment; `BaseModel`, encrypted fields, audit registry. **Port the payroll golden-master fixtures first.** | `docker compose up` runs api + web + db + redis; CI green |
| **1. Design sign-off** | 2, 4 | This document approved; **~250-cell permission matrix signed off in a spreadsheet by a named owner** | Matrix approved — **Phase 3 cannot start without it** |
| **2. Schema** | 5 | Full PostgreSQL schema + migrations + role seeder (18 roles) + `RolePermission` seeder + statutory seed data | `migrate` on a clean DB produces the complete schema; seeders idempotent |
| **3. Access engine + auth** | 6, 8 | `core/access/` complete; `ScopedModelViewSet`; JWT; `/login/admin`; bootstrap endpoint; **system check that fails boot on an unmapped view** | RBAC unit suite green; boot failure proven by test; CEO write-block proven |
| **4. API buildout** | 6, 9–10 | Departments, users, roles, employees; **atomic employee creation**; OpenAPI schema; generated TS client | HR Head creates a complete employee in one call; partial payloads rejected |
| **5. React shell** | 7 | Vite app, router, AppShell, component library, PermissionProvider, `<Can>`, `<ProtectedRoute>`, auth pages | All 18 roles log in and land on a role-appropriate dashboard |
| **6. Recruitment** | 11–13 | Workflow engine, jobs, candidates, applications, interviews (**with double-booking prevention**), feedback forms, **department decisions**, **HR final rejection**, **admin override**, offers, retention/purge queue | **Therapist and Office Boy pipelines run end-to-end with zero code difference.** Only HR Head can reject; Dept Heads can only recommend; reason enforced at DB + API + UI; overlapping interviews rejected by the DB constraint |
| **7. Employee & onboarding** | 14–15 | Employee profile tabs, documents, onboarding templates + items with upload, letters + PDF, `hire_candidate()`, **probation review + reminders** | Candidate → Employee in one transaction; probation reminders fire at T-30/T-7; confirmation letter generates only after HR confirms |
| **8. Assets & company accounts** | 16–17 | Asset master, allocation/return lifecycle, employee assets tab, email account management, credential handoff | Full allocate→return cycle audited; **no endpoint returns a credential**; exit blocked on unreturned assets |
| **9. Finance & payroll** | 18 | Salary structures, runs, statutory engine, adjustments, payslips + PDF, NEFT, XLSX | **Ported golden-master tests pass exactly**, per state, per regime |
| **10. BI, notifications, audit** | 19–20 | Metric registry + snapshots, notification polling + preferences, audit viewer | Every metric returns correctly scoped data for all 18 roles |
| **11. Dashboards & UI** | 21–22 | All 6 dashboards; full professional UI pass; accessibility; responsive | Every role has a purpose-built dashboard |
| **12. Testing & cutover** | 23–24 | Security + RBAC test suite; E2E workflow tests; penetration checklist; production deploy | All cutover gates below pass |

**Cutover gates — all must pass:**
- Every role × resource × action asserted against the signed-off matrix
- An automated walker hits **every** mutating endpoint as CEO and asserts 403
- Only HR Head can execute a final rejection; Department Heads get 403 on `/reject`; Admin gets 403 on `/reject` but 200 on `/override-decision`
- Rejection without a reason ≥20 chars fails at DB, API **and** UI
- Department heads see their own department and **not** siblings
- No endpoint returns a credential field under any role
- Employee creation with a partial payload is rejected
- Scheduling an interview that overlaps an interviewer's existing booking is rejected, including under concurrent requests
- No candidate is purged without explicit HR/Admin approval; candidates who became employees are never eligible
- Probation confirmation never occurs without an explicit HR decision
- Payroll golden-master matches the ported reference exactly
- Direct API calls cannot bypass any UI restriction

**Estimated effort: ~4–6 months for a small team.** The three long poles are the access engine (~3 weeks), the configurable workflow engine (~4 weeks), and the React frontend across all modules (~8–10 weeks).

---

# PART 12 — Resolved Decisions

All previously open items are now settled and integrated into the sections above.

| # | Decision | Where it lands |
|---|---|---|
| 1 | **CEO created by Admin later** — bootstrap provisions Admin only; Admin then issues the `ceo` role through normal user management. `requires_employee=False`, still exclusive. | §1.2, Phase 3 |
| 2 | **Double-booking hard-blocked** — overlapping interviews for the same interviewer are rejected at DB (`EXCLUDE USING gist`), service (locked transaction) and UI (live conflict check). Full availability/calendar deferred. | §4.7, §7.3, Phase 6 |
| 3 | **No extra offer approval gate** — HR Head's authority suffices, bounded by the salary permission model. Optional non-blocking Finance advisory above a configurable CTC threshold, off by default. | §7.6b, Phase 6 |
| 4 | **Probation: notify, never auto-confirm** — reminders at T-30/T-7/overdue; HR decides Confirm / Extend / Terminate; confirmation letter auto-generates only *after* HR confirms. | §4.3 (`ProbationReview`), §7.5b, Phase 7 |
| 5 | **Manual asset allocation in V1** — designation-based standard kits deferred; the single `allocate()` entry point is the seam. | §4.4, Phase 8 |
| 6 | **Manual company email provisioning** — HR records the account; no Google/Microsoft API in V1; password never stored in any form; `provider` + `external_account_id` reserve the integration seam. | §4.5, §7.7, Phase 8 |
| 7 | **12-month retention after final decision** — configurable; eligible candidates queue for explicit HR/Admin approval; purge is anonymisation (not deletion) so analytics and audit survive; candidates who became employees are never eligible. | §4.7 (`Candidate`), §7.8, Phase 6 |

**Nothing further blocks approval.** On sign-off, Phase 0 begins: project scaffolding, Docker dev environment, CI, and porting the payroll golden-master fixtures as the statutory compliance oracle.
