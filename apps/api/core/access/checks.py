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

from .permissions import PLATFORM_PATH_PREFIX

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
    platform_only = getattr(view_class, "platform_only", False)
    access_exempt = getattr(view_class, "access_exempt", False)
    under_platform = f"/{path}".startswith(PLATFORM_PATH_PREFIX)

    if platform_only and access_exempt:
        return [
            Error(
                f"API view '{name}' (/{path}) declares both `platform_only` "
                f"and `access_exempt`.",
                hint=(
                    "`access_exempt` means no RBAC at all. A platform view "
                    "carrying it would be reachable by anyone if the platform "
                    "branch were ever reordered, and would be invisible to "
                    "this check. Drop `access_exempt`."
                ),
                id="access.E013",
                obj=view_class,
            )
        ]

    if under_platform and not platform_only:
        return [
            Error(
                f"API view '{name}' (/{path}) is under {PLATFORM_PATH_PREFIX} "
                f"but does not declare `platform_only = True`.",
                hint=(
                    "Inherit a Platform* base from core.access.drf. The URL "
                    "prefix is not the boundary -- the declaration is -- so a "
                    "view that sits there without it is an organization view "
                    "wearing a platform URL, and RBACPermission would look for "
                    "an `access_resource` it does not have."
                ),
                id="access.E011",
                obj=view_class,
            )
        ]

    if platform_only and not under_platform:
        return [
            Error(
                f"API view '{name}' (/{path}) declares `platform_only` but is "
                f"not under {PLATFORM_PATH_PREFIX}.",
                hint=(
                    "Keeping the two domains on disjoint URL prefixes is what "
                    "makes the boundary legible in a route table, a log line "
                    "and a proxy rule -- not only in Python. Move the route."
                ),
                id="access.E012",
                obj=view_class,
            )
        ]

    if platform_only:
        # Deliberately declares no resource: see PlatformOnlyMixin. The
        # authorization is the flag, and it was enforced above.
        return []

    if access_exempt:
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

    # An entry still on the backlog for a model that has since been converted
    # makes the backlog read longer than it is, which is how a list like this
    # stops being believed.
    for label in sorted(PENDING_TENANCY):
        if label in real_labels and is_tenanted(django_apps.get_model(*label.split("."))):
            errors.append(
                Error(
                    f"'{label}' is on the tenancy backlog but already carries "
                    f"an organization.",
                    hint="Remove it from PENDING_TENANCY.",
                    id="access.E007",
                    obj="core.access.tenancy",
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


@register()
def check_unique_constraints_are_organization_scoped(app_configs, **kwargs):
    """
    On a tenant-owned table, nothing is unique platform-wide by accident.

    Two companies must both be able to have an EMP001, a department coded HR,
    and a leave type called CL. A uniqueness rule that spans organizations does
    not merely inconvenience the second customer -- it TELLS them the value is
    taken, which leaks the existence of another tenant's data through an error
    message.

    A constraint passes if it names `organization`, or if it is unique through
    a relation that is itself organization-owned -- `(payroll_run, employee)`
    needs no organization column, because a PayrollRun belongs to exactly one.
    That rule is why this is a check and not a hand-maintained list: it stays
    true as models change.

    Anything genuinely global says so in `GLOBALLY_UNIQUE`, with a reason.
    """
    from django.apps import apps as django_apps
    from django.db.models import UniqueConstraint

    from .tenancy import (
        GLOBALLY_UNIQUE,
        SCOPED_THROUGH_USER,
        TENANT_EXEMPT,
        is_tenanted,
    )

    errors: list[Error] = []

    def scoped_by_relation(model, field_names) -> bool:
        """True when one of the fields points at an organization-owned table."""
        for fname in field_names:
            try:
                field = model._meta.get_field(fname)
            except Exception:  # noqa: BLE001 — expression, not a field
                continue
            related = getattr(field, "related_model", None)
            if related is not None and is_tenanted(related):
                return True
        return False

    for model in sorted(django_apps.get_models(), key=lambda m: m._meta.label):
        if model._meta.app_label not in FIRST_PARTY_APP_LABELS:
            continue
        if not is_tenanted(model) or model._meta.label in TENANT_EXEMPT:
            continue

        for field in model._meta.local_fields:
            if not getattr(field, "unique", False) or field.primary_key:
                continue
            label = f"{model._meta.label}.{field.name}"
            if (
                label in GLOBALLY_UNIQUE
                or label in SCOPED_THROUGH_USER
                or scoped_by_relation(model, [field.name])
            ):
                continue
            errors.append(
                Error(
                    f"'{label}' is unique across every organization.",
                    hint=(
                        "Drop `unique=True` and add a UniqueConstraint over "
                        "['organization', ...], or record it in "
                        "GLOBALLY_UNIQUE with the reason it must span tenants."
                    ),
                    id="access.E008",
                    obj=model,
                )
            )

        for constraint in model._meta.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            fields = list(constraint.fields or ())
            if not fields or "organization" in fields:
                continue
            name = f"{model._meta.label}.{constraint.name}"
            if name in GLOBALLY_UNIQUE or name in SCOPED_THROUGH_USER:
                continue
            if scoped_by_relation(model, fields):
                continue
            errors.append(
                Error(
                    f"'{model._meta.label}.{constraint.name}' is unique across "
                    f"every organization (fields={fields}).",
                    hint=(
                        "Add 'organization' to its fields, or record it in "
                        "GLOBALLY_UNIQUE with a reason."
                    ),
                    id="access.E008",
                    obj=model,
                )
            )

    return errors


@register()
def check_serializers_scope_their_relations(app_configs, **kwargs):
    """
    Every writable relation lookup is confined to one organization.

    `ModelSerializer` builds a `PrimaryKeyRelatedField(queryset=Model.objects
    .all())` for each writable foreign key -- automatically, invisibly, and
    around 77 times here. Each is a lookup across every organization's rows, so
    without scoping a POST naming another company's department id is accepted
    and the row is created pointing across the tenant boundary.

    The danger is that none of them is written down: `grep queryset=` finds a
    fraction, because DRF generates the rest. So this asks the running
    serializers rather than the source, and a new one cannot join without
    either the mixin or a deliberate answer.
    """
    import importlib
    import inspect
    import pathlib

    from rest_framework import serializers as drf

    from core.api.serializers import ScopedRelationsMixin

    errors: list[Error] = []
    root = pathlib.Path(__file__).resolve().parents[2]

    for path in sorted((root / "apps").rglob("*.py")):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        module_name = ".".join(path.relative_to(root).with_suffix("").parts)
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 — a module that will not import is
            continue       # someone else's check to fail
        for name, obj in vars(module).items():
            if not (
                inspect.isclass(obj)
                and issubclass(obj, drf.ModelSerializer)
                and obj is not drf.ModelSerializer
                and obj.__module__ == module_name
            ):
                continue
            try:
                fields = obj().get_fields()
            except Exception:  # noqa: BLE001 — needs context to build
                continue
            writable_relation = any(
                isinstance(f, (drf.PrimaryKeyRelatedField, drf.SlugRelatedField))
                and not f.read_only
                and getattr(f, "queryset", None) is not None
                for f in fields.values()
            )
            if writable_relation and not issubclass(obj, ScopedRelationsMixin):
                errors.append(
                    Error(
                        f"Serializer '{module_name}.{name}' has a writable "
                        f"relation whose lookup spans every organization.",
                        hint=(
                            "Add ScopedRelationsMixin to its bases, or declare "
                            "the field explicitly with a scoped queryset."
                        ),
                        id="access.E010",
                        obj=obj,
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
