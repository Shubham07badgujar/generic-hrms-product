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

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.services import role_admin
from apps.audit.events import record_event
from apps.organization.models import (
    Department,
    Designation,
    EmployeeLevel,
    Location,
    Organization,
    OrgSettings,
    OrgStatus,
    Team,
)
from apps.organization.setup import SetupError, finish_setup, setup_state
from core.access import Action, Resource, require
from core.access.drf import ScopedModelViewSet, ScopedReadOnlyModelViewSet
from core.api.exceptions import BusinessRuleError

from .serializers import (
    DepartmentSerializer,
    DesignationSerializer,
    EmployeeLevelSerializer,
    LocationSerializer,
    OrgSettingsSerializer,
    PermissionCellSerializer,
    RoleSerializer,
    RoleWriteSerializer,
    TeamSerializer,
)
from core.querysets import deferred


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

    #: Statuses whose identity may be shown to an anonymous caller. A suspended
    #: or archived organization is indistinguishable from one that never
    #: existed -- see the 404 below.
    PUBLIC_STATUSES = frozenset(
        {OrgStatus.PENDING_SETUP, OrgStatus.TRIAL, OrgStatus.ACTIVE}
    )

    def _resolve(self, request):
        """
        Which organization is being asked about, with no principal to ask.

        Order matters, and the LAST step matters most:

          1. an explicit ?org=<slug>
          2. exactly one organization in the database — which keeps a
             single-organization self-hosted deployment behaving as it always
             did, with no slug and no configuration
          3. nothing

        There is deliberately no "otherwise use the first one". Serving one
        customer's name and logo on another customer's login page is a
        cross-tenant identity leak, and it is the same fail-open shape as the
        incident this whole design exists to prevent: when the answer is
        unknown, the honest result is no answer.
        """
        slug = (request.query_params.get("org") or "").strip()
        public = Organization.objects.filter(status__in=self.PUBLIC_STATUSES)
        if slug:
            return public.filter(slug=slug).first()
        if Organization.objects.count() == 1:
            return public.first()
        return None

    def get(self, request):
        organization = self._resolve(request)
        if organization is None:
            # Identical for "no such organization" and "suspended", so this
            # endpoint cannot be used to enumerate customers or to learn which
            # of them have stopped paying.
            return Response(status=404)

        logo = None
        if organization.logo:
            try:
                logo = request.build_absolute_uri(organization.logo.url)
            except ValueError:
                # MEDIA_URL is None in production: files are served through
                # authenticated views, never as URLs. The branding logo is the
                # one image that legitimately has no authenticated viewer, so
                # it has never rendered there. Left as None rather than papered
                # over -- serving it needs its own deliberate route.
                logo = None
        return Response({
            "name": organization.name or "HRMS",
            "legal_name": organization.legal_name or "",
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
        """This caller's organization settings, created on first access."""
        from core.access.context import get_context

        return OrgSettings.for_org(get_context(self.request).organization_id)

    def get(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.VIEW)
        return Response(OrgSettingsSerializer(self._row()).data)

    def patch(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.EDIT)
        row = self._row()
        # Identity is read through `.organization`; the operational fields are
        # on the settings row itself. Audited together because they are edited
        # together on one screen.
        watched = {
            "name": lambda r: r.organization.name,
            "legal_name": lambda r: r.organization.legal_name,
            "signatory_name": lambda r: r.signatory_name,
            "signatory_designation": lambda r: r.signatory_designation,
        }
        before = {name: read(row) for name, read in watched.items()}
        before["has_logo"] = bool(row.organization.logo)
        before["has_signature"] = bool(row.signature)
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
                **{name: read(updated) for name, read in watched.items()},
                "has_logo": bool(updated.organization.logo),
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
        deferred(Department).filter(is_active=True)
        .select_related("head_employee", "parent_department")
        .order_by("name")
    )
    serializer_class = DepartmentSerializer
    filterset_fields = ["kind", "parent_department"]


class DesignationViewSet(_ReferenceViewSet):
    access_resource = Resource.DESIGNATION
    in_use = (("employees", "employees"),)
    queryset = (
        deferred(Designation).filter(is_active=True).select_related("department").order_by("title")
    )
    serializer_class = DesignationSerializer
    filterset_fields = ["department"]


class LocationViewSet(_ReferenceViewSet):
    access_resource = Resource.LOCATION
    in_use = (("employees", "employees"),)
    queryset = deferred(Location).filter(is_active=True).order_by("name")
    serializer_class = LocationSerializer


class EmployeeLevelViewSet(_ReferenceViewSet):
    #: Seniority bands are part of the designation surface — see module docstring.
    access_resource = Resource.DESIGNATION
    in_use = (("employees", "employees"),)
    queryset = deferred(EmployeeLevel).filter(is_active=True).order_by("rank")
    serializer_class = EmployeeLevelSerializer


class TeamViewSet(_ReadOnlyReferenceViewSet):
    access_resource = Resource.DEPARTMENT
    queryset = deferred(Team).filter(is_active=True).select_related("department").order_by("name")
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
    queryset = deferred(Role).filter(is_active=True).order_by("layer", "name")
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


class SetupStateView(APIView):
    """
    GET  /org/setup/         — every wizard step and whether it is done
    POST /org/setup/finish/  — the one transition out of PENDING_SETUP

    `ORG_SETTINGS` rather than a new resource, deliberately. The wizard is not
    a new kind of authority: it writes nothing of its own, and everything it
    links to is already governed by the resource that owns that screen. Adding
    a SETUP resource to the matrix would mean an Organization Admin could edit
    their own roles to remove it, which for a wizard is meaningless and for the
    catalogue is one more row nobody needed.
    """

    access_resource = Resource.ORG_SETTINGS
    access_actions = {"GET": Action.VIEW, "POST": Action.EDIT}

    def _organization(self):
        from core.access.context import get_context

        return Organization.objects.get(pk=get_context(self.request).organization_id)

    def get(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.VIEW)
        return Response(setup_state(self._organization()))

    def post(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.EDIT)
        organization = self._organization()
        try:
            finish_setup(organization, actor=request.user)
        except SetupError as exc:
            # 422, not 400: the request is well-formed and the caller is
            # entitled to make it. The system will not allow it YET, which is a
            # business rule, and the SPA renders those differently from a
            # validation error on a field.
            raise BusinessRuleError(str(exc)) from exc
        return Response(setup_state(organization))


class MyPlanView(APIView):
    """
    GET /org/plan/ — what this organization is on, and how much of it is used.

    READ ONLY, and that is the product decision rather than an omission. A
    customer does not change their own plan through the HR product: that is a
    commercial conversation, and an endpoint that let an Organization Admin
    upgrade themselves would be a billing decision made by whoever happened to
    hold the role.

    `ORG_SETTINGS/VIEW` rather than a new resource: this is the same screen
    family as the rest of organization settings, and a PLAN resource in the
    runtime-editable matrix would be one an Admin could grant themselves and
    one more row in a catalogue that deliberately stays small.
    """

    access_resource = Resource.ORG_SETTINGS
    access_actions = {"GET": Action.VIEW}

    def get(self, request):
        require(request.user, Resource.ORG_SETTINGS, Action.VIEW)

        from apps.platform.models import Subscription
        from apps.platform.services.subscriptions import (
            active_employee_count,
        )
        from apps.platform.services.subscriptions import (
            seats_remaining as subscription_seats_remaining,
        )
        from core.access.context import get_context
        from core.access.features import FeatureCode

        organization_id = get_context(request).organization_id
        subscription = (
            Subscription.objects.filter(
                organization_id=organization_id, is_active=True
            )
            .select_related("plan")
            .first()
        )
        used = active_employee_count(organization_id)

        if subscription is None:
            # A deployment that sells nothing. Reported as unlimited rather
            # than as an error, because that is what it is.
            return Response(
                {
                    "plan": None,
                    "status": None,
                    "features": sorted(str(f) for f in FeatureCode),
                    "employees_used": used,
                    "employee_limit": None,
                    "seats_remaining": None,
                    "storage_limit_mb": None,
                }
            )

        limit = subscription.employee_limit
        return Response(
            {
                "plan": {
                    "code": subscription.plan.code,
                    "name": subscription.plan.name,
                    "description": subscription.plan.description,
                    "support_level": subscription.plan.support_level,
                },
                "status": subscription.status,
                "features": subscription.enabled_features,
                "employees_used": used,
                "employee_limit": limit,
                # One definition, shared with the employee importer, so the
                # number a person reads here and the number that gates a bulk
                # hire cannot drift apart.
                "seats_remaining": subscription_seats_remaining(organization_id),
                # Surfaced, not enforced. A storage cap that silently broke a
                # payroll run's PDF generation would be worse than no cap, so
                # this is a number the customer can see and act on rather than
                # a wall they hit.
                "storage_limit_mb": subscription.plan.storage_limit_mb,
                "trial_ends_at": subscription.ends_at,
            }
        )


class OrganizationExportView(APIView):
    """
    GET /org/export/ -- the organization's whole record, as a ZIP of CSVs.

    `access_exempt`, and deliberately so: the rule is not a grant. In a
    cancelled organization the grant matrix resolves to nothing -- that is the
    suspension gate working -- so an RBAC check here would refuse exactly the
    customer this route exists for. `apps.organization.export.refusal_for`
    holds the rule instead (Admin of their own organization; operational, or
    cancelled within the export window) and is the same function `/me/` asks,
    so the SPA's button and this answer agree.

    It is also the ONLY route a cancelled organization reaches beyond sign-in,
    identity and branding; see `SUSPENDED_ALLOWED_PREFIXES`.
    """

    permission_classes = [IsAuthenticated]
    access_exempt = True

    def get(self, request):
        from django.http import HttpResponse

        from apps.organization.export import ExportRefused, build_export

        try:
            result = build_export(request.user)
        except ExportRefused as refusal:
            raise BusinessRuleError(str(refusal)) from refusal

        response = HttpResponse(result.content, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        # A whole company's records: never cached by anything in between.
        response["Cache-Control"] = "no-store"
        return response
