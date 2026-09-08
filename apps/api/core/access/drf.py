
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
