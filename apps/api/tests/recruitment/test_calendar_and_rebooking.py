"""
Google Calendar mirroring and the reject-time → rebook cycle.

The claims:
  1. Scheduling creates the calendar event — attendees, timezone, Meet room —
     and the Meet link lands in the very "interview scheduled" email.
  2. A Google failure never blocks scheduling: it is recorded on the row and
     retryable. Cancel/reschedule keep the event in step.
  3. An interviewer rejecting a TIME cancels the interview and its event,
     kills every old booking token, and automatically re-invites the candidate
     — with the interviewer's proposed windows when given. The application's
     stage and status never move.
  4. Rebooking any number of times keeps the whole history.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.recruitment.models import (
    CandidateNotification,
    CandidateNotificationKind as K,
    Interview,
    InterviewSlotInvite,
    InterviewStatus,
    SlotInviteStatus,
)
from apps.recruitment.services import calendar as gcal
from apps.recruitment.services import slots
from apps.recruitment.services.engine import record_decision
from apps.recruitment.services.interviews import (
    cancel_interview,
    reject_interview_time,
    reschedule_interview,
    schedule_interview,
)
from apps.workflows.models import Decision

pytestmark = pytest.mark.django_db


class FakeTransport:
    """The Google side, scripted. Records every call; fails on demand."""

    def __init__(self, fail_on: set | None = None):
        self.calls: list[tuple] = []
        self.fail_on = fail_on or set()

    def request(self, method, url, *, headers=None, json_body=None, params=None):
        if "oauth2.googleapis.com" in url:
            return {"access_token": "tok", "expires_in": 3600}
        self.calls.append((method, url, json_body, params))
        if method == "POST":
            if "create" in self.fail_on:
                raise RuntimeError("google says no")
            return {"id": "evt-123", "hangoutLink": "https://meet.google.com/fake-abc"}
        if method == "PATCH":
            if "update" in self.fail_on:
                raise RuntimeError("google says no")
            return {"id": url.rsplit("/", 1)[-1]}
        if method == "DELETE":
            if "delete" in self.fail_on:
                raise RuntimeError("google says no")
            return {}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def google(monkeypatch):
    """Calendar 'configured': service_for returns a service on the fake wire."""
    transport = FakeTransport()

    def _service(t=None):
        return gcal.GoogleCalendarService(
            credentials={"kind": "oauth_user", "refresh_token": "r",
                         "client_id": "c", "client_secret": "s"},
            transport=transport,
        )

    monkeypatch.setattr(gcal, "service_for", _service)
    return transport


@pytest.fixture
def at_round_one(therapist_job, make_application, staff, django_capture_on_commit_callbacks):
    """Verified, at round one, with the booking link out (HR configured times)."""
    app = make_application(therapist_job)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        slots.configure_slot_invite(
            application=app,
            actor=staff["hr_head"].user,
            options=slots.default_slot_options(),
        )
    return app


def _select_and_book(app, staff, django_capture_on_commit_callbacks):
    invite = InterviewSlotInvite.objects.get(
        application=app, stage=app.current_stage,
        status__in=[SlotInviteStatus.PENDING, SlotInviteStatus.SELECTED],
    )
    chosen = invite.options[0]
    slots.select_slot(invite=invite, start=chosen["start"], end=chosen["end"])
    with django_capture_on_commit_callbacks(execute=True):
        interview = schedule_interview(
            application=app, stage=app.current_stage,
            interviewer=staff["clinic_doctor"], actor=staff["hr_head"].user,
            scheduled_at=dt.datetime.fromisoformat(chosen["start"]),
            mode="video",
        )
    return interview


# ---------------------------------------------------------------- calendar


def test_scheduling_creates_the_event_and_the_email_carries_the_meet_link(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)

    interview.refresh_from_db()
    assert interview.calendar_event_id == "evt-123"
    assert interview.calendar_sync_status == "synced"
    assert interview.location_or_link == "https://meet.google.com/fake-abc"

    method, url, body, params = google.calls[0]
    assert method == "POST" and url.endswith("/events")
    attendee_emails = {a["email"] for a in body["attendees"]}
    assert at_round_one.candidate.email in attendee_emails
    # Fixture staff carry no work_email; the service falls back to the login.
    assert staff["clinic_doctor"].user.email in attendee_emails
    assert body["start"]["timeZone"] == str(timezone.get_current_timezone())
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert params["sendUpdates"] == "all"

    # The Meet room is IN the confirmation email — not a follow-up.
    mail_row = CandidateNotification.objects.get(
        application=at_round_one, kind=K.INTERVIEW_SCHEDULED
    )
    assert "https://meet.google.com/fake-abc" in mail_row.body_text


def test_a_google_failure_never_blocks_scheduling_and_is_retryable(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    google.fail_on = {"create"}
    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)

    interview.refresh_from_db()
    assert interview.status == InterviewStatus.SCHEDULED  # the booking stood
    assert interview.calendar_sync_status == "failed"
    assert "google says no" in interview.calendar_error
    assert interview.calendar_event_id == ""

    # The retry button: Google is back, the event appears.
    google.fail_on = set()
    gcal.retry_sync(interview)
    interview.refresh_from_db()
    assert interview.calendar_sync_status == "synced"
    assert interview.calendar_event_id == "evt-123"


def test_cancel_and_reschedule_keep_the_event_in_step(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)

    with django_capture_on_commit_callbacks(execute=True):
        reschedule_interview(
            interview=interview, actor=staff["hr_head"].user,
            scheduled_at=interview.scheduled_at + dt.timedelta(days=1),
        )
    assert any(m == "PATCH" for m, *_ in google.calls)

    with django_capture_on_commit_callbacks(execute=True):
        cancel_interview(interview=interview, actor=staff["hr_head"].user, reason="off")
    interview.refresh_from_db()
    assert interview.calendar_sync_status == "cancelled"
    assert any(m == "DELETE" for m, *_ in google.calls)


# ---------------------------------------------------------------- rebooking


def test_rejecting_the_time_rebooks_the_candidate_not_the_application(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    app = at_round_one
    interview = _select_and_book(app, staff, django_capture_on_commit_callbacks)
    old_invite = InterviewSlotInvite.objects.get(application=app)
    stage_before, status_before = app.current_stage_id, app.status

    with django_capture_on_commit_callbacks(execute=True):
        reject_interview_time(
            interview=interview,
            actor=staff["clinic_doctor"].user,  # the interviewer themselves
            reason="Clinical schedule conflict",
        )

    # The interview and its event are gone; the application has not moved.
    interview.refresh_from_db()
    assert interview.status == InterviewStatus.CANCELLED
    assert any(m == "DELETE" for m, *_ in google.calls)
    app.refresh_from_db()
    assert (app.current_stage_id, app.status) == (stage_before, status_before)

    # The old token is dead; a fresh invite with a fresh token is live.
    old_invite.refresh_from_db()
    assert old_invite.status == SlotInviteStatus.CANCELLED
    with pytest.raises(ValidationError):
        slots.select_slot(
            invite=old_invite,
            start=old_invite.options[0]["start"], end=old_invite.options[0]["end"],
        )
    fresh = InterviewSlotInvite.objects.get(
        application=app, status=SlotInviteStatus.PENDING
    )
    assert fresh.token != old_invite.token

    # The candidate got the rescheduling email: reason, old time, new link.
    mail_row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_REBOOKING)
    assert "Clinical schedule conflict" in mail_row.body_text
    assert fresh.token in mail_row.body_text
    assert "needs to be rescheduled" in mail_row.body_text


def test_the_interviewer_may_propose_the_new_windows(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    app = at_round_one
    interview = _select_and_book(app, staff, django_capture_on_commit_callbacks)
    tz = timezone.get_current_timezone()
    tomorrow = timezone.localtime() + dt.timedelta(days=1)
    proposed = [
        {"start": tomorrow.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(),
         "end": tomorrow.replace(hour=16, minute=0, second=0, microsecond=0).isoformat()},
        {"start": tomorrow.replace(hour=16, minute=0, second=0, microsecond=0).isoformat(),
         "end": tomorrow.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()},
    ]

    with django_capture_on_commit_callbacks(execute=True):
        reject_interview_time(
            interview=interview, actor=staff["hr_head"].user,
            reason="Panel unavailable that morning", options=proposed,
        )

    fresh = InterviewSlotInvite.objects.get(application=app, status=SlotInviteStatus.PENDING)
    assert fresh.options == proposed  # only the proposed windows are offered


def test_rejecting_needs_a_real_reason_and_a_live_interview(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)
    with pytest.raises(ValidationError, match="reason"):
        reject_interview_time(interview=interview, actor=staff["hr_head"].user, reason="no")

    cancel_interview(interview=interview, actor=staff["hr_head"].user)
    with pytest.raises(ValidationError, match="cannot be rejected"):
        reject_interview_time(
            interview=interview, actor=staff["hr_head"].user, reason="too late anyway"
        )


def test_garbage_proposed_windows_are_refused(at_round_one, staff, google, django_capture_on_commit_callbacks):
    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)
    for bad in ([], [{"start": "not-a-date", "end": "either"}],
                [{"start": "2020-01-01T10:00:00+05:30", "end": "2020-01-01T11:00:00+05:30"}]):
        with pytest.raises(ValidationError):
            reject_interview_time(
                interview=interview, actor=staff["hr_head"].user,
                reason="times were wrong", options=bad,
            )


def test_three_rebookings_keep_the_whole_history(
    at_round_one, staff, google, django_capture_on_commit_callbacks
):
    app = at_round_one
    for round_trip in range(3):
        interview = _select_and_book(app, staff, django_capture_on_commit_callbacks)
        with django_capture_on_commit_callbacks(execute=True):
            reject_interview_time(
                interview=interview, actor=staff["hr_head"].user,
                reason=f"Conflict number {round_trip + 1}",
            )
        app.refresh_from_db()

    # 1 original + 3 reissues, every row kept; exactly one live token.
    invites = InterviewSlotInvite.objects.filter(application=app)
    assert invites.count() == 4
    assert invites.filter(status=SlotInviteStatus.PENDING).count() == 1
    assert slots.invite_count_for(app, app.current_stage) == 4
    # Three cancelled interviews on the books, none of them a decision.
    assert Interview.objects.filter(
        application=app, status=InterviewStatus.CANCELLED
    ).count() == 3
    assert app.status == "active"
    # Three rebooking emails, each unique.
    assert CandidateNotification.objects.filter(
        application=app, kind=K.INTERVIEW_REBOOKING
    ).count() == 3


def test_rejecting_a_time_needs_interview_edit(at_round_one, staff, google, django_capture_on_commit_callbacks):
    from core.access.engine import AccessDenied

    interview = _select_and_book(at_round_one, staff, django_capture_on_commit_callbacks)
    with pytest.raises(AccessDenied):
        reject_interview_time(
            interview=interview, actor=staff["therapist"].user,
            reason="I am not allowed to do this",
        )
