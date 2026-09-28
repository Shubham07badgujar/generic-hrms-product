"""
The DRF permission class.

DELIBERATELY SEPARATE FROM `drf.py`
-----------------------------------
This module imports only `rest_framework.permissions.BasePermission`. It must
never import `rest_framework.viewsets`, `generics` or `views`.

Django settings name this class in `DEFAULT_PERMISSION_CLASSES`, and DRF
resolves that setting at import time from inside `rest_framework.views`. If
this module pulled in `viewsets`, the chain would be:

    rest_framework.views
      -> rest_framework.schemas  (evaluates DEFAULT_PERMISSION_CLASSES)
        -> core.access.permissions
          -> rest_framework.viewsets
            -> rest_framework.views   <-- still initialising, class not yet defined

which raises `ImportError: Module does not define a "RBACPermission" attribute`.
The view base classes live in `drf.py` for exactly this reason.
"""

from __future__ import annotations

import logging

from rest_framework.permissions import BasePermission

from .catalog import READ_ACTIONS, Action

logger = logging.getLogger("hrms.access")

#: DRF viewset action -> our Action verb.
DEFAULT_ACTION_MAP = {
    "list": Action.VIEW,
    "retrieve": Action.VIEW,
    "create": Action.CREATE,
    "update": Action.EDIT,
    "partial_update": Action.EDIT,
    "destroy": Action.DELETE,
}

#: Fallback for plain APIViews with no viewset action.
METHOD_ACTION_MAP = {
    "GET": Action.VIEW,
    "HEAD": Action.VIEW,
    "OPTIONS": Action.VIEW,
    "POST": Action.CREATE,
    "PUT": Action.EDIT,
    "PATCH": Action.EDIT,
    "DELETE": Action.DELETE,
}


def resolve_action(view, request) -> str:
    """
    Determine which Action a request represents.

    Precedence: the view's explicit `access_actions` map, then the viewset
    action, then `access_action`, then the HTTP method.

    Custom `@action` endpoints MUST declare themselves explicitly:
    `POST /applications/{id}/reject/` is `Action.REJECT`, not `Action.CREATE`.
    Falling through to the method map there would let anyone holding create
    rights on applications reject a candidate — an authority reserved to HR Head.
    """
    explicit = getattr(view, "access_actions", None) or {}
    viewset_action = getattr(view, "action", None)

    if viewset_action and viewset_action in explicit:
        return explicit[viewset_action]
    if request.method in explicit:
        return explicit[request.method]
    if viewset_action and viewset_action in DEFAULT_ACTION_MAP:
        return DEFAULT_ACTION_MAP[viewset_action]
    if getattr(view, "access_action", None):
        return view.access_action
    return METHOD_ACTION_MAP.get(request.method, Action.VIEW)


def resolve_resource(view, request) -> str | None:
    """
    Determine which Resource a request is about.

    Almost always `view.access_resource`. The exception is a viewset with an
    action that acts on a DIFFERENT resource: `POST /applications/{id}/recommend/`
    is a write on DEPARTMENT_DECISION, not on APPLICATION, because that is the
    permission the matrix actually grants a Department Head.

    Without this the route gate and the service's `require()` would check
    different things, and every mismatch is either a false 403 or a hole.
    """
    per_action = getattr(view, "access_resources", None) or {}
    viewset_action = getattr(view, "action", None)
    if viewset_action and viewset_action in per_action:
        return per_action[viewset_action]
    return getattr(view, "access_resource", None)


#: What a user who still holds a temporary password may reach. Everything
#: needed to REPLACE it and nothing else: sign in, keep the session alive,
#: sign out, learn who you are (the SPA needs this to route), and set the
#: new password. Deliberately tiny — every entry is a hole in the gate.
PASSWORD_GATE_ALLOWED_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/me/",
)

#: Where the platform domain lives, and the only place it may live.
#:
#: Two disjoint URL trees is the cheapest way to make the domain split legible
#: outside Python -- in a route table, a log line, an access log, a proxy rule.
#: `access.E011` and `access.E012` keep the tree and the `platform_only`
#: declaration in agreement in both directions.
#:
#: Note what is deliberately NOT here: the platform sign-in entrance. It lives
#: at /api/v1/auth/login/platform/ beside the other entrances, because a login
#: view cannot be `platform_only` -- there is no principal yet to hold the flag
#: -- and because that keeps this rule absolute, with no allowlist of platform
#: URLs that are somehow not platform views. It also means a freshly
#: bootstrapped platform admin carrying `must_change_password` can still reach
#: change-password, which is already inside the password gate's allowed prefix.
PLATFORM_PATH_PREFIX = "/api/v1/platform/"


