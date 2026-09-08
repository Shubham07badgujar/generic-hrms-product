"""
Candidate data retention.

`config/settings/base.py` has pointed at this file since the retention clock was
written. The file did not exist. `Candidate.retention_until` was set by
`engine._stamp_final_decision` and read by nobody, so the policy was documented,
scheduled in a comment, and never once executed.

Bulk import is about to multiply candidate volume by an order of magnitude,
which turns that gap from an oversight into an exposure. Hence this.

ANONYMISE, NEVER DELETE
-----------------------
The settings comment already committed to this and it is also the only thing
that works: `Application.candidate` is PROTECT, and applications carry the
hiring record — decisions, interview feedback, rejection rationales — which the
organisation needs for its own defence long after the person's contact details
should be gone. Deleting the candidate row would either fail on the constraint
or take the hiring history with it.

So the personal data is destroyed in place and the shell of the record remains:
enough to say "a candidate was rejected at stage 4 in March", not enough to say
who they were.

TWO CLOCKS
----------
  * Candidates who reached a final decision — hired, rejected, offer declined —
    are governed by CANDIDATE_RETENTION_MONTHS from that decision.
  * Candidates acquired in bulk who never affirmed consent are governed by
    CANDIDATE_UNAFFIRMED_RETENTION_DAYS, which is much shorter. We approached
    them; they never asked to be in our system. Data minimisation is the
    strongest argument available for holding third-party-sourced data at all,
    and it is worth very little if the holding is indefinite.

Anyone still in an active pipeline is never purged, whatever the clock says —
an in-flight application is a live purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    ApplicationStatus,
    Candidate,
    CandidateExternalRef,
    ConsentRecord,
)

#: Statuses that mean the pipeline is still running for this person.
LIVE_STATUSES = frozenset(
    {
        ApplicationStatus.ACTIVE,
        ApplicationStatus.SELECTED,
        ApplicationStatus.OFFER_SENT,
        ApplicationStatus.OFFER_ACCEPTED,
    }
)

#: What an anonymised candidate looks like. Deliberately not "" everywhere:
#: a name is needed for the record to render at all in a list of past
#: applications, and "Redacted" says what happened rather than looking like
#: missing data.
REDACTED_FIRST_NAME = "Redacted"
REDACTED_LAST_NAME = "Candidate"


@dataclass
class PurgeResult:
    anonymised: list[str] = field(default_factory=list)
    skipped_live: int = 0
    considered: int = 0

    @property
    def count(self) -> int:
        return len(self.anonymised)


def _due_final_decision(now):
    """Candidates whose post-decision retention has run out."""
    return Candidate.objects.filter(
        is_active=True,
        retention_until__isnull=False,
        retention_until__lt=now.date(),
    )


def _due_unaffirmed(now):
    """
    Bulk-sourced candidates who never affirmed, past the shorter clock.

    "Never affirmed" is read from the ledger rather than from Candidate, because
    the ledger is what records an upgrade from s.7(a) to s.6. A candidate who
    replied and consented has an affirmed record and is governed by the ordinary
    clock instead.
    """
    days = getattr(settings, "CANDIDATE_UNAFFIRMED_RETENTION_DAYS", 90)
    cutoff = now - timedelta(days=days)

    return (
        Candidate.objects.filter(
            is_active=True,
            consent_given=False,
            created_at__lt=cutoff,
        )
        .exclude(consent_records__affirmed_at__isnull=False)
        .exclude(legal_basis="")
    )


def candidates_due_for_purge(*, now=None):
    """Everything past a clock, whichever clock applies. Live pipelines excluded."""
    now = now or timezone.now()

    due_ids = set(_due_final_decision(now).values_list("pk", flat=True))
    due_ids |= set(_due_unaffirmed(now).values_list("pk", flat=True))
    if not due_ids:
        return Candidate.objects.none()

    # An in-flight application is a live purpose for holding the data, and
    # outranks any clock.
    in_flight = set(
        Application.objects.filter(
            candidate_id__in=due_ids,
            is_active=True,
            status__in=LIVE_STATUSES,
        ).values_list("candidate_id", flat=True)
    )
    return Candidate.objects.filter(pk__in=due_ids - in_flight)


@transaction.atomic
def anonymise_candidate(candidate: Candidate) -> None:
    """
    Destroy the personal data in place, keeping the hiring record.

    Every direct identifier goes. `phone_e164` and `email_normalized` go with
    them — they are derived from the identifiers and would otherwise remain a
    perfectly good way to recognise the person we just anonymised.

    External refs are hard-deleted rather than soft-deleted: a platform's
    candidate id is itself an identifier, and a soft-deleted row still holds it.
    """
    candidate.first_name = REDACTED_FIRST_NAME
    candidate.last_name = REDACTED_LAST_NAME
    candidate.email = None
    candidate.email_normalized = None
    candidate.phone = ""
    candidate.phone_e164 = None
    candidate.current_employer = ""
    candidate.expected_ctc = None
    candidate.notice_period_days = None
    candidate.total_experience_years = None
    candidate.notice_due_at = None

    if candidate.resume:
        candidate.resume.delete(save=False)

    # save() re-derives the keys from the now-empty fields, so update_fields
    # must not be narrowed here.
    candidate.save()

    # The ledger keeps its shape — basis, dates, who recorded it — because that
    # is the evidence the purge itself was lawful. The evidence blob is where
    # free-text about the person could hide, so it goes.
    candidate.consent_records.update(evidence={})

    # hard_delete(), NOT delete(). The default on this codebase's querysets is a
    # soft delete — `update(is_active=False)` — which would leave the platform's
    # own candidate id sitting in the table. That id IS an identifier, and one
    # that would happily re-match this person on the next import.
    CandidateExternalRef.objects.filter(candidate=candidate).hard_delete()


def purge_expired_candidates(*, apply: bool = False, now=None) -> PurgeResult:
    """
    Run the retention policy.

    Dry by default, following `repair_application_stages`: a routine that
    destroys personal data should report what it would do before it does it.
    """
    now = now or timezone.now()
    due = candidates_due_for_purge(now=now)
    result = PurgeResult(considered=due.count())

    for candidate in due.iterator(chunk_size=200):
        result.anonymised.append(str(candidate.pk))
        if apply:
            anonymise_candidate(candidate)

    return result


def record_consent(
    *,
    candidate: Candidate,
    basis: str,
    recorded_by,
    recorded_via: str,
    evidence: dict | None = None,
) -> ConsentRecord:
    """Append a basis to the ledger and mirror it onto the candidate."""
    record = ConsentRecord.objects.create(
        candidate=candidate,
        basis=basis,
        recorded_by=recorded_by,
        recorded_via=recorded_via,
        evidence=evidence or {},
        created_by=recorded_by,
    )
    if not candidate.legal_basis:
        candidate.legal_basis = basis
        candidate.save(update_fields=["legal_basis", "updated_at"])
    return record
