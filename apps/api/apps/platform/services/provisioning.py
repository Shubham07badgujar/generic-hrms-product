"""
Creating a customer organization: one transaction, or nothing at all.

A half-provisioned tenant is the worst outcome available here. It has a row in
the organizations table, so the slug is taken and a retry collides; it may have
roles but no administrator, or an administrator with no membership, who
therefore resolves to DENY_ALL and cannot use the system they were created to
run. None of that announces itself -- the operator sees a green screen and the
customer sees a login that refuses them. So the whole of it is one
`@transaction.atomic`, and a failure at the last step removes the first.

ORDER, AND WHY THIS ORDER

    validate everything          <- before the first write
    Organization
    OrgSettings
    configuration seeds          <- roles first; everything else needs them
    Organization Admin           <- user + membership + role grant, together
    audit
    (commit)
    invitation email             <- on_commit, so it cannot announce a login
                                    that got rolled back

Validation is entirely ahead of the first write on purpose. Everything it
checks -- a free slug, an unused email -- is cheap to ask and expensive to
discover halfway through: the transaction would roll back correctly, but the
operator would be told "provisioning failed" for a reason they could have been
told before anything was attempted.

WHAT THIS IS NOT DOING YET

Nothing. The subscription seam left here in Stage 3 is filled: a new
organization starts on a trial of the cheapest public plan, or on no
subscription at all where the deployment sells none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils.text import slugify

from core.middleware import acting_as

logger = logging.getLogger("hrms.platform")


class ProvisioningError(Exception):
    """A refusal the operator can act on, raised before anything was written."""


@dataclass
class ProvisionResult:
    organization: object
    admin: object
    #: Returned to the caller and nowhere else -- not audited, not logged, not
    #: stored. Same rule the employee-credentials path already follows: if the
    #: invitation mail is lost the remedy is a reset, never a lookup.
    temporary_password: str
    #: Whether the invitation actually went out. Reported rather than raised: a
    #: mail failure must not undo a correct provisioning, and nobody should be
    #: told credentials were sent when they were not.
    invitation_sent: bool = False
    #: None on a deployment with no plans -- see `_start_subscription`.
    subscription: object = None
    seeded: dict = field(default_factory=dict)


def _seed_roles(organization):
    from apps.accounts.services.roles import seed_roles

    result = seed_roles(organization=organization)
    return f"{result}"


def _seed_leave(organization):
    from apps.leave.seeds import seed_leave

    return seed_leave()


def _seed_onboarding(organization):
    from apps.onboarding.seeds import seed_all

    return seed_all()


def _seed_workflows(organization):
    from apps.workflows.seeds import seed_workflows

    return f"{len(seed_workflows())} workflow(s)"


def _seed_offboarding(organization):
    from apps.offboarding.seeds import seed_all

    return seed_all()


def _seed_attendance(organization):
    from apps.attendance.seeds import seed_shift_rules

    return f"{seed_shift_rules()} shift rule(s)"


#: Configuration every new organization starts with, in dependency order.
#:
#: THIS LIST IS THE ONE COPY. It used to live inside `seed_all.py`, which is a
#: developer convenience command; a customer created through this service would
#: then have silently lacked whatever somebody added there and not here. That
#: command now imports this list, so the demo system and a real customer are
#: seeded from the same statement of what an organization needs.
#:
#: Roles first, unconditionally: everything below needs an actor who holds one,
#: and `seed_roles` is the only one of these that takes the organization
#: explicitly, because its `update_or_create` matches on a code that is unique
#: only per organization.
#:
#: `seed_statutory` is deliberately ABSENT. India's PF, ESI and Professional Tax
#: tables are facts about the Republic of India, not about a customer;
#: duplicating them per organization would mean N copies to verify and would
#: defeat the four-eyes certification the statutory engine is built around.
#: They are seeded once per deployment.
CONFIG_SEEDS = [
    ("roles", "roles and the permission matrix", _seed_roles),
    ("leave", "leave types, policies and a holiday calendar", _seed_leave),
    ("onboarding", "document types, checklist items, letter templates", _seed_onboarding),
    ("workflows", "hiring workflows", _seed_workflows),
    ("offboarding", "exit clearance template", _seed_offboarding),
    ("attendance", "shift rules", _seed_attendance),
]


def _validate(*, name, slug, admin_email, actor):
    """Everything that can be refused, refused before the first write."""
    from apps.accounts.models import User
    from apps.organization.models import Organization

    if not name or not name.strip():
        raise ProvisioningError("An organization needs a name.")

    slug = slugify(slug or name)
    if not slug:
        raise ProvisioningError(
            f"{name!r} does not reduce to a usable slug. Pass one explicitly."
        )
    if Organization.objects.filter(slug=slug).exists():
        raise ProvisioningError(f"The slug {slug!r} is already taken.")

    admin_email = User.objects.normalize_email(admin_email or "")
    if not admin_email:
        raise ProvisioningError("An organization needs an administrator's email.")
    if User.objects.filter(email=admin_email).exists():
        # The V1 identity model: one login belongs to one organization, and
        # `User.email` is the USERNAME_FIELD, so it is unique platform-wide.
        # This is the cost of that decision, and it belongs in a clear message
        # here rather than in a support ticket about a confusing 500.
        raise ProvisioningError(
            f"{admin_email} already has an account on this platform. A login "
            f"belongs to exactly one organization, so this address cannot "
            f"administer a second one. Use a different address."
        )

    if actor is not None and not getattr(actor, "is_platform_admin", False):
        raise ProvisioningError(
            "Only a platform administrator provisions organizations."
        )

    return slug, admin_email


def _start_subscription(organization, *, plan=None, actor=None):
    """
    The trial this organization starts on.

    Returns None when no plan is named and none is marked default -- a
    self-hosted single-company deployment has no plans at all, and provisioning
    must not require a commercial concept that installation never bought.
    Everything downstream treats "no subscription" as unlimited.
    """
    from apps.platform.models import Plan
    from apps.platform.services.subscriptions import start_subscription

    if plan is None:
        plan = (
            Plan.objects.filter(is_active=True, is_public=True)
            .order_by("display_order", "name")
            .first()
        )
    if plan is None:
        return None
    return start_subscription(organization, plan=plan, actor=actor)


@transaction.atomic
def provision_organization(
    *,
    name: str,
    admin_email: str,
    slug: str = "",
    legal_name: str = "",
    admin_first_name: str = "",
    admin_last_name: str = "",
    primary_email: str = "",
    phone: str = "",
    city: str = "",
    state: str = "",
    #: An ISO 3166-1 alpha-2 code, not a country name -- the column is two
    #: characters wide. Empty keeps the model default rather than
    #: overriding it with a guess.
    country: str = "",
    timezone_name: str = "",
    currency: str = "",
    #: The plan to start on. Omitted, the cheapest public plan is used, or
    #: none at all where the deployment sells nothing.
    plan=None,
    actor=None,
) -> ProvisionResult:
    """
    Create a customer organization and its first administrator.

    Returns the organization, the administrator, and the temporary password.
    Sending the invitation is scheduled for after commit and its outcome is
    reported on the result, never raised.
    """
    from apps.accounts.models import Role, User, UserRole
    from apps.accounts.services.passwords import (
        generate_temporary_password,
        send_account_created_email,
    )
    from apps.audit.events import record_event
    from apps.organization.models import (
        Organization,
        OrganizationMembership,
        OrgSettings,
        OrgStatus,
    )
    from core.access.catalog import RoleCode

    slug, admin_email = _validate(
        name=name, slug=slug, admin_email=admin_email, actor=actor
    )

    organization = Organization.objects.create(
        name=name.strip(),
        legal_name=legal_name.strip(),
        slug=slug,
        # PENDING_SETUP is a WORKING state, not a locked one: the administrator
        # is about to walk the setup wizard and must be able to read and write
        # in order to finish it. Only finishing it makes the organization
        # ACTIVE.
        status=OrgStatus.PENDING_SETUP,
        primary_email=primary_email.strip(),
        phone=phone.strip(),
        city=city.strip(),
        state=state.strip(),
        **({"country": country.strip().upper()} if country.strip() else {}),
        **({"timezone": timezone_name} if timezone_name else {}),
        **({"currency": currency} if currency else {}),
    )

    result = ProvisionResult(
        organization=organization, admin=None, temporary_password=""
    )

    # Everything below is org-owned, and org-owned rows take their organization
    # from the acting context. Binding it once here rather than threading an
    # argument through six seed functions is deliberate: the write path stamps
    # from bound context on every other table in the product, an UNBOUND call
    # raises `OrgContextMissing` rather than writing a loose row, and the seeds
    # would only be passing the argument along to rows that get stamped anyway.
    # The explicit-argument rule this project applies to config RESOLVERS is
    # about reads, where a forgotten call site is a silent cross-tenant read;
    # here a forgotten binding is a loud exception.
    with acting_as(actor, organization=organization):
        OrgSettings.for_org(organization)
        result.subscription = _start_subscription(
            organization, plan=plan, actor=actor
        )

        for key, _description, seed in CONFIG_SEEDS:
            result.seeded[key] = seed(organization)

        admin_role = Role.objects.filter(
            organization=organization, code=RoleCode.ADMIN, is_active=True
        ).first()
        if admin_role is None:
            # Unreachable unless `seed_roles` stopped producing an Admin, and
            # loud rather than silent because the alternative is an
            # organization whose administrator cannot administer it.
            raise ProvisioningError(
                "Seeding produced no Admin role for this organization."
            )

        temporary_password = generate_temporary_password()
        admin = User.objects.create_user(
            email=admin_email,
            password=temporary_password,
            first_name=admin_first_name.strip(),
            last_name=admin_last_name.strip(),
        )
        admin.must_change_password = True
        admin.save(update_fields=["must_change_password"])

        UserRole.objects.create(user=admin, role=admin_role, assigned_by=actor)
        # Without the membership the administrator resolves to DENY_ALL:
        # tenant identity comes from a membership and nowhere else. In the same
        # transaction as the account and the grant, so provisioning yields a
        # usable administrator or nothing.
        OrganizationMembership.objects.create(organization=organization, user=admin)

        record_event(
            organization,
            actor=actor,
            entity_type="organization.Organization",
            verb="create",
            resource="",
            after={
                "name": organization.name,
                "slug": organization.slug,
                "status": organization.status,
                "admin_email": admin.email,
                "seeded": sorted(result.seeded),
            },
        )

    result.admin = admin
    result.temporary_password = temporary_password

    def _invite():
        # After commit, so a rolled-back provisioning never announces
        # credentials for a login that does not exist.
        try:
            result.invitation_sent = send_account_created_email(
                user=admin, temporary_password=temporary_password, actor=actor
            )
        except Exception:  # noqa: BLE001 -- mail must not undo a good write
            logger.exception("platform.invitation_failed org=%s", organization.slug)
            result.invitation_sent = False

    transaction.on_commit(_invite)
    return result
