"""
Staging PII retention.

`ImportRow` holds a verbatim copy of a platform export — names, phones and
addresses — including for people the organisation decided not to import and
will never contact. That copy has a SHORTER lawful life than the Candidate
record it may have produced, which is the whole reason `apps.imports` is its own
app rather than a corner of recruitment.

TWO CLOCKS
----------
  Uncommitted (24 hours)  A preview nobody acted on. Whatever the intention
                          was, the file was read and abandoned, and there is no
                          lawful purpose in keeping a stranger's contact details
                          because somebody once opened a spreadsheet.

  Committed (7 days)      The candidates that mattered are now Candidate rows
                          governed by the recruitment retention policy. The
                          staging copy is a duplicate of a subset of that, plus
                          everything that was rejected — kept briefly so an
                          operator can still see what happened, then gone.

WHAT SURVIVES
-------------
Everything that is not a person: batch metadata, counts, the file hash, the
column mapping, row numbers, outcomes, match rules, error and warning CODES,
and every audit entry. So "what did this import do" stays answerable forever,
while "who was in the file" stops being answerable on schedule.

The Candidate and Application records the import created are NEVER touched.
They have their own clock, in apps/recruitment/services/retention.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.imports.models import BatchStatus, ImportBatch, ImportRow

#: A preview nobody acted on.
UNCOMMITTED_HOURS = 24
#: Staging copies of work that did land.
COMMITTED_DAYS = 7

COMMITTED_STATUSES = (BatchStatus.COMPLETED, BatchStatus.PARTIAL)

#: Columns holding a person. Cleared to their empty value, not to a placeholder
#: — a placeholder is still a value, and `email` in particular must be NULL
#: rather than "" for the same reason it must be on Candidate.
PII_FIELDS = {
    "raw": dict,
    "first_name": str,
    "last_name": str,
    "email": type(None),
    "phone": str,
    "email_normalized": type(None),
    "phone_e164": type(None),
    "external_id": str,
    "current_employer": str,
    "total_experience_years": type(None),
    "expected_ctc": type(None),
    "notice_period_days": type(None),
    # SECRET_KEY-salted digest of the identity keys. Not readable, but it is
    # still a stable identifier for a person and would let two purged batches be
    # correlated, so it goes too.
    "row_fingerprint": str,
}


@dataclass
class StagingPurgeResult:
    uncommitted_rows: int = 0
    committed_rows: int = 0
    batches: int = 0

    @property
    def total(self) -> int:
        return self.uncommitted_rows + self.committed_rows


def _blank(field: str):
    kind = PII_FIELDS[field]
    if kind is dict:
        return {}
    if kind is str:
        return ""
    return None


def rows_due(*, now=None):
    """
    Staging rows whose PII has outlived its purpose.

    Selects on `raw` being non-empty so an already-purged row is not counted
    again — which is what makes the operation idempotent and the reported counts
    meaningful on a second run.
    """
    now = now or timezone.now()
    uncommitted_before = now - timedelta(hours=UNCOMMITTED_HOURS)
    committed_before = now - timedelta(days=COMMITTED_DAYS)

    return ImportRow.objects.filter(
        Q(
            # Never committed, and old enough that nobody is coming back to it.
            ~Q(batch__status__in=COMMITTED_STATUSES),
            batch__created_at__lt=uncommitted_before,
        )
        | Q(
            batch__status__in=COMMITTED_STATUSES,
            batch__committed_at__lt=committed_before,
        )
    ).exclude(raw={})


@transaction.atomic
def purge_staging_pii(*, apply: bool = False, now=None) -> StagingPurgeResult:
    """
    Strip personal data from expired staging rows.

    Dry by default, following `repair_application_stages` — a routine whose
    purpose is to destroy personal data should be able to say what it would
    destroy first.

    Uses `queryset.update()` deliberately. These rows carry no business meaning
    of their own, there is nothing for a signal to audit that the batch-level
    IMPORT event does not already record, and a per-row save on a large purge
    would be thousands of pointless round trips. The batch counts are the
    audited fact; the rows were only ever scaffolding.
    """
    now = now or timezone.now()
    due = rows_due(now=now)

    uncommitted = due.filter(~Q(batch__status__in=COMMITTED_STATUSES))
    committed = due.filter(batch__status__in=COMMITTED_STATUSES)

    result = StagingPurgeResult(
        uncommitted_rows=uncommitted.count(),
        committed_rows=committed.count(),
        batches=ImportBatch.objects.filter(rows__in=due).distinct().count(),
    )

    if apply and result.total:
        blanks = {field: _blank(field) for field in PII_FIELDS}
        # One statement. Re-running it finds nothing, because `raw` is now {}
        # and `rows_due` excludes that.
        ImportRow.objects.filter(pk__in=list(due.values_list("pk", flat=True))).update(
            **blanks
        )

    return result
