from django.apps import AppConfig


class OnboardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.onboarding"
    label = "onboarding"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            EmployeeLetter,
            EmployeeOnboarding,
            LetterTemplate,
            OnboardingItem,
            OnboardingTemplate,
            OnboardingTemplateItem,
        )

        register(OnboardingTemplate)
        register(OnboardingTemplateItem)
        register(EmployeeOnboarding)
        register(OnboardingItem)
        register(LetterTemplate)
        # `body_html` is the whole letter and would bloat every audit row; the
        # letter record itself is the durable copy.
        register(EmployeeLetter, redact={"body_html"})
