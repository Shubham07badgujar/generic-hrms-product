"""
What candidates are told, and the record that they were told it.

THE RULE
--------
An email is a CONSEQUENCE of a state change, never a substitute for one. Every
sender here is called by the service that performed the transition — the
engine, the interview scheduler, the offer workflow — after that transition has
been written, and reads its facts from the Application it was handed. A view
cannot cause a candidate email; only a committed change can. That is what makes
"Selected" impossible to send to somebody whose record says "Rejected".

Sends run in `transaction.on_commit`. If the transition rolls back, nothing was
sent; if it commits, the mail follows. A mail failure is logged, recorded and
surfaced for retry — it never unwinds the transition, because "the candidate
was rejected but the email bounced" is a fact HR can act on, and "the rejection
did not happen because SMTP was down" is not.

IDEMPOTENCY
-----------
Every notification carries a dedupe key derived from the event — the
application plus the transition, plus the interview or offer where one is
involved. The key is UNIQUE on the table. Replaying the request that caused the
transition (a double-click, a retried POST) finds the existing row and does not
send again. A rescheduled interview is a new event and gets a new key.

TEMPLATES
---------
Django templates under recruitment/templates/recruitment/email/: one subject
line, one text part and one HTML part per kind, all extending a shared base.
The rendered result is frozen onto the log row, so what was actually said
survives later edits to the template. HTML is autoescaped; the plain-text part
is not (it is not HTML).
"""

from __future__ import annotations

import logging

from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    CandidateNotification,
)
from apps.recruitment.models import (
    CandidateNotificationKind as Kind,
)
from apps.recruitment.models import (
    CandidateNotificationStatus as Status,
)
from core.config import email_config, render_message

logger = logging.getLogger("hrms.recruitment.comms")

#: The stage kinds and decisions that produce a candidate email, and which one.
#: The engine consults this table after `record_decision` has written the
#: transition; anything not listed here is silent, deliberately — an
#: interviewer's REQUEST_INFO at HR verification, say, is an internal act.
_STAGE_DECISION_KINDS: dict[tuple[str, str], str] = {
    # Deliberately NO entry for ("hr_verification", "verify"): the recruiter's
    # verification is an internal act. The candidate already got the
    # application-received email at submission and next hears from us when the
    # interview slots are configured — a "verified" email in between is noise.
    ("hr_verification", "reject"): Kind.HR_VERIFICATION_REJECTED,
    ("interview", "pass"): Kind.INTERVIEW_SELECTED,
    ("interview", "recommend_select"): Kind.INTERVIEW_SELECTED,
    ("interview", "recommend_reject"): Kind.INTERVIEW_NOT_SELECTED,
    ("interview", "request_info"): Kind.INTERVIEW_ON_HOLD,
    ("interview", "reject"): Kind.FINAL_REJECTION,
    ("department_decision", "recommend_select"): Kind.DEPARTMENT_DECISION,
    ("department_decision", "recommend_reject"): Kind.DEPARTMENT_DECISION,
    ("department_decision", "pass"): Kind.DEPARTMENT_DECISION,
    ("department_decision", "reject"): Kind.FINAL_REJECTION,
    ("hr_final_decision", "select"): Kind.FINAL_SELECTION,
    ("hr_final_decision", "reject"): Kind.FINAL_REJECTION,
    ("application", "reject"): Kind.HR_VERIFICATION_REJECTED,
    ("application", "screen_out"): Kind.HR_VERIFICATION_REJECTED,
    ("hr_verification", "screen_out"): Kind.HR_VERIFICATION_REJECTED,
}


def kind_for_decision(stage, decision: str) -> str | None:
    """Which email, if any, a decision at a stage earns the candidate."""
    return _STAGE_DECISION_KINDS.get((stage.kind, decision))


# ---------------------------------------------------------------- context


def _organization_for(application):
    """
    The company this correspondence comes from.

    Relational: the application carries its own organization, so the company
    named in a candidate's inbox is a property of the record rather than of
    whoever happened to be signed in. This read ambient context while the
    recruitment tables were still being converted; they carry the column now,
    and reading it from the row is what makes a send that runs later on a
    worker -- a retry, a queued batch -- still name the right company.

    Returns None only for a row with no organization, and the caller then
    falls back to the deployment's display name rather than guessing. Naming
    the wrong company in a candidate's inbox would be worse than naming none.
    """
    return getattr(application, "organization", None)


def _company_name(organization=None) -> str:
    from apps.accounts.services.passwords import _company_name as company

    return company(organization)


