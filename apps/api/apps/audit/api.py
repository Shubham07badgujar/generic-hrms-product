"""
Audit viewer API.

Read-only, and structurally so: `AuditLog` refuses writes and deletes in
`save()`/`delete()`, and this exposes list and retrieve only. There is no
endpoint that could edit an audit record, which is the point of an audit
record.

Scoping runs through the ordinary engine on `subject_employee`, so a Department
Head sees what happened to their people — including when HR or Finance did it —
while an organisation-level event with no subject stays visible only at ALL
scope. Managers and below hold no AUDIT_LOG grant at all and get 403.
"""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from core.access import Action, Resource, can, scope_queryset
from core.access.drf import ScopedReadOnlyModelViewSet
from rest_framework.routers import DefaultRouter

from .models import AuditAction, AuditLog

#: Actions a reviewer looks for first. Surfaced as a filter shortcut because
#: "show me every override and reversal" is the single most common audit
#: question, and making people build it from a multi-select loses it.
SENSITIVE_ACTIONS = [
    AuditAction.OVERRIDE,
    AuditAction.REVERSE,
    AuditAction.REJECT,
    AuditAction.PERMISSION_CHANGE,
    AuditAction.ROLE_CHANGE,
    AuditAction.CREDENTIAL_ISSUE,
    AuditAction.CREDENTIAL_VIEW,
    AuditAction.ACCESS_PII,
    AuditAction.EXPORT,
]


from core.api.serializers import ScopedRelationsMixin

class AuditLogSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    subject_name = serializers.CharField(
        source="subject_employee.full_name", read_only=True, default=None
    )
    subject_code = serializers.CharField(
        source="subject_employee.employee_code", read_only=True, default=None
    )
    subject_department = serializers.CharField(
        source="subject_employee.department.name", read_only=True, default=None
    )
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    is_sensitive = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "occurred_at",
            "actor", "actor_email", "actor_name",
            "action", "action_label", "resource",
            "entity_type", "entity_id", "entity_label",
            "subject_employee", "subject_name", "subject_code", "subject_department",
            "before", "after", "reason",
            "ip", "request_id", "is_sensitive",
        ]

    def get_actor_name(self, obj) -> str:
        if obj.actor_id and getattr(obj.actor, "full_name", ""):
            return obj.actor.full_name
        # Falls back to the denormalised email, then to "system" — a
        # pre-authentication event (failed login, bootstrap) genuinely has no
        # actor, and showing a blank there reads as missing data.
        return obj.actor_email or "system"

    def get_is_sensitive(self, obj) -> bool:
        return obj.action in SENSITIVE_ACTIONS


class AuditLogViewSet(ScopedReadOnlyModelViewSet):
    access_resource = Resource.AUDIT_LOG
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.select_related(
        "actor", "subject_employee", "subject_employee__department"
    ).all()
    filterset_fields = ["action", "resource", "entity_type", "actor", "subject_employee"]
    search_fields = ["entity_label", "actor_email", "reason"]
    ordering_fields = ["occurred_at"]
    ordering = ["-occurred_at"]

    access_actions = {"sensitive": Action.VIEW, "options": Action.VIEW}

    def get_queryset(self):
        queryset = super().get_queryset()

        since = self.request.query_params.get("since")
        until = self.request.query_params.get("until")
        if since:
            queryset = queryset.filter(occurred_at__date__gte=since)
        if until:
            queryset = queryset.filter(occurred_at__date__lte=until)

        # "Show me only the events that matter" — one flag rather than making
        # the reviewer reconstruct the list from a multi-select each time.
        if self.request.query_params.get("sensitive_only") in ("1", "true", "True"):
            queryset = queryset.filter(action__in=SENSITIVE_ACTIONS)

        return queryset

    @action(detail=False, methods=["get"])
    def options(self, request):
        """Filter vocabulary, so the UI never hardcodes an enum the server owns."""
        visible = self.get_queryset()
        return Response(
            {
                "actions": [
                    {"value": value, "label": label, "sensitive": value in SENSITIVE_ACTIONS}
                    for value, label in AuditAction.choices
                ],
                "resources": sorted(
                    r for r in visible.values_list("resource", flat=True).distinct() if r
                ),
                "entity_types": sorted(
                    e for e in visible.values_list("entity_type", flat=True).distinct() if e
                ),
            }
        )


router = DefaultRouter()
router.register("audit", AuditLogViewSet, basename="audit-log")

audit_patterns = router.urls
