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
#: The apps whose models this project owns. Third-party tables (Django's own,
#: celery-beat, token blacklist) are not ours to tenant.
FIRST_PARTY_APP_LABELS = frozenset(
    {
        "accounts", "organization", "employees", "assets", "itaccounts",
        "workflows", "recruitment", "onboarding", "offboarding", "attendance",
        "leave", "payroll", "policies", "notifications", "reporting",
        "imports", "audit", "statutory",
    }
)

EXEMPT_VIEW_NAMES = {
    "TokenObtainView",          # login: no principal exists yet
    "TokenRefreshView",         # refresh: authenticated by the cookie itself
    "LogoutView",
    "AdminBootstrapView",       # token + IP gated; creates the first Admin
    "HealthCheckView",
    "SpectacularAPIView",
    "SpectacularSwaggerView",
    "SpectacularRedocView",
    # DRF generates one APIRootView per DefaultRouter, listing every route it
    # owns. It declares no resource, so RBACPermission denies it for every
    # principal and an anonymous caller gets 401 -- which is the right outcome
    # for a route index. Listed here because it is third-party code we cannot
    # annotate, not because it is reachable.
    "APIRootView",
}


def check_one_view(view_class, path: str) -> list[Error]:
    """
    Verdict for a single API view.

    Split out of the resolver walk below so it can be exercised directly. A
    check that is registered but has quietly become a no-op still reports
    success, so `tests/core/test_system_checks.py` calls this with a
    deliberately unmapped view and asserts it complains.
    """
    from .registry import RESOURCE_SPECS

    name = view_class.__name__

    if getattr(view_class, "access_exempt", False):
        return []

    resource = getattr(view_class, "access_resource", None)
    if not resource:
        return [
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
        ]

    if str(resource) not in RESOURCE_SPECS:
        return [
            Error(
                f"API view '{name}' declares unknown resource "
                f"'{resource}'.",
                hint="Add a ResourceSpec in core/access/registry.py.",
                id="access.E002",
                obj=view_class,
            )
        ]

    return []


@register()
def check_tenancy_coverage(app_configs, **kwargs):
    """
    Every first-party model is tenant-scoped, deliberately global, or on the
    named backlog. Nothing may be none of the three.

    This is what stops the conversion from stalling silently. A model that is
    not yet scoped is UNPROTECTED, and the only acceptable version of that is
    one somebody wrote down: `PENDING_TENANCY` is the backlog, it shrinks as
    apps convert, and a NEW model cannot join it by accident -- it fails the
    build until its author decides which of the three it is.
    """
    from django.apps import apps as django_apps

    from .tenancy import PENDING_TENANCY, TENANT_EXEMPT, is_tenanted

    errors: list[Error] = []
    first_party = {
        m
        for m in django_apps.get_models()
        if m._meta.app_label in FIRST_PARTY_APP_LABELS
    }
    real_labels = {m._meta.label for m in first_party}

    for model in sorted(first_party, key=lambda m: m._meta.label):
        label = model._meta.label
        if is_tenanted(model) or label in TENANT_EXEMPT or label in PENDING_TENANCY:
            continue
        errors.append(
            Error(
                f"Model '{label}' is neither tenant-scoped nor declared global.",
                hint=(
                    "Inherit OrgOwnedModel so it carries an organization, or "
                    "add it to TENANT_EXEMPT in core/access/tenancy.py with a "
                    "reason. A model that is silently unscoped is a "
                    "cross-tenant leak waiting for its first query."
                ),
                id="access.E005",
                obj=model,
            )
        )

    # A backlog entry naming a model that no longer exists hides the fact that
    # the real one is unprotected -- and leaves a dead name in a security list,
    # which is how `CredentialHandoff` sat in the view allow-list for a view
    # that had been deleted.
    for label in sorted((PENDING_TENANCY | set(TENANT_EXEMPT)) - real_labels):
        errors.append(
            Error(
                f"'{label}' is named in core/access/tenancy.py but no such "
                f"model exists.",
                hint="Remove the stale entry.",
                id="access.E006",
                obj="core.access.tenancy",
            )
        )

    return errors


@register(Tags.urls)
def check_all_api_views_are_mapped(app_configs, **kwargs):
    """Every DRF view under /api/ must declare access_resource or opt out."""
    from django.urls import get_resolver

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

            errors.extend(check_one_view(view_class, path))

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
