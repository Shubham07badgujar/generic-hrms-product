from django.apps import AppConfig


class EmployeesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.employees"
    label = "employees"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            DocumentType,
            Employee,
            EmployeeAddress,
            EmployeeDocument,
            EmployeeEducation,
            EmployeeExperience,
            ProbationReview,
        )

        # PII is recorded as CHANGED but never with its value.
        register(Employee, redact={"pan", "aadhaar", "bank_account_number"})
        register(EmployeeAddress)
        register(EmployeeEducation)
        register(EmployeeExperience)
        register(DocumentType)
        # The FILE is the sensitive part; the audit trail records that a
        # document moved between states, never its contents or storage path.
        register(EmployeeDocument, redact={"file"})
        register(ProbationReview)
