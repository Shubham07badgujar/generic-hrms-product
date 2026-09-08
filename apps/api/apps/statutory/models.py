"""
Versioned statutory rule sets.

A rule set is the NUMBERS for one statute, in one jurisdiction, over one date
range: rates, thresholds, slabs, caps, and eligibility parameters. The LOGIC
that interprets them lives in a versioned Python class named by `rule_version`.

Rate changes are data. Methodology changes are a controlled release. There is
deliberately no expression DSL — see docs/STATUTORY_ENGINE_DESIGN.md §1.3.
"""

from __future__ import annotations

import hashlib
import json

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q, Value

from core.models import BaseModel

from .contracts import Statute


class DateRange(Func):
    """`daterange(effective_from, effective_to, '[]')` for the exclusion constraint.

    Inclusive on both ends: a rule set effective 2024-04-01 to 2025-03-31 is in
    force ON 2025-03-31, so the next set must start 2025-04-01.
    """

    function = "daterange"
    output_field = DateTimeRangeField()


class VerificationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending verification"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"


STATUTE_CHOICES = [
    (Statute.PF, "Provident Fund"),
    (Statute.ESI, "Employees' State Insurance"),
    (Statute.PROFESSIONAL_TAX, "Professional Tax"),
    (Statute.GRATUITY, "Gratuity"),
    (Statute.INCOME_TAX, "Income Tax / TDS"),
]


