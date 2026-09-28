"""
Whose mail server, whose wording, whose reply-to — and whose retry.

Sender identity was settled in the per-organization configuration slice. What
this file covers is the rest of the send path: the wording an organization may
override without touching anyone else's, the row a delivery is recorded
against, and the rule that a message sent later — on a worker, after a retry —
still goes out as the company whose record it belongs to rather than as
whoever happens to be signed in at the time.

The one place the two security domains legitimately touch is here too:
platform-originated mail, which is sent before a tenant has any mail
configuration of its own, goes through the deployment's own server.
"""

from __future__ import annotations

import pytest

from core.config import email_config, render_message
from core.middleware import acting_as

pytestmark = pytest.mark.django_db

#: A kind that exists as a shipped template, so the fallback half is real.
KEY = "recruitment/email/final_selection"


def _context() -> dict:
    return {
        "candidate_name": "Priya Menon",
        "job_title": "Staff Engineer",
        "application_id": "APP-0000ABCD",
        "applied_on": "01 Jan 2026",
        "current_status": "Selected",
        "current_stage": "Offer",
        "company_name": "Acme Health",
        "hr_contact_email": "hr@acme-health.example",
        "next_step": "",
    }


def notify_one(world):
    from apps.notifications.services import notify

    return notify(
        recipient=world.admin, kind="payroll_processed", title="Ready for review"
    )


def _override(world, *, subject: str, body: str = "Body for {{ candidate_name }}."):
    from apps.organization.models import OrgEmailTemplate

    with acting_as(None, organization=world.organization):
        return OrgEmailTemplate.objects.create(
            key=KEY, subject=subject, body_text=body
        )


# ------------------------------------------------------------------ wording


def test_an_organization_without_an_override_gets_the_shipped_wording(org_a):
    message = render_message(org_a.organization, KEY, _context())

    assert message.subject
    assert message.text
    assert message.is_organization_specific is False


def test_an_override_replaces_the_wording_for_that_organization_only(org_a, org_b):
    _override(org_a, subject="Welcome aboard at Acme")

    mine = render_message(org_a.organization, KEY, _context())
    theirs = render_message(org_b.organization, KEY, _context())

    assert mine.subject == "Welcome aboard at Acme"
    assert mine.is_organization_specific is True

    assert theirs.subject != mine.subject
    assert theirs.is_organization_specific is False, (
        "one customer's wording reached another customer's message"
    )


def test_two_organizations_may_hold_an_override_for_the_same_message(org_a, org_b):
    """
    The composite key doing its job.

    A globally unique template key would mean the second customer to write
    their own rejection email could not have one.
    """
    _override(org_a, subject="A's wording")
    _override(org_b, subject="B's wording")

    assert render_message(org_a.organization, KEY, _context()).subject == "A's wording"
    assert render_message(org_b.organization, KEY, _context()).subject == "B's wording"


def test_an_override_is_rendered_with_the_message_context(org_a):
    _override(org_a, subject="Offer for {{ candidate_name }}")

    message = render_message(org_a.organization, KEY, _context())
    assert message.subject == "Offer for Priya Menon"


def test_an_override_without_html_still_gets_html(org_a):
    """Plain text alone must not produce an email with an empty HTML part."""
    message = render_message(org_a.organization, KEY, _context())
    shipped_html = message.html

    _override(org_a, subject="Plain only")
    overridden = render_message(org_a.organization, KEY, _context())

    assert overridden.html == shipped_html


def test_a_subject_cannot_carry_a_line_break(org_a):
    """
    A newline in a subject is a header-injection attempt once it reaches SMTP.

    Worth asserting on the override path specifically: the shipped templates
    are reviewed, and an organization's own subject box is not.
    """
    _override(org_a, subject="Congratulations\nBcc: someone@elsewhere.example")

    message = render_message(org_a.organization, KEY, _context())
    assert "\n" not in message.subject
    assert "\r" not in message.subject


def test_no_organization_renders_the_shipped_wording(org_a):
    """
    A resolver called with no organization falls back to the product, never
    to whichever customer happens to have written an override.
    """
    _override(org_a, subject="A's wording")

    message = render_message(None, KEY, _context())
    assert message.subject != "A's wording"
    assert message.is_organization_specific is False


# ------------------------------------------------------ candidate mail path


