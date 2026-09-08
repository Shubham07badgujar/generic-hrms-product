"""
Where notifications attach to real workflows.

One function per business event, each answering the same two questions: WHO
needs to act, and WHAT do they do next. Kept in one file rather than scattered
through the services so the full set of things this system will interrupt
someone about can be read in a single sitting — that is the only way to keep it
honest about noise.

Every function is called from the service that owns the event, AFTER its
transaction has committed the meaningful work. `notify()` never raises, so a
caller does not need to guard these.
"""

from __future__ import annotations

from .models import NotificationKind as K
from .models import Priority
from .services import notify, notify_many


def _users_holding(resource: str, action: str, *, scope_at_least=None):
    """
    Everyone who may act on a resource — the recipients for "someone must do X".

    Resolved from the permission matrix rather than a hardcoded role list, so
    granting a new role the permission also starts telling it about the work
    instead of leaving a queue silently unwatched.

    `scope_at_least` defaults to any scope at all. Pass a floor for the cases
    where a SELF-scoped holder is not a real candidate to act on the item.
    """
    from apps.accounts.models import User
    from core.access import Scope, can

    minimum = Scope.SELF if scope_at_least is None else scope_at_least
    return [
        user
        for user in User.objects.filter(is_active=True)
        if can(user, resource, action) >= minimum
    ]


def _user_of(employee):
    return getattr(employee, "user", None) if employee else None


def _manager_of(employee):
    return _user_of(getattr(employee, "reporting_manager", None))


# ------------------------------------------------------------- recruitment


def interview_scheduled(interview) -> None:
    """The interviewer has a new commitment; the recruiter needs to know it stuck."""
    candidate = interview.application.candidate
    notify(
        recipient=_user_of(interview.interviewer),
        kind=K.INTERVIEW_SCHEDULED,
        title=f"Interview with {candidate.full_name}",
        body=f"{interview.scheduled_at:%d %b %Y, %H:%M} — round {interview.round_number}.",
        link_url=f"/recruitment/applications/{interview.application_id}",
        entity=interview,
        priority=Priority.HIGH,
        dedupe_key=f"interview:{interview.pk}:scheduled",
    )


def interview_feedback_due(interview) -> None:
    notify(
        recipient=_user_of(interview.interviewer),
        kind=K.INTERVIEW_FEEDBACK_DUE,
        title=f"Feedback due for {interview.application.candidate.full_name}",
        body="The pipeline cannot advance until your feedback is recorded.",
        link_url=f"/recruitment/applications/{interview.application_id}",
        entity=interview,
        priority=Priority.HIGH,
        dedupe_key=f"interview:{interview.pk}:feedback",
    )


def department_decision_recorded_for(*, application, decision: str, rationale: str, actor=None) -> None:
    """
    Routed to whoever can make the decision terminal — HR Head alone.

    This is the handover in the two-level rejection design: the department has
    recommended, and only HR can act on it. Without this the recommendation
    sits in a queue nobody was told about.

    Recipients come from the permission matrix rather than a role name, so
    granting CANDIDATE/REJECT to another role also starts telling it about the
    work rather than silently leaving the queue unwatched.
    """
    from core.access import Action, Resource

    verb = decision.replace("recommend_", "").replace("_", " ")
    notify_many(
        recipients=_users_holding(Resource.CANDIDATE, Action.REJECT),
        kind=K.DEPARTMENT_DECISION_RECORDED,
        title=f"{application.candidate.full_name}: department recommends {verb}",
        body=(rationale or "")[:400],
        link_url="/recruitment/decisions",
        entity=application,
        priority=Priority.HIGH,
        dedupe_key=f"application:{application.pk}:recommended",
    )


