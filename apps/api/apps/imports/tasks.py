"""Scheduled staging-PII purge."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("hrms.audit")


@shared_task(name="imports.purge_staging_pii")
def purge_staging_pii_task(apply: bool = True) -> dict:
    """
    Strip personal data from expired import staging rows.

    Runs with `apply=True`: a scheduled dry run is a log line nobody reads. The
    dry run belongs to the management command, where a human is deciding.

    Logs counts only. This task exists to destroy personal data; writing any of
    it to the log on the way past would defeat the point.
    """
    from apps.imports.services.retention import purge_staging_pii

    result = purge_staging_pii(apply=apply)
    logger.info(
        "imports.staging_purge uncommitted=%s committed=%s batches=%s apply=%s",
        result.uncommitted_rows, result.committed_rows, result.batches, apply,
    )
    return {
        "uncommitted_rows": result.uncommitted_rows,
        "committed_rows": result.committed_rows,
        "batches": result.batches,
    }
