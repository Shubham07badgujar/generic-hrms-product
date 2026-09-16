"""
The platform console's read surface.

What a SaaS operator legitimately needs to see: which organizations exist, what
state each is in, and how large each one is. Nothing here reads an employee, a
payslip, a candidate or a document, and that is not a matter of restraint --
the platform principal holds no resource grant anywhere, so every one of those
querysets resolves to nothing for them, and `RBACPermission` refuses the routes
outright.

The employee COUNT is here and the employees are not. A count is commercial
metadata: it is what a plan's seat limit is measured against, and an operator
who cannot see it cannot answer "are they about to exceed their plan". Knowing
that a customer employs 118 people tells you nothing about any of them.

THE WRITE SURFACE IS THIN ON PURPOSE

Three actions -- change the plan, move the commercial status, override the seat
limit -- and each one is a thin wrapper over a service that already holds the
rules, the locking and the audit. The view's whole job is to translate a
service refusal into 422 and to require the reason that some of them need.
Putting the logic here instead would have meant the platform API and a
management command could disagree about whether a downgrade below the headcount
is allowed.
"""

from __future__ import annotations

from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.organization.models import OPERATIONAL_STATUSES, Organization
from apps.platform.models import Plan, Subscription
from apps.platform.services import subscriptions as subscription_services
from core.access.drf import PlatformAPIView, PlatformReadOnlyModelViewSet
from core.api.exceptions import BusinessRuleError
from core.querysets import deferred


class SubscriptionSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source="plan.code", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    employee_limit = serializers.IntegerField(read_only=True)
    enabled_features = serializers.ListField(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id", "plan_code", "plan_name", "status",
            "employee_limit", "employee_limit_override", "override_reason",
            "enabled_features",
            "started_at", "ends_at", "cancelled_at",
            "read_only_grace_days", "features_narrowed_at",
        ]


class PlatformOrganizationSerializer(serializers.ModelSerializer):
    employee_count = serializers.IntegerField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    is_operational = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id", "name", "legal_name", "slug", "status", "is_operational",
            "primary_email", "phone", "city", "state", "country",
            "timezone", "currency",
            "employee_count", "member_count", "subscription",
            "created_at",
        ]

    def get_is_operational(self, obj) -> bool:
        return obj.status in OPERATIONAL_STATUSES

    def get_subscription(self, obj):
        # None on a deployment that sells nothing, which is a real state and
        # not an error -- a self-hosted single-company install has no plans.
        subscription = getattr(obj, "subscription", None)
        if subscription is None or not subscription.is_active:
            return None
        return SubscriptionSerializer(subscription).data


class PlatformOrganizationViewSet(PlatformReadOnlyModelViewSet):
    """
    Every organization on the deployment, which is the one queryset in the
    product that is SUPPOSED to span tenants.

    It is safe to say that here and nowhere else because `Organization` is
    declared TENANT_EXEMPT -- it IS the tenant, so scoping it by itself is
    circular -- and because reaching this view at all requires the platform
    flag.
    """

    serializer_class = PlatformOrganizationSerializer
    queryset = deferred(Organization)
    filterset_fields = ["status"]
    search_fields = ["name", "legal_name", "slug", "primary_email"]
    ordering_fields = ["name", "created_at", "status"]
    ordering = ["name"]

    def get_queryset(self):
        from apps.employees.models import Employee

        # A subquery rather than a reverse join, because org-owned models
        # declare `related_name="+"`: with ninety of them pointing here,
        # reverse accessors would make `Organization` unreadable and would
        # tempt exactly the cross-tenant traversal the rest of the design
        # spends its time preventing.
        #
        # `all_orgs()` is the sanctioned escape from the tenant filter, and
        # this is the one place in the product entitled to use it on a read:
        # counting a customer's seats is what a seat limit is measured
        # against, and the platform principal has no organization of its own
        # to be filtered to.
        headcount = (
            Employee.objects.all_orgs()
            .filter(organization_id=OuterRef("pk"), is_active=True)
            .values("organization_id")
            .annotate(n=Count("id"))
            .values("n")[:1]
        )
        return (
            super()
            .get_queryset()
            .select_related("subscription", "subscription__plan")
            .annotate(
                employee_count=Coalesce(Subquery(headcount, output_field=IntegerField()), 0),
                member_count=Count("memberships", distinct=True),
            )
        )

    # -- the write surface ------------------------------------------------
    #
    # Each action is a thin wrapper: validate the shape, call the service,
    # translate its refusal. The rules, the row lock and the audit row all
    # live in the service, so the API and a management command cannot come to
    # different conclusions about the same change.

    def _refuse(self, exc):
        """A service refusal is 422: well-formed, permitted, and not allowed."""
        raise BusinessRuleError(str(exc)) from exc

    @action(detail=True, methods=["post"], url_path="change-plan")
    def change_plan(self, request, pk=None):
        organization = self.get_object()
        payload = ChangePlanSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        plan = Plan.objects.filter(
            code=payload.validated_data["plan"], is_active=True
        ).first()
        if plan is None:
            raise BusinessRuleError(
                f"No plan with code {payload.validated_data['plan']!r}."
            )
        try:
            subscription_services.change_plan(
                organization,
                plan=plan,
                actor=request.user,
                reason=payload.validated_data.get("reason", ""),
                force=payload.validated_data.get("force", False),
            )
        except subscription_services.SubscriptionError as exc:
            self._refuse(exc)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="subscription-status")
    def subscription_status(self, request, pk=None):
        organization = self.get_object()
        payload = SubscriptionStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            subscription_services.set_status(
                organization,
                status=payload.validated_data["status"],
                actor=request.user,
                reason=payload.validated_data.get("reason", ""),
            )
        except subscription_services.SubscriptionError as exc:
            self._refuse(exc)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="seat-override")
    def seat_override(self, request, pk=None):
        """
        Give one customer a seat count their plan does not carry.

        The reason is REQUIRED by the serializer and by a database constraint,
        because "why does this customer have 400 seats on a 50-seat plan" has
        to be answerable a year later, whatever wrote the row.
        """
        organization = self.get_object()
        payload = SeatOverrideSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        subscription = Subscription.objects.filter(
            organization=organization, is_active=True
        ).first()
        if subscription is None:
            raise BusinessRuleError(f"{organization.slug} has no subscription.")

        limit = payload.validated_data.get("employee_limit")
        reason = payload.validated_data["reason"]
        before = subscription.employee_limit_override

        subscription.employee_limit_override = limit
        # Clearing the override clears its reason with it: a reason left
        # behind describes a decision that no longer applies, which is worse
        # than none at all.
        subscription.override_reason = reason if limit is not None else ""
        subscription.save(
            update_fields=["employee_limit_override", "override_reason", "updated_at"]
        )

        from apps.audit.events import record_event
        from core.middleware import acting_as

        with acting_as(request.user, organization=organization):
            record_event(
                subscription,
                actor=request.user,
                entity_type="platform.Subscription",
                verb="update",
                resource="",
                before={"employee_limit_override": before},
                after={"employee_limit_override": limit},
                reason=reason,
            )
        return Response(self.get_serializer(self.get_object()).data)