def candidate_decided(application, *, selected: bool, actor=None) -> None:
    """Tell the people who own the pipeline that it reached a terminal state."""
    recipients = [
        _user_of(application.job_opening.recruiter),
        _user_of(application.job_opening.hiring_manager),
    ]
    notify_many(
        recipients=[r for r in recipients if r is not None],
        kind=K.CANDIDATE_SELECTED if selected else K.CANDIDATE_REJECTED,
        title=f"{application.candidate.full_name} was "
              f"{'selected' if selected else 'rejected'}",
        body=f"For {application.job_opening.title}.",
        link_url=f"/recruitment/applications/{application.pk}",
        entity=application,
        dedupe_key=f"application:{application.pk}:decided",
    )


# ------------------------------------------------------- employee lifecycle


def onboarding_task_assigned(item) -> None:
    notify(
        recipient=_user_of(item.onboarding.employee),
        kind=K.ONBOARDING_TASK_ASSIGNED,
        title=f"Onboarding: {item.title}",
        body="Complete this to finish your onboarding.",
        link_url="/onboarding",
        entity=item,
        dedupe_key=f"onboarding-item:{item.pk}",
    )


def document_uploaded(document) -> None:
    """
    Somebody's paperwork is waiting to be checked.

    Addressed to whoever may APPROVE documents org-wide — HR Head, HR Manager
    and Admin under the current matrix — rather than to a hardcoded pair of
    role codes. A queue should be watched by the people who can clear it, and
    naming roles here would leave it silently unwatched the day verification
    authority is given to someone under a different title.

    The body is METADATA ONLY: who it concerns, what kind of document, who sent
    it. Not the filename, which people put surprising things in, and certainly
    not the contents. Reading the document means following the link and passing
    the same authorisation as anywhere else.
    """
    from core.access import Action, Resource, Scope

    employee = document.employee
    uploader = document.uploaded_by
    on_behalf = uploader is not None and getattr(employee, "user_id", None) != uploader.pk

    body = f"{employee.full_name} ({employee.employee_code}) · {document.document_type.name}"
    if uploader is not None:
        body += f"\nUploaded by {uploader.email}"
        if on_behalf:
            # Worth stating plainly. Filing paperwork into a colleague's record
            # is routine for HR and is also exactly the thing a reviewer should
            # notice on the occasion when it was not.
            body += " — on this employee's behalf, not their own record"
    body += "\nStatus: awaiting verification"

    notify_many(
        recipients=_users_holding(
            Resource.EMPLOYEE_DOCUMENT, Action.APPROVE, scope_at_least=Scope.ALL
        ),
        kind=K.DOCUMENT_UPLOADED,
        title=f"Document to verify: {employee.full_name}",
        body=body,
        link_url=f"/documents?employee={employee.pk}",
        entity=document,
        # One row per document per recipient, however often the upload is
        # retried or replayed.
        dedupe_key=f"document-uploaded:{document.pk}",
    )


def document_rejected(document) -> None:
    """
    The one employee whose document it is, and nobody else.

    Sent to that employee's own login. Somebody with no login gets nothing,
    which is the correct outcome: there is no one to tell, and substituting a
    recipient would mean showing one employee another's rejection.
    """
    employee = document.employee
    rejected_by = document.rejected_by

    body = f"{document.document_type.name} — rejected\n\nReason: {document.rejection_reason}"
    if rejected_by is not None:
        body += f"\n\nRejected by {rejected_by.email}"
    if document.rejected_at is not None:
        body += f" on {document.rejected_at:%d %b %Y, %H:%M}"
    body += (
        "\n\nUpload a corrected copy from your profile. It is recorded as a new "
        "submission, so this rejection stays on the record."
    )

    notify(
        recipient=_user_of(employee),
        kind=K.DOCUMENT_REJECTED,
        title=f"Document rejected: {document.document_type.name}",
        body=body,
        link_url="/me",
        entity=document,
        # A rejection is final, so one row is the whole story — and a replayed
        # request must not tell someone twice.
        dedupe_key=f"document-rejected:{document.pk}",
    )


