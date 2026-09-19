"""
THE PERMISSION MATRIX — the authoritative definition of who can do what.

This file is the enforcement baseline. Every authorization decision in the
system traces back to a cell defined here. Review it as policy, not as code:
`manage.py seed_roles --dump` prints it as a readable table.

READING A ROW
-------------
    Resource.EMPLOYEE: {Action.VIEW: Scope.DEPARTMENT}

means "may view employees within their own department (and its
sub-departments)". Absence of a cell means DENY — there are no negative rules.

THE RULES THAT MATTER MOST
--------------------------
1. CEO holds VIEW/EXPORT only, everywhere. Never a write, anywhere.
2. Only HR_HEAD holds CANDIDATE/REJECT. Not Admin, not Department Heads.
3. Admin holds CANDIDATE/OVERRIDE instead — a separate, audited action.
4. Department Heads hold DEPARTMENT_DECISION/RECOMMEND, which routes to HR.
5. Only ADMIN, HR_HEAD and HR_MANAGER carry `can_manage_users`.
6. RECRUITER and PAYROLL_EXECUTIVE have disjoint permissions — segregation of
   duties: nobody both hires a person and pays them.
7. Only FINANCE_HEAD and ADMIN hold PAYROLL_RUN/APPROVE. HR and payroll staff
   process runs but cannot approve their own work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.access.catalog import (
    Action,
    DashboardKey,
    DepartmentKind,
    Layer,
    Resource,
    RoleCode,
    Scope,
)

# Shorthand
A, R, S = Action, Resource, Scope
Perms = dict[str, dict[str, int]]


def _merge(*blocks: Perms) -> Perms:
    """Combine permission blocks. Later blocks win on conflict."""
    out: Perms = {}
    for block in blocks:
        for resource, actions in block.items():
            out.setdefault(resource, {}).update(actions)
    return out


def _read(scope: int, *resources: str) -> Perms:
    """VIEW + EXPORT at `scope`."""
    return {r: {A.VIEW: scope, A.EXPORT: scope} for r in resources}


def _view(scope: int, *resources: str) -> Perms:
    return {r: {A.VIEW: scope} for r in resources}


def _manage(scope: int, *resources: str) -> Perms:
    """Full CRUD at `scope`."""
    return {
        r: {A.VIEW: scope, A.CREATE: scope, A.EDIT: scope, A.DELETE: scope}
        for r in resources
    }


# ---------------------------------------------------------------------------
# Reusable blocks
# ---------------------------------------------------------------------------

#: Every authenticated person, regardless of role. Their own data only.
SELF_SERVICE: Perms = {
    R.EMPLOYEE: {A.VIEW: S.SELF, A.EDIT: S.SELF},  # serializer limits editable fields
    R.EMPLOYEE_DOCUMENT: {A.VIEW: S.SELF, A.CREATE: S.SELF},
    R.ATTENDANCE: {A.VIEW: S.SELF, A.CREATE: S.SELF},
    R.REGULARIZATION: {A.VIEW: S.SELF, A.CREATE: S.SELF},
    R.LEAVE_REQUEST: {A.VIEW: S.SELF, A.CREATE: S.SELF, A.DELETE: S.SELF},
    R.PAYSLIP: {A.VIEW: S.SELF, A.EXPORT: S.SELF},
    R.SALARY: {A.VIEW: S.SELF},
    #: One's own package summary — the API strips internal HR/Finance notes.
    R.PACKAGE: {A.VIEW: S.SELF},
    R.ASSET_ALLOCATION: {A.VIEW: S.SELF},
    R.EMAIL_ACCOUNT: {A.VIEW: S.SELF},
    R.ONBOARDING: {A.VIEW: S.SELF, A.EDIT: S.SELF},
    # Resigning is a right, not a privilege: CREATE at SELF lets a person
    # submit their own resignation and work their own clearance items. It
    # is a REQUEST — approving it, and the status change that follows, take
    # OFFBOARDING/APPROVE, which self-service does not carry.
    R.OFFBOARDING: {A.VIEW: S.SELF, A.CREATE: S.SELF, A.EDIT: S.SELF},
    R.LETTER: {A.VIEW: S.SELF},
    R.NOTIFICATION: {A.VIEW: S.SELF, A.EDIT: S.SELF},
    R.POLICY_DOC: {A.VIEW: S.ALL},  # everyone must be able to read policy
    R.DASHBOARD_TEAM: {A.VIEW: S.SELF},
    # Self-service metrics. SELF scope means every metric in the registry runs
    # its ordinary code path and returns a queryset containing only this
    # person — their own leave, their own payslip history — so "employees see
    # their own numbers" needs no employee-specific metric and no role check.
    R.REPORT: {A.VIEW: S.SELF},
}

#: Added to any role that conducts interviews.
#: Note there is NO CANDIDATE permission here. Interviewers reach candidate
#: details through their own INTERVIEW rows (the interview serializer embeds a
#: candidate summary), so an interviewer can see the person they are meeting
#: and nobody else. Granting CANDIDATE/VIEW would expose the whole pipeline,
#: since candidates are not person-scoped.
INTERVIEWER: Perms = {
    R.INTERVIEW: {A.VIEW: S.SELF, A.EDIT: S.SELF},
    R.INTERVIEW_FEEDBACK: {A.VIEW: S.SELF, A.CREATE: S.SELF, A.EDIT: S.SELF},
    # Interviewers see the applications and candidates they are scheduled
    # against — SELF scope, which the recruitment viewsets resolve as "rows I
    # have an interview on". Previously S.ALL, on the reasoning that the
    # workflow engine would restrict them anyway; that held for ACTING but not
    # for READING, so an interviewer could list the entire pipeline.
    R.APPLICATION: {A.VIEW: S.SELF},
    R.CANDIDATE: {A.VIEW: S.SELF},
    # Read the pipeline DEFINITION. Not sensitive — it is the company's hiring
    # process, and the stage names are already visible on every application
    # these roles can open. Without it the stage tracker renders empty for
    # everyone except HR, which makes "where is this candidate?" unanswerable
    # for the people being asked to assess them. VIEW only; workflows are
    # authored under change control.
    R.HIRING_WORKFLOW: {A.VIEW: S.ALL},
    # The JOB they are scheduled against. SELF resolves in the recruitment
    # scoper to "jobs I hold an interview on (or own as recruiter)" — without
    # it the application page's job card came back 403 for the very person
    # asked to assess the candidate.
    R.JOB_OPENING: {A.VIEW: S.SELF},
    # Reference lists a job posting points at: location names and designation
    # titles. VIEW only, same reasoning as the recruiter's org-configuration
    # reads below — reading a job's location is not authoring locations, and
    # the org-config write invariant asserts that separation.
    R.DESIGNATION: {A.VIEW: S.ALL},
    R.LOCATION: {A.VIEW: S.ALL},
}

#: A manager over a reporting tree.
TEAM_LEAD: Perms = _merge(
    _view(S.TEAM, R.EMPLOYEE, R.ATTENDANCE, R.ASSET_ALLOCATION),
    {
        R.LEAVE_REQUEST: {A.VIEW: S.TEAM, A.APPROVE: S.TEAM},
        R.REGULARIZATION: {A.VIEW: S.TEAM, A.APPROVE: S.TEAM},
        R.DASHBOARD_TEAM: {A.VIEW: S.TEAM},
        R.REPORT: {A.VIEW: S.TEAM, A.EXPORT: S.TEAM},
        # A manager writes the probation ASSESSMENT for their own reports.
        # EDIT, never DECIDE: the recommendation is advice, and the
        # employment decision stays with HR — the same split recruitment
        # draws between a department recommendation and an HR final decision.
        R.PROBATION_REVIEW: {A.VIEW: S.TEAM, A.EDIT: S.TEAM},
        R.ONBOARDING: {A.VIEW: S.TEAM, A.EDIT: S.TEAM},
        # A manager confirms the handover for their own leavers. EDIT, not
        # APPROVE: signing off a task is not authorising the exit.
        R.OFFBOARDING: {A.VIEW: S.TEAM, A.EDIT: S.TEAM},
    },
)

#: A Layer-2 department head: everything in their department, read-heavy.
DEPARTMENT_HEAD: Perms = _merge(
    _read(S.DEPARTMENT, R.EMPLOYEE, R.ATTENDANCE, R.ASSET_ALLOCATION, R.ONBOARDING),
    {
        R.LEAVE_REQUEST: {A.VIEW: S.DEPARTMENT, A.APPROVE: S.DEPARTMENT},
        R.REGULARIZATION: {A.VIEW: S.DEPARTMENT, A.APPROVE: S.DEPARTMENT},
        # Same split as managers: assess, never decide.
        R.PROBATION_REVIEW: {A.VIEW: S.DEPARTMENT, A.EDIT: S.DEPARTMENT},
        R.EMPLOYEE_DOCUMENT: {A.VIEW: S.DEPARTMENT},
        R.OFFBOARDING: {A.VIEW: S.DEPARTMENT, A.EDIT: S.DEPARTMENT},
        R.DASHBOARD_DEPARTMENT: {A.VIEW: S.DEPARTMENT},
        R.REPORT: {A.VIEW: S.DEPARTMENT, A.EXPORT: S.DEPARTMENT},
        R.AUDIT_LOG: {A.VIEW: S.DEPARTMENT},
        R.DEPARTMENT: {A.VIEW: S.ALL},
        R.DESIGNATION: {A.VIEW: S.ALL},
        R.LOCATION: {A.VIEW: S.ALL},
    },
)

#: Layer-2 heads of departments that hire: they see their pipeline and make the
#: departmental recommendation that routes to HR Head.
#
#: DEPARTMENT, not ALL. Recruitment rows hang off a job rather than a person,
#: so the generic scoper cannot narrow them and these were originally granted
#: at ALL with the department check left to the service layer. That protected
#: ACTIONS but not LISTS: a Medical Director could read the operations pipeline.
#: `DepartmentScopedMixin` in the recruitment API now makes DEPARTMENT
#: meaningful for these resources, so the grant matches the approved
#: visibility matrix instead of relying on a second layer to compensate.
DEPARTMENT_HIRING: Perms = {
    # As for interviewers: the pipeline definition is process, not data.
    R.HIRING_WORKFLOW: {A.VIEW: S.ALL},
    R.JOB_OPENING: {A.VIEW: S.DEPARTMENT},
    R.CANDIDATE: {A.VIEW: S.DEPARTMENT},
    R.APPLICATION: {A.VIEW: S.DEPARTMENT},
    R.INTERVIEW: {A.VIEW: S.DEPARTMENT},
    R.INTERVIEW_FEEDBACK: {A.VIEW: S.DEPARTMENT},
    # The Level-1 decision. RECOMMEND, never REJECT — the terminal action
    # belongs to HR Head alone.
    R.DEPARTMENT_DECISION: {
        A.VIEW: S.DEPARTMENT, A.RECOMMEND: S.DEPARTMENT, A.CREATE: S.DEPARTMENT
    },
}


# ---------------------------------------------------------------------------
# Role specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleSpec:
    code: str
    name: str
    layer: int
    description: str
    dashboard_key: str
    permissions: Perms
    is_read_only: bool = False
    can_manage_users: bool = False
    requires_employee: bool = True
    is_grantable: bool = True
    department_kind: str = ""
    max_seats: int | None = None


#: Every resource the CEO may read. Deliberately enumerated rather than
#: "everything minus a few": a new resource must be consciously added here, so
#: adding one never silently widens executive visibility.
CEO_READABLE = (
    R.EMPLOYEE, R.EMPLOYEE_DOCUMENT, R.PROBATION_REVIEW,
    R.DEPARTMENT, R.DESIGNATION, R.LOCATION, R.USER,
    R.ASSET, R.ASSET_ALLOCATION, R.EMAIL_ACCOUNT,
    R.JOB_OPENING, R.CANDIDATE, R.APPLICATION, R.INTERVIEW,
    R.INTERVIEW_FEEDBACK, R.DEPARTMENT_DECISION, R.OFFER,
    R.ONBOARDING, R.OFFBOARDING, R.LETTER,
    # ATTENDANCE_DEVICE is deliberately absent: the eSSL plumbing (device
    # serials, sync errors, ID mappings) is operational detail, not the
    # governance picture — the CEO reads attendance itself, not the pipes.
    R.ATTENDANCE, R.REGULARIZATION, R.LEAVE_REQUEST,
    R.SALARY, R.PAYROLL_RUN, R.PAYSLIP, R.PAYROLL_ADJUSTMENT,
    # Statutory rates and their verification state. Read-only like everything
    # else in this list: which rates payroll runs on, and whether Finance has
    # verified them, is a governance question the CEO is entitled to see —
    # but certifying one stays the Finance Head's professional judgement.
    R.STATUTORY_CONFIG,
    # Their OWN notifications. Without this the CEO cannot reach the endpoint
    # at all — not "sees an empty list", but 403 — leaving the one principal
    # who most needs to be told things as the only one who cannot be.
    #
    # Marking them read stays blocked, by the read-only clamp and again by
    # ReadOnlyPrincipalMiddleware. That is a real limitation and the right
    # trade: the CEO's list is read-only like everything else they touch, and
    # carving out an exception would put a hole in a control that is currently
    # absolute and therefore easy to reason about.
    R.NOTIFICATION,
    R.POLICY_DOC, R.AUDIT_LOG, R.REPORT,
    R.DASHBOARD_ORG, R.DASHBOARD_DEPARTMENT, R.DASHBOARD_TEAM,
)

#: Everything Admin manages outright.
ADMIN_MANAGED = (
    R.USER, R.ROLE, R.DEPARTMENT, R.DESIGNATION, R.LOCATION, R.ORG_SETTINGS,
    R.EMPLOYEE, R.EMPLOYEE_DOCUMENT, R.PROBATION_REVIEW,
    R.ASSET, R.ASSET_ALLOCATION, R.EMAIL_ACCOUNT,
    R.HIRING_WORKFLOW, R.JOB_OPENING, R.CANDIDATE, R.APPLICATION,
    R.INTERVIEW, R.INTERVIEW_FEEDBACK, R.DEPARTMENT_DECISION, R.OFFER,
    R.ONBOARDING, R.OFFBOARDING, R.LETTER,
    R.ATTENDANCE, R.REGULARIZATION, R.ATTENDANCE_DEVICE,
    R.LEAVE_REQUEST, R.LEAVE_POLICY,
    R.SALARY, R.PAYROLL_RUN, R.PAYSLIP, R.PAYROLL_ADJUSTMENT,
    R.STATUTORY_CONFIG, R.POLICY_DOC, R.NOTIFICATION, R.REPORT,
)


ROLE_SPECS: tuple[RoleSpec, ...] = (
    # ======================= LAYER 1 =======================
    RoleSpec(
        code=RoleCode.CEO,
        name="CEO",
        layer=Layer.LEADERSHIP,
        description="Organization-wide executive oversight. View-only.",
        dashboard_key=DashboardKey.CEO,
        is_read_only=True,
        requires_employee=False,
        max_seats=1,
        # VIEW + EXPORT at ALL, and nothing else. The engine additionally
        # strips every write action for a read-only role, so even a bad edit
        # to this block cannot grant one.
        permissions=_read(S.ALL, *CEO_READABLE),
    ),
    RoleSpec(
        code=RoleCode.ADMIN,
        name="Admin",
        layer=Layer.LEADERSHIP,
        description="Full system management across every module.",
        dashboard_key=DashboardKey.ADMIN,
        can_manage_users=True,
        requires_employee=False,
        is_grantable=False,  # bootstrap only — never grantable in-app
        max_seats=2,
        permissions=_merge(
            _manage(S.ALL, *ADMIN_MANAGED),
            {
                R.AUDIT_LOG: {A.VIEW: S.ALL, A.EXPORT: S.ALL},  # never mutable
                R.PAYROLL_RUN: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.APPROVE: S.ALL, A.EXPORT: S.ALL,
                },
                # The leave routing rule: HEADS' leave is decided by Admin —
                # including HR Head's own, which nobody may self-approve. The
                # leave service enforces the band; this grant is what lets
                # Admin act on it at all.
                R.LEAVE_REQUEST: {A.VIEW: S.ALL, A.APPROVE: S.ALL, A.DELETE: S.ALL},
                # `_manage` covers CRUD only, so verification and rejection have
                # to be named here. Without this Admin could collect documents
                # and never clear the queue — which is the gap that separating
                # the actions opens if you forget to re-grant them.
                R.EMPLOYEE_DOCUMENT: {A.APPROVE: S.ALL, A.REJECT: S.ALL},
                # Admin does NOT get CANDIDATE/REJECT. Terminal rejection is
                # HR Head's authority. Admin may only OVERRIDE a decision that
                # has already been made — a distinct, clearly-labelled and
                # fully-audited action.
                R.CANDIDATE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.OVERRIDE: S.ALL, A.IMPORT: S.ALL,
                },
                # Bulk hire from a staff list. `_manage` above grants CRUD
                # only, so IMPORT has to be named -- which is the point of it
                # being its own action: the first thing a company does on this
                # platform is bring its existing people onto it, and the last
                # thing it wants is that ability spread wider than the people
                # who already hire.
                R.EMPLOYEE: {A.IMPORT: S.ALL},
                R.APPLICATION: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.OVERRIDE: S.ALL,
                },
                R.DASHBOARD_ORG: {A.VIEW: S.ALL},
                R.DASHBOARD_DEPARTMENT: {A.VIEW: S.ALL},
                R.DASHBOARD_TEAM: {A.VIEW: S.ALL},
            },
        ),
    ),

    # ======================= LAYER 2 =======================
    RoleSpec(
        code=RoleCode.MEDICAL_DIRECTOR,
        name="Medical Director",
        layer=Layer.DEPARTMENT_HEAD,
        description="Heads the clinical department. Conducts the clinical interview round.",
        dashboard_key=DashboardKey.DEPARTMENT,
        department_kind=DepartmentKind.MEDICAL,
        permissions=_merge(
            SELF_SERVICE,
            DEPARTMENT_HEAD,
            DEPARTMENT_HIRING,
            # The office's hiring process seats this head IN the interview
            # chair (the clinical round). The engine maps "pass" at an
            # interview stage to INTERVIEW_FEEDBACK/CREATE — the same grant
            # HR Head carries for the rounds workflows put them on. SELF:
            # only interviews they are booked on; VIEW stays DEPARTMENT
            # from the hiring block above. INTERVIEW/EDIT at SELF is what
            # lets them reject a booked time on their own interviews and
            # trigger the candidate's rebooking — as the INTERVIEWER bundle
            # already grants every other interviewing role.
            {
                R.INTERVIEW: {A.EDIT: S.SELF},
                R.INTERVIEW_FEEDBACK: {A.CREATE: S.SELF, A.EDIT: S.SELF},
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.OPERATIONAL_HEAD,
        name="Operational Head",
        layer=Layer.DEPARTMENT_HEAD,
        description="Heads operations. Conducts the operations interview round.",
        dashboard_key=DashboardKey.DEPARTMENT,
        department_kind=DepartmentKind.OPERATIONS,
        permissions=_merge(
            SELF_SERVICE,
            DEPARTMENT_HEAD,
            DEPARTMENT_HIRING,
            # As for the Medical Director: the operations rounds are theirs.
            {
                R.INTERVIEW: {A.EDIT: S.SELF},
                R.INTERVIEW_FEEDBACK: {A.CREATE: S.SELF, A.EDIT: S.SELF},
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.HR_HEAD,
        name="HR Head",
        layer=Layer.DEPARTMENT_HEAD,
        description=(
            "Owns the employee lifecycle and coordinates recruitment. "
            "Sole authority for final candidate rejection."
        ),
        dashboard_key=DashboardKey.DEPARTMENT,
        department_kind=DepartmentKind.HR,
        can_manage_users=True,
        permissions=_merge(
            SELF_SERVICE,
            DEPARTMENT_HEAD,
            # HR hires into EVERY department, so people management is org-wide
            # even though HR's own departmental view is scoped. This asymmetry
            # is exactly what a (resource, action) -> scope grain expresses and
            # a flat per-module grid cannot.
            _manage(S.ALL, R.EMPLOYEE, R.EMPLOYEE_DOCUMENT, R.ONBOARDING, R.LETTER),
            # Attesting to a document and refusing one are their own grants,
            # separate from the CRUD above: an organisation may want somebody
            # who collects paperwork without vouching for it, and that is only
            # expressible if the three are distinct.
            {R.EMPLOYEE_DOCUMENT: {A.APPROVE: S.ALL, A.REJECT: S.ALL}},
            # Bulk hiring from a staff list, on top of the CRUD above and
            # separately grantable from it -- the same split as CANDIDATE, and
            # for the same reason: hiring one person and hiring two hundred in
            # one action are different amounts of trust, and the second can be
            # revoked without stopping the first.
            {R.EMPLOYEE: {A.IMPORT: S.ALL}},
            _manage(S.ALL, R.JOB_OPENING, R.CANDIDATE, R.APPLICATION, R.INTERVIEW),
            _manage(S.ALL, R.OFFER, R.HIRING_WORKFLOW),
            {
                R.USER: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.ROLE: {A.VIEW: S.ALL},  # assigns roles, does not define them
                # THE terminal decision authority. APPROVE selects, REJECT
                # rejects — no other role holds either.
                R.CANDIDATE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.APPROVE: S.ALL, A.REJECT: S.ALL, A.IMPORT: S.ALL,
                },
                R.APPLICATION: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.APPROVE: S.ALL, A.REJECT: S.ALL,
                },
                R.OFFER: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.DEPARTMENT_DECISION: {A.VIEW: S.ALL},  # reviews, cannot author
                # VIEW org-wide as before. CREATE/EDIT at SELF so that when a
                # workflow names this role as a round's interviewer — a
                # recruiter screening call, an HR culture round — the holder can
                # record that round's feedback and outcome for interviews THEY
                # are booked on, exactly as the department interviewer roles
                # can. Without it the engine (which maps "pass" at an interview
                # stage to INTERVIEW_FEEDBACK/CREATE) refused the very person
                # the workflow had put in the chair.
                R.INTERVIEW_FEEDBACK: {A.VIEW: S.ALL, A.CREATE: S.SELF, A.EDIT: S.SELF},
                # DECIDE is the confirm/extend/terminate authority. EDIT lets
                # HR record an assessment too, for the case where no manager
                # is in place to write one.
                R.PROBATION_REVIEW: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.DECIDE: S.ALL,
                },
                # APPROVE closes a checklist over outstanding mandatory items —
                # a heavier act than ticking one off, hence a separate action.
                R.ONBOARDING: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.APPROVE: S.ALL,
                },
                R.ASSET: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # DELETE is the write-off: accepting that company property is
                # not coming back, which is how an exit gets past the asset gate.
                R.ASSET_ALLOCATION: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.DELETE: S.ALL,
                },
                R.EMAIL_ACCOUNT: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # APPROVE is the exit authority: accepting a resignation,
                # waiving notice, releasing early, and the final sign-off
                # that lets someone reach EXITED.
                R.OFFBOARDING: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.APPROVE: S.ALL,
                },
                R.LEAVE_REQUEST: {A.VIEW: S.ALL, A.APPROVE: S.ALL},
                R.LEAVE_POLICY: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.REGULARIZATION: {A.VIEW: S.ALL, A.APPROVE: S.ALL},
                R.ATTENDANCE: {A.VIEW: S.ALL, A.EDIT: S.ALL},
                # The eSSL biometric integration: devices, employee mappings,
                # sync controls. IMPORT is the sync trigger. Employees hold
                # nothing here — absence is deny.
                R.ATTENDANCE_DEVICE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.IMPORT: S.ALL,
                },
                R.POLICY_DOC: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # HR co-owns payroll operations with Finance: maintains salary
                # structures, prepares and reviews runs, configures rules. What
                # HR Head still cannot do is RELEASE money or CERTIFY rates —
                # PAYROLL_RUN/APPROVE and STATUTORY_CONFIG/APPROVE stay with
                # the Finance Head (see the invariants below), so the flow is
                # HR prepares/reviews → Finance approves.
                R.SALARY: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.PAYROLL_RUN: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.EXPORT: S.ALL,
                },
                R.PAYSLIP: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                R.PAYROLL_ADJUSTMENT: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.APPROVE: S.ALL,
                },
                # Configure statutory rules (PF/ESIC/PT/TDS percentages and
                # limits); Finance certifies them — a natural four-eyes split.
                R.STATUTORY_CONFIG: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # Custom package schedules: HR Head and Finance Head alone
                # configure and approve releases — by explicit specification.
                R.PACKAGE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.APPROVE: S.ALL,
                },
                R.AUDIT_LOG: {A.VIEW: S.ALL},
                R.REPORT: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                R.DASHBOARD_DEPARTMENT: {A.VIEW: S.ALL},
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.FINANCE_HEAD,
        name="Accounts & Finance Head",
        layer=Layer.DEPARTMENT_HEAD,
        description="Owns payroll and financial operations. Approves payroll runs.",
        dashboard_key=DashboardKey.DEPARTMENT,
        department_kind=DepartmentKind.FINANCE,
        permissions=_merge(
            SELF_SERVICE,
            DEPARTMENT_HEAD,
            _manage(S.ALL, R.SALARY, R.PAYROLL_ADJUSTMENT),
            {
                # A drafted bonus is a proposal until a head approves it; the
                # run only ever pays approved adjustments. The Payroll
                # Executive can draft but never approve — same split as runs.
                R.PAYROLL_ADJUSTMENT: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.APPROVE: S.ALL,
                },
                # The approval authority. Payroll and HR staff process runs;
                # only Finance Head and Admin approve them. Whoever prepares
                # the money must not be the one who releases it.
                R.PAYROLL_RUN: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.APPROVE: S.ALL, A.EXPORT: S.ALL,
                },
                # DELETE is the five-day correction window: a freshly
                # generated payslip may be withdrawn by the Finance Head with
                # a recorded reason; past the window the payslip is locked
                # for everyone, Finance included. The window itself lives in
                # settings (PAYSLIP_DELETE_WINDOW_DAYS), not in code.
                R.PAYSLIP: {A.VIEW: S.ALL, A.EXPORT: S.ALL, A.DELETE: S.ALL},
                # Finance owns the settlement gate on EVERY exit, not only
                # those from their own department — so ALL, overriding the
                # DEPARTMENT scope inherited from the department-head block.
                # APPROVE clears a settlement; it does not let finance sign off
                # HR's clearance items, because the clearance service checks
                # item ownership as a second layer.
                R.OFFBOARDING: {A.VIEW: S.ALL, A.EDIT: S.ALL, A.APPROVE: S.ALL},
                # APPROVE here is the statutory-verification authority: signing
                # off that a rate matches the gazette. Finance Head ONLY —
                # Admin can edit a draft rate set but can never certify one.
                R.STATUTORY_CONFIG: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.APPROVE: S.ALL
                },
                R.EMPLOYEE: {A.VIEW: S.ALL},  # payroll spans every department
                # Custom package schedules — the other half of the pair with
                # HR Head; only these two configure and approve releases.
                R.PACKAGE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.DELETE: S.ALL, A.APPROVE: S.ALL,
                },
                R.REPORT: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                R.AUDIT_LOG: {A.VIEW: S.ALL},
            },
        ),
    ),

    # ======================= LAYER 3 =======================
    RoleSpec(
        code=RoleCode.SENIOR_DOCTOR,
        name="Senior Doctor",
        layer=Layer.MANAGER,
        description="Senior clinician. Conducts clinical interviews.",
        dashboard_key=DashboardKey.MANAGER,
        department_kind=DepartmentKind.MEDICAL,
        permissions=_merge(SELF_SERVICE, TEAM_LEAD, INTERVIEWER),
    ),
    RoleSpec(
        code=RoleCode.OPERATIONS_MANAGER,
        name="Operations Manager",
        layer=Layer.MANAGER,
        description="Manages operational staff. Conducts operational interviews.",
        dashboard_key=DashboardKey.MANAGER,
        department_kind=DepartmentKind.OPERATIONS,
        permissions=_merge(SELF_SERVICE, TEAM_LEAD, INTERVIEWER),
    ),
    RoleSpec(
        code=RoleCode.HR_MANAGER,
        name="HR Manager",
        layer=Layer.MANAGER,
        description="Runs recruitment and HR operations delegated by HR Head.",
        dashboard_key=DashboardKey.MANAGER,
        department_kind=DepartmentKind.HR,
        can_manage_users=True,  # delegated; narrower than HR Head
        permissions=_merge(
            SELF_SERVICE,
            TEAM_LEAD,
            _manage(S.ALL, R.CANDIDATE, R.APPLICATION),
            {
                # SCHEDULING IS THE HR HEAD'S. No INTERVIEW/CREATE here: booking
                # an interview and sending a slot-booking link are the HR Head's
                # acts alone (Admin excepted, as everywhere). This role still
                # SEES every interview, and EDIT at SELF keeps the interviewer
                # duties — rescheduling, cancelling or rejecting the time of a
                # round THEY are booked to conduct.
                R.INTERVIEW: {A.VIEW: S.ALL, A.EDIT: S.SELF},
                R.USER: {A.VIEW: S.ALL, A.CREATE: S.ALL},  # create, never delete
                # Bulk candidate ingest, on top of the CRUD from _manage above.
                R.CANDIDATE: {A.IMPORT: S.ALL},
                # And bulk hiring: HR Manager already creates employees one at
                # a time, and a migration onto the platform is their job.
                R.EMPLOYEE: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.IMPORT: S.ALL,
                },
                R.EMPLOYEE_DOCUMENT: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL,
                    A.APPROVE: S.ALL, A.REJECT: S.ALL,
                },
                R.JOB_OPENING: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # VIEW org-wide as before. CREATE/EDIT at SELF so that when a
                # workflow names this role as a round's interviewer — a
                # recruiter screening call, an HR culture round — the holder can
                # record that round's feedback and outcome for interviews THEY
                # are booked on, exactly as the department interviewer roles
                # can. Without it the engine (which maps "pass" at an interview
                # stage to INTERVIEW_FEEDBACK/CREATE) refused the very person
                # the workflow had put in the chair.
                R.INTERVIEW_FEEDBACK: {A.VIEW: S.ALL, A.CREATE: S.SELF, A.EDIT: S.SELF},
                R.DEPARTMENT_DECISION: {A.VIEW: S.ALL},
                R.ONBOARDING: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.LETTER: {A.VIEW: S.ALL, A.CREATE: S.ALL},
                R.PROBATION_REVIEW: {A.VIEW: S.ALL, A.CREATE: S.ALL},
                R.ASSET_ALLOCATION: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.EMAIL_ACCOUNT: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # VIEW only: every leave request is decided by the HR Head
                # (the service refuses anyone else), so HR Manager reads the
                # records without an approval grant. The TEAM-scoped APPROVE
                # inherited from TEAM_LEAD stays — it powers the
                # policy-enabled reporting-manager path, nothing more.
                R.LEAVE_REQUEST: {A.VIEW: S.ALL},
                R.REGULARIZATION: {A.VIEW: S.ALL, A.APPROVE: S.ALL},
                R.ATTENDANCE: {A.VIEW: S.ALL},
                # Reads the eSSL integration's state; running or reconfiguring
                # it stays with the HR Head and Admin.
                R.ATTENDANCE_DEVICE: {A.VIEW: S.ALL},
                R.REPORT: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                # ORG CONFIGURATION: read, and only read — same reasoning as
                # the recruiter's block below. This role creates employees and
                # job openings org-wide; the forms' department, designation
                # (which also serves /levels/), and clinic/location dropdowns
                # are these lists, and CREATE without READ was unusable.
                # Authoring the lists stays with Admin and HR Head, asserted
                # by the org-config write invariant.
                R.DEPARTMENT: {A.VIEW: S.ALL},
                R.DESIGNATION: {A.VIEW: S.ALL},
                R.LOCATION: {A.VIEW: S.ALL},
                # The pipeline DEFINITION, same reasoning as the interviewer
                # block: without it the stage tracker on every application
                # this role manages renders "Workflow unavailable".
                R.HIRING_WORKFLOW: {A.VIEW: S.ALL},
                # Explicitly NOT granted: CANDIDATE/REJECT, ROLE/*, SALARY/*.
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.ACCOUNTS_MANAGER,
        name="Accounts Manager",
        layer=Layer.MANAGER,
        description="Processes payroll under the Finance Head's authority.",
        dashboard_key=DashboardKey.MANAGER,
        department_kind=DepartmentKind.FINANCE,
        permissions=_merge(
            SELF_SERVICE,
            TEAM_LEAD,
            {
                R.SALARY: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                R.PAYROLL_ADJUSTMENT: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # Processes and prepares — no APPROVE. Segregation of duties.
                R.PAYROLL_RUN: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.EXPORT: S.ALL
                },
                R.PAYSLIP: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                # Prepares the settlement; clearing it is the Finance Head's.
                R.OFFBOARDING: {A.VIEW: S.ALL, A.EDIT: S.ALL},
                R.EMPLOYEE: {A.VIEW: S.ALL},
                R.STATUTORY_CONFIG: {A.VIEW: S.ALL},
                R.REPORT: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
            },
        ),
    ),

    # ======================= LAYER 4 =======================
    RoleSpec(
        code=RoleCode.CLINIC_DOCTOR,
        name="Clinic Doctor",
        layer=Layer.EXECUTIVE,
        description="Clinical staff. Interviews candidates when assigned.",
        dashboard_key=DashboardKey.EXECUTIVE,
        department_kind=DepartmentKind.MEDICAL,
        permissions=_merge(SELF_SERVICE, INTERVIEWER),
    ),
    RoleSpec(
        code=RoleCode.CRE,
        name="CRE",
        layer=Layer.EXECUTIVE,
        description="Customer relations executive. Interviews when assigned.",
        dashboard_key=DashboardKey.EXECUTIVE,
        department_kind=DepartmentKind.OPERATIONS,
        permissions=_merge(SELF_SERVICE, INTERVIEWER),
    ),
    RoleSpec(
        code=RoleCode.RECRUITER,
        name="Recruiter",
        layer=Layer.EXECUTIVE,
        description="Recruitment operations: openings and candidates. Scheduling stays with the HR Head.",
        dashboard_key=DashboardKey.EXECUTIVE,
        department_kind=DepartmentKind.HR,
        permissions=_merge(
            SELF_SERVICE,
            _manage(S.ALL, R.CANDIDATE, R.APPLICATION),
            {
                # SCHEDULING IS THE HR HEAD'S — same reasoning as the HR
                # Manager block above: view every interview, act only on a
                # round this person is themselves booked to conduct. No
                # CREATE, so no booking and no slot-invite links.
                R.INTERVIEW: {A.VIEW: S.ALL, A.EDIT: S.SELF},
                R.JOB_OPENING: {A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL},
                # Bulk candidate ingest. Separately grantable from
                # CANDIDATE/CREATE so it can be revoked without also
                # stopping this role adding a walk-in candidate.
                R.CANDIDATE: {A.IMPORT: S.ALL},
                # VIEW org-wide as before. CREATE/EDIT at SELF so that when a
                # workflow names this role as a round's interviewer — a
                # recruiter screening call, an HR culture round — the holder can
                # record that round's feedback and outcome for interviews THEY
                # are booked on, exactly as the department interviewer roles
                # can. Without it the engine (which maps "pass" at an interview
                # stage to INTERVIEW_FEEDBACK/CREATE) refused the very person
                # the workflow had put in the chair.
                R.INTERVIEW_FEEDBACK: {A.VIEW: S.ALL, A.CREATE: S.SELF, A.EDIT: S.SELF},
                R.DEPARTMENT_DECISION: {A.VIEW: S.ALL},
                R.OFFER: {A.VIEW: S.ALL},
                R.ONBOARDING: {A.VIEW: S.ALL, A.CREATE: S.ALL},
                R.HIRING_WORKFLOW: {A.VIEW: S.ALL},
                R.REPORT: {A.VIEW: S.ALL},
                # ORG CONFIGURATION: read, and only read.
                #
                # A job opening points at a department, a workflow, a target
                # role, a designation, a location and a seniority band. Holding
                # CREATE on JOB_OPENING without being able to READ those lists
                # was a permission that could not actually be exercised — the
                # form's required dropdowns came back 403 and empty, so a
                # recruiter could never assemble a valid opening.
                #
                # VIEW only, deliberately: being able to pick a department is
                # not being able to invent one. Creating or editing the
                # organisation's own structure stays with Admin and HR Head,
                # and the invariants below assert that separation rather than
                # trusting this comment.
                #
                # DESIGNATION also covers seniority bands — EmployeeLevel is
                # served by EmployeeLevelViewSet under Resource.DESIGNATION, so
                # this one grant opens both /designations/ and /levels/.
                #
                # ROLE/VIEW needs no `can_manage_users`. That gate strips WRITE
                # actions on USER and ROLE and never reads, which is the same
                # rule that lets the CEO see accounts without touching them.
                R.DEPARTMENT: {A.VIEW: S.ALL},
                R.DESIGNATION: {A.VIEW: S.ALL},
                R.LOCATION: {A.VIEW: S.ALL},
                R.ROLE: {A.VIEW: S.ALL},
                # SEGREGATION OF DUTIES: no payroll resource appears here. A
                # recruiter must never be able to hire a person and then pay
                # them. Compare PAYROLL_EXECUTIVE, which has no recruitment.
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.PAYROLL_EXECUTIVE,
        name="Payroll Executive",
        layer=Layer.EXECUTIVE,
        description="Payroll operations: processing, payslips, payroll records.",
        dashboard_key=DashboardKey.EXECUTIVE,
        department_kind=DepartmentKind.FINANCE,
        permissions=_merge(
            SELF_SERVICE,
            {
                R.PAYROLL_RUN: {
                    A.VIEW: S.ALL, A.CREATE: S.ALL, A.EDIT: S.ALL, A.EXPORT: S.ALL
                },
                R.PAYSLIP: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                R.PAYROLL_ADJUSTMENT: {A.VIEW: S.ALL, A.CREATE: S.ALL},
                R.SALARY: {A.VIEW: S.ALL},
                #: Sees package schedules to process payroll against them —
                #: never changes the rules, never approves a release.
                R.PACKAGE: {A.VIEW: S.ALL},
                R.EMPLOYEE: {A.VIEW: S.ALL},
                R.STATUTORY_CONFIG: {A.VIEW: S.ALL},
                R.REPORT: {A.VIEW: S.ALL, A.EXPORT: S.ALL},
                # No APPROVE, and no recruitment resource whatsoever.
            },
        ),
    ),
    RoleSpec(
        code=RoleCode.EXECUTIVE,
        name="Executive",
        layer=Layer.EXECUTIVE,
        description="Operational executive. Self-service and assigned work.",
        dashboard_key=DashboardKey.EXECUTIVE,
        permissions=dict(SELF_SERVICE),
    ),

    # ======================= LAYER 5 =======================
    RoleSpec(
        code=RoleCode.THERAPIST,
        name="Therapist",
        layer=Layer.STAFF,
        description="Clinical staff. Self-service only.",
        dashboard_key=DashboardKey.SELF,
        department_kind=DepartmentKind.MEDICAL,
        permissions=dict(SELF_SERVICE),
    ),
    RoleSpec(
        code=RoleCode.OFFICE_BOY,
        name="Office Boy",
        layer=Layer.STAFF,
        description="Support staff. Self-service only.",
        dashboard_key=DashboardKey.SELF,
        department_kind=DepartmentKind.OPERATIONS,
        permissions=dict(SELF_SERVICE),
    ),
    RoleSpec(
        code=RoleCode.EMPLOYEE,
        name="Employee",
        layer=Layer.STAFF,
        description="General staff. Self-service only.",
        dashboard_key=DashboardKey.SELF,
        permissions=dict(SELF_SERVICE),
    ),
)


# ---------------------------------------------------------------------------
# Invariants — asserted at import, so a bad edit fails fast and loudly
# ---------------------------------------------------------------------------

def _assert_invariants() -> None:
    from core.access.catalog import WRITE_ACTIONS

    by_code = {spec.code: spec for spec in ROLE_SPECS}

    assert len(by_code) == len(ROLE_SPECS), "Duplicate role code in ROLE_SPECS."
    assert len(ROLE_SPECS) == 18, f"Expected 18 roles, found {len(ROLE_SPECS)}."

    for spec in ROLE_SPECS:
        # A read-only role must hold no write anywhere.
        if spec.is_read_only:
            for resource, actions in spec.permissions.items():
                offending = set(actions) & WRITE_ACTIONS
                assert not offending, (
                    f"Read-only role '{spec.code}' has write actions "
                    f"{sorted(offending)} on '{resource}'."
                )
            assert not spec.can_manage_users, (
                f"Read-only role '{spec.code}' cannot manage users."
            )

        # User-management resources require the structural flag.
        for resource in (Resource.USER, Resource.ROLE):
            writes = set(spec.permissions.get(resource, {})) & WRITE_ACTIONS
            if writes:
                assert spec.can_manage_users, (
                    f"Role '{spec.code}' has write actions {sorted(writes)} on "
                    f"'{resource}' without can_manage_users."
                )

    # Exactly one role may perform terminal candidate rejection.
    rejecters = [
        s.code
        for s in ROLE_SPECS
        if Action.REJECT in s.permissions.get(Resource.APPLICATION, {})
    ]
    assert rejecters == [RoleCode.HR_HEAD], (
        f"Only HR Head may hold APPLICATION/REJECT; found {rejecters}."
    )

    # Admin overrides, and must NOT be able to reject directly.
    admin = by_code[RoleCode.ADMIN]
    assert Action.REJECT not in admin.permissions.get(Resource.APPLICATION, {}), (
        "Admin must not hold APPLICATION/REJECT — only OVERRIDE."
    )
    assert Action.OVERRIDE in admin.permissions.get(Resource.APPLICATION, {}), (
        "Admin must hold APPLICATION/OVERRIDE."
    )

    # Payroll approval is restricted to Finance Head and Admin.
    approvers = {
        s.code
        for s in ROLE_SPECS
        if Action.APPROVE in s.permissions.get(Resource.PAYROLL_RUN, {})
    }
    assert approvers == {RoleCode.FINANCE_HEAD, RoleCode.ADMIN}, (
        f"Payroll approval must be Finance Head + Admin only; found {sorted(approvers)}."
    )

    # Segregation of duties: recruitment and payroll must not overlap.
    #
    # Measured on SCOPE, not mere presence. Every role inherits SELF_SERVICE,
    # which legitimately includes "view my own payslip" — a Recruiter still
    # gets paid. What must never happen is one person reaching *other people's*
    # payroll and also hiring them. So the rule is: no scope above SELF on the
    # opposing domain.
    recruitment_resources = {
        Resource.CANDIDATE, Resource.APPLICATION, Resource.JOB_OPENING, Resource.INTERVIEW
    }
    payroll_resources = {
        Resource.PAYROLL_RUN, Resource.PAYSLIP, Resource.PAYROLL_ADJUSTMENT, Resource.SALARY
    }

    def _beyond_self(spec: RoleSpec, resources: set[str]) -> list[str]:
        return sorted(
            f"{resource}.{action}"
            for resource in resources & set(spec.permissions)
            for action, scope in spec.permissions[resource].items()
            if scope > Scope.SELF
        )

    recruiter_payroll = _beyond_self(by_code[RoleCode.RECRUITER], payroll_resources)
    assert not recruiter_payroll, (
        f"Recruiter reaches other people's payroll: {recruiter_payroll}. "
        f"Segregation of duties — a recruiter must not hire and pay the same person."
    )

    payroll_recruitment = _beyond_self(
        by_code[RoleCode.PAYROLL_EXECUTIVE], recruitment_resources
    )
    assert not payroll_recruitment, (
        f"Payroll Executive reaches recruitment: {payroll_recruitment}."
    )

    # ORGANISATION CONFIGURATION is writable only by the roles that own the
    # structure itself.
    #
    # Several roles need to READ these lists to fill in pickers — a recruiter
    # choosing a department for a job opening, for instance. None of them may
    # write. Asserted here so that widening a read grant can never quietly
    # bring a write along with it: the failure surfaces at import, not as a
    # recruiter renaming a department in production.
    org_config_resources = {
        Resource.DEPARTMENT,
        Resource.DESIGNATION,
        Resource.LOCATION,
        Resource.ROLE,
        Resource.HIRING_WORKFLOW,
    }
    org_config_authors = {RoleCode.ADMIN, RoleCode.HR_HEAD}

    for spec in ROLE_SPECS:
        if spec.code in org_config_authors:
            continue
        writes = sorted(
            f"{resource}.{action}"
            for resource in org_config_resources & set(spec.permissions)
            for action in spec.permissions[resource]
            if action in WRITE_ACTIONS
        )
        assert not writes, (
            f"Role '{spec.code}' may write organisation configuration: {writes}. "
            f"Reading these lists is for pickers; authoring them belongs to "
            f"{sorted(org_config_authors)}."
        )

    # The recruiter specifically must be able to fill in every required field
    # of the job opening form. Each of these is a dropdown on that form, and
    # the CREATE grant below is unusable without all of them.
    recruiter = by_code[RoleCode.RECRUITER]
    for resource in (
        Resource.DEPARTMENT,
        Resource.DESIGNATION,   # also serves the seniority levels endpoint
        Resource.LOCATION,
        Resource.ROLE,
        Resource.HIRING_WORKFLOW,
    ):
        assert recruiter.permissions.get(resource, {}).get(Action.VIEW) == Scope.ALL, (
            f"Recruiter cannot read '{resource}', which the job opening form "
            f"requires — the CREATE grant on JOB_OPENING would be unusable."
        )
    assert {Action.VIEW, Action.CREATE, Action.EDIT} <= set(
        recruiter.permissions.get(Resource.JOB_OPENING, {})
    ), "Recruiter must be able to view, create and edit job openings."

    # BULK CANDIDATE IMPORT is a reviewed capability, pinned to exactly four
    # roles. Ingesting thousands of third-party records under a legal-basis
    # attestation is a different authority from adding one walk-in candidate,
    # and this assert is what stops it drifting outward unnoticed: widening it
    # fails at import rather than in production.
    importers = {
        s.code
        for s in ROLE_SPECS
        if Action.IMPORT in s.permissions.get(Resource.CANDIDATE, {})
    }
    assert importers == {
        RoleCode.ADMIN,
        RoleCode.HR_HEAD,
        RoleCode.HR_MANAGER,
        RoleCode.RECRUITER,
    }, f"Unexpected holders of CANDIDATE/IMPORT: {sorted(importers)}."

    # DOCUMENT VERIFICATION AND REJECTION are separate authorities from
    # collecting documents, and both are pinned. Attesting that somebody's
    # Aadhaar is genuine, and refusing it, are acts an auditor will one day ask
    # about by name; they should never spread to a role by accident because it
    # happened to be given EDIT on a resource.
    for action in (Action.APPROVE, Action.REJECT):
        holders = {
            s.code
            for s in ROLE_SPECS
            if action in s.permissions.get(Resource.EMPLOYEE_DOCUMENT, {})
        }
        assert holders == {
            RoleCode.ADMIN,
            RoleCode.HR_HEAD,
            RoleCode.HR_MANAGER,
        }, f"Unexpected holders of EMPLOYEE_DOCUMENT/{action}: {sorted(holders)}."

    # Uploading a document for SOMEBODY ELSE is expressed as scope, not as a
    # separate action: CREATE at SELF reaches only yourself, at DEPARTMENT your
    # department, at ALL anyone. This pins the floor — no role may hold it
    # above SELF unless it is one that manages people's records — so that a
    # clinical or operational role never quietly gains the ability to write
    # into a colleague's permanent file.
    for spec in ROLE_SPECS:
        create_scope = spec.permissions.get(Resource.EMPLOYEE_DOCUMENT, {}).get(
            Action.CREATE, Scope.NONE
        )
        if create_scope > Scope.SELF:
            assert spec.code in {
                RoleCode.ADMIN,
                RoleCode.HR_HEAD,
                RoleCode.HR_MANAGER,
            }, (
                f"'{spec.code}' may create documents for other employees "
                f"(scope {create_scope}). Only the people-management roles may."
            )

    # IMPORT is a WRITE action, so it must never appear on organisation
    # configuration — that is asserted generically above, and this pins the
    # specific worry: nobody may bulk-load departments or roles.
    for spec in ROLE_SPECS:
        for resource in org_config_resources:
            assert Action.IMPORT not in spec.permissions.get(resource, {}), (
                f"'{spec.code}' may bulk-import '{resource}'."
            )

    # can_manage_users is limited to the three roles the hierarchy allows.
    managers = {s.code for s in ROLE_SPECS if s.can_manage_users}
    assert managers == {RoleCode.ADMIN, RoleCode.HR_HEAD, RoleCode.HR_MANAGER}, (
        f"Unexpected user-managing roles: {sorted(managers)}."
    )

    # Statutory verification (certifying rates against the gazette) belongs to
    # Finance Head alone. Admin explicitly must NOT hold it — an approved
    # design decision, mirroring HR Head's exclusive candidate rejection.
    statutory_verifiers = {
        s.code
        for s in ROLE_SPECS
        if Action.APPROVE in s.permissions.get(Resource.STATUTORY_CONFIG, {})
    }
    assert statutory_verifiers == {RoleCode.FINANCE_HEAD}, (
        f"Only Finance Head may verify statutory rule sets; found "
        f"{sorted(statutory_verifiers)}."
    )


_assert_invariants()


def cell_count() -> int:
    """Total non-deny cells — the size of the matrix under review."""
    return sum(len(actions) for spec in ROLE_SPECS for actions in spec.permissions.values())
