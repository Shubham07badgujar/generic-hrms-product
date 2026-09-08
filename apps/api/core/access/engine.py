"""
The authorization engine — the ONLY sanctioned way to make an access decision.

Four public functions:

    can(user, resource, action)                 -> Scope    (falsy when denied)
    require(user, resource, action)             -> Scope    (raises when denied)
    scope_queryset(qs, user, resource=, action=) -> QuerySet (narrowed to scope)
    get_scoped_object(user, resource, model, **lookup) -> instance (404 if outside)

Nothing else in the codebase may hand-roll a scope filter or a role check. The
previous system had ~78 inline role checks across 25 files, each free to get it
subtly wrong, and several did.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import Http404

from .catalog import Action, Resource, Scope
from .context import get_context
from .registry import RESOURCE_SPECS

logger = logging.getLogger("hrms.access")


class AccessDenied(PermissionDenied):
    """
    Raised when a principal lacks the required scope.

    Subclasses Django's PermissionDenied so DRF renders it as 403 and plain
    Django views handle it too.
    """

    def __init__(self, resource: str, action: str, detail: str | None = None):
        self.resource = resource
        self.action = action
        super().__init__(
            detail or f"You do not have permission to {action} {resource}."
        )


def can(user, resource: str, action: str = Action.VIEW) -> Scope:
    """
    Breadth this user has for `(resource, action)`.

    Returns `Scope.NONE` (falsy) when denied, so `if can(...)` works while the
    value still carries the breadth needed for filtering.
    """
    return get_context(user).scope_for(resource, action)


def allows(user, resource: str, action: str = Action.VIEW) -> bool:
    return bool(can(user, resource, action))


def require(user, resource: str, action: str = Action.VIEW) -> Scope:
    """
    Assert permission, returning the granted scope.

    Call at the top of every mutating service function. Services are reachable
    from management commands and Celery tasks as well as HTTP, so a check that
    lives only in a view is not a check.
    """
    scope = can(user, resource, action)
    if not scope:
        logger.warning(
            "access.denied user=%s resource=%s action=%s",
            getattr(user, "pk", None),
            resource,
            action,
        )
        raise AccessDenied(resource, action)
    return scope


def scope_queryset(
    qs: QuerySet,
    user,
    *,
    resource: str,
    action: str = Action.VIEW,
    scope: int | None = None,
) -> QuerySet:
    """
    Narrow `qs` to what this user may see.

    Returns `qs.none()` rather than raising, because list endpoints should show
    an empty list, not an error — an error would leak that rows exist.

    `scope` overrides the reach WITHOUT changing which resource's path is used
    to apply it, for the case where the two genuinely differ: "which employees
    may I file a document against" is the EMPLOYEE_DOCUMENT/CREATE reach walked
    over EMPLOYEE rows. Callers pass a scope they obtained from `can()`, so this
    narrows by a real grant and can never widen beyond one — but it is easy to
    misread, so it stays keyword-only and rare. Everything else omits it.
    """
    ctx = get_context(user)
    if scope is None:
        scope = ctx.scope_for(resource, action)

    if not scope:
        return qs.none()
    if scope == Scope.ALL:
        return qs

    spec = RESOURCE_SPECS.get(str(resource))
    if spec is None:
        # A resource nobody wired up. Fail closed and shout — this is a bug,
        # and the system check should have caught it before deploy.
        logger.error("access.no_resource_spec resource=%s", resource)
        return qs.none()

    # Sub-ALL scopes are only meaningful for resources that belong to a person.
    # A PayrollRun has no owning employee, so "department scope" over payroll
    # runs is undefined — deny rather than guess.
    if not spec.person_scoped:
        logger.info(
            "access.sub_all_scope_on_unscoped_resource resource=%s scope=%s",
            resource,
            scope.name,
        )
        return qs.none()

    if ctx.employee_id is None:
        return qs.none()

    path = spec.employee_path

    if scope == Scope.DEPARTMENT:
        if not ctx.department_ids:
            return qs.none()
        lookup = (
            "department_id__in" if path == "" else f"{path}__department_id__in"
        )
        return qs.filter(**{lookup: ctx.department_ids})

    if scope == Scope.TEAM:
        ids = ctx.reporting_tree_ids
        if not ids:
            return qs.none()
        lookup = "id__in" if path == "" else f"{path}_id__in"
        return qs.filter(**{lookup: ids})

    # Scope.SELF
    lookup = "id" if path == "" else f"{path}_id"
    return qs.filter(**{lookup: ctx.employee_id})


def get_scoped_object(user, resource: str, model, *, action: str = Action.VIEW, **lookup):
    """
    Fetch one object, or raise 404 if it is outside the caller's scope.

    404 rather than 403 on purpose: a 403 confirms the row exists, which is an
    enumeration oracle. An out-of-scope object should be indistinguishable from
    one that does not exist.
    """
    qs = scope_queryset(model.objects.all(), user, resource=resource, action=action)
    try:
        return qs.get(**lookup)
    except model.DoesNotExist as exc:
        raise Http404(f"No {model.__name__} matches the given query.") from exc


def visible_scope_label(user, resource: str, action: str = Action.VIEW) -> str:
    """Human-readable scope, for showing users why a list looks the way it does."""
    return can(user, resource, action).label
