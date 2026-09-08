"""
The authorization vocabulary: Layer, Scope, Action, Resource.

DESIGN
------
Authorization resolves to a *scope*, not a boolean. `Scope.NONE == 0`, so
`if can(user, ...)` reads naturally while the returned value still carries the
breadth needed to filter a queryset. One resolved value answers both
"may they?" and "how much?".

The previous system answered only the first question, via ~78 hand-written
`has_role()` checks scattered across 25 files with no central chokepoint, plus
two permission engines that were fully implemented and wired to nothing. Every
list endpoint then re-derived its own scoping inline, and several forgot to.

Everything here is a plain enum with no database dependency, so it can be
imported from migrations, management commands and tests without import cycles.
"""

from __future__ import annotations

from django.db import models


class Layer(models.IntegerChoices):
    """Organizational layer. Lower number = higher authority."""

    LEADERSHIP = 1, "Layer 1 — Leadership"
    DEPARTMENT_HEAD = 2, "Layer 2 — Department Head"
    MANAGER = 3, "Layer 3 — Manager"
    EXECUTIVE = 4, "Layer 4 — Executive"
    STAFF = 5, "Layer 5 — Staff"


class Scope(models.IntegerChoices):
    """
    How much of a resource a principal may reach.

    Ordered and comparable: a higher value strictly contains every lower one,
    which is what lets `resolve_context()` aggregate multiple roles with a
    simple `max()`.
    """

    NONE = 0, "No access"
    SELF = 1, "Own records only"
    TEAM = 2, "Own reporting tree"
    DEPARTMENT = 3, "Own department and its sub-departments"
    ALL = 4, "Whole organization"


class Action(models.TextChoices):
    VIEW = "view", "View"
    CREATE = "create", "Create"
    EDIT = "edit", "Edit"
    DELETE = "delete", "Delete"
    APPROVE = "approve", "Approve"
    EXPORT = "export", "Export"
    # Terminal candidate rejection. Deliberately NOT `delete` or `edit`: it is
    # seeded to hr_head alone, so it must be independently grantable.
    REJECT = "reject", "Reject"
    # Administrative override of a decision someone else owns. Separate from
    # REJECT so that "Admin can override" never implies "Admin can reject".
    OVERRIDE = "override", "Override decision"
    # Department Head's recommendation in the two-level rejection flow.
    RECOMMEND = "recommend", "Recommend decision"
    # HR's probation outcome (confirm / extend / terminate).
    DECIDE = "decide", "Decide"
    # Bulk ingest of externally sourced records. Separate from CREATE because
    # adding one walk-in candidate and ingesting two thousand third-party
    # records under a legal-basis attestation are different authorities with
    # different blast radii — and only one of them is worth revoking on its own.
    IMPORT = "import", "Bulk import"


#: Actions that mutate state. A read-only principal (CEO) has every one of
#: these stripped by the engine, unconditionally, after all other resolution.
WRITE_ACTIONS = frozenset(
    {
        Action.CREATE,
        Action.EDIT,
        Action.DELETE,
        Action.APPROVE,
        Action.REJECT,
        Action.OVERRIDE,
        Action.RECOMMEND,
        Action.DECIDE,
        Action.IMPORT,
    }
)

READ_ACTIONS = frozenset({Action.VIEW, Action.EXPORT})

assert WRITE_ACTIONS | READ_ACTIONS == set(Action.values), (
    "Every Action must be classified as read or write — otherwise the "
    "read-only clamp would silently let an unclassified action through."
)


