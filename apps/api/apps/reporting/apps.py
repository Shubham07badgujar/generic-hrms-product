from django.apps import AppConfig


class ReportingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reporting"
    label = "reporting"

    def ready(self):
        # Importing the module is what registers every metric. Without this the
        # registry is empty and `/bi/metrics/` returns nothing — a failure that
        # looks like a permission problem and is not one.
        from . import metrics  # noqa: F401