def test_a_candidate_email_is_worded_by_the_application_s_own_organization(
    org_a, org_b
):
    """
    The wording follows the record, not the session.

    `render()` used to resolve its organization from ambient context, which
    meant a send running on a worker had none and a send running under the
    wrong context had the wrong one.
    """
    from apps.recruitment.services.communications import render

    _override(org_a, subject="A's wording")

    subject_a, _, _ = render("final_selection", _context(), organization=org_a.organization)
    subject_b, _, _ = render("final_selection", _context(), organization=org_b.organization)

    assert subject_a == "A's wording"
    assert subject_b != subject_a


def test_the_organization_of_a_candidate_email_comes_from_the_row(org_a):
    """
    Not from ambient context — the property that makes a retry correct.

    Asserted by resolving with NOTHING bound, which is the state a Celery
    worker is in.
    """
    from apps.recruitment.services.communications import _organization_for

    application = org_a.rows["application"]
    with acting_as(None, organization=None):
        resolved = _organization_for(application)

    assert resolved is not None
    assert resolved.pk == org_a.organization.pk


def test_a_candidate_send_with_nothing_bound_still_leaves_as_the_right_company(
    org_a, org_b, settings
):
    """
    The retry property, asserted in the state a worker is actually in.

    `_deliver` is reached from `on_commit` and from HR's retry button, and one
    day from a queue. With no organization bound it must still resolve this
    notification's own mail configuration -- not the deployment's, and not
    whichever tenant was configured first.
    """
    from django.core import mail

    from apps.organization.models import OrgEmailConfig
    from apps.recruitment.models import CandidateNotification
    from apps.recruitment.services.communications import _deliver

    settings.HR_FROM_EMAIL = "noreply@platform.example"

    with acting_as(None, organization=org_a.organization):
        OrgEmailConfig.objects.create(
            organization=org_a.organization,
            host="smtp.acme.example",
            port=587,
            username="acme",
            password="secret",
            from_email="people@acme-health.example",
            hr_contact="hr@acme-health.example",
        )
        application = org_a.rows["application"]
        row = CandidateNotification.objects.create(
            application=application,
            candidate=application.candidate,
            job_opening=application.job_opening,
            kind="final_selection",
            recipient_email="priya@example.test",
            subject="Congratulations",
            body_text="You have been selected.",
            body_html="<p>You have been selected.</p>",
            dedupe_key=f"retry-test:{application.pk}",
        )

    from tests.conftest import across_organizations

    mail.outbox.clear()
    # Fetched the way a deployment-wide sweep does, then delivered with
    # NOTHING bound -- which is the case under test: the message must leave as
    # the notification's own company, not as whatever happens to be in force.
    with across_organizations():
        pending = CandidateNotification.objects.all_orgs().get(pk=row.pk)
    with acting_as(None, organization=None):
        _deliver(pending)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.from_email == "people@acme-health.example"
    assert sent.reply_to == ["hr@acme-health.example"]


# ------------------------------------------------------------- delivery log


def test_a_delivery_row_belongs_to_the_notification_it_delivers(org_a):
    from apps.notifications.models import NotificationDelivery
    from apps.notifications.services import notify

    with acting_as(org_a.admin, organization=org_a.organization):
        notification = notify(
            recipient=org_a.admin, kind="payroll_processed", title="Ready for review"
        )

    from tests.conftest import across_organizations

    with across_organizations():
        deliveries = list(
            NotificationDelivery.objects.all_orgs().filter(notification=notification)
        )
    assert deliveries
    for delivery in deliveries:
        assert delivery.organization_id == org_a.organization.pk


def test_a_delivery_cannot_be_stamped_with_a_different_organization(org_a, org_b):
    """
    `org_source` refusing drift, on this table specifically.

    A delivery row stamped with the acting organization rather than the
    notification's would make one customer's send appear in another's log.
    """
    from apps.notifications.models import NotificationDelivery
    from apps.notifications.services import notify

    with acting_as(org_a.admin, organization=org_a.organization):
        notification = notify(
            recipient=org_a.admin, kind="payroll_processed", title="Ready for review"
        )

    from tests.conftest import across_organizations

    with across_organizations():
        delivery = NotificationDelivery.objects.all_orgs().filter(
            notification=notification
        ).first()
    with acting_as(org_b.admin, organization=org_b.organization):
        rebuilt = NotificationDelivery(
            notification=notification,
            channel="email",
            organization_id=org_b.organization.pk,
        )
        with pytest.raises(Exception) as raised:
            rebuilt.save()

    assert "organization" in str(raised.value).lower()
    assert delivery.organization_id == org_a.organization.pk


