"""
Probation.

THE SYSTEM NEVER CONFIRMS AN EMPLOYEE.

That is the rule this module exists to hold, and it is worth being explicit
about what it costs: a scheduled job could quietly flip everyone whose
probation end date has passed, and nobody would notice until someone who should
have been let go had accrued confirmed-employee rights. So the sweep does
exactly one thing — it moves ACTIVE probations to DUE and records that a
reminder was sent. Confirmation, extension and termination are all explicit HR
acts, each with an actor, a timestamp and (for the two that hurt) a reason.

The confirmation LETTER is generated only after the decision, never alongside
the reminder.
"""

from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import (
    Employee,
    EmployeeStatus,
    ProbationDecision,
    ProbationReview,
    ProbationStatus,
)
from apps.notifications import events
from core.access import Action, Resource, require

#: Reminder points, as days before the probation ends.
REMINDER_DAYS = (30, 7)

MIN_RATIONALE_LENGTH = 20


class ProbationError(ValidationError):
    """A refused probation operation."""


# ---------------------------------------------------------------------------
# Setup and the reminder sweep
# ---------------------------------------------------------------------------


@transaction.atomic
def start_probation(*, employee: Employee, months: int = 6, actor=None) -> Employee:
    """Put a new joiner on probation. Called from employee creation."""
    if employee.probation_status not in {
        ProbationStatus.NOT_APPLICABLE,
        ProbationStatus.ACTIVE,
    }:
        return employee

    start = employee.date_of_joining
    employee.probation_start_date = start
    employee.probation_end_date = start + dt.timedelta(days=int(months * 30.44))
    employee.probation_status = ProbationStatus.ACTIVE
    if employee.status in {EmployeeStatus.ONBOARDING, EmployeeStatus.ACTIVE}:
        employee.status = EmployeeStatus.ON_PROBATION
    employee.save(
        update_fields=[
            "probation_start_date",
            "probation_end_date",
            "probation_status",
            "status",
            "updated_at",
        ]
    )
    return employee


def due_for_reminder(as_of=None) -> dict[str, list[Employee]]:
    """
    Who needs chasing, grouped by reminder point.

    A pure read. Nothing here writes, so a dashboard can call it freely and the
    sweep below is the only thing that records having notified anyone.
    """
    today = as_of or timezone.localdate()
    on_probation = Employee.objects.filter(
        probation_status__in=[ProbationStatus.ACTIVE, ProbationStatus.DUE],
        is_active=True,
    ).select_related("department", "reporting_manager")

    buckets: dict[str, list[Employee]] = {"t_minus_30": [], "t_minus_7": [], "overdue": []}
    for employee in on_probation:
        if not employee.probation_end_date:
            continue
        days = (employee.probation_end_date - today).days
        if days < 0:
            buckets["overdue"].append(employee)
        elif days <= 7:
            buckets["t_minus_7"].append(employee)
        elif days <= 30:
            buckets["t_minus_30"].append(employee)
    return buckets


@transaction.atomic
def run_reminder_sweep(as_of=None) -> dict[str, int]:
    """
    The nightly job.

    Creates a PENDING review at T-30 so HR has somewhere to record their
    assessment, marks probations DUE once the date passes, and stamps the
    reminder flags. It does not decide anything.
    """
    today = as_of or timezone.localdate()
    counts = {"reviews_opened": 0, "marked_due": 0, "reminded_30d": 0, "reminded_7d": 0}

    buckets = due_for_reminder(today)

    for bucket, employees in buckets.items():
        for employee in employees:
            review, created = ProbationReview.objects.get_or_create(
                employee=employee,
                probation_end_date=employee.probation_end_date,
                decision=ProbationDecision.PENDING,
                defaults={"reviewer": employee.reporting_manager},
            )
            if created:
                counts["reviews_opened"] += 1

            # The reminder flags are what make the sweep idempotent; the
            # notification rides on the same edge so a nightly re-run does not
            # re-notify. `probation_review_due` is also deduped by review, so
            # the two guards agree even if the sweep is run twice in a day.
            if bucket == "t_minus_30" and not review.notified_30d:
                review.notified_30d = True
                review.save(update_fields=["notified_30d", "updated_at"])
                events.probation_review_due(review)
                counts["reminded_30d"] += 1
            elif bucket == "t_minus_7" and not review.notified_7d:
                review.notified_7d = True
                review.save(update_fields=["notified_7d", "updated_at"])
                events.probation_review_due(review)
                counts["reminded_7d"] += 1
            elif bucket == "overdue" and review.notified_overdue_on != today:
                review.notified_overdue_on = today
                review.save(update_fields=["notified_overdue_on", "updated_at"])
                events.probation_review_due(review)

            # DUE is a prompt, not a decision. It says "HR must look at this",
            # and stops there.
            if bucket == "overdue" and employee.probation_status == ProbationStatus.ACTIVE:
                employee.probation_status = ProbationStatus.DUE
                employee.save(update_fields=["probation_status", "updated_at"])
                counts["marked_due"] += 1

    return counts


# ---------------------------------------------------------------------------
# The reviewer's assessment
# ---------------------------------------------------------------------------


