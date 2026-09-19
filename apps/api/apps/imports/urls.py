"""Import routes: candidates from a job board, employees from a staff list."""

from rest_framework.routers import DefaultRouter

from apps.imports.api import CandidateImportViewSet, EmployeeImportViewSet

router = DefaultRouter()
router.register(
    "candidate-imports", CandidateImportViewSet, basename="candidate-import"
)
# Separate route, not a `kind` parameter on the one above. The two differ in
# what may be done to a batch (a candidate batch is attested and its rows are
# editable; an employee batch is neither) and in which resource authorises it
# -- CANDIDATE against EMPLOYEE. One viewset switching on a query parameter
# would make the permission depend on a value in the request.
router.register(
    "employee-imports", EmployeeImportViewSet, basename="employee-import"
)

urlpatterns = router.urls
