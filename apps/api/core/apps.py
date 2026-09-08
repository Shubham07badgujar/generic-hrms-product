from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Config for the shared `core` package.

    Its only job is to import `core.access.checks`, and that job is
    load-bearing. Those checks are registered with `@register(Tags.urls)` at
    module import time, so a module nobody imports registers nothing: before
    this AppConfig existed, `manage.py check` reported "no issues" while
    running none of them, and the build-time guarantee that every API view is
    RBAC-mapped -- which `core/access/checks.py` calls the single most
    important structural difference from the previous system -- was not in
    force at all. `tests/core/test_system_checks.py` asserts it is.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    label = "core"

    def ready(self):
        from core.access import checks  # noqa: F401  (registers system checks)
