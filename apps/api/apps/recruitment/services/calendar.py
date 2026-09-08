"""
Google Calendar for interviews.

HRMS is the source of truth: the Interview row decides when, who and whether.
The calendar event MIRRORS it — invitations land in the interviewer's and the
candidate's calendars, and a Google Meet room is attached when enabled. Every
sync failure is recorded on the interview (`calendar_sync_status`,
`calendar_error`) and retryable; none of them ever blocks scheduling.

CREDENTIALS
-----------
The same one-time OAuth grant the Forms integration uses — `_load_oauth_token`
and the token machinery are imported from `external_forms`, not duplicated.
The grant must carry the calendar scope (SCOPES there includes it); a grant
recorded before that scope existed fails with "insufficient authentication
scopes", and the fix is re-running `manage.py google_forms_authorize` once.

TIMING
------
`sync_created` runs INSIDE the scheduling transaction, deliberately: the Meet
link it obtains is written to `location_or_link` BEFORE the "interview
scheduled" email renders, so the candidate's confirmation carries the link.
The trade-off — a rollback after event creation orphans one calendar event —
is accepted; the reverse (an email with no link, patched by a second email)
confuses every candidate every time.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from .external_forms import (
    RequestsTransport,
    Transport,
    _load_oauth_token,
    _load_service_account,
    token_for,
)

logger = logging.getLogger("hrms.recruitment.calendar")

CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"


def calendar_enabled() -> bool:
    """On when a Google credential is configured and the switch is not off."""
    return bool(
        getattr(settings, "GOOGLE_CALENDAR_ENABLED", True)
        and (_load_oauth_token() or _load_service_account())
    )


class GoogleCalendarService:
    """
    Create / update / cancel interview events. `sendUpdates=all` on every
    write is what makes Google email the calendar invitations.
    """

    def __init__(self, *, credentials: dict, transport: Transport | None = None):
        self._transport = transport or RequestsTransport()
        self._token = token_for(credentials, self._transport)
        self._calendar_id = getattr(settings, "GOOGLE_CALENDAR_ID", "primary")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token.get()}",
            "Content-Type": "application/json",
        }

    def _url(self, event_id: str = "") -> str:
        base = f"{CALENDAR_API}/{self._calendar_id}/events"
        return f"{base}/{event_id}" if event_id else base

    # ---- payload ------------------------------------------------------

    def _event_body(self, interview) -> dict:
        application = interview.application
        candidate = application.candidate
        job = application.job_opening
        tz = str(timezone.get_current_timezone())

        body: dict = {
            "summary": (
                f"Interview - {candidate.full_name} - {job.title} - "
                f"{interview.stage.name}"
            ),
            "description": (
                f"Candidate: {candidate.full_name}\n"
                f"Position: {job.title}\n"
                f"Round: {interview.stage.name}\n"
                f"Interviewer: {interview.interviewer.full_name}\n"
                f"HRMS reference: APP-{str(application.pk)[:8].upper()}\n\n"
                f"Scheduled through the HRMS. The HRMS remains the source of "
                f"truth; changes to timing are made there, not by editing "
                f"this event."
            ),
            "start": {"dateTime": interview.scheduled_at.isoformat(), "timeZone": tz},
            "end": {"dateTime": interview.scheduled_end.isoformat(), "timeZone": tz},
            "attendees": self._attendees(interview),
            # The event mirrors HRMS — guests must not move it.
            "guestsCanModify": False,
        }
        return body

    @staticmethod
    def _attendees(interview) -> list[dict]:
        rows = []
        interviewer = interview.interviewer
        interviewer_email = interviewer.work_email or (
            interviewer.user.email if interviewer.user_id else ""
        )
        if interviewer_email:
            rows.append({"email": interviewer_email})
        candidate_email = interview.application.candidate.email
        if candidate_email:
            rows.append({"email": candidate_email})
        return rows

    # ---- operations ---------------------------------------------------

    def create_event(self, interview) -> dict:
        body = self._event_body(interview)
        params = {"sendUpdates": "all"}
        if getattr(settings, "GOOGLE_MEET_ENABLED", True):
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": f"hrms-{interview.pk}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            params["conferenceDataVersion"] = 1
        return self._transport.request(
            "POST", self._url(), headers=self._headers(), json_body=body, params=params
        )

    def update_event(self, interview) -> dict:
        return self._transport.request(
            "PATCH",
            self._url(interview.calendar_event_id),
            headers=self._headers(),
            json_body=self._event_body(interview),
            params={"sendUpdates": "all"},
        )

    def cancel_event(self, event_id: str) -> None:
        self._transport.request(
            "DELETE",
            self._url(event_id),
            headers=self._headers(),
            params={"sendUpdates": "all"},
        )


def service_for(transport: Transport | None = None) -> GoogleCalendarService | None:
    credentials = _load_oauth_token() or _load_service_account()
    if credentials is None or not getattr(settings, "GOOGLE_CALENDAR_ENABLED", True):
        return None
    return GoogleCalendarService(credentials=credentials, transport=transport)


# ------------------------------------------------------------------- sync

#: What the interview row records about each outcome. Never raises: a Google
#: outage is a fact to record and retry, not a reason a booking fails.


def sync_created(interview, *, service: GoogleCalendarService | None = None) -> None:
    service = service or service_for()
    if service is None:
        return
    try:
        event = service.create_event(interview)
    except Exception as exc:  # noqa: BLE001 — recorded on the row, retryable
        logger.exception("recruitment.calendar_create_failed interview=%s", interview.pk)
        interview.calendar_sync_status = "failed"
        interview.calendar_error = str(exc)[:2000]
        interview.save(update_fields=["calendar_sync_status", "calendar_error", "updated_at"])
        _audit(interview, event="calendar_event_failed")
        return

    interview.calendar_event_id = event.get("id", "")
    interview.calendar_sync_status = "synced"
    interview.calendar_error = ""
    meet = event.get("hangoutLink", "")
    fields = ["calendar_event_id", "calendar_sync_status", "calendar_error", "updated_at"]
    # The Meet room becomes the meeting link unless HR already supplied one.
    if meet and not interview.location_or_link:
        interview.location_or_link = meet
        fields.append("location_or_link")
    interview.save(update_fields=fields)
    _audit(interview, event="calendar_event_created")


def sync_updated(interview, *, service: GoogleCalendarService | None = None) -> None:
    service = service or service_for()
    if service is None:
        return
    if not interview.calendar_event_id:
        # Never created (or creation failed earlier) — this IS the retry.
        sync_created(interview, service=service)
        return
    try:
        service.update_event(interview)
    except Exception as exc:  # noqa: BLE001
        logger.exception("recruitment.calendar_update_failed interview=%s", interview.pk)
        interview.calendar_sync_status = "failed"
        interview.calendar_error = str(exc)[:2000]
        interview.save(update_fields=["calendar_sync_status", "calendar_error", "updated_at"])
        _audit(interview, event="calendar_event_update_failed")
        return
    interview.calendar_sync_status = "synced"
    interview.calendar_error = ""
    interview.save(update_fields=["calendar_sync_status", "calendar_error", "updated_at"])
    _audit(interview, event="calendar_event_updated")


def sync_cancelled(interview, *, service: GoogleCalendarService | None = None) -> None:
    service = service or service_for()
    if service is None or not interview.calendar_event_id:
        return
    try:
        service.cancel_event(interview.calendar_event_id)
    except Exception as exc:  # noqa: BLE001 — a vanished event is already cancelled
        logger.warning(
            "recruitment.calendar_cancel_failed interview=%s error=%s", interview.pk, exc
        )
        interview.calendar_sync_status = "failed"
        interview.calendar_error = str(exc)[:2000]
        interview.save(update_fields=["calendar_sync_status", "calendar_error", "updated_at"])
        _audit(interview, event="calendar_event_cancel_failed")
        return
    interview.calendar_sync_status = "cancelled"
    interview.calendar_error = ""
    interview.save(update_fields=["calendar_sync_status", "calendar_error", "updated_at"])
    _audit(interview, event="calendar_event_cancelled")


def retry_sync(interview, *, actor=None) -> None:
    """
    HR's retry button. A cancelled interview retries the cancellation; a
    live one retries create-or-update. Same never-raise contract.
    """
    from apps.recruitment.models import BLOCKING_INTERVIEW_STATUSES

    if interview.status in BLOCKING_INTERVIEW_STATUSES:
        sync_updated(interview)
    else:
        sync_cancelled(interview)


def _audit(interview, *, event: str) -> None:
    from apps.audit.events import record_event
    from core.access import Resource

    record_event(
        interview,
        actor=None,
        entity_type="recruitment.Interview",
        verb="update",
        resource=Resource.INTERVIEW,
        after={
            "event": event,
            "application_id": str(interview.application_id),
            "calendar_event_id": interview.calendar_event_id,
            "calendar_sync_status": interview.calendar_sync_status,
            "error": interview.calendar_error[:200],
        },
    )
