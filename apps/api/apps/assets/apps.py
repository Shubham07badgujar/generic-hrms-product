from django.apps import AppConfig


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assets"
    label = "assets"

    def ready(self):
        from apps.audit.registry import register

        from .models import Asset, AssetAllocation, AssetCategory, AssetMaintenanceLog

        register(AssetCategory)
        register(Asset)
        # Allocation and return are the events an auditor actually looks for;
        # the service also writes explicit ALLOCATE / RETURN entries with the
        # right verb, because "updated" understates handing someone a laptop.
        register(AssetAllocation)
        register(AssetMaintenanceLog)
