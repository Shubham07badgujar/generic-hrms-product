"""
Organization structure: readable by the forms, managed by the Admin Panel.

These endpoints exist because the SPA cannot render a job form, an employee
form or a role picker without knowing which departments, designations,
locations, levels and roles exist — and, since the org-management phase,
because the Admin maintains those catalogues from here rather than from the
Django admin.

Writes are gated by the ordinary matrix: only Admin holds CREATE/EDIT/DELETE
on DEPARTMENT, DESIGNATION and LOCATION, so opening the routes widens nothing
for anyone else. Two rules keep the structure safe to edit:

  1. DELETE IS DEACTIVATION. Nothing is ever removed from the database —
     history, scoping and audit rows all point into these tables.
  2. A ROW SOMETHING STILL USES CANNOT BE DEACTIVATED. Retiring a department
     that still employs people would leave those people's scope dangling; the
     refusal names what is still attached so the admin can move it first.

`EmployeeLevel` and `Team` have no Resource of their own in the catalog. They
are gated on DESIGNATION and DEPARTMENT respectively — the resources they are
structurally part of — rather than inventing permission surfaces the approved
matrix never signed off.
"""

from __future__ import annotations

from core.api.exceptions import BusinessRuleError

from apps.accounts.models import Role
from apps.accounts.services import role_admin
from apps.audit.events import record_event
from apps.organization.models import (
    Department,
    Designation,
    EmployeeLevel,
    Location,
    OrgSettings,
    Team,
)
from core.access import Resource, require
from core.access.drf import ScopedModelViewSet, ScopedReadOnlyModelViewSet
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.views import APIView

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from core.access import Action

from .serializers import (
    DepartmentSerializer,
    OrgSettingsSerializer,
    DesignationSerializer,
    EmployeeLevelSerializer,
    LocationSerializer,
    PermissionCellSerializer,
    RoleSerializer,
    RoleWriteSerializer,
    TeamSerializer,
)


def _call(fn, **kwargs):
    # Run a service, translating its Django ValidationError into a DRF 400.
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


class _ReadOnlyReferenceViewSet(ScopedReadOnlyModelViewSet):
    """Reference lists are small and picker-consumed, so never paginated."""

    pagination_class = None


class _ReferenceViewSet(ScopedModelViewSet):
    """
    A managed catalogue: full CRUD for whoever the matrix grants it to,
    deactivation instead of deletion, and refusal to retire anything in use.
    """

    pagination_class = None

    #: (attribute path on the instance, human label) pairs whose ACTIVE rows
    #: block deactivation. Subclasses enumerate what genuinely depends on them.
    in_use: tuple[tuple[str, str], ...] = ()

    def perform_destroy(self, instance):
        blockers = []
        for relation, label in self.in_use:
            if getattr(instance, relation).filter(is_active=True).exists():
                blockers.append(label)
        if blockers:
            raise BusinessRuleError(
                {
                    "detail": (
                        f"'{instance}' still has active {', '.join(blockers)}. "
                        "Move or retire those first — deactivating this now "
                        "would leave them pointing at nothing."
                    )
                }
            )
        # Soft: BaseModel.delete() flips is_active. The row stays for history.
        instance.delete()


class OrgBrandingView(APIView):
    """
    GET /org/branding/ — the organisation's public face: name and logo.

    Deliberately UNAUTHENTICATED. The login screen and the public job
    application form both need to say whose system this is before anyone has
    signed in, so this endpoint is the one place the product's identity comes
    from — no company name is compiled into the frontend. It exposes nothing
    beyond what the login page itself shows: display name, legal name, and
    the logo. Everything else on OrgSettings stays behind ORG_SETTINGS/VIEW.
    """

    #: Genuinely public, and therefore declared rather than left unmapped.
    #: `manage.py check` fails on any API view that declares neither
    #: `access_resource` nor this flag; without it this view was an unmapped
    #: hole that the check would have caught, had the check been running.
    access_exempt = True

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        row = OrgSettings.objects.first()
        if row is None:
            return Response({"name": "HRMS", "legal_name": "", "logo": None})
        logo = None
        if row.logo:
            try:
                logo = request.build_absolute_uri(row.logo.url)
            except ValueError:
                logo = None
        return Response({
            "name": row.name or "HRMS",
            "legal_name": row.legal_name or "",
            "logo": logo,
        })


