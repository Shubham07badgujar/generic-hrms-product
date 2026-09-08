"""
One-time creation of the founding Admin account.

The single hardest problem in a fresh RBAC system: someone must create the first
privileged user, but there is no privileged user yet to authorise it. This is
solved with the narrowest possible mechanism, used exactly once per deployment.

The account is created with NO usable password. The password is set out of band
(directly in the database, per the approved plan) so that even a leaked bootstrap
token yields an account nobody can log into — the token grants "create the admin
shell", never "obtain admin access".
"""

from __future__ import annotations

from django.db import transaction

from apps.accounts.models import Role, User, UserRole
from core.access.catalog import RoleCode


class BootstrapError(Exception):
    pass


def _bootstrap_organization():
    """
    The organization the founding Admin will administer.

    Bootstrap is the other half of the chicken-and-egg this module exists for:
    a principal's tenant comes from a membership, but at bootstrap time there
    may be no organization to be a member of. So one is adopted or created --
    and this is the ONLY place in the product where an organization comes into
    being without an authenticated creator, for the same reason the Admin does.

    Adopts the sole existing organization when there is exactly one, which is
    what a self-hosted single-company deployment has. Refuses to choose when
    there are several: on a multi-tenant platform the founding operator is a
    platform concern, and silently attaching them to whichever customer
    happened to be created first would be precisely the cross-tenant guess this
    architecture forbids. Stage 3's provisioning flow replaces this path.
    """
    from apps.organization.models import Organization, OrgStatus

    existing = list(Organization.objects.all()[:2])
    if len(existing) == 1:
        return existing[0]
    if existing:
        raise BootstrapError(
            "Several organizations exist, so there is no single one to attach "
            "the founding Admin to. Create the account through the platform "
            "provisioning flow instead."
        )

    from django.conf import settings
    from django.utils.text import slugify

    name = settings.ORG_DISPLAY_NAME or "Organization"
    return Organization.objects.create(
        name=name, slug=slugify(name) or "organization", status=OrgStatus.ACTIVE
    )


@transaction.atomic
def create_admin(
    *,
    email: str,
    first_name: str = "",
    last_name: str = "",
    actor=None,
    organization=None,
) -> User:
    """
    Create the founding Admin, or refuse if one already exists.

    One-shot by design: once any active Admin exists, this raises. There is no
    "create a second admin" path here — subsequent admins are granted through
    the normal, audited user-management flow by the first one.
    """
    admin_role = Role.objects.filter(code=RoleCode.ADMIN, is_active=True).first()
    if admin_role is None:
        raise BootstrapError(
            "The Admin role does not exist. Run `manage.py seed_roles` first."
        )

    if UserRole.objects.filter(
        role=admin_role, is_active=True, user__is_active=True
    ).exists():
        raise BootstrapError(
            "An active Admin already exists. Bootstrap is one-time; grant further "
            "admins through the normal user-management flow."
        )

    email = User.objects.normalize_email(email)
    if User.objects.filter(email=email).exists():
        raise BootstrapError(f"A user with email {email!r} already exists.")

    user = User.objects.create_user(
        email=email,
        password=None,  # unusable; set out of band
        first_name=first_name,
        last_name=last_name,
        is_staff=True,  # Django-admin access for the founding operator
    )
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])

    UserRole.objects.create(user=user, role=admin_role, assigned_by=actor)

    # Without this the founding Admin resolves to DENY_ALL and cannot use the
    # system they were created to administer: tenant identity comes from a
    # membership and nowhere else. Same transaction as the account and the role
    # grant, so bootstrap either produces a usable Admin or nothing at all.
    from apps.organization.models import OrganizationMembership

    OrganizationMembership.objects.create(
        organization=organization or _bootstrap_organization(), user=user
    )

    _audit_bootstrap(user, actor=actor)
    return user


def _audit_bootstrap(user, *, actor) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "system",
        action=AuditAction.ROLE_CHANGE,
        resource="user",
        entity_type="accounts.User",
        entity_id=str(user.pk),
        entity_label=user.email,
        after={"event": "admin_bootstrap", "email": user.email},
        request_id=get_request_id(),
    )
