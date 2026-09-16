"""
The candidate importer.

Two phases, and the second trusts nothing from the first.

UPLOAD → PREVIEW
    Validate the bytes, parse them, normalise each row, resolve it against the
    database, and stage the result. No domain writes at all. The uploaded file
    is never persisted — it exists as the request's temporary file and is gone
    when the response is sent.

COMMIT
    Re-authorise the actor, re-resolve the job opening against their scope, and
    re-resolve EVERY row from scratch. The preview's verdict is display only:
    between preview and commit another import — or a person typing — can create
    the candidate this row was going to create, and acting on the stale answer
    is how duplicates appear.

TRANSACTIONS
    One savepoint per row, covering the candidate AND its application together.
    Not one transaction for the file: a real platform export has a nonzero
    failure rate, so all-or-nothing would essentially never succeed and HR's
    only recourse would be perfecting a 500-row spreadsheet. Not a savepoint per
    object either: a candidate committed without its application is invisible to
    everyone below Scope.ALL, because both candidate scope paths traverse
    `applications__`.

    So one bad row costs one row, and never leaves an orphan.

RESUMABILITY
    A process that dies mid-commit leaves the batch in COMMITTING with some rows
    still pending. Re-running processes only those, and idempotent resolution
    makes re-processing a finished row harmless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.imports.models import (
    BatchStatus,
    ImportBatch,
    ImportRow,
    MatchRule,
    RowStatus,
)
from apps.imports.platforms.registry import PlatformSpec
from apps.imports.services import dedup
from apps.imports.services.parsing import parse
from apps.recruitment.access import resolve_job_opening
from apps.recruitment.models import Application, Candidate, CandidateExternalRef
from apps.recruitment.services import intake, retention
from core.access import Action, Resource, require
from core.api.exceptions import BusinessRuleError
from core.fields import fingerprint
from core.models import TenancyError
from core.phone import to_e164_in
from core.validators import validate_upload

logger = logging.getLogger("hrms.audit")

ALLOWED_EXTENSIONS = frozenset({".xlsx", ".csv"})
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
#: A durable ceiling that survives a Redis restart, unlike the request throttle.
DAILY_ROW_BUDGET = 10_000


class ImportError_(Exception):
    """Base for import refusals that are not field validation."""


@dataclass
class CommitResult:
    batch: ImportBatch
    created: int = 0
    updated: int = 0
    duplicate: int = 0
    review: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- upload


def _normalise_row(parsed: dict) -> dict:
    """Canonical row -> the fields ImportRow stores."""
    email = (parsed.get("email") or "").strip().lower() or None
    phone = (parsed.get("phone") or "").strip()
    return {
        "first_name": (parsed.get("first_name") or "")[:100],
        "last_name": (parsed.get("last_name") or "")[:100],
        "email": email,
        "email_normalized": email,
        "phone": phone[:40],
        "phone_e164": to_e164_in(phone),
        "external_id": (parsed.get("external_id") or "")[:128],
        "current_employer": (parsed.get("current_employer") or "")[:160],
        "total_experience_years": parsed.get("total_experience_years"),
        "expected_ctc": parsed.get("expected_ctc"),
        "notice_period_days": parsed.get("notice_period_days"),
    }


@transaction.atomic
def create_batch(
    *,
    actor,
    platform: PlatformSpec,
    job_opening,
    file,
    column_override: dict | None = None,
) -> ImportBatch:
    """
    Validate, parse and stage an upload. Writes no candidates and no applications.
    """
    require(actor, Resource.CANDIDATE, Action.IMPORT)

    if not platform.available:
        raise BusinessRuleError(platform.unavailable_reason)

    # Scope check on the specific job, by the same narrowing that filters the
    # job list. Raises Http404 rather than 403 — see apps.recruitment.access.
    job_opening = resolve_job_opening(job_opening, user=actor)

    # Refuse a job whose workflow cannot accept anyone BEFORE parsing, so a
    # 2,000-row file is not read only to fail at the first write.
    intake.first_stage_or_refuse(job_opening)

    facts = validate_upload(
        file,
        allowed_extensions=ALLOWED_EXTENSIONS,
        max_bytes=MAX_UPLOAD_BYTES,
        subject="spreadsheet",
    )
    parsed = parse(file, facts=facts, spec=platform, column_override=column_override)

    batch = ImportBatch.objects.create(
        platform=platform.key,
        job_opening=job_opening,
        uploaded_by=actor,
        original_filename=facts.original_name,
        file_sha256=facts.sha256,
        file_size_bytes=facts.size_bytes,
        declared_content_type=facts.declared_content_type,
        column_mapping=parsed.mapping,
        detected_headers=parsed.headers,
        unmapped_headers=parsed.unmapped_headers,
        rows_total=len(parsed.rows),
        created_by=actor,
    )

    seen: dict[str, int] = {}
    rows: list[ImportRow] = []

    for parsed_row in parsed.rows:
        fields = _normalise_row(parsed_row)
        row = ImportRow(
            batch=batch,
            row_number=parsed_row["_row"],
            raw=parsed_row.get("_raw", {}),
            **fields,
        )

        # Within-file duplicates. Platform exports produce these constantly.
        # Scoped to this batch only: the digest is SECRET_KEY-salted, so it must
        # never be relied on across a key rotation.
        digest = fingerprint(
            f"{platform.key}|{row.external_id}|{row.email_normalized}|{row.phone_e164}"
        )
        row.row_fingerprint = digest or ""

        if not row.first_name:
            row.status = RowStatus.INVALID
            row.errors = [{"row": row.row_number, "code": "missing_name"}]
        elif digest and digest in seen:
            row.status = RowStatus.DUPLICATE_IN_FILE
            row.duplicate_of_row = seen[digest]
        else:
            if digest:
                seen[digest] = row.row_number
            outcome = dedup.resolve(row, platform=platform.key)
            row.status = outcome.status
            row.match_rule = outcome.rule
            row.matched_candidate = outcome.candidate
            row.errors = outcome.errors
            row.warnings = outcome.warnings

        rows.append(row)

    ImportRow.objects.bulk_create(rows, batch_size=500)
    _refresh_counters(batch)

    # Counts, hashes and identifiers. No cell content, no candidate PII — this
    # line goes to container logs, which are not a PII store.
    logger.info(
        "import.preview batch=%s actor=%s platform=%s rows=%s sha=%s",
        batch.pk, actor.pk, platform.key, batch.rows_total, facts.sha256[:12],
    )
    _audit(batch, actor=actor, verb="import", extra={"phase": "preview"})
    return batch


# ---------------------------------------------------------------- commit


def _refresh_counters(batch: ImportBatch) -> None:
    """Recount authoritatively from the rows, never from an in-memory tally."""
    from django.db.models import Count

    counts = dict(
        ImportRow.objects.filter(batch=batch)
        .values_list("status")
        .annotate(n=Count("id"))
    )
    batch.rows_total = sum(counts.values())
    batch.rows_created = counts.get(RowStatus.CREATED, 0)
    batch.rows_updated = counts.get(RowStatus.MATCHED_UPDATED, 0)
    batch.rows_duplicate = (
        counts.get(RowStatus.MATCHED_SKIPPED, 0)
        + counts.get(RowStatus.DUPLICATE_IN_FILE, 0)
        + counts.get(RowStatus.APPLICATION_EXISTS, 0)
    )
    batch.rows_review = counts.get(RowStatus.NEEDS_REVIEW, 0)
    batch.rows_failed = counts.get(RowStatus.FAILED, 0) + counts.get(
        RowStatus.INVALID, 0
    )
    batch.save(
        update_fields=[
            "rows_total", "rows_created", "rows_updated", "rows_duplicate",
            "rows_review", "rows_failed", "updated_at",
        ]
    )


def _audit(batch, *, actor, verb, extra=None):
    from apps.audit.events import record_event

    payload = {
        "platform": batch.platform,
        "job_opening_id": str(batch.job_opening_id),
        "original_filename": batch.original_filename,
        "file_sha256": batch.file_sha256,
        "file_size_bytes": batch.file_size_bytes,
        "declared_content_type": batch.declared_content_type,
        "column_mapping": batch.column_mapping,
        "rows_total": batch.rows_total,
        "rows_created": batch.rows_created,
        "rows_updated": batch.rows_updated,
        "rows_duplicate": batch.rows_duplicate,
        "rows_review": batch.rows_review,
        "rows_failed": batch.rows_failed,
        "legal_basis": batch.legal_basis,
    }
    payload.update(extra or {})
    # Counts, hashes, column NAMES and the attestation. No candidate PII: the
    # per-row CREATE events already carry what was written, and duplicating it
    # here would put the whole export in a second place.
    record_event(
        batch,
        actor=actor,
        entity_type="imports.ImportBatch",
        verb=verb,
        resource=Resource.CANDIDATE,
        after=payload,
        reason=batch.legal_basis_note or None,
    )


def _check_row_budget(actor, pending: int) -> None:
    """
    A durable 24-hour ceiling.

    The request throttle counts requests and lives in Redis, so a restart resets
    it and it cannot express "rows". This is the backstop that can.
    """
    from datetime import timedelta

    since = timezone.now() - timedelta(hours=24)
    spent = ImportRow.objects.filter(
        batch__uploaded_by=actor,
        batch__committed_at__gte=since,
        status__in=[RowStatus.CREATED, RowStatus.MATCHED_UPDATED],
    ).count()
    if spent + pending > DAILY_ROW_BUDGET:
        raise BusinessRuleError(
            f"This would exceed the daily import limit of {DAILY_ROW_BUDGET:,} "
            f"candidate rows. {spent:,} have been imported in the last 24 hours."
        )


def attest(
    *,
    actor,
    batch: ImportBatch,
    legal_basis: str,
    legal_basis_note: str,
    attestation_text: str,
    platform_account_ref: str = "",
    source_export_date=None,
) -> ImportBatch:
    """Record the lawful basis one HR human is claiming for this whole file."""
    require(actor, Resource.CANDIDATE, Action.IMPORT)

    batch.legal_basis = legal_basis
    batch.legal_basis_note = legal_basis_note
    batch.attestation_text = attestation_text
    batch.platform_account_ref = platform_account_ref
    batch.source_export_date = source_export_date
    batch.attested_by = actor
    batch.attested_at = timezone.now()
    batch.save(
        update_fields=[
            "legal_basis", "legal_basis_note", "attestation_text",
            "platform_account_ref", "source_export_date",
            "attested_by", "attested_at", "updated_at",
        ]
    )
    return batch


def commit(*, actor, batch: ImportBatch) -> CommitResult:
    """
    Write the staged rows into the recruitment pipeline.

    Everything is re-checked here. The preview ran at some earlier moment under
    permissions that may since have been revoked, against a job that may since
    have moved out of the actor's scope, on a database that has since changed.
    """
    # 1. Re-authorise. `require` before any read, so the rejection path never
    #    touches the database.
    require(actor, Resource.CANDIDATE, Action.IMPORT)

    # 2. Re-resolve the job against the actor's CURRENT scope. 404, not 403.
    job = resolve_job_opening(batch.job_opening, user=actor)
    first_stage = intake.first_stage_or_refuse(job)

    if not batch.is_attested:
        raise BusinessRuleError(
            "This import has no recorded legal basis. Attest one before importing."
        )
    if batch.status in (BatchStatus.COMPLETED, BatchStatus.PARTIAL):
        # Already done. Returning the existing result rather than raising keeps
        # a retried request idempotent.
        return _result_from(batch)
    if batch.status == BatchStatus.DISCARDED:
        raise BusinessRuleError("This import was discarded.")

    pending = ImportRow.objects.filter(
        batch=batch, status__in=[RowStatus.VALID, RowStatus.PENDING]
    )
    _check_row_budget(actor, pending.count())

    batch.status = BatchStatus.COMMITTING
    batch.save(update_fields=["status", "updated_at"])

    result = CommitResult(batch=batch)

    # One advisory lock per job for the duration. Two people committing files
    # that share candidates against the same job would otherwise race on the
    # email unique index; this serialises them without serialising all intake.
    with transaction.atomic():
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"candidate_import:{job.pk}"],
            )

    for row in pending.iterator(chunk_size=200):
        _commit_row(row, actor=actor, job=job, batch=batch, first_stage=first_stage, result=result)

    _refresh_counters(batch)
    batch.refresh_from_db()
    batch.status = (
        BatchStatus.PARTIAL if batch.rows_failed or batch.rows_review else BatchStatus.COMPLETED
    )
    batch.committed_at = timezone.now()
    batch.save(update_fields=["status", "committed_at", "updated_at"])

    logger.info(
        "import.commit batch=%s actor=%s created=%s updated=%s failed=%s",
        batch.pk, actor.pk, result.created, result.updated, result.failed,
    )
    _audit(batch, actor=actor, verb="import", extra={"phase": "commit"})
    return result


def _commit_row(row, *, actor, job, batch, first_stage, result) -> None:
    """
    One row, in its own savepoint.

    The savepoint spans the candidate AND the application deliberately: a
    candidate without an application is invisible to everyone below Scope.ALL,
    since both candidate scope paths traverse `applications__`. Committing them
    separately is exactly how an orphan is made.
    """
    try:
        with transaction.atomic():
            # Re-resolve from the database. The stored preview verdict is
            # display only and may be stale by now.
            outcome = dedup.resolve(row, platform=batch.platform)

            if outcome.status in (RowStatus.INVALID, RowStatus.NEEDS_REVIEW):
                row.status = outcome.status
                row.errors = outcome.errors
                row.warnings = outcome.warnings
                row.save(update_fields=["status", "errors", "warnings"])
                if outcome.status == RowStatus.NEEDS_REVIEW:
                    result.review += 1
                else:
                    result.failed += 1
                    result.failures.append(
                        {"row": row.row_number, "code": _first_code(outcome.errors)}
                    )
                return

            candidate = outcome.candidate
            conflicting_email = any(
                w.get("code") == "email_belongs_to_other_candidate"
                for w in outcome.warnings
            )

            # The platform's curated extras (city, education, skills, …) from
            # the row's original cells — see PlatformSpec.profile_columns.
            from apps.imports.platforms.registry import get_platform

            spec = get_platform(batch.platform)
            extras = spec.profile_from_raw(row.raw) if spec else {}

            if candidate is None:
                candidate = intake.create_candidate(
                    actor=actor,
                    first_name=row.first_name,
                    last_name=row.last_name,
                    email=row.email,
                    phone=row.phone,
                    current_employer=row.current_employer,
                    total_experience_years=row.total_experience_years,
                    expected_ctc=row.expected_ctc,
                    notice_period_days=row.notice_period_days,
                    source=batch.platform,
                    profile=extras,
                    # NOT consent. They agreed with the platform, not with us.
                    consent_given=False,
                    legal_basis=batch.legal_basis,
                    notice_due_at=timezone.now(),
                )
                row.status = RowStatus.CREATED
                result.created += 1
            else:
                changed = dedup.enrich(
                    candidate, row, conflicting_email=conflicting_email
                )
                # Same fill-the-blanks contract as enrich: profile keys the
                # candidate lacks are added, existing ones are never rewritten.
                missing = {
                    key: value
                    for key, value in extras.items()
                    if not (candidate.profile or {}).get(key)
                }
                if missing:
                    candidate.profile = {**(candidate.profile or {}), **missing}
                    candidate.save(update_fields=["profile", "updated_at"])
                    changed = list(changed) + list(missing)
                row.status = (
                    RowStatus.MATCHED_UPDATED if changed else RowStatus.MATCHED_SKIPPED
                )
                if changed:
                    result.updated += 1
                else:
                    result.duplicate += 1

            # The platform's id, recorded whether the candidate was created or
            # matched. This is the one thing an import always adds, and it is
            # what makes the NEXT import stronger.
            if row.external_id:
                CandidateExternalRef.objects.get_or_create(
                    source=batch.platform,
                    external_id=row.external_id,
                    is_active=True,
                    defaults={"candidate": candidate, "created_by": actor},
                )

            # A basis for THIS candidate, evidenced by the batch attestation.
            if not candidate.consent_records.filter(
                origin_batch=batch, withdrawn_at__isnull=True
            ).exists():
                record = retention.record_consent(
                    candidate=candidate,
                    basis=batch.legal_basis,
                    recorded_by=actor,
                    recorded_via="bulk_import",
                    evidence={
                        "attestation_text": batch.attestation_text,
                        "platform": batch.platform,
                        "platform_account_ref": batch.platform_account_ref,
                        "source_export_date": str(batch.source_export_date or ""),
                        "file_sha256": batch.file_sha256,
                    },
                )
                record.origin_batch = batch
                record.save(update_fields=["origin_batch", "updated_at"])

            # The application, in the SAME savepoint as the candidate.
            existing = Application.objects.filter(
                candidate=candidate, job_opening=job
            ).first()
            if existing is not None:
                if not existing.is_active:
                    # uniq_application_per_candidate_job has no is_active
                    # condition, so the row physically blocks re-creation.
                    # Silently resurrecting something someone deliberately
                    # removed is worse than saying so.
                    raise ValueError("application_soft_deleted")
                row.status = RowStatus.APPLICATION_EXISTS
                result.duplicate += 1
            else:
                intake.create_application(
                    actor=actor,
                    candidate=candidate,
                    job_opening=job,
                    first_stage=first_stage,
                )

            row.matched_candidate = candidate
            row.match_rule = outcome.rule
            row.warnings = outcome.warnings
            row.save(
                update_fields=[
                    "status", "matched_candidate", "match_rule", "warnings",
                ]
            )

    except TenancyError:
        # NOT one bad row. A tenancy fault is a programming error -- a query
        # with no organization bound, or a row whose organization disagrees
        # with its parent's -- and recording it as `row_failed` turns a bug
        # into a plausible-looking partial import that nobody investigates.
        # It cost real time here: flipping imports to a filtering manager made
        # half of every batch "fail" with no clue why.
        raise
    except Exception as exc:  # noqa: BLE001 — one bad row must cost one row
        # PII-free by construction: the code, never the value. This object is
        # what gets logged, audited and shipped to Sentry.
        code = str(exc) if str(exc) == "application_soft_deleted" else "row_failed"
        row.status = RowStatus.FAILED
        row.errors = [{"row": row.row_number, "code": code}]
        row.save(update_fields=["status", "errors"])
        result.failed += 1
        result.failures.append({"row": row.row_number, "code": code})
        logger.warning(
            "import.row_failed batch=%s row=%s code=%s", batch.pk, row.row_number, code
        )


def _first_code(errors) -> str:
    return errors[0].get("code", "invalid") if errors else "invalid"


def _result_from(batch: ImportBatch) -> CommitResult:
    return CommitResult(
        batch=batch,
        created=batch.rows_created,
        updated=batch.rows_updated,
        duplicate=batch.rows_duplicate,
        review=batch.rows_review,
        failed=batch.rows_failed,
    )


# ------------------------------------------------------- staged-row editing


def _assert_batch_editable(batch: ImportBatch) -> None:
    if batch.status != BatchStatus.PARSED:
        raise BusinessRuleError(
            "Rows can be edited only while the batch is staged. This one is "
            f"{batch.get_status_display().lower()}."
        )


def _reparse(batch: ImportBatch, raw: dict, row_number: int) -> dict:
    """
    An edited (or hand-added) row goes through the SAME assembly the file
    did at upload — header mapping, per-column parsers, name splitting — so
    an edit can never produce a row the commit path has not seen the likes of.
    """
    from apps.imports.platforms.registry import get_platform
    from apps.imports.services.parsing import _assemble, _clip

    spec = get_platform(batch.platform)
    if spec is None:
        raise BusinessRuleError("This batch's platform is no longer registered.")
    headers = list(batch.detected_headers or [])
    if not headers:
        raise BusinessRuleError("This batch recorded no column headers to edit against.")
    # The batch stores {canonical field: original header}; `_assemble` wants
    # {column index: canonical field}. Rebuild the index view from the headers.
    mapping = {}
    for canonical, header in (batch.column_mapping or {}).items():
        try:
            mapping[headers.index(header)] = canonical
        except ValueError:
            continue
    values = [_clip(raw.get(header, "")) for header in headers]
    return _assemble(mapping, headers, values, row_number, spec)


def _apply_raw(row: ImportRow, batch: ImportBatch, raw: dict) -> ImportRow:
    """Re-derive every stored field of `row` from the edited raw cells."""
    parsed = _reparse(batch, raw, row.row_number)
    fields = _normalise_row(parsed)
    for name, value in fields.items():
        setattr(row, name, value)
    row.raw = parsed.get("_raw", {})

    digest = fingerprint(
        f"{batch.platform}|{row.external_id}|{row.email_normalized}|{row.phone_e164}"
    )
    row.row_fingerprint = digest or ""

    twin = None
    if digest:
        twin = (
            ImportRow.objects.filter(batch=batch, row_fingerprint=digest)
            .exclude(pk=row.pk)
            .order_by("row_number")
            .first()
        )

    if not row.first_name:
        row.status = RowStatus.INVALID
        row.errors = [{"row": row.row_number, "code": "missing_name"}]
        row.warnings = []
        row.match_rule = ""
        row.matched_candidate = None
        row.duplicate_of_row = None
    elif twin is not None:
        row.status = RowStatus.DUPLICATE_IN_FILE
        row.duplicate_of_row = twin.row_number
        row.errors = []
        row.warnings = []
    else:
        outcome = dedup.resolve(row, platform=batch.platform)
        row.status = outcome.status
        row.match_rule = outcome.rule
        row.matched_candidate = outcome.candidate
        row.errors = outcome.errors
        row.warnings = outcome.warnings
        row.duplicate_of_row = None

    row.save()
    return row


@transaction.atomic
def update_row(*, actor, batch: ImportBatch, row_number: int, raw: dict) -> ImportRow:
    """Edit one staged row's cells; identity, dedup and validity re-resolve."""
    require(actor, Resource.CANDIDATE, Action.IMPORT)
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    _assert_batch_editable(batch)
    row = ImportRow.objects.filter(batch=batch, row_number=row_number).first()
    if row is None:
        raise BusinessRuleError(f"No staged row {row_number} in this batch.")
    row = _apply_raw(row, batch, raw)
    _refresh_counters(batch)
    _audit(batch, actor=actor, verb="import",
           extra={"phase": "row_edited", "row": row_number})
    return row


