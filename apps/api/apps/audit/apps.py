from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"
    label = "audit"

    def ready(self):
        # Every other app has already registered its models by now — this app
        # is last in INSTALLED_APPS precisely so that is true.
        from . import signals

        signals.connect_all()