def test_a_notification_email_is_sent_as_its_own_organization(org_a, org_b):
    """
    The transport reads the ROW's organization, not the acting context.

    This is the retry shape for the in-app/email channel: a send attempted
    later must still leave as the company the notice belongs to.
    """
    from apps.notifications.models import Notification
    from apps.notifications.transports import EmailTransport
    from apps.organization.models import OrgEmailConfig

    with acting_as(None, organization=org_a.organization):
        OrgEmailConfig.objects.create(
            organization=org_a.organization,
            host="smtp.acme.example",
            port=587,
            username="acme",
            password="secret",
            from_email="people@acme-health.example",
        )

    with acting_as(org_a.admin, organization=org_a.organization):
        notification = notify_one(org_a)

    from django.core import mail

    mail.outbox.clear()
    # Deliberately sent while the OTHER organization is bound.
    from tests.conftest import across_organizations

    with across_organizations():
        to_send = Notification.objects.all_orgs().get(pk=notification.pk)
    with acting_as(org_b.admin, organization=org_b.organization):
        EmailTransport().send(to_send)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].from_email == "people@acme-health.example"



# ------------------------------------------------------ the platform's mail


def test_platform_mail_uses_the_deployment_s_own_server(settings):
    """
    A brand-new organization has no mail settings, so the invitation that
    announces its existence cannot be sent as it.

    Stated as a test because it is the one place the platform and organization
    domains legitimately share a mechanism, and "no organization" must resolve
    to the deployment rather than to whichever tenant was configured first.
    """
    settings.EMAIL_HOST = "smtp.platform.example"
    settings.HR_EMAIL_HOST_USER = "platform"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.example"

    config = email_config(None)

    assert config.host == "smtp.platform.example"
    assert config.is_organization_specific is False


def test_a_tenant_without_mail_settings_falls_back_to_the_platform(org_a, settings):
    settings.EMAIL_HOST = "smtp.platform.example"
    settings.HR_EMAIL_HOST_USER = "platform"

    config = email_config(org_a.organization)

    assert config.host == "smtp.platform.example"
    assert config.is_organization_specific is False


def test_a_resolved_connection_still_carries_the_socket_timeout(org_a, settings):
    """
    Per-organization mail must not reintroduce the hang it was built after.

    `tests/core/test_outbound_mail_is_bounded.py` pins that Django's SMTP
    backend falls back to `EMAIL_TIMEOUT` when a caller passes none. This pins
    that OUR builder is such a caller — it is now the only place in the
    product that constructs a mail connection, so if it ever starts passing a
    timeout of its own that setting stops being the guarantee it claims to be.
    """
    from apps.organization.models import OrgEmailConfig

    # The suite runs on the locmem backend, which has no socket and therefore
    # no timeout to inspect. Pointing at the real SMTP backend for this one
    # assertion is what makes it about production behaviour rather than about
    # the test harness.
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

    with acting_as(None, organization=org_a.organization):
        OrgEmailConfig.objects.create(
            organization=org_a.organization,
            host="smtp.acme.example",
            port=587,
            username="acme",
            password="secret",
        )

    connection = email_config(org_a.organization).connection()
    assert connection is not None
    assert connection.timeout == settings.EMAIL_TIMEOUT


def test_a_tenant_never_falls_back_to_another_tenant(org_a, org_b, settings):
    """The rule that has no ordering in which it could be violated."""
    from apps.organization.models import OrgEmailConfig

    settings.EMAIL_HOST = "smtp.platform.example"
    settings.HR_EMAIL_HOST_USER = "platform"

    with acting_as(None, organization=org_a.organization):
        OrgEmailConfig.objects.create(
            organization=org_a.organization,
            host="smtp.acme.example",
            port=587,
            username="acme",
            password="secret",
            from_email="people@acme-health.example",
        )

    theirs = email_config(org_b.organization)
    assert theirs.host == "smtp.platform.example"
    assert theirs.from_email != "people@acme-health.example"
