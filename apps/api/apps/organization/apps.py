from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organization"
    label = "organization"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            Department,
            Designation,
            EmployeeLevel,
            Location,
            Organization,
            OrganizationMembership,
            OrgSettings,
            Team,
        )

        for model in (
            # The tenant itself, and who belongs to it. Both are audited
            # because a change to either alters who can reach what: an
            # organization's status decides whether its people may work at
            # all, and a membership IS the tenant identity every request is
            # resolved from.
            Organization,
            OrganizationMembership,
            OrgSettings,
            Department,
            Designation,
            Location,
            EmployeeLevel,
            Team,
        ):
            register(model)
