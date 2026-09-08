from django.apps import AppConfig


class RecruitmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recruitment"
    label = "recruitment"

    def ready(self):
        from apps.audit.registry import register

        from .models import (
            Application, Candidate, CandidateRejection, DecisionOverride,
            Interview, InterviewFeedback, JobOpening, Offer, StageDecision,
        )

        for model in (
            JobOpening, Candidate, Application, Interview, InterviewFeedback,
            StageDecision, CandidateRejection, DecisionOverride, Offer,
        ):
            register(model)
