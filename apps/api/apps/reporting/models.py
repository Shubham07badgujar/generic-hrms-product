"""
Materialised metric values.

Only for aggregates expensive enough that computing them per request would make
a dashboard slow. Everything else is computed live, because a stale number
presented as current is worse than a slow one.

A snapshot is stored at ALL scope and re-scoped on read — it is a cache of the
underlying aggregate, never a cache of one person's view of it. Caching a
scoped result would be a disclosure bug waiting for a cache-key collision.
"""

from __future__ import annotations

from django.db import models

from core.models import BaseModel


class MetricSnapshot(BaseModel):
    metric_key = models.CharField(max_length=80, db_index=True)
    #: The grouping this row belongs to, e.g. {"department": "<uuid>"}.
    #: Empty for an organisation-wide total.
    dimension = models.JSONField(default=dict, blank=True)

    period_start = models.DateField()
    period_end = models.DateField()

    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    #: Anything the metric wants to carry alongside — denominators, counts.
    context = models.JSONField(default=dict, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric_key", "-period_start"]
        indexes = [
            models.Index(fields=["metric_key", "-period_start"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["metric_key", "dimension", "period_start"],
                name="uniq_snapshot_per_metric_dimension_period",
            )
        ]

    def __str__(self) -> str:
        return f"{self.metric_key} {self.period_start} = {self.value}"
