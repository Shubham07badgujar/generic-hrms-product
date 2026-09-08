"""
Computing metrics.

`MetricContext` is the only thing a metric is given, and it exposes exactly one
way to reach data: `ctx.scoped(queryset, resource)`, which runs
`scope_queryset()` for the caller. There is no unscoped path a metric could
reach for, which is what makes "every metric is scope-aware" a property of the
architecture rather than a rule people have to remember.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.utils import timezone

from core.access import Action, Resource, can, require, scope_queryset
from core.access.engine import visible_scope_label

from .registry import MetricError, MetricResult, MetricSpec, get, visible_to

logger = logging.getLogger("hrms.reporting")

#: A range nobody asked for. Long enough to be useful on a first load, short
#: enough that an unbounded scan never happens by accident.
DEFAULT_RANGE_DAYS = 365
MAX_RANGE_DAYS = 366 * 3


@dataclass
class MetricContext:
    """What a metric may reach. Deliberately narrow."""

    user: object
    spec: MetricSpec
    #: Set ONLY by the snapshot job, which computes organisation-wide
    #: aggregates for storage. Never set from a request — `compute()` does not
    #: accept it, so there is no path from an HTTP caller to an unscoped read.
    unrestricted: bool = False

    def scoped(self, queryset, resource: str, action: str = Action.VIEW):
        """
        The ONLY data path a metric has.

        Every queryset passes through the access engine, so a metric physically
        cannot return a row its caller is not entitled to — including one whose
        author never thought about scoping.
        """
        if self.unrestricted:
            return queryset
        return scope_queryset(queryset, self.user, resource=resource, action=action)

    def employees(self):
        from apps.employees.models import Employee

        return self.scoped(Employee.objects.filter(is_active=True), Resource.EMPLOYEE)


def compute(key: str, *, user, start=None, end=None, group_by: str = "") -> MetricResult:
    """
    Compute one metric for one caller.

    Authorization happens here and only here — `require()` on the metric's
    declared resource. A metric function is never reached by a caller who may
    not see it, so no metric needs a permission check of its own.
    """
    spec = get(key)
    require(user, spec.resource, spec.action)

    start, end = _resolve_range(spec, start, end)
    group_by = _resolve_grouping(spec, group_by)

    ctx = MetricContext(user=user, spec=spec)
    params = {"start": start, "end": end, "group_by": group_by}

    try:
        points = spec.fn(ctx, params)
    except MetricError:
        raise
    except Exception as exc:
        logger.exception("reporting.metric_failed key=%s", key)
        raise MetricError(f"'{key}' could not be computed.") from exc

    return MetricResult(
        key=spec.key,
        label=spec.label,
        unit=spec.unit,
        shape=spec.shape,
        points=list(points),
        scope_label=visible_scope_label(user, spec.resource, spec.action),
        generated_at=timezone.now(),
        params={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "group_by": group_by,
        },
    )


def compute_many(keys: list[str], *, user, **kwargs) -> dict[str, MetricResult | None]:
    """
    Several metrics in one call — what a dashboard actually needs.

    A metric the caller cannot see, or that fails, comes back as None rather
    than failing the whole dashboard. One broken tile should not blank the page.
    """
    out: dict[str, MetricResult | None] = {}
    for key in keys:
        try:
            out[key] = compute(key, user=user, **kwargs)
        except Exception:
            logger.info("reporting.metric_skipped key=%s", key)
            out[key] = None
    return out


def available(user) -> list[MetricSpec]:
    return visible_to(user)


def _resolve_range(spec: MetricSpec, start, end) -> tuple[dt.date, dt.date]:
    today = timezone.localdate()
    end = end or today
    start = start or (end - dt.timedelta(days=DEFAULT_RANGE_DAYS))

    if start > end:
        raise MetricError("The start of the range must not be after its end.")
    if (end - start).days > MAX_RANGE_DAYS:
        # Not a policy limit — a protection. An unbounded range on a trend
        # metric is a table scan per month, and a dashboard that times out is
        # indistinguishable from one that is broken.
        raise MetricError(
            f"A range longer than {MAX_RANGE_DAYS} days is too large to compute."
        )
    return start, end


def _resolve_grouping(spec: MetricSpec, group_by: str) -> str:
    if not group_by:
        return ""
    if group_by not in spec.groupings:
        raise MetricError(
            f"'{spec.key}' cannot be grouped by '{group_by}'. "
            f"Available: {', '.join(spec.groupings) or 'none'}."
        )
    return group_by


# ---------------------------------------------------------------- snapshots


def refresh_snapshots(*, as_of=None) -> int:
    """
    Materialise the metrics marked snapshotable.

    Computed at ALL scope by an unrestricted internal principal and stored
    unscoped. Snapshots are a cache of the AGGREGATE, never of one person's
    view — caching a scoped result would turn a cache-key collision into a
    disclosure bug.

    Nothing reads these yet: they exist so a trend metric can be served from
    storage once the dataset makes live computation too slow. Wiring the read
    path is a change to `compute()` alone.
    """
    from .models import MetricSnapshot
    from .registry import all_specs

    today = as_of or timezone.localdate()
    start = today - dt.timedelta(days=DEFAULT_RANGE_DAYS)
    written = 0

    for spec in all_specs():
        if not spec.snapshotable:
            continue
        ctx = MetricContext(user=None, spec=spec, unrestricted=True)
        try:
            points = spec.fn(ctx, {"start": start, "end": today, "group_by": ""})
        except Exception:
            logger.exception("reporting.snapshot_failed key=%s", spec.key)
            continue

        for point in points:
            MetricSnapshot.objects.update_or_create(
                metric_key=spec.key,
                dimension={"point": point.key},
                period_start=start,
                defaults={
                    "period_end": today,
                    "value": point.value if isinstance(point.value, (int, float)) else 0,
                    "context": point.context,
                },
            )
            written += 1

    return written
