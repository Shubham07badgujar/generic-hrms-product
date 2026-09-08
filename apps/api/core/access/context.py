"""
AccessContext — the resolved authority of one principal, for one request.

Resolution is deliberately linear and fail-closed: each step can only ever
*narrow* what the previous step allowed, and the two clamps that matter most
(read-only, user-management) run last so nothing downstream can undo them.
"""

from __future__ import annotations

import logging
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property
from typing import Mapping

from django.utils import timezone

from .catalog import (
    READ_ACTIONS,
    USER_MANAGEMENT_RESOURCES,
    Action,
    DashboardKey,
    Resource,
    Scope,
)

logger = logging.getLogger("hrms.access")

#: Per-request memo, so a request that checks 30 permissions runs the
#: resolution queries once rather than 30 times.
_ctx_cache: ContextVar[dict] = ContextVar("hrms_access_ctx", default=None)


@dataclass(frozen=True)
class AccessContext:
    """Immutable snapshot of what a principal may do."""

    user_id: object | None = None
    role_codes: frozenset[str] = frozenset()
    layers: frozenset[int] = frozenset()
    read_only: bool = False
    can_manage_users: bool = False
    employee_id: object | None = None
    department_id: object | None = None
    department_ids: frozenset = frozenset()
    grants: Mapping[tuple[str, str], int] = field(default_factory=dict)
    dashboard_key: str = DashboardKey.SELF

    @property
    def min_layer(self) -> int:
        """Highest authority held. 99 when the principal holds no role."""
        return min(self.layers) if self.layers else 99

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    def scope_for(self, resource: str, action: str = Action.VIEW) -> Scope:
        """Breadth this principal has for `(resource, action)`. NONE when denied."""
        return Scope(self.grants.get((str(resource), str(action)), Scope.NONE))

    def has(self, resource: str, action: str = Action.VIEW) -> bool:
        return bool(self.scope_for(resource, action))

    @cached_property
    def reporting_tree_ids(self) -> frozenset:
        """
        Every employee at or below this principal in the reporting tree.

        Computed at most once per request. Breadth-first with a `visited` set,
        so a cycle in `reporting_manager` (which the schema permits) terminates
        instead of hanging.
        """
        if self.employee_id is None:
            return frozenset()

        from apps.employees.models import Employee

        visited = {self.employee_id}
        frontier = {self.employee_id}
        while frontier:
            children = set(
                Employee.objects.filter(reporting_manager_id__in=frontier)
                .exclude(pk__in=visited)
                .values_list("pk", flat=True)
            )
            if not children:
                break
            visited |= children
            frontier = children
        return frozenset(visited)


#: Returned for anonymous, inactive, or role-less principals. Frozen and shared
#: — there is exactly one way to be denied everything.
DENY_ALL = AccessContext()


def _department_closure(department_id) -> frozenset:
    """A department plus all of its descendants. Cycle-safe."""
    if department_id is None:
        return frozenset()

    from apps.organization.models import Department

    visited = {department_id}
    queue = deque([department_id])
    while queue:
        current = queue.popleft()
        children = Department.objects.filter(
            parent_department_id=current, is_active=True
        ).values_list("pk", flat=True)
        for child in children:
            if child not in visited:
                visited.add(child)
                queue.append(child)
    return frozenset(visited)


