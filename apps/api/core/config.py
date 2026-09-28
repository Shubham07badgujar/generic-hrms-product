"""
Per-organization configuration, resolved explicitly.

`settings.X` stops being THE value and becomes the DEFAULT. Each concern gets a
typed object, resolved from the organization's own row when it has one and from
the deployment's settings when it does not -- which keeps a self-hosted
single-company installation on exactly the same code path as a SaaS tenant,
with an empty configuration table and no branch anywhere saying "if multi-
tenant".

TWO RULES THAT CARRY THE DESIGN

**Every resolver takes an explicit `organization`.** There is deliberately no
`current_email_config()` convenience reading ambient state. That shape -- a
value that must be SET before it is READ -- is precisely
HRMS-INC-20260717-01, and the failure mode of forgetting it is silent: the
wrong customer's SMTP server, or the wrong customer's biometric credentials.
With an explicit argument a forgotten call site is a `TypeError` at import or
first call, not a message delivered through somebody else's mail server.

**Fallback is to `settings`, NEVER to another organization.** Every lookup here
is keyed on one organization id and falls through to the deployment default.
There is no "borrow the other tenant's value" path, and no ordering in which
one could appear.

SECRETS

Stored in `EncryptedCharField`, exposed `write_only`, and read back only as
`has_password: bool`. No masked value, because a mask still leaks the length
and the shape; no reveal endpoint, because the reason to reveal a password is
always better served by setting a new one. An operator who needs the value has
the customer's own credential, not ours.
"""

from __future__ import annotations

from contextlib import contextmanager

from dataclasses import dataclass

from django.conf import settings



@contextmanager
def _organization_bound(organization_id):
    """
    Bind one organization for the length of a read, then put back what was
    there. Used by the org-configuration lookups above, which are given the
    organization explicitly and are called from places that have none bound.
    """
    from core.middleware import _current_org, set_current_org_id

    token = set_current_org_id(organization_id)
    try:
        yield
    finally:
        _current_org.reset(token)


def _organization_id(organization):
    """Accept an Organization or its id, so callers pass whichever they hold."""
    if organization is None:
        return None
    return getattr(organization, "pk", organization)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmailConfig:
    """How one organization's outbound mail is sent, and as whom."""

    host: str
    port: int
    use_tls: bool
    username: str
    password: str
    from_email: str
    #: Where a confused recipient should reply. Shown in message bodies, so it
    #: is the address a human reads rather than the one SMTP authenticates as.
    hr_contact: str
    #: True when this came from the organization's own row rather than the
    #: deployment defaults. Surfaced so a settings screen can say "using the
    #: platform's mail server" instead of showing blank fields that look
    #: broken.
    is_organization_specific: bool = False

    def connection(self):
        """
        A Django mail connection for this organization, or None for the default.

        None means "use whatever the deployment configured", which is what a
        single-company installation wants and what a tenant that has not
        configured mail yet falls back to. Returning None rather than building
        a connection from the defaults keeps one code path: `send_mail` already
        treats `connection=None` that way.
        """
        if not (self.host and self.username):
            return None
        from django.core import mail

        return mail.get_connection(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            use_tls=self.use_tls,
        )


def email_config(organization) -> EmailConfig:
    """
    This organization's mail configuration, falling back to the deployment's.

    Field by field rather than all-or-nothing: an organization that sets only
    its own from-address, and leaves the SMTP server to the platform, gets
    exactly that. Requiring them to restate the whole block to change one line
    is how configuration screens end up with copied-in values that go stale.
    """
    from apps.organization.models import OrgEmailConfig

    row = None
    organization_id = _organization_id(organization)
    if organization_id is not None:
        # Plain filter, deliberately. `OrgEmailConfig` carries an organization
        # but inherits `BaseModel`, so its manager never applies the tenant
        # predicate and there is nothing here to step around -- unlike
        # `OrgAttendanceIntegration` below, which is organization-owned and
        # needs `all_orgs()` to get past a predicate that would otherwise
        # refuse a resolver whose whole contract is that the CALLER names the
        # organization.
        #
        # Bound for the read. `OrgEmailConfig` is one of the tables release 2
        # put row-level security on, and this resolver is called from mail
        # paths that often have nothing in force -- a dispatcher's subtask, a
        # command, provisioning's invitation. Unbound the row is invisible and
        # every `pick()` below silently takes the DEPLOYMENT default, so the
        # customer's mail would leave with the platform's identity instead of
        # their own. Silent, and wrong in the one direction that matters.
        with _organization_bound(organization_id):
            row = OrgEmailConfig.objects.filter(
                organization_id=organization_id, is_active=True
            ).first()

    def pick(attr, default):
        value = getattr(row, attr, None) if row else None
        if value in (None, ""):
            return default
        return value

    return EmailConfig(
        host=pick("host", settings.EMAIL_HOST),
        port=pick("port", settings.EMAIL_PORT),
        use_tls=(
            row.use_tls if row and row.host else settings.EMAIL_USE_TLS
        ),
        username=pick("username", settings.HR_EMAIL_HOST_USER),
        password=pick("password", settings.HR_EMAIL_HOST_PASSWORD),
        from_email=pick(
            "from_email", settings.HR_FROM_EMAIL or settings.DEFAULT_FROM_EMAIL
        ),
        hr_contact=pick("hr_contact", settings.HR_CONTACT_EMAIL),
        is_organization_specific=bool(row and row.host),
    )


