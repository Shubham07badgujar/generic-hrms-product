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


@organization_task(name="imports.send_import_welcome")
def send_import_welcome_emails(organization, batch_id) -> dict:
    """
    One message per person hired by an import, over ONE connection.

    ON DEMAND, not on a schedule. Queued by `commit_employee_batch` after its
    transaction commits -- after, because the commit still holds the
    employee-code counter's lock and an SMTP conversation is not something to
    do while other hires are waiting on it.

    WHY THE PASSWORDS ARE MADE HERE. `create_employee` normally schedules one
    welcome email per hire, each opening its own connection bounded by
    EMAIL_TIMEOUT: two hundred hires is up to fifty minutes of blocked worker.
    The import therefore creates everyone with `send_welcome_email=False` and
    hands this task the BATCH -- not the credentials. A temporary password is
    held in memory and stored nowhere, so passing two hundred of them as task
    arguments would write live credentials into the broker, where they would
    sit in Redis and in anything that inspects a queue. Generating each one at
    send time is what `reissue_credentials` already does for a single person.

    Failures are per person and reported, never raised: one bad address must
    not cost the other hundred and ninety-nine their credentials, and the
    accounts exist either way.
    """
    from django.core import mail

    from apps.accounts.services.passwords import (
        generate_temporary_password,
        send_account_created_email,
    )
    from apps.imports.models import ImportBatch, ImportRow
    from core.config import email_config

    batch = ImportBatch.objects.filter(pk=batch_id).first()
    if batch is None:
        # Queued for a batch that no longer resolves in this organization.
        # Nothing to send, and nothing to raise about: the alternative is a
        # task that retries forever against a row somebody deleted.
        logger.info(
            "imports.welcome_batch_missing organization=%s batch=%s",
            organization.pk, batch_id,
        )
        return {"sent": 0, "failed": 0}

    rows = (
        ImportRow.objects.filter(batch=batch, created_employee__isnull=False)
        .select_related("created_employee", "created_employee__user")
        .order_by("row_number")
    )

    sent = failed = 0
    # `connection()` answers None when this organization has configured no mail
    # of its own, meaning "use whatever the deployment configured". For a single
    # send that is exactly right; here the whole point is to open ONE, so the
    # deployment default is built explicitly rather than left to each message.
    connection = email_config(organization).connection() or mail.get_connection()
    try:
        connection.open()
        for row in rows:
            employee = row.created_employee
            user = getattr(employee, "user", None)
            if user is None:
                continue
            password = generate_temporary_password()
            user.set_password(password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])
            delivered = send_account_created_email(
                user=user,
                temporary_password=password,
                employee=employee,
                role=None,
                actor=batch.uploaded_by,
                connection=connection,
            )
            sent += int(bool(delivered))
            failed += int(not delivered)
    finally:
        connection.close()

    # Counts only: this task exists to deliver credentials, and writing any of
    # them -- or the addresses they went to -- into a log would defeat it.
    logger.info(
        "imports.welcome_sent organization=%s batch=%s sent=%s failed=%s",
        organization.pk, batch.pk, sent, failed,
    )
    return {"sent": sent, "failed": failed}
