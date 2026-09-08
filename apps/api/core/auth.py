"""
JWT authentication that binds the acting user into the request context.

WHY THIS EXISTS
---------------
`RequestContextMiddleware` captures `request.user` into a ContextVar so
`BaseModel.save()` can attribute writes and the audit layer can name an actor.
For session auth that works, because AuthenticationMiddleware resolves the user
before our middleware runs.

For JWT it does not: the Authorization header is processed by DRF *inside* view
dispatch, long after middleware. The ContextVar would hold the lazy
AnonymousUser for the whole request, and every write made through the API would
be recorded with no actor — an audit trail of ghosts.

This subclass closes the gap at the only correct point: the moment the token is
actually verified. No reset is needed here; RequestContextMiddleware owns the
ContextVar lifecycle and resets it (to the pre-request state, discarding any
intermediate set) when the response leaves.

This mirrors the failure mode behind the old system's cross-tenant incident —
middleware trusting a user that token auth had not yet resolved. Same lesson,
applied to attribution instead of tenancy.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication

from core.middleware import _current_user


class ContextBindingJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _token = result
            _current_user.set(user)
        return result
