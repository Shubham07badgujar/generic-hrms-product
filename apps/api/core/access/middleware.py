"""
ReadOnlyPrincipalMiddleware — the CEO backstop.

One of four independent mechanisms enforcing read-only. This one exists
specifically to cover the route a developer adds next month and forgets to map:
even with no `access_resource`, no decorator and no service-level `require()`,
an unsafe HTTP method from a read-only principal is refused here.

The other three:
  1. the engine's read-only clamp (context.resolve_context step 8)
  2. `CheckConstraint(~Q(is_read_only=True, can_manage_users=True))` on Role
  3. `require()` at the top of every mutating service function

No single one of them is trusted alone.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

logger = logging.getLogger("hrms.access")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths a read-only principal must still be able to POST to, or they could
#: not log in or out. Deliberately tiny — every entry is a hole.
EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/bootstrap/",
    "/admin/",  # Django admin has its own superuser gate
)


class ReadOnlyPrincipalMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in UNSAFE_METHODS and not self._is_exempt(request.path):
            # Resolved lazily: for JWT requests request.user is still
            # AnonymousUser here, which yields DENY_ALL — read_only is False,
            # so we correctly fall through and let RBACPermission decide inside
            # the view, where the token has been resolved.
            from .context import get_context

            ctx = get_context(request)
            if ctx.read_only:
                logger.warning(
                    "access.readonly_blocked user=%s method=%s path=%s",
                    ctx.user_id,
                    request.method,
                    request.path,
                )
                return JsonResponse(
                    {
                        "error": {
                            "code": "read_only_principal",
                            "message": (
                                "Your role has view-only access. This action "
                                "modifies data and is not permitted."
                            ),
                        }
                    },
                    status=403,
                )
        return self.get_response(request)

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)
