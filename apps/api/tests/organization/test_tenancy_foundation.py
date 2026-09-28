"""
The tenant entity and the membership that resolves it.

These cover the two properties the rest of the multi-tenant design rests on:
an organization's status decides whether its people may work, and a user
resolves to exactly one organization.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from core.middleware import acting_as
from tests.conftest import across_organizations

from apps.organization.models import (
    OPERATIONAL_STATUSES,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrgStatus,
)

pytestmark = pytest.mark.django_db


def _org(slug: str, **kw) -> Organization:
    return Organization.objects.create(
        name=kw.pop("name", slug.replace("-", " ").title()), slug=slug, **kw
    )


def _user(email: str):
    from apps.accounts.models import User

    return User.objects.create_user(email=email, password="test-password-12345")


def _join(org, user, **kw):
    """
    Put a user in an organization, BOUND to that organization.

    These tests build their own organizations while the suite's autouse
    fixture still binds the session one, and a membership belongs to the
    organization it names -- so from release 2 the database refuses to write
    it into whichever tenant happens to be in force. Saying which organization
    is being built is what the product does everywhere else, too.
    """
    from core.middleware import acting_as

    with acting_as(None, organization=org):
        return OrganizationMembership.objects.create(organization=org, user=user, **kw)


# ------------------------------------------------------------------ lifecycle


@pytest.mark.parametrize(
    "status,operational",
    [
        (OrgStatus.PENDING_SETUP, True),
        (OrgStatus.TRIAL, True),
        (OrgStatus.ACTIVE, True),
        (OrgStatus.SUSPENDED, False),
        (OrgStatus.CANCELLED, False),
        (OrgStatus.ARCHIVED, False),
    ],
)
def test_only_the_working_statuses_are_operational(status, operational):
    """
    PENDING_SETUP is deliberately operational: the administrator is partway
    through the setup wizard and must be able to read and write in order to
    finish it. A locked "pending" state would make onboarding impossible.
    """
    org = _org("acme", status=status)

    assert org.is_operational is operational
    assert (status in OPERATIONAL_STATUSES) is operational


def test_a_new_organization_starts_pending_setup():
    assert _org("acme").status == OrgStatus.PENDING_SETUP


def test_slug_is_globally_unique():
    """
    The one globally unique key besides User.email. The pre-authentication
    branding endpoint has no principal to resolve a tenant from, so the slug
    is what tells it which organization is being asked about.
    """
    _org("acme")

    with pytest.raises(IntegrityError):
        _org("acme", name="A Different Company")


# ----------------------------------------------------------------- membership


def test_a_user_resolves_to_one_organization():
    org = _org("acme")
    user = _user("alice@example.test")

    _join(org, user)

    with acting_as(None, organization=org):
        assert user.memberships.get().organization == org


def test_a_user_cannot_hold_two_active_memberships():
    """The V1 clamp. Without it a login would be ambiguous."""
    a, b = _org("acme"), _org("globex")
    user = _user("alice@example.test")
    _join(a, user)

    with pytest.raises(IntegrityError), transaction.atomic():
        _join(b, user)


def test_the_schema_permits_a_second_non_active_membership():
    """
    The clamp is on ACTIVE rows only, so the table is already a genuine
    many-to-many. That is what makes multi-organization support a change of
    policy rather than a redesign of identity -- though only the schema half;
    see docs/ARCHITECTURE.md §13.4.
    """
    a, b = _org("acme"), _org("globex")
    user = _user("alice@example.test")
    _join(a, user)

    _join(b, user, status=MembershipStatus.REMOVED)

    # Both organizations at once, so it reads the way the platform does.
    with across_organizations():
        assert user.memberships.count() == 2
        assert user.memberships.filter(status=MembershipStatus.ACTIVE).count() == 1


def test_the_same_user_and_organization_cannot_be_paired_twice():
    org = _org("acme")
    user = _user("alice@example.test")
    _join(org, user)

    with pytest.raises(IntegrityError), transaction.atomic():
        _join(org, user, status=MembershipStatus.REMOVED)


def test_two_organizations_hold_separate_people():
    a, b = _org("acme"), _org("globex")
    _join(a, _user("a@example.test"))
    _join(b, _user("b@example.test"))

    with across_organizations():
        assert a.memberships.count() == 1
        assert b.memberships.count() == 1
        assert set(a.memberships.all()).isdisjoint(b.memberships.all())
