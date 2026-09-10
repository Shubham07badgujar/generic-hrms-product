"""
Platform routes.

Everything mounted here is under `/api/v1/platform/`, and `access.E011` fails
the build for anything here that does not declare `platform_only = True`. The
sign-in entrance is deliberately NOT here -- see `PLATFORM_PATH_PREFIX`.
"""

from django.urls import path

from .views import PlatformSummaryView, router

platform_patterns = [
    path("summary/", PlatformSummaryView.as_view(), name="platform-summary"),
    *router.urls,
]
