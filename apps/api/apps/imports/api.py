"""
Candidate import API.

Gated on CANDIDATE/IMPORT rather than CANDIDATE/CREATE. Adding one walk-in
candidate and ingesting two thousand third-party records under a legal-basis
attestation are different authorities with different blast radii, and the
permission matrix is where that distinction is reviewed. Reusing CREATE would
have made the capability invisible to a reviewer and impossible to revoke on its
own.

Every custom route names its action in `access_actions`. Without that,
`resolve_action` falls through to `METHOD_ACTION_MAP` and a POST to `/commit/`
would be authorised as `Action.CREATE` — the hole `views.py` in recruitment
warns about. `manage.py check` fails the build if `access_resource` is missing,
but it cannot know that a custom verb was mapped to the wrong action.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.imports.models import ImportBatch, ImportKind, ImportRow, RowStatus
from apps.imports.platforms import get_platform, list_platforms
from apps.imports.services import importer
from apps.recruitment.models import JobOpening, LegalBasis
from core.access import Action, Resource, Scope, can
from core.access.drf import ScopedModelViewSet
from core.api.serializers import ScopedRelationsMixin
from core.querysets import deferred

MIN_BASIS_NOTE = 20


def _service_call(fn, **kwargs):
    """Translate a service's Django ValidationError into a DRF 400."""
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        detail = (
            exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        )
        raise DRFValidationError(detail) from exc


# --------------------------------------------------------------- serializers


class ImportRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportRow
        fields = [
            "id", "row_number", "first_name", "last_name", "email", "phone",
            "external_id", "current_employer", "total_experience_years",
            "expected_ctc", "notice_period_days",
            # Every cell of the original spreadsheet row, keyed by header —
            # what the editable preview grid displays and edits.
            "raw",
            "status", "match_rule", "matched_candidate", "duplicate_of_row",
            "errors", "warnings",
        ]
        read_only_fields = fields


class ImportBatchSerializer(serializers.ModelSerializer):
    rows = ImportRowSerializer(many=True, read_only=True)
    platform_label = serializers.SerializerMethodField()
    job_title = serializers.CharField(source="job_opening.title", read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id", "platform", "platform_label", "job_opening", "job_title",
            "original_filename", "file_sha256", "column_mapping",
            "detected_headers", "unmapped_headers", "status",
            "rows_total", "rows_created", "rows_updated", "rows_duplicate",
            "rows_review", "rows_failed",
            "legal_basis", "attested_at", "committed_at", "created_at",
            "rows",
        ]
        read_only_fields = fields

    def get_platform_label(self, obj) -> str:
        spec = get_platform(obj.platform)
        return spec.label if spec else obj.platform


class UploadSerializer(ScopedRelationsMixin, serializers.Serializer):
    platform = serializers.CharField()
    job_opening = serializers.PrimaryKeyRelatedField(queryset=deferred(JobOpening))
    file = serializers.FileField()
    column_override = serializers.JSONField(required=False)


class RowRawSerializer(serializers.Serializer):
    """Edited cells, keyed by the batch's own detected headers."""

    raw = serializers.DictField(
        child=serializers.CharField(allow_blank=True, max_length=2000, trim_whitespace=False),
    )


class RowEditSerializer(RowRawSerializer):
    row_number = serializers.IntegerField(min_value=1)


class RowNumberSerializer(serializers.Serializer):
    row_number = serializers.IntegerField(min_value=1)


class AttestSerializer(serializers.Serializer):
    """
    The lawful basis one HR human claims for a whole file.

    `legal_basis_note` carries the same 20-character floor as a rejection
    rationale, and for the same reason: an attestation nobody had to think about
    is not an attestation.
    """

    legal_basis = serializers.ChoiceField(
        choices=[
            LegalBasis.VOLUNTARILY_PROVIDED,
            LegalBasis.EMPLOYER_SUBSCRIPTION,
        ]
    )
    legal_basis_note = serializers.CharField(min_length=MIN_BASIS_NOTE)
    attestation_text = serializers.CharField()
    platform_account_ref = serializers.CharField(required=False, allow_blank=True)
    source_export_date = serializers.DateField(required=False, allow_null=True)


