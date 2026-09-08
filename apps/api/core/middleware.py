"""
Request-scoped context.

Binds the acting user and a request id to a ContextVar so `BaseModel.save()`
can attribute writes and the audit layer can correlate a chain of writes back
to one HTTP request — without threading `request` through every service call.

ContextVar (not thread-local) so this survives async views and Celery tasks.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_current_user: ContextVar = ContextVar("hrms_current_user", default=None)
_request_id: ContextVar = ContextVar("hrms_request_id", default=None)


def get_current_user():
    return _current_user.get()


def get_request_id():
    return _request_id.get()


@contextmanager
def acting_as(user, request_id=None):
    """
    Run a block attributed to `user`.

    Required in Celery tasks and management commands, which have no request
    cycle — without it, writes are recorded with no actor.
    """
    user_token = _current_user.set(user)
    rid_token = _request_id.set(request_id or uuid.uuid4())
    try:
        yield
    finally:
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
        try:
            response = self.get_response(request)
        finally:
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
