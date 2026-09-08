from django.apps import AppConfig


class WorkflowsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workflows"
    label = "workflows"

    def ready(self):
        from apps.audit.registry import register

        from .models import FeedbackField, FeedbackForm, HiringWorkflow, StageTransition, WorkflowStage

        for model in (HiringWorkflow, WorkflowStage, StageTransition, FeedbackForm, FeedbackField):
            register(model)
