"""
Resource → model wiring.

Scoping cannot be inferred from a model; it must be declared. This registry is
the declaration: for each resource, which model backs it and how to walk from
that model to the Employee who owns the row.

A resource missing from here can never be scoped below `Scope.ALL` — the engine
denies instead of guessing. `core.access.checks` fails `manage.py check` if any
`Resource` is unregistered, so the omission is caught at build time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Resource


@dataclass(frozen=True)
class ResourceSpec:
    """How to scope one resource."""

    #: "app_label.ModelName", resolved lazily to avoid import cycles.
    model_label: str

    #: ORM path from this model to its owning Employee.
    #:   ""          -> the model IS Employee
    #:   "employee"  -> model.employee is the owner
    #:   "current_holder" -> a differently-named FK to Employee
    #: None -> the row has no owning employee (see person_scoped).
    employee_path: str | None = ""

    #: False when rows have no owning employee (PayrollRun, JobOpening,
    #: LeavePolicy...). Such resources are all-or-nothing: a principal either
    #: has Scope.ALL or sees none. Anything narrower is undefined, so the
    #: engine denies it rather than inventing a filter.
    person_scoped: bool = True


RESOURCE_SPECS: dict[str, ResourceSpec] = {
    # --- People -----------------------------------------------------------
    Resource.EMPLOYEE: ResourceSpec("employees.Employee", ""),
    Resource.EMPLOYEE_DOCUMENT: ResourceSpec("employees.EmployeeDocument", "employee"),
    Resource.PROBATION_REVIEW: ResourceSpec("employees.ProbationReview", "employee"),

    # --- Assets & accounts ------------------------------------------------
    Resource.ASSET_ALLOCATION: ResourceSpec("assets.AssetAllocation", "employee"),
    Resource.EMAIL_ACCOUNT: ResourceSpec("itaccounts.CompanyEmailAccount", "employee"),
    # An asset in the store belongs to nobody until allocated.
    Resource.ASSET: ResourceSpec("assets.Asset", None, person_scoped=False),

    # --- Time -------------------------------------------------------------
    Resource.ATTENDANCE: ResourceSpec("attendance.AttendanceRecord", "employee"),
    Resource.REGULARIZATION: ResourceSpec("attendance.RegularizationRequest", "employee"),
    # Integration plumbing belongs to nobody — all-or-nothing visibility.
    Resource.ATTENDANCE_DEVICE: ResourceSpec(
        "attendance.AttendanceDevice", None, person_scoped=False
    ),
    Resource.LEAVE_REQUEST: ResourceSpec("leave.LeaveRequest", "employee"),

    # --- Money ------------------------------------------------------------
    Resource.SALARY: ResourceSpec("payroll.SalaryStructure", "employee"),
    Resource.PACKAGE: ResourceSpec("payroll.EmployeePackage", "employee"),
    Resource.PAYSLIP: ResourceSpec("payroll.Payslip", "employee"),
    Resource.PAYROLL_ADJUSTMENT: ResourceSpec("payroll.PayrollAdjustment", "employee"),

    # --- Lifecycle --------------------------------------------------------
    Resource.ONBOARDING: ResourceSpec("onboarding.EmployeeOnboarding", "employee"),
    Resource.OFFBOARDING: ResourceSpec("offboarding.ExitWorkflow", "employee"),
    Resource.LETTER: ResourceSpec("onboarding.EmployeeLetter", "employee"),

    # --- Recruitment ------------------------------------------------------
    # Interviews scope by INTERVIEWER, not by candidate: a Clinic Doctor sees
    # the interviews assigned to them, which is what "assigned candidates only"
    # means in practice.
    Resource.INTERVIEW: ResourceSpec("recruitment.Interview", "interviewer"),
    Resource.INTERVIEW_FEEDBACK: ResourceSpec(
        "recruitment.InterviewFeedback", "submitted_by"
    ),
    # Candidates and applications belong to a job, not a person. Department
    # heads reach them through JOB_OPENING's department, handled explicitly in
    # the recruitment services rather than by generic employee-path scoping.
    Resource.CANDIDATE: ResourceSpec("recruitment.Candidate", None, person_scoped=False),
    Resource.APPLICATION: ResourceSpec(
        "recruitment.Application", None, person_scoped=False
    ),
    Resource.JOB_OPENING: ResourceSpec(
        "recruitment.JobOpening", None, person_scoped=False
    ),
    Resource.OFFER: ResourceSpec("recruitment.Offer", None, person_scoped=False),
    # One model covers every stage decision — HR verification, interview
    # outcome and department recommendation are the same act to the engine.
    Resource.DEPARTMENT_DECISION: ResourceSpec(
        "recruitment.StageDecision", None, person_scoped=False
    ),
    Resource.HIRING_WORKFLOW: ResourceSpec(
        "workflows.HiringWorkflow", None, person_scoped=False
    ),

    # --- Configuration (all-or-nothing) -----------------------------------
    Resource.USER: ResourceSpec("accounts.User", None, person_scoped=False),
    Resource.ROLE: ResourceSpec("accounts.Role", None, person_scoped=False),
    Resource.DEPARTMENT: ResourceSpec(
        "organization.Department", None, person_scoped=False
    ),
    Resource.DESIGNATION: ResourceSpec(
        "organization.Designation", None, person_scoped=False
    ),
    Resource.LOCATION: ResourceSpec("organization.Location", None, person_scoped=False),
    Resource.ORG_SETTINGS: ResourceSpec(
        "organization.OrgSettings", None, person_scoped=False
    ),
    Resource.LEAVE_POLICY: ResourceSpec("leave.LeavePolicy", None, person_scoped=False),
    Resource.PAYROLL_RUN: ResourceSpec("payroll.PayrollRun", None, person_scoped=False),
    # Statutory rates are one verified, effective-dated rule set per statute —
    # not a table of config rows per statute — so this guards `StatutoryRuleSet`
    # rather than a PF-specific model.
    Resource.STATUTORY_CONFIG: ResourceSpec(
        "statutory.StatutoryRuleSet", None, person_scoped=False
    ),
    Resource.POLICY_DOC: ResourceSpec("policies.Policy", None, person_scoped=False),
    # Scoped by the person the event is ABOUT, not the person who did it — a
    # Department Head should see what happened to their people, including when
    # HR or Finance did it. Rows with no subject (config changes, role edits,
    # statutory rates) resolve to nothing below ALL scope, which is correct:
    # an organisation-level event belongs to no department.
    Resource.AUDIT_LOG: ResourceSpec("audit.AuditLog", "subject_employee"),
    Resource.REPORT: ResourceSpec("reporting.MetricSnapshot", None, person_scoped=False),

    # --- Notifications ----------------------------------------------------
    # Scoped by recipient User, not Employee — Admin and CEO have no Employee
    # record but still receive notifications. Handled in the notifications
    # viewset, which always filters to request.user.
    Resource.NOTIFICATION: ResourceSpec(
        "notifications.Notification", None, person_scoped=False
    ),

    # --- Dashboards (capability surfaces, no model) -----------------------
    Resource.DASHBOARD_ORG: ResourceSpec("reporting.MetricSnapshot", None, person_scoped=False),
    Resource.DASHBOARD_DEPARTMENT: ResourceSpec("reporting.MetricSnapshot", None, person_scoped=False),
    Resource.DASHBOARD_TEAM: ResourceSpec("reporting.MetricSnapshot", None, person_scoped=False),
}


def missing_resources() -> list[str]:
    """Resources with no spec. Used by the system check."""
    return sorted(set(Resource.values) - set(RESOURCE_SPECS.keys()))
