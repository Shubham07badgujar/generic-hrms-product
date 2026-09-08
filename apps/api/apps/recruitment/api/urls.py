"""Recruitment routes."""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .public import PublicApplyView, PublicSlotView

from .views import (
    ApplicationViewSet,
    CandidateViewSet,
    FeedbackFormViewSet,
    HiringWorkflowViewSet,
    InterviewViewSet,
    JobOpeningViewSet,
    OfferViewSet,
)

router = DefaultRouter()
router.register("workflows", HiringWorkflowViewSet, basename="hiring-workflow")
router.register("feedback-forms", FeedbackFormViewSet, basename="feedback-form")
router.register("jobs", JobOpeningViewSet, basename="job-opening")
router.register("candidates", CandidateViewSet, basename="candidate")
router.register("applications", ApplicationViewSet, basename="application")
router.register("interviews", InterviewViewSet, basename="interview")
router.register("offers", OfferViewSet, basename="offer")

recruitment_patterns = [
    # Anonymous. Listed before the router so nothing under /public/ is ever
    # shadowed by a scoped viewset.
    path("public/apply/<str:token>/", PublicApplyView.as_view(), name="public-apply"),
    path("public/interview-slot/<str:token>/", PublicSlotView.as_view(), name="public-slot"),
    *router.urls,
]
