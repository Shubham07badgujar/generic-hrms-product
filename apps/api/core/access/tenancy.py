"""
Which models are tenant-scoped, and which are knowingly not yet.

The organization predicate can only filter on a column that exists. During the
conversion most models do not have one yet, so the predicate has to let their
queries through -- and a filter that silently lets things through is precisely
the failure this architecture exists to prevent.

So the transitional state is not implicit. Every model that is not yet
converted is named in `PENDING_TENANCY` below, `manage.py check` fails for any
model that is neither converted, pending, nor deliberately global, and the
count is reported on every build. The set shrinks to empty as apps convert;
when it is empty the transitional branch is dead code and comes out.

The point is that "this table is not protected yet" is a line in a file
somebody has to delete, not an absence nobody notices.
"""

from __future__ import annotations

#: Genuinely global. Not tenant data, and never will be.
TENANT_EXEMPT: dict[str, str] = {
    "organization.Organization": "Is the tenant.",
    "organization.OrganizationMembership": (
        "Resolving a principal's organization is what this table is queried "
        "FOR, so requiring one first would be circular."
    ),
    "accounts.User": (
        "Login identity is global: email is the USERNAME_FIELD and must be "
        "unique platform-wide. A user's tenant comes from their membership."
    ),
    "statutory.StatutoryRuleSet": (
        "PF/ESI/PT/income-tax tables are facts about the Republic of India, "
        "not about a customer. Duplicating them per organization would mean N "
        "copies to verify and would defeat four-eyes verification."
    ),
    "audit.AuditLog": (
        "Carries a NULLABLE organization instead: pre-authentication events, "
        "platform actions and organization creation itself genuinely have "
        "none. Scoped by an explicit predicate, never by the shared one."
    ),
}

#: NOT YET CONVERTED. Each entry is a table whose rows are tenant data but
#: which does not carry the column yet, so queries against it are NOT scoped by
#: organization. Delete entries as apps are converted; the list is the backlog
#: and the honest statement of what is currently unprotected.
PENDING_TENANCY: frozenset[str] = frozenset(
    {
        "accounts.Role",
        "accounts.RolePermission",
        "accounts.UserRole",
        "accounts.UserPermissionOverride",
        "organization.Department",
        "organization.Designation",
        "organization.Location",
        "organization.EmployeeLevel",
        "organization.Team",
        "employees.Employee",
        "employees.EmployeeAddress",
        "employees.EmergencyContact",
        "employees.EmployeeEducation",
        "employees.EmployeeExperience",
        "employees.DocumentType",
        "employees.EmployeeDocument",
        "employees.ProbationReview",
        "assets.AssetCategory",
        "assets.Asset",
        "assets.AssetAllocation",
        "assets.AssetMaintenanceLog",
        "itaccounts.CompanyEmailAccount",
        "attendance.AttendanceDevice",
        "attendance.EsslEmployeeLink",
        "attendance.RawPunch",
        "attendance.AttendanceRecord",
        "attendance.RegularizationRequest",
        "attendance.ShiftRule",
        "attendance.EsslSyncRun",
        "leave.LeaveType",
        "leave.LeavePolicy",
        "leave.HolidayCalendar",
        "leave.Holiday",
        "leave.LeaveBalance",
        "leave.LeaveRequest",
        "leave.LeaveTransaction",
        "leave.LeaveSettings",
        "leave.ShortLeave",
        "leave.ShortLeaveConversion",
        "leave.HolidayWork",
        "payroll.SalaryComponent",
        "payroll.SalaryStructure",
        "payroll.SalaryStructureLine",
        "payroll.InvestmentDeclaration",
        "payroll.PayrollRun",
        "payroll.Payslip",
        "payroll.PayslipLine",
        "payroll.StatutoryContribution",
        "payroll.PayrollAdjustment",
        "payroll.EmployeeLoan",
        "payroll.ReimbursementClaim",
        "payroll.EmployeePackage",
        "payroll.PackagePeriod",
        "payroll.PackageDeferral",
        "recruitment.JobOpening",
        "recruitment.Candidate",
        "recruitment.ConsentRecord",
        "recruitment.CandidateExternalRef",
        "recruitment.Application",
        "recruitment.ApplicationEvent",
        "recruitment.Interview",
        "recruitment.InterviewFeedback",
        "recruitment.StageDecision",
        "recruitment.CandidateRejection",
        "recruitment.DecisionOverride",
        "recruitment.Offer",
        "recruitment.CandidateNotification",
        "recruitment.InterviewSlotInvite",
        "workflows.HiringWorkflow",
        "workflows.WorkflowStage",
        "workflows.StageTransition",
        "workflows.FeedbackForm",
        "workflows.FeedbackField",
        "onboarding.OnboardingTemplate",
        "onboarding.OnboardingTemplateItem",
        "onboarding.EmployeeOnboarding",
        "onboarding.OnboardingItem",
        "onboarding.LetterTemplate",
        "onboarding.EmployeeLetter",
        "offboarding.ResignationRequest",
        "offboarding.ClearanceTemplate",
        "offboarding.ClearanceTemplateItem",
        "offboarding.ExitWorkflow",
        "offboarding.ExitClearanceItem",
        "offboarding.FinalSettlement",
        "offboarding.ExitInterview",
        "notifications.Notification",
        "notifications.NotificationPreference",
        "notifications.NotificationDelivery",
        "imports.ImportBatch",
        "imports.ImportRow",
        "reporting.MetricSnapshot",
    }
)


def is_tenanted(model) -> bool:
    """Whether this model actually carries an organization column."""
    return any(f.name == "organization" for f in model._meta.get_fields())
