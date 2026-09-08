"""
Phone normalization.

Phone is the only identity key many blue-collar candidates have, so these cases
are the difference between recognising a returning applicant and either
duplicating them or merging them with a stranger.

Every number here is invented. `9876543210` and friends are the standard
documentation range and belong to nobody.
"""

from __future__ import annotations

import pytest

from core.phone import to_e164_in


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+919876543210",
        "919876543210",
        "09876543210",
        "+91 98765 43210",
        "+91-98765-43210",
        "98765 43210",
        "  9876543210  ",
        "(+91) 9876543210",
    ],
)
def test_the_same_handset_written_nine_ways_normalizes_identically(raw):
    """The whole point: one person, one key, however the export wrote it."""
    assert to_e164_in(raw) == "+919876543210"


@pytest.mark.parametrize("leading", ["6", "7", "8", "9"])
def test_every_indian_mobile_series_is_accepted(leading):
    assert to_e164_in(f"{leading}876543210") == f"+91{leading}876543210"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not a phone",
        "12345",                 # too short
        "98765432101234",        # too long
        "1234567890",            # landline series — declines to guess
        "5876543210",            # not a mobile leading digit
        "9999999999",            # placeholder
        "0000000000",
        "+14155550123",          # explicitly non-Indian
    ],
)
def test_anything_we_cannot_place_confidently_returns_none(raw):
    """
    None removes phone from that row's identity keys. That is the safe
    direction — a wrong normalization silently merges two people.
    """
    assert to_e164_in(raw) is None


def test_two_different_numbers_in_one_cell_are_declined():
    """
    Agency exports do this constantly. Picking the first would attribute a
    candidate to whichever number happened to be typed first.
    """
    assert to_e164_in("9876543210 / 9123456789") is None
    assert to_e164_in("9876543210, 9123456789") is None


def test_the_same_number_written_twice_in_one_cell_is_not_ambiguous():
    assert to_e164_in("9876543210 / +919876543210") == "+919876543210"


def test_normalization_is_idempotent():
    once = to_e164_in("098765 43210")

    assert to_e164_in(once) == once
