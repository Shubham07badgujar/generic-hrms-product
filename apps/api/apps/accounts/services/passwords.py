"""
Temporary passwords, first-login password change, and the welcome email.

The lifecycle this closes:

    HR creates employee → login created with a GENERATED temporary password
    → email tells the person their username, the password and where to sign in
    → they log in; every request is refused until they set their own password
    → they set one; the temporary one is dead; their sessions are cut over.

WHAT MAKES THE TEMPORARY PASSWORD ACTUALLY TEMPORARY
----------------------------------------------------
Two things, and both are needed. `must_change_password` is set on the account
at creation and cleared only by `change_password`; a middleware refuses every
authenticated request except the handful needed to change it while the flag is
up. And `set_password` overwrites the hash — the old secret verifies against
nothing afterwards, and every outstanding refresh token is blacklisted, so a
session opened with the temporary password cannot outlive it either.
"""

from __future__ import annotations

import logging
import secrets
import string

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from core.config import email_config

logger = logging.getLogger("hrms.accounts")

#: Letters, digits and a few punctuation marks a person can read out over the
#: phone without ambiguity — no l/1/I/O/0 lookalikes. 14 characters from this
#: alphabet is comfortably past the site's 12-character minimum and far past
#: anything guessable; it is meant to be used once and replaced.
_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits + "@#%&*+=?" if c not in "l1IO0"
)
TEMPORARY_PASSWORD_LENGTH = 14


class PasswordError(ValidationError):
    """A refused password operation."""


