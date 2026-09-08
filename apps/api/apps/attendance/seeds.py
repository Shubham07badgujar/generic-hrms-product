"""
Default attendance configuration — the company's own timing chart, as data.

A STARTER CHART. Edit it in the app (Attendance -> shift rules); these are
defaults for a new install, not the product's opinion:
  Every location .............. 10:00-19:00 (full day 9h)
  "Head Office", if present ... 10:00-18:00 (full day 8h)
  Grace 15 min everywhere (late after 10:15); three COUNTED late arrivals
  tolerated per month, the fourth becomes a Half Day. A late that still
  completes the full day's hours is compensated and never counted.

Idempotent AND non-destructive: a rule that already exists is left exactly as
HR configured it — re-seeding must never revert a policy edit. Matched by
LOCATION NAME so it survives locations being added later; a location without
its own rule uses the org default row.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.db import transaction

from .models import ShiftRule

DEFAULT = {
    "start_time": dt.time(10, 0),
    "end_time": dt.time(19, 0),
    "grace_minutes": 15,
    "allowed_late_per_month": 3,
}

#: Locations whose hours differ from the default. Everything else inherits.
OVERRIDES = {
    "Head Office": {"end_time": dt.time(18, 0)},
}


def _with_full_day(values: dict) -> dict:
    """Full-day hours default to the shift's own span (breaks are inside)."""
    span = (
        values["end_time"].hour * 60 + values["end_time"].minute
        - values["start_time"].hour * 60 - values["start_time"].minute
    )
    return {**values, "full_day_hours": Decimal(span) / Decimal(60)}


@transaction.atomic
def seed_shift_rules() -> int:
    from apps.organization.models import Location

    count = 0
    # get_or_create, never update: HR's edits to an existing rule outlive
    # every re-seed. Only genuinely missing rules are written.
    ShiftRule.objects.get_or_create(location=None, defaults=_with_full_day(DEFAULT))
    count += 1

    for location in Location.objects.active():
        values = dict(DEFAULT)
        for name, override in OVERRIDES.items():
            if name.lower() in location.name.lower() or (
                name == "Head Office" and location.is_head_office
            ):
                values.update(override)
        ShiftRule.objects.get_or_create(
            location=location, defaults=_with_full_day(values)
        )
        count += 1
    return count