def _reference(application: Application) -> str:
    """Short, human, and unique enough for a subject line: APP-<8 hex>."""
    return f"APP-{str(application.pk)[:8].upper()}"


def base_context(application: Application) -> dict:
    job = application.job_opening
    candidate = application.candidate
    stage = application.current_stage
    return {
        "candidate_name": candidate.full_name,
        "job_title": job.title,
        "application_id": _reference(application),
        "applied_on": timezone.localtime(application.applied_at).strftime("%d %b %Y"),
        "current_status": _status_label(application),
        "current_stage": getattr(stage, "name", ""),
        "company_name": _company_name(_organization_for(application)),
        "hr_contact_email": email_config(
            _organization_for(application)
        ).hr_contact,
        "next_step": "",
    }


def _status_label(application: Application) -> str:
    stage = application.current_stage
    if application.status == "active" and stage is not None:
        return stage.name
    return application.get_status_display()


def interview_context(interview) -> dict:
    when = timezone.localtime(interview.scheduled_at)
    end = timezone.localtime(interview.scheduled_end)
    mode = (interview.mode or "").replace("_", " ").strip().capitalize() or "In person"
    return {
        "interview_round": interview.stage.name,
        "interview_date": when.strftime("%A, %d %B %Y"),
        "interview_time": f"{when:%H:%M} – {end:%H:%M} ({when:%Z})",
        "interviewer_name": interview.interviewer.full_name if interview.interviewer_id else "",
        "interview_mode": mode,
        "meeting_link": interview.location_or_link or "",
    }


def _money(value) -> str:
    """₹12,00,000-style is what Indian readers expect; fall back to the raw value."""
    from decimal import Decimal, InvalidOperation

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    whole = f"{int(amount):d}"
    if len(whole) <= 3:
        return f"₹{whole}"
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


def offer_context(offer) -> dict:
    designation = (
        offer.designation.title
        if offer.designation_id
        else (offer.application.job_opening.title)
    )
    return {
        "offer_designation": designation,
        "offer_ctc": _money(offer.offered_ctc),
        "joining_date": offer.joining_date.strftime("%d %b %Y"),
        "offer_valid_until": offer.valid_until.strftime("%d %b %Y") if offer.valid_until else "",
    }


# ---------------------------------------------------------------- rendering


def template_key(kind: str) -> str:
    """The shipped template path for a kind, which is also its override key."""
    return f"recruitment/email/{kind}"


def render(kind: str, context: dict, *, organization=None) -> tuple[str, str, str]:
    """
    (subject, text, html) for a kind, as THIS organization words it.

    An organization that has written its own version of this message gets its
    own; every other organization gets the text this product ships. Passing no
    organization renders the shipped template -- never another customer's
    wording, which has no resolution order in which it could appear.

    Raises TemplateDoesNotExist for an unknown kind.
    """
    message = render_message(organization, template_key(kind), context)
    return message.subject, message.text, message.html


# ---------------------------------------------------------------- sending


def _deliver(notification: CandidateNotification) -> None:
    """One SMTP attempt, outcome written to the row. Never raises."""
    notification.attempts += 1
    notification.last_attempt_at = timezone.now()

    if not notification.recipient_email:
        notification.status = Status.SKIPPED
        notification.error = "The candidate has no email address on record."
        notification.save(
            update_fields=["attempts", "last_attempt_at", "status", "error", "updated_at"]
        )
        return

    # A candidate notification is sent BY an organization, so the sender
    # identity, the reply-to address and the connection are all theirs. The
    # notification row carries its own organization -- taking it from the row
    # rather than from ambient context is what makes a retry, which may run
    # much later on a worker, still send as the right company.
    mail = email_config(notification.organization_id)

    try:
        message = EmailMultiAlternatives(
            subject=notification.subject,
            body=notification.body_text,
            from_email=mail.from_email,
            to=[notification.recipient_email],
            reply_to=[mail.hr_contact] if mail.hr_contact else None,
            connection=mail.connection(),
        )
        message.attach_alternative(notification.body_html, "text/html")
        for filename, content, mimetype in _attachments_for(notification):
            message.attach(filename, content, mimetype)
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — recorded, surfaced, never raised
        logger.exception(
            "recruitment.candidate_email_failed notification=%s kind=%s",
            notification.pk, notification.kind,
        )
        notification.status = Status.FAILED
        notification.error = f"{type(exc).__name__}: {exc}"[:2000]
        notification.save(
            update_fields=["attempts", "last_attempt_at", "status", "error", "updated_at"]
        )
        _audit(notification, verb="create", event="candidate_email_failed")
        return

    notification.status = Status.SENT
    notification.sent_at = timezone.now()
    notification.error = ""
    notification.save(
        update_fields=["attempts", "last_attempt_at", "status", "sent_at", "error", "updated_at"]
    )
    _audit(notification, verb="create", event="candidate_email_sent")