def generate_temporary_password() -> str:
    """A fresh secret from the OS CSPRNG. Never logged, never stored in clear."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))


@transaction.atomic
def change_password(*, user, current_password: str, new_password: str) -> None:
    """
    Set a new password of the user's own choosing.

    The current password is required even during the forced first-login change:
    a stolen access token must not be enough to take over the account by
    setting a new password on it. Validators are the site's configured set —
    length, commonness, similarity to the user's own name and email.
    """
    if not user.check_password(current_password):
        raise PasswordError({"current_password": "That is not your current password."})
    if current_password == new_password:
        raise PasswordError({"new_password": "The new password must differ from the current one."})

    try:
        password_validation.validate_password(new_password, user=user)
    except ValidationError as exc:
        raise PasswordError({"new_password": exc.messages}) from exc

    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])

    # Every session opened before this moment — including the one opened with
    # the temporary password — is cut over. The caller's own refresh token goes
    # too; the client re-authenticates with the new password, which is the
    # honest outcome of having changed it.
    _revoke_all_refresh_tokens(user)

    _audit(user, verb="update", event="password_changed")
    logger.info("accounts.password_changed user=%s", user.pk)


def _revoke_all_refresh_tokens(user) -> None:
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def _organization_of(user):
    """
    The organization a principal belongs to, or None.

    Mail is addressed on behalf of a specific company, so it has to be resolved
    from the recipient rather than read from a settings row -- there is no
    longer a single company to read. Swallows failure for the same reason the
    callers below do: an account exists whether or not its welcome mail can
    name the company, and failing the send would be the worse outcome.
    """
    try:
        from apps.organization.membership import organization_of

        return organization_of(user)
    except Exception:  # noqa: BLE001 — a missing table must not stop the mail
        logger.warning("accounts.org_lookup_failed", exc_info=True)
        return None


def _company_name(organization) -> str:
    """
    The organisation's own name, as its administrator maintains it.

    The env setting is only a fallback for a principal whose organization
    cannot be resolved. Preferring the setting would mean the email said
    something different from every letter and payslip.
    """
    if organization is not None and organization.name:
        return organization.name
    return settings.ORG_DISPLAY_NAME


def _legal_name(organization) -> str:
    """
    The registered entity, for the signature block.

    A real legal name in the footer is one of the plainest signals that a
    message comes from a business rather than a bulk sender, and it costs
    nothing. Empty when the company has not recorded one.
    """
    return getattr(organization, "legal_name", "") or ""


def _position_of(employee, role) -> str:
    """
    What to print as the assigned position.

    Designation is optional on an Employee — plenty of joiners have a role and
    no formal title yet — so the role's name is the fallback. Between them
    there is always something true to say, which is better than a line reading
    "Your assigned position is: None".
    """
    designation = getattr(employee, "designation", None) if employee else None
    if designation and getattr(designation, "title", ""):
        return designation.title
    if role is not None and getattr(role, "name", ""):
        return role.name
    return ""


def _hr_mail_connection(organization=None, *, config=None):
    """
    HR's own authenticated sender, for THIS organization.

    The welcome email announces an employment relationship, so the office sends
    it from HR's mailbox rather than the recruitment address candidates
    correspond with. Returns None when unconfigured — the message then goes
    through the deployment's default connection, exactly as before.

    The organization is passed EXPLICITLY. There is no ambient-current-org
    convenience here on purpose: the failure mode of forgetting one would be a
    message sent through another customer's mail server, authenticating as
    them, and nothing about that announces itself. An explicit argument turns
    a forgotten call site into a visible one.

    Callers that have already resolved the configuration pass it in rather than
    resolving twice. The function stays the SEAM either way -- it is what a
    test substitutes to simulate an SMTP failure, and bypassing it here for a
    direct `config.connection()` call silently disarmed exactly that test.
    """
    from core.config import email_config

    return (config or email_config(organization)).connection()


def _transactional_headers(from_email: str) -> dict:
    """
    Headers that say "one person, one event" rather than "campaign".

    `Message-ID` is the one that matters. Django derives it from the machine's
    hostname, which inside a container is a random hex string that resolves
    nowhere - and a Message-ID whose domain does not exist is a signal spam
    filters weigh. Rebuilding it at the sending domain costs nothing and
    removes the anomaly.

    `Auto-Submitted` is the RFC 3834 way of saying this was generated by a
    system rather than typed by a person, which is what stops mailing-list and
    out-of-office machinery from replying to it. Deliberately NO
    `List-Unsubscribe`: that header belongs on bulk mail, and claiming a new
    employee can unsubscribe from their own account details would be a lie.
    """
    import email.utils

    domain = from_email.rsplit("@", 1)[-1].strip().strip(">") or "localhost"
    return {
        "Message-ID": email.utils.make_msgid(domain=domain),
        "Date": email.utils.formatdate(localtime=True),
        "Auto-Submitted": "auto-generated",
        "X-Auto-Response-Suppress": "OOF, AutoReply",
    }


def send_account_created_email(
    *, user, temporary_password: str, employee=None, role=None, actor=None,
    #: An already-open SMTP connection to send over. For a BULK send: an
    #: employee import that opened one per message would hold a worker for
    #: `EMAIL_TIMEOUT` seconds per person, two hundred times. Left None, this
    #: opens and closes its own, which is right for a single hire.
    connection=None,
) -> bool:
    """
    Tell the new employee how to sign in.

    Sent by whoever created the account, AFTER that creation committed, so a
    rolled-back creation never announces credentials for a login that does not
    exist — and so the employee record, the login, the username and the
    temporary password are all real by the time this runs.

    Goes out as multipart: a plain-text part for clients that want one, and an
    HTML part so it reads as a company communication rather than a dump. Both
    carry the same words; neither carries anything the other does not.

    The temporary password appears here and nowhere else — not in the audit
    trail, not in a log line, not in any API response. If this mail is lost the
    remedy is a reset, not a lookup.

    Returns whether delivery was attempted successfully. A mail failure is
    logged, audited and REPORTED, never raised: the account exists either way,
    and failing the creation would be the worse outcome. The caller surfaces
    the result so nobody is told credentials went out when they did not.
    """
    organization = _organization_of(user)
    company = _company_name(organization)
    legal_name = _legal_name(organization)
    login_url = f"{settings.FRONTEND_URL.rstrip('/')}/login"
    # FRONTEND_URL stays a deployment setting: there is one SPA, and a
    # per-organization login URL would be a custom-domain feature that does not
    # exist. The CONTACT address is per-organization, because it is the
    # customer's own HR mailbox that a confused new joiner replies to.
    mail_config = email_config(organization)
    hr_contact = mail_config.hr_contact
    name = (getattr(employee, "full_name", "") or user.get_full_name() or user.email).strip()
    position = _position_of(employee, role)

    # The credentials go ONLY to the PERSONAL email when one is on record:
    # the company mailbox may not exist yet on day one, and the whole point
    # of this message is to let the person reach it. Either address signs
    # them in — the login resolves a personal email to its owning account.
    recipient = (getattr(employee, "personal_email", "") or "").strip() or user.email

    # A concrete, personal subject rather than "Account Details" - that phrase
    # is phishing's stock-in-trade and filters weight it accordingly. The
    # employee code acts as a reference the way an order number does on a
    # receipt: it marks this as one-off transactional mail, not a campaign.
    reference = getattr(employee, "employee_code", "") or ""
    subject = (
        f"Your {company} HRMS account is ready"
        + (f" – {reference}" if reference else "")
    )

    position_text = f"Position: {position}\n" if position else ""
    reference_text = f"Employee code: {reference}\n" if reference else ""
    text_body = (
        f"Dear {name},\n\n"
        f"Your employee account on the {company} HRMS has been created by "
        f"the HR team, and this message carries your sign-in details.\n\n"
        f"{reference_text}"
        f"{position_text}"
        f"\n"
        f"HOW TO SIGN IN\n"
        f"--------------\n"
        f"Portal:             {login_url}\n"
        f"Login email:        {user.email}\n"
        f"Temporary password: {temporary_password}\n\n"
        f"You may sign in with the login email above or with this personal "
        f"email address - both open the same account.\n\n"
        f"You will be asked to choose your own password the first time you "
        f"sign in. The temporary password above stops working at that point, "
        f"so it cannot be reused by anyone who reads this message later."
        f"\n\n"
        f"A NOTE ON SECURITY\n"
        f"------------------\n"
        f"{company} will never ask you for your password by email, telephone "
        f"or message. If anyone does, do not reply - tell HR.\n\n"
        f"Please do not forward this message or share these details.\n\n"
        f"WHY YOU RECEIVED THIS\n"
        f"---------------------\n"
        f"This is an automatic message, sent once, to the personal email "
        f"address recorded for you when your employment record was created. "
        f"It is not a newsletter and there is nothing to unsubscribe from."
        f"\n\n"
        f"If you were not expecting this, or anything above looks wrong, "
        f"please contact HR at {hr_contact} before signing in.\n\n"
        f"Regards,\n"
        f"HR Team\n"
        f"{legal_name or company}\n"
        f"{hr_contact}\n"
    )

    position_html = (
        f'''<tr><td style="padding:4px 16px 4px 0;color:#5b6070">Position</td>
        <td style="padding:4px 0"><strong>{escape(position)}</strong></td></tr>'''
        if position
        else ""
    )
    reference_html = (
        f'''<tr><td style="padding:4px 16px 4px 0;color:#5b6070">Employee code</td>
        <td style="padding:4px 0"><strong>{escape(reference)}</strong></td></tr>'''
        if reference
        else ""
    )
    # One narrow column, real prose, headings that say what each part is, and a
    # link that shows its true destination. No images, no tracking pixel, no
    # button graphics - the shape of a letter from an office rather than a
    # campaign, and every word of it is also in the plain-text part.
    html_body = f"""\
