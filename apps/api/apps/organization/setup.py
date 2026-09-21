"""
The first-time setup wizard: a progress marker, not a staging area.

Every step writes to the REAL domain tables through endpoints that already
exist -- departments through the departments endpoint, roles through the roles
endpoint. The wizard adds one read and one transition, not a parallel data
model with its own copy of the company.

COMPLETION IS COMPUTED, NEVER STORED

Each step is a predicate against those tables. That single decision removes a
whole category of bug: there is no per-step row to go stale, no "completed"
flag that disagrees with the data, and no transition state machine to get
wrong. Closing the browser loses nothing, because nothing was being held. And
deleting the last department honestly reopens that step, which a stored flag
would not do -- it would report a finished setup for an organization that can
no longer hire anybody.

PENDING_SETUP IS A WORKING STATE

Not a locked one. The administrator is DOING the setup, so they must be able to
read and write in order to finish it; `PENDING_SETUP` is in
`OPERATIONAL_STATUSES` for exactly that reason. Only `finish_setup` moves the
organization out of it -- to TRIAL or ACTIVE, whichever its subscription
implies, a question it asks the subscriptions service rather than answering.

REQUIRED VERSUS ADVISORY

A step is required only when there is a real data test for it today. Email
configuration is per-organization work that does not exist until Stage 5, and
an employee import is optional by nature -- a company may key its first hires
in by hand. Marking those required would mean either blocking finish on
something unimplementable, or writing a predicate that returns True and
pretending it checked. Both are worse than saying "advisory" out loud.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class SetupError(Exception):
    """A refusal the administrator can act on."""


@dataclass(frozen=True)
class SetupStep:
    key: str
    title: str
    #: Blocks `finish_setup` when unsatisfied.
    required: bool
    #: Where the SPA sends them. A string rather than a reverse() call: this is
    #: a client route, not a server one.
    route: str
    satisfied: Callable[[object], bool]
    #: What the step is for, in the administrator's words rather than ours.
    detail: str = ""


def _scoped(model, organization):
    from core.models import org_scoped

    return org_scoped(model, organization)


def _has_profile(organization) -> bool:
    # Name comes from provisioning, so requiring it would be a step that is
    # complete before the wizard opens. The legal name is the one identity
    # field only the customer knows, and it appears on every letter, payslip
    # and offer the product generates.
    return bool((organization.legal_name or "").strip())


def _has_departments(organization) -> bool:
    from apps.organization.models import Department

    return _scoped(Department, organization).filter(is_active=True).exists()


def _has_locations(organization) -> bool:
    from apps.organization.models import Location

    return _scoped(Location, organization).filter(is_active=True).exists()


def _has_designations(organization) -> bool:
    from apps.organization.models import Designation

    return _scoped(Designation, organization).filter(is_active=True).exists()


def _has_roles(organization) -> bool:
    from apps.accounts.models import Role

    return _scoped(Role, organization).filter(is_active=True).exists()


def _has_leave_policy(organization) -> bool:
    from apps.leave.models import LeavePolicy

    return _scoped(LeavePolicy, organization).filter(is_active=True).exists()


def _has_attendance_policy(organization) -> bool:
    from apps.attendance.models import ShiftRule

    return _scoped(ShiftRule, organization).filter(is_active=True).exists()


def _has_payroll_config(organization) -> bool:
    from apps.payroll.models import SalaryComponent

    return _scoped(SalaryComponent, organization).filter(is_active=True).exists()


def _has_employees(organization) -> bool:
    from apps.employees.models import Employee

    return _scoped(Employee, organization).filter(is_active=True).exists()


def _email_configured(organization) -> bool:
    # Per-organization email arrives with the configuration work in Stage 5.
    # Until then this reports honestly rather than returning True and calling
    # itself a check.
    return False


#: The wizard, in the order it is walked.
#:
#: Several of these are already satisfied when the wizard first opens, because
#: provisioning seeds roles, leave policies and shift rules. That is shown
#: rather than hidden: an administrator who can see that seven of ten steps are
#: done starts by doing the three that are not, and the three that are not --
#: departments, locations, designations -- are exactly the ones only they can
#: answer.
SETUP_STEPS: list[SetupStep] = [
    SetupStep(
        key="company_profile",
        title="Company profile",
        required=True,
        route="/settings/organization",
        satisfied=_has_profile,
        detail="The registered legal name, which appears on every letter, "
               "payslip and offer the product generates.",
    ),
    SetupStep(
        key="departments",
        title="Departments",
        required=True,
        route="/settings/departments",
        satisfied=_has_departments,
        detail="Nobody can be hired into a company with no departments.",
    ),
    SetupStep(
        key="locations",
        title="Locations",
        required=True,
        route="/settings/locations",
        satisfied=_has_locations,
        detail="Drives the holiday calendar and the Professional Tax "
               "jurisdiction, so it cannot be inferred.",
    ),
    SetupStep(
        key="designations",
        title="Designations",
        required=True,
        route="/settings/designations",
        satisfied=_has_designations,
        detail="Job titles, which every employee record needs.",
    ),
    SetupStep(
        key="roles",
        title="Roles and permissions",
        required=True,
        route="/settings/roles",
        satisfied=_has_roles,
        detail="A starting set is provisioned with the organization. Review, "
               "rename or extend it -- these are defaults you own, not system "
               "roles.",
    ),
    SetupStep(
        key="leave_policy",
        title="Leave policy",
        required=True,
        route="/settings/leave",
        satisfied=_has_leave_policy,
        detail="Leave types and their accrual, seeded and yours to change.",
    ),
    SetupStep(
        key="attendance_policy",
        title="Attendance policy",
        required=True,
        route="/settings/attendance",
        satisfied=_has_attendance_policy,
        detail="Shift timings, grace period and the half-day threshold.",
    ),
    SetupStep(
        key="payroll_compliance",
        title="Payroll and compliance",
        required=False,
        route="/settings/payroll",
        satisfied=_has_payroll_config,
        detail="Salary components, and the statutory rate sets your Finance "
               "Head certifies. Advisory: an organization that does not run "
               "payroll here does not need it.",
    ),
    SetupStep(
        key="email",
        title="Email configuration",
        required=False,
        route="/settings/email",
        satisfied=_email_configured,
        detail="Advisory: per-organization email is not implemented yet, so "
               "mail currently goes out through the deployment's settings.",
    ),
    SetupStep(
        key="employees",
        title="Employees",
        required=False,
        route="/employees",
        satisfied=_has_employees,
        detail="Import a staff list or add people one at a time. Advisory: a "
               "company may finish setup and hire tomorrow.",
    ),
]


def setup_state(organization) -> dict:
    """Every step, whether it is done, and what still blocks finishing."""
    from apps.organization.models import OrgStatus

    steps = [
        {
            "key": step.key,
            "title": step.title,
            "required": step.required,
            "route": step.route,
            "detail": step.detail,
            "complete": bool(step.satisfied(organization)),
        }
        for step in SETUP_STEPS
    ]
    blocking = [s["key"] for s in steps if s["required"] and not s["complete"]]
    return {
        "status": organization.status,
        "in_setup": organization.status == OrgStatus.PENDING_SETUP,
        "steps": steps,
        "completed": sum(1 for s in steps if s["complete"]),
        "total": len(steps),
        "blocking": blocking,
        "can_finish": (
            organization.status == OrgStatus.PENDING_SETUP and not blocking
        ),
    }


def finish_setup(organization, *, actor=None):
    """
    The one transition out of PENDING_SETUP.

    Refuses while a required step is unsatisfied, and names the steps rather
    than saying "setup incomplete" -- the administrator is looking at ten rows
    and needs to know which of them.
    """
    from apps.audit.events import record_event
    from apps.organization.models import OrgStatus

    if organization.status != OrgStatus.PENDING_SETUP:
        raise SetupError(
            f"This organization is {organization.get_status_display()}, not "
            f"pending setup. There is nothing to finish."
        )

    outstanding = [
        step for step in SETUP_STEPS if step.required and not step.satisfied(organization)
    ]
    if outstanding:
        raise SetupError(
            "Setup is not finished: "
            + ", ".join(step.title for step in outstanding)
            + "."
        )

    # Not ACTIVE unconditionally. A customer who finishes setup in the middle
    # of a trial is on a trial, and writing ACTIVE here made the organization
    # disagree with the subscription the customer's own plan page was reading.
    # The mapping lives in the subscriptions service; this asks it.
    from apps.platform.services.subscriptions import status_after_setup

    before = organization.status
    organization.status = status_after_setup(organization)
    organization.save(update_fields=["status", "updated_at"])

    # Bound explicitly rather than inherited. `AuditLog` takes its organization
    # from the acting context, so over HTTP this row would be stamped and from
    # a service call or a command it would not -- and an audit row with no
    # organization is platform-owned, which means the customer's own trail
    # would silently lose the moment they went live. This service knows which
    # organization it is finishing; a service that knows should never depend on
    # ambient state.
    from core.middleware import acting_as

    with acting_as(actor, organization=organization):
        record_event(
            organization,
            actor=actor,
            entity_type="organization.Organization",
            verb="update",
            resource="",
            before={"status": before},
            after={"status": organization.status, "event": "setup_finished"},
        )
    return organization