def notify_candidate(
    *,
    application: Application,
    kind: str,
    dedupe_key: str,
    extra_context: dict | None = None,
    actor=None,
) -> CandidateNotification | None:
    """
    Record and (after commit) send one email about one application.

    Returns the log row, or None if this event was already recorded — the
    unique dedupe key is what makes a replayed request a no-op.

    Called INSIDE the transition's transaction. The row is created now, in the
    same transaction as the change it announces, so it rolls back with it. The
    SMTP attempt is deferred to on_commit so nothing is sent for a change that
    did not stick.
    """
    application = Application.objects.select_related(
        "candidate", "job_opening", "current_stage"
    ).get(pk=application.pk)

    context = base_context(application)
    context.update(extra_context or {})
    try:
        subject, text, html = render(
            kind, context, organization=_organization_for(application)
        )
    except Exception:  # noqa: BLE001 — a broken template must not break hiring
        logger.exception("recruitment.candidate_email_template_error kind=%s", kind)
        return None

    try:
        with transaction.atomic():
            row = CandidateNotification.objects.create(
                application=application,
                candidate=application.candidate,
                job_opening=application.job_opening,
                kind=kind,
                recipient_email=application.candidate.email or "",
                subject=subject[:255],
                body_text=text,
                body_html=html,
                context={k: str(v) for k, v in context.items()},
                triggered_by=actor if getattr(actor, "pk", None) else None,
                dedupe_key=dedupe_key[:200],
            )
    except IntegrityError:
        # Already recorded for this exact event. Not a second email.
        return None

    transaction.on_commit(lambda: _deliver(_reload(row.pk)))
    return row


def _attachments_for(notification: CandidateNotification) -> list[tuple[str, bytes, str]]:
    """
    Files that ride along with a notification.

    The offer email carries the offer letter PDF that was generated and
    FROZEN on the Offer row at send time — so a retry attaches exactly the
    letter the first attempt carried, whatever the letterhead says today.
    Any failure here downgrades to "no attachment", never to "no email".
    """
    if notification.kind != Kind.OFFER_SENT:
        return []
    try:
        offer = notification.application.offer
        if not offer.letter_pdf:
            return []
        candidate = notification.application.candidate
        return [(
            f"Offer Letter - {candidate.full_name}.pdf",
            offer.letter_pdf.read(),
            "application/pdf",
        )]
    except Exception:  # noqa: BLE001 — the email must still go
        logger.exception(
            "recruitment.offer_letter_attach_failed notification=%s", notification.pk
        )
        return []


def _reload(pk) -> CandidateNotification:
    return CandidateNotification.objects.select_related("candidate", "application").get(pk=pk)


def retry(*, notification: CandidateNotification, actor) -> CandidateNotification:
    """
    Try a failed or skipped send again. HR's button.

    Re-reads the recipient from the candidate, so fixing a mistyped address and
    pressing retry does the obvious thing. A SENT row is not re-sent — a second
    copy of "you have been rejected" is not a retry, it is a mistake — and the
    caller is told so.
    """
    from django.core.exceptions import ValidationError

    if notification.status == Status.SENT:
        raise ValidationError({"notification": "This email was delivered; it will not be sent twice."})

    notification.recipient_email = notification.candidate.email or ""
    notification.status = Status.PENDING
    notification.save(update_fields=["recipient_email", "status", "updated_at"])
    _audit(notification, verb="update", event="candidate_email_retry", actor=actor)
    _deliver(notification)
    return notification


# ---------------------------------------------------------------- audit


def _audit(notification: CandidateNotification, *, verb: str, event: str, actor=None) -> None:
    from apps.audit.events import record_event
    from core.access import Resource

    record_event(
        notification,
        actor=actor or notification.triggered_by,
        entity_type="recruitment.CandidateNotification",
        verb=verb,
        resource=Resource.APPLICATION,
        after={
            "event": event,
            "kind": notification.kind,
            "application_id": str(notification.application_id),
            "candidate_id": str(notification.candidate_id),
            "job_opening_id": str(notification.job_opening_id),
            "status": notification.status,
            "attempts": notification.attempts,
            # The address, not the body: the audit trail says WHAT was sent
            # and to WHOM, and the log row keeps the words.
            "recipient": notification.recipient_email,
            "error": notification.error[:300] if notification.error else "",
        },
    )
