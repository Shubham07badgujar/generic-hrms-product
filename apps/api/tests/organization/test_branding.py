"""
The organisation's public face.

The one endpoint the frontend learns its identity from -- public by design,
because the login screen and the job-application page must say whose system
this is before anyone signs in, and disclosing nothing beyond what those pages
already show.

It is also the ONLY place a tenant is resolved with no principal to ask, which
makes it the most delicate surface in the multi-tenant design. The tests below
are mostly about what it must REFUSE to do: serving one customer's identity to
another customer's visitors would be a cross-tenant leak, and it would be the
same fail-open shape as the incident this architecture exists to prevent --
answering confidently when the honest answer is "I don't know".
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.organization.models import Organization, OrgStatus

pytestmark = pytest.mark.django_db

BRANDING = "/api/v1/org/branding/"


def _org(slug, name, **kw):
    return Organization.objects.create(
        slug=slug, name=name, status=kw.pop("status", OrgStatus.ACTIVE), **kw
    )


def _sole_org(slug, name, **kw):
    """
    Make `name` the only organization in the database.

    Renames the session organization rather than deleting it and creating
    another. Deleting is no longer possible: every tenant-owned table now
    references Organization with PROTECT, and the session organization owns the
    seeded role catalogue -- which is the constraint working exactly as
    intended. The rename rolls back with the test like any other write.
    """
    organization = Organization.objects.get()
    organization.slug = slug
    organization.name = name
    for field, value in kw.items():
        setattr(organization, field, value)
    organization.save()
    return organization


def test_a_lone_organization_needs_no_slug(db):
    """
    A single-organization deployment keeps working exactly as it did: no slug,
    no host configuration, no setup. That path is the self-hosted product, and
    multi-tenancy must not make it harder to run.
    """
    _sole_org("acme", "Acme Traders", legal_name="Acme Traders Pvt Ltd")

    response = APIClient().get(BRANDING)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Acme Traders"
    assert body["legal_name"] == "Acme Traders Pvt Ltd"


def test_a_slug_selects_between_organizations(db):
    _org("acme", "Acme Traders")
    _org("globex", "Globex Corp")

    assert APIClient().get(f"{BRANDING}?org=acme").json()["name"] == "Acme Traders"
    assert APIClient().get(f"{BRANDING}?org=globex").json()["name"] == "Globex Corp"


def test_it_refuses_to_guess_between_organizations(db):
    """
    THE CENTRAL CASE. With more than one tenant and no slug, there is no
    correct answer -- and "the first one" is a cross-tenant identity leak that
    would put one customer's name and logo on another's login page.
    """
    _org("acme", "Acme Traders")
    _org("globex", "Globex Corp")

    assert APIClient().get(BRANDING).status_code == 404


def test_an_unknown_slug_is_a_404(db):
    _org("acme", "Acme Traders")
    _org("globex", "Globex Corp")

    assert APIClient().get(f"{BRANDING}?org=nobody").status_code == 404


def test_a_suspended_organization_is_indistinguishable_from_an_absent_one(db):
    """
    Same 404, deliberately. A different status code would turn this
    unauthenticated endpoint into a way to enumerate customers and to learn
    which of them have stopped paying.
    """
    _org("acme", "Acme Traders")
    _org("gone", "Gone Ltd", status=OrgStatus.SUSPENDED)

    unknown = APIClient().get(f"{BRANDING}?org=no-such-company")
    suspended = APIClient().get(f"{BRANDING}?org=gone")

    assert suspended.status_code == unknown.status_code == 404
    assert suspended.content == unknown.content


def test_branding_exposes_nothing_else(db):
    """Registrations, codes and counters stay behind ORG_SETTINGS/VIEW."""
    from apps.organization.models import OrgSettings

    organization = _sole_org("acme", "Acme")
    OrgSettings.objects.create(
        organization=organization, gstin="22AAAAA0000A1Z5", pan="AAAAA0000A"
    )

    body = APIClient().get(BRANDING).json()

    assert set(body) == {"name", "legal_name", "logo"}


def test_it_needs_no_authentication(db):
    """The login page has no token to offer, and that is the whole point."""
    _sole_org("acme", "Acme Traders")

    assert APIClient().get(BRANDING).status_code == 200
