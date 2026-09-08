from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.attendance"
    label = "attendance"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            AttendanceDevice,
            AttendanceRecord,
            EsslEmployeeLink,
            RegularizationRequest,
            ShiftRule,
        )

        # RawPunch is deliberately absent: it is bulk-created thousands at a
        # time, so the sync writes ONE explicit audit event per run instead.
        register(AttendanceDevice)
        register(EsslEmployeeLink)
        register(AttendanceRecord)
        register(RegularizationRequest)
        register(ShiftRule)