# ---------------------------------------------------------------------------
# Message wording
# ---------------------------------------------------------------------------


def _one_line(value: str) -> str:
    """
    A subject line, flattened.

    A template file ends with a newline and an author may press Return in a
    subject box; either becomes a header-injection attempt the moment it
    reaches SMTP. Collapsing whitespace here is cheaper than remembering to at
    every send site.
    """
    return " ".join(value.split())


@dataclass(frozen=True)
class RenderedMessage:
    """One outbound message, already rendered for one organization."""

    subject: str
    text: str
    html: str
    #: True when the wording came from the organization's own row rather than
    #: the template this product ships. Surfaced so an editing screen can say
    #: "using the standard wording" instead of showing an empty box.
    is_organization_specific: bool = False


def render_message(organization, key: str, context: dict) -> RenderedMessage:
    """
    Render one message as one organization would word it.

    `key` is the shipped template's path without its extension --
    "recruitment/email/offer_sent" -- so the template path IS the identifier
    and there is no second registry of message names to keep in step with the
    files.

    Resolution is the same shape as every other resolver here: the
    organization's own override if it has one, the shipped template otherwise,
    and NEVER another organization's wording. An override supplying no HTML
    still gets the shipped HTML rendered with its own context, which reads
    better than nothing and better than HTML contradicting the text beside it.

    The context is built by the sending service and holds plain strings, so an
    organization authoring a template reaches its own message's facts and
    nothing behind them.
    """
    from django.template import Context, Template
    from django.template.loader import render_to_string

    row = None
    organization_id = _organization_id(organization)
    if organization_id is not None:
        from apps.organization.models import OrgEmailTemplate

        # Bound for the read: this is called from mail-sending paths that run
        # with nothing in force (a Celery task's fan-out, a management
        # command), and from release 2 the database shows a confined role only
        # the bound organization's rows. The organization is an argument here,
        # so saying so costs nothing and removes the dependency on ambient
        # state entirely.
        with _organization_bound(organization_id):
            row = (
                OrgEmailTemplate.objects.all_orgs()
                .filter(organization_id=organization_id, key=key, is_active=True)
                .first()
            )

    def shipped(suffix: str) -> str:
        return render_to_string(f"{key}{suffix}", context)

    if row is None:
        return RenderedMessage(
            subject=_one_line(shipped(".subject.txt")),
            text=shipped(".txt"),
            html=shipped(".html"),
        )

    def authored(source: str) -> str:
        return Template(source).render(Context(context))

    return RenderedMessage(
        subject=_one_line(authored(row.subject)),
        text=authored(row.body_text),
        html=authored(row.body_html) if row.body_html else shipped(".html"),
        is_organization_specific=True,
    )


# ---------------------------------------------------------------------------
# Attendance devices
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceConfig:
    """One organization's biometric endpoint and the credentials for it."""

    base_url: str
    username: str
    password: str
    is_organization_specific: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.username)


def attendance_config(organization) -> AttendanceConfig:
    """
    This organization's biometric integration.

    Separate from `email_config` rather than one big config object, because
    they sit behind DIFFERENT PERMISSIONS: device credentials are
    `ATTENDANCE_DEVICE` and mail settings are `ORG_SETTINGS`. One table would
    have forced one permission, and an HR manager who may configure the
    attendance clock would thereby read the mail password.

    The brief's rule about these credentials in particular: never copy one
    company's biometric credentials into another organization, and never expose
    them to ordinary employees. The first is structural -- this reads one
    organization id and falls back only to the deployment default. The second
    is the permission, plus `write_only` on the serializer.
    """
    from apps.attendance.models import OrgAttendanceIntegration

    row = None
    organization_id = _organization_id(organization)
    if organization_id is not None:
        with _organization_bound(organization_id):
            row = (
                OrgAttendanceIntegration.objects.all_orgs()
                .filter(organization_id=organization_id, is_active=True)
                .first()
            )

    def pick(attr, default):
        value = getattr(row, attr, None) if row else None
        return default if value in (None, "") else value

    return AttendanceConfig(
        base_url=pick("base_url", settings.ESSL_BASE_URL),
        username=pick("username", settings.ESSL_USERNAME),
        password=pick("password", settings.ESSL_PASSWORD),
        is_organization_specific=bool(row and row.base_url),
    )
