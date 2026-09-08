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


class DepartmentSerializer(serializers.ModelSerializer):
    head_employee_name = serializers.CharField(
        source="head_employee.full_name", read_only=True, default=None
    )

    class Meta:
        model = Department
        fields = [
            "id", "name", "code", "kind", "description",
            "parent_department", "head_employee", "head_employee_name",
        ]


class DesignationSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source="department.name", read_only=True, default=None
    )
    #: Optional in the DATABASE (null=True) but the model omits blank=True, so
    #: DRF would demand it on create. A designation genuinely may be org-wide.
    department = serializers.PrimaryKeyRelatedField(
        queryset=Designation._meta.get_field("department").related_model.objects.filter(
            is_active=True
        ),
        required=False,
        allow_null=True,
        default=None,
    )

    class Meta:
        model = Designation
        fields = ["id", "title", "department", "department_name", "description"]


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "name", "code", "city", "state", "pincode", "is_head_office"]


class EmployeeLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeLevel
        fields = ["id", "name", "code", "layer", "rank"]


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "code", "department", "parent_team", "head_employee"]


class RoleSerializer(serializers.ModelSerializer):
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


class RoleWriteSerializer(serializers.ModelSerializer):
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
    #: Booleans, not URLs: the images feed generated letters and are never
    #: served to the browser from here.
    has_logo = serializers.SerializerMethodField()
    has_signature = serializers.SerializerMethodField()

    def get_has_logo(self, row) -> bool:
        return bool(row.logo)

    def get_has_signature(self, row) -> bool:
        return bool(row.signature)

    class Meta:
        model = OrgSettings
        fields = [
            "id", "name", "legal_name", "currency", "timezone",
            "financial_year_start_month", "employee_code_prefix",
            "signatory_name", "signatory_designation",
            "logo", "signature", "has_logo", "has_signature",
        ]
        extra_kwargs = {
            "logo": {"write_only": True, "required": False},
            "signature": {"write_only": True, "required": False},
        }
