"""
Deciding whether a spreadsheet row is somebody we already know.

PRECEDENCE, AND WHY IT IS THAT ORDER
------------------------------------
  1. platform external id — an identity assertion made by the SOURCE. The
     strongest thing available, because it is not inferred from a contact
     detail at all.
  2. normalized email — strong, but people mistype addresses, share them within
     a household, and abandon them.
  3. exactly one phone match, AND the names overlap — weakest. Numbers are
     shared, and TRAI recycles them after about 90 days of inactivity. The name
     check is what stops a recycled number silently merging two strangers.
  4. several phone matches — refuse, and ask a human.
  5. nothing — create.

A row with no identity key at all is INVALID. Without one, every re-import
manufactures a fresh duplicate of that person forever.

NAME IS NEVER AN IDENTITY KEY. "Ramesh Kumar" matches thousands of people.

CONFLICTS ARE NEVER AUTO-MERGED
-------------------------------
If the email points at candidate A and the phone at candidate B, we proceed with
the higher-precedence match and record a warning naming the other. Merging two
Candidate rows cascades through Application, StageDecision, CandidateRejection
and Offer — all PROTECT — and is irreversible. It is a deliberate human act, not
an import side effect.

The corollary that keeps rule 1 safe: a conflicting field is never written. If
this row's email already belongs to a different active candidate, we simply do
not copy it across, so the enrichment can never collide with the unique index.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.imports.models import MatchRule, RowStatus
from apps.recruitment.models import Candidate, CandidateExternalRef


@dataclass
class Resolution:
    status: str
    candidate: Candidate | None = None
    rule: str = MatchRule.NONE
    warnings: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def _name_tokens(value: str) -> set[str]:
    return {token for token in (value or "").lower().split() if len(token) > 1}


def names_overlap(row_first: str, row_last: str, candidate: Candidate) -> bool:
    """
    Any shared name token is enough.

    Deliberately generous: "Ravi Kumar" vs "Ravi" is the same person written
    differently, which is routine across platforms. The check exists to catch a
    recycled phone number now belonging to somebody else entirely, not to police
    spelling.
    """
    incoming = _name_tokens(f"{row_first} {row_last}")
    existing = _name_tokens(candidate.full_name)
    if not incoming or not existing:
        return False
    return bool(incoming & existing)


def resolve(row, *, platform: str) -> Resolution:
    """
    Decide what a parsed row means against the CURRENT database.

    Called at preview to show the operator what will happen, and called AGAIN at
    commit — never replayed from the stored preview. Between the two, another
    import or a person at a keyboard can create the candidate this row was going
    to create, and acting on a stale verdict is how duplicates appear.
    """
    warnings: list[dict] = []

    if not (row.external_id or row.email_normalized or row.phone_e164):
        return Resolution(
            status=RowStatus.INVALID,
            errors=[{"row": row.row_number, "code": "no_identity_key"}],
        )

    ext = None
    if row.external_id:
        ref = (
            CandidateExternalRef.objects.filter(
                source=platform,
                external_id=row.external_id,
                is_active=True,
                candidate__is_active=True,
            )
            .select_related("candidate")
            .first()
        )
        ext = ref.candidate if ref else None

    by_email = (
        Candidate.objects.filter(
            email_normalized=row.email_normalized, is_active=True
        ).first()
        if row.email_normalized
        else None
    )

    by_phone = (
        list(Candidate.objects.filter(phone_e164=row.phone_e164, is_active=True)[:5])
        if row.phone_e164
        else []
    )

    # --- rule 1: the platform's own assertion wins ------------------------
    if ext is not None:
        if by_email and by_email.pk != ext.pk:
            warnings.append(
                {"row": row.row_number, "code": "email_belongs_to_other_candidate"}
            )
        if by_phone and ext not in by_phone:
            warnings.append(
                {"row": row.row_number, "code": "phone_belongs_to_other_candidate"}
            )
        return Resolution(RowStatus.VALID, ext, MatchRule.EXTERNAL_ID, warnings)

    # --- rule 2: normalized email -----------------------------------------
    if by_email is not None:
        if by_phone and by_email not in by_phone:
            warnings.append(
                {"row": row.row_number, "code": "phone_belongs_to_other_candidate"}
            )
        return Resolution(RowStatus.VALID, by_email, MatchRule.EMAIL, warnings)

    # --- rule 3: exactly one phone, and the names agree -------------------
    if len(by_phone) == 1:
        target = by_phone[0]
        if not names_overlap(row.first_name, row.last_name, target):
            return Resolution(
                RowStatus.NEEDS_REVIEW,
                None,
                MatchRule.PHONE,
                warnings + [
                    {"row": row.row_number, "code": "phone_match_name_mismatch"}
                ],
            )
        return Resolution(RowStatus.VALID, target, MatchRule.PHONE, warnings)

    # --- rule 4: ambiguous ------------------------------------------------
    if len(by_phone) > 1:
        return Resolution(
            RowStatus.NEEDS_REVIEW,
            None,
            MatchRule.PHONE,
            warnings + [{"row": row.row_number, "code": "ambiguous_phone_match"}],
        )

    # --- rule 5: new person ------------------------------------------------
    # One extra lookup purely to warn. A soft-deleted match means somebody
    # deliberately removed this person and the import is about to bring them
    # back as a new record; that should not be silent.
    if row.email_normalized and Candidate.objects.filter(
        email_normalized=row.email_normalized, is_active=False
    ).exists():
        warnings.append({"row": row.row_number, "code": "matched_inactive_candidate"})

    return Resolution(RowStatus.VALID, None, MatchRule.NONE, warnings)


#: Fields an import may fill in on an EXISTING candidate.
#:
#: Enrich-only: written only where the current value is empty. The database is
#: HR's curated record and a platform export is lower-trust bulk data, so
#: overwriting would destroy corrections and — worse — make the operation
#: order-dependent, since re-importing an older export would regress the record.
#: Monotonic and order-independent is what makes the idempotency claim provable.
ENRICHABLE_FIELDS = (
    "last_name",
    "email",
    "phone",
    "current_employer",
    "total_experience_years",
    "expected_ctc",
    "notice_period_days",
)

#: Never touched by an import, at any precedence. Consent and retention are
#: decided by people and by the retention service; `first_name` is never blank
#: on an existing row; `resume` and `is_active` are not an import's business.
PROTECTED_FIELDS = frozenset(
    {
        "first_name",
        "consent_given",
        "consent_at",
        "legal_basis",
        "notice_due_at",
        "final_decision_at",
        "retention_until",
        "is_active",
        "resume",
        "source",
    }
)


def enrich(candidate: Candidate, row, *, conflicting_email: bool) -> list[str]:
    """
    Fill blanks on an existing candidate. Returns the field names actually written.

    `conflicting_email` says this row's address already belongs to somebody
    else, in which case it is skipped rather than written — that is what stops
    an enrichment colliding with the unique index.
    """
    changed: list[str] = []

    for name in ENRICHABLE_FIELDS:
        assert name not in PROTECTED_FIELDS  # guards a careless edit above
        incoming = getattr(row, name, None)
        if incoming in (None, ""):
            continue
        if name == "email" and conflicting_email:
            continue
        if getattr(candidate, name, None) in (None, ""):
            setattr(candidate, name, incoming)
            changed.append(name)

    if changed:
        candidate.save()
    return changed