def probation_review_due(review) -> None:
    """
    HR and the reporting manager, because the system never auto-confirms.

    CRITICAL: an unreviewed probation quietly lapses into an employee whose
    status nobody decided, which is precisely what the design forbids.
    """
    from core.access import Action, Resource

    employee = review.employee
    recipients = _users_holding(Resource.PROBATION_REVIEW, Action.DECIDE)
    recipients.append(_manager_of(employee))

    notify_many(
        recipients=recipients,
        kind=K.PROBATION_REVIEW_DUE,
        title=f"Probation review due: {employee.full_name}",
        body=f"Probation ends {review.probation_end_date:%d %b %Y}. "
             f"Confirm, extend or terminate — this never resolves on its own.",
        link_url=f"/employees/{employee.pk}",
        entity=review,
        priority=Priority.CRITICAL,
        dedupe_key=f"probation:{review.pk}:due",
    )


def probation_decided(review) -> None:
    notify(
        recipient=_user_of(review.employee),
        kind=K.PROBATION_DECIDED,
        title=f"Your probation was {review.get_decision_display().lower()}",
        link_url="/me",
        entity=review,
        priority=Priority.HIGH,
        dedupe_key=f"probation:{review.pk}:decided",
    )


# -------------------------------------------------------------- offboarding


def resignation_submitted(resignation) -> None:
    from core.access import Action, Resource

    employee = resignation.employee
    recipients = _users_holding(Resource.OFFBOARDING, Action.APPROVE)
    recipients.append(_manager_of(employee))

    notify_many(
        recipients=recipients,
        kind=K.RESIGNATION_SUBMITTED,
        title=f"{employee.full_name} has resigned",
        body=f"Requested last working day "
             f"{resignation.requested_last_working_date:%d %b %Y}.",
        link_url="/offboarding",
        entity=resignation,
        priority=Priority.HIGH,
        dedupe_key=f"resignation:{resignation.pk}",
    )


def clearance_task_assigned(item) -> None:
    """
    The owner of a clearance gate.

    CRITICAL because an exit cannot complete while this is open — the person
    leaving is blocked on someone who may not know they are the blocker.
    """
    notify(
        recipient=_user_of(item.assigned_to),
        kind=K.CLEARANCE_TASK_ASSIGNED,
        title=f"Exit clearance: {item.title}",
        body=f"For {item.exit_workflow.employee.full_name}. "
             f"The exit cannot complete until this is cleared.",
        link_url=f"/offboarding/{item.exit_workflow_id}",
        entity=item,
        priority=Priority.CRITICAL,
        dedupe_key=f"clearance:{item.pk}",
    )


# ------------------------------------------------------------------ payroll


def payroll_processed(run) -> None:
    """Whoever approves payroll now has something waiting."""
    from core.access import Action, Resource

    notify_many(
        recipients=_users_holding(Resource.PAYROLL_RUN, Action.APPROVE),
        kind=K.PAYROLL_PROCESSED,
        title=f"Payroll {run.period_year}-{run.period_month:02d} is ready for review",
        body=f"{run.totals.get('employee_count', 0)} payslips, "
             f"net {run.totals.get('net_pay', '0')}.",
        link_url=f"/payroll/{run.pk}",
        entity=run,
        priority=Priority.HIGH,
        dedupe_key=f"payroll-run:{run.pk}:processed",
    )


def payroll_approved(run) -> None:
    from core.access import Action, Resource

    notify_many(
        recipients=_users_holding(Resource.PAYROLL_RUN, Action.EDIT),
        kind=K.PAYROLL_APPROVED,
        title=f"Payroll {run.period_year}-{run.period_month:02d} approved",
        body="The run is locked. Corrections now require a recorded reversal.",
        link_url=f"/payroll/{run.pk}",
        entity=run,
        dedupe_key=f"payroll-run:{run.pk}:approved",
    )


