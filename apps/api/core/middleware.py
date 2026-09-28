"""
Request-scoped context.

Binds the acting user and a request id to a ContextVar so `BaseModel.save()`
can attribute writes and the audit layer can correlate a chain of writes back
to one HTTP request — without threading `request` through every service call.

ContextVar (not thread-local) so this survives async views and Celery tasks.

ORGANIZATION CONTEXT IS WRITE-PATH ONLY
---------------------------------------
`_current_org` exists so a newly created row can be stamped with its owner and
so request-less callers (Celery, management commands) can scope their queries.
It is deliberately NOT how a request decides which organization it is reading.

That distinction is the whole lesson of HRMS-INC-20260717-01: the old system
read an ambient tenant variable that middleware had to have set first, and on
JWT requests it was read while still unset, so the manager failed open. Reads
now resolve the organization from `AccessContext`, which is derived from the
authenticated principal inside view dispatch -- see core/access/context.py.

So: never read `get_current_org_id()` to answer "whose data may this request
see". Ask the access context. This variable answers a narrower question --
"which organization is this code acting on behalf of right now" -- and its
absence is an error rather than a licence.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_current_user: ContextVar = ContextVar("hrms_current_user", default=None)
_request_id: ContextVar = ContextVar("hrms_request_id", default=None)
_current_org: ContextVar = ContextVar("hrms_current_org", default=None)


def get_current_user():
    return _current_user.get()


def get_current_org_id():
    """
    The organization this code is acting on behalf of, or None.

    None means "not established", never "all of them". Every caller treats it
    as an error condition -- a strict tenant manager raises on it -- because the one
    thing this must never do is quietly widen a query.
    """
    return _current_org.get()


@contextmanager
def organization_scope(organization):
    """
    Bind ONE organization for a block, then put back whatever was there.

    For a function that is HANDED an organization and reads its rows: the
    setup wizard, the mail and attendance resolvers, the seat check, the
    onboarding gate. From release 2 the database shows a confined connection
    only the bound organization, so a function that knows which organization
    it means has to say so -- and unlike `acting_as` this changes nothing
    else, which is what makes it safe to use inside a request that already has
    a principal.

    Scoped, never published: the caller's own binding is restored on the way
    out, so asking a question about one organization cannot move another
    caller's tenant.
    """
    org_id = getattr(organization, "pk", organization)
    token = _current_org.set(org_id)
    try:
        yield
    finally:
        _current_org.reset(token)


def set_current_org_id(org_id):
    """
    Bind the acting organization, returning the token needed to restore it.

    Called from the access layer once a context resolves, and from
    `acting_as()`. Not for general use: binding an organization by hand is how
    you end up with code that is correct only if it ran in the right order.
    """
    return _current_org.set(org_id)


def get_request_id():
    return _request_id.get()


@contextmanager
def acting_as(user, request_id=None, organization=None):
    """
    Run a block attributed to `user`, on behalf of `organization`.

    Required in Celery tasks and management commands, which have no request
    cycle — without it, writes are recorded with no actor and tenant-scoped
    queries have no organization to scope to.

    `organization` accepts a model instance or a bare id. Passing it is how a
    Celery task binds its tenant: the task receives an organization id as an
    ARGUMENT and enters this block with it. It must never inherit one, because
    Celery serialises arguments but not context variables — a task relying on
    inherited context runs under whatever the previous task on that worker
    left behind.

    Also resets `core.access.context`'s memoized context cache for the
    duration. That cache is keyed by user id, and outside a request it is
    never otherwise cleared; leaving it in place would let a block running as
    one principal, or for one organization, be served a context resolved for
    another.
    """
    from core.access import context as access_context

    org_id = getattr(organization, "pk", organization)
    if org_id is None and user is not None:
        # Derived from the principal, never guessed. Without this,
        # `acting_as(user)` -- the pre-tenancy signature, still used wherever
        # only attribution was wanted -- would bind None and actively UNBIND
        # whatever organization was in force, turning a helper that adds
        # context into one that silently removes it.
        from core.access.context import get_context

        org_id = get_context(user).organization_id

    user_token = _current_user.set(user)
    rid_token = _request_id.set(request_id or uuid.uuid4())
    org_token = _current_org.set(org_id)
    cache_token = access_context.reset_context_cache()
    try:
        yield
    finally:
        access_context.restore_context_cache(cache_token)
        _current_org.reset(org_token)
        _current_user.reset(user_token)
        _request_id.reset(rid_token)


class RequestContextMiddleware:
    """Binds request.user and a request id for the duration of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid.uuid4()
        request.request_id = request_id

        # NOTE: for JWT-authenticated requests `request.user` is still
        # AnonymousUser at middleware time — DRF resolves the token later, in
        # view dispatch. The lazy object here therefore resolves correctly at
        # *use* time, inside the view, which is the only place it is read.
        user_token = _current_user.set(request.user)
        rid_token = _request_id.set(request_id)
        # Deliberately NOT set here. At middleware time a JWT request is still
        # anonymous, so there is no principal to derive an organization from —
        # which is exactly the ordering that made the old tenant manager fail
        # open. The access layer binds it once it resolves a real context.
        org_token = _current_org.set(None)
        try:
            response = self.get_response(request)
        finally:
            _current_org.reset(org_token)
            _current_user.reset(user_token)
            _request_id.reset(rid_token)

        response["X-Request-ID"] = str(request_id)
        return response


def client_ip(request) -> str | None:
    """
    Best-effort client IP for audit records.

    Trusts X-Forwarded-For because the app runs behind Caddy. If it is ever
    exposed directly, this becomes spoofable — audit IPs would then be
    attacker-controlled.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
