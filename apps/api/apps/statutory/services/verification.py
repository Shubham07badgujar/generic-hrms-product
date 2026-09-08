"""
Rate-set verification workflow.

    DRAFT ──submit──> PENDING ──verify──> VERIFIED ──supersede──> SUPERSEDED
      ▲                  │                    │
      └──── reject ──────┘                    │
      └──── parameter edit ──────────────────-┘   (checksum mismatch, automatic)

AUTHORITY
    Finance Head alone verifies. Admin CANNOT — signing off that a rate matches
    the gazette is a finance-competence act, not a systems-administration one.

FOUR EYES
    The person who last edited the parameters cannot normally verify them. A
    controlled exceptional path exists for a one-person finance team so payroll
    can never become permanently blocked; it demands a reason, writes a
    distinct audit event, marks the rule set permanently, and notifies Admin
    and CEO.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from core.access import Action, Resource, require
from core.access.catalog import RoleCode

from ..models import RuleSetAuditEvent, StatutoryRuleSet, VerificationStatus

logger = logging.getLogger("hrms.statutory")

#: Minimum characters for any justification. Long enough that "ok" and "done"
#: are refused — the point is a reviewable statement, not a formality.
MIN_NOTE_LENGTH = 20


class VerificationError(Exception):
    """A workflow transition that the rules do not permit."""


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


@transaction.atomic
def submit_for_verification(rule_set: StatutoryRuleSet, *, actor) -> StatutoryRuleSet:
    """Move DRAFT/REJECTED -> PENDING. Requires provenance to be present."""
    require(actor, Resource.STATUTORY_CONFIG, Action.EDIT)

    if rule_set.verification_status not in (
        VerificationStatus.DRAFT,
        VerificationStatus.REJECTED,
    ):
        raise VerificationError(
            f"Cannot submit a rule set in status '{rule_set.verification_status}'."
        )

    # Verification without evidence is indistinguishable from clicking a button,
    # so the evidence is demanded before the request is even raised.
    missing = []
    if not rule_set.source_citation.strip():
        missing.append("source_citation")
    if rule_set.retrieved_on is None:
        missing.append("retrieved_on")
    if not rule_set.parameters:
        missing.append("parameters")
    if missing:
        raise VerificationError(
            f"Cannot submit without: {', '.join(missing)}. A verifier needs to know "
            f"what these values were taken from and when."
        )

    rule_set.verification_status = VerificationStatus.PENDING
    rule_set.submitted_by = actor
    rule_set.submitted_at = timezone.now()
    rule_set.rejection_reason = ""
    rule_set.save(
        update_fields=[
            "verification_status", "submitted_by", "submitted_at",
            "rejection_reason", "updated_at",
        ]
    )
    _audit(rule_set, RuleSetAuditEvent.SUBMITTED, actor)
    _notify_verifiers(rule_set)
    return rule_set


@transaction.atomic
def verify(
    rule_set: StatutoryRuleSet,
    *,
    actor,
    note: str,
    self_verification_reason: str = "",
) -> StatutoryRuleSet:
    """
    Certify that these values match the cited source. PENDING -> VERIFIED.

    `self_verification_reason` is required only when the verifier is also the
    last editor. Supplying it when four-eyes is satisfiable is refused, so the
    exceptional path cannot become the habitual one.
    """
    # Finance Head only. Deliberately NOT satisfied by Admin — see module docstring.
    require(actor, Resource.STATUTORY_CONFIG, Action.APPROVE)

    if rule_set.verification_status != VerificationStatus.PENDING:
        raise VerificationError(
            f"Only a rule set pending verification can be verified; this one is "
            f"'{rule_set.verification_status}'."
        )

    if len(note.strip()) < MIN_NOTE_LENGTH:
        raise VerificationError(
            f"A verification note of at least {MIN_NOTE_LENGTH} characters is required. "
            f"State what you checked these values against."
        )

    is_self = _is_self_verification(rule_set, actor)
    if is_self:
        if not _may_self_verify(actor):
            raise VerificationError(
                "You last edited this rule set, so you cannot verify it. Ask another "
                "Finance Head to review it. If nobody else can, an authorised "
                "exceptional self-verification is available — it requires a written "
                "reason and is reported to Admin and the CEO."
            )
        if len(self_verification_reason.strip()) < MIN_NOTE_LENGTH:
            raise VerificationError(
                f"Exceptional self-verification requires a reason of at least "
                f"{MIN_NOTE_LENGTH} characters explaining why no second reviewer is "
                f"available."
            )
    elif self_verification_reason.strip():
        raise VerificationError(
            "A self-verification reason was supplied, but you are not the last editor "
            "of this rule set. Verify normally."
        )

    if rule_set.is_tampered:
        raise VerificationError(
            "This rule set's parameters changed after it was last verified. "
            "Re-submit it before verifying."
        )

    rule_set.verification_status = VerificationStatus.VERIFIED
    rule_set.verified_by = actor
    rule_set.verified_at = timezone.now()
    rule_set.verification_note = note.strip()
    rule_set.verified_checksum = rule_set.compute_checksum()
    if is_self:
        rule_set.was_self_verified = True
        rule_set.self_verification_reason = self_verification_reason.strip()

    rule_set.save(
        update_fields=[
            "verification_status", "verified_by", "verified_at", "verification_note",
            "verified_checksum", "was_self_verified", "self_verification_reason",
            "updated_at",
        ]
    )

    event = (
        RuleSetAuditEvent.SELF_VERIFIED if is_self else RuleSetAuditEvent.VERIFIED
    )
    _audit(
        rule_set,
        event,
        actor,
        note=note,
        extra=(
            {"self_verification_reason": self_verification_reason.strip(), "four_eyes": False}
            if is_self
            else {"four_eyes": True}
        ),
    )

    if is_self:
        # Enhanced visibility: the exception is surfaced to the two roles with
        # organization-wide oversight, not buried in a log.
        _notify_oversight_of_self_verification(rule_set, actor, self_verification_reason)
        logger.warning(
            "statutory.self_verified rule_set=%s statute=%s actor=%s",
            rule_set.pk, rule_set.statute, actor.pk,
        )

    _supersede_previous(rule_set, actor=actor)
    return rule_set


@transaction.atomic
def reject(rule_set: StatutoryRuleSet, *, actor, reason: str) -> StatutoryRuleSet:
    """PENDING -> REJECTED, with a reason the editor can act on."""
    require(actor, Resource.STATUTORY_CONFIG, Action.APPROVE)

    if rule_set.verification_status != VerificationStatus.PENDING:
        raise VerificationError(
            f"Only a pending rule set can be rejected; this one is "
            f"'{rule_set.verification_status}'."
        )
    if len(reason.strip()) < MIN_NOTE_LENGTH:
        raise VerificationError(
            f"A rejection reason of at least {MIN_NOTE_LENGTH} characters is required."
        )

    rule_set.verification_status = VerificationStatus.REJECTED
    rule_set.rejection_reason = reason.strip()
    rule_set.save(
        update_fields=["verification_status", "rejection_reason", "updated_at"]
    )
    _audit(rule_set, RuleSetAuditEvent.REJECTED, actor, note=reason)
    return rule_set


def _supersede_previous(new_set: StatutoryRuleSet, *, actor) -> None:
    """
    Close the previously in-force set the moment a successor is verified.

    Without this the exclusion constraint would reject the new set, so this is
    what makes "a rate change opens a new version" work in practice rather than
    requiring a manual close.
    """
    previous = (
        StatutoryRuleSet.objects.filter(
            statute=new_set.statute,
            jurisdiction=new_set.jurisdiction,
            regime=new_set.regime,
            verification_status=VerificationStatus.VERIFIED,
            is_active=True,
        )
        .exclude(pk=new_set.pk)
        .filter(effective_from__lt=new_set.effective_from)
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=new_set.effective_from))
        .order_by("-effective_from")
        .first()
    )
    if previous is None:
        return

    previous.effective_to = new_set.effective_from - timezone.timedelta(days=1)
    previous.verification_status = VerificationStatus.SUPERSEDED
    previous.save(
        update_fields=["effective_to", "verification_status", "updated_at"]
    )
    _audit(
        previous,
        RuleSetAuditEvent.SUPERSEDED,
        actor,
        extra={"superseded_by": str(new_set.pk)},
    )


# ---------------------------------------------------------------------------
# Authority helpers
# ---------------------------------------------------------------------------


def _is_self_verification(rule_set: StatutoryRuleSet, actor) -> bool:
    """
    True when the verifier is the person who last shaped the content.

    Falls back to `submitted_by` and `created_by` so an unset `last_edited_by`
    (data imported outside the app, say) fails SAFE — treated as self-
    verification and therefore subject to the stricter path.
    """
    for attr in ("last_edited_by_id", "submitted_by_id", "created_by_id"):
        editor_id = getattr(rule_set, attr, None)
        if editor_id is not None:
            return editor_id == actor.pk
    return True


def _may_self_verify(actor) -> bool:
    """
    Whether this Finance Head may use the exceptional path.

    Gated on an explicit per-user permission override, not on a role: it is an
    exception granted to a named person in a named situation, and it expires.
    Granting it is itself an audited act by Admin.
    """
    from core.access import can

    return bool(can(actor, Resource.STATUTORY_CONFIG, Action.OVERRIDE))


# ---------------------------------------------------------------------------
# Audit & notification
# ---------------------------------------------------------------------------


def _audit(rule_set, event, actor, *, note: str = "", extra: dict | None = None) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    action_map = {
        RuleSetAuditEvent.VERIFIED: AuditAction.APPROVE,
        RuleSetAuditEvent.SELF_VERIFIED: AuditAction.OVERRIDE,
        RuleSetAuditEvent.REJECTED: AuditAction.REJECT,
        RuleSetAuditEvent.SUPERSEDED: AuditAction.UPDATE,
        RuleSetAuditEvent.AUTO_REVERTED: AuditAction.REVERSE,
    }

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=action_map.get(event, AuditAction.UPDATE),
        resource=Resource.STATUTORY_CONFIG,
        entity_type="statutory.StatutoryRuleSet",
        entity_id=str(rule_set.pk),
        entity_label=str(rule_set)[:255],
        after={
            "event": str(event),
            "statute": rule_set.statute,
            "jurisdiction": rule_set.jurisdiction,
            "regime": rule_set.regime,
            "effective_from": rule_set.effective_from.isoformat(),
            "rule_version": rule_set.rule_version,
            "checksum": rule_set.checksum,
            "note": note,
            **(extra or {}),
        },
        request_id=get_request_id(),
    )


def _notify_verifiers(rule_set) -> None:
    """Tell Finance Heads a rule set is waiting."""
    _notify_roles(
        [RoleCode.FINANCE_HEAD],
        kind="statutory.verification_pending",
        title="Statutory rule set awaiting verification",
        body=f"{rule_set} is pending verification before payroll can use it.",
        entity=rule_set,
    )


def _notify_oversight_of_self_verification(rule_set, actor, reason: str) -> None:
    """
    Report an exceptional self-verification to Admin and CEO.

    Deliberately high priority and unconditional: the four-eyes principle was
    waived on numbers that determine everyone's pay, and that must be visible
    to oversight without anyone going looking for it.
    """
    _notify_roles(
        [RoleCode.ADMIN, RoleCode.CEO],
        kind="statutory.self_verified",
        title="Exceptional self-verification of a statutory rule set",
        body=(
            f"{actor.get_full_name()} verified {rule_set} that they also edited, "
            f"bypassing four-eyes review.\n\nReason given: {reason}"
        ),
        entity=rule_set,
        priority="high",
    )


def _notify_roles(role_codes, *, kind, title, body, entity, priority="normal") -> None:
    from apps.accounts.models import User
    from apps.notifications.services import notify

    recipients = User.objects.filter(
        is_active=True,
        user_roles__is_active=True,
        user_roles__role__code__in=role_codes,
    ).distinct()

    for user in recipients:
        notify(
            recipient=user,
            kind=kind,
            title=title,
            body=body,
            # `notify` derives entity_type/entity_id from the object itself.
            entity=entity,
            priority=priority,
        )


# Imported late to keep the module importable during migrations.
from django.db import models  # noqa: E402
