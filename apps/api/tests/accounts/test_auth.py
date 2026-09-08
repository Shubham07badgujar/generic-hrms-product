"""
Authentication behaviour.

These are security claims, so each is asserted rather than assumed.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
ADMIN_LOGIN = "/api/v1/auth/login/admin/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
ME = "/api/v1/me/"
PERMISSIONS = "/api/v1/me/permissions/"

PASSWORD = "test-password-12345"
COOKIE = settings.REFRESH_COOKIE_NAME


# ---------------------------------------------------------------- login


def test_login_returns_access_token_and_sets_refresh_cookie(api, make_user):
    user = make_user("hr_head")

    response = api.post(LOGIN, {"email": user.email, "password": PASSWORD})

    assert response.status_code == 200
    assert response.data["access"]
    assert COOKIE in response.cookies


def test_refresh_token_is_never_in_the_response_body(api, make_user):
    """
    The refresh token must exist only in an httpOnly cookie.

    Returning it in JSON would put a long-lived credential within reach of any
    XSS on the page — the exact exposure the cookie design removes.
    """
    user = make_user("employee")

    response = api.post(LOGIN, {"email": user.email, "password": PASSWORD})

    assert "refresh" not in response.data
    body = response.content.decode()
    assert str(response.cookies[COOKIE].value) not in body


def test_refresh_cookie_is_httponly_and_samesite_strict(api, make_user):
    user = make_user("employee")

    response = api.post(LOGIN, {"email": user.email, "password": PASSWORD})

    cookie = response.cookies[COOKIE]
    assert cookie["httponly"], "JavaScript must not be able to read the refresh token."
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth/"


def test_wrong_password_is_rejected(api, make_user):
    user = make_user("employee")

    response = api.post(LOGIN, {"email": user.email, "password": "wrong-password"})

    assert response.status_code == 400


def _without_request_id(payload):
    """
    Drop the per-request correlation id before comparing two error bodies.

    It differs by construction on every call and says nothing about the
    account, so leaving it in would make identical responses look different.
    """
    error = {k: v for k, v in payload["error"].items() if k != "request_id"}
    return {**payload, "error": error}


def test_unknown_and_known_emails_fail_identically(api, make_user):
    """
    No user enumeration: a wrong password and a non-existent account must be
    indistinguishable, or the login form becomes an address oracle.
    """
    user = make_user("employee")

    known = api.post(LOGIN, {"email": user.email, "password": "wrong-password"})
    unknown = api.post(LOGIN, {"email": "nobody@example.test", "password": "wrong-password"})

    assert known.status_code == unknown.status_code
    assert _without_request_id(known.data) == _without_request_id(unknown.data)


def test_inactive_user_cannot_log_in(api, make_user):
    user = make_user("employee")
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert api.post(LOGIN, {"email": user.email, "password": PASSWORD}).status_code == 400


# -------------------------------------------------------------- lockout


def test_account_locks_after_repeated_failures(api, make_user):
    from apps.accounts.api.serializers import MAX_FAILED_LOGINS

    user = make_user("employee")

    for _ in range(MAX_FAILED_LOGINS):
        api.post(LOGIN, {"email": user.email, "password": "wrong-password"})

    user.refresh_from_db()
    assert user.is_locked

    # The correct password must not open a locked account.
    response = api.post(LOGIN, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 400
    assert "locked" in str(response.data).lower()


def test_successful_login_resets_the_failure_counter(api, make_user):
    """
    Otherwise failures accumulate across weeks and lock a user who never
    actually failed five times in a row.
    """
    user = make_user("employee")

    for _ in range(3):
        api.post(LOGIN, {"email": user.email, "password": "wrong-password"})
    user.refresh_from_db()
    assert user.failed_login_count == 3

    api.post(LOGIN, {"email": user.email, "password": PASSWORD})
    user.refresh_from_db()
    assert user.failed_login_count == 0
    assert user.locked_until is None


# --------------------------------------------------------- /login/admin


def test_admin_can_use_the_admin_login(api, make_user):
    admin = make_user("admin")

    response = api.post(ADMIN_LOGIN, {"email": admin.email, "password": PASSWORD})

    assert response.status_code == 200
    assert response.data["access"]


def test_non_admin_is_refused_at_the_admin_login(api, make_user):
    hr = make_user("hr_head")

    response = api.post(ADMIN_LOGIN, {"email": hr.email, "password": PASSWORD})

    assert response.status_code == 400


def test_admin_login_does_not_reveal_who_is_an_admin(api, make_user):
    """
    A valid non-admin credential and a bad credential must fail identically.

    Any difference turns /login/admin into an oracle for "which of these
    addresses is an administrator" — a target list.
    """
    hr = make_user("hr_head")

    valid_non_admin = api.post(ADMIN_LOGIN, {"email": hr.email, "password": PASSWORD})
    bad_password = api.post(ADMIN_LOGIN, {"email": hr.email, "password": "wrong-password"})

    assert valid_non_admin.status_code == bad_password.status_code
    assert _without_request_id(valid_non_admin.data) == _without_request_id(
        bad_password.data
    )


# -------------------------------------------------------------- refresh


def test_refresh_issues_a_new_access_token(api, make_user):
    user = make_user("employee")
    api.post(LOGIN, {"email": user.email, "password": PASSWORD})

    response = api.post(REFRESH)

    assert response.status_code == 200
    assert response.data["access"]


def test_refresh_rotates_the_token(api, make_user):
    user = make_user("employee")
    login = api.post(LOGIN, {"email": user.email, "password": PASSWORD})
    original = login.cookies[COOKIE].value

    refreshed = api.post(REFRESH)

    assert refreshed.cookies[COOKIE].value != original


def test_a_replayed_refresh_token_is_rejected(api, make_user):
    """
    Rotation is only worth having if the spent token actually dies.

    This is what makes refresh-token theft self-revealing: whichever party
    spends it second gets a 401 and an unexpected logout.
    """
    user = make_user("employee")
    api.post(LOGIN, {"email": user.email, "password": PASSWORD})
    stolen = api.cookies[COOKIE].value

    api.post(REFRESH)  # legitimate rotation; `stolen` is now spent

    api.cookies[COOKIE] = stolen
    replay = api.post(REFRESH)

    assert replay.status_code == 401


def test_refresh_without_a_cookie_is_unauthorised(api):
    assert api.post(REFRESH).status_code == 401


# --------------------------------------------------------------- logout


def test_logout_clears_the_cookie_and_kills_the_token(api, make_user):
    user = make_user("employee")
    api.post(LOGIN, {"email": user.email, "password": PASSWORD})
    api.credentials(
        HTTP_AUTHORIZATION=f"Bearer {api.post(REFRESH).data['access']}"
    )

    response = api.post(LOGOUT)
    assert response.status_code == 204

    api.credentials()
    assert api.post(REFRESH).status_code == 401


# ------------------------------------------------------------------- me


def _auth(api, user):
    token = api.post(LOGIN, {"email": user.email, "password": PASSWORD}).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def test_me_requires_authentication(api):
    assert api.get(ME).status_code == 401


def test_me_returns_identity_and_roles(api, make_user):
    user = make_user("hr_head")
    _auth(api, user)

    response = api.get(ME)

    assert response.status_code == 200
    assert response.data["email"] == user.email
    assert "hr_head" in response.data["roles"]


def test_me_never_exposes_a_password(api, make_user):
    user = make_user("employee")
    _auth(api, user)

    body = api.get(ME).content.decode().lower()

    assert "password" not in body or "must_change_password" in body
    assert user.password not in body


# ---------------------------------------------------------- permissions


def test_permissions_snapshot_shape(api, make_user):
    user = make_user("hr_head")
    _auth(api, user)

    data = api.get(PERMISSIONS).data

    assert data["dashboard"] == "department"
    assert data["read_only"] is False
    assert data["can_manage_users"] is True
    assert "hr_head" in data["roles"]
    assert "notice" in data, "The snapshot must state that it is advisory."


def test_role_requiring_an_employee_resolves_to_nothing_without_one(api, make_user):
    """
    THE FAIL-CLOSED CLAMP, demonstrated end to end.

    HR Head holds a large permission matrix, but the role is flagged
    `requires_employee`. With no linked Employee record the engine clears every
    grant — because department- and team-scoped permissions are meaningless
    without a department or a reporting position, and resolving them to
    "everything" would be catastrophic.

    The operational consequence, flagged in the architecture doc and now proven:
    a login provisioned WITHOUT an Employee record is inert. Employee creation
    must therefore be a single transaction that creates person, role and login
    together — never a login on its own.
    """
    user = make_user("hr_head")
    assert not hasattr(user, "employee")
    _auth(api, user)

    data = api.get(PERMISSIONS).data

    assert data["grants"] == {}, (
        "A role requiring an Employee must resolve to no access without one."
    )
    # Role-derived flags still report correctly — the role IS held, it simply
    # grants nothing until the person exists.
    assert data["roles"] == ["hr_head"]
    assert data["dashboard"] == "department"


def test_only_hr_head_holds_candidate_rejection_in_the_matrix(roles):
    """
    The approved authority rule, asserted against the seeded matrix.

    Checked at the matrix rather than through a snapshot because HR Head's
    grants are Employee-gated (see the previous test), and the Employee model
    lands in a later phase. This assertion is independent of that.
    """
    from apps.accounts.models import RolePermission
    from core.access import Action, Resource

    holders = set(
        RolePermission.objects.filter(
            resource=Resource.APPLICATION, action=Action.REJECT, is_active=True
        ).values_list("role__code", flat=True)
    )

    assert holders == {"hr_head"}, (
        f"Final candidate rejection must be HR Head alone; found {sorted(holders)}."
    )


def test_admin_snapshot_has_override_but_not_reject(api, make_user):
    """
    The approved hierarchy in the payload the UI reads: Admin overrides a
    decision, and can never make the terminal rejection itself.
    """
    user = make_user("admin")
    _auth(api, user)

    application = api.get(PERMISSIONS).data["grants"]["application"]

    assert "override" in application
    assert "reject" not in application


def test_ceo_snapshot_is_read_only_with_no_write_grants(api, make_user):
    from core.access.catalog import WRITE_ACTIONS

    user = make_user("ceo")
    _auth(api, user)

    data = api.get(PERMISSIONS).data

    assert data["read_only"] is True
    assert data["dashboard"] == "ceo"
    offending = {
        f"{resource}.{action}"
        for resource, actions in data["grants"].items()
        for action in actions
        if action in WRITE_ACTIONS
    }
    assert not offending, f"CEO snapshot contains write grants: {sorted(offending)}"


def test_user_with_no_roles_gets_nothing(api, db):
    """
    A provisioned login with no role must resolve to no access — not to a
    default employee view. Half-provisioning has to fail visibly.
    """
    from apps.accounts.models import User

    user = User.objects.create_user(email="orphan@example.test", password=PASSWORD)
    _auth(api, user)

    data = api.get(PERMISSIONS).data

    assert data["grants"] == {}
    assert data["roles"] == []


# ------------------------------------------------------- sign-in rate limits
#
# Two throttles guard the login endpoints and a request must satisfy both:
# `login` counts per calling machine, `login_account` counts per account being
# signed in to. The tests below pin the boundary of each independently, which
# is why one varies the client address while holding the account fixed and the
# other does the opposite.
#
# The suite-wide fixture nulls every rate so unrelated tests cannot throttle
# one another, so a test whose SUBJECT is throttling has to put the real rates
# back — that is what `real_rates` does.
#
# The configured values are read from the settings module rather than from
# `django.conf.settings`, because the fixture has overridden the latter.
# Reading base.py is what makes these assertions about the shipped
# configuration rather than about a number the test invented for itself.

LOGIN_LIMIT = 150


def _configured(scope):
    """The rate as configured in base.py, not as overridden for the suite."""
    from config.settings import base

    return base.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope]


@pytest.fixture
def real_rates(monkeypatch):
    """Restore the shipped rates for tests whose subject is rate limiting."""
    from rest_framework.throttling import SimpleRateThrottle

    from config.settings import base

    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        base.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
    )


def test_the_configured_sign_in_rates_are_what_we_think_they_are():
    """
    Asserted against base.py's SOURCE as well as its loaded dict.

    Reading the module alone is not enough. A settings module layered on top
    can write `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = ...`, which mutates
    the very dict base.py built — dev.py did exactly that, so
    `config.settings.base` reported 200/hour while the file said otherwise, and
    a test trusting the module would have confirmed a rate no deployment used.

    Checking both means the file states the intended rate AND nothing has
    quietly replaced it underneath at runtime.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "config" / "settings" / "base.py"
    ).read_text(encoding="utf-8")

    assert '"login": "150/hour"' in source
    assert '"login_account": "150/hour"' in source

    assert _configured("login") == "150/hour", (
        "Something overrode the login rate in place after base.py set it."
    )
    assert _configured("login_account") == "150/hour"


