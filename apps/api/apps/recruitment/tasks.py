"""
Scheduled recruitment work.

Each beat row names a dispatcher, which queues one subtask per running
organization. See `core/tasks.py` for the rule and the reasoning.

The Google Forms sync goes one level further and queues a subtask per JOB.
That is not symmetry for its own sake: the sync talks to an external API per
job, and a single job whose form has been deleted or whose token has expired
would otherwise abort the loop and leave every later job in that organization
unsynced until somebody read a worker log.
"""

from __future__ import annotations

import logging

from celery import shared_task

from core.tasks import fan_out, organization_task

logger = logging.getLogger("hrms.audit")


# ------------------------------------------------------------- retention


@organization_task(name="recruitment.purge_expired_candidates.for_organization")
def purge_expired_candidates_for_organization(organization, apply: bool = True) -> dict:
    """
    Anonymise one organization's candidate data whose retention has run out.

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
        "recruitment.retention_purge organization=%s considered=%s anonymised=%s apply=%s",
        organization.pk, result.considered, result.count, apply,
    )
    return {"considered": result.considered, "anonymised": result.count}


@shared_task(name="recruitment.purge_expired_candidates")
def purge_expired_candidates_task(apply: bool = True) -> dict:
    return fan_out(purge_expired_candidates_for_organization, apply=apply)


# ---------------------------------------------------------- external forms


@organization_task(name="recruitment.sync_google_form_responses.for_job")
def sync_google_form_responses_for_job(organization, job_id) -> dict:
    """
    Pull new responses for ONE job opening.

    The job is re-read through the tenant manager with `organization` bound, so
    an id belonging to a different customer does not resolve and this raises.
    That is the intended behaviour and the thing worth testing: a task handed
    the wrong tenant's object must FAIL rather than quietly find nothing, which
    is indistinguishable from "no new responses".
    """
    from apps.recruitment.models import JobOpening
    from apps.recruitment.services.external_forms import (
        provider_for_job,
        sync_responses,
    )

    job = JobOpening.objects.get(pk=job_id)
    result = sync_responses(job, provider=provider_for_job())
    logger.info(
        "recruitment.google_forms_job organization=%s job=%s %s",
        organization.pk, job_id, result,
    )
    return {key: int(result.get(key, 0) or 0) for key in ("seen", "created", "duplicate", "refused")}


@organization_task(name="recruitment.sync_google_form_responses.for_organization")
def sync_google_form_responses_for_organization(organization) -> dict:
    """Queue one per-job sync for each of this organization's linked jobs."""
    from apps.recruitment.models import JobOpening, JobStatus
    from apps.recruitment.services.external_forms import google_forms_enabled

    # Still a deployment-level switch: the Google credentials have not been
    # made per-organization yet, unlike mail and the attendance devices. Until
    # they are, an installation either has the integration or does not.
    if not google_forms_enabled():
        return {"skipped": True}

    job_ids = [
        str(pk)
        for pk in JobOpening.objects.filter(
            is_active=True,
            status=JobStatus.PUBLISHED,
            external_form_provider="google_forms",
        )
        .exclude(external_form_id="")
        .values_list("pk", flat=True)
    ]
    for job_id in job_ids:
        sync_google_form_responses_for_job.delay(organization.pk, job_id)

    return {"dispatched": len(job_ids)}


@shared_task(name="recruitment.sync_google_form_responses")
def sync_google_form_responses_task() -> dict:
    return fan_out(sync_google_form_responses_for_organization)
