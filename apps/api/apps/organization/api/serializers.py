"""Organization reference serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import Role
from apps.organization.models import (
    Department,
    Designation,
    EmployeeLevel,
    Location,
    OrgSettings,
    Team,
)


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin
from core.querysets import deferred

class DepartmentSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    head_employee_name = serializers.CharField(
        source="head_employee.full_name", read_only=True, default=None
    )

    class Meta:
        model = Department
        fields = [
            "id", "name", "code", "kind", "description",
            "parent_department", "head_employee", "head_employee_name",
        ]


class DesignationSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    department_name = serializers.CharField(
        source="department.name", read_only=True, default=None
    )
    #: Optional in the DATABASE (null=True) but the model omits blank=True, so
    #: DRF would demand it on create. A designation genuinely may be org-wide.
    department = serializers.PrimaryKeyRelatedField(
        queryset=deferred(Department).filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    class Meta:
        model = Designation
        fields = ["id", "title", "department", "department_name", "description"]


class LocationSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name", "code", "city", "state", "pincode", "is_head_office"]


class EmployeeLevelSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = EmployeeLevel
        fields = ["id", "name", "code", "layer", "rank"]


class TeamSerializer(ScopedRelationsMixin, OrgScopedUniqueMixin, serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "code", "department", "parent_team", "head_employee"]


class RoleSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    """
    The read shape, used everywhere a role is displayed or picked.
    `is_system` tells the Admin Panel which rows have immutable structure.
    """

    class Meta:
        model = Role
        fields = [
            "id", "code", "name", "layer", "description",
            "is_read_only", "can_manage_users", "requires_employee",
            "is_grantable", "is_system", "department_kind", "dashboard_key",
        ]
        read_only_fields = fields


class RoleWriteSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    """
    What the Admin may AUTHOR. Deliberately absent: `is_read_only`,
    `can_manage_users`, `is_system` — the three flags that change what the
    access engine means, which stay a code review rather than a form field.
    The service enforces the same rule; this shape just keeps the form honest.
    """

    #: Declared explicitly: the model marks `code` editable=False (immutable
    #: after birth), which makes ModelSerializer silently DROP it — and a
    #: create route without a code cannot create anything.
    code = serializers.SlugField(max_length=50, required=True)

    class Meta:
        model = Role
        fields = ["code", "name", "layer", "description", "is_grantable", "department_kind"]

    def validate_code(self, value: str) -> str:
        return value.strip().lower().replace(" ", "_").replace("-", "_")


class PermissionCellSerializer(serializers.Serializer):
    """One matrix cell. scope 0 removes it — absence is deny."""

    resource = serializers.CharField()
    action = serializers.CharField()
    scope = serializers.IntegerField(min_value=0, max_value=4)


class OrgSettingsSerializer(serializers.ModelSerializer):
    """
    The Organisation → Settings form.

    Company identity (name, legal name, currency, timezone, logo) moved to
    `Organization`; the statutory and operational particulars stayed on
    `OrgSettings`. They are presented as ONE payload deliberately -- the split
    is a modelling decision about where a hot counter and a jurisdiction's
    registration numbers belong, and there is no reason to make an
    administrator learn about it or to break the frontend's contract over it.
    """

    name = serializers.CharField(source="organization.name")
    legal_name = serializers.CharField(
        source="organization.legal_name", required=False, allow_blank=True
    )
    currency = serializers.CharField(source="organization.currency", required=False)
    timezone = serializers.CharField(source="organization.timezone", required=False)
    logo = serializers.ImageField(
        source="organization.logo", write_only=True, required=False, allow_null=True
    )

    #: Booleans, not URLs: the images feed generated letters and are never
    #: served to the browser from here.
    has_logo = serializers.SerializerMethodField()
    has_signature = serializers.SerializerMethodField()

    def get_has_logo(self, row) -> bool:
        return bool(row.organization.logo)

    def get_has_signature(self, row) -> bool:
        return bool(row.signature)

    def update(self, instance, validated_data):
        """Split the flat payload back across the two rows it spans."""
        organization_fields = validated_data.pop("organization", {})
        if organization_fields:
            for field, value in organization_fields.items():
                setattr(instance.organization, field, value)
            instance.organization.save(
                update_fields=[*organization_fields, "updated_at"]
            )
        return super().update(instance, validated_data)

    class Meta:
        model = OrgSettings
        fields = [
            "id", "name", "legal_name", "currency", "timezone",
            "financial_year_start_month", "employee_code_prefix",
            "signatory_name", "signatory_designation",
            "logo", "signature", "has_logo", "has_signature",
        ]
        extra_kwargs = {
            "signature": {"write_only": True, "required": False},
        }
