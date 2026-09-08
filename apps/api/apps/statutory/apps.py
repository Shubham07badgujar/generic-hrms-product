from django.apps import AppConfig


class StatutoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.statutory"
    label = "statutory"

    def ready(self):
        from apps.audit.registry import register

        from .models import StatutoryRuleSet

        # Parameter diffs on statutory rates are among the most
        # security-relevant changes in the system.
        register(StatutoryRuleSet)
