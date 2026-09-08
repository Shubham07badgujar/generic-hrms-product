"""Candidate import routes."""

from rest_framework.routers import DefaultRouter

from apps.imports.api import CandidateImportViewSet

router = DefaultRouter()
router.register(
    "candidate-imports", CandidateImportViewSet, basename="candidate-import"
)

urlpatterns = router.urls
