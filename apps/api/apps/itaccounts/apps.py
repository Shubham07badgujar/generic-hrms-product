from django.apps import AppConfig


class ItaccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.itaccounts"
    label = "itaccounts"

    def ready(self):
        from apps.audit.registry import register

        from .models import CompanyEmailAccount

        # No redaction list is needed: there is no credential field to redact.
        register(CompanyEmailAccount)
