"""
Notification API.

Every read filters on `recipient=request.user` through `services.visible_to()`,
never through the employee-path scoper. That is not a shortcut around RBAC —
it is the correct authority for this resource. A notification belongs to a
User, and Admin and CEO hold no Employee record, so employee-based scoping
would hide every notification from exactly the two principals who most need
them.

The polling contract is `GET /notifications/unread-count/`, which is one
indexed COUNT and nothing else, so the SPA can call it every 30 seconds
without thinking about cost.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.routers import DefaultRouter

from core.access import Action, Resource
from core.access.drf import ScopedQuerysetMixin

from . import services
from .models import Notification, NotificationKind, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id", "kind", "priority", "title", "body", "link_url",
            "entity_type", "entity_id", "is_read", "read_at", "created_at",
        ]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = NotificationPreference
        fields = ["id", "kind", "label", "in_app", "email"]

    def get_label(self, obj) -> str:
        return NotificationKind(obj.kind).label


class NotificationViewSet(
    ScopedQuerysetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet
):
    """
    The only viewset that had no scoping mixin at all.

    Its `get_queryset` filters to the recipient, and a recipient belongs to one
    organization, so the rows were never actually cross-tenant. But that safety
    is a consequence of another model's constraint rather than anything stated
    here, and it would evaporate quietly the day a user can belong to two
    organizations. The mixin applies the tenant predicate in `filter_queryset`,
    which says it directly.
    """

    access_resource = Resource.NOTIFICATION
    serializer_class = NotificationSerializer
    parser_classes = [JSONParser]
    filterset_fields = ["kind", "is_read", "priority"]

    def get_queryset(self):
        return services.visible_to(self.request.user).order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        """
        Mark one notification read.

        `get_queryset` has already restricted this to the caller's own rows, and
        the service re-checks ownership, so one person can never mark another's.

        A read-only principal (the CEO) is refused here, by the engine's
        write-action clamp and again by ReadOnlyPrincipalMiddleware. Their list
        is therefore read-only. That is a genuine limitation, kept deliberately:
        exempting this route would put the first hole in a control that is
        currently absolute.
        """
        notification = self.get_object()
        services.mark_read(notification, user=request.user)
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        return Response({"marked": services.mark_all_read(request.user)})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """The polling endpoint. One indexed COUNT, called every 30 seconds."""
        return Response({"unread": services.unread_count(request.user)})


class NotificationPreferenceView(APIView):
    """
    Per-kind delivery preferences for the caller.

    GET returns every kind, including ones never configured, so the UI can show
    a complete list without knowing the enum. Absence means on.
    """

    access_resource = Resource.NOTIFICATION
    #: POST here EDITS your own settings; it does not create a notification.
    #: Left to the default method map it resolves to CREATE, which self-service
    #: does not hold — so every user got a 403 saving their own preferences.
    access_actions = {"POST": Action.EDIT}

    def get(self, request):
        stored = {
            preference.kind: preference
            for preference in NotificationPreference.objects.filter(user=request.user)
        }
        rows = [
            {
                "kind": kind,
                "label": label,
                "in_app": stored[kind].in_app if kind in stored else True,
                "email": stored[kind].email if kind in stored else False,
                "configured": kind in stored,
            }
            for kind, label in NotificationKind.choices
        ]
        return Response({"preferences": rows})

    def post(self, request):
        kind = request.data.get("kind", "")
        try:
            preference = services.set_preference(
                user=request.user,
                kind=kind,
                in_app=bool(request.data.get("in_app", True)),
                email=bool(request.data.get("email", False)),
            )
        except ValueError as exc:
            return Response(
                {"error": {"code": "invalid", "message": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(NotificationPreferenceSerializer(preference).data)


router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")

notification_patterns = router.urls
