from django.apps import AppConfig


class OffboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.offboarding"
    label = "offboarding"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            ClearanceTemplate,
            ClearanceTemplateItem,
            ExitClearanceItem,
            ExitInterview,
            ExitWorkflow,
            FinalSettlement,
            ResignationRequest,
        )

        register(ResignationRequest)
        register(ExitWorkflow)
        register(ClearanceTemplate)
        register(ClearanceTemplateItem)
        register(ExitClearanceItem)
        register(FinalSettlement)
        # The interview holds candid feedback about named people. The audit
        # trail records that it was conducted and by whom, never its contents.
        register(
            ExitInterview,
            redact={
                "employee_feedback",
                "manager_feedback",
                "workplace_feedback",
                "improvement_suggestions",
                "hr_notes",
            },
        )
