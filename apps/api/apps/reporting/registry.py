"""
The metric registry.

ONE RULE, and every metric in this system obeys it: **a metric never queries a
model directly.** It declares the access resource it reads, and receives a
queryset that `scope_queryset()` has already narrowed to what the caller may
see. That is what makes a CEO's `headcount` and a Medical Director's
`headcount` the same function returning different numbers, rather than two
functions that will eventually disagree.

There is deliberately no per-role branching anywhere in this package. If a
metric ever needs `if role == ...`, the permission matrix is the thing that
should change.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

from core.access.catalog import Action


class MetricError(Exception):
    """A metric that cannot be computed as asked."""


@dataclass(frozen=True)
class MetricPoint:
    """One labelled value. `key` is stable for the client; `label` is display."""

    key: str
    label: str
    value: float | int | str
    #: Optional secondary figure — a prior-period comparison, a denominator.
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    key: str
    label: str
    unit: str
    #: "scalar" -> one number; "series" -> ordered over time; "breakdown" -> by group.
    shape: str
    points: list[MetricPoint]
    #: The breadth the caller actually got, so the UI can say "your department"
    #: rather than implying the number is organisation-wide.
    scope_label: str
    generated_at: dt.datetime
    params: dict = field(default_factory=dict)

    @property
    def total(self) -> float:
        numeric = [p.value for p in self.points if isinstance(p.value, (int, float))]
        return sum(numeric) if numeric else 0


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    description: str
    #: The access resource this metric reads. Authorization is entirely this —
    #: holding VIEW on the resource is what makes the metric visible, and the
    #: scope held is what decides how much of it the caller gets.
    resource: str
    action: str
    unit: str
    shape: str
    #: Grouping dimensions this metric accepts, e.g. ("department", "status").
    groupings: tuple[str, ...]
    supports_range: bool
    #: Expensive enough to be worth a nightly snapshot.
    snapshotable: bool
    fn: Callable

    @property
    def family(self) -> str:
        return self.key.split(".")[0]


_REGISTRY: dict[str, MetricSpec] = {}


def metric(
    key: str,
    *,
    label: str,
    description: str,
    resource: str,
    action: str = Action.VIEW,
    unit: str = "count",
    shape: str = "scalar",
    groupings: tuple[str, ...] = (),
    supports_range: bool = False,
    snapshotable: bool = False,
):
    """Register a metric. The decorated function receives `(ctx, params)`."""

    def wrapper(fn):
        if key in _REGISTRY:
            raise MetricError(f"Metric '{key}' is already registered.")
        _REGISTRY[key] = MetricSpec(
            key=key,
            label=label,
            description=description,
            resource=str(resource),
            action=str(action),
            unit=unit,
            shape=shape,
            groupings=groupings,
            supports_range=supports_range,
            snapshotable=snapshotable,
            fn=fn,
        )
        return fn

    return wrapper


def get(key: str) -> MetricSpec:
    spec = _REGISTRY.get(key)
    if spec is None:
        raise MetricError(f"No metric named '{key}'.")
    return spec


def all_specs() -> list[MetricSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.family, s.key))


def visible_to(user) -> list[MetricSpec]:
    """
    Metrics this user may see, decided solely by the permission matrix.

    A metric absent from this list is not hidden — the compute endpoint refuses
    it too, from the same check. The UI is showing the server's answer, not
    filtering a list it was given in full.
    """
    from core.access import can

    return [spec for spec in all_specs() if can(user, spec.resource, spec.action)]
