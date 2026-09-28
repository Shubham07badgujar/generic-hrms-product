from django.apps import AppConfig


class DbguardConfig(AppConfig):
    """
    Database-level tenant hardening: composite organization FKs, row-level
    security policies, audit-row guards and runtime-role grants.

    Defence in depth BENEATH the application's own isolation (tenant
    manager, scope_queryset, RBACPermission, the access.E0xx checks), which
    stays exactly as it is. No models: only migrations and a manifest.
    """

    name = "apps.dbguard"
    label = "dbguard"
    verbose_name = "Database tenant guards"

    def ready(self):
        # Keeps app.org_id / app.user_id in step with the bound context on
        # every connection -- see core/db_context.py.
        from core import db_context

        db_context.install()