def canonical_checksum(rule_version: str, parameters: dict) -> str:
    """
    SHA-256 over the version and canonicalised parameters.

    `sort_keys` + fixed separators so semantically identical parameters always
    hash identically regardless of key order or whitespace — otherwise a
    re-save with no real change would look like tampering.
    """
    payload = json.dumps(
        {"rule_version": rule_version, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class StatutoryRuleSet(BaseModel):
    statute = models.CharField(max_length=20, choices=STATUTE_CHOICES, db_index=True)

    jurisdiction = models.CharField(
        max_length=8,
        blank=True,
        db_index=True,
        help_text="State code for Professional Tax. Empty for national statutes.",
    )
    regime = models.CharField(
        max_length=16,
        blank=True,
        help_text="'old' / 'new' for income tax. Empty where not applicable.",
    )

    # --- validity ---
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(
        null=True, blank=True, help_text="NULL = still in force. Inclusive."
    )
    financial_year = models.CharField(
        max_length=9, blank=True, db_index=True, help_text='e.g. "2025-2026".'
    )

    # --- content ---
    rule_version = models.CharField(
        max_length=40,
        help_text="Selects the implementation class, e.g. 'income_tax.v2'. "
        "The class declares which parameters it requires.",
    )
    parameters = models.JSONField(
        default=dict, help_text="Rates, thresholds, slabs, caps, eligibility parameters."
    )

    # --- provenance (required before verification) ---
    source_citation = models.CharField(
        max_length=500,
        blank=True,
        help_text="Statute, section, notification or circular these values come from.",
    )
    source_url = models.URLField(blank=True, max_length=500)
    retrieved_on = models.DateField(null=True, blank=True)
    assumptions = models.JSONField(
        default=list, blank=True, help_text="Stated limits of applicability."
    )

    # --- verification ---
    verification_status = models.CharField(
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.DRAFT,
        db_index=True,
    )
    submitted_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_note = models.TextField(
        blank=True, help_text="What the verifier checked these values against."
    )
    rejection_reason = models.TextField(blank=True)

    #: Who last touched `parameters`. Drives the four-eyes rule: this person
    #: cannot normally be the verifier.
    last_edited_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # --- exceptional self-verification (one-person finance team) ---
    was_self_verified = models.BooleanField(
        default=False,
        help_text="Verified by the same person who edited it, under the "
        "exceptional path. Permanent — never cleared, so the exception stays "
        "visible for the life of the rule set.",
    )
    self_verification_reason = models.TextField(blank=True)

    # --- tamper detection ---
    checksum = models.CharField(max_length=64, editable=False, blank=True)
    verified_checksum = models.CharField(max_length=64, editable=False, blank=True)

    class Meta:
        ordering = ["statute", "jurisdiction", "-effective_from"]
        constraints = [
            # PostgreSQL itself refuses two in-force rule sets covering the same
            # statute+jurisdiction+regime over overlapping dates. Ambiguous
            # resolution becomes structurally impossible rather than something
            # the resolver has to tie-break.
            #
            # Scoped to verified/superseded so competing DRAFTS may be prepared
            # side by side — which is the normal way a finance team stages next
            # year's rates before the Finance Act is final.
            ExclusionConstraint(
                name="excl_ruleset_no_overlapping_in_force",
                expressions=[
                    ("statute", RangeOperators.EQUAL),
                    ("jurisdiction", RangeOperators.EQUAL),
                    ("regime", RangeOperators.EQUAL),
                    (
                        DateRange(
                            F("effective_from"),
                            F("effective_to"),
                            Value("[]"),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(
                    verification_status__in=[
                        VerificationStatus.VERIFIED,
                        VerificationStatus.SUPERSEDED,
                    ]
                )
                & Q(is_active=True),
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True)
                | Q(effective_to__gte=F("effective_from")),
                name="ck_ruleset_effective_range_ordered",
            ),
        ]
        indexes = [
            models.Index(fields=["statute", "jurisdiction", "effective_from"]),
            models.Index(fields=["verification_status", "statute"]),
        ]

    def __str__(self) -> str:
        parts = [self.statute]
        if self.jurisdiction:
            parts.append(self.jurisdiction)
        if self.regime:
            parts.append(self.regime)
        window = f"{self.effective_from}→{self.effective_to or 'open'}"
        return f"{'/'.join(parts)} {window} [{self.verification_status}]"

    # -- integrity ---------------------------------------------------------

    def compute_checksum(self) -> str:
        return canonical_checksum(self.rule_version, self.parameters or {})

    @property
    def is_tampered(self) -> bool:
        """Content changed since it was verified."""
        return bool(self.verified_checksum) and self.checksum != self.verified_checksum

    @property
    def is_usable_for_payroll(self) -> bool:
        return (
            self.verification_status == VerificationStatus.VERIFIED
            and self.is_active
            and not self.is_tampered
        )

    def save(self, *args, **kwargs):
        """
        Recompute the checksum on every write, and auto-revert a verified set
        whose content has changed.

        Editing a verified rate silently is the failure mode this prevents —
        the previous system's statutory values drifted for a year behind a
        warning nobody read.
        """
        self.checksum = self.compute_checksum()

        if (
            self.verification_status == VerificationStatus.VERIFIED
            and self.verified_checksum
            and self.checksum != self.verified_checksum
        ):
            self.verification_status = VerificationStatus.DRAFT
            self.verified_by = None
            self.verified_at = None
            self.verified_checksum = ""
            self.verification_note = (
                f"{self.verification_note}\n\n[AUTO-REVERTED] Parameters changed after "
                f"verification; this rule set must be re-verified before use."
            ).strip()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "verification_status",
                    "verified_by",
                    "verified_at",
                    "verified_checksum",
                    "verification_note",
                    "checksum",
                }
        elif kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"checksum"}

        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "Must not precede effective_from."})
        if self.statute == Statute.PROFESSIONAL_TAX and not self.jurisdiction:
            raise ValidationError(
                {"jurisdiction": "Professional Tax rule sets require a state code."}
            )
        if self.statute == Statute.INCOME_TAX and not self.regime:
            raise ValidationError(
                {"regime": "Income tax rule sets require a regime ('old' or 'new')."}
            )
        if self.statute == Statute.INCOME_TAX and not self.financial_year:
            raise ValidationError(
                {"financial_year": "Income tax rule sets require a financial year."}
            )


class RuleSetAuditEvent(models.TextChoices):
    """Lifecycle events, recorded separately from the generic field-diff audit
    because *what happened* matters more here than *which column changed*."""

    CREATED = "created", "Created"
    EDITED = "edited", "Parameters edited"
    SUBMITTED = "submitted", "Submitted for verification"
    VERIFIED = "verified", "Verified"
    SELF_VERIFIED = "self_verified", "Self-verified (exceptional)"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"
    AUTO_REVERTED = "auto_reverted", "Auto-reverted (content changed)"
