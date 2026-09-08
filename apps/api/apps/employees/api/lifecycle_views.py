"""
Lifecycle API: documents, probation, onboarding, letters, accounts, assets.

Every mutating route is a thin wrapper over a service. The services carry the
rules — no automatic confirmation, a confirmation letter only after HR decides,
an exit blocked by unreturned property, no credential ever persisted — and they
are also reachable from management commands and Celery, so a check that lived
only here would not be a check.

FILE DOWNLOADS GO THROUGH A VIEW, NEVER A URL IN A PAYLOAD. `download` re-runs
the same scoping the list did, so a link cannot outlive the permission that
produced it.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.assets.models import Asset, AssetAllocation, AssetCategory
from apps.assets.services import allocate, return_asset, write_off
from apps.employees.models import (
    DocumentType,
    Employee,
    EmployeeDocument,
    ProbationReview,
)
from apps.employees.services import documents as document_service
from apps.employees.services import lifecycle as lifecycle_service
from apps.employees.services import probation as probation_service
from apps.itaccounts.models import CompanyEmailAccount
from apps.itaccounts.services import (
    assert_no_credentials_in,
    deprovision,
    mark_provisioned,
    record_account,
    suspend,
)
from apps.onboarding.letters import generate_letter
from apps.onboarding.models import (
    EmployeeLetter,
    EmployeeOnboarding,
    LetterTemplate,
    OnboardingItem,
    OnboardingTemplate,
)
from apps.onboarding.services import (
    complete_item,
    complete_onboarding,
    reopen_item,
    waive_item,
)
from core.access import Action, Resource
from core.api.mixins import ServiceCreatedOnly
from core.access.drf import (
    ScopedModelViewSet,
    ScopedReadOnlyModelViewSet,
)

from . import lifecycle_serializers as s
from .filters import EmployeeDocumentFilter


def _call(fn, **kwargs):
    """Run a service, translating its Django ValidationError into a DRF 400."""
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


def _serve(file_field, *, filename: str | None = None):
    """
    Stream a stored file.

    Reached only after the viewset's scoped queryset has already admitted the
    row, so the permission check is the lookup itself.
    """
    if not file_field:
        raise Http404("No file is attached to this record.")
    return FileResponse(
        file_field.open("rb"), as_attachment=True, filename=filename or file_field.name.split("/")[-1]
    )


# ===========================================================================
# Documents
# ===========================================================================


class DocumentTypeViewSet(ScopedReadOnlyModelViewSet):
    """The catalogue. Read-only: types are configuration, edited under change control."""

    access_resource = Resource.EMPLOYEE_DOCUMENT
    pagination_class = None
    queryset = DocumentType.objects.filter(is_active=True)
    serializer_class = s.DocumentTypeSerializer
    filterset_fields = ["category", "is_mandatory"]

    def get_queryset(self):
        # The catalogue is not personal data — anyone who may hold documents
        # needs to know which ones exist. Scoping it by employee would leave a
        # self-service user unable to see what they are being asked for.
        return self.queryset


class EmployeeDocumentViewSet(ScopedModelViewSet):
    access_resource = Resource.EMPLOYEE_DOCUMENT
    queryset = EmployeeDocument.objects.select_related(
        "employee", "employee__department", "employee__user",
        "document_type", "uploaded_by", "verified_by", "rejected_by",
    ).filter(is_active=True)
    serializer_class = s.EmployeeDocumentSerializer
    filterset_class = EmployeeDocumentFilter
    #: Free-text across the things a reviewer would actually type: a person's
    #: name, their employee code, or the document kind. NOT the stored filename
    #: — searching it would invite matching on strings people put in filenames.
    search_fields = [
        "employee__first_name",
        "employee__last_name",
        "employee__employee_code",
        "document_type__name",
    ]
    ordering_fields = ["uploaded_at", "expires_on", "status"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "download": Action.VIEW,
        # Three separate authorities. Verifying is APPROVE and rejecting is
        # REJECT — neither is EDIT, so a role can be allowed to collect
        # documents without being allowed to vouch for or refuse them.
        "verify": Action.APPROVE,
        "reject": Action.REJECT,
    }

    def create(self, request, *args, **kwargs):
        """Upload a document against an employee. Always lands PENDING."""
        employee_id = request.data.get("employee")
        if not employee_id:
            raise DRFValidationError({"employee": "An employee is required."})

        # Resolved through the EMPLOYEE_DOCUMENT/CREATE reach — "whose file may
        # I write to" — and NOT through EMPLOYEE/VIEW, which only ever answered
        # "whose record may I read". Conflating the two let anyone who could
        # see a colleague file a document into that colleague's permanent
        # record. The service re-checks this; it is not trusting the route.
        employee = (
            document_service.employees_a_user_may_file_against(request.user)
            .filter(pk=employee_id)
            .first()
        )
        if employee is None:
            raise Http404("No such employee.")

        payload = s.DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        document = _call(
            document_service.upload_document,
            employee=employee,
            actor=request.user,
            **payload.validated_data,
        )
        return Response(
            s.EmployeeDocumentSerializer(document).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        return _serve(document.file, filename=document.original_filename or None)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        document = _call(
            document_service.verify_document, document=self.get_object(), actor=request.user
        )
        return Response(s.EmployeeDocumentSerializer(document).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        payload = s.DocumentRejectSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        document = _call(
            document_service.reject_document,
            document=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(s.EmployeeDocumentSerializer(document).data)


# ===========================================================================
# Probation
# ===========================================================================


class ProbationReviewViewSet(ServiceCreatedOnly, ScopedModelViewSet):
    access_resource = Resource.PROBATION_REVIEW
    queryset = ProbationReview.objects.select_related(
        "employee", "employee__department", "reviewer", "decided_by"
    ).filter(is_active=True)
    serializer_class = s.ProbationReviewSerializer
    filterset_fields = ["employee", "decision", "recommendation"]
    ordering_fields = ["probation_end_date", "decided_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "assess": Action.EDIT,
        #: The employment decision. DECIDE is held by HR alone — a manager with
        #: EDIT can assess and recommend, and gets 403 here.
        "decide": Action.DECIDE,
        "pending": Action.VIEW,
        "upcoming": Action.VIEW,
    }

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """Reviews awaiting an HR decision. The queue HR works."""
        queryset = self.filter_queryset(self.get_queryset().filter(decision="pending"))
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=False, methods=["get"])
    def upcoming(self, request):
        """
        Probations approaching or overdue, grouped by reminder point.

        A pure read of the same rule the nightly sweep uses, so the dashboard
        and the reminders can never disagree.
        """
        from core.access.engine import scope_queryset

        buckets = probation_service.due_for_reminder()
        visible = set(
            scope_queryset(
                Employee.objects.all(), request.user,
                resource=Resource.EMPLOYEE, action=Action.VIEW,
            ).values_list("pk", flat=True)
        )

        def shape(employees):
            return [
                {
                    "id": str(e.pk),
                    "employee_code": e.employee_code,
                    "full_name": e.full_name,
                    "department_name": e.department.name if e.department else None,
                    "probation_end_date": e.probation_end_date,
                    "probation_status": e.probation_status,
                }
                for e in employees
                if e.pk in visible
            ]

        return Response({key: shape(value) for key, value in buckets.items()})

    @action(detail=True, methods=["post"])
    def assess(self, request, pk=None):
        """The reviewer's assessment and recommendation. Advisory."""
        payload = s.ProbationAssessmentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        review = _call(
            probation_service.record_assessment,
            review=self.get_object(),
            actor=request.user,
            **payload.validated_data,
        )
        return Response(s.ProbationReviewSerializer(review).data)

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        """HR's decision. The ONLY path to a confirmed employee."""
        payload = s.ProbationDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        review = _call(
            probation_service.decide,
            review=self.get_object(),
            actor=request.user,
            **payload.validated_data,
        )
        return Response(s.ProbationReviewSerializer(review).data)