@transaction.atomic
def add_row(*, actor, batch: ImportBatch, raw: dict) -> ImportRow:
    """Hand-add a candidate row to the staged batch, as if the file had it."""
    require(actor, Resource.CANDIDATE, Action.IMPORT)
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    _assert_batch_editable(batch)
    last = (
        ImportRow.objects.filter(batch=batch).order_by("-row_number")
        .values_list("row_number", flat=True).first()
    )
    row = ImportRow(batch=batch, row_number=(last or 0) + 1)
    row = _apply_raw(row, batch, raw)
    _refresh_counters(batch)
    _audit(batch, actor=actor, verb="import",
           extra={"phase": "row_added", "row": row.row_number})
    return row


@transaction.atomic
def remove_row(*, actor, batch: ImportBatch, row_number: int) -> None:
    """Drop a staged row before commit. Staging only — never a candidate."""
    require(actor, Resource.CANDIDATE, Action.IMPORT)
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    _assert_batch_editable(batch)
    deleted, _ = ImportRow.objects.filter(batch=batch, row_number=row_number).delete()
    if not deleted:
        raise BusinessRuleError(f"No staged row {row_number} in this batch.")
    _refresh_counters(batch)
    _audit(batch, actor=actor, verb="import",
           extra={"phase": "row_removed", "row": row_number})