class Resource(models.TextChoices):
    """
    Every protected noun in the system.

    A resource is a *permission surface*, not necessarily one model. Several
    map to a model (EMPLOYEE), some to a slice of one (SALARY is the
    compensation face of Employee), and some to a capability with no model at
    all (DASHBOARD_ORG).
    """

    # --- Identity & organization ---
    USER = "user", "User accounts"
    ROLE = "role", "Roles and permissions"
    DEPARTMENT = "department", "Departments"
    DESIGNATION = "designation", "Designations"
    LOCATION = "location", "Locations"
    ORG_SETTINGS = "org_settings", "Organization settings"

    # --- People ---
    EMPLOYEE = "employee", "Employee records"
    EMPLOYEE_DOCUMENT = "employee_document", "Employee documents"
    PROBATION_REVIEW = "probation_review", "Probation reviews"

    # --- Assets & accounts ---
    ASSET = "asset", "Assets"
    ASSET_ALLOCATION = "asset_allocation", "Asset allocations"
    EMAIL_ACCOUNT = "email_account", "Company email accounts"

    # --- Recruitment ---
    JOB_OPENING = "job_opening", "Job openings"
    CANDIDATE = "candidate", "Candidates"
    APPLICATION = "application", "Applications"
    INTERVIEW = "interview", "Interviews"
    INTERVIEW_FEEDBACK = "interview_feedback", "Interview feedback"
    DEPARTMENT_DECISION = "department_decision", "Department hiring decisions"
    OFFER = "offer", "Offers"
    HIRING_WORKFLOW = "hiring_workflow", "Hiring workflow configuration"

    # --- Lifecycle ---
    ONBOARDING = "onboarding", "Onboarding"
    OFFBOARDING = "offboarding", "Offboarding"
    LETTER = "letter", "Employee letters"

    # --- Time ---
    ATTENDANCE = "attendance", "Attendance"
    REGULARIZATION = "regularization", "Attendance regularization"
    #: The eSSL biometric integration surface: devices, employee mappings,
    #: sync controls and history. Deliberately its OWN resource so employees'
    #: SELF-scoped attendance rights never open any of it.
    ATTENDANCE_DEVICE = "attendance_device", "Biometric attendance devices"
    LEAVE_REQUEST = "leave_request", "Leave requests"
    LEAVE_POLICY = "leave_policy", "Leave policy configuration"

    # --- Money ---
    SALARY = "salary", "Salary structures"
    #: Custom compensation packages: multi-period schedules and deferred
    #: amounts. A separate surface from SALARY because the spec restricts its
    #: WRITES to HR Head and Finance Head alone — narrower than the set of
    #: roles that may maintain salary structures.
    PACKAGE = "package", "Compensation packages"
    PAYROLL_RUN = "payroll_run", "Payroll runs"
    PAYSLIP = "payslip", "Payslips"
    PAYROLL_ADJUSTMENT = "payroll_adjustment", "Payroll adjustments"
    STATUTORY_CONFIG = "statutory_config", "Statutory configuration"

    # --- Governance ---
    POLICY_DOC = "policy_doc", "Policy documents"
    AUDIT_LOG = "audit_log", "Audit log"
    NOTIFICATION = "notification", "Notifications"
    REPORT = "report", "Reports"

    # --- Dashboards (capability surfaces, no model) ---
    DASHBOARD_ORG = "dashboard_org", "Organization-wide dashboard"
    DASHBOARD_DEPARTMENT = "dashboard_department", "Department dashboard"
    DASHBOARD_TEAM = "dashboard_team", "Team dashboard"


#: Resources that constitute user management. A role without
#: `can_manage_users` has every one of these stripped by the engine, so a
#: mistake in the permission matrix alone can never grant account control.
USER_MANAGEMENT_RESOURCES = frozenset({Resource.USER, Resource.ROLE})


#: Canonical role codes. Seeded by `apps.accounts.services.seed_roles`.
class RoleCode:
    CEO = "ceo"
    ADMIN = "admin"

    MEDICAL_DIRECTOR = "medical_director"
    OPERATIONAL_HEAD = "operational_head"
    HR_HEAD = "hr_head"
    FINANCE_HEAD = "finance_head"

    SENIOR_DOCTOR = "senior_doctor"
    OPERATIONS_MANAGER = "operations_manager"
    HR_MANAGER = "hr_manager"
    ACCOUNTS_MANAGER = "accounts_manager"

    CLINIC_DOCTOR = "clinic_doctor"
    CRE = "cre"
    RECRUITER = "recruiter"
    PAYROLL_EXECUTIVE = "payroll_executive"
    EXECUTIVE = "executive"

    THERAPIST = "therapist"
    OFFICE_BOY = "office_boy"
    EMPLOYEE = "employee"


class DepartmentKind(models.TextChoices):
    """
    Department taxonomy.

    Drives dashboard selection, BI grouping and role seeding. It is NEVER used
    for authorization scope — that comes from `Employee.department`, so that
    moving a person between departments changes their access without touching
    their role.
    """

    MEDICAL = "medical", "Medical"
    OPERATIONS = "operations", "Operations"
    HR = "hr", "Human Resources"
    FINANCE = "finance", "Accounts & Finance"
    OTHER = "other", "Other"


class DashboardKey(models.TextChoices):
    """Which dashboard a role lands on. Set per-role, not inferred from layer."""

    CEO = "ceo", "CEO — organization-wide, read-only"
    ADMIN = "admin", "Admin — full system management"
    DEPARTMENT = "department", "Department head"
    MANAGER = "manager", "Manager"
    EXECUTIVE = "executive", "Executive"
    SELF = "self", "Self-service"
