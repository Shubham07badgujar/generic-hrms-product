"""
Scheduled recruitment work.

The first `tasks.py` in this project. Retention is the thing that finally
needed one: a policy that only runs when somebody remembers to run it is not a
policy, and `Candidate.retention_until` had been written by the engine and read
by nothing since it was added.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("hrms.audit")


@shared_task(name="recruitment.purge_expired_candidates")
def purge_expired_candidates_task(apply: bool = True) -> dict:
    """
    Anonymise candidate personal data whose retention has run out.

    Runs with `apply=True` because a scheduled dry run is a log line nobody
    reads. The dry run belongs to the management command, where a human is
    deciding whether to proceed.

    Logs counts and identifiers only — never a name, address or phone. This
    task exists to destroy personal data, and writing it to the log on the way
    past would defeat the point.
    """
    from apps.recruitment.services.retention import purge_expired_candidates

    result = purge_expired_candidates(apply=apply)
    logger.info(
        "recruitment.retention_purge considered=%s anonymised=%s apply=%s",
        result.considered,
        result.count,
        apply,
    )
    return {"considered": result.considered, "anonymised": result.count}


@shared_task(name="recruitment.sync_google_form_responses")
def sync_google_form_responses_task() -> dict:
    """
    Pull new Google Form responses for every published job that has one.

    Each response goes through the same public intake as the hosted form, so
    running this twice is harmless. Does nothing at all when Google Forms is
    not configured, which is the default.
    """
    from apps.recruitment.models import JobOpening, JobStatus
    from apps.recruitment.services.external_forms import (
        google_forms_enabled,
        provider_for_job,
        sync_responses,
    )

    if not google_forms_enabled():
        return {"skipped": True}
    provider = provider_for_job()
    totals = {"jobs": 0, "seen": 0, "created": 0, "duplicate": 0, "refused": 0}
    for job in JobOpening.objects.filter(
        is_active=True, status=JobStatus.PUBLISHED, external_form_provider="google_forms"
    ).exclude(external_form_id=""):
        result = sync_responses(job, provider=provider)
        totals["jobs"] += 1
        for key in ("seen", "created", "duplicate", "refused"):
            totals[key] += int(result.get(key, 0) or 0)
    logger.info("recruitment.google_forms_sync %s", totals)
    return totals
