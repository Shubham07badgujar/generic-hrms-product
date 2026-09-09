"""
Staging for externally sourced candidate data.

WHY A SEPARATE APP
------------------
`ImportRow.raw` holds a full copy of a platform export — real people's names,
phones and addresses, including people the company will never contact. That has
a SHORTER lawful retention than the Candidate record it may or may not produce.
Keeping it in its own app makes "purge staging PII on its own clock" a
self-contained job that cannot reach into the domain tables by accident.

THE FILE ITSELF IS NEVER STORED
-------------------------------
There is no FileField here, deliberately. The upload exists only as the
request's temporary file and is gone before the response is sent. What an
auditor needs is what was imported and by whom, which is these rows plus the
SHA-256 of the source — not the archive itself sitting on the media volume, in
every nightly backup, for the life of the deployment, with no malware scanning
anywhere in this stack.

TWO-PHASE, AND THE SECOND PHASE TRUSTS NOTHING FROM THE FIRST
-------------------------------------------------------------
Upload parses and stages; commit writes. The commit re-resolves every row
against the database rather than replaying the preview's verdict, because
between the two another import — or a person typing at a keyboard — can create
the candidate this row was going to create. That is the easiest thing in the
whole design to get wrong and the most expensive to discover in production.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import OrgOwnedModel, OrgOwnedTimestampedModel


class BatchStatus(models.TextChoices):
    """
    Also the crash-recovery mechanism.

    A process that dies mid-commit leaves the batch in COMMITTING, and the
    resume path re-processes only rows still pending. Idempotent resolution
    makes re-processing an already-done row harmless, so recovery needs no
    separate bookkeeping.
    """

    PARSED = "parsed", "Parsed — awaiting attestation"
    COMMITTING = "committing", "Committing"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Completed with failures"
    DISCARDED = "discarded", "Discarded"


class RowStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VALID = "valid", "Valid"
    INVALID = "invalid", "Invalid"
    NEEDS_REVIEW = "needs_review", "Needs review"
    DUPLICATE_IN_FILE = "duplicate_in_file", "Duplicate within the file"
    CREATED = "created", "Candidate created"
    MATCHED_UPDATED = "matched_updated", "Existing candidate enriched"
    MATCHED_SKIPPED = "matched_skipped", "Existing candidate, nothing to add"
    APPLICATION_EXISTS = "application_exists", "Already applied to this job"
    FAILED = "failed", "Failed"


class MatchRule(models.TextChoices):
    """Which key identified the candidate. Ordered by strength."""

    EXTERNAL_ID = "external_id", "Platform candidate ID"
    EMAIL = "email", "Normalized email"
    PHONE = "phone", "Normalized phone"
    NONE = "none", "No match — created"


class ImportBatch(OrgOwnedModel):
    """One uploaded file, targeted at one job opening."""

    platform = models.CharField(max_length=40, db_index=True)
    job_opening = models.ForeignKey(
        "recruitment.JobOpening", on_delete=models.PROTECT, related_name="import_batches"
    )
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="candidate_imports"
    )

    #: Sanitised at upload — basename only, control characters stripped. Stored
    #: for the audit trail and shown back to the uploader; never used to build a
    #: path and never interpolated into an error message.
    original_filename = models.CharField(max_length=120)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    file_size_bytes = models.PositiveIntegerField(default=0)
    #: What the client CLAIMED the file was. Recorded, never trusted — the
    #: accept decision is made from the extension and the bytes.
    declared_content_type = models.CharField(max_length=100, blank=True)
    #: canonical field -> header. Column NAMES only, never cell values.
    column_mapping = models.JSONField(default=dict, blank=True)
    #: Every header the file declared, and the ones nothing claimed. Column
    #: names, so no PII — and what lets the preview offer a manual remap
    #: instead of dead-ending an export whose names we simply do not know.
    detected_headers = models.JSONField(default=list, blank=True)
    unmapped_headers = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20, choices=BatchStatus.choices, default=BatchStatus.PARSED,
        db_index=True,
    )

    rows_total = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0)
    rows_review = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)

    # ---- the attestation ---------------------------------------------------
    #
    # An import cannot claim consent — the candidate agreed with the PLATFORM,
    # not with us. What one HR human can do, once per file, is attest the ground
    # on which the organisation holds this data. That attestation is the whole
    # lawful basis for the batch, so it is recorded with who made it, when, and
    # the exact wording they were shown.
    legal_basis = models.CharField(max_length=32, blank=True)
    legal_basis_note = models.TextField(blank=True)
    #: The platform account the export came from, and when it was exported.
    #: Evidence that the data is what it claims to be.
    platform_account_ref = models.CharField(max_length=120, blank=True)
    source_export_date = models.DateField(null=True, blank=True)
    #: Snapshotted, not referenced. The wording will change; this record must
    #: still say what was agreed to on the day — the same reasoning as
    #: CandidateRejection.history_snapshot.
    attestation_text = models.TextField(blank=True)
    attested_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="candidate_import_attestations",
    )
    attested_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["uploaded_by", "-created_at"]),
            models.Index(fields=["job_opening", "status"]),
        ]
        constraints = [
            # The consent floor, at the database rather than in a service a
            # future refactor could route around. A batch cannot reach a
            # committed state without an attestation naming a basis, a note
            # explaining it and the person who made it.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=["committing", "completed", "partial"])
                    | (
                        models.Q(attested_by__isnull=False)
                        & models.Q(attested_at__isnull=False)
                        & ~models.Q(legal_basis="")
                        & ~models.Q(legal_basis_note="")
                    )
                ),
                name="ck_import_committed_requires_attestation",
            ),
            # Matches the 20-character floor the rejection rationale uses. An
            # attestation nobody had to think about is not an attestation.
            models.CheckConstraint(
                condition=models.Q(legal_basis_note="")
                | models.Q(legal_basis_note__length__gte=20),
                name="ck_import_basis_note_minimum_length",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.platform} → {self.job_opening_id} ({self.rows_total} rows)"

    @property
    def is_attested(self) -> bool:
        return bool(self.attested_by_id and self.attested_at and self.legal_basis)


class ImportRow(OrgOwnedTimestampedModel):
    """
    One spreadsheet row, parsed and normalised.

    `TimestampedModel`, not `BaseModel`: these are staging records with no
    actor of their own and no soft-delete semantics — the batch owns them, and
    the retention job removes them outright.
    """

    #: Inherits its organization from `batch` rather than from the
    #: acting context, so a child can never disagree with its parent.
    org_source = "batch"

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="rows"
    )
    row_number = models.PositiveIntegerField()

    #: The parsed cells, before normalisation. PII, and the reason this app has
    #: a shorter retention clock than the domain tables.
    raw = models.JSONField(default=dict, blank=True)

    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email_normalized = models.EmailField(null=True, blank=True, db_index=True)
    phone_e164 = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    external_id = models.CharField(max_length=128, blank=True, db_index=True)
    current_employer = models.CharField(max_length=160, blank=True)
    total_experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    expected_ctc = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    notice_period_days = models.PositiveSmallIntegerField(null=True, blank=True)

    #: Stable digest of the row's identity keys, for spotting the same person
    #: appearing twice in one file. Scoped to within a batch: it is
    #: SECRET_KEY-salted, so it must never be relied on across a key rotation.
    row_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)

    status = models.CharField(
        max_length=24, choices=RowStatus.choices, default=RowStatus.PENDING,
        db_index=True,
    )
    match_rule = models.CharField(
        max_length=16, choices=MatchRule.choices, blank=True
    )
    matched_candidate = models.ForeignKey(
        "recruitment.Candidate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    duplicate_of_row = models.PositiveIntegerField(null=True, blank=True)

    #: Machine-readable only: {"column": "email", "code": "invalid_email"}.
    #: NEVER the offending value — this is the object that gets logged,
    #: audited and shipped to Sentry.
    errors = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["batch", "row_number"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["batch", "row_fingerprint"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_number"], name="uniq_import_row_per_batch"
            )
        ]

    def __str__(self) -> str:
        return f"row {self.row_number} of {self.batch_id}"

    @property
    def has_identity(self) -> bool:
        """Some way to recognise this person again."""
        return bool(self.external_id or self.email_normalized or self.phone_e164)
