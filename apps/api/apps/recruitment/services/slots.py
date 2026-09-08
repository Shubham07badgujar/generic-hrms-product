"""
Interview slot selection — HR offers times, the candidate picks, HR confirms.

THE SHAPE
---------
When an application sits at an interview stage, HR configures that round's
available windows (`configure_slot_invite`); ONLY THEN is an
`InterviewSlotInvite` created and the candidate emailed a tokenised link.
Arriving at the stage sends nothing — a round the candidate has not passed
never produces a booking link. The page behind the link offers exactly the
configured windows, minus any already taken by another candidate of the same
job and round; the candidate may choose exactly one, and only from what was
offered — the server validates against the stored options, not the request.
Their choice notifies everyone who can schedule interviews; one of them
confirms it by scheduling through the EXISTING `schedule_interview` —
interviewer choice, role check, double-booking guard and the "interview
scheduled" email (with the meeting link) all belong to that path, not to this
one. Scheduling marks the invite confirmed.

This is deliberately an antechamber to the existing interview system, not a
second one: nothing here creates an Interview, moves a stage, or emails a
schedule. It records a preference and gets the right people told.

TOKENS
------
Same construction as the job application link: unguessable, single-purpose,
resolving to exactly one invite. It grants the holder two things — reading the
round's offered slots and choosing one — and nothing else about the pipeline.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    InterviewSlotInvite,
    SlotInviteStatus,
)

logger = logging.getLogger("hrms.recruitment.slots")

#: The windows a candidate may choose from, local time. Deliberately few and
#: wide: the candidate is telling us WHEN THEY CAN COME, and the exact start
#: within the window is HR's to fix at confirmation.
DEFAULT_WINDOWS = ((10, 12), (14, 16))
#: Business days offered beyond today.
DEFAULT_DAYS = 5
#: An unanswered invite dies quietly after this; a new one can be issued.
INVITE_LIFETIME_DAYS = 7


def _business_days(start: dt.date, count: int) -> list[dt.date]:
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days


def default_slot_options(now=None) -> list[dict]:
    """
    Standard windows over the coming business days, as aware ISO datetimes.

    TODAY is included: any default window that has not yet started still
    counts — a morning invite can offer this afternoon. Windows already begun
    are dropped, so the list never contains a time nobody can book.
    """
    now = now or timezone.localtime()
    tz = now.tzinfo
    days = ([now.date()] if now.weekday() < 5 else []) + _business_days(now.date(), DEFAULT_DAYS)
    options = []
    for day in days:
        for start_hour, end_hour in DEFAULT_WINDOWS:
            start = dt.datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
            if start <= now:
                continue
            end = dt.datetime(day.year, day.month, day.day, end_hour, tzinfo=tz)
            options.append({"start": start.isoformat(), "end": end.isoformat()})
    return options


@transaction.atomic
def configure_slot_invite(
    *, application: Application, actor, options: list[dict]
) -> InterviewSlotInvite:
    """
    HR configures the current interview round's available windows and, only
    then, the candidate is emailed the selection link.

    This is the one door into slot selection: the workflow engine never sends
    a link by itself, so a round the candidate has not reached (or passed
    into) can never leak a booking page. Reconfiguring an open round cancels
    the outstanding invite — its token dies — and issues a fresh link with
    the new times.
    """
    from core.access import Action, Resource, require

    require(actor, Resource.INTERVIEW, Action.CREATE)

    stage = application.current_stage
    if stage.kind != "interview" or not stage.requires_interview:
        raise ValidationError(
            {"stage": "Slot selection applies at an interview round, and the "
                      f"application is currently at '{stage.name}'."}
        )
    if not application.is_open:
        raise ValidationError({"application": "This application is no longer in progress."})
    candidate = application.candidate
    if not candidate.email:
        raise ValidationError(
            {"candidate": "This candidate has no email address to send the link to."}
        )

    cleaned = clean_options(options)

    replaced = InterviewSlotInvite.objects.filter(
        application=application, stage=stage, is_active=True,
        status__in=[SlotInviteStatus.PENDING, SlotInviteStatus.SELECTED],
    ).update(status=SlotInviteStatus.CANCELLED, updated_at=timezone.now())

    invite = InterviewSlotInvite.objects.create(
        application=application,
        stage=stage,
        candidate=candidate,
        options=cleaned,
        expires_at=timezone.now() + dt.timedelta(days=INVITE_LIFETIME_DAYS),
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    _audit(invite, event="slot_invite_configured")
    logger.info(
        "recruitment.slot_invite_configured application=%s stage=%s options=%s replaced=%s",
        application.pk, stage.pk, len(cleaned), replaced,
    )

    from apps.recruitment.services import communications as comms

    comms.notify_candidate(
        application=application,
        kind=comms.Kind.INTERVIEW_SLOT_INVITE,
        dedupe_key=f"slot-invite:{invite.pk}",
        extra_context={
            "interview_round": stage.name,
            "slot_link": invite.selection_url,
            "slot_deadline": timezone.localtime(invite.expires_at).strftime("%d %b %Y"),
        },
        actor=actor,
    )
    return invite


def _window(option: dict) -> tuple[dt.datetime, dt.datetime]:
    return (
        dt.datetime.fromisoformat(option["start"]),
        dt.datetime.fromisoformat(option["end"]),
    )


def taken_windows(application: Application, stage) -> list[tuple[dt.datetime, dt.datetime]]:
    """
    Windows already claimed for this job and round by OTHER candidates: a slot
    another invite holds (selected or confirmed) or a scheduled interview
    occupies. These must never be offered — and never accepted — again.
    """
    from apps.recruitment.models import Interview, InterviewStatus

    taken: list[tuple[dt.datetime, dt.datetime]] = []
    others = InterviewSlotInvite.objects.filter(
        stage=stage, application__job_opening=application.job_opening_id,
        status__in=[SlotInviteStatus.SELECTED, SlotInviteStatus.CONFIRMED],
        is_active=True, selected_slot__isnull=False,
    ).exclude(application=application).values_list("selected_slot", flat=True)
    for slot in others:
        try:
            taken.append(_window(slot))
        except (KeyError, TypeError, ValueError):
            continue
    booked = Interview.objects.filter(
        stage=stage, application__job_opening=application.job_opening_id,
        status__in=[InterviewStatus.SCHEDULED, InterviewStatus.RESCHEDULED],
        is_active=True,
    ).exclude(application=application).values_list("scheduled_at", "scheduled_end")
    taken.extend(booked)
    return taken


def _overlaps(window: tuple[dt.datetime, dt.datetime], taken) -> bool:
    start, end = window
    return any(start < t_end and t_start < end for t_start, t_end in taken)


def clean_options(options) -> list[dict]:
    """
    Validate interviewer-proposed slots: a non-empty list of {start, end}
    aware ISO datetimes, each window in the future and well-formed. Returned
    normalised and sorted — this is what the candidate page will offer.
    """
    if not isinstance(options, list) or not options:
        raise ValidationError({"options": "Propose at least one time window."})
    now = timezone.now()
    cleaned = []
    for row in options:
        try:
            start = dt.datetime.fromisoformat(str(row["start"]))
            end = dt.datetime.fromisoformat(str(row["end"]))
        except (KeyError, TypeError, ValueError):
            raise ValidationError({"options": "Each window needs an ISO start and end."})
        if start.tzinfo is None or end.tzinfo is None:
            raise ValidationError({"options": "Times must carry a timezone."})
        if end <= start:
            raise ValidationError({"options": "A window must end after it starts."})
        if start <= now:
            # Same-day is fine; ALREADY-STARTED is not. Naming the window and
            # the rule stops this reading as "today is not allowed".
            local = timezone.localtime(start)
            raise ValidationError(
                {
                    "options": (
                        f"The {local:%d %b %H:%M} window has already passed. "
                        f"Same-day slots are fine — just pick a time later than now."
                    )
                }
            )
        cleaned.append({"start": start.isoformat(), "end": end.isoformat()})
    cleaned.sort(key=lambda o: o["start"])
    return cleaned


@transaction.atomic
def reissue_slot_invite(
    *,
    application: Application,
    stage,
    actor,
    reason: str = "",
    options: list[dict] | None = None,
    previous_time: str = "",
) -> InterviewSlotInvite | None:
    """
    The rebooking half of "interviewer rejects the time": every open invite
    for this round is cancelled — its token dies with it — and a FRESH invite
    with a fresh token is issued, offering `options` (or the standard
    windows). The candidate is emailed the rescheduling message with the new
    link. The application's status is untouched: rejecting a TIME is not a
    decision about the PERSON.
    """
    candidate = application.candidate
    cancelled = InterviewSlotInvite.objects.filter(
        application=application, stage=stage, is_active=True,
        status__in=[
            SlotInviteStatus.PENDING, SlotInviteStatus.SELECTED, SlotInviteStatus.CONFIRMED,
        ],
    ).update(status=SlotInviteStatus.CANCELLED, updated_at=timezone.now())
    logger.info(
        "recruitment.slot_invite_reissued application=%s stage=%s cancelled=%s",
        application.pk, stage.pk, cancelled,
    )

    if not candidate.email:
        return None

    invite = InterviewSlotInvite.objects.create(
        application=application,
        stage=stage,
        candidate=candidate,
        options=clean_options(options) if options else default_slot_options(),
        expires_at=timezone.now() + dt.timedelta(days=INVITE_LIFETIME_DAYS),
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    _audit(invite, event="slot_invite_reissued")

    from apps.recruitment.services import communications as comms

    comms.notify_candidate(
        application=application,
        kind=comms.Kind.INTERVIEW_REBOOKING,
        dedupe_key=f"slot-rebook:{invite.pk}",
        extra_context={
            "interview_round": stage.name,
            "slot_link": invite.selection_url,
            "slot_deadline": timezone.localtime(invite.expires_at).strftime("%d %b %Y"),
            "previous_time": previous_time,
            "rebook_reason": (reason or "").strip(),
        },
        actor=actor,
    )
    return invite


def invite_count_for(application, stage) -> int:
    """How many invites this round has needed — 1 is normal, more is rebooking."""
    return InterviewSlotInvite.objects.filter(
        application=application, stage=stage, is_active=True
    ).count()


def invite_for_token(token: str) -> InterviewSlotInvite:
    invite = (
        InterviewSlotInvite.objects.select_related(
            "application__job_opening", "stage", "candidate"
        )
        .filter(token=token, is_active=True)
        .first()
    )
    if invite is None:
        from django.http import Http404

        raise Http404("No such invitation.")
    return invite


def public_invite_summary(invite: InterviewSlotInvite) -> dict:
    """
    What the candidate may see: the round, the job, the choices. Nothing else.

    Choices are filtered twice — past windows go, and so does anything another
    candidate of the same job and round has meanwhile taken. The candidate
    only ever sees times that are genuinely available right now.
    """
    now = timezone.now()
    open_for_selection = (
        invite.status == SlotInviteStatus.PENDING and invite.expires_at > now
        and invite.application.is_open
    )
    taken = taken_windows(invite.application, invite.stage)
    options = []
    for option in invite.options:
        try:
            start, end = _window(option)
        except (KeyError, TypeError, ValueError):
            continue
        if start <= now or _overlaps((start, end), taken):
            continue
        options.append(option)
    return {
        "job_title": invite.application.job_opening.title,
        "round_name": invite.stage.name,
        "candidate_name": invite.candidate.full_name,
        "status": invite.status,
        "open": open_for_selection,
        "options": options,
        "selected": invite.selected_slot or None,
    }


@transaction.atomic
def select_slot(*, invite: InterviewSlotInvite, start: str, end: str) -> InterviewSlotInvite:
    """
    Record the candidate's choice. Anonymous — the token is the authority.

    The choice must be one of the OFFERED options, still in the future, on an
    invite that is still open. Choosing twice is refused (the first choice has
    already been announced to HR); HR can still schedule any time they agree
    with the candidate.
    """
    invite = InterviewSlotInvite.objects.select_for_update(of=("self",)).get(pk=invite.pk)
    now = timezone.now()

    if invite.status == SlotInviteStatus.CONFIRMED:
        raise ValidationError({"slot": "This interview is already confirmed."})
    if invite.status == SlotInviteStatus.SELECTED:
        raise ValidationError({"slot": "A slot has already been chosen for this round."})
    if invite.status != SlotInviteStatus.PENDING or invite.expires_at <= now:
        raise ValidationError({"slot": "This invitation is no longer open."})
    if not invite.application.is_open:
        raise ValidationError({"slot": "This application is no longer in progress."})

    chosen = next(
        (o for o in invite.options if o["start"] == start and o["end"] == end), None
    )
    if chosen is None or _window(chosen)[0] <= now:
        raise ValidationError({"slot": "Please choose one of the offered times."})
    # Between the page load and this click, another candidate of the same job
    # and round may have claimed the window. Refuse rather than double-book.
    if _overlaps(_window(chosen), taken_windows(invite.application, invite.stage)):
        raise ValidationError(
            {"slot": "That time has just been taken — please choose another slot."}
        )

    invite.selected_slot = chosen
    invite.selected_at = now
    invite.status = SlotInviteStatus.SELECTED
    invite.save(update_fields=["selected_slot", "selected_at", "status", "updated_at"])

    _notify_hr_of_selection(invite)
    _audit(invite, event="slot_selected")
    return invite


def _notify_hr_of_selection(invite: InterviewSlotInvite) -> None:
    """Tell the people who can actually confirm it — the interview schedulers."""
    from apps.notifications.events import _users_holding
    from apps.notifications.models import NotificationKind, Priority
    from apps.notifications.services import notify_many
    from core.access import Action, Resource, Scope

    start = dt.datetime.fromisoformat(invite.selected_slot["start"])
    end = dt.datetime.fromisoformat(invite.selected_slot["end"])
    notify_many(
        recipients=_users_holding(Resource.INTERVIEW, Action.CREATE, scope_at_least=Scope.ALL),
        kind=NotificationKind.INTERVIEW_SLOT_SELECTED,
        title=f"Interview slot chosen: {invite.candidate.full_name}",
        body=(
            f"{invite.candidate.full_name} · {invite.application.job_opening.title} · "
            f"{invite.stage.name}\nChosen window: "
            f"{timezone.localtime(start):%A, %d %b %Y %H:%M}–{timezone.localtime(end):%H:%M}\n"
            f"Confirm it by scheduling the interview."
        ),
        link_url=f"/recruitment/applications/{invite.application_id}",
        entity=invite,
        priority=Priority.HIGH,
        dedupe_key=f"slot-selected:{invite.pk}",
    )


def confirm_invites_for(application, stage) -> None:
    """
    Called by `schedule_interview` after the interview exists: the open invite
    for this stage, if any, is settled. The scheduled email the candidate gets
    (with the meeting link) IS the final confirmation.
    """
    InterviewSlotInvite.objects.filter(
        application=application, stage=stage, is_active=True,
        status__in=[SlotInviteStatus.PENDING, SlotInviteStatus.SELECTED],
    ).update(status=SlotInviteStatus.CONFIRMED, updated_at=timezone.now())


def _audit(invite: InterviewSlotInvite, *, event: str) -> None:
    from apps.audit.events import record_event
    from core.access import Resource

    record_event(
        invite,
        actor=None,
        entity_type="recruitment.InterviewSlotInvite",
        verb="update",
        resource=Resource.APPLICATION,
        after={
            "event": event,
            "application_id": str(invite.application_id),
            "stage": invite.stage.name,
            "selected_slot": invite.selected_slot,
        },
    )
