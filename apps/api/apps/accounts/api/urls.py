"""Authentication and identity routes."""

from django.conf import settings
from django.urls import path

from .views import (
    AccessCatalogView,
    AdminTokenObtainView,
    ChangePasswordView,
    LogoutView,
    MeView,
    MyPermissionsView,
    PlatformTokenObtainView,
    TokenObtainView,
    TokenRefreshView,
)

auth_patterns = [
    path("login/", TokenObtainView.as_view(), name="login"),
    path("login/admin/", AdminTokenObtainView.as_view(), name="login-admin"),
    path(
        "login/platform/", PlatformTokenObtainView.as_view(), name="login-platform"
    ),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
]

identity_patterns = [
    path("me/", MeView.as_view(), name="me"),
    path("me/permissions/", MyPermissionsView.as_view(), name="my-permissions"),
    path("access-catalog/", AccessCatalogView.as_view(), name="access-catalog"),
]

# The bootstrap route is registered ONLY when a token is configured. With the
# token unset the URL genuinely does not exist — a 404, not a 403 — so a
# completed deployment offers nothing to probe.
bootstrap_patterns = []
if getattr(settings, "ADMIN_BOOTSTRAP_TOKEN", ""):
    from .bootstrap_views import AdminBootstrapView

    bootstrap_patterns = [
        path("bootstrap/admin/", AdminBootstrapView.as_view(), name="bootstrap-admin"),
    ]