class PasswordChangeRequired(BasePermission):
    """
    Refuse an authenticated request while the account still runs on a
    temporary password.

    This is what turns `must_change_password` from a hint the SPA may act on
    into a rule the API enforces: a person holding only the temporary secret
    can log in and change it, and cannot read a single record or perform a
    single action until they have. It sits BEFORE the RBAC check in the
    default chain, so it applies to every route, mapped or not, and it lives
    here rather than in middleware because JWT users are still anonymous at
    middleware time — the token is only resolved by the time DRF asks
    permissions.

    Anonymous requests pass straight through: `IsAuthenticated` decides those,
    and a login attempt must not be blocked by a flag on the account it is
    trying to reach.
    """

    message = (
        "Your account is using a temporary password. Choose a new password "
        "before continuing."
    )
    code = "password_change_required"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return True
        if not getattr(user, "must_change_password", False):
            return True
        if any(request.path.startswith(p) for p in PASSWORD_GATE_ALLOWED_PREFIXES):
            return True
        logger.info(
            "access.password_change_required user=%s path=%s", user.pk, request.path
        )
        return False


#: What a new joiner may reach while their MANDATORY onboarding items are
#: outstanding: sign in, learn who they are, work their onboarding, upload
#: and track their documents, and read their own profile. RBAC still applies
#: behind every one of these, so "allowed prefix" never means "allowed data".
ONBOARDING_GATE_ALLOWED_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/me/",
    "/api/v1/onboarding",          # /onboarding/, /onboarding-items/
    "/api/v1/employee-documents",  # upload, list own, see rejection reasons
    "/api/v1/document-types",
    "/api/v1/employees/",          # own profile (scope limits it to SELF)
    "/api/v1/notifications",
)


def onboarding_gate_applies(user) -> bool:
    """
    Whether this user is still behind the onboarding gate.

    True only for a person whose own checklist is IN PROGRESS with mandatory
    items outstanding. The people who RUN onboarding (ONBOARDING/EDIT at ALL
    — HR Head, HR Manager, Admin) are never gated: gating the only person
    able to approve the documents would deadlock the whole mechanism.
    """
    # BOUND to the user's own organization for the whole question. Every row
    # this reads -- the Employee, their checklist, its outstanding items --
    # belongs to that organization, and `/api/v1/me/` is `access_exempt`, so
    # nothing else binds one. From release 2 an unbound read returns nothing,
    # and "nothing" here means NOT GATED: a new joiner would be waved past the
    # onboarding they have not done. A gate that fails open is worse than no
    # gate, because the screen above it says the check ran.
    from apps.organization.membership import active_membership
    from core.middleware import organization_scope

    membership = active_membership(user)
    if membership is None:
        return False

    with organization_scope(membership.organization_id):
        employee = getattr(user, "employee", None)
        if employee is None:
            return False

        from core.access import Action, Resource, Scope
        from core.access.engine import can

        if can(user, Resource.ONBOARDING, Action.EDIT) >= Scope.ALL:
            return False

        onboarding = getattr(employee, "onboarding", None)
        if onboarding is None or onboarding.status != "in_progress":
            return False
        return onboarding.outstanding_mandatory.exists()


class OnboardingGate(BasePermission):
    """
    Restrict a new joiner to onboarding until their mandatory items are done.

    Same shape as the password gate above, for the same reason: this turns
    "the SPA redirects you to onboarding" from a suggestion into a rule the
    API enforces on every route. The moment the last mandatory item is
    completed or waived the checklist auto-closes, and this gate opens by
    itself — no flag to flip, no extra state to forget.
    """

    message = (
        "Complete your onboarding first: upload the required documents and "
        "wait for HR to approve them."
    )
    code = "onboarding_pending"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return True
        if any(request.path.startswith(p) for p in ONBOARDING_GATE_ALLOWED_PREFIXES):
            return True

        # One evaluation per request, however many permission checks run.
        cached = getattr(request, "_onboarding_gated", None)
        if cached is None:
            cached = onboarding_gate_applies(user)
            request._onboarding_gated = cached
        if cached:
            logger.info(
                "access.onboarding_pending user=%s path=%s", user.pk, request.path
            )
            return False
        return True


