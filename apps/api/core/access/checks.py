"""
Build-time guarantees.

Turns "we remembered to protect every endpoint" from a hope into a check that
fails `manage.py check`, and therefore CI and the deploy.

This is the single most important structural difference from the previous
system, where authorization was ~78 hand-written checks with nothing verifying
that a new view had one.
"""

from __future__ import annotations

from django.core.checks import Error, Tags, register

#: Views legitimately reachable without a permission mapping — login, token
#: refresh, the one-time credential handoff, health checks. Every entry needs a
#: comment justifying it.
EXEMPT_VIEW_NAMES = {
    "TokenObtainView",          # login: no principal exists yet
    "TokenRefreshView",         # refresh: authenticated by the cookie itself
    "LogoutView",
    "AdminBootstrapView",       # token + IP gated; creates the first Admin
    "CredentialHandoffView",    # one-time token in the URL is the credential
    "HealthCheckView",
    "SpectacularAPIView",
    "SpectacularSwaggerView",
    "SpectacularRedocView",
}


@register(Tags.urls)
def check_all_api_views_are_mapped(app_configs, **kwargs):
    """Every DRF view under /api/ must declare access_resource or opt out."""
    from django.urls import get_resolver

    from .registry import RESOURCE_SPECS

    errors: list[Error] = []
    seen: set[str] = set()

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
                continue

            callback = getattr(pattern, "callback", None)
            if callback is None:
                continue

            view_class = getattr(callback, "cls", None) or getattr(
                callback, "view_class", None
            )
            if view_class is None:
                continue

            path = prefix + str(pattern.pattern)
            if not path.startswith("api/"):
                continue

            name = view_class.__name__
            if name in seen or name in EXEMPT_VIEW_NAMES:
                continue
            seen.add(name)

            if getattr(view_class, "access_exempt", False):
                continue

            resource = getattr(view_class, "access_resource", None)
            if not resource:
                errors.append(
                    Error(
                        f"API view '{name}' (/{path}) does not declare "
                        f"`access_resource`.",
                        hint=(
                            "Set `access_resource = Resource.X` on the view, or "
                            "`access_exempt = True` if it is genuinely public. "
                            "Unmapped views are denied at runtime."
                        ),
                        id="access.E001",
                        obj=view_class,
                    )
                )
            elif str(resource) not in RESOURCE_SPECS:
                errors.append(
                    Error(
                        f"API view '{name}' declares unknown resource "
                        f"'{resource}'.",
                        hint="Add a ResourceSpec in core/access/registry.py.",
                        id="access.E002",
                        obj=view_class,
                    )
                )

    walk(get_resolver().url_patterns)
    return errors


@register()
def check_every_resource_has_a_spec(app_configs, **kwargs):
    """Every Resource must be wired in the registry, or it can never be scoped."""
    from .registry import missing_resources

    missing = missing_resources()
    if not missing:
        return []
    return [
        Error(
            f"Resources with no ResourceSpec: {', '.join(missing)}.",
            hint=(
                "Add each to RESOURCE_SPECS in core/access/registry.py. Without "
                "a spec the engine denies every sub-ALL scope for that resource."
            ),
            id="access.E003",
        )
    ]


@register()
def check_role_invariants(app_configs, **kwargs):
    """
    A read-only role must never also manage users.

    The database enforces this with a CheckConstraint; this surfaces it at
    check time with a readable message instead of an IntegrityError at runtime.
    """
    from django.db import OperationalError, ProgrammingError

    try:
        from apps.accounts.models import Role

        bad = list(
            Role.objects.filter(is_read_only=True, can_manage_users=True).values_list(
                "code", flat=True
            )
        )
    except (OperationalError, ProgrammingError, ImportError):
        # Table not migrated yet — nothing to validate.
        return []

    if not bad:
        return []
    return [
        Error(
            f"Roles are both read-only and user-managing: {', '.join(bad)}.",
            hint="A view-only principal must never be able to create accounts.",
            id="access.E004",
        )
    ]