def payroll_reversed(run, *, reason: str) -> None:
    """
    A released payroll being undone is the loudest thing this system does.

    Everyone who can touch payroll hears about it, at CRITICAL, regardless of
    preference — money already went out on figures now declared wrong.
    """
    from core.access import Action, Resource

    recipients = _users_holding(Resource.PAYROLL_RUN, Action.EDIT)
    recipients += _users_holding(Resource.PAYROLL_RUN, Action.APPROVE)

    notify_many(
        recipients=recipients,
        kind=K.PAYROLL_REVERSED,
        title=f"Payroll {run.period_year}-{run.period_month:02d} was REVERSED",
        body=reason[:400],
        link_url=f"/payroll/{run.pk}",
        entity=run,
        priority=Priority.CRITICAL,
        dedupe_key=f"payroll-run:{run.pk}:reversed",
    )


def payslip_available(payslip) -> None:
    notify(
        recipient=_user_of(payslip.employee),
        kind=K.PAYSLIP_AVAILABLE,
        title=f"Payslip for {payslip.payroll_run.period_year}-"
              f"{payslip.payroll_run.period_month:02d}",
        body=f"Net pay {payslip.net_pay}.",
        link_url=f"/payslips/{payslip.pk}",
        entity=payslip,
        dedupe_key=f"payslip:{payslip.pk}",
    )


def statutory_verification_due(rule_sets) -> None:
    """
    Rates are waiting on Finance, and payroll cannot be approved until they sign.

    CRITICAL: this is the gate that blocks paying anyone, and the people who
    can clear it are not the people who hit it.
    """
    from core.access import Action, Resource

    if not rule_sets:
        return

    notify_many(
        recipients=_users_holding(Resource.STATUTORY_CONFIG, Action.APPROVE),
        kind=K.STATUTORY_VERIFICATION_DUE,
        title=f"{len(rule_sets)} statutory rate set(s) await verification",
        body="No payroll run can be approved until each is checked against its "
             "gazette source.",
        link_url="/payroll",
        priority=Priority.CRITICAL,
        dedupe_key="statutory:verification-due",
    )


# --------------------------------------------------- assets and accounts


def asset_allocated(allocation) -> None:
    notify(
        recipient=_user_of(allocation.employee),
        kind=K.ASSET_ALLOCATED,
        title=f"{allocation.asset.name} was allocated to you",
        body=f"Asset tag {allocation.asset.asset_tag}. You are accountable for its "
             f"return when you leave.",
        link_url="/me",
        entity=allocation,
        dedupe_key=f"allocation:{allocation.pk}",
    )


def email_account_ready(account) -> None:
    notify(
        recipient=_user_of(account.employee),
        kind=K.EMAIL_ACCOUNT_READY,
        title="Your company account is ready",
        body=f"{account.email_address}. Credentials are delivered separately and "
             f"once only — this system never stores them.",
        link_url="/me",
        entity=account,
        dedupe_key=f"email-account:{account.pk}:ready",
    )


# --------------------------------------------------------- administrative


def role_changed(user, *, roles: list[str]) -> None:
    notify(
        recipient=user,
        kind=K.ROLE_CHANGED,
        title="Your access has changed",
        body=f"You now hold: {', '.join(roles) or 'no roles'}.",
        link_url="/me",
        priority=Priority.HIGH,
    )


def admin_override(override, *, subject_label: str) -> None:
    """
    An override reverses someone else's decision.

    The people whose decision was overridden are told, because a decision
    silently reversed is how an audit trail becomes the only place anyone finds
    out — and by then it is an incident, not a conversation.
    """
    from core.access import Action, Resource

    notify_many(
        recipients=_users_holding(Resource.CANDIDATE, Action.REJECT),
        kind=K.ADMIN_OVERRIDE,
        title=f"Administrative override: {subject_label}",
        body=getattr(override, "reason", "")[:400],
        link_url="/audit",
        entity=override,
        priority=Priority.HIGH,
        dedupe_key=f"override:{override.pk}",
    )
