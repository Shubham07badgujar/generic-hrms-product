"""Employee and lifecycle routes."""

from rest_framework.routers import DefaultRouter

from .lifecycle_views import (
    AssetAllocationViewSet,
    AssetCategoryViewSet,
    AssetViewSet,
    CompanyEmailAccountViewSet,
    DocumentTypeViewSet,
    EmployeeDocumentViewSet,
    EmployeeLetterViewSet,
    EmployeeOnboardingViewSet,
    LetterTemplateViewSet,
    OnboardingItemViewSet,
    OnboardingTemplateViewSet,
    ProbationReviewViewSet,
)
from .views import EmployeeViewSet

router = DefaultRouter()
router.register("employees", EmployeeViewSet, basename="employee")

router.register("document-types", DocumentTypeViewSet, basename="document-type")
router.register("employee-documents", EmployeeDocumentViewSet, basename="employee-document")

router.register("probation-reviews", ProbationReviewViewSet, basename="probation-review")

router.register("onboarding-templates", OnboardingTemplateViewSet, basename="onboarding-template")
router.register("onboarding", EmployeeOnboardingViewSet, basename="employee-onboarding")
router.register("onboarding-items", OnboardingItemViewSet, basename="onboarding-item")

router.register("letter-templates", LetterTemplateViewSet, basename="letter-template")
router.register("letters", EmployeeLetterViewSet, basename="employee-letter")

router.register("company-accounts", CompanyEmailAccountViewSet, basename="company-account")

router.register("asset-categories", AssetCategoryViewSet, basename="asset-category")
router.register("assets", AssetViewSet, basename="asset")
router.register("asset-allocations", AssetAllocationViewSet, basename="asset-allocation")

employee_patterns = router.urls
