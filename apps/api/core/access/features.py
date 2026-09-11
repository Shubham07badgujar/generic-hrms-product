"""
Which plan feature governs which resource.

FEATURE GATING WITH NO SCATTERED CHECKS

The usual way this goes wrong is `if org.plan.has_payroll:` appearing in forty
places, three of which are wrong and one of which nobody writes at all. So
there is exactly one map here, one permission class that reads it, and a system
check that the map is TOTAL over `Resource`.

Nobody writes a feature check anywhere. A view is gated because its resource is
mapped to a feature, and adding a `Resource` without deciding its feature fails
`manage.py check` -- the same mechanical-coverage trick as `access.E003`, and
the reason a new module cannot ship accidentally ungated or accidentally
ungateable.

WHY KEYED ON `Resource` AND NOT ON A NEW VOCABULARY

`RBACPermission` has already resolved the view's resource by the time the
feature class runs, so the gate costs one dictionary lookup and introduces no
second way of naming a module. It also means the platform surface is
automatically out of scope: platform views declare no `access_resource`, so
there is nothing to look up and nothing to gate -- which is correct, since a
customer's plan must never govern the operator's own console.

WHAT IS DELIBERATELY NOT HERE

`FeatureCode` is not a `Resource`, and PLAN and SUBSCRIPTION never become ones.
`Role` rows are runtime-editable by an organization's own Admin, so a plan
resource in the permission matrix would let a customer grant themselves a
feature they are not paying for. Plans are enforced from code and from the
subscription row, never from the matrix.
"""

from __future__ import annotations

from django.db import models

from .catalog import Resource


class FeatureCode(models.TextChoices):
    """
    A sellable module.

    Coarser than `Resource` on purpose. A plan is something a customer reads on
    a pricing page and a salesperson describes in a sentence; forty-one
    switches is a configuration screen, not a plan.
    """

    #: Always on. An HRMS without employees, departments and its own settings
    #: is not a cheaper HRMS, it is a broken one -- so this is not a feature any
    #: plan may disable, and `Plan.disabled_features` refuses to name it.
    CORE = "core", "Core HR"
    RECRUITMENT = "recruitment", "Recruitment"
    ONBOARDING = "onboarding", "Onboarding"
    OFFBOARDING = "offboarding", "Offboarding"
    ATTENDANCE = "attendance", "Attendance"
    #: Separate from ATTENDANCE: manual and imported attendance is the baseline,
    #: and the biometric device integration is the part with a per-customer
    #: cost -- an endpoint, credentials and a sync job.
    ATTENDANCE_BIOMETRIC = "attendance_biometric", "Biometric devices"
    LEAVE = "leave", "Leave"
    PAYROLL = "payroll", "Payroll and statutory"
    ASSETS = "assets", "Asset tracking"
    IT_ACCOUNTS = "it_accounts", "Company email accounts"
    REPORTING = "reporting", "Reports and analytics"


#: Features a plan may never switch off, because the product does not work
#: without them.
ALWAYS_ON = frozenset({FeatureCode.CORE})


#: Every `Resource`, mapped to exactly one feature.
#:
#: TOTALITY IS CHECKED, NOT TRUSTED: `access.E014` fails the build when a
#: resource is missing, so a new module cannot ship ungated by omission. The
#: check is the reason this can be a plain dict rather than a defaulting one --
#: a `.get(resource, CORE)` would make every forgotten resource silently free.
FEATURE_OF_RESOURCE: dict[str, str] = {
    # -- core: the organization itself, its people, and the records that
    # follow them. Disabling any of these would not produce a cheaper product.
    Resource.USER: FeatureCode.CORE,
    Resource.ROLE: FeatureCode.CORE,
    Resource.DEPARTMENT: FeatureCode.CORE,
    Resource.DESIGNATION: FeatureCode.CORE,
    Resource.LOCATION: FeatureCode.CORE,
    Resource.ORG_SETTINGS: FeatureCode.CORE,
    Resource.EMPLOYEE: FeatureCode.CORE,
    Resource.EMPLOYEE_DOCUMENT: FeatureCode.CORE,
    Resource.PROBATION_REVIEW: FeatureCode.CORE,
    Resource.POLICY_DOC: FeatureCode.CORE,
    Resource.NOTIFICATION: FeatureCode.CORE,
    # An audit trail is not an upsell. A customer who cannot answer "who
    # changed this" has a compliance problem, and selling them the answer
    # would be selling them their own obligations back.
    Resource.AUDIT_LOG: FeatureCode.CORE,
    Resource.DASHBOARD_ORG: FeatureCode.CORE,
    Resource.DASHBOARD_DEPARTMENT: FeatureCode.CORE,
    Resource.DASHBOARD_TEAM: FeatureCode.CORE,
    # -- recruitment
    Resource.JOB_OPENING: FeatureCode.RECRUITMENT,
    Resource.CANDIDATE: FeatureCode.RECRUITMENT,
    Resource.APPLICATION: FeatureCode.RECRUITMENT,
    Resource.INTERVIEW: FeatureCode.RECRUITMENT,
    Resource.INTERVIEW_FEEDBACK: FeatureCode.RECRUITMENT,
    Resource.DEPARTMENT_DECISION: FeatureCode.RECRUITMENT,
    Resource.OFFER: FeatureCode.RECRUITMENT,
    Resource.HIRING_WORKFLOW: FeatureCode.RECRUITMENT,
    # -- joining and leaving
    Resource.ONBOARDING: FeatureCode.ONBOARDING,
    # Letters are generated by both onboarding and offboarding, and an
    # organization that has either needs them. Filed under ONBOARDING because
    # that is where the first one is issued; revisit if the two are ever sold
    # separately.
    Resource.LETTER: FeatureCode.ONBOARDING,
    Resource.OFFBOARDING: FeatureCode.OFFBOARDING,
    # -- time
    Resource.ATTENDANCE: FeatureCode.ATTENDANCE,
    Resource.REGULARIZATION: FeatureCode.ATTENDANCE,
    Resource.ATTENDANCE_DEVICE: FeatureCode.ATTENDANCE_BIOMETRIC,
    Resource.LEAVE_REQUEST: FeatureCode.LEAVE,
    Resource.LEAVE_POLICY: FeatureCode.LEAVE,
    # -- money
    Resource.SALARY: FeatureCode.PAYROLL,
    Resource.PACKAGE: FeatureCode.PAYROLL,
    Resource.PAYROLL_RUN: FeatureCode.PAYROLL,
    Resource.PAYSLIP: FeatureCode.PAYROLL,
    Resource.PAYROLL_ADJUSTMENT: FeatureCode.PAYROLL,
    Resource.STATUTORY_CONFIG: FeatureCode.PAYROLL,
    # -- everything else
    Resource.ASSET: FeatureCode.ASSETS,
    Resource.ASSET_ALLOCATION: FeatureCode.ASSETS,
    Resource.EMAIL_ACCOUNT: FeatureCode.IT_ACCOUNTS,
    Resource.REPORT: FeatureCode.REPORTING,
}


def feature_for(resource) -> str | None:
    """The feature governing `resource`, or None when it is not a resource."""
    if not resource:
        return None
    return FEATURE_OF_RESOURCE.get(str(resource))