<div style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
            font-size:15px;line-height:1.65;color:#1a1c22;max-width:560px">
  <p style="margin:0 0 16px">Dear {escape(name)},</p>

  <p style="margin:0 0 16px">
    Your employee account on the {escape(company)} HRMS has been created by the
    HR team. Your sign-in details are below.
  </p>

  <table role="presentation" cellpadding="0" cellspacing="0"
         style="margin:0 0 20px;border-collapse:collapse">
    {reference_html}
    {position_html}
  </table>

  <h2 style="margin:0 0 10px;font-size:15px;font-weight:600;color:#1a1c22">
    How to sign in
  </h2>
  <table role="presentation" cellpadding="0" cellspacing="0"
         style="margin:0 0 18px;border:1px solid #e2e2dd;border-radius:6px;
                padding:14px 16px;background:#fafaf8">
    <tr>
      <td style="padding:4px 16px 4px 0;color:#5b6070">Portal</td>
      <td style="padding:4px 0">
        <a href="{escape(login_url)}" style="color:#232b7c">{escape(login_url)}</a>
      </td>
    </tr>
    <tr>
      <td style="padding:4px 16px 4px 0;color:#5b6070">Login email</td>
      <td style="padding:4px 0"><strong>{escape(user.email)}</strong></td>
    </tr>
    <tr>
      <td style="padding:4px 16px 4px 0;color:#5b6070">Temporary password</td>
      <td style="padding:4px 0"><strong
        style="font-family:ui-monospace,Menlo,Consolas,monospace"
        >{escape(temporary_password)}</strong></td>
    </tr>
  </table>

  <p style="margin:0 0 16px">
    You may sign in with the login email above or with this personal email
    address &mdash; both open the same account.
  </p>
  <p style="margin:0 0 22px">
    You will be asked to choose your own password the first time you sign in.
    The temporary password above stops working at that point, so it cannot be
    reused by anyone who reads this message later.
  </p>

  <h2 style="margin:0 0 10px;font-size:15px;font-weight:600;color:#1a1c22">
    A note on security
  </h2>
  <p style="margin:0 0 22px">
    {escape(company)} will never ask you for your password by email, telephone
    or message. If anyone does, do not reply &mdash; tell HR. Please do not
    forward this message or share these details.
  </p>

  <h2 style="margin:0 0 10px;font-size:15px;font-weight:600;color:#1a1c22">
    Why you received this
  </h2>
  <p style="margin:0 0 16px">
    This is an automatic message, sent once, to the personal email address
    recorded for you when your employment record was created. It is not a
    newsletter and there is nothing to unsubscribe from.
  </p>
  <p style="margin:0 0 24px">
    If you were not expecting this, or anything above looks wrong, please
    contact HR at
    <a href="mailto:{escape(hr_contact)}" style="color:#232b7c">{escape(hr_contact)}</a>
    before signing in.
  </p>

  <p style="margin:0 0 4px;color:#5b6070">Regards,</p>
  <p style="margin:0;color:#5b6070">
    <strong style="color:#1a1c22">HR Team</strong><br>
    {escape(legal_name or company)}<br>
    <a href="mailto:{escape(hr_contact)}" style="color:#232b7c">{escape(hr_contact)}</a>
  </p>
