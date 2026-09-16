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

THE ORGANIZATION IS BOUND HERE TOO
----------------------------------
`get_context()` binds the organization for every route that goes through
`RBACPermission` — which is every route except the handful declaring
`access_exempt`. `/api/v1/me/` is one of those: a user may always read their own
identity, so no permission class runs, so nothing bound a tenant. Its serializer
then asks whether onboarding is outstanding, and the first app whose manager
filtered turned that into a 500 on sign-in.

This is NOT the shape that caused HRMS-INC-20260717-01. That incident was
middleware setting tenant context BEFORE the token was resolved, so the variable
stayed unset and the manager failed open. Here the organization is derived from
the principal the token just proved, at the moment it is proved, and the read
path still resolves its own organization from `AccessContext` rather than
trusting this value. What is bound here serves the write path and the
request-less callers — stamping a new row, and managers that need to know whose
data this is.

A principal with no membership — a platform admin — leaves it unbound, which is
the correct answer for someone who belongs to no organization.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication

from core.middleware import _current_org, _current_user


class ContextBindingJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _token = result
            _current_user.set(user)
            self._bind_organization(user)
        return result

    @staticmethod
    def _bind_organization(user) -> None:
        """
        Bind the tenant this principal belongs to, if any.

        One indexed lookup on the membership table. `RequestContextMiddleware`
        set the variable to None on the way in and resets it on the way out, so
        nothing bound here can outlive its request.
        """
        from apps.organization.membership import organization_of

        organization = organization_of(user)
        if organization is not None:
            _current_org.set(organization.pk)