class RBACPermission(BasePermission):
    """
    Enforces `(resolved resource, resolved action)` against the caller.

    FAILS CLOSED. A view declaring neither `access_resource` nor
    `access_exempt = True` is denied and logged at ERROR.
    `core.access.checks` promotes that runtime denial to a build failure, so an
    unmapped endpoint breaks CI rather than quietly shipping unprotected.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        platform_view = getattr(view, "platform_only", False)
        platform_user = bool(getattr(user, "is_platform_admin", False))

        # Checked BEFORE `access_exempt`, so a platform view that also declared
        # itself exempt -- which `manage.py check` rejects, but belt and braces
        # -- is still gated rather than public.
        if platform_view:
            if not (user and user.is_authenticated and platform_user):
                logger.warning(
                    "access.platform_denied user=%s path=%s",
                    getattr(user, "pk", None),
                    request.path,
                )
                return False
            return True

        if getattr(view, "access_exempt", False):
            return True

        # A platform operator on an ORGANIZATION route.
        #
        # `resolve_context` already returns a context with no grants and no
        # organization for them, so `scope_for` below would refuse anyway and
        # every tenant queryset would resolve to nothing. This says it a second
        # time on purpose: "holds no grants" is a property somebody could
        # change by handing them a role, and the brief's rule -- a Platform
        # Admin gets no implicit access to customer HR data -- should not
        # depend on nobody ever doing that.
        if platform_user:
            logger.warning(
                "access.platform_admin_on_tenant_route user=%s path=%s",
                getattr(user, "pk", None),
                request.path,
            )
            return False

        resource = resolve_resource(view, request)
        if not resource:
            logger.error(
                "access.unmapped_view view=%s path=%s — denying. Set "
                "`access_resource`, or `access_exempt = True` if genuinely public.",
                view.__class__.__name__,
                request.path,
            )
            return False

        from .context import get_context

        action = resolve_action(view, request)
        if not get_context(request).scope_for(resource, action):
            logger.warning(
                "access.denied user=%s resource=%s action=%s view=%s",
                getattr(request.user, "pk", None),
                resource,
                action,
                view.__class__.__name__,
            )
            return False
        return True


#: What a SUSPENDED or CANCELLED organization's users may still reach: sign in,
#: learn who they are, and read the screen explaining what happened. Everything
#: else is refused with a code the SPA routes on.
#:
#: Deliberately tiny, and deliberately not "read everything". A suspended
#: organization's data is preserved, not published -- the customer gets it back
#: on restore or through an export, both of which are actions somebody takes,
#: not a side effect of the account still resolving.
SUSPENDED_ALLOWED_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/me/",
    "/api/v1/org/branding/",
    # The one exception to "preserved, not published", and a narrow one: the
    # route decides for itself, admitting only an Admin of a CANCELLED
    # organization inside the export window (and refusing SUSPENDED outright).
    # Export before deletion is a right; it is not a general read-through.
    "/api/v1/org/export/",
)


class OrganizationOperational(BasePermission):
    """
    Refuse a suspended, cancelled or archived organization -- and SAY SO.

    NOT MIDDLEWARE, and that is the whole point of it being here. A
    `SuspendedOrgMiddleware` would run before DRF resolved the JWT, see
    `AnonymousUser` on every token request, and wave every suspended
    organization straight through. That is the exact shape of
    HRMS-INC-20260717-01: a decision made before the principal exists, which is
    therefore made about nobody.

    `resolve_context` already denies these organizations by returning a
    grant-less context, so this class adds no authority -- it adds an ANSWER.
    Without it a suspended customer and a user who simply lacks a permission
    produce the same 403, and the SPA cannot tell one from the other.
    """

    message = (
        "This organization is not currently active. Contact your administrator."
    )
    code = "organization_suspended"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return True
        if getattr(user, "is_platform_admin", False):
            return True
        if any(request.path.startswith(p) for p in SUSPENDED_ALLOWED_PREFIXES):
            return True

        # Imported here, not at module scope: this module is named in
        # DEFAULT_PERMISSION_CLASSES and must stay free of heavy imports --
        # see the module docstring.
        from apps.organization.models import OPERATIONAL_STATUSES

        from .context import get_context

        status = get_context(request).organization_status
        if not status or status in OPERATIONAL_STATUSES:
            return True
        logger.info(
            "access.org_not_operational_route user=%s status=%s path=%s",
            user.pk, status, request.path,
        )
        return False


class FeatureEnabled(BasePermission):
    """
    Refuse a module the organization's plan does not include.

    THE ONLY FEATURE CHECK IN THE PRODUCT. It reads the resource
    `RBACPermission` has already resolved, looks it up in one map, and that is
    the entire mechanism -- so `if plan.has_payroll` never appears anywhere,
    and a module cannot be half-gated because somebody covered the list
    endpoint and forgot the export.

    GATES ON THE ACTION, NOT THE RESOURCE ALONE. When a customer downgrades off
    payroll, their PayrollRuns, Payslips and statutory records remain stored
    and unmodified; what stops is WRITING. Reading and exporting them stays
    available for the plan's grace window, because a customer must be able to
    retrieve records they are statutorily obliged to keep, and a downgrade that
    made last year's payslips unreachable would be a compliance problem the
    product created.

    Ordered AFTER `RBACPermission` in the chain, deliberately. A principal who
    has no permission on a resource should be told that, not told which modules
    their employer declined to buy -- the refusal reason is itself information,
    and the narrower one is the honest answer.
    """

    message = "Your plan does not include this module."
    code = "feature_not_available"

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return True

        # Platform views and genuinely public ones resolve no resource, so
        # there is nothing to look up -- which is correct: a customer's plan
        # must never govern the operator's own console.
        resource = resolve_resource(view, request)
        if not resource:
            return True

        from .features import feature_for

        feature = feature_for(resource)
        if feature is None:
            # Unmapped. `access.E014` fails the build for this, so reaching it
            # at runtime means the check was bypassed -- allow rather than
            # refuse, because a build-time omission should not take a customer
            # down mid-request.
            logger.error("access.unmapped_feature resource=%s", resource)
            return True

        from .context import get_context

        context = get_context(request)
        if str(feature) not in context.disabled_features:
            return True

        action = resolve_action(view, request)
        if action in READ_ACTIONS and context.within_read_grace:
            return True

        logger.info(
            "access.feature_disabled user=%s feature=%s action=%s path=%s",
            user.pk, feature, action, request.path,
        )
        return False
