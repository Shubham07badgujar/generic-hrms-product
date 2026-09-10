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
"""

from __future__ import annotations

from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.organization.models import OPERATIONAL_STATUSES, Organization
from core.access.drf import PlatformAPIView, PlatformReadOnlyModelViewSet


class PlatformOrganizationSerializer(serializers.ModelSerializer):
    employee_count = serializers.IntegerField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    is_operational = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            "id", "name", "legal_name", "slug", "status", "is_operational",
            "primary_email", "phone", "city", "state", "country",
            "timezone", "currency",
            "employee_count", "member_count",
            "created_at",
        ]

    def get_is_operational(self, obj) -> bool:
        return obj.status in OPERATIONAL_STATUSES


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
    queryset = Organization.objects.all()
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
            .annotate(
                employee_count=Coalesce(Subquery(headcount, output_field=IntegerField()), 0),
                member_count=Count("memberships", distinct=True),
            )
        )


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
