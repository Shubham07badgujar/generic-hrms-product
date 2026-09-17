"""
Who belongs to which organization.

`OrganizationMembership` is the sole source of tenant identity, so "is this
user in this organization?" is asked from several unrelated places: permission
resolution, welcome mail, and notification addressing. Each of those had its
own copy of the query, which is how a tenant-identity predicate drifts — one
copy remembers `is_active`, the next remembers only `status`, and the looser
one becomes the hole.

So: one definition here, imported by all of them. The predicate is ACTIVE
status AND the row not soft-deleted, and it is written down exactly once.
"""

from __future__ import annotations


def _organization_id(organization):
    """Accept an Organization or its id, so callers pass whichever they hold."""
    if organization is None:
        return None
    return getattr(organization, "pk", organization)


def active_membership(user):
    """
    The one membership that decides this principal's organization.

    One query, and the `uniq_one_active_membership` constraint is what makes
    `.first()` unambiguous rather than arbitrary.
    """
    from .models import MembershipStatus, OrganizationMembership

    user_id = getattr(user, "pk", None)
    if user_id is None:
        return None
    return (
        OrganizationMembership.objects.filter(
            user_id=user_id, status=MembershipStatus.ACTIVE, is_active=True
        )
        .select_related("organization")
        .first()
    )


def organization_of(user):
    """The organization a principal belongs to, or None."""
    membership = active_membership(user)
    return membership.organization if membership else None


def role_grants(user):
    """
    This principal's active role grants, in their OWN organization.

    Read through the membership rather than the bound organization, because
    the places that ask are exactly the places where nothing is bound yet: the
    admin sign-in door checks roles before a session exists, and `/me/` is
    `access_exempt`, so no permission check binds one either. A user's roles
    are a fact about the user's organization, and the membership already says
    which that is -- so the answer does not depend on whatever happens to be
    bound, including another organization's.

    No membership, no grants: an empty queryset, never every grant this user
    holds anywhere.
    """
    from apps.accounts.models import UserRole

    grants = UserRole.objects.all_orgs().filter(is_active=True, role__is_active=True)
    membership = active_membership(user)
    if membership is None:
        return grants.none()
    return grants.filter(user_id=user.pk, organization_id=membership.organization_id)


def is_member(user, organization) -> bool:
    """
    Whether this principal is an active member of THIS organization.

    The addressing guard. Note it answers False for a `None` organization
    rather than raising or waving the check through: an unbound caller is
    exactly the state in which a recipient cannot be shown to be in scope, so
    the honest answer is no.
    """
    organization_id = _organization_id(organization)
    user_id = getattr(user, "pk", None)
    if organization_id is None or user_id is None:
        return False

    from .models import MembershipStatus, OrganizationMembership

    return OrganizationMembership.objects.filter(
        user_id=user_id,
        organization_id=organization_id,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).exists()


def member_users(organization):
    """
    Every active user of one organization, as a `User` queryset.

    Returns an empty queryset for a `None` organization. That is the fail-
    closed half of notification addressing: a caller that could not name an
    organization addresses nobody, rather than addressing everybody.
    """
    from apps.accounts.models import User

    organization_id = _organization_id(organization)
    if organization_id is None:
        return User.objects.none()

    from .models import MembershipStatus, OrganizationMembership

    member_ids = OrganizationMembership.objects.filter(
        organization_id=organization_id,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).values_list("user_id", flat=True)

    return User.objects.filter(pk__in=member_ids, is_active=True)
