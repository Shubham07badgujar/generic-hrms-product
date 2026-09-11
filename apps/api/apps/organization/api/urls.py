"""Organization reference routes."""

from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    DepartmentViewSet,
    DesignationViewSet,
    EmployeeLevelViewSet,
    LocationViewSet,
    OrgBrandingView,
    MyPlanView,
    OrgSettingsView,
    RoleViewSet,
    SetupStateView,
    TeamViewSet,
)

router = DefaultRouter()
router.register("departments", DepartmentViewSet, basename="department")
router.register("designations", DesignationViewSet, basename="designation")
router.register("locations", LocationViewSet, basename="location")
router.register("levels", EmployeeLevelViewSet, basename="employee-level")
router.register("teams", TeamViewSet, basename="team")
router.register("roles", RoleViewSet, basename="role")

organization_patterns = [
    path("org/settings/", OrgSettingsView.as_view(), name="org-settings"),
    path("org/branding/", OrgBrandingView.as_view(), name="org-branding"),
    # One route, two verbs: GET reports the wizard, POST finishes it. A
    # separate /finish/ path would suggest the wizard has state of its own
    # to advance, and it has none -- every step is computed from the real
    # tables each time it is asked.
    path("org/setup/", SetupStateView.as_view(), name="org-setup"),
    path("org/plan/", MyPlanView.as_view(), name="org-plan"),
] + router.urls
