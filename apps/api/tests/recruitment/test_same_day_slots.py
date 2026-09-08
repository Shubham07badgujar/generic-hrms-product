"""
Same-day interview slots.

The rule under test: TODAY is a legitimate day to offer and book. Only a
window that has already started is refused — and the refusal must say so in
those words, because "must be in the future" read as "today is not allowed"
and got reported as a bug.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.recruitment.services import slots

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _at(year=2026, month=8, day=26, hour=9, minute=0):
    """A Wednesday morning, unless told otherwise."""
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST)


# ------------------------------------------------------------ clean_options


def test_a_same_day_future_window_is_accepted():
    start = timezone.now() + dt.timedelta(minutes=30)
    cleaned = slots.clean_options(
        [{"start": start.isoformat(), "end": (start + dt.timedelta(hours=1)).isoformat()}]
    )
    assert len(cleaned) == 1


def test_an_already_started_window_is_refused_by_name():
    start = timezone.now() - dt.timedelta(minutes=5)
    with pytest.raises(ValidationError) as refusal:
        slots.clean_options(
            [{"start": start.isoformat(), "end": (start + dt.timedelta(hours=1)).isoformat()}]
        )
    message = str(refusal.value)
    assert "already passed" in message
    assert "Same-day slots are fine" in message


# ----------------------------------------------------- default_slot_options


def test_the_default_windows_include_the_rest_of_today():
    """At 09:00, both of today's standard windows are still ahead."""
    options = slots.default_slot_options(now=_at(hour=9))
    starts = [o["start"] for o in options]
    assert "2026-08-26T10:00:00+05:30" in starts
    assert "2026-08-26T14:00:00+05:30" in starts


def test_a_window_already_begun_today_is_not_offered():
    """At 12:30, the morning window is gone but the afternoon remains."""
    options = slots.default_slot_options(now=_at(hour=12, minute=30))
    starts = [o["start"] for o in options]
    assert "2026-08-26T10:00:00+05:30" not in starts
    assert "2026-08-26T14:00:00+05:30" in starts


def test_late_in_the_day_the_defaults_start_tomorrow():
    options = slots.default_slot_options(now=_at(hour=17))
    starts = [o["start"] for o in options]
    assert not any(s.startswith("2026-08-26") for s in starts)
    # The following business days are still all offered.
    assert any(s.startswith("2026-08-27") for s in starts)
    assert len(options) == slots.DEFAULT_DAYS * len(slots.DEFAULT_WINDOWS)


def test_a_weekend_offers_no_same_day_window():
    saturday = _at(day=29, hour=9)
    options = slots.default_slot_options(now=saturday)
    assert not any(o["start"].startswith("2026-08-29") for o in options)
