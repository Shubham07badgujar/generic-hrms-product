"""
Candidate identity, step 2 of 4: BACKFILL.

Populates the columns 0006 created, so that 0009 can put constraints on them.

THE NORMALIZER IS COPIED HERE ON PURPOSE
----------------------------------------
`core.phone.to_e164_in` is imported by nothing in this file. A migration must
reproduce forever what it did on the day it ran; importing live code means that
improving the normalizer silently changes the meaning of a two-year-old
migration, and a replay against a restored backup would produce different data
from the original run.

The duplication is real and is the price. Deliberate re-normalization after a
rule change is a management command, not an edit to this file.

Reverse is a no-op rather than an error: 0006's columns simply go away again,
and there is nothing in these values that did not come from data still present.
"""

import re

from django.db import migrations

# ---------------------------------------------------------------------------
# FROZEN COPY of core.phone.to_e164_in as of migration 0007. Do not edit to
# match a later version of that module — see the module docstring.
# ---------------------------------------------------------------------------
_IN_CC = "91"
_NSN_LENGTH = 10
_MOBILE_LEADING = frozenset("6789")
_NON_DIGITS = re.compile(r"[^\d+]")
_SEPARATORS = re.compile(r"[\/,;|]| or ", re.IGNORECASE)


def _parse_single(raw):
    digits = _NON_DIGITS.sub("", raw)
    plus = digits.startswith("+")
    digits = digits.replace("+", "")
    if not digits:
        return None
    if plus and not digits.startswith(_IN_CC):
        return None
    if digits.startswith(_IN_CC) and len(digits) == len(_IN_CC) + _NSN_LENGTH:
        digits = digits[len(_IN_CC):]
    elif digits.startswith("0") and len(digits) == _NSN_LENGTH + 1:
        digits = digits[1:]
    if len(digits) != _NSN_LENGTH:
        return None
    if digits[0] not in _MOBILE_LEADING:
        return None
    if len(set(digits)) == 1:
        return None
    return f"+{_IN_CC}{digits}"


def _to_e164_in(raw):
    if not raw:
        return None
    candidates = [part for part in _SEPARATORS.split(str(raw)) if part.strip()]
    if len(candidates) > 1:
        found = {value for value in (_parse_single(p) for p in candidates) if value}
        return found.pop() if len(found) == 1 else None
    return _parse_single(str(raw))


def backfill(apps, schema_editor):
    Candidate = apps.get_model("recruitment", "Candidate")

    # Empty string first. Postgres unique indexes treat NULLs as distinct but
    # '' as an ordinary value, so leaving these behind would make the second
    # email-less candidate collide with the first the moment 0009 lands.
    Candidate.objects.filter(email="").update(email=None)

    # Chunked, and touching only the derived columns. `.iterator()` keeps the
    # working set flat on a table of any size.
    batch = []
    for candidate in Candidate.objects.all().only("id", "email", "phone").iterator(
        chunk_size=1000
    ):
        candidate.email_normalized = (candidate.email or "").strip().lower() or None
        candidate.phone_e164 = _to_e164_in(candidate.phone)
        batch.append(candidate)
        if len(batch) >= 1000:
            Candidate.objects.bulk_update(batch, ["email_normalized", "phone_e164"])
            batch = []
    if batch:
        Candidate.objects.bulk_update(batch, ["email_normalized", "phone_e164"])

    # Every existing row needs a lawful basis, because 0009 makes that a check
    # constraint. Rows that recorded consent get CONSENT; the rest get
    # LEGACY_UNRECORDED, which is not a claim that a basis existed — it is an
    # honest statement that none was recorded, and it is queryable, which "" is
    # not.
    Candidate.objects.filter(consent_given=True, legal_basis="").update(
        legal_basis="consent"
    )
    Candidate.objects.filter(consent_given=False, legal_basis="").update(
        legal_basis="legacy_unrecorded"
    )


class Migration(migrations.Migration):

    dependencies = [("recruitment", "0006_candidate_identity_columns")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