</div>"""

    try:
        # Sender, reply-to and connection all come from THIS organization's
        # resolved configuration, falling back field by field to the
        # deployment's -- never to another organization's.
        from_email = mail_config.from_email
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[recipient],
            reply_to=[hr_contact] if hr_contact else None,
            connection=connection or _hr_mail_connection(config=mail_config),
            headers=_transactional_headers(from_email),
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception:  # noqa: BLE001 — logged, deliberately swallowed
        logger.exception("accounts.welcome_email_failed user=%s", user.pk)
        _audit(
            user, verb="create", event="welcome_email_failed",
            actor=actor, recipient=recipient,
        )
        return False

    _audit(
        user, verb="create", event="welcome_email_sent",
        actor=actor, recipient=recipient,
    )
    return True


def _audit(user, *, verb: str, event: str, actor=None, recipient: str = "") -> None:
    from apps.audit.events import record_event
    from core.access import Resource

    after = {"event": event, "email": user.email, "at": timezone.now().isoformat()}
    # The login email and the mailbox a message actually went to can differ:
    # credentials go to the personal address when one is on record. Recording
    # only the login made "the mail never arrived" undiagnosable from the
    # audit trail alone.
    if recipient:
        after["recipient"] = recipient

    record_event(
        user,
        actor=actor or user,
        entity_type="accounts.User",
        verb=verb,
        resource=Resource.USER,
        # Metadata only. Never the password, never a hash.
        after=after,
    )


def reissue_credentials(*, employee, actor, reason: str = "") -> bool:
    """
    Issue a fresh temporary password and send the welcome mail again.

    The reason this exists: a welcome mail can be lost after we have done
    everything right. Gmail and other providers accept a message at SMTP and
    only then decide not to deliver it, bouncing to the SENDER's mailbox —
    so the send looks successful here while the employee never receives it.
    Without a way to re-send, the only remedy was deleting and recreating the
    person, which changes their employee code and loses their history.

    A fresh password is generated rather than resending the old one: the
    original is not stored anywhere (by design), and reissuing invalidates
    whatever may have leaked through a bounce sitting in an inbox.

    Returns whether the mail was accepted for delivery. As with creation, a
    failure is reported rather than raised — the account is still usable.
    """
    from django.core.exceptions import ValidationError

    user = employee.user if employee.user_id else None
    if user is None:
        raise ValidationError(
            {"employee": f"{employee.full_name} has no login, so there are no credentials to send."}
        )
    if not user.is_active:
        raise ValidationError(
            {"employee": f"{employee.full_name}'s login is deactivated. Reactivate it first."}
        )

    return reissue_temporary_password(
        user=user,
        actor=actor,
        employee=employee,
        recipient=employee.personal_email or user.email,
    )


def reissue_temporary_password(
    *, user, actor, employee=None, recipient: str = "",
    event: str = "credentials_reissued",
    failed_event: str = "credentials_reissue_failed",
) -> bool:
    """
    The core of every "send them their credentials again": one path.

    Shared by the employee route above and by the platform's resend of a
    customer administrator's invitation, because the steps are the same and
    the order matters -- a fresh password (the old one is stored nowhere, and a
    bounce sitting in an inbox dies with it), the forced change on next
    sign-in, every refresh token revoked so a session opened on the old
    credential ends, THEN the mail, THEN the audit row saying whether it went.
    Two copies of that sequence would drift, and the one that drifted would be
    the one nobody runs until an invitation is lost.

    The password goes into the mail and nowhere else. The audit row records the
    event, the address and the outcome; never the password, never a hash.
    `event` and `failed_event` name what happened in the trail, depending on
    whether the mail went.
    """
    temporary_password = generate_temporary_password()
    user.set_password(temporary_password)
    user.must_change_password = True
    user.save(update_fields=["password", "must_change_password"])
    _revoke_all_refresh_tokens(user)

    role = None
    if user.user_roles.filter(is_active=True).exists():
        role = (
            user.user_roles.filter(is_active=True, role__is_active=True)
            .select_related("role")
            .order_by("role__layer")
            .first()
        )
        role = role.role if role else None

    sent = send_account_created_email(
        user=user,
        temporary_password=temporary_password,
        employee=employee,
        role=role,
        actor=actor,
    )

    _audit(
        user,
        verb="update",
        event=event if sent else failed_event,
        actor=actor,
        recipient=recipient or user.email,
    )
    return sent