@transaction.atomic
def record_assessment(
    *,
    review: ProbationReview,
    actor,
    recommendation: str,
    performance_rating=None,
    reliability_rating=None,
    role_specific_rating=None,
    strengths: str = "",
    areas_for_improvement: str = "",
    notes: str = "",
) -> ProbationReview:
    """
    The manager's assessment and RECOMMENDATION.

    Advisory, exactly like a department head's recommendation in recruitment.
    It never changes the employee's status; HR decides.
    """
    require(actor, Resource.PROBATION_REVIEW, Action.EDIT)

    if review.is_decided:
        raise ProbationError(
            {"review": "This review has already been decided and cannot be reassessed."}
        )
    if recommendation not in ProbationDecision.values or recommendation == ProbationDecision.PENDING:
        raise ProbationError({"recommendation": f"Invalid recommendation '{recommendation}'."})

    actor_employee = getattr(actor, "employee", None)

    review.recommendation = recommendation
    review.performance_rating = performance_rating
    review.reliability_rating = reliability_rating
    review.role_specific_rating = role_specific_rating
    review.strengths = strengths
    review.areas_for_improvement = areas_for_improvement
    review.reviewer_notes = notes
    review.reviewer = actor_employee or review.reviewer
    review.reviewed_at = timezone.now()
    review.save()
    return review


# ---------------------------------------------------------------------------
# HR's decision
# ---------------------------------------------------------------------------


@transaction.atomic
def decide(
    *,
    review: ProbationReview,
    actor,
    decision: str,
    rationale: str = "",
    extended_to=None,
    generate_letter: bool = True,
) -> ProbationReview:
    """
    HR's employment decision. The only path to a confirmed employee.

    CONFIRM generates the confirmation letter — after the decision is recorded,
    never before, and only because the decision exists.
    """
    require(actor, Resource.PROBATION_REVIEW, Action.DECIDE)

    if review.is_decided:
        raise ProbationError(
            {
                "review": (
                    f"This probation was already decided ({review.decision}) on "
                    f"{review.decided_at:%d %b %Y}. Record a new review instead of "
                    f"rewriting the old one."
                )
            }
        )

    if decision not in {
        ProbationDecision.CONFIRM,
        ProbationDecision.EXTEND,
        ProbationDecision.TERMINATE,
    }:
        raise ProbationError({"decision": f"'{decision}' is not a probation decision."})

    # Confirmation needs no defence. Extending or ending someone's employment
    # does, and it is recorded permanently.
    if decision in {ProbationDecision.EXTEND, ProbationDecision.TERMINATE}:
        if len(rationale.strip()) < MIN_RATIONALE_LENGTH:
            raise ProbationError(
                {
                    "rationale": (
                        f"A decision to {decision} requires a written rationale of at least "
                        f"{MIN_RATIONALE_LENGTH} characters, recorded permanently."
                    )
                }
            )

    if decision == ProbationDecision.EXTEND:
        if not extended_to:
            raise ProbationError({"extended_to": "An extension must name the new end date."})
        if extended_to <= review.probation_end_date:
            raise ProbationError(
                {"extended_to": "The new end date must be later than the current one."}
            )

    employee = Employee.objects.select_for_update().get(pk=review.employee_id)
    previous = {
        "probation_status": employee.probation_status,
        "status": employee.status,
    }

    review.decision = decision
    review.decided_by = actor
    review.decided_at = timezone.now()
    review.rationale = rationale.strip()
    review.extended_to = extended_to if decision == ProbationDecision.EXTEND else None

    if decision == ProbationDecision.CONFIRM:
        employee.probation_status = ProbationStatus.CONFIRMED
        employee.confirmation_date = timezone.localdate()
        employee.status = EmployeeStatus.CONFIRMED
        employee.save(
            update_fields=["probation_status", "confirmation_date", "status", "updated_at"]
        )
    elif decision == ProbationDecision.EXTEND:
        employee.probation_status = ProbationStatus.ACTIVE
        employee.probation_end_date = extended_to
        employee.status = EmployeeStatus.ON_PROBATION
        employee.save(
            update_fields=["probation_status", "probation_end_date", "status", "updated_at"]
        )
    else:  # TERMINATE
        employee.probation_status = ProbationStatus.TERMINATED
        employee.status = EmployeeStatus.TERMINATED
        employee.save(update_fields=["probation_status", "status", "updated_at"])

    review.save()

    if decision == ProbationDecision.CONFIRM and generate_letter:
        review.confirmation_letter = _issue_confirmation_letter(employee=employee, actor=actor)
        review.save(update_fields=["confirmation_letter", "updated_at"])

    _audit_decision(review=review, actor=actor, previous=previous, employee=employee)
    return review


def _issue_confirmation_letter(*, employee: Employee, actor):
    """
    Generate the confirmation letter.

    A missing template must not undo a confirmation that HR has already made,
    so this is best-effort: the decision stands and the letter can be issued
    once a template exists.
    """
    from apps.onboarding.letters import LetterError, generate_letter
    from apps.onboarding.models import LetterType

    try:
        return generate_letter(
            employee=employee, actor=actor, letter_type=LetterType.CONFIRMATION
        )
    except LetterError:
        return None


def _audit_decision(*, review, actor, previous, employee) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.APPROVE
        if review.decision == ProbationDecision.CONFIRM
        else AuditAction.UPDATE,
        resource=Resource.PROBATION_REVIEW,
        entity_type="employees.ProbationReview",
        entity_id=str(review.pk),
        entity_label=str(review),
        before=previous,
        after={
            "event": "probation_decision",
            "decision": review.decision,
            "recommendation": review.recommendation,
            "rationale": review.rationale,
            "extended_to": review.extended_to.isoformat() if review.extended_to else None,
            "employee": employee.employee_code,
            "probation_status": employee.probation_status,
            "status": employee.status,
        },
        request_id=get_request_id(),
    )
