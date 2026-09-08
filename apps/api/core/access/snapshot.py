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
        "layers": sorted(ctx.layers),
        "roles": sorted(ctx.role_codes),
        "grants": grants,
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