class PlatformPlanSerializer(serializers.ModelSerializer):
    enabled_features = serializers.ListField(read_only=True)
    subscriber_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Plan
        fields = [
            "id", "code", "name", "description",
            "employee_limit", "disabled_features", "enabled_features",
            "storage_limit_mb", "support_level", "is_public", "display_order",
            "subscriber_count",
        ]


class PlatformPlanViewSet(PlatformReadOnlyModelViewSet):
    """
    What this deployment sells.

    Read-only over the API. Plans are created by `manage.py seed_plans` or by
    hand, and a plan is referenced by live subscriptions -- an endpoint that
    could delete one would take customers with it, and an endpoint that could
    silently retune `employee_limit` would change what every subscriber is
    entitled to without a single audit row naming them.
    """

    serializer_class = PlatformPlanSerializer
    queryset = deferred(Plan)
    ordering = ["display_order", "name"]

    def get_queryset(self):
        # A subquery, not a reverse join: `Subscription.plan` declares
        # `related_name="+"` like every other FK in this codebase, so there is
        # no accessor to count through. Same shape as the headcount above, and
        # for the same reason -- the convention is repo-wide rather than a
        # tenancy precaution here, since Plan is platform-owned.
        subscribers = (
            Subscription.objects.filter(plan_id=OuterRef("pk"), is_active=True)
            .values("plan_id")
            .annotate(n=Count("id"))
            .values("n")[:1]
        )
        return super().get_queryset().annotate(
            subscriber_count=Coalesce(
                Subquery(subscribers, output_field=IntegerField()), 0
            )
        )


class ChangePlanSerializer(serializers.Serializer):
    plan = serializers.SlugField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    #: Proceed even though the new plan's seat limit is below the current
    #: headcount. Requires a reason, and the service enforces that -- a
    #: platform admin sometimes has an agreement the system does not know
    #: about, and "why does this customer have 400 seats on a 50-seat plan"
    #: has to be answerable a year later.
    force = serializers.BooleanField(required=False, default=False)


class SubscriptionStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class SeatOverrideSerializer(serializers.Serializer):
    #: NULL defers to the plan again.
    employee_limit = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )
    reason = serializers.CharField(max_length=255)


class PlatformSummaryView(PlatformAPIView):
    """One line per status, for the console's header."""

    def get(self, request):
        counts = dict(
            Organization.objects.values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
        return Response(
            {
                "organizations_total": sum(counts.values()),
                "organizations_operational": sum(
                    n for status, n in counts.items()
                    if status in OPERATIONAL_STATUSES
                ),
                "by_status": counts,
            }
        )


router = DefaultRouter()
router.register("organizations", PlatformOrganizationViewSet, basename="platform-org")
router.register("plans", PlatformPlanViewSet, basename="platform-plan")