class OrgSettingsView(APIView):
    """
    GET/PATCH /org/settings/ — the organisation's one settings row.

    Carries the letterhead used by generated documents: name, legal name,
    signatory, logo and signature images. VIEW to read, EDIT to change —
    per the matrix that is Admin's surface.
    """

    #: RBACPermission (the default permission class) reads these; without
    #: them a bare APIView fails closed and everyone gets 403.
    access_resource = Resource.ORG_SETTINGS
    access_actions = {"GET": Action.VIEW, "PATCH": Action.EDIT}

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _row(self):
        row = OrgSettings.objects.first()
        if row is None:
            row = OrgSettings.objects.create(name="Organisation")
        return row

    def get(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.VIEW)
        return Response(OrgSettingsSerializer(self._row()).data)

    def patch(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.EDIT)
        row = self._row()
        watched = ["name", "legal_name", "signatory_name", "signatory_designation"]
        before = {name: getattr(row, name) for name in watched}
        before["has_logo"], before["has_signature"] = bool(row.logo), bool(row.signature)
        serializer = OrgSettingsSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        record_event(
            updated,
            actor=request.user,
            entity_type="organization.OrgSettings",
            verb="update",
            resource=Resource.ORG_SETTINGS,
            before=before,
            after={
                **{name: getattr(updated, name) for name in watched},
                "has_logo": bool(updated.logo),
                "has_signature": bool(updated.signature),
            },
        )
        return Response(OrgSettingsSerializer(updated).data)


class DepartmentViewSet(_ReferenceViewSet):
    access_resource = Resource.DEPARTMENT
    in_use = (
        ("employees", "employees"),
        ("job_openings", "job openings"),
        ("designations", "designations"),
        ("teams", "teams"),
        ("children", "sub-departments"),
    )
    queryset = (
        Department.objects.filter(is_active=True)
        .select_related("head_employee", "parent_department")
        .order_by("name")
    )
    serializer_class = DepartmentSerializer
    filterset_fields = ["kind", "parent_department"]


class DesignationViewSet(_ReferenceViewSet):
    access_resource = Resource.DESIGNATION
    in_use = (("employees", "employees"),)
    queryset = (
        Designation.objects.filter(is_active=True).select_related("department").order_by("title")
    )
    serializer_class = DesignationSerializer
    filterset_fields = ["department"]


class LocationViewSet(_ReferenceViewSet):
    access_resource = Resource.LOCATION
    in_use = (("employees", "employees"),)
    queryset = Location.objects.filter(is_active=True).order_by("name")
    serializer_class = LocationSerializer


class EmployeeLevelViewSet(_ReferenceViewSet):
    #: Seniority bands are part of the designation surface — see module docstring.
    access_resource = Resource.DESIGNATION
    in_use = (("employees", "employees"),)
    queryset = EmployeeLevel.objects.filter(is_active=True).order_by("rank")
    serializer_class = EmployeeLevelSerializer


class TeamViewSet(_ReadOnlyReferenceViewSet):
    access_resource = Resource.DEPARTMENT
    queryset = Team.objects.filter(is_active=True).select_related("department").order_by("name")
    serializer_class = TeamSerializer
    filterset_fields = ["department"]


class RoleViewSet(ScopedModelViewSet):
    """
    The role catalogue — for pickers, and now for the Admin Panel.

    Reads are what they always were: gated on ROLE/VIEW, held by anyone who
    must choose a role from a list, conferring no authority over anything.

    Writes ride the same engine as everywhere else, with two layers on top of
    the matrix: the engine's user-management gate strips ROLE writes from any
    principal without `can_manage_users`, and `role_admin` — the only path
    these routes call — refuses the operations that would change what the
    engine itself means (minting user managers, touching system-role
    structure, cells the clamp would silently ignore).
    """

    access_resource = Resource.ROLE
    pagination_class = None
    queryset = Role.objects.filter(is_active=True).order_by("layer", "name")
    serializer_class = RoleSerializer
    filterset_fields = ["layer", "is_grantable", "department_kind"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    access_actions = {
        "permissions": Action.VIEW,
        "set_permissions": Action.EDIT,
    }

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return RoleWriteSerializer
        return RoleSerializer

    def create(self, request, *args, **kwargs):
        payload = RoleWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        role = _call(role_admin.create_role, actor=request.user, **payload.validated_data)
        return Response(RoleSerializer(role).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        role = self.get_object()
        payload = RoleWriteSerializer(instance=role, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        fields = dict(payload.validated_data)
        fields.pop("code", None)  # immutable after birth
        role = _call(role_admin.update_role, actor=request.user, role=role, **fields)
        return Response(RoleSerializer(role).data)

    def perform_destroy(self, instance):
        _call(role_admin.deactivate_role, actor=self.request.user, role=instance)

    @action(detail=True, methods=["get"])
    def permissions(self, request, pk=None):
        """The role's live matrix: every non-deny cell, with its provenance."""
        role = self.get_object()
        rows = role.permissions.filter(is_active=True).order_by("resource", "action")
        return Response(
            [
                {
                    "resource": row.resource,
                    "action": row.action,
                    "scope": row.scope,
                    "is_customized": row.is_customized,
                }
                for row in rows
            ]
        )

    @action(detail=True, methods=["put", "post"], url_path="permissions/set")
    def set_permissions(self, request, pk=None):
        role = self.get_object()
        payload = PermissionCellSerializer(data=request.data.get("cells", []), many=True)
        payload.is_valid(raise_exception=True)
        _call(
            role_admin.set_permissions,
            actor=request.user,
            role=role,
            cells=payload.validated_data,
        )
        return self.permissions(request, pk=pk)
