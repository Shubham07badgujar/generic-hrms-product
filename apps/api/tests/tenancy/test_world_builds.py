"""
The two-organization fixture builds two genuinely separate worlds.

Guards the guard. Every isolation assertion in this package is worthless if the
fixture quietly shares something between the organizations, or if one of them
fails to build and the test passes over an empty set.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def test_the_two_organizations_are_distinct(org_a, org_b):
    assert org_a.organization.pk != org_b.organization.pk
    assert org_a.slug != org_b.slug


def test_each_has_its_own_role_catalogue(org_a, org_b):
    """
    Roles are per-organization now. Before `seed_roles` took an organization it
    would have matched the first company's role by code and rewritten it,
    leaving the second with none -- and every principal there resolving to
    DENY_ALL.
    """
    from apps.accounts.models import Role

    a = set(Role.objects.filter(organization=org_a.organization).values_list("code", flat=True))
    b = set(Role.objects.filter(organization=org_b.organization).values_list("code", flat=True))

    assert a == b, "both organizations get the same role vocabulary"
    assert len(a) >= 18
    assert not (
        set(Role.objects.filter(organization=org_a.organization).values_list("pk", flat=True))
        & set(Role.objects.filter(organization=org_b.organization).values_list("pk", flat=True))
    ), "and they are different rows"


def test_every_fixture_row_belongs_to_its_own_organization(org_a, org_b):
    for world in (org_a, org_b):
        for label, row in world.rows.items():
            assert row.organization_id == world.organization.pk, (
                f"{world.slug}.{label} was built into the wrong organization"
            )


def test_both_organizations_use_the_same_business_keys(org_a, org_b):
    """The whole point of scoped uniqueness, exercised for real."""
    assert org_a.rows["employee"].employee_code == org_b.rows["employee"].employee_code
    assert org_a.department.code == org_b.department.code
    assert org_a.rows["asset"].asset_tag == org_b.rows["asset"].asset_tag
    assert org_a.rows["leave_type"].code == org_b.rows["leave_type"].code


def test_principals_can_sign_in(org_a, org_b, api_for):
    for world in (org_a, org_b):
        for user in (world.admin, world.hr, world.worker):
            api_for(user)  # asserts a 200 internally


def test_each_principal_resolves_to_its_own_organization(org_a, org_b):
    from core.access.context import resolve_context

    for world in (org_a, org_b):
        for user in (world.admin, world.hr, world.worker):
            ctx = resolve_context(user)
            assert ctx.organization_id == world.organization.pk
            assert ctx.grants, f"{user.email} resolved with no grants"