def test_login_allows_150_attempts_from_one_client_then_429s(api, make_user, real_rates):
    """
    The per-machine boundary, exercised through the endpoint.

    Each attempt uses a DIFFERENT address so only the per-client throttle is in
    play — if the account throttle were also counting, this would prove nothing
    about which limit produced the 429.
    """
    for i in range(LOGIN_LIMIT):
        response = api.post(
            LOGIN,
            {"email": f"nobody-{i}@example.test", "password": "wrong-password"},
            REMOTE_ADDR="198.51.100.7",
        )
        # 400, not 429: the attempt reached authentication and was refused
        # there on the credentials. That is the distinction being asserted —
        # rejected by the validator, not blocked by the throttle.
        assert response.status_code == 400, (
            f"attempt {i + 1} of {LOGIN_LIMIT} should have been allowed through "
            f"to authentication, got {response.status_code}"
        )

    blocked = api.post(
        LOGIN,
        {"email": "nobody-last@example.test", "password": "wrong-password"},
        REMOTE_ADDR="198.51.100.7",
    )
    assert blocked.status_code == 429

    # A different machine is unaffected — the limit is per client, not global.
    # This is the regression that mattered in production: with the proxy
    # misconfigured, every visitor shared one budget and ten sign-ins locked
    # out the whole organisation.
    user = make_user("employee")
    other = api.post(
        LOGIN,
        {"email": user.email, "password": PASSWORD},
        REMOTE_ADDR="203.0.113.9",
    )
    assert other.status_code == 200


