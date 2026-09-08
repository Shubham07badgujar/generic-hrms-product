"""
Binding and unbinding the acting organization.

`_current_org` is the WRITE-path half of tenancy: it stamps new rows and scopes
request-less callers. Reads resolve their organization from `AccessContext`
instead, derived from the authenticated principal -- see
docs/ARCHITECTURE.md §13.2 for why that distinction is the whole point.

What is NOT covered here: `TenantManager` filtering and `OrgOwnedModel.save()`
stamping, both of which need a concrete table. Exercising them against a
synthetic model needs `isolate_apps`, which cannot resolve `accounts.User`
(already-imported models do not re-register in a temporary app registry). They
are covered against real models as each app is converted -- which is better
evidence anyway, since it tests the tables that actually hold payroll.
"""

from __future__ import annotations

import pytest

from core.access import context as access_context
from core.middleware import acting_as, get_current_org_id

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    from apps.organization.models import Organization

    return Organization.objects.create(name="Acme", slug="acme")


def test_nothing_is_bound_by_default():
    """
    None means "not established", never "all of them". Every consumer treats
    it as an error; that is what makes the default safe.
    """
    assert get_current_org_id() is None


def test_acting_as_binds_and_restores(org):
    with acting_as(None, organization=org):
        assert get_current_org_id() == org.pk

    assert get_current_org_id() is None


def test_a_bare_id_is_accepted(org):
    """Celery hands the task an id, not a model instance."""
    with acting_as(None, organization=org.pk):
        assert get_current_org_id() == org.pk


def test_blocks_nest_without_leaking(org):
    from apps.organization.models import Organization

    other = Organization.objects.create(name="Globex", slug="globex")

    with acting_as(None, organization=org):
        with acting_as(None, organization=other):
            assert get_current_org_id() == other.pk
        assert get_current_org_id() == org.pk, "inner block clobbered the outer one"


def test_an_exception_still_restores_the_previous_binding(org):
    """
    A task that raises must not leave its organization bound. A worker thread
    is reused, so the next task on it would inherit one -- the fail-open shape,
    moved from a request onto a worker.
    """
    with pytest.raises(RuntimeError):
        with acting_as(None, organization=org):
            raise RuntimeError("boom")

    assert get_current_org_id() is None


def test_acting_as_isolates_the_access_context_cache(org):
    """
    `_ctx_cache` was set and never reset. Inside a request that is harmless --
    the request attribute is used instead -- but a WSGI worker thread or a
    Celery worker reuses its context across units of work, so entries survived
    into work they were never resolved for. Stale role grants were already
    wrong; a stale organization is the failure this design exists to prevent.
    """
    sentinel = {"someone": "a stale context"}
    token = access_context._ctx_cache.set(sentinel)
    try:
        with acting_as(None, organization=org):
            assert access_context._ctx_cache.get() is None, (
                "acting_as inherited a context cache resolved outside its block"
            )

        assert access_context._ctx_cache.get() is sentinel, (
            "acting_as failed to restore the caller's cache"
        )
    finally:
        access_context._ctx_cache.reset(token)
