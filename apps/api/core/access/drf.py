
"""
Scoped DRF view base classes.

`RBACPermission` deliberately lives in `permissions.py`, not here — see the
module docstring there for the import-cycle reason. Import it from this module
for convenience; it is re-exported below.
"""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.generics import GenericAPIView
from rest_framework.views import APIView

from .catalog import Action
from .engine import scope_queryset
from .permissions import (  # noqa: F401  (re-exported)
    DEFAULT_ACTION_MAP,
    METHOD_ACTION_MAP,
    RBACPermission,
    resolve_action,
    resolve_resource,
)


class ScopedQuerysetMixin:
    """
    Applies `scope_queryset()` to `get_queryset()`.

    This is what makes it impossible for a list endpoint to forget scoping. A
    Department Head hitting `/employees/` gets their department — not the whole
    organization — without the view author writing a single filter. The
    previous system left this to each view, and several got it wrong.
    """

    access_resource: str | None = None

    def scoping_action(self):
        """
        Which Action decides ROW VISIBILITY for this request.

        For standard CRUD it is the request's own action, so an EDIT at
        DEPARTMENT scope cannot reach a row outside that department even when
        VIEW is broader.

        For a custom `@action` it is VIEW. A custom route names a specific
        authority — `reject`, `feedback`, `convert` — that `RBACPermission` has
        already checked, and whose fine-grained rules the underlying service
        checks again. Scoping the lookup by that write action instead would ask
        a different question ("which interviews may this doctor CREATE?") and
        return 404 for the very row they are entitled to act on.
        """
        viewset_action = getattr(self, "action", None)
        if viewset_action and viewset_action not in DEFAULT_ACTION_MAP:
            return Action.VIEW
        return resolve_action(self, self.request)

    def get_queryset(self):
        qs = super().get_queryset()
        resource = getattr(self, "access_resource", None)
        if not resource:
            # Unreachable in practice: RBACPermission denies first and the
            # system check fails the build. Empty rather than unfiltered is the
            # right failure direction regardless.
            return qs.none()
        return scope_queryset(
            qs,
            self.request.user,
            resource=resource,
            action=self.scoping_action(),
        )

    def filter_queryset(self, queryset):
        """
        Apply the tenant predicate where NO override can step around it.

        `get_queryset()` is the obvious hook and the wrong one to rely on: nine
        viewsets override it, and several return a queryset built from scratch
        without calling `super()` -- reasonably, because their rows hang off a
        period or a job rather than a person, so the person-path scoper does
        not apply to them. Every one of those was returning rows from every
        organization.

        DRF routes both `list()` and `get_object()` through `filter_queryset()`,
        so putting the predicate here covers the overrides too. It is applied
        twice on the ordinary path -- once in `get_queryset()`, once here --
        which costs an idempotent `WHERE organization_id = %s` and removes a
        whole category of mistake.
        """
        from .context import get_context
        from .engine import apply_org_predicate

        queryset = apply_org_predicate(queryset, get_context(self.request))
        return super().filter_queryset(queryset)

    def scoped(self, qs, *, resource=None, action=None):
        """Scope an ad-hoc queryset inside a custom `@action`."""
        return scope_queryset(
            qs,
            self.request.user,
            resource=resource or self.access_resource,
            action=action or resolve_action(self, self.request),
        )


class ScopedAPIView(ScopedQuerysetMixin, APIView):
    """Base for non-CRUD endpoints."""


class ScopedGenericAPIView(ScopedQuerysetMixin, GenericAPIView):
    """Base for single-purpose list/detail endpoints."""


class ScopedModelViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    """
    Standard CRUD base.

    Subclasses MUST set `access_resource`; `manage.py check` fails otherwise.
    """


class ScopedReadOnlyModelViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only CRUD base."""


class ScopedListAPIView(ScopedQuerysetMixin, mixins.ListModelMixin, GenericAPIView):
    """List-only endpoint."""

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# The platform surface
# ---------------------------------------------------------------------------


class PlatformOnlyMixin:
    """
    Declares a view to be part of the PLATFORM domain, not the organization one.

    `platform_only = True` is the whole declaration, and `RBACPermission` reads
    it before anything else: the view is reachable only by a principal holding
    `User.is_platform_admin`, and no organization principal reaches it whatever
    roles their Admin invents at runtime.

    NOT `access_resource`, deliberately. Putting PLAN or ORGANIZATION in the
    permission catalogue would make them rows in the runtime-editable matrix,
    and an organization Admin editing their own roles could then grant
    themselves platform authority. The platform surface is therefore outside
    the matrix entirely -- the same reasoning that makes `is_read_only` a
    column on User rather than a role.

    NOT `access_exempt` either. That means "no RBAC at all", so a platform view
    that forgot its permission would be wide open AND invisible to the system
    check. `manage.py check` fails for a view declaring both.

    These bases carry NO tenant scoping, which is the point of the domain
    split: a platform view legitimately reads across organizations, because
    organizations are what it administers. It gets nowhere near their HR data,
    because none of those models is reachable from here without a resource
    grant the principal does not hold.
    """

    platform_only = True


class PlatformAPIView(PlatformOnlyMixin, APIView):
    """Base for platform endpoints that are not CRUD."""


class PlatformGenericAPIView(PlatformOnlyMixin, GenericAPIView):
    """Base for single-purpose platform list/detail endpoints."""


class PlatformReadOnlyModelViewSet(PlatformOnlyMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only platform CRUD."""


class PlatformModelViewSet(PlatformOnlyMixin, viewsets.ModelViewSet):
    """Platform CRUD."""
