"""
Root URL configuration.

This is an API-only backend. There are no server-rendered application pages —
the React SPA is served separately as static files. Django admin is retained
only as a break-glass operational tool for superusers.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.accounts.api.urls import auth_patterns, bootstrap_patterns, identity_patterns
from apps.audit.api import audit_patterns
from apps.notifications.api import NotificationPreferenceView, notification_patterns
from apps.reporting.api import MetricCatalogView, MetricView
from apps.employees.api.urls import employee_patterns
from apps.offboarding.api import offboarding_patterns
from apps.organization.api.urls import organization_patterns
from apps.payroll.api import MyPayrollView, payroll_patterns
from apps.attendance.api import attendance_patterns
from apps.imports.urls import urlpatterns as import_patterns
from apps.leave.api import leave_patterns
from apps.recruitment.api.urls import recruitment_patterns
from core.api.views import HealthCheckView

api_v1 = [
    path("health/", HealthCheckView.as_view(), name="health"),
    path("auth/", include(auth_patterns)),
    *identity_patterns,
    *bootstrap_patterns,
    *organization_patterns,
    *employee_patterns,
    *recruitment_patterns,
    *import_patterns,
    *offboarding_patterns,
    *leave_patterns,
    *attendance_patterns,
    # Self-service before the router: "my payslips" must not depend on the
    # client sending the right employee id.
    path("payroll/me/", MyPayrollView.as_view(), name="my-payroll"),
    *payroll_patterns,
    # BI. The catalogue is listed before the compute route so "metrics" is
    # never swallowed as a metric key.
    path("bi/metrics/", MetricCatalogView.as_view(), name="bi-metrics"),
    path("bi/<str:key>/", MetricView.as_view(), name="bi-metric"),
    path(
        "notifications/preferences/",
        NotificationPreferenceView.as_view(),
        name="notification-preferences",
    ),
    *notification_patterns,
    *audit_patterns,
]

urlpatterns = [
    path("api/v1/", include((api_v1, "api"), namespace="v1")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
        path(
            "api/schema/redoc/",
            SpectacularRedocView.as_view(url_name="schema"),
            name="redoc",
        ),
    ]
