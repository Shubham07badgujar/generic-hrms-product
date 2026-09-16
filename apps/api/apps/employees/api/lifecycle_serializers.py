"""
Serializers for documents, probation, onboarding, letters, accounts and assets.

A note that applies throughout: FILE FIELDS ARE NEVER SERIALIZED AS URLS. A
document or letter exposes `has_file` and a download ROUTE, and the bytes are
served by a view that re-checks scope. A storage URL in a JSON body outlives
the session that fetched it and is shareable by anyone who sees it.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.assets.models import (
    Asset,
    AssetAllocation,
    AssetCategory,
)
from apps.employees.models import (
    DocumentType,
    Employee,
    EmployeeDocument,
    ProbationReview,
)
from apps.itaccounts.models import CompanyEmailAccount
from apps.onboarding.models import (
    EmployeeLetter,
    EmployeeOnboarding,
    LetterTemplate,
    OnboardingItem,
    OnboardingTemplate,
    OnboardingTemplateItem,
)


# ===========================================================================
# Documents
# ===========================================================================


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin

class DocumentTypeSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = DocumentType
        fields = [
            "id", "name", "code", "category", "description",
            "is_mandatory", "requires_expiry", "order",
        ]


class EmployeeDocumentSerializer(serializers.ModelSerializer):
    document_type_name = serializers.CharField(source="document_type.name", read_only=True)
    category = serializers.CharField(source="document_type.category", read_only=True)
    uploaded_by_email = serializers.CharField(
        source="uploaded_by.email", read_only=True, default=None
    )
    verified_by_email = serializers.CharField(
        source="verified_by.email", read_only=True, default=None
    )
    rejected_by_email = serializers.CharField(
        source="rejected_by.email", read_only=True, default=None
    )
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=None
    )
    #: Presence, not location. The bytes come from the download route.
    has_file = serializers.SerializerMethodField()
    #: Whether somebody filed this into another person's record. Computed here
    #: rather than left to the UI to infer by comparing two email addresses —
    #: the employee's own login is not in this payload, so the UI would be
    #: guessing, and it would guess wrong for anyone whose work email differs
    #: from their sign-in.
    filed_by_someone_else = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeDocument
        fields = [
            "id", "employee", "employee_name", "employee_code", "department_name",
            "document_type", "document_type_name", "category",
            "original_filename", "content_type", "size_bytes", "has_file",
            "uploaded_by", "uploaded_by_email", "uploaded_at", "filed_by_someone_else",
            "status", "verified_by", "verified_by_email", "verified_at",
            "rejected_by", "rejected_by_email", "rejected_at", "rejection_reason",
            "issue_date", "expires_on", "notes",
        ]
        read_only_fields = fields

    def get_has_file(self, obj) -> bool:
        return bool(obj.file)

    def get_filed_by_someone_else(self, obj) -> bool:
        if obj.uploaded_by_id is None:
            # Nothing to compare against. Claiming either way would assert
            # something this row does not actually record.
            return False
        return obj.uploaded_by_id != obj.employee.user_id


class DocumentUploadSerializer(ScopedRelationsMixin, serializers.Serializer):
    document_type = serializers.PrimaryKeyRelatedField(queryset=DocumentType.objects.all())
    file = serializers.FileField()
    issue_date = serializers.DateField(required=False, allow_null=True)
    expires_on = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class DocumentRejectSerializer(serializers.Serializer):
    """
    The reason, checked for length in WORDS.

    `max_length` would be the obvious field argument and the wrong unit: the
    policy is written in words, and a character cap either cuts off a
    legitimate explanation early or lets a very long one through. The service
    re-checks this — a serializer guards the HTTP door, and the service is
    reachable from a shell and a command as well.
    """

    reason = serializers.CharField(min_length=5)

    def validate_reason(self, value: str) -> str:
        from apps.employees.services.documents import MAX_REJECTION_WORDS, count_words

        value = value.strip()
        words = count_words(value)
        if words > MAX_REJECTION_WORDS:
            raise serializers.ValidationError(
                f"The reason is {words} words; the limit is {MAX_REJECTION_WORDS}."
            )
        return value


# ===========================================================================
# Probation
# ===========================================================================


class ProbationReviewSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=None
    )
    reviewer_name = serializers.CharField(source="reviewer.full_name", read_only=True, default=None)
    decided_by_email = serializers.CharField(
        source="decided_by.email", read_only=True, default=None
    )
    is_decided = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProbationReview
        fields = [
            "id", "employee", "employee_name", "employee_code", "department_name",
            "probation_end_date",
            "reviewer", "reviewer_name", "reviewed_at",
            "performance_rating", "reliability_rating", "role_specific_rating",
            "strengths", "areas_for_improvement", "recommendation", "reviewer_notes",
            "decision", "decided_by", "decided_by_email", "decided_at",
            "rationale", "extended_to", "confirmation_letter", "is_decided",
            "notified_30d", "notified_7d", "notified_overdue_on",
        ]
        read_only_fields = fields


class ProbationAssessmentSerializer(serializers.Serializer):
    recommendation = serializers.ChoiceField(choices=["confirm", "extend", "terminate"])
    performance_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=5
    )
    reliability_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=5
    )
    role_specific_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=5
    )
    strengths = serializers.CharField(required=False, allow_blank=True, default="")
    areas_for_improvement = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ProbationDecisionSerializer(serializers.Serializer):
    """
    HR's decision.

    The 20-character rationale floor for EXTEND and TERMINATE is checked here
    AND in the service, which is the authority. A confirmation needs no
    defence, so the rule is conditional rather than blanket.
    """

    decision = serializers.ChoiceField(choices=["confirm", "extend", "terminate"])
    rationale = serializers.CharField(required=False, allow_blank=True, default="")
    extended_to = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        decision = attrs["decision"]
        if decision in {"extend", "terminate"} and len(attrs.get("rationale", "").strip()) < 20:
            raise serializers.ValidationError(
                {
                    "rationale": (
                        f"A decision to {decision} requires a written rationale of at "
                        f"least 20 characters, recorded permanently."
                    )
                }
            )
        if decision == "extend" and not attrs.get("extended_to"):
            raise serializers.ValidationError(
                {"extended_to": "An extension must name the new probation end date."}
            )
        return attrs


# ===========================================================================
# Onboarding
# ===========================================================================


class OnboardingTemplateItemSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    document_type_name = serializers.CharField(
        source="document_type.name", read_only=True, default=None
    )

    class Meta:
        model = OnboardingTemplateItem
        fields = [
            "id", "title", "description", "kind", "owner",
            "document_type", "document_type_name",
            "is_mandatory", "due_offset_days", "order",
        ]


class OnboardingTemplateSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    items = OnboardingTemplateItemSerializer(many=True, read_only=True)
    department_name = serializers.CharField(
        source="department.name", read_only=True, default=None
    )

    class Meta:
        model = OnboardingTemplate
        fields = [
            "id", "name", "description", "department", "department_name",
            "employment_type", "is_default", "items",
        ]


class OnboardingItemSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(
        source="assigned_to.full_name", read_only=True, default=None
    )
    document_type_name = serializers.CharField(
        source="document_type.name", read_only=True, default=None
    )
    is_overdue = serializers.BooleanField(read_only=True)
    is_done = serializers.BooleanField(read_only=True)
    completed_by_email = serializers.CharField(
        source="completed_by.email", read_only=True, default=None
    )

    class Meta:
        model = OnboardingItem
        fields = [
            "id", "title", "description", "kind", "owner",
            "assigned_to", "assigned_to_name",
            "document_type", "document_type_name", "document",
            "is_mandatory", "due_date", "order", "status",
            "completed_at", "completed_by", "completed_by_email",
            "notes", "is_overdue", "is_done",
        ]
        read_only_fields = fields


class EmployeeOnboardingSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_code", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name", read_only=True, default=None
    )
    items = OnboardingItemSerializer(many=True, read_only=True)
    completed_count = serializers.SerializerMethodField()
    total_count = serializers.SerializerMethodField()
    outstanding_mandatory_count = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeOnboarding
        fields = [
            "id", "employee", "employee_name", "employee_code", "department_name",
            "template", "template_name", "joining_date", "status", "completed_at",
            "notes", "items", "completed_count", "total_count",
            "outstanding_mandatory_count",
        ]
        read_only_fields = fields

    def get_completed_count(self, obj) -> int:
        return obj.progress[0]

    def get_total_count(self, obj) -> int:
        return obj.progress[1]

    def get_outstanding_mandatory_count(self, obj) -> int:
        return obj.outstanding_mandatory.count()


class OnboardingItemActionSerializer(ScopedRelationsMixin, serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    document = serializers.PrimaryKeyRelatedField(
        queryset=EmployeeDocument.objects.all(), required=False, allow_null=True
    )
    file = serializers.FileField(required=False, allow_null=True)


class OnboardingWaiveSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5)


# ===========================================================================
# Letters
# ===========================================================================


class LetterTemplateSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = LetterTemplate
        fields = ["id", "name", "letter_type", "subject", "version", "is_default"]


class EmployeeLetterSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    generated_by_email = serializers.CharField(
        source="generated_by.email", read_only=True, default=None
    )
    has_pdf = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeLetter
        fields = [
            "id", "employee", "employee_name", "letter_type",
            "template", "template_name", "template_version",
            "subject", "status", "generated_by", "generated_by_email",
            "generated_at", "issued_at", "acknowledged_at", "has_pdf",
        ]
        read_only_fields = fields

    def get_has_pdf(self, obj) -> bool:
        return bool(obj.pdf_file)


class LetterGenerateSerializer(ScopedRelationsMixin, serializers.Serializer):
    letter_type = serializers.CharField()
    template = serializers.PrimaryKeyRelatedField(
        queryset=LetterTemplate.objects.all(), required=False, allow_null=True
    )
    context_extra = serializers.DictField(required=False, default=dict)


# ===========================================================================
# Company accounts
# ===========================================================================


class CompanyEmailAccountSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    """
    Metadata only.

    There is no credential field here because there is none on the model. See
    `apps.itaccounts.models` for why that is a design decision rather than an
    omission.
    """

    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    requested_by_email = serializers.CharField(
        source="requested_by.email", read_only=True, default=None
    )
    provisioned_by_email = serializers.CharField(
        source="provisioned_by.email", read_only=True, default=None
    )

    class Meta:
        model = CompanyEmailAccount
        fields = [
            "id", "employee", "employee_name", "email_address", "provider",
            "external_account_id", "status",
            "requested_by", "requested_by_email", "requested_at",
            "provisioned_by", "provisioned_by_email", "provisioned_at",
            "suspended_at", "deprovisioned_at", "notes",
        ]
        read_only_fields = [
            "status", "requested_by", "requested_at",
            "provisioned_by", "provisioned_at", "suspended_at", "deprovisioned_at",
        ]


class AccountProvisionSerializer(serializers.Serializer):
    external_account_id = serializers.CharField(required=False, allow_blank=True, default="")


# ===========================================================================
# Assets
# ===========================================================================


class AssetCategorySerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = AssetCategory
        fields = ["id", "name", "code", "description", "requires_serial", "is_returnable"]


class AssetSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    #: Optional on purpose. The register form asks HR for a name, a tag and
    #: little else — an omitted category lands in the "General" bucket rather
    #: than forcing a taxonomy on people who never asked for one.
    category = serializers.PrimaryKeyRelatedField(
        queryset=AssetCategory.objects.filter(is_active=True), required=False
    )
    category_name = serializers.CharField(source="category.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)
    held_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id", "asset_tag", "category", "category_name", "name",
            "serial_number", "make", "model",
            "purchase_date", "purchase_cost", "warranty_expires_on", "vendor",
            "location", "location_name", "status", "condition", "notes",
            "held_by_name",
        ]
        #: Status belongs to the allocation lifecycle: a new asset is born
        #: Available, `allocate()` makes it Assigned, `return_asset()` makes it
        #: Available again. An edit writing it directly would desynchronise the
        #: register from the allocations that are the actual source of truth.
        read_only_fields = ["status"]

    def create(self, validated_data):
        if "category" not in validated_data:
            validated_data["category"], _ = AssetCategory.objects.get_or_create(
                code="general",
                defaults={
                    "name": "General",
                    "description": "Default bucket for uncategorised assets.",
                    "requires_serial": False,
                    "is_returnable": True,
                },
            )
        return super().create(validated_data)

    def get_held_by_name(self, obj) -> str | None:
        allocation = obj.current_allocation
        return allocation.employee.full_name if allocation else None


class AssetAllocationSerializer(serializers.ModelSerializer):
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_category = serializers.CharField(source="asset.category.name", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    allocated_by_email = serializers.CharField(
        source="allocated_by.email", read_only=True, default=None
    )
    received_by_email = serializers.CharField(
        source="received_by.email", read_only=True, default=None
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetAllocation
        fields = [
            "id", "asset", "asset_tag", "asset_name", "asset_category",
            "employee", "employee_name",
            "allocated_at", "allocated_by", "allocated_by_email",
            "condition_at_allocation", "allocation_notes", "expected_return_date",
            "returned_at", "received_by", "received_by_email",
            "condition_at_return", "return_notes",
            "status", "write_off_reason", "is_open",
        ]
        read_only_fields = fields


class AllocateSerializer(ScopedRelationsMixin, serializers.Serializer):
    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.all())
    employee = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.filter(is_active=True)
    )
    condition = serializers.CharField(required=False, default="good")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    expected_return_date = serializers.DateField(required=False, allow_null=True)


class BulkAllocateSerializer(ScopedRelationsMixin, serializers.Serializer):
    """
    Several assets into one pair of hands, in one request.

    All-or-nothing: the view wraps the loop in a transaction, so "one of the
    four was already assigned" leaves nothing half-done.
    """

    employee = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.filter(is_active=True)
    )
    assets = serializers.PrimaryKeyRelatedField(
        queryset=Asset.objects.filter(is_active=True), many=True, allow_empty=False
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnSerializer(serializers.Serializer):
    condition = serializers.CharField(required=False, default="good")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    to_maintenance = serializers.BooleanField(required=False, default=False)


class WriteOffSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5)


# ===========================================================================
# Lifecycle
# ===========================================================================


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    effective_date = serializers.DateField(required=False, allow_null=True)
