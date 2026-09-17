"""
Running scheduled work for one organization at a time.

THE RULE
--------
**Every Celery task that operates on organization-owned data takes an
`organization_id` as its first argument, and independently re-resolves and
validates that organization before querying anything.**

Celery serialises ARGUMENTS, not context variables. A task that assumed it had
inherited a tenant would run under whatever the previous task on that worker
happened to leave behind — the same "correct only if something else ran first"
shape as HRMS-INC-20260717-01, on a worker instead of a request. So the tenant
arrives as data, is re-read from the database, and is bound for the duration of
the call and no longer.

The id is re-resolved rather than trusted as an object graph. A serialised
object would be a snapshot: a customer suspended between dispatch and execution
would still look operational, and the work would run for a tenant that is
supposed to be stopped.

FAN-OUT, NOT A SCHEDULER ROW PER CUSTOMER
-----------------------------------------
Each scheduled job is a DISPATCHER holding the beat row's task name, which
queues one subtask per running organization. Three reasons, in order of how
much they matter:

1. One customer's unreachable device server is one failed subtask with its own
   retry, rather than a loop that dies at organization three and silently
   leaves the rest of the alphabet unsynced.
2. Creating a customer stays a pure database operation. A row per organization
   in the scheduler would make provisioning write to `django_celery_beat`, and
   a failure there would leave a tenant whose leave never accrues.
3. Suspension needs no scheduler change at all: a non-running organization is
   simply not dispatched to, and its work resumes on restore with nothing
   destroyed in the meantime.

ATTRIBUTION
-----------
Scheduled work binds `acting_as(None, organization=...)`, so audit rows carry
the organization and no actor. That is deliberate. The alternative — a system
User to attribute automated writes to — is a login that exists, with a row in
the user table and a place in every "who can sign in" answer, bought for a
cosmetic improvement to an audit column. "Nobody, on a schedule" is the honest
value, and the task name is in the worker log beside it.
"""

from __future__ import annotations

import functools
import logging

from celery import shared_task

logger = logging.getLogger("hrms.tasks")


class UnknownOrganization(Exception):
    """A task was handed an organization id that does not exist."""


#: Name suffixes that mark a task as fanned-out work rather than a scheduled
#: job. The meta-test in `tests/core/test_beat_schedule.py` reads them: a task
#: ending in one of these is allowed to be absent from the beat schedule, but
#: only if the dispatcher it hangs off IS scheduled. That pairing is what stops
#: a fan-out subtask from quietly becoming a task nothing ever calls.
#:
#: `.for_job` is the second level: one customer's external-form sync queues one
#: of those per job opening, so a single deleted form does not abort the rest.
PER_ORGANIZATION_SUFFIXES = (".for_organization", ".for_job")


def running_organizations():
    """
    The organizations scheduled work should run for, newest last.

    "Running" is `Organization.is_operational` — pending setup, trial and
    active. A suspended, cancelled or archived customer is skipped rather than
    failed: nothing is destroyed, no row is queued, and a restore resumes the
    schedule with no catch-up to arrange.
    """
    from apps.organization.models import OPERATIONAL_STATUSES, Organization

    return Organization.objects.filter(status__in=OPERATIONAL_STATUSES).order_by(
        "created_at"
    )


def fan_out(subtask, **kwargs) -> dict:
    """
    Queue one `subtask` per running organization. The body of every dispatcher.

    Returns a summary rather than results: the subtasks run independently, and
    a dispatcher that waited on them would turn N customers' work into one
    failure domain, which is the thing this shape exists to avoid.
    """
    organization_ids = [str(pk) for pk in running_organizations().values_list("pk", flat=True)]
    for organization_id in organization_ids:
        subtask.delay(organization_id, **kwargs)

    logger.info(
        "tasks.dispatched task=%s organizations=%s",
        getattr(subtask, "name", subtask), len(organization_ids),
    )
    return {"dispatched": len(organization_ids), "organizations": organization_ids}


def resolve_organization(organization_id):
    """
    Re-read one organization from the database, or refuse.

    Raises rather than returning None. A task that silently did nothing when
    handed an id it could not resolve would look exactly like a task with no
    work to do — and "looks like success" is how a tenant stops being processed
    for a month without anybody noticing.
    """
    from apps.organization.models import Organization

    organization = Organization.objects.filter(pk=organization_id).first()
    if organization is None:
        raise UnknownOrganization(
            f"No organization with id {organization_id!r}. A scheduled task was "
            f"queued for a tenant that no longer exists, or was handed an id "
            f"from somewhere other than the dispatcher."
        )
    return organization


def organization_task(name: str):
    """
    Declare a task that runs for exactly one organization.

    The decorated function is called as `fn(organization, ...)` with that
    organization bound as the acting tenant, so every queryset inside it is
    scoped by the fail-closed manager without the body having to say so.

    Wrapping rather than leaving each task to do this by hand is the point: the
    re-resolution, the running check and the binding are three steps, and a
    task that skipped the third would still pass its own happy-path test while
    querying whatever the worker last bound.
    """

    def decorate(fn):
        @shared_task(name=name)
        @functools.wraps(fn)
        def run(organization_id, *args, **kwargs):
            from core.middleware import acting_as

            organization = resolve_organization(organization_id)
            if not organization.is_operational:
                # A race, not an error: the customer was suspended between
                # dispatch and execution. Skipping resumes cleanly on restore.
                logger.info(
                    "tasks.skipped_not_running task=%s organization=%s status=%s",
                    name, organization_id, organization.status,
                )
                return {"skipped": True, "reason": "not running"}

            with acting_as(None, organization=organization):
                return fn(organization, *args, **kwargs)

        return run

    return decorate
