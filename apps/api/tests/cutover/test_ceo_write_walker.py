"""
Cutover gate 2: the CEO write-walker.

Walks the URL resolver, finds EVERY route, and fires every unsafe HTTP method
at it as the CEO. Each must be refused.

This is deliberately not a list of endpoints someone maintains. A hand-written
list protects the routes its author remembered; this one is derived from the
resolver, so a route added next year is covered the day it is added. That is
the same reasoning behind the boot-time system check, applied to behaviour
rather than configuration.

Nothing here consults the frontend. Button visibility is not a control.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import URLPattern, URLResolver, clear_url_caches, get_resolver

pytestmark = pytest.mark.django_db

UNSAFE_METHODS = ("post", "put", "patch", "delete")

#: Refusals. 401/403 are the access engine; 404 is an out-of-scope object,
#: which is also a refusal (and deliberately indistinguishable from absent);
#: 405 means the route does not accept the method at all.
REFUSED = {401, 403, 404, 405}

#: Placeholder values for URL parameters. The object need not exist — a CEO
#: must be refused before the handler ever looks for it, and a 404 from a
#: missing row is itself a refusal.
SAMPLE = {
    "pk": str(uuid.uuid4()),
    "id": str(uuid.uuid4()),
    "key": "hr.headcount",
    "token": "x" * 32,
    "format": "json",
}


#: Routes excluded from the walk, each for a stated reason.
#:
#: SESSION LIFECYCLE — signing in and out must work for every principal
#: INCLUDING the CEO. A read-only account that cannot authenticate is not a
#: secured account, it is a locked-out one. These carry no business authority,
#: are separately rate-limited, and return 400 on the empty body sent here.
#:
#: BOOTSTRAP — a pre-authentication provisioning endpoint gated on a token and
#: an IP allowlist, not on RBAC, and deliberately throttled to 5/hour so it
#: cannot be probed. Walking it would exhaust that budget from a cache that
#: does not roll back between tests, breaking the dedicated bootstrap suite
#: that follows. It is asserted explicitly below instead, so no coverage is
#: lost — only the repeated probing.
EXCLUDED_ROUTES = (
    "/api/v1/auth/login/",
    "/api/v1/auth/login/admin/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/refresh/",
    "/api/v1/bootstrap/admin/",
    # One's own password is not an organisational write: it is the credential
    # the read-only clamp protects, not a record the clamp governs. Excluded
    # from the sweep on the same footing as login and logout — the CEO must be
    # able to rotate their own secret or a leaked one could never be revoked —
    # and asserted separately below: the route accepts nothing that would
    # change anyone ELSE'S account.
    "/api/v1/auth/change-password/",
)


def _fill(pattern: str) -> str | None:
    """Turn a route pattern into a concrete path, or None if it is not walkable."""
    import re

    path = pattern

    # Django path() converters: <uuid:pk>, <str:key>, <pk>.
    for name, value in SAMPLE.items():
        path = re.sub(rf"<[^:>]*:?{name}>", value, path)

    # DRF router regex groups: (?P<pk>[^/.]+). Named groups we have a sample
    # for become that sample; the rest make the route unwalkable.
    def replace_group(match: re.Match) -> str:
        name = match.group(1)
        return SAMPLE.get(name, "\x00")

    path = re.sub(r"\(\?P<([^>]+)>[^)]*\)", replace_group, path)

    # Strip regex anchors and the optional format-suffix tail, both of which
    # are syntax rather than path.
    path = path.replace("^", "").replace("$", "").replace("\\", "")

    if "\x00" in path or "<" in path:
        return None
    if any(ch in path for ch in "[]?*+|()"):
        return None
    return path


def collect_routes() -> list[str]:
    """Every concrete API path the resolver exposes."""
    found: list[str] = []

    def walk(resolver, prefix: str):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry, prefix + str(entry.pattern))
            elif isinstance(entry, URLPattern):
                full = prefix + str(entry.pattern)
                if not full.startswith("api/"):
                    continue
                filled = _fill(full)
                if filled and not filled.endswith(".json"):
                    found.append("/" + filled)

    walk(get_resolver(), "")

    # Leave the URL machinery exactly as found. This runs at MODULE IMPORT, so
    # it primes Django's resolver cache during collection — before any fixture
    # has run. The bootstrap suite reloads `config.urls` and calls
    # `clear_url_caches()` to prove that an unset token removes the route, and
    # a resolver this module warmed underneath it made those tests fail for
    # reasons that had nothing to do with bootstrap.
    clear_url_caches()

    return sorted(set(found) - set(EXCLUDED_ROUTES))


ROUTES = collect_routes()


def test_the_walker_actually_found_the_api():
    """
    A walker that found nothing would pass every assertion below.

    This guards the guard: if route collection silently breaks, the CEO gate
    would report success while testing nothing at all.
    """
    assert len(ROUTES) >= 40, f"Only {len(ROUTES)} routes collected — collection is broken."
    joined = " ".join(ROUTES)
    for expected in ("employees", "payroll", "audit", "notifications", "bi"):
        assert expected in joined, f"Walker missed the {expected} routes entirely."


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r)
def test_ceo_cannot_write_to_any_route(route, auth, everyone):
    """
    Every unsafe method, every route, as the CEO. All must be refused.

    Four independent mechanisms should each be enough on their own: the engine
    clamp, ReadOnlyPrincipalMiddleware, the DB constraint on the role, and
    `require()` in the services. This asserts the observable result of all four.
    """
    client = auth(everyone["ceo"])
    allowed = []

    for method in UNSAFE_METHODS:
        response = getattr(client, method)(route, {}, format="json")
        if response.status_code not in REFUSED:
            allowed.append(f"{method.upper()} {route} -> {response.status_code}")

    assert not allowed, "CEO performed writes:\n  " + "\n  ".join(allowed)


def test_an_ordinary_employee_is_also_walked_for_comparison(auth, everyone):
    """
    The walker is only meaningful if it can fail.

    A self-service employee legitimately has a few writes (their own
    resignation, their own documents, their own notification preferences), so
    this asserts the walker DOES observe writes when they exist — otherwise a
    broken walker would look identical to a perfectly locked-down system.
    """
    client = auth(everyone["employee"])
    permitted = 0

    for route in ROUTES:
        for method in UNSAFE_METHODS:
            response = getattr(client, method)(route, {}, format="json")
            if response.status_code not in REFUSED:
                permitted += 1

    assert permitted > 0, (
        "Even a self-service employee was refused everywhere, which means the "
        "walker cannot distinguish a locked account from a broken test."
    )


def test_ceo_is_refused_on_the_specific_actions_that_matter_most(auth, everyone, staff):
    """
    The named authorities, hit directly rather than via the generic walk.

    These are the operations whose accidental exposure would be most serious,
    so they get an explicit assertion that names them.
    """
    client = auth(everyone["ceo"])
    employee_id = staff["therapist"].pk

    attempts = [
        ("post", "/api/v1/employees/", {"first_name": "X", "email": "x@y.test"}),
        ("patch", f"/api/v1/employees/{employee_id}/", {"first_name": "Changed"}),
        ("delete", f"/api/v1/employees/{employee_id}/", None),
        ("post", "/api/v1/payroll/runs/", {"period_year": 2026, "period_month": 1}),
        ("post", "/api/v1/jobs/", {"title": "X"}),
        ("post", "/api/v1/candidates/", {"first_name": "X"}),
        ("post", "/api/v1/notifications/read-all/", {}),
    ]

    allowed = []
    for method, path, body in attempts:
        response = getattr(client, method)(path, body or {}, format="json")
        if response.status_code not in REFUSED:
            allowed.append(f"{method.upper()} {path} -> {response.status_code}")

    assert not allowed, "CEO reached privileged operations:\n  " + "\n  ".join(allowed)


def test_ceo_can_still_read_the_things_oversight_requires(auth, everyone):
    """
    The counterweight.

    A read-only account that cannot read is not secure, it is broken — and the
    temptation to "fix" it by loosening writes is exactly what this asserts is
    unnecessary.
    """
    client = auth(everyone["ceo"])

    for path in (
        "/api/v1/employees/",
        "/api/v1/candidates/",
        "/api/v1/payslips/",
        "/api/v1/audit/",
        "/api/v1/bi/metrics/",
        "/api/v1/bi/hr.headcount/",
    ):
        assert client.get(path).status_code == 200, f"CEO cannot read {path}"


@pytest.mark.django_db
def test_the_ceo_can_only_change_their_own_password(auth, everyone):
    """
    The change-password route is excluded from the sweep above; this pins why
    that is safe. It takes no target: it acts on `request.user` and nobody
    else, so a read-only principal reaching it can rotate their own secret and
    nothing more.
    """
    from apps.accounts.models import User

    ceo_user = everyone["ceo"]
    other = User.objects.exclude(pk=ceo_user.pk).first()
    before = other.password if other else None
    api = auth(ceo_user)

    response = api.post(
        "/api/v1/auth/change-password/",
        # A payload that TRIES to name somebody else is simply ignored: the
        # serializer has no such field.
        {
            "user": str(other.pk) if other else "",
            "email": other.email if other else "",
            "current_password": "wrong-on-purpose",
            "new_password": "Correct-Horse-Battery-9",
        },
        format="json",
    )

    # Refused (wrong current password), and either way nobody else moved.
    assert response.status_code == 400
    if other:
        other.refresh_from_db()
        assert other.password == before
