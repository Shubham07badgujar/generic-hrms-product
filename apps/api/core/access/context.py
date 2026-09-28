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
from dataclasses import dataclass, field, replace
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

    #: The organization this principal acts within. `None` means UNBOUND, and
    #: unbound means no rows -- never "all of them". Because `DENY_ALL` carries
    #: the default, the fail-closed path is the default object rather than a
    #: branch someone has to remember to write.
    organization_id: object | None = None

    #: Operator of the platform, not a member of any customer organization.
    #: Carries no grants and no organization, so every tenant queryset resolves
    #: to nothing for them. Their surface is /api/v1/platform/.
    is_platform_admin: bool = False

    role_codes: frozenset[str] = frozenset()
    layers: frozenset[int] = frozenset()
    read_only: bool = False
    can_manage_users: bool = False
    employee_id: object | None = None
    department_id: object | None = None
    department_ids: frozenset = frozenset()
    grants: Mapping[tuple[str, str], int] = field(default_factory=dict)
    dashboard_key: str = DashboardKey.SELF

    #: The organization's lifecycle status, carried EVEN ON A DENY so the
    #: refusal can say why. Without it a suspended customer and a user with no
    #: permission are the same 403, and the SPA cannot route one of them to a
    #: screen explaining what happened.
    organization_status: str = ""

    #: Features this organization's plan does NOT include.
    #:
    #: The negative form, matching `Plan.disabled_features`, and for the same
    #: reason: absence must mean ALLOWED. An organization with no subscription
    #: -- a self-hosted single-company install, which has no plans at all --
    #: gets an empty set and therefore the whole product, rather than nothing.
    #:
    #: This is the one place in the access layer that fails OPEN, and it is
    #: deliberate. A bug that lost the subscription would give a customer more
    #: modules; a bug in the tenant predicate would give them another
    #: customer's data. Only the second is a security failure, and the two
    #: should not be made to share a failure direction just for symmetry.
    disabled_features: frozenset[str] = frozenset()

    #: When the plan last narrowed, and for how long reads survive it. Reading
    #: and exporting a switched-off module's existing records stays available
    #: for this window, because a customer must be able to retrieve records
    #: they are statutorily obliged to keep.
    features_narrowed_at: object | None = None
    read_only_grace_days: int = 0

    @property
    def within_read_grace(self) -> bool:
        """Whether a disabled module is still readable and exportable."""
        if self.features_narrowed_at is None:
            # Never narrowed: the feature was absent from the start, so there
            # is nothing the customer entered under it to retrieve.
            return False
        deadline = self.features_narrowed_at + timezone.timedelta(
            days=self.read_only_grace_days
        )
        return timezone.now() <= deadline

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


def _active_membership(user):
    """
    The one membership that decides this principal's organization.

    One query, and the `uniq_one_active_membership` constraint is what makes
    `.first()` unambiguous rather than arbitrary.
    """
    from apps.organization.membership import active_membership

    return active_membership(user)


def _plan_state(organization_id) -> dict:
    """
    What the organization's plan permits, as three plain values.

    One query per request, joined to the plan, alongside the membership and
    grant queries that were already happening. Resolved HERE rather than in the
    permission class for the same reason the grants are: identity, authority
    and entitlement change together, and a request that checks thirty
    permissions should ask once.
    """
    from apps.platform.models import Subscription

    subscription = (
        Subscription.objects.filter(organization_id=organization_id, is_active=True)
        .select_related("plan")
        .first()
    )
    if subscription is None:
        return {}
    return {
        "disabled_features": frozenset(
            str(f) for f in (subscription.plan.disabled_features or [])
        ),
        "features_narrowed_at": subscription.features_narrowed_at,
        "read_only_grace_days": subscription.read_only_grace_days,
    }


def platform_admin_context(user) -> AccessContext:
    """
    A platform operator's context: no organization, no grants.

    Deliberately not "an organization context meaning everything". `grants` is
    empty, so `RBACPermission` denies every tenant route, and
    `organization_id` is None, so every tenant queryset resolves to nothing.
    The brief's rule -- Platform Admin gets no implicit access to customer HR
    data -- is therefore a property of the object rather than a policy someone
    enforces.

    `read_only` stays False: they are not restricted on the PLATFORM surface,
    where creating and suspending organizations is the whole job.
    """
    return AccessContext(user_id=user.pk, is_platform_admin=True)


