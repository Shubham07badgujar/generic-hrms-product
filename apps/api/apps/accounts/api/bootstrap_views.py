
"""
The Postman-callable Admin bootstrap endpoint.

Layered so that no single failure exposes it:

  1. The ROUTE ONLY EXISTS when ADMIN_BOOTSTRAP_TOKEN is set. Unset, the URL is
     a genuine 404 — not a 403 — so once bootstrapping is done the endpoint
     cannot be probed, fingerprinted, or brute-forced. It is absent, not guarded.
  2. IP allowlist.
  3. Constant-time token comparison.
  4. One-shot: refuses once any active Admin exists.
  5. Throttled.
  6. Audited, with the source IP.
  7. NO PASSWORD IN THE REQUEST OR RESPONSE. The account is created unusable;
     the password is set out of band. A leaked token therefore yields an account
     nobody can sign into.

Every rejection returns an identical 403 body, so the endpoint never reveals
which control refused: a wrong token and a disallowed IP are indistinguishable.
"""

from __future__ import annotations

import hmac
import logging

from django.conf import settings
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services.bootstrap import BootstrapError, create_admin
from core.middleware import client_ip

logger = logging.getLogger("hrms.access")

GENERIC_REJECTION = {"error": {"code": "not_permitted", "message": "Not permitted."}}


class BootstrapRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    # Deliberately NO password field. See module docstring.


class AdminBootstrapView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    access_exempt = True
    throttle_scope = "bootstrap"

    def post(self, request):
        token = getattr(settings, "ADMIN_BOOTSTRAP_TOKEN", "")
        if not token:
            # Defence in depth: the URL should not be registered at all.
            return Response(GENERIC_REJECTION, status=status.HTTP_403_FORBIDDEN)

        ip = client_ip(request) or ""
        allowed = getattr(settings, "ADMIN_BOOTSTRAP_ALLOWED_IPS", [])
        if allowed and ip not in allowed:
            logger.warning("bootstrap.ip_rejected ip=%s", ip)
            return Response(GENERIC_REJECTION, status=status.HTTP_403_FORBIDDEN)

        presented = request.headers.get("X-Bootstrap-Token", "")
        if not hmac.compare_digest(presented, token):
            logger.warning("bootstrap.token_rejected ip=%s", ip)
            return Response(GENERIC_REJECTION, status=status.HTTP_403_FORBIDDEN)

        serializer = BootstrapRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = create_admin(**serializer.validated_data)
        except BootstrapError as exc:
            # 409: the request was authorised but conflicts with current state
            # (an Admin already exists). Distinct from the 403s above, which say
            # nothing about state.
            logger.warning("bootstrap.refused ip=%s reason=%s", ip, exc)
            return Response(
                {"error": {"code": "bootstrap_unavailable", "message": str(exc)}},
                status=status.HTTP_409_CONFLICT,
            )

        logger.warning("bootstrap.admin_created email=%s ip=%s", user.email, ip)
        return Response(
            {
                "id": str(user.pk),
                "email": user.email,
                "next_steps": [
                    "Set a password out of band: manage.py changepassword "
                    f"{user.email}",
                    "Sign in at /login/admin",
                    "Unset ADMIN_BOOTSTRAP_TOKEN so this endpoint returns 404",
                ],
            },
            status=status.HTTP_201_CREATED,
        )
