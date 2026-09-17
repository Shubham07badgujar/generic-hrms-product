"""
Authority is resolved inside the principal's own organization, whatever is bound.

`resolve_context` is what PRODUCES the organization a request gets bound to, so
the queries inside it cannot lean on the bound value: on the admin sign-in door,
under `force_authenticate`, and for `get_context(user)` inside a service, the
ambient organization is either absent or somebody else's. These tests plant
rows in the OTHER organization for the same person and assert they confer
nothing.
"""

from __future__ import annotations

import pytest

from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def _role(organization, code):
    from apps.accounts.models import Role

    return Role.objects.all_orgs().get(organization=organization, code=code)


def test_a_grant_row_in_another_organization_confers_nothing(org_a, org_b):
    """
    One user, a membership in A, and an Admin grant row sitting in B.

    That row should never exist, and the schema's clamp is what normally stops
    it. But grants were read as "every UserRole this user holds", so a single
    inconsistent row -- a bad import, a botched merge, a manual fix -- would
    have made an ordinary employee in A an Admin in A. Authority has to come
    from grants in the organization the membership names, and nowhere else.
    """
    from apps.accounts.models import UserRole
    from core.access.context import resolve_context

    worker = org_a.worker
    with acting_as(None, organization=org_b.organization):
        UserRole.objects.bulk_create(
            [UserRole(user=worker, role=_role(org_b.organization, "admin"))]
        )

    ctx = resolve_context(worker)

    assert ctx.organization_id == org_a.organization.pk
    assert "admin" not in ctx.role_codes, (
        "a grant row in another organization made this user an Admin here"
    )
    # Positive control: the grant they really hold still resolves.
    assert "employee" in ctx.role_codes


def test_the_admin_sign_in_door_ignores_another_organizations_admin_grant(
    org_a, org_b, api_for
):
    """The same planted row, at the door that checks roles before any session exists."""
    from rest_framework.test import APIClient

    from apps.accounts.models import UserRole
    from tests.tenancy.conftest import PASSWORD

    worker = org_a.worker
    with acting_as(None, organization=org_b.organization):
        UserRole.objects.bulk_create(
            [UserRole(user=worker, role=_role(org_b.organization, "admin"))]
        )

    client = APIClient(HTTP_HOST="localhost")
    refused = client.post(
        "/api/v1/auth/login/admin/",
        {"email": worker.email, "password": PASSWORD},
        format="json",
    )
    assert refused.status_code in (400, 401), (
        f"a non-admin passed the admin door on another organization's grant: "
        f"{refused.status_code} {refused.content[:200]}"
    )

    # Positive control: A's real Admin gets through the same door.
    admitted = client.post(
        "/api/v1/auth/login/admin/",
        {"email": org_a.admin.email, "password": PASSWORD},
        format="json",
    )
    assert admitted.status_code == 200, admitted.content[:200]


def test_me_reports_only_roles_held_in_the_users_own_organization(
    org_a, org_b, api_for
):
    from apps.accounts.models import UserRole

    worker = org_a.worker
    with acting_as(None, organization=org_b.organization):
        UserRole.objects.bulk_create(
            [UserRole(user=worker, role=_role(org_b.organization, "admin"))]
        )

    response = api_for(worker).get("/api/v1/me/")

    assert response.status_code == 200, response.content[:200]
    assert response.json()["roles"] == ["employee"]


@pytest.mark.django_db
def test_bootstrap_grants_the_admin_role_of_the_organization_it_founds(db):
    """
    Bootstrap looked the Admin role up by code BEFORE it knew the organization.

    Role codes are unique only per organization, so with two organizations
    seeded the founding Admin could be granted the other one's Admin role --
    and be a member of one company holding authority defined by another.
    """
    from apps.accounts.models import UserRole
    from apps.accounts.services.bootstrap import create_admin
    from apps.accounts.services.roles import seed_roles
    from apps.organization.models import Organization, OrgStatus

    founded = Organization.objects.create(
        name="Founded Co", slug="founded-co", status=OrgStatus.ACTIVE
    )
    with acting_as(None, organization=founded):
        seed_roles(organization=founded)

    user = create_admin(email="founder@founded-co.example", organization=founded)

    grants = UserRole.objects.all_orgs().filter(user=user).select_related("role")
    assert [(g.role.code, g.role.organization_id) for g in grants] == [
        ("admin", founded.pk)
    ], "the founding Admin holds a role from another organization"