def _department_closure(department_id, *, organization_id) -> frozenset:
    """
    A department plus all of its descendants. Cycle-safe.

    The organization is passed, not read from context. This runs INSIDE
    `resolve_context`, which is what produces the organization to bind -- so
    reading the bound one here would be circular, and on every path that
    resolves before binding (`force_authenticate`, a service calling
    `get_context(user)` outside a request) there is nothing bound yet. The
    principal's own membership already answered the question; use that.
    """
    if department_id is None:
        return frozenset()

    from apps.organization.models import Department

    visited = {department_id}
    queue = deque([department_id])
    while queue:
        current = queue.popleft()
        children = Department.objects.all_orgs().filter(
            organization_id=organization_id,
            parent_department_id=current,
            is_active=True,
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

    # 1.5 ORGANIZATION BINDING.
    #
    #     Derived from the resolved principal, never from an ambient variable.
    #     This is the structural answer to HRMS-INC-20260717-01: the old system
    #     read a tenant ContextVar that middleware had to have set first, and on
    #     JWT requests it was read while still unset, so the manager failed open
    #     and returned rows across organizations. Here the organization is a
    #     property of the same user object the permission decision is already
    #     being made from, so there is no ordering left to lose.
    #
    #     Placed BEFORE role resolution deliberately. A principal with no
    #     membership never reaches the grants query, so no mistake in the
    #     permission matrix can produce an organization-less context that still
    #     carries grants.
    if getattr(user, "is_platform_admin", False):
        return platform_admin_context(user)

    membership = _active_membership(user)
    if membership is None:
        logger.warning("access.no_membership user=%s", user.pk)
        return DENY_ALL
    if not membership.organization.is_operational:
        logger.warning(
            "access.org_not_operational user=%s org=%s status=%s",
            user.pk,
            membership.organization_id,
            membership.organization.status,
        )
        # DENY_ALL with the status attached, not bare DENY_ALL. The denial is
        # identical; what changes is that `OrganizationOperational` can now
        # tell a suspended customer apart from a user who simply lacks a
        # permission, and answer with a code the SPA can act on.
        return replace(
            DENY_ALL, organization_status=membership.organization.status
        )
    organization_id = membership.organization_id

    # 1.6 BIND IT, FOR THE REST OF THIS FUNCTION.
    #
    #     Everything below reads tenant-owned tables -- roles, permissions,
    #     overrides, the Employee record -- and from release 2 the runtime
    #     connects as a role that row-level security APPLIES to. With no
    #     organization bound in the database session those reads match no
    #     rows, so a principal would resolve to "no roles" and the product
    #     would deny everything. The membership above is what establishes the
    #     organization, and this is the first point at which it is known.
    #
    #     Scoped, not published: the token is restored on the way out, because
    #     `resolve_context` is also called ABOUT other people (a service asking
    #     `get_context(someone_else)`), and that must not move the caller's own
    #     tenant. Publishing for the request is `_bind`'s job, and it happens
    #     on the request path only.
    from core.middleware import _current_org, set_current_org_id

    org_token = set_current_org_id(organization_id)
    try:
        return _resolve_within_organization(user, membership, organization_id)
    finally:
        _current_org.reset(org_token)


def _resolve_within_organization(user, membership, organization_id) -> AccessContext:
    """
    The rest of `resolve_context`, with the organization bound.

    Split out for that binding alone -- see step 1.6. The queries below still
    name the organization explicitly; the binding is what lets the DATABASE
    return their rows, and the explicit filter is what keeps the answer right
    regardless.
    """
    plan_state = _plan_state(organization_id)

    from apps.accounts.models import RolePermission, UserPermissionOverride, UserRole

    # 2. Active role grants.
    #
    #    Steps 2, 5 and 6 name the organization EXPLICITLY, from the membership
    #    just resolved, rather than letting the tenant manager read it from
    #    context. This function is what PRODUCES the organization that gets
    #    bound, so on every path that resolves before binding (the admin
    #    sign-in door, force_authenticate, get_context(user) in a service) the
    #    ambient value is either absent or somebody else's. A grant row from
    #    another organization must never contribute authority here.
    grants_qs = (
        UserRole.objects.all_orgs()
        .filter(
            organization_id=organization_id,
            user_id=user.pk,
            is_active=True,
            role__is_active=True,
        )
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
    # An Employee record in a DIFFERENT organization is not this principal's
    # employee here, and must not lend them a department scope in this one.
    if employee is not None and employee.organization_id != organization_id:
        logger.warning(
            "access.employee_outside_membership user=%s", user.pk
        )
        employee = None
    if employee is not None and employee.is_active:
        employee_id = employee.pk
        department_id = employee.department_id

    # 5. Aggregate role permissions. MAX scope wins across roles.
    grants: dict[tuple[str, str], int] = {}
    for perm in RolePermission.objects.all_orgs().filter(
        organization_id=organization_id,
        role_id__in=[r.pk for r in roles],
        is_active=True,
    ):
        key = (perm.resource, perm.action)
        grants[key] = max(grants.get(key, Scope.NONE), perm.scope)

    # 6. Per-user overrides REPLACE the role-derived scope — they can widen or
    #    explicitly deny (scope=NONE). Expired ones are ignored.
    now = timezone.now()
    for override in UserPermissionOverride.objects.all_orgs().filter(
        organization_id=organization_id, user_id=user.pk, is_active=True
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
        organization_id=organization_id,
        organization_status=membership.organization.status,
        **plan_state,
        role_codes=frozenset(r.code for r in roles),
        layers=layers,
        read_only=read_only,
        can_manage_users=can_manage_users,
        employee_id=employee_id,
        department_id=department_id,
        department_ids=_department_closure(
            department_id, organization_id=organization_id
        ),
        grants=grants,
        dashboard_key=dashboard_key,
    )


def _requested_org_id(request) -> object | None:
    """
    The organization this request is ASKING for, distinct from the one its
    principal turns out to belong to.

    Always None in V1: a user has exactly one membership, so the organization
    is a function of the principal and a client cannot ask for another. It
    exists so the memo below is keyed on it from the start -- when multi-org
    arrives and the requested organization comes from a header or a token
    claim, a context resolved for organization A can never be served to a
    request for organization B. That is a three-line precaution against
    precisely the class of bug this design exists to prevent.
    """
    return None


def _bind(ctx: AccessContext) -> AccessContext:
    """
    Publish the resolved organization to the write-path ContextVar.

    Reads never consult that variable -- they use `ctx.organization_id`
    directly. This is what lets `OrgOwnedModel.save()` stamp a new row and
    the tenant manager scope a service-layer query without every service function
    growing an `organization=` argument, which is not viable across 386 service
    functions of which only a third even take an actor.

    ONLY CALLED ON THE REQUEST PATH, and that restriction is the point: this
    sets a ContextVar without resetting it, so it is safe only where something
    else owns the lifecycle. `RequestContextMiddleware` does -- it binds None on
    the way in and resets on the way out, whatever happens in between.

    Off the request path there is no such owner, so binding here would leak the
    organization into whatever the worker thread ran next. That is the same
    fault as the unreset `_ctx_cache` below, and it is why `acting_as()` -- which
    brackets and restores -- is the only sanctioned way to bind an organization
    outside a request.
    """
    from core.middleware import set_current_org_id

    set_current_org_id(ctx.organization_id)
    return ctx


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
        cached_key = getattr(request, "_access_context_key", False)
        current_key = (
            getattr(user, "pk", None) if user is not None else None,
            _requested_org_id(request),
        )
        if cached is not None and cached_key == current_key:
            return _bind(cached)
        ctx = resolve_context(user)
        request._access_context = ctx
        request._access_context_key = current_key
        return _bind(ctx)

    if user is None or not getattr(user, "is_authenticated", False):
        return DENY_ALL

    cache = _ctx_cache.get()
    if cache is None:
        cache = {}
        _ctx_cache.set(cache)
    if user.pk not in cache:
        cache[user.pk] = resolve_context(user)
    # Deliberately NOT bound: see `_bind`. Nothing here would reset it.
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
