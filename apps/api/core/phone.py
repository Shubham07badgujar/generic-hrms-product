"""
Phone normalization for candidate identity.

WHY NOT A LIBRARY
-----------------
`phonenumbers` is the obvious answer and is deliberately not used here. It is a
large dependency carrying a metadata database that has to be kept current, and
the only question this codebase asks of a phone number is "are these two the
same person's number". That is one country, one format family, and about thirty
lines — the dependency would cost more to keep honest than it saves.

WHY NORMALIZATION IS NOT OPTIONAL
---------------------------------
A WorkIndia export writes the same handset as `+91-98765 43210`, `098765 43210`
and `9876543210` in three different rows. Stored raw, those are three
candidates. Phone is the ONLY identity key many blue-collar candidates have, so
getting this wrong means either duplicating people or merging strangers.

WHY `None` IS A RESULT, NOT A FAILURE
-------------------------------------
Anything we cannot confidently place is returned as `None`, which simply removes
phone from that row's identity keys. Guessing would be worse than not knowing:
a wrong normalization silently merges two people, and a merged candidate record
is very hard to unpick once applications hang off it.
"""

from __future__ import annotations

import re

#: India country calling code, and the length of a subscriber number.
_IN_CC = "91"
_NSN_LENGTH = 10

#: Indian mobile numbers begin 6, 7, 8 or 9. Landlines do not, and a landline in
#: a candidate export is nearly always a typo or an agency switchboard — neither
#: identifies a person, so both are declined.
_MOBILE_LEADING = frozenset("6789")

_NON_DIGITS = re.compile(r"[^\d+]")
#: Several numbers in one cell: "9876543210 / 9123456789", "98765, 91234".
_SEPARATORS = re.compile(r"[\/,;|]| or ", re.IGNORECASE)


def to_e164_in(raw: str | None) -> str | None:
    """
    Normalize an Indian mobile number to E.164 (`+919876543210`).

    Returns None when the value cannot be placed confidently — empty, too short,
    too long, a landline, an obvious placeholder, or several numbers in one
    cell where picking one would be a guess.
    """
    if not raw:
        return None

    # A cell holding two numbers identifies nobody in particular. Take the first
    # only if the rest are junk; otherwise decline.
    candidates = [part for part in _SEPARATORS.split(str(raw)) if part.strip()]
    if len(candidates) > 1:
        parsed = [_parse_single(part) for part in candidates]
        found = {value for value in parsed if value}
        # Several distinct real numbers: no basis for choosing. One number
        # written twice is not ambiguous.
        return found.pop() if len(found) == 1 else None

    return _parse_single(str(raw))


def _parse_single(raw: str) -> str | None:
    digits = _NON_DIGITS.sub("", raw)

    # A leading + is only meaningful before the country code.
    plus = digits.startswith("+")
    digits = digits.replace("+", "")
    if not digits:
        return None

    if plus and not digits.startswith(_IN_CC):
        # An explicitly international number outside India. We have no rules for
        # it, so we decline rather than mangle it into an Indian one.
        return None

    # Strip the country code, then the trunk prefix, in that order:
    # +919876543210 → 919876543210 → 9876543210
    #  09876543210  →  09876543210 → 9876543210
    if digits.startswith(_IN_CC) and len(digits) == len(_IN_CC) + _NSN_LENGTH:
        digits = digits[len(_IN_CC):]
    elif digits.startswith("0") and len(digits) == _NSN_LENGTH + 1:
        digits = digits[1:]

    if len(digits) != _NSN_LENGTH:
        return None
    if digits[0] not in _MOBILE_LEADING:
        return None
    # Placeholders: 9999999999, 1234567890 and friends appear constantly in
    # platform exports and identify nobody.
    if len(set(digits)) == 1:
        return None

    return f"+{_IN_CC}{digits}"
