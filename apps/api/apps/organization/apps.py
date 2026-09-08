from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organization"
    label = "organization"

    def ready(self):
        from apps.audit.registry import register

        from .models import Department, Designation, EmployeeLevel, Location, OrgSettings, Team

        for model in (OrgSettings, Department, Designation, Location, EmployeeLevel, Team):
            register(model)
