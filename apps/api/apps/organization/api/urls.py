"""Organization reference routes."""

from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    DepartmentViewSet,
    DesignationViewSet,
    EmployeeLevelViewSet,
    LocationViewSet,
    OrgBrandingView,
    OrgSettingsView,
    RoleViewSet,
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
] + router.urls
