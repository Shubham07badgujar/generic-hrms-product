"""
BI API.

Two endpoints. `GET /bi/metrics/` lists what the caller may see, and
`GET /bi/{key}/` computes one. Both answer from the same permission check, so
a metric that appears in the list is one the compute endpoint will serve, and a
metric that does not is refused rather than merely hidden.

There is no authorization logic here. `compute()` calls `require()` on the
metric's declared resource and every queryset inside goes through
`scope_queryset()` — this module only translates HTTP into that call.
"""

from __future__ import annotations

import datetime as dt

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.access import Resource
from core.access.engine import AccessDenied

from .registry import MetricError
from .services import available, compute


class MetricSpecSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()
    family = serializers.CharField()
    unit = serializers.CharField()
    shape = serializers.CharField()
    groupings = serializers.ListField(child=serializers.CharField())
    supports_range = serializers.BooleanField()


class MetricCatalogView(APIView):
    """
    What this caller may see.

    Guarded by REPORT/VIEW so the catalogue itself is a permission, then
    filtered per-metric — holding REPORT does not imply holding EMPLOYEE, and a
    recruiter's catalogue correctly excludes payroll cost.
    """

    access_resource = Resource.REPORT

    def get(self, request):
        specs = available(request.user)
        return Response(
            {
                "metrics": MetricSpecSerializer(specs, many=True).data,
                "families": sorted({spec.family for spec in specs}),
            }
        )


class MetricView(APIView):
    """Compute one metric."""

    access_resource = Resource.REPORT

    def get(self, request, key: str):
        try:
            result = compute(
                key,
                user=request.user,
                start=_date(request.query_params.get("start")),
                end=_date(request.query_params.get("end")),
                group_by=request.query_params.get("group_by", ""),
            )
        except AccessDenied:
            # Deliberately the same shape as an unknown metric. Confirming that
            # `finance.payroll_cost` exists but is forbidden tells a caller what
            # this organisation measures, which is not theirs to learn.
            return Response(
                {"error": {"code": "not_found", "message": f"No metric named '{key}'."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        except MetricError as exc:
            return Response(
                {"error": {"code": "invalid", "message": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "key": result.key,
                "label": result.label,
                "unit": result.unit,
                "shape": result.shape,
                "scope_label": result.scope_label,
                "generated_at": result.generated_at.isoformat(),
                "source": result.source,
                "params": result.params,
                "points": [
                    {
                        "key": point.key,
                        "label": point.label,
                        "value": point.value,
                        "context": point.context,
                    }
                    for point in result.points
                ],
            }
        )


def _date(value: str | None):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MetricError(f"'{value}' is not a date (expected YYYY-MM-DD).") from exc
