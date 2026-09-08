"""
Authentication endpoints.

The refresh token never appears in a JSON body. It lives only in an httpOnly,
Secure, SameSite=Strict cookie the browser cannot read from JavaScript, which
takes it out of reach of XSS. The access token, being short-lived and
low-value, is returned in the body for the SPA to hold in memory.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.access.snapshot import build_snapshot, catalog_reference
from core.api.throttling import LoginAccountThrottle, LoginClientThrottle

from .serializers import (
    ChangePasswordSerializer,
    AdminTokenObtainSerializer,
    MeSerializer,
    TokenObtainSerializer,
)

REFRESH_COOKIE = settings.REFRESH_COOKIE_NAME
# The refresh cookie is only ever sent to the refresh/logout routes, so it is
# not attached to every API call.
REFRESH_COOKIE_PATH = "/api/v1/auth/"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


class _BaseLoginView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    access_exempt = True  # no principal exists yet; RBAC cannot apply
    throttle_scope = "login"
    # Both, not either: one caps a single machine, the other caps attempts
    # against a single account no matter how many machines make them. Listing
    # them here replaces the project-wide default, which is why the IP throttle
    # has to be named explicitly rather than inherited.
    throttle_classes = [LoginClientThrottle, LoginAccountThrottle]
    serializer_class = TokenObtainSerializer

    def post(self, request):
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        refresh = data.pop("refresh")
        body = {
            "access": data["access"],
            "must_change_password": data.get("must_change_password", False),
            "onboarding_pending": data.get("onboarding_pending", False),
        }
        response = Response(body, status=status.HTTP_200_OK)
        # Refresh goes to the cookie, never the body.
        _set_refresh_cookie(response, refresh)
        return response


class TokenObtainView(_BaseLoginView):
    """POST /api/v1/auth/login/ — universal login."""

    serializer_class = TokenObtainSerializer


class AdminTokenObtainView(_BaseLoginView):
    """
    POST /api/v1/auth/login/admin/ — hardened admin entrance.

    A non-admin who authenticates correctly still receives the generic
    "Invalid email or password", so the endpoint never confirms whether an
    address belongs to an admin.
    """

    serializer_class = AdminTokenObtainSerializer


class TokenRefreshView(APIView):
    """
    POST /api/v1/auth/refresh/ — rotate the refresh token, mint a new access.

    Reads the refresh token from the cookie, not the body. Rotation +
    blacklist-after-rotation means a stolen refresh token is single-use: the
    moment either the attacker or the legitimate user spends it, the other's
    copy is dead, and the theft surfaces as an unexpected logout.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    access_exempt = True

    def _expired(self):
        response = Response(
            {"error": {"code": "invalid_refresh_token", "message": "Session expired."}},
            status=status.HTTP_401_UNAUTHORIZED,
        )
        _clear_refresh_cookie(response)
        return response

    def post(self, request):
        from apps.accounts.models import User

        raw = request.COOKIES.get(REFRESH_COOKIE)
        if not raw:
            return Response(
                {"error": {"code": "no_refresh_token", "message": "Not authenticated."}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            presented = RefreshToken(raw)
            user_id = presented[settings.SIMPLE_JWT["USER_ID_CLAIM"]]
            user = User.objects.get(pk=user_id, is_active=True)
        except (TokenError, InvalidToken, User.DoesNotExist):
            return self._expired()

        if settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            # Single-use: blacklist the presented token and issue a fresh pair.
            # A replay of the old token now fails, which is how a theft surfaces.
            presented.blacklist()
            fresh = RefreshToken.for_user(user)
            fresh["email"] = user.email
            fresh["name"] = user.get_full_name()
            access, refresh_out = str(fresh.access_token), str(fresh)
        else:
            access, refresh_out = str(presented.access_token), raw

        response = Response({"access": access}, status=status.HTTP_200_OK)
        _set_refresh_cookie(response, refresh_out)
        return response


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — blacklist the refresh token and clear the cookie."""

    permission_classes = [IsAuthenticated]
    access_exempt = True

    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE)
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass  # already invalid; clearing the cookie is enough
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_refresh_cookie(response)
        return response


class ChangePasswordView(APIView):
    """
    POST /api/v1/auth/change-password/ — set a new password of one's own.

    Reachable while `must_change_password` is up (the auth prefix is on the
    gate's allow-list) — it is the one thing a temporary password unlocks.
    Also usable any time afterwards. Requires the current password even on the
    forced first-login change: a stolen access token must not be enough to
    take the account over by setting a new secret on it.

    Every outstanding refresh token is revoked by the service, including the
    caller's own, so the response also clears the cookie: the client signs in
    again with the password it just chose.
    """

    permission_classes = [IsAuthenticated]
    access_exempt = True  # one's own credentials; not a matrix resource
    throttle_scope = "password_change"

    def post(self, request):
        from apps.accounts.services.passwords import change_password

        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_password(
                user=request.user,
                current_password=serializer.validated_data["current_password"],
                new_password=serializer.validated_data["new_password"],
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise DRFValidationError(detail) from exc

        response = Response({"detail": "Password changed. Sign in again with your new password."})
        _clear_refresh_cookie(response)
        return response


class MeView(APIView):
    """GET /api/v1/me/ — the signed-in user's identity."""

    permission_classes = [IsAuthenticated]
    access_exempt = True  # a user may always read their own identity

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class MyPermissionsView(APIView):
    """
    GET /api/v1/me/permissions/ — the SPA's permission snapshot.

    Advisory to the client, authoritative nowhere. See core/access/snapshot.py.
    """

    permission_classes = [IsAuthenticated]
    access_exempt = True

    def get(self, request):
        return Response(build_snapshot(request.user))


class AccessCatalogView(APIView):
    """GET /api/v1/access-catalog/ — resource/action/scope vocabulary for the SPA."""

    permission_classes = [IsAuthenticated]
    access_exempt = True

    def get(self, request):
        return Response(catalog_reference())
