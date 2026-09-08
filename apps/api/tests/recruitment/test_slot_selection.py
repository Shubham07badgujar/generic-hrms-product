"""
Interview slot selection: HR offers the times, the candidate picks one.

The claims:
  1. Arriving at an interview round sends the candidate NOTHING. The booking
     link goes out only when someone with scheduling authority configures the
     round's available windows — first round and every later round alike.
  2. The candidate may choose exactly one slot, only from what was offered,
     only while the invite is open — and never a window another candidate of
     the same job and round has already taken. The choice notifies the
     schedulers.
  3. Confirmation IS the existing `schedule_interview`: it settles the invite
     and the existing "interview scheduled" email (with the meeting link) is
     the final confirmation. No second interview system.
  4. The token grants the round's options and one choice — nothing else.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.notifications.models import Notification, NotificationKind
from apps.recruitment.models import (
    CandidateNotification,
    CandidateNotificationKind as K,
    InterviewSlotInvite,
    SlotInviteStatus,
)
from apps.recruitment.services import slots
from apps.recruitment.services.engine import record_decision
from apps.recruitment.services.interviews import schedule_interview
from apps.workflows.models import Decision
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db


def _windows(count=4, *, start_days=2):
    """`count` one-hour windows on consecutive days, aware ISO strings."""
    base = timezone.localtime().replace(minute=0, second=0, microsecond=0)
    rows = []
    for index in range(count):
        start = base + dt.timedelta(days=start_days + index, hours=1)
        rows.append(
            {"start": start.isoformat(), "end": (start + dt.timedelta(hours=1)).isoformat()}
        )
    return rows


@pytest.fixture
def at_round_one(therapist_job, make_application, staff, django_capture_on_commit_callbacks):
    """An application verified by the recruiter, standing at the first round."""
    app = make_application(therapist_job)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    return app


@pytest.fixture
def invited(at_round_one, staff, django_capture_on_commit_callbacks):
    """Round one with the booking link sent: HR configured four windows."""
    with django_capture_on_commit_callbacks(execute=True):
        slots.configure_slot_invite(
            application=at_round_one, actor=staff["hr_head"].user, options=_windows()
        )
    return at_round_one


def test_arriving_at_the_round_sends_nothing_until_hr_configures_times(
    at_round_one, staff, django_capture_on_commit_callbacks
):
    app = at_round_one
    # The stage moved, but no invite and no email exist yet.
    assert app.current_stage.kind == "interview"
    assert not InterviewSlotInvite.objects.filter(application=app).exists()
    assert not CandidateNotification.objects.filter(
        application=app, kind=K.INTERVIEW_SLOT_INVITE
    ).exists()
    # Verification itself emailed nothing either — the candidate's next mail
    # after "application received" is the booking link.
    assert not CandidateNotification.objects.filter(
        application=app, kind=K.HR_VERIFICATION_PASSED
    ).exists()

    with django_capture_on_commit_callbacks(execute=True):
        invite = slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user, options=_windows()
        )
    assert invite.stage_id == app.current_stage_id
    assert invite.status == SlotInviteStatus.PENDING

    mail_row = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SLOT_INVITE)
    assert mail_row.status == "sent"
    assert invite.token in mail_row.body_text
    assert "/interview-slot/" in mail_row.body_text


def test_configuring_needs_scheduling_authority_and_an_interview_stage(
    therapist_job, make_application, staff
):
    app = make_application(therapist_job)
    # Still at verification — not an interview round.
    with pytest.raises(ValidationError, match="interview round"):
        slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user, options=_windows()
        )
    # An interviewer role without INTERVIEW/CREATE is stopped by RBAC.
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    with pytest.raises(AccessDenied):
        slots.configure_slot_invite(
            application=app, actor=staff["clinic_doctor"].user, options=_windows()
        )


def test_scheduling_and_slot_links_are_the_hr_heads_alone(
    therapist_job, make_application, staff
):
    """
    The policy, pinned: sending a booking link and scheduling an interview
    are the HR HEAD's acts. The recruiter and HR Manager who run the rest of
    the pipeline are refused both — losing this again is a policy change.
    """
    app = make_application(therapist_job)
    record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()

    for role in ("recruiter", "hr_manager"):
        with pytest.raises(AccessDenied):
            slots.configure_slot_invite(
                application=app, actor=staff[role].user, options=_windows()
            )
        with pytest.raises(AccessDenied):
            schedule_interview(
                application=app,
                stage=app.current_stage,
                interviewer=staff["clinic_doctor"],
                actor=staff[role].user,
                scheduled_at=timezone.now() + dt.timedelta(days=1),
            )

    # And the HR Head is not.
    invite = slots.configure_slot_invite(
        application=app, actor=staff["hr_head"].user, options=_windows()
    )
    assert invite.pk


def test_reconfiguring_kills_the_old_link_and_sends_a_fresh_one(
    invited, staff, django_capture_on_commit_callbacks
):
    first = InterviewSlotInvite.objects.get(application=invited)
    with django_capture_on_commit_callbacks(execute=True):
        second = slots.configure_slot_invite(
            application=invited, actor=staff["hr_head"].user, options=_windows(count=2)
        )
    first.refresh_from_db()
    assert first.status == SlotInviteStatus.CANCELLED
    assert second.pk != first.pk and second.status == SlotInviteStatus.PENDING
    # The dead token no longer opens.
    assert APIClient().get(
        f"/api/v1/public/interview-slot/{first.token}/"
    ).data["open"] is False


def test_the_public_page_offers_only_the_offered_future_slots(invited):
    invite = InterviewSlotInvite.objects.get(
        application=invited, status=SlotInviteStatus.PENDING
    )
    api = APIClient()
    r = api.get(f"/api/v1/public/interview-slot/{invite.token}/")
    assert r.status_code == 200
    assert r.data["job_title"] == "Therapist" and r.data["open"] is True
    assert r.data["options"] == invite.options  # all four are future and free
    # Nothing internal.
    assert "application" not in r.data and "id" not in r.data
    assert api.get("/api/v1/public/interview-slot/not-a-token/").status_code == 404


def test_a_window_taken_by_another_candidate_disappears_and_is_refused(
    invited, therapist_job, make_application, staff
):
    invite = InterviewSlotInvite.objects.get(
        application=invited, status=SlotInviteStatus.PENDING
    )
    taken = invite.options[0]

    # A second candidate for the same job and round takes the first window.
    rival = make_application(therapist_job, first_name="Banu")
    record_decision(application=rival, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    rival.refresh_from_db()
    rival_invite = slots.configure_slot_invite(
        application=rival, actor=staff["hr_head"].user, options=[taken]
    )
    slots.select_slot(invite=rival_invite, start=taken["start"], end=taken["end"])

    # The window is gone from the first candidate's page…
    page = APIClient().get(f"/api/v1/public/interview-slot/{invite.token}/")
    assert taken not in page.data["options"]
    assert len(page.data["options"]) == len(invite.options) - 1
    # …and refused if posted anyway (page loaded before the rival chose).
    with pytest.raises(ValidationError, match="just been taken"):
        slots.select_slot(invite=invite, start=taken["start"], end=taken["end"])


def test_the_candidate_selects_a_slot_and_the_schedulers_are_told(invited):
    invite = InterviewSlotInvite.objects.get(
        application=invited, status=SlotInviteStatus.PENDING
    )
    chosen = invite.options[0]
    r = APIClient().post(
        f"/api/v1/public/interview-slot/{invite.token}/",
        {"start": chosen["start"], "end": chosen["end"]}, format="json",
    )
    assert r.status_code == 200, r.data
    invite.refresh_from_db()
    assert invite.status == SlotInviteStatus.SELECTED and invite.selected_slot == chosen

    told = Notification.objects.filter(kind=NotificationKind.INTERVIEW_SLOT_SELECTED)
    assert told.exists()
    body = told.first().body
    assert invited.candidate.full_name in told.first().title
    assert "Confirm it by scheduling" in body

    # A second choice is refused — the first is already announced.
    r2 = APIClient().post(
        f"/api/v1/public/interview-slot/{invite.token}/",
        {"start": invite.options[1]["start"], "end": invite.options[1]["end"]}, format="json",
    )
    assert r2.status_code == 400


def test_a_slot_not_on_the_offer_is_refused(invited):
    invite = InterviewSlotInvite.objects.get(
        application=invited, status=SlotInviteStatus.PENDING
    )
    fake_start = (timezone.now() + dt.timedelta(days=2)).isoformat()
    fake_end = (timezone.now() + dt.timedelta(days=2, hours=2)).isoformat()
    r = APIClient().post(
        f"/api/v1/public/interview-slot/{invite.token}/",
        {"start": fake_start, "end": fake_end}, format="json",
    )
    assert r.status_code == 400
    invite.refresh_from_db()
    assert invite.status == SlotInviteStatus.PENDING


def test_an_expired_invite_takes_no_choice(invited):
    invite = InterviewSlotInvite.objects.get(
        application=invited, status=SlotInviteStatus.PENDING
    )
    invite.expires_at = timezone.now() - dt.timedelta(minutes=1)
    invite.save()
    chosen = invite.options[0]
    with pytest.raises(ValidationError, match="no longer open"):
        slots.select_slot(invite=invite, start=chosen["start"], end=chosen["end"])


def test_scheduling_confirms_the_invite_and_sends_the_final_schedule(
    invited, staff, django_capture_on_commit_callbacks
):
    app = invited
    invite = InterviewSlotInvite.objects.get(application=app, status=SlotInviteStatus.PENDING)
    chosen = invite.options[0]
    slots.select_slot(invite=invite, start=chosen["start"], end=chosen["end"])

    with django_capture_on_commit_callbacks(execute=True):
        schedule_interview(
            application=app, stage=app.current_stage, interviewer=staff["clinic_doctor"],
            actor=staff["hr_head"].user,
            scheduled_at=dt.datetime.fromisoformat(chosen["start"]),
            mode="video", location_or_link="https://meet.google.com/abc-defg-hij",
        )
    invite.refresh_from_db()
    assert invite.status == SlotInviteStatus.CONFIRMED
    final = CandidateNotification.objects.get(application=app, kind=K.INTERVIEW_SCHEDULED)
    assert "meet.google.com" in final.body_text  # the meeting link rides the existing email


def test_passing_a_round_sends_nothing_until_hr_configures_the_next(
    invited, staff, django_capture_on_commit_callbacks
):
    from tests.recruitment.test_helpers import complete_interview_for

    app = invited
    complete_interview_for(app, 30, "clinic_doctor", staff)
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["clinic_doctor"].user, decision=Decision.PASS)
    app.refresh_from_db()

    # Round two: no automatic invite, no automatic email.
    assert not InterviewSlotInvite.objects.filter(
        application=app, stage=app.current_stage
    ).exists()
    assert CandidateNotification.objects.filter(
        application=app, kind=K.INTERVIEW_SLOT_INVITE
    ).count() == 1  # only round one's

    # HR configures round two → a fresh link for the new round goes out.
    with django_capture_on_commit_callbacks(execute=True):
        second = slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user, options=_windows(start_days=8)
        )
    assert second.stage_id == app.current_stage_id
    assert CandidateNotification.objects.filter(
        application=app, kind=K.INTERVIEW_SLOT_INVITE
    ).count() == 2


def test_a_candidate_without_an_email_is_refused_clearly(
    therapist_job, make_application, staff, django_capture_on_commit_callbacks
):
    app = make_application(therapist_job)
    app.candidate.email = None
    app.candidate.save()
    with django_capture_on_commit_callbacks(execute=True):
        record_decision(application=app, actor=staff["recruiter"].user, decision=Decision.VERIFY)
    app.refresh_from_db()
    assert app.current_stage.kind == "interview"  # the workflow moved regardless
    with pytest.raises(ValidationError, match="no email"):
        slots.configure_slot_invite(
            application=app, actor=staff["hr_head"].user, options=_windows()
        )
    assert not InterviewSlotInvite.objects.filter(application=app).exists()
