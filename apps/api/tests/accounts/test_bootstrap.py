"""
Admin bootstrap.

The one place a privileged account can be created without an existing
privileged account, so each control is asserted individually.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

BOOTSTRAP = "/api/v1/bootstrap/admin/"
TOKEN = "test-bootstrap-token-value"


# ------------------------------------------------------- management command


def test_command_creates_an_admin_with_no_usable_password(roles):
    from apps.accounts.services.bootstrap import create_admin

    user = create_admin(email="founder@example.test", first_name="Asha")

    assert user.email == "founder@example.test"
    assert not user.has_usable_password(), (
        "Bootstrap must never set a password. A leaked token would otherwise "
        "yield an account the attacker can sign into."
    )
    assert user.must_change_password
    assert user.user_roles.filter(role__code="admin", is_active=True).exists()


def test_the_bootstrapped_admin_can_actually_administer(roles):
    """
    The assertion every other test in this file stopped one step short of.

    They all check that the account EXISTS -- the right password state, the
    right role row, the right audit record. None checked that it WORKS, and for
    a while it did not: the founding Admin was created with no organization
    membership, so `resolve_context()` returned DENY_ALL and the account could
    reach nothing at all. Twelve green tests, and an administrator who could
    not administer.

    Existence is not the property that matters here. Authority is.
    """
    from apps.accounts.services.bootstrap import create_admin
    from core.access import Action, Resource, Scope
    from core.access.context import resolve_context

    user = create_admin(email="founder@example.test", first_name="Asha")

    ctx = resolve_context(user)

    assert ctx.organization_id is not None, (
        "The founding Admin has no organization, so every query they make "
        "resolves to nothing."
    )
    assert ctx.scope_for(Resource.EMPLOYEE, Action.VIEW) == Scope.ALL
    assert ctx.scope_for(Resource.ROLE, Action.EDIT) == Scope.ALL


def test_bootstrap_is_one_shot(roles):
    from apps.accounts.services.bootstrap import BootstrapError, create_admin

    create_admin(email="first@example.test")

    with pytest.raises(BootstrapError, match="already exists"):
        create_admin(email="second@example.test")


def test_bootstrap_requires_an_active_admin_role(roles):
    """
    Without an active Admin role there is nothing to grant, so it must refuse
    rather than create an account with no authority.

    The role is deactivated inside the test transaction (roles are seeded once
    per session), which reproduces the un-seeded condition without disturbing
    other tests.
    """
    from apps.accounts.models import Role
    from apps.accounts.services.bootstrap import BootstrapError, create_admin

    Role.objects.filter(code="admin").update(is_active=False)

    with pytest.raises(BootstrapError, match="seed_roles"):
        create_admin(email="premature@example.test")


def test_bootstrap_writes_an_audit_record(roles):
    from apps.accounts.services.bootstrap import create_admin
    from apps.audit.models import AuditLog

    user = create_admin(email="audited@example.test")

    assert AuditLog.objects.filter(
        entity_type="accounts.User", entity_id=str(user.pk)
    ).exists()


# ------------------------------------------------------------- HTTP endpoint


@pytest.fixture
def bootstrap_api(settings):
    """
    An API client with the bootstrap route live.

    The route is registered at import time from settings, so enabling it means
    re-resolving the URLconf — which is itself the proof that an unset token
    removes the route rather than merely guarding it.
    """
    from django.urls import clear_url_caches
    from rest_framework.test import APIClient

    settings.ADMIN_BOOTSTRAP_TOKEN = TOKEN
    settings.ADMIN_BOOTSTRAP_ALLOWED_IPS = ["127.0.0.1"]

    import importlib

    import apps.accounts.api.urls as auth_urls
    import config.urls as root_urls

    importlib.reload(auth_urls)
    importlib.reload(root_urls)
    clear_url_caches()

    yield APIClient()

    settings.ADMIN_BOOTSTRAP_TOKEN = ""
    importlib.reload(auth_urls)
    importlib.reload(root_urls)
    clear_url_caches()


def test_route_does_not_exist_without_a_token(api, roles):
    """
    With no token configured the URL is absent, not merely forbidden.

    A 404 gives an attacker nothing to probe or brute-force; a 403 would
    confirm the endpoint exists and invite attempts.
    """
    response = api.post(BOOTSTRAP, {"email": "x@example.test"})

    assert response.status_code == 404


def test_creates_an_admin_with_a_valid_token(bootstrap_api, roles):
    response = bootstrap_api.post(
        BOOTSTRAP,
        {"email": "founder@example.test", "first_name": "Asha"},
        HTTP_X_BOOTSTRAP_TOKEN=TOKEN,
    )

    assert response.status_code == 201
    assert response.data["email"] == "founder@example.test"
    assert "next_steps" in response.data


def test_rejects_a_wrong_token(bootstrap_api, roles):
    response = bootstrap_api.post(
        BOOTSTRAP,
        {"email": "attacker@example.test"},
        HTTP_X_BOOTSTRAP_TOKEN="wrong-token",
    )

    assert response.status_code == 403


def test_rejects_a_missing_token(bootstrap_api, roles):
    assert bootstrap_api.post(BOOTSTRAP, {"email": "x@example.test"}).status_code == 403


def test_rejects_a_disallowed_ip(bootstrap_api, roles, settings):
    settings.ADMIN_BOOTSTRAP_ALLOWED_IPS = ["10.0.0.1"]

    response = bootstrap_api.post(
        BOOTSTRAP, {"email": "x@example.test"}, HTTP_X_BOOTSTRAP_TOKEN=TOKEN
    )

    assert response.status_code == 403


def test_every_rejection_looks_the_same(bootstrap_api, roles, settings):
    """
    A wrong token and a disallowed IP must be indistinguishable, so probing
    cannot map which control is blocking.
    """
    wrong_token = bootstrap_api.post(
        BOOTSTRAP, {"email": "x@example.test"}, HTTP_X_BOOTSTRAP_TOKEN="nope"
    )
    settings.ADMIN_BOOTSTRAP_ALLOWED_IPS = ["10.0.0.1"]
    bad_ip = bootstrap_api.post(
        BOOTSTRAP, {"email": "x@example.test"}, HTTP_X_BOOTSTRAP_TOKEN=TOKEN
    )

    assert wrong_token.status_code == bad_ip.status_code == 403
    assert wrong_token.data == bad_ip.data


def test_endpoint_accepts_no_password_field(bootstrap_api, roles):
    """
    A password supplied in the request must be ignored, not honoured.

    The whole design rests on the created account being unusable until a
    password is set out of band.
    """
    response = bootstrap_api.post(
        BOOTSTRAP,
        {"email": "founder@example.test", "password": "attacker-chosen-password"},
        HTTP_X_BOOTSTRAP_TOKEN=TOKEN,
    )

    assert response.status_code == 201

    from apps.accounts.models import User

    user = User.objects.get(email="founder@example.test")
    assert not user.has_usable_password()
    assert not user.check_password("attacker-chosen-password")


def test_second_call_conflicts(bootstrap_api, roles):
    bootstrap_api.post(
        BOOTSTRAP, {"email": "first@example.test"}, HTTP_X_BOOTSTRAP_TOKEN=TOKEN
    )

    second = bootstrap_api.post(
        BOOTSTRAP, {"email": "second@example.test"}, HTTP_X_BOOTSTRAP_TOKEN=TOKEN
    )

    assert second.status_code == 409