def test_one_account_cannot_be_tried_more_than_150_times_from_any_number_of_clients(
    api, real_rates
):
    """
    The per-account boundary — the protection that makes raising the per-client
    limit safe to do.

    Every attempt comes from a different address, so the per-client throttle
    never accumulates. Before this throttle existed a distributed attempt
    against one account had no ceiling at all: each new address bought a fresh
    allowance.
    """
    target = "victim@example.test"

    for i in range(LOGIN_LIMIT):
        response = api.post(
            LOGIN,
            {"email": target, "password": "wrong-password"},
            REMOTE_ADDR=f"192.0.2.{i % 254 + 1}",
        )
        assert response.status_code == 400, f"attempt {i + 1} unexpectedly blocked"

    blocked = api.post(
        LOGIN,
        {"email": target, "password": "wrong-password"},
        REMOTE_ADDR="198.51.100.200",  # a machine that has tried nothing
    )
    assert blocked.status_code == 429, (
        "A fresh client was able to keep attacking an account that had already "
        "used its full hourly allowance."
    )


def test_capitalising_the_address_does_not_buy_a_second_account_budget(api):
    """
    Login matches addresses case-insensitively, so counting `Victim@` apart
    from `victim@` would let an attacker multiply their budget by varying the
    capitalisation.
    """
    from core.api.throttling import LoginAccountThrottle

    throttle = LoginAccountThrottle()

    class _Req:
        data = {"email": "  Victim@Example.Test  "}

    class _Req2:
        data = {"email": "victim@example.test"}

    assert throttle.get_cache_key(_Req(), None) == throttle.get_cache_key(_Req2(), None)


