from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        # Register models for audit. Role and permission changes are among the
        # most security-relevant events in the system, so they are audited from
        # day one rather than added later.
        from apps.audit.registry import register

        from .models import Role, RolePermission, User, UserPermissionOverride, UserRole

        register(User, redact={"password", "mfa_secret"})
        register(Role)
        register(RolePermission)
        register(UserRole)
        register(UserPermissionOverride)