# ------------------------------------------------------------------ viewset


class CandidateImportViewSet(ScopedModelViewSet):
    access_resource = Resource.CANDIDATE
    access_actions = {
        "create": Action.IMPORT,      # upload + parse + preview
        "commit": Action.IMPORT,
        "attest": Action.IMPORT,
        "destroy": Action.IMPORT,
        "update_row": Action.IMPORT,
        "add_row": Action.IMPORT,
        "remove_row": Action.IMPORT,
        "retrieve": Action.VIEW,
        "list": Action.VIEW,
        "errors": Action.VIEW,
        "platforms": Action.VIEW,
    }
    throttle_scope = "candidate_import"
    #: Only the EXPENSIVE, abusable operations spend the hourly budget:
    #: parsing an uploaded spreadsheet and committing a batch. Everything
    #: else — the platform list, batch polling, single-row edits — is
    #: ordinary UI traffic; counting it burned the budget on page loads and
    #: locked HR out of the wizard's first step.
    THROTTLED_ACTIONS = frozenset({"create", "commit"})

    def get_throttles(self):
        if getattr(self, "action", None) in self.THROTTLED_ACTIONS:
            return super().get_throttles()
        return []
    # Multipart for the upload, JSON for attest/commit. Without JSONParser
    # those two routes answer 415 and the flow cannot be completed at all.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = ImportBatchSerializer
    #: Candidate batches only. `ImportBatch` now also stages staff lists, and
    #: without this filter an employee import would appear in the candidate
    #: wizard -- where its rows would be read as applicants and committed
    #: against a job opening it does not have.
    queryset = deferred(ImportBatch).filter(
        kind=ImportKind.CANDIDATES
    ).select_related("job_opening")
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        """
        Batch visibility is ownership-based below Scope.ALL.

        A preview payload is a raw dump of somebody's spreadsheet — every name,
        phone and address in it. A peer holding the same grant has no business
        reading it, so anything short of organisation-wide sees only its own.
        """
        from core.access import get_context
        from core.access.engine import apply_org_predicate

        # Hand-rolled scoper: routed through the shared tenant predicate so it
        # cannot drift from scope_queryset(). A staging batch is a raw dump of
        # somebody's spreadsheet, which makes an unscoped read here worse than
        # most.
        qs = apply_org_predicate(self.queryset, get_context(self.request.user))
        scope = can(self.request.user, Resource.CANDIDATE, Action.IMPORT)
        if not scope:
            return qs.none()
        if scope == Scope.ALL:
            return qs
        return qs.filter(uploaded_by=self.request.user)

    @action(detail=False, methods=["get"])
    def platforms(self, request):
        """
        What can be imported, and what cannot — with the reason.

        The unavailable ones are returned deliberately. Omitting them would let
        a user assume support is merely pending; this says what is actually
        missing and what would unblock it.
        """
        return Response(
            [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "available": spec.available,
                    "unavailable_reason": spec.unavailable_reason,
                    "notes": spec.notes,
                    "accepts": sorted(importer.ALLOWED_EXTENSIONS) if spec.available else [],
                }
                for spec in list_platforms()
            ]
        )

    def create(self, request, *args, **kwargs):
        payload = UploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        spec = get_platform(data["platform"])
        if spec is None:
            raise DRFValidationError({"platform": "Unknown platform."})

        batch = _service_call(
            importer.create_batch,
            actor=request.user,
            platform=spec,
            job_opening=data["job_opening"],
            file=data["file"],
            column_override=data.get("column_override"),
        )
        return Response(
            self.get_serializer(batch).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def attest(self, request, pk=None):
        payload = AttestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        batch = _service_call(
            importer.attest,
            actor=request.user,
            batch=self.get_object(),
            **payload.validated_data,
        )
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        result = _service_call(
            importer.commit, actor=request.user, batch=self.get_object()
        )
        return Response(
            {
                "batch": self.get_serializer(result.batch).data,
                "created": result.created,
                "updated": result.updated,
                "duplicate": result.duplicate,
                "review": result.review,
                "failed": result.failed,
                "failures": result.failures,
            }
        )

    @action(detail=True, methods=["post"], url_path="rows/update")
    def update_row(self, request, pk=None):
        """Edit one staged row's cells; validity and dedup re-resolve."""
        batch = self.get_object()
        payload = RowEditSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _service_call(
            importer.update_row,
            actor=request.user, batch=batch,
            row_number=payload.validated_data["row_number"],
            raw=payload.validated_data["raw"],
        )
        return Response(ImportRowSerializer(row).data)

    @action(detail=True, methods=["post"], url_path="rows/add")
    def add_row(self, request, pk=None):
        """Hand-add a candidate row to the staged batch."""
        batch = self.get_object()
        payload = RowRawSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = _service_call(
            importer.add_row,
            actor=request.user, batch=batch, raw=payload.validated_data["raw"],
        )
        return Response(ImportRowSerializer(row).data, status=201)

    @action(detail=True, methods=["post"], url_path="rows/remove")
    def remove_row(self, request, pk=None):
        """Drop a staged row before commit. Staging only — never a candidate."""
        batch = self.get_object()
        payload = RowNumberSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        _service_call(
            importer.remove_row,
            actor=request.user, batch=batch,
            row_number=payload.validated_data["row_number"],
        )
        return Response(status=204)

    @action(detail=True, methods=["get"])
    def errors(self, request, pk=None):
        """
        The failed-row report.

        JSON, not CSV. A CSV of candidate-derived values is a formula-injection
        vector against whoever opens it, and this endpoint exists to be opened
        by a person who is already having a bad time.
        """
        batch = self.get_object()
        rows = ImportRow.objects.filter(
            batch=batch,
            status__in=[RowStatus.INVALID, RowStatus.FAILED, RowStatus.NEEDS_REVIEW],
        ).order_by("row_number")

        return Response(
            {
                "batch": str(batch.pk),
                "filename": batch.original_filename,
                "rows": [
                    {
                        "row_number": row.row_number,
                        # Enough to find the row in their own file, without
                        # echoing the whole record back out.
                        "identifier": row.email or row.phone or row.external_id or "",
                        "status": row.status,
                        "errors": row.errors,
                        "warnings": row.warnings,
                    }
                    for row in rows
                ],
            }
        )


# ---------------------------------------------------------------- staff list


class EmployeeImportRowSerializer(serializers.ModelSerializer):
    """
    A staged employee row.

    Deliberately not the candidate row serializer: that one exposes
    `expected_ctc`, `notice_period_days` and `external_id`, which an employee
    row never carries, and a field that is always null is a field somebody
    eventually tries to fill.
    """

    employee_code = serializers.CharField(
        source="created_employee.employee_code", read_only=True, default=""
    )

    class Meta:
        model = ImportRow
        fields = [
            "id", "row_number", "first_name", "last_name", "email", "phone",
            # Every cell of the original row, keyed by header: the preview grid
            # reads the department and joining date from here, because those
            # are resolved against the database only at commit.
            "raw",
            "status", "duplicate_of_row", "created_employee", "employee_code",
            "errors", "warnings",
        ]
        read_only_fields = fields


class EmployeeImportBatchSerializer(serializers.ModelSerializer):
    rows = EmployeeImportRowSerializer(many=True, read_only=True)
    seats = serializers.SerializerMethodField()

    class Meta:
        model = ImportBatch
        fields = [
            "id", "kind", "original_filename", "file_sha256", "column_mapping",
            "detected_headers", "unmapped_headers", "status",
            "rows_total", "rows_created", "rows_duplicate", "rows_failed",
            "committed_at", "created_at", "seats", "rows",
        ]
        read_only_fields = fields

    def get_seats(self, obj) -> dict:
        """
        What the plan allows, against what this file would use.

        ADVISORY HERE, authoritative at commit. This is the number that lets
        somebody trim a file before uploading it again rather than discovering
        the limit part-way through; the commit re-checks it under a row lock,
        because between this read and that write another hire can happen.
        """
        from apps.imports.services.employee_import import committable
        from apps.platform.services.subscriptions import seats_remaining

        remaining = seats_remaining(obj.organization_id)
        needed = committable(obj).count()
        return {
            "needed": needed,
            # None means no plan and therefore no limit: a self-hosted
            # deployment that never bought a seat count.
            "remaining": remaining,
            "sufficient": remaining is None or needed <= remaining,
        }


class EmployeeUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    #: header -> canonical field, for a sheet whose column names this product
    #: has never seen. Validated against the employee spec.
    column_override = serializers.DictField(
        child=serializers.CharField(), required=False
    )


class EmployeeImportViewSet(ScopedModelViewSet):
    """
    Bulk hire from a spreadsheet: upload, review, commit.

    Separately grantable from EMPLOYEE/CREATE, following the candidate
    precedent: a role can be allowed to hire one person at a time and not two
    hundred at once, and the wider grant can be revoked without stopping
    ordinary hiring.

    ROW EDITING IS NOT HERE, and that is a scope decision rather than an
    oversight. The candidate wizard can patch a staged row because a job-board
    export arrives with cells nobody can fix at the source. A staff list has an
    owner and a source file: fixing the sheet and re-uploading keeps one
    version of the truth, and re-uploading is cheap because nothing was
    written.
    """

    access_resource = Resource.EMPLOYEE
    access_actions = {
        "create": Action.IMPORT,   # upload + parse + preview
        "commit": Action.IMPORT,
        "destroy": Action.IMPORT,
        "retrieve": Action.VIEW,
        "list": Action.VIEW,
    }
    throttle_scope = "employee_import"
    #: Only the expensive, abusable operations spend the hourly budget:
    #: parsing an upload and committing a batch. Polling a preview is ordinary
    #: UI traffic.
    THROTTLED_ACTIONS = frozenset({"create", "commit"})

    def get_throttles(self):
        if getattr(self, "action", None) in self.THROTTLED_ACTIONS:
            return super().get_throttles()
        return []

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = EmployeeImportBatchSerializer
    queryset = deferred(ImportBatch).filter(kind=ImportKind.EMPLOYEES)
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        """
        Ownership-based below organisation-wide scope, as the candidate wizard
        is: a staged batch is a raw dump of a spreadsheet full of addresses and
        phone numbers, and a peer holding the same grant has no business
        reading somebody else's upload.
        """
        from core.access import get_context
        from core.access.engine import apply_org_predicate

        qs = apply_org_predicate(self.queryset, get_context(self.request.user))
        scope = can(self.request.user, Resource.EMPLOYEE, Action.IMPORT)
        if not scope:
            return qs.none()
        if scope == Scope.ALL:
            return qs
        return qs.filter(uploaded_by=self.request.user)

    def create(self, request, *args, **kwargs):
        """Upload, parse and stage. Writes no employees and no logins."""
        from apps.imports.services.employee_import import create_employee_batch

        payload = EmployeeUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        batch = _service_call(
            create_employee_batch,
            actor=request.user,
            file=payload.validated_data["file"],
            column_override=payload.validated_data.get("column_override"),
        )
        return Response(
            self.get_serializer(batch).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        """
        Hire everyone in the batch, or refuse it.

        `SeatLimitReached` reaches the client as its own 422 code, so the SPA
        can offer an upgrade rather than render a generic refusal.
        """
        from apps.imports.services.employee_import import commit_employee_batch

        batch = self.get_object()
        result = _service_call(
            commit_employee_batch, actor=request.user, batch=batch
        )
        body = self.get_serializer(result.batch).data
        body["created"] = result.created
        body["failed"] = result.failed
        return Response(body)

    def destroy(self, request, *args, **kwargs):
        """
        Discard a staged batch.

        The rows go, because they are a copy of somebody else's spreadsheet and
        this app exists because such a copy has a shorter lawful life than the
        records it might have produced. The batch row stays, marked discarded:
        what was uploaded, by whom, and that it was abandoned is the audit
        trail.
        """
        from apps.imports.models import BatchStatus

        batch = self.get_object()
        if batch.status in (BatchStatus.COMPLETED, BatchStatus.PARTIAL):
            raise DRFValidationError(
                {"detail": "This import has already been committed."}
            )
        ImportRow.objects.filter(batch=batch).delete()
        batch.status = BatchStatus.DISCARDED
        batch.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
