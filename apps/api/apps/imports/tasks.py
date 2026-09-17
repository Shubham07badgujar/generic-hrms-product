"""
Scheduled staging-PII purge.

The plan for this slice said this one task could stay global -- a
data-minimisation sweep on a fixed clock, with no per-organization
configuration to resolve. It fans out anyway, for two reasons that only became
visible with the code in front of us.

`purge_staging_pii` queries `ImportRow` and `ImportBatch` through the
fail-closed manager. A genuinely global sweep would therefore need an explicit
all-organizations escape hatch, and would run one customer's deletion inside
the same transaction as another's. And the audit rows it writes would belong to
no organization, so "what happened to that customer's import data" would have
no tenant to filter by -- which is the question the audit trail exists to
answer.

Fanning out costs one queue message per customer per night and keeps every
write stamped. That is a better trade than the escape hatch.
"""

from __future__ import annotations

import logging

from celery import shared_task

from core.tasks import fan_out, organization_task

logger = logging.getLogger("hrms.audit")


@organization_task(name="imports.purge_staging_pii.for_organization")
def purge_staging_pii_for_organization(organization, apply: bool = True) -> dict:
    """
    Strip personal data from one organization's expired staging rows.

    Runs with `apply=True`: a scheduled dry run is a log line nobody reads. The
    dry run belongs to the management command, where a human is deciding.

    Logs counts only. This task exists to destroy personal data; writing any of
    it to the log on the way past would defeat the point.
    """
    from apps.imports.services.retention import purge_staging_pii

    result = purge_staging_pii(apply=apply)
    logger.info(
        "imports.staging_purge organization=%s uncommitted=%s committed=%s "
        "batches=%s apply=%s",
        organization.pk, result.uncommitted_rows, result.committed_rows,
        result.batches, apply,
    )
    return {
        "uncommitted_rows": result.uncommitted_rows,
        "committed_rows": result.committed_rows,
        "batches": result.batches,
    }


@shared_task(name="imports.purge_staging_pii")
def purge_staging_pii_task(apply: bool = True) -> dict:
    return fan_out(purge_staging_pii_for_organization, apply=apply)