def resolve_context(user) -> AccessContext:
    """
    Resolve a user's full authority.

    Ordering is load-bearing. Read the numbered steps before changing anything.
    """
    # 1. Anonymous or deactivated principals get nothing.
    if user is None or not getattr(user, "is_authenticated", False):
        return DENY_ALL
    if not getattr(user, "is_active", False):
        return DENY_ALL

    from apps.accounts.models import RolePermission, UserPermissionOverride, UserRole

    # 2. Active role grants.
    grants_qs = (
        UserRole.objects.filter(user_id=user.pk, is_active=True, role__is_active=True)
        .select_related("role")
    )
    roles = [g.role for g in grants_qs]

    # 3. A user with no role has no access. Not "default employee access" —
    #    none. Provisioning a login without a role must fail visibly.
    if not roles:
        logger.info("access.no_roles user=%s", user.pk)
        return DENY_ALL

    read_only = any(r.is_read_only for r in roles)
    can_manage_users = any(r.can_manage_users for r in roles)
    # ALL, not ANY: holding one org-level role (admin/ceo) exempts the
    # principal from needing an Employee record.
    requires_employee = all(r.requires_employee for r in roles)
    layers = frozenset(r.layer for r in roles)

    # Lowest layer number wins the dashboard, so someone holding both a
    # department-head and a manager role lands on the more senior view.
    dashboard_key = min(roles, key=lambda r: r.layer).dashboard_key or DashboardKey.SELF

    # 4. Department scope derives from the linked Employee — never from the
    #    role — so moving someone between departments changes their access
    #    without re-granting anything.
    employee_id = None
    department_id = None
    employee = getattr(user, "employee", None)
    if employee is not None and employee.is_active:
        employee_id = employee.pk
        department_id = employee.department_id

    # 5. Aggregate role permissions. MAX scope wins across roles.
    grants: dict[tuple[str, str], int] = {}
    for perm in RolePermission.objects.filter(
        role_id__in=[r.pk for r in roles], is_active=True
    ):
        key = (perm.resource, perm.action)
        grants[key] = max(grants.get(key, Scope.NONE), perm.scope)

    # 6. Per-user overrides REPLACE the role-derived scope — they can widen or
    #    explicitly deny (scope=NONE). Expired ones are ignored.
    now = timezone.now()
    for override in UserPermissionOverride.objects.filter(
        user_id=user.pk, is_active=True
    ).exclude(expires_at__lt=now):
        grants[(override.resource, override.action)] = override.scope

    # 7. Fail closed when a role needs an Employee and there isn't one.
    #    Without this, a half-provisioned account resolves to department/team
    #    scopes with no department and no team — i.e. undefined behaviour.
    if employee_id is None:
        if requires_employee:
            logger.info("access.no_employee_record user=%s", user.pk)
            grants = {}
        else:
            # admin/ceo: organization-wide only. A system role must never
            # resolve a department or team scope it has no basis for.
            grants = {k: v for k, v in grants.items() if v == Scope.ALL}

    # 8. READ-ONLY CLAMP. Unconditional, and last among the grant filters, so
    #    no RolePermission row, no override, and no future code path can give a
    #    read-only principal (the CEO) a write.
    if read_only:
        grants = {k: v for k, v in grants.items() if k[1] in READ_ACTIONS}
        can_manage_users = False

    # 9. USER-MANAGEMENT GATE. A mistake in the permission matrix alone can
    #    never confer account control; the role must also carry the flag.
    #
    #    Strips WRITE actions only. Viewing who holds an account is not
    #    managing users — the CEO is required to "view all employees and users"
    #    while being unable to create, edit or delete any of them. Gating reads
    #    here as well would make those two requirements contradictory.
    if not can_manage_users:
        grants = {
            k: v
            for k, v in grants.items()
            if k[0] not in USER_MANAGEMENT_RESOURCES or k[1] in READ_ACTIONS
        }

    return AccessContext(
        user_id=user.pk,
        role_codes=frozenset(r.code for r in roles),
        layers=layers,
        read_only=read_only,
        can_manage_users=can_manage_users,
        employee_id=employee_id,
        department_id=department_id,
        department_ids=_department_closure(department_id),
        grants=grants,
        dashboard_key=dashboard_key,
    )


def get_context(user_or_request) -> AccessContext:
    """Resolve with per-request memoization."""
    request = None
    user = user_or_request
    if hasattr(user_or_request, "user"):
        request = user_or_request
        user = user_or_request.user

    if request is not None:
        # The cache is keyed by the user it was resolved FOR, not merely by the
        # request. This is load-bearing: ReadOnlyPrincipalMiddleware resolves a
        # context during middleware, where a JWT request still carries
        # AnonymousUser because DRF authenticates later, inside view dispatch.
        # A request-only cache would pin that anonymous DENY_ALL for the whole
        # request and refuse every authenticated API call.
        cached = getattr(request, "_access_context", None)
        cached_for = getattr(request, "_access_context_user_id", False)
        current_user_id = getattr(user, "pk", None) if user is not None else None
        if cached is not None and cached_for == current_user_id:
            return cached
        ctx = resolve_context(user)
        request._access_context = ctx
        request._access_context_user_id = current_user_id
        return ctx

    if user is None or not getattr(user, "is_authenticated", False):
        return DENY_ALL

    cache = _ctx_cache.get()
    if cache is None:
        cache = {}
        _ctx_cache.set(cache)
    if user.pk not in cache:
        cache[user.pk] = resolve_context(user)
    return cache[user.pk]


def reset_context_cache():
    """
    Start a fresh memo scope, returning the token that restores the old one.

    `_ctx_cache` is a ContextVar that was previously set and never reset. Inside
    a request that is harmless -- the request-attribute cache above is used
    instead -- but a WSGI worker thread or a Celery worker reuses its context
    across requests and tasks, so entries survived into work they were never
    resolved for. That was already wrong for role grants; once a context also
    carries an organization it is a stale TENANT, which is the failure this
    whole design exists to prevent.

    `acting_as()` brackets every request-less block with this.
    """
    return _ctx_cache.set(None)


def restore_context_cache(token) -> None:
    """Undo `reset_context_cache()`."""
    _ctx_cache.reset(token)


def invalidate(user_id=None) -> None:
    """
    Drop memoized contexts.

    Call after granting or revoking a role, or editing permissions, so the
    change takes effect within the same request rather than the next one.
    """
    cache = _ctx_cache.get()
    if cache is None:
        return
    if user_id is None:
        cache.clear()
    else:
        cache.pop(user_id, None)
