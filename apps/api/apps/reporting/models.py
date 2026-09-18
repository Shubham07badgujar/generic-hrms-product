"""
Materialised metric values.

Only for aggregates expensive enough that computing them per request would make
a dashboard slow. Everything else is computed live, because a stale number
presented as current is worse than a slow one.

A snapshot is stored at ALL scope within one organization — a cache of the
underlying aggregate, never a cache of one person's view of it. Caching a
scoped result would be a disclosure bug waiting for a cache-key collision.

Which means it cannot be narrowed on read: no arithmetic turns a stored
organisation-wide total into one department's share of it. So `compute()`
serves a snapshot ONLY to a caller who holds ALL scope on the metric's
resource, and everyone else gets the live computation. That rule lives in
`services._stored_points`.
"""

from __future__ import annotations

from django.db import models

from core.models import OrgOwnedModel


class MetricSnapshot(OrgOwnedModel):
    metric_key = models.CharField(max_length=80, db_index=True)
    #: The grouping this row belongs to, e.g. {"department": "<uuid>"}.
    #: Empty for an organisation-wide total.
    dimension = models.JSONField(default=dict, blank=True)

    period_start = models.DateField()
    period_end = models.DateField()

    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    #: Anything the metric wants to carry alongside — denominators, counts.
    context = models.JSONField(default=dict, blank=True)

    #: The point's display label, stored rather than re-derived: "Mar 2025" is
    #: the metric's to format, and a read path that rebuilt it would be a
    #: second implementation free to disagree with the first.
    label = models.CharField(max_length=120, blank=True)
    #: Position within the metric's result. A series is ordered, and row order
    #: is not: without this, "headcount by month" comes back in whatever order
    #: the rows happen to sort in.
    sequence = models.PositiveIntegerField(default=0)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric_key", "-period_start"]
        indexes = [
            models.Index(fields=["metric_key", "-period_start"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "metric_key", "dimension", "period_start"],
                name="uniq_snapshot_per_metric_dimension_period",
            )
        ]

    def __str__(self) -> str:
        return f"{self.metric_key} {self.period_start} = {self.value}"
