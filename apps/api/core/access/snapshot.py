"""
The permission snapshot handed to the React SPA.

This is what `GET /me/permissions` returns and what the front end caches to
decide which menu items, buttons and routes to render. It is a PROJECTION of
the server-side AccessContext — never the authority.

The distinction is load-bearing and stated in the payload itself: hiding a
button the snapshot forbids is a courtesy to the user; the API re-resolves and
re-enforces every permission on every request regardless of what the client
believes. A tampered snapshot changes what the user sees, never what they can do.
"""

from __future__ import annotations

from .catalog import Action, Resource, Scope
from .context import AccessContext, get_context


def _enabled_features(ctx) -> list[str]:
    """
    Features the client may render, as a positive list.

    Inverted from the stored `disabled_features` on purpose. In the database
    absence must mean ALLOWED, so that adding a FeatureCode does not silently
    switch a module off for every existing customer; in the snapshot absence
    must mean UNAVAILABLE, so the SPA's `hasFeature()` is the same truthiness
    check as its `can()`. Both conventions are right for where they live, and
    this function is the one place they meet.
    """
    from .features import FeatureCode

    return sorted(
        str(f) for f in FeatureCode if str(f) not in ctx.disabled_features
    )


def build_snapshot(user) -> dict:
    """
    A compact, client-consumable view of one principal's authority.

    Shape:
        {
          "dashboard": "department",
          "read_only": false,
          "layers": [2],
          "roles": ["hr_head"],
          "grants": { "candidate": {"view": "all", "reject": "all"}, ... },
          "notice": "Advisory only. The API enforces every permission ..."
        }

    `grants` maps resource -> action -> scope name. The SPA's `can(resource,
    action)` is a truthiness check on presence; `scopeOf(...)` reads the value
    when a screen needs to know the breadth (e.g. "show the department picker
    only at ALL scope").
    """
    ctx: AccessContext = get_context(user)

    grants: dict[str, dict[str, str]] = {}
    for (resource, action), scope in ctx.grants.items():
        if scope == Scope.NONE:
            continue
        grants.setdefault(resource, {})[action] = Scope(scope).name.lower()

    return {
        "dashboard": ctx.dashboard_key,
        "read_only": ctx.read_only,
        "can_manage_users": ctx.can_manage_users,
        # WHICH PRODUCT this session is for, not what it may do. A platform
        # operator holds no grant in any organization, so `grants` is empty for
        # them -- and empty is exactly what an organization user with a broken
        # role assignment also looks like. Without this the SPA cannot tell a
        # SaaS operator from a customer's employee who has been granted
        # nothing, and would show the second one an empty HR app and the first
        # one the same empty HR app instead of the console.
        #
        # It grants nothing. Every platform route re-checks the flag on the
        # User row, and every tenant queryset still resolves to nothing for
        # them; a tampered snapshot changes which screens a browser draws, not
        # which requests succeed.
        "is_platform_admin": ctx.is_platform_admin,
        "layers": sorted(ctx.layers),
        "roles": sorted(ctx.role_codes),
        "grants": grants,
        # Entitlement rides along with authority rather than getting its own
        # endpoint, because they always change together -- a plan change and a
        # role change both alter what the SPA should render, and two fetches
        # would give it two moments to disagree with itself.
        #
        # A LIST of what is enabled, so absence means unavailable: the same
        # convention as `grants`, and the one the client already implements.
        # The negative form lives in the database, where absence has to mean
        # allowed; it is inverted here so the client has one rule.
        "features": _enabled_features(ctx),
        "organization_status": ctx.organization_status,
        "notice": (
            "Advisory only. The API independently enforces every permission on "
            "every request; this snapshot governs presentation, not access."
        ),
    }


def catalog_reference() -> dict:
    """
    The vocabulary the snapshot uses, so the SPA can be built against stable
    identifiers rather than hard-coded strings that silently drift from the
    backend. Served once, cached by the client.
    """
    return {
        "resources": sorted(Resource.values),
        "actions": sorted(Action.values),
        "scopes": [s.name.lower() for s in Scope],
    }