# ===========================================================================
# Onboarding
# ===========================================================================


class OnboardingTemplateViewSet(ScopedModelViewSet):
    access_resource = Resource.ONBOARDING
    pagination_class = None
    queryset = (
        OnboardingTemplate.objects.filter(is_active=True)
        .select_related("department")
        .prefetch_related("items__document_type")
    )
    serializer_class = s.OnboardingTemplateSerializer
    filterset_fields = ["department", "is_default"]

    def get_queryset(self):
        # Templates are org configuration, not per-employee data; scoping them
        # by employee path would return nothing for everyone.
        from core.access import can

        return self.queryset if can(self.request.user, Resource.ONBOARDING) else self.queryset.none()


class EmployeeOnboardingViewSet(ServiceCreatedOnly, ScopedModelViewSet):
    access_resource = Resource.ONBOARDING
    queryset = (
        EmployeeOnboarding.objects.select_related("employee", "employee__department", "template")
        .prefetch_related("items__assigned_to", "items__document_type")
        .filter(is_active=True)
    )
    serializer_class = s.EmployeeOnboardingSerializer
    filterset_fields = ["status", "employee"]
    ordering_fields = ["joining_date", "created_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {"complete": Action.EDIT, "overdue": Action.VIEW}

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        """Checklists carrying an overdue item. Drives the HR dashboard."""
        from django.utils import timezone

        queryset = self.get_queryset().filter(
            status="in_progress",
            items__due_date__lt=timezone.localdate(),
            items__status__in=["pending", "in_progress", "submitted", "blocked"],
        ).distinct()
        return Response(self.get_serializer(queryset, many=True).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        onboarding = _call(
            complete_onboarding,
            onboarding=self.get_object(),
            actor=request.user,
            force=bool(request.data.get("force")),
        )
        return Response(self.get_serializer(onboarding).data)


class OnboardingItemViewSet(ServiceCreatedOnly, ScopedModelViewSet):
    """
    Individual checklist lines.

    Scoped through the checklist's employee, so an employee sees their own
    items, a manager their team's, and HR everyone's.
    """

    access_resource = Resource.ONBOARDING
    queryset = (
        OnboardingItem.objects.select_related(
            "onboarding", "onboarding__employee", "assigned_to", "document_type"
        ).filter(is_active=True)
    )
    serializer_class = s.OnboardingItemSerializer
    filterset_fields = ["status", "kind", "onboarding", "assigned_to"]
    ordering_fields = ["due_date", "order"]
    # Multipart for document completions that carry a file; JSON for every
    # completion that does not (the SPA's "Mark done" and the handbook
    # acknowledgement both post JSON, which multipart-only refused with 415).
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {"complete": Action.EDIT, "waive": Action.EDIT, "reopen": Action.EDIT}

    def get_queryset(self):
        """
        Scoped through the checklist's employee.

        NOT `scope_queryset` directly. The registry's ONBOARDING spec walks
        `employee` on EmployeeOnboarding, and an item reaches the employee one
        hop further along — so handing this queryset to the generic scoper
        produced `employee_id=...` against a model with no such field, and every
        caller below ALL scope got a FieldError instead of their own items.

        The SCOPE still comes from the access engine; only the path is local.
        Same arrangement as the exit-clearance viewset, for the same reason.
        """
        from django.db import models as db_models

        from core.access import can, get_context
        from core.access.catalog import Scope

        scope = can(self.request.user, Resource.ONBOARDING, Action.VIEW)
        if not scope:
            return self.queryset.none()
        if scope == Scope.ALL:
            return self.queryset

        context = get_context(self.request.user)
        if context.employee_id is None:
            return self.queryset.none()

        if scope == Scope.DEPARTMENT:
            if not context.department_ids:
                return self.queryset.none()
            return self.queryset.filter(
                onboarding__employee__department_id__in=context.department_ids
            )
        if scope == Scope.TEAM:
            ids = context.reporting_tree_ids
            if not ids:
                return self.queryset.none()
            return self.queryset.filter(onboarding__employee_id__in=ids)

        # SELF: the caller's own checklist, plus any item assigned to them —
        # an item can be owned by someone other than the new joiner.
        return self.queryset.filter(
            db_models.Q(onboarding__employee_id=context.employee_id)
            | db_models.Q(assigned_to_id=context.employee_id)
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        payload = s.OnboardingItemActionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = _call(
            complete_item,
            item=self.get_object(),
            actor=request.user,
            notes=payload.validated_data.get("notes", ""),
            document=payload.validated_data.get("document"),
            file=payload.validated_data.get("file"),
        )
        return Response(s.OnboardingItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        payload = s.OnboardingWaiveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = _call(
            waive_item,
            item=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(s.OnboardingItemSerializer(item).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        item = _call(
            reopen_item,
            item=self.get_object(),
            actor=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(s.OnboardingItemSerializer(item).data)


# ===========================================================================
# Letters
# ===========================================================================


class LetterTemplateViewSet(ScopedReadOnlyModelViewSet):
    access_resource = Resource.LETTER
    pagination_class = None
    queryset = LetterTemplate.objects.filter(is_active=True)
    serializer_class = s.LetterTemplateSerializer
    filterset_fields = ["letter_type"]

    def get_queryset(self):
        from core.access import can

        return self.queryset if can(self.request.user, Resource.LETTER) else self.queryset.none()


class EmployeeLetterViewSet(ScopedModelViewSet):
    access_resource = Resource.LETTER
    queryset = EmployeeLetter.objects.select_related("employee", "generated_by", "template").filter(
        is_active=True
    )
    serializer_class = s.EmployeeLetterSerializer
    filterset_fields = ["employee", "letter_type", "status"]
    ordering_fields = ["generated_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {"download": Action.VIEW}

    def create(self, request, *args, **kwargs):
        """
        Generate a letter and its PDF.

        A CONFIRMATION letter is refused unless HR has recorded a probation
        confirmation — enforced in the service, so this route cannot be used to
        reach round the back of that rule.
        """
        from core.access.engine import scope_queryset

        employee = scope_queryset(
            Employee.objects.all(), request.user, resource=Resource.EMPLOYEE, action=Action.VIEW
        ).filter(pk=request.data.get("employee")).first()
        if employee is None:
            raise Http404("No such employee.")

        payload = s.LetterGenerateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        letter = _call(
            generate_letter,
            employee=employee,
            actor=request.user,
            letter_type=payload.validated_data["letter_type"],
            template=payload.validated_data.get("template"),
            context_extra=payload.validated_data.get("context_extra") or {},
        )
        return Response(s.EmployeeLetterSerializer(letter).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        letter = self.get_object()
        return _serve(letter.pdf_file, filename=f"{letter.letter_type}-{letter.employee.employee_code}.pdf")


# ===========================================================================
# Company accounts
# ===========================================================================


class CompanyEmailAccountViewSet(ScopedModelViewSet):
    access_resource = Resource.EMAIL_ACCOUNT
    queryset = CompanyEmailAccount.objects.select_related(
        "employee", "requested_by", "provisioned_by"
    ).filter(is_active=True)
    serializer_class = s.CompanyEmailAccountSerializer
    filterset_fields = ["status", "provider", "employee"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "provision": Action.EDIT,
        "suspend": Action.EDIT,
        "deprovision": Action.EDIT,
    }

    def create(self, request, *args, **kwargs):
        # Refuses loudly if a credential was sent, rather than dropping it and
        # leaving the caller believing it was stored.
        assert_no_credentials_in(dict(request.data))

        from core.access.engine import scope_queryset

        employee = scope_queryset(
            Employee.objects.all(), request.user, resource=Resource.EMPLOYEE, action=Action.VIEW
        ).filter(pk=request.data.get("employee")).first()
        if employee is None:
            raise Http404("No such employee.")

        payload = s.CompanyEmailAccountSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        account = _call(
            record_account,
            employee=employee,
            actor=request.user,
            email_address=payload.validated_data["email_address"],
            provider=payload.validated_data.get("provider", "google_workspace"),
            external_account_id=payload.validated_data.get("external_account_id", ""),
            notes=payload.validated_data.get("notes", ""),
        )
        return Response(
            s.CompanyEmailAccountSerializer(account).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def provision(self, request, pk=None):
        assert_no_credentials_in(dict(request.data))
        payload = s.AccountProvisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        account = _call(
            mark_provisioned,
            account=self.get_object(),
            actor=request.user,
            external_account_id=payload.validated_data["external_account_id"],
        )
        return Response(s.CompanyEmailAccountSerializer(account).data)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        account = _call(
            suspend,
            account=self.get_object(),
            actor=request.user,
            reason=request.data.get("reason", ""),
        )
        return Response(s.CompanyEmailAccountSerializer(account).data)

    @action(detail=True, methods=["post"])
    def deprovision(self, request, pk=None):
        account = _call(deprovision, account=self.get_object(), actor=request.user)
        return Response(s.CompanyEmailAccountSerializer(account).data)


# ===========================================================================
# Assets
# ===========================================================================


class AssetCategoryViewSet(ScopedReadOnlyModelViewSet):
    access_resource = Resource.ASSET
    pagination_class = None
    queryset = AssetCategory.objects.filter(is_active=True)
    serializer_class = s.AssetCategorySerializer


class AssetViewSet(ScopedModelViewSet):
    """
    The asset register.

    Registered `person_scoped=False`: an asset in the store belongs to nobody,
    so it is all-or-nothing. Who HOLDS one is the allocation, which is
    person-scoped.
    """

    access_resource = Resource.ASSET
    queryset = Asset.objects.select_related("category", "location").filter(is_active=True)
    serializer_class = s.AssetSerializer
    filterset_fields = ["status", "category", "condition", "location"]
    search_fields = ["asset_tag", "name", "serial_number"]
    ordering_fields = ["asset_tag", "purchase_date"]


class AssetAllocationViewSet(ScopedModelViewSet):
    access_resource = Resource.ASSET_ALLOCATION
    queryset = AssetAllocation.objects.select_related(
        "asset", "asset__category", "employee", "allocated_by", "received_by"
    ).filter(is_active=True)
    serializer_class = s.AssetAllocationSerializer
    filterset_fields = ["employee", "asset", "status"]
    ordering_fields = ["allocated_at", "returned_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "return_asset": Action.EDIT,
        #: Writing off company property is heavier than recording its return,
        #: so it takes DELETE rather than EDIT.
        "write_off": Action.DELETE,
        "bulk": Action.CREATE,
    }

    def create(self, request, *args, **kwargs):
        payload = s.AllocateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        allocation = _call(allocate, actor=request.user, **payload.validated_data)
        return Response(
            s.AssetAllocationSerializer(allocation).data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """
        POST /asset-allocations/bulk/ — several assets to one employee at once.

        One transaction around the same `allocate()` service the single path
        uses: every rule (already assigned, retired, exited employee) applies
        per item, and any refusal rolls the whole batch back so HR never has to
        untangle a half-assigned kit.
        """
        payload = s.BulkAllocateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        employee = payload.validated_data["employee"]
        notes = payload.validated_data["notes"]

        with transaction.atomic():
            allocations = [
                _call(allocate, asset=asset, employee=employee, actor=request.user, notes=notes)
                for asset in payload.validated_data["assets"]
            ]
        return Response(
            s.AssetAllocationSerializer(allocations, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="return")
    def return_asset(self, request, pk=None):
        payload = s.ReturnSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        allocation = _call(
            return_asset, allocation=self.get_object(), actor=request.user, **payload.validated_data
        )
        return Response(s.AssetAllocationSerializer(allocation).data)

    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        payload = s.WriteOffSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        allocation = _call(
            write_off,
            allocation=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
        )
        return Response(s.AssetAllocationSerializer(allocation).data)
