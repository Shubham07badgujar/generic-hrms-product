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

from core.access import Action, Resource, Scope, can, get_context, require, scope_queryset
from core.access.engine import apply_org_predicate, visible_scope_label

from .registry import (
    MetricError,
    MetricPoint,
    MetricResult,
    MetricSpec,
    get,
    visible_to,
)

logger = logging.getLogger("hrms.reporting")

#: A range nobody asked for. Long enough to be useful on a first load, short
#: enough that an unbounded scan never happens by accident.
DEFAULT_RANGE_DAYS = 365
MAX_RANGE_DAYS = 366 * 3

#: How old a snapshot may be and still be served as the current number. The
#: refresh runs nightly, so anything older than this means a run was missed —
#: and at that point the live computation is slower but true, which is the
#: trade this whole module is explicit about making.
SNAPSHOT_MAX_AGE = dt.timedelta(hours=36)


@dataclass
class MetricContext:
    """What a metric may reach. Deliberately narrow."""

    user: object
    spec: MetricSpec
    #: Set ONLY by the snapshot job, which computes organisation-wide
    #: aggregates for storage. Never set from a request — `compute()` does not
    #: accept it, so there is no path from an HTTP caller to an unscoped read.
    unrestricted: bool = False
    #: The one organization an unrestricted context may read. Required with
    #: `unrestricted`: skipping `scope_queryset()` also skips the org predicate
    #: it applies first, so this is what keeps "organisation-wide" from meaning
    #: "platform-wide".
    organization: object = None

    def scoped(self, queryset, resource: str, action: str = Action.VIEW):
        """
        The ONLY data path a metric has.

        Every queryset passes through the access engine, so a metric physically
        cannot return a row its caller is not entitled to — including one whose
        author never thought about scoping.
        """
        if self.unrestricted:
            if self.organization is None:
                raise MetricError(
                    "An unrestricted metric context needs an organization; "
                    "without one it would aggregate every tenant."
                )
            # Explicit rather than left to the bound tenant. Every app filters
            # at the manager now, so this is the second of two answers rather
            # than the only one -- but an unrestricted context exists precisely
            # to skip the scoping the request path applies, and it should not
            # then depend on ambient state to stay inside one customer.
            return queryset.filter(organization_id=self.organization.pk)
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

    Answered from a stored snapshot when one exactly fits the question and the
    caller is entitled to the whole aggregate; otherwise computed live. The
    fallback is not an error path — it is the normal answer for every caller
    below ALL scope, for every window that is not the snapshotted one, and for
    every hour before the first nightly refresh.
    """
    spec = get(key)
    scope = require(user, spec.resource, spec.action)

    start, end = _resolve_range(spec, start, end)
    group_by = _resolve_grouping(spec, group_by)
    params = {"start": start, "end": end, "group_by": group_by}

    stored = _stored_points(
        spec, user, scope=scope, start=start, end=end, group_by=group_by
    )
    if stored is not None:
        points, generated_at = stored
        source = "snapshot"
    else:
        ctx = MetricContext(user=user, spec=spec)
        try:
            points = spec.fn(ctx, params)
        except MetricError:
            raise
        except Exception as exc:
            logger.exception("reporting.metric_failed key=%s", key)
            raise MetricError(f"'{key}' could not be computed.") from exc
        generated_at, source = timezone.now(), "live"

    return MetricResult(
        key=spec.key,
        label=spec.label,
        unit=spec.unit,
        shape=spec.shape,
        points=list(points),
        scope_label=visible_scope_label(user, spec.resource, spec.action),
        generated_at=generated_at,
        source=source,
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


def _stored_points(
    spec: MetricSpec, user, *, scope, start, end, group_by: str
) -> tuple[list[MetricPoint], dt.datetime] | None:
    """
    The stored answer to exactly this question, or None to compute it live.

    THE SCOPE RULE, and the reason this is not a generic cache: a snapshot is
    the organisation-wide aggregate, and no arithmetic narrows a stored total
    into one department's share of it. So it is served only to a caller holding
    ALL scope on the metric's resource. A Department Head asking the same
    question gets the live computation and their own number — the alternative
    is handing them the whole organisation's figure, which is the disclosure
    bug the model docstring warns about, arriving through the cache instead of
    through a query.

    Everything else here is about not answering a question that was not asked:

      * the window must match exactly. The job stores one window per day; a
        caller asking about a different range is asking about different rows;
      * a grouped request has no stored form — the job computes ungrouped
        totals only, so `group_by` falls straight through to live;
      * a snapshot older than `SNAPSHOT_MAX_AGE` is ignored. A missed refresh
        makes the dashboard slower, not wrong.

    Tenancy is applied explicitly, as it is on the write side. `reporting` is
    in STRICT_TENANT_APPS now, so the manager filters too -- but this predicate
    comes from the CALLER'S resolved context rather than from whatever is
    bound, and reading one organisation's aggregate into another's dashboard is
    the worst version of this bug available. Two independent answers to the
    same question is the right number here.
    """
    if not spec.snapshotable or group_by:
        return None
    if scope != Scope.ALL:
        return None

    from .models import MetricSnapshot

    rows = list(
        apply_org_predicate(
            MetricSnapshot.objects.all_orgs().active(), get_context(user)
        )
        .filter(
            metric_key=spec.key,
            period_start=start,
            period_end=end,
            computed_at__gte=timezone.now() - SNAPSHOT_MAX_AGE,
        )
        .order_by("sequence")
    )
    if not rows:
        return None

    points = [
        MetricPoint(
            key=str(row.dimension.get("point", "")),
            label=row.label or str(row.dimension.get("point", "")),
            value=_number(row.value),
            context=row.context,
        )
        for row in rows
    ]
    return points, max(row.computed_at for row in rows)


def _number(value):
    """
    A stored Decimal as the kind of number the metric produced.

    Headcount is counted in people. Reading `12.0000` back as `12.0` where the
    live path says `12` would make the same metric render two ways depending on
    the time of day.
    """
    return int(value) if value == value.to_integral_value() else float(value)


def refresh_snapshots(organization, *, as_of=None) -> int:
    """
    Materialise one organization's metrics marked snapshotable.

    Computed at ALL scope within that organization by an unrestricted internal
    principal, and stored per organization with no narrower scope applied.
    Snapshots are a cache of the AGGREGATE, never of one person's view —
    caching a scoped result would turn a cache-key collision into a disclosure
    bug.

    Run nightly by `reporting.refresh_snapshots`, which fans out one call per
    running organization. `compute()` reads these back through
    `_stored_points`, which filters by organization as explicitly as this write
    does.
    """
    from .models import MetricSnapshot
    from .registry import all_specs

    today = as_of or timezone.localdate()
    start = today - dt.timedelta(days=DEFAULT_RANGE_DAYS)
    written = 0

    for spec in all_specs():
        if not spec.snapshotable:
            continue
        ctx = MetricContext(
            user=None, spec=spec, unrestricted=True, organization=organization
        )
        try:
            points = spec.fn(ctx, {"start": start, "end": today, "group_by": ""})
        except Exception:
            logger.exception(
                "reporting.snapshot_failed key=%s organization=%s",
                spec.key, organization.pk,
            )
            continue

        # The organization is named in the lookup rather than left to the
        # manager. It does filter now that `reporting` is strict, but this
        # write must not depend on that: an upsert keyed on metric and window
        # alone would find another tenant's row and overwrite it the moment
        # anything ran it unbound.
        own = MetricSnapshot.objects.all_orgs().filter(
            organization=organization, metric_key=spec.key
        )
        current = []
        for sequence, point in enumerate(points):
            row, _ = own.update_or_create(
                organization=organization,
                metric_key=spec.key,
                dimension={"point": point.key},
                period_start=start,
                defaults={
                    "period_end": today,
                    "value": point.value if isinstance(point.value, (int, float)) else 0,
                    "context": point.context,
                    # Stored so the read path reproduces the metric's own
                    # result rather than a second version of it.
                    "label": point.label,
                    "sequence": sequence,
                },
            )
            current.append(row.pk)
            written += 1

        # Everything this run did NOT write, dropped: the rows this metric
        # leaves behind are exactly the ones it just produced.
        #
        # Deleting by window alone (`period_start` older than today's) was not
        # enough. `period_start` moves with the date, so a second run on the
        # SAME day writes the same window and leaves any point that has since
        # disappeared sitting beside the fresh ones — a reversed payroll run
        # would keep being served for its month, from a row nothing updates.
        # Re-runs are ordinary: a retried subtask, or an operator refreshing
        # after a correction.
        #
        # A hard delete, because a superseded cache row has no history worth
        # keeping and a soft delete would leave the growth exactly where it was.
        # Reached only after the metric computed successfully -- the `continue`
        # above means a failed metric keeps yesterday's rows rather than losing
        # them to an outage.
        own.exclude(pk__in=current).hard_delete()

    return written
