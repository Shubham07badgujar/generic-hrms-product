"""
Interview scheduling and feedback.

DOUBLE-BOOKING IS PREVENTED AT THREE LAYERS:

  1. DATABASE — a PostgreSQL exclusion constraint on
     (interviewer, tstzrange(scheduled_at, scheduled_end)) filtered to blocking
     statuses. This is the authoritative guarantee: two concurrent requests
     cannot both succeed, because the second fails at COMMIT no matter what the
     application checked.
  2. SERVICE — an overlap query inside the transaction with row locking, so the
     user gets a readable message naming the clashing interview instead of an
     IntegrityError.
  3. API — a `check-conflict` endpoint the scheduling form calls as the time is
     chosen, so the clash surfaces before submission.

Layer 1 without 2 is correct but unusable; 2 without 1 is a race.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import (
    BLOCKING_INTERVIEW_STATUSES,
    ApplicationEvent,
    Interview,
    InterviewFeedback,
    InterviewStatus,
)
from apps.workflows.models import FeedbackFieldKind
from core.access import Action, Resource, require

from .engine import _event, assert_actor_may_act_at_stage
from . import communications as comms


class SchedulingConflict(ValidationError):
    """The interviewer already has an interview overlapping this time."""


#: Employment statuses that end eligibility to interview. Someone on notice
#: still works here and may well take the round; someone exited or terminated
#: cannot, and booking them would produce an interview nobody can attend.
FORMER_EMPLOYEE_STATUSES = ("exited", "terminated")


def eligible_interviewers(stage):
    """
    Everyone who may take `stage`'s round: current employees holding the
    role the stage names, with a live login.

    The single source of truth for "who can interview here", shared by the
    scheduling form, the guard below, and the pipeline's diagnostics. Built
    from the stage's own configuration, so it is right for every workflow
    without anything job-specific: change the stage's role, and the pool
    changes with it.

    A stage with no responsible role (an automatic step) has no pool.
    """
    from apps.employees.models import Employee

    if not stage.responsible_role_id:
        return Employee.objects.none()

    return (
        Employee.objects.filter(
            is_active=True,
            user__isnull=False,
            user__is_active=True,
            user__user_roles__is_active=True,
            user__user_roles__role__is_active=True,
            user__user_roles__role_id=stage.responsible_role_id,
        )
        .exclude(status__in=FORMER_EMPLOYEE_STATUSES)
        .select_related("department", "designation")
        .distinct()
        .order_by("first_name", "last_name")
    )


def interviewer_pool_problem(stage) -> str:
    """
    Why `stage` cannot be scheduled, in words HR can act on — or "" when it
    can. Distinguishes "nobody was ever given this role" from "the only
    people who held it have left", because the fix differs.
    """
    from apps.employees.models import Employee

    if not stage.responsible_role_id or eligible_interviewers(stage).exists():
        return ""

    role = stage.responsible_role
    former = Employee.objects.filter(
        is_active=True,
        user__user_roles__is_active=True,
        user__user_roles__role_id=stage.responsible_role_id,
        status__in=FORMER_EMPLOYEE_STATUSES,
    ).distinct()
    if former.exists():
        names = ", ".join(e.full_name for e in former[:3])
        return (
            f"'{stage.name}' needs the {role.name} role, and everyone who holds "
            f"it has left the organisation ({names}). Give the role to a current "
            f"employee, or point this round at a different role."
        )
    return (
        f"Nobody holds the {role.name} role that '{stage.name}' requires. "
        f"Assign the role to an employee, or point this round at a different role."
    )


def find_candidate_conflict(*, application, start, end, exclude_pk=None):
    """
    Any live interview overlapping [start, end) for the SAME CANDIDATE.

    The interviewer's diary was guarded from the beginning; the candidate's
    was not, so one person could be booked into two rounds at once — with
    two different interviewers each expecting to meet them.
    """
    qs = Interview.objects.filter(
        application__candidate_id=application.candidate_id,
        status__in=BLOCKING_INTERVIEW_STATUSES,
        is_active=True,
        scheduled_at__lt=end,
    ).select_related("stage", "interviewer", "application__job_opening")
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for row in qs:
        if row.scheduled_end > start:
            return row
    return None


def find_open_round_interview(*, application, stage, exclude_pk=None):
    """The live interview this application already has for this round, if any."""
    qs = Interview.objects.filter(
        application=application,
        stage=stage,
        status__in=BLOCKING_INTERVIEW_STATUSES,
        is_active=True,
    ).select_related("interviewer")
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def find_conflict(*, interviewer, start, end, exclude_pk=None):
    """
    Any blocking interview overlapping [start, end) for this interviewer.

    Half-open comparison: an interview ending exactly when another begins is
    not a clash, which is what makes back-to-back scheduling possible.
    """
    qs = Interview.objects.filter(
        interviewer=interviewer,
        status__in=BLOCKING_INTERVIEW_STATUSES,
        scheduled_at__lt=end,
        scheduled_end__gt=start,
        is_active=True,
    ).select_related("application__candidate")
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


@transaction.atomic
def schedule_interview(
    *,
    application,
    stage,
    interviewer,
    actor,
    scheduled_at,
    duration_minutes: int = 45,
    mode: str = "in_person",
    location_or_link: str = "",
) -> Interview:
    """Schedule an interview, refusing any overlap for the interviewer."""
    require(actor, Resource.INTERVIEW, Action.CREATE)

    if not application.is_open:
        raise ValidationError(
            {"application": "This application is closed and cannot be scheduled."}
        )

    if not stage.requires_interview:
        raise ValidationError(
            {"stage": f"'{stage.name}' is not an interview stage."}
        )

    # Somebody who has left cannot take a round. The picker already hides
    # them; without this the API accepted what the UI refused to offer, and
    # the booking produced an interview with nobody to attend it.
    if interviewer.status in FORMER_EMPLOYEE_STATUSES:
        raise ValidationError(
            {
                "interviewer": (
                    f"{interviewer.full_name} has left the organisation "
                    f"({interviewer.get_status_display().lower()}) and cannot be "
                    f"booked for an interview."
                )
            }
        )

    # The interviewer must hold the role the stage names — otherwise a
    # Clinic Doctor could be booked into the Senior Doctor round. When the
    # round has no eligible interviewer at all, say so instead: the caller
    # picked the only person they could see, and the real problem is the
    # unstaffed role, not their choice.
    if stage.responsible_role_id and interviewer.user_id:
        interviewer_roles = set(
            interviewer.user.user_roles.filter(is_active=True, role__is_active=True)
            .values_list("role__code", flat=True)
        )
        if stage.responsible_role.code not in interviewer_roles:
            raise ValidationError(
                {
                    "interviewer": interviewer_pool_problem(stage) or (
                        f"{interviewer.full_name} does not hold the "
                        f"{stage.responsible_role.name} role required by "
                        f"'{stage.name}'."
                    )
                }
            )

    end = scheduled_at + timezone.timedelta(minutes=duration_minutes)

    # Lock this interviewer's blocking rows so a concurrent booking cannot slip
    # between the check and the insert.
    Interview.objects.select_for_update().filter(
        interviewer=interviewer, status__in=BLOCKING_INTERVIEW_STATUSES
    ).exists()

    clash = find_conflict(interviewer=interviewer, start=scheduled_at, end=end)
    if clash is not None:
        raise SchedulingConflict(
            {
                "scheduled_at": (
                    f"{interviewer.full_name} already has an interview from "
                    f"{clash.scheduled_at:%d %b %H:%M} to {clash.scheduled_end:%H:%M} "
                    f"with {clash.application.candidate.full_name}."
                )
            }
        )

    # One live interview per round. Booking a second one left two open
    # interviews for the same step: two invitations to the candidate, two
    # interviewers each believing they were meeting them, and no answer to
    # which one's feedback the round is waiting on. Moving the existing
    # booking is what "another time" means — reschedule it, or reject the
    # time so the candidate is offered a fresh choice.
    existing = find_open_round_interview(application=application, stage=stage)
    if existing is not None:
        raise SchedulingConflict(
            {
                "stage": (
                    f"{application.candidate.full_name} already has a "
                    f"{stage.name} interview with {existing.interviewer.full_name} "
                    f"on {existing.scheduled_at:%d %b %Y at %H:%M}. Reschedule "
                    f"that one, or reject its time, rather than booking a second."
                )
            }
        )

    # And the candidate's own diary — the mirror of the interviewer's. A
    # person cannot attend two rounds at once, whoever is conducting them.
    candidate_clash = find_candidate_conflict(
        application=application, start=scheduled_at, end=end
    )
    if candidate_clash is not None:
        raise SchedulingConflict(
            {
                "scheduled_at": (
                    f"{application.candidate.full_name} is already in a "
                    f"{candidate_clash.stage.name} interview with "
                    f"{candidate_clash.interviewer.full_name} from "
                    f"{candidate_clash.scheduled_at:%d %b %H:%M} to "
                    f"{candidate_clash.scheduled_end:%H:%M}."
                )
            }
        )

    interview = Interview.objects.create(
        application=application,
        stage=stage,
        interviewer=interviewer,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        mode=mode,
        location_or_link=location_or_link,
    )

    # BEFORE the scheduled email renders, so the Meet room the event creates
    # becomes the meeting link in that very email. Never raises — a Google
    # outage is recorded on the row and retried from the interview panel.
    from . import calendar as gcal

    gcal.sync_created(interview)

    _event(
        application,
        kind=ApplicationEvent.Kind.INTERVIEW_SCHEDULED,
        actor=actor,
        to_stage=stage,
        note=f"{interviewer.full_name} · {scheduled_at:%d %b %Y %H:%M}",
        detail={"interview_id": str(interview.pk)},
    )
    comms.notify_candidate(
        application=application,
        kind=comms.Kind.INTERVIEW_SCHEDULED,
        dedupe_key=f"interview:{interview.pk}:scheduled",
        extra_context=comms.interview_context(interview),
        actor=actor,
    )
    # The scheduled email above IS the final confirmation of any slot the
    # candidate picked for this round; the invite is settled.
    from apps.recruitment.services.slots import confirm_invites_for

    confirm_invites_for(application, stage)
    return interview


@transaction.atomic
def reschedule_interview(*, interview, actor, scheduled_at, duration_minutes=None) -> Interview:
    """Move an interview, re-running the conflict check against the new time."""
    require(actor, Resource.INTERVIEW, Action.EDIT)

    duration = duration_minutes or interview.duration_minutes
    end = scheduled_at + timezone.timedelta(minutes=duration)

    clash = find_conflict(
        interviewer=interview.interviewer, start=scheduled_at, end=end, exclude_pk=interview.pk
    )
    if clash is not None:
        raise SchedulingConflict(
            {
                "scheduled_at": (
                    f"{interview.interviewer.full_name} already has an interview from "
                    f"{clash.scheduled_at:%d %b %H:%M} to {clash.scheduled_end:%H:%M}."
                )
            }
        )

    # The candidate's diary too — moving a round onto another of their own
    # rounds is the same collision from the other side.
    candidate_clash = find_candidate_conflict(
        application=interview.application, start=scheduled_at, end=end,
        exclude_pk=interview.pk,
    )
    if candidate_clash is not None:
        raise SchedulingConflict(
            {
                "scheduled_at": (
                    f"{interview.application.candidate.full_name} is already in a "
                    f"{candidate_clash.stage.name} interview with "
                    f"{candidate_clash.interviewer.full_name} from "
                    f"{candidate_clash.scheduled_at:%d %b %H:%M} to "
                    f"{candidate_clash.scheduled_end:%H:%M}."
                )
            }
        )

    interview.scheduled_at = scheduled_at
    interview.duration_minutes = duration
    interview.status = InterviewStatus.RESCHEDULED
    interview.save()

    # The calendar event moves with the interview; attendees get the update.
    from . import calendar as gcal

    gcal.sync_updated(interview)

    _event(
        interview.application,
        kind=ApplicationEvent.Kind.INTERVIEW_SCHEDULED,
        actor=actor,
        to_stage=interview.stage,
        note=f"Rescheduled to {scheduled_at:%d %b %Y %H:%M}",
    )
    # Keyed on the NEW time: moving it twice is two events, and the candidate
    # should hear about both; replaying the same move is one.
    comms.notify_candidate(
        application=interview.application,
        kind=comms.Kind.INTERVIEW_RESCHEDULED,
        dedupe_key=f"interview:{interview.pk}:rescheduled:{scheduled_at.isoformat()}",
        extra_context=comms.interview_context(interview),
        actor=actor,
    )
    return interview


@transaction.atomic
def cancel_interview(*, interview, actor, reason: str = "") -> Interview:
    """
    Cancel a booked interview, freeing the interviewer's slot.

    Only a scheduled or rescheduled interview can be cancelled — a completed
    one has already happened, and a cancelled one already is. The application
    stays open at its current stage; cancelling a meeting is not a decision
    about the candidate, and the engine is the only thing that makes those.
    """
    require(actor, Resource.INTERVIEW, Action.EDIT)

    if interview.status not in BLOCKING_INTERVIEW_STATUSES:
        raise ValidationError(
            {"interview": f"A {interview.get_status_display().lower()} interview cannot be cancelled."}
        )

    interview.status = InterviewStatus.CANCELLED
    interview.save(update_fields=["status", "updated_at"])

    # The mirrored event goes too; Google tells the attendees.
    from . import calendar as gcal

    gcal.sync_cancelled(interview)

    _event(
        interview.application,
        kind=ApplicationEvent.Kind.INTERVIEW_SCHEDULED,
        actor=actor,
        to_stage=interview.stage,
        note=f"Cancelled: {reason.strip()}" if reason.strip() else "Cancelled",
        detail={"interview_id": str(interview.pk), "cancelled": True},
    )
    comms.notify_candidate(
        application=interview.application,
        kind=comms.Kind.INTERVIEW_CANCELLED,
        dedupe_key=f"interview:{interview.pk}:cancelled",
        extra_context=comms.interview_context(interview),
        actor=actor,
    )
    return interview


@transaction.atomic
def reject_interview_time(
    *,
    interview: Interview,
    actor,
    reason: str,
    options: list[dict] | None = None,
) -> Interview:
    """
    The interviewer (or HR) rejects a BOOKED TIME — not the candidate.

    The interview is cancelled, its calendar event withdrawn, every open slot
    invite for the round dies with its token, and the candidate automatically
    receives a fresh booking link — offering `options` when the interviewer
    proposed specific windows, the standard windows otherwise. The
    application's stage and status are untouched throughout; ending a
    candidacy belongs to the engine, never to scheduling.

    Authorisation mirrors cancel/reschedule (INTERVIEW/EDIT): HR anywhere,
    an interviewer on their own interviews — the API scopes which rows an
    interviewer can reach.
    """
    require(actor, Resource.INTERVIEW, Action.EDIT)

    if interview.status not in BLOCKING_INTERVIEW_STATUSES:
        raise ValidationError(
            {"interview": f"A {interview.get_status_display().lower()} interview's time cannot be rejected."}
        )
    if not (reason or "").strip() or len(reason.strip()) < 5:
        raise ValidationError(
            {"reason": "Give the reason the time does not work — it is recorded and shown in history."}
        )
    # Proposed windows are validated BEFORE anything is cancelled, so a typo
    # in the times never leaves the round half-torn-down.
    from apps.recruitment.services.slots import clean_options

    cleaned_options = clean_options(options) if options is not None else None

    previous_time = (
        f"{timezone.localtime(interview.scheduled_at):%A, %d %B %Y %H:%M} "
        f"({timezone.localtime(interview.scheduled_at):%Z})"
    )

    interview.status = InterviewStatus.CANCELLED
    interview.save(update_fields=["status", "updated_at"])

    from . import calendar as gcal

    gcal.sync_cancelled(interview)

    actor_label = getattr(getattr(actor, "employee", None), "full_name", "") or getattr(
        actor, "email", ""
    )
    _event(
        interview.application,
        kind=ApplicationEvent.Kind.INTERVIEW_SCHEDULED,
        actor=actor,
        to_stage=interview.stage,
        note=f"Time rejected by {actor_label}: {reason.strip()}",
        detail={
            "interview_id": str(interview.pk),
            "time_rejected": True,
            "rejected_by": actor_label,
            "reason": reason.strip(),
            "previous_time": previous_time,
        },
    )

    # The fresh link, the rescheduling email, and the death of every old token.
    from apps.recruitment.services.slots import reissue_slot_invite

    reissue_slot_invite(
        application=interview.application,
        stage=interview.stage,
        actor=actor,
        reason=reason,
        options=cleaned_options,
        previous_time=previous_time,
    )
    return interview


@transaction.atomic
def submit_feedback(
    *,
    interview: Interview,
    actor,
    answers: dict,
    recommendation: str,
    strengths: str = "",
    concerns: str = "",
    overall_rating: int | None = None,
) -> InterviewFeedback:
    """
    Record structured feedback.

    Only the ASSIGNED interviewer may submit — holding the right role is not
    enough, or any Clinic Doctor could write feedback on any other's interview.
    """
    require(actor, Resource.INTERVIEW_FEEDBACK, Action.CREATE)

    application = interview.application
    assert_actor_may_act_at_stage(actor=actor, application=application, stage=interview.stage)

    actor_employee = getattr(actor, "employee", None)
    if actor_employee is None or actor_employee.pk != interview.interviewer_id:
        raise ValidationError(
            {
                "interview": (
                    f"Only the assigned interviewer ({interview.interviewer.full_name}) "
                    f"may submit feedback for this interview."
                )
            }
        )

    if hasattr(interview, "feedback"):
        raise ValidationError(
            {"interview": "Feedback has already been submitted for this interview."}
        )

    # A stage may have no assessment form — a recruiter's screening call, an
    # HR culture round in a custom workflow. The verdict is still recorded:
    # recommendation, rating, strengths, concerns. Only structured answers
    # need a form to be validated against, so with no form none are accepted.
    form = interview.stage.feedback_form
    if form is None:
        if answers:
            raise ValidationError(
                {"answers": "This stage has no assessment form, so it takes no structured answers."}
            )
        validated = {}
    else:
        validated = _validate_answers(form, answers)

    feedback = InterviewFeedback.objects.create(
        interview=interview,
        form=form,
        submitted_by=actor_employee,
        answers=validated,
        strengths=strengths,
        concerns=concerns,
        recommendation=recommendation,
        overall_rating=overall_rating,
    )

    interview.status = InterviewStatus.COMPLETED
    interview.save(update_fields=["status", "updated_at"])

    _event(
        application,
        kind=ApplicationEvent.Kind.FEEDBACK_SUBMITTED,
        actor=actor,
        to_stage=interview.stage,
        note=recommendation,
        detail={"rating": overall_rating, "interview_id": str(interview.pk)},
    )
    return feedback


def _validate_answers(form, answers: dict) -> dict:
    """Every required field answered, every value the right shape."""
    errors: dict[str, str] = {}
    cleaned: dict = {}

    for field in form.fields.filter(is_active=True).order_by("order"):
        value = answers.get(field.key)

        if value in (None, ""):
            if field.is_required:
                errors[field.key] = f"'{field.label}' is required."
            continue

        if field.kind == FeedbackFieldKind.RATING_1_5:
            try:
                rating = int(value)
            except (TypeError, ValueError):
                errors[field.key] = f"'{field.label}' must be a number from 1 to 5."
                continue
            if not 1 <= rating <= 5:
                errors[field.key] = f"'{field.label}' must be between 1 and 5."
                continue
            cleaned[field.key] = rating
        elif field.kind == FeedbackFieldKind.BOOLEAN:
            cleaned[field.key] = bool(value)
        elif field.kind == FeedbackFieldKind.CHOICE:
            if field.choices and value not in field.choices:
                errors[field.key] = f"'{value}' is not a valid choice for '{field.label}'."
                continue
            cleaned[field.key] = value
        else:
            cleaned[field.key] = str(value)

    if errors:
        raise ValidationError(errors)
    return cleaned