def test_the_account_throttle_stores_no_readable_addresses(api):
    """The cache key is a digest — Redis must not become a directory of accounts."""
    from core.api.throttling import LoginAccountThrottle

    class _Req:
        data = {"email": "victim@example.test"}

    key = LoginAccountThrottle().get_cache_key(_Req(), None)

    assert "victim@example.test" not in key
    assert "victim" not in key


def test_refresh_still_works_once_the_login_limit_is_spent(api, make_user, real_rates):
    """
    Refresh must not be collateral damage.

    It carries no credentials — it spends an httpOnly cookie the browser holds
    — so it is deliberately outside the sign-in throttles. A user already
    holding a session must not be logged out because someone else on their
    office NAT exhausted the login allowance.
    """
    user = make_user("employee")
    assert api.post(LOGIN, {"email": user.email, "password": PASSWORD}).status_code == 200

    for i in range(LOGIN_LIMIT):
        api.post(
            LOGIN,
            {"email": f"noise-{i}@example.test", "password": "wrong-password"},
        )

    assert api.post(LOGIN, {"email": user.email, "password": PASSWORD}).status_code == 429

    refreshed = api.post(REFRESH)
    assert refreshed.status_code == 200
    assert "access" in refreshed.data
    # And the rotated cookie is still set, so the session continues normally.
    assert COOKIE in refreshed.cookies


def test_raising_the_login_limit_left_the_other_limits_alone(api):
    """
    The bootstrap and public-application endpoints are deliberately far
    tighter. Widening sign-in must not have widened those by association.
    """
    assert _configured("bootstrap") == "5/hour"
    assert _configured("credential_handoff") == "10/hour"

    # `public_apply` was once removed for having no consumer — a dead scope
    # invites something unrelated to attach to it. It is back because the
    # public application form now exists, and it is pinned to that ONE view:
    # if this assertion fails, either the rate moved or something else has
    # started borrowing the scope.
    from apps.recruitment.api.public import PublicApplyView

    assert _configured("public_apply") == "20/hour"
    assert PublicApplyView.throttle_scope == "public_apply"
    assert PublicApplyView.access_exempt is True  # anonymous by design, said out loud
    # Bulk import has its own, sized in batches per hour rather than
    # requests per minute.
    # Raised from 12 when the throttle stopped counting reads and row edits:
    # only uploads and commits spend it now, and forty covers a real stack of
    # export files in one HR sitting.
    assert _configured("candidate_import") == "40/hour"
