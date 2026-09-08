"""
Candidate and application intake — the one way either comes into existence.

WHY THIS MODULE EXISTS
----------------------
Until now, an Application was created in exactly one place: three lines inside
`ApplicationViewSet.perform_create`. No transaction, no journal entry, no guard
against a workflow with no stages. Candidate creation was likewise a single
`serializer.save()` in a viewset.

That was survivable while HTTP was the only way in. It stops being survivable
the moment a bulk importer wants to create the same things, because the second
path would inevitably drift from the first — and, as `hiring.py` puts it about
`create_employee`, the bulk one is always the weaker. So the viewsets now call
these functions too, and there is one implementation to get right.

WHAT THE CALLER MUST STILL DO
-----------------------------
Nothing here decides *which* candidate or job the caller meant. It authorises
the actor against the resource, resolves the job opening against the actor's
scope, and refuses anything the pipeline cannot represent. Row-level questions
— is this a duplicate, should it enrich an existing record — belong to the
importer, which is why they are not here.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.recruitment.access import resolve_job_opening
from apps.recruitment.services import communications as comms
from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    Candidate,
    ConsentRecord,
    LegalBasis,
)
from core.access import Action, Resource, require


class IntakeError(ValidationError):
    """A refused candidate or application intake."""


def first_stage_or_refuse(job_opening):
    """
    The stage a new application starts at, or a clean refusal.

    `HiringWorkflow.first_stage` returns None for a workflow with no active
    stages, and `Application.current_stage` is NOT NULL — so the unguarded path
    is an IntegrityError from deep inside the ORM, which in a 500-row import
    means a stack trace instead of a row number. `HiringWorkflow.clean()` guards
    this at publish time only, and only when the workflow already has a pk.

    Returned rather than looked up per row so a bulk caller can hoist it: it is
    a property backed by a query, so calling it per application is an N+1.
    """
    stage = job_opening.workflow.first_stage
    if stage is None:
        raise IntakeError(
            {
                "job_opening": (
                    f"'{job_opening.workflow.name}' has no stages, so an "
                    f"application cannot enter it."
                )
            }
        )
    return stage


@transaction.atomic
def create_candidate(*, actor, **fields) -> Candidate:
    """
    Record a candidate.

    `created_by` is set explicitly rather than left to `BaseModel.save()`, which
    reads a thread-local bound by request middleware. A management command or a
    Celery task has no request, so trusting the middleware would silently drop
    attribution on exactly the bulk paths that most need it.
    """
    require(actor, Resource.CANDIDATE, Action.CREATE)

    fields.setdefault("created_by", actor)
    return Candidate.objects.create(**fields)


@transaction.atomic
def affirm_consent(*, actor, candidate, via: str = "candidate_reply") -> Candidate:
    """
    Upgrade a candidate from a legitimate-use basis to actual consent.

    This is the moment s.7(a) becomes s.6: someone we sourced from a platform
    replied, applied, or ticked the box, and now genuinely consents to us
    holding their data. It is the ONLY path that may set `consent_given=True`
    on a candidate the organisation approached, and it exists so that "they
    consented" is never something an import can assert on a person's behalf.

    Stamps the live ledger record too, so the upgrade is evidenced and dated
    rather than inferred from the boolean having changed.
    """
    require(actor, Resource.CANDIDATE, Action.EDIT)

    now = timezone.now()
    candidate.consent_given = True
    candidate.consent_at = now
    candidate.legal_basis = LegalBasis.CONSENT
    # Notice is no longer owed: they are talking to us of their own accord.
    candidate.notice_due_at = None
    candidate.save(
        update_fields=[
            "consent_given", "consent_at", "legal_basis", "notice_due_at", "updated_at"
        ]
    )

    live = candidate.consent_records.filter(withdrawn_at__isnull=True).first()
    if live is not None and live.affirmed_at is None:
        live.affirmed_at = now
        live.save(update_fields=["affirmed_at", "updated_at"])
    else:
        ConsentRecord.objects.create(
            candidate=candidate,
            basis=LegalBasis.CONSENT,
            recorded_by=actor,
            recorded_via=via,
            affirmed_at=now,
            created_by=actor,
        )

    return candidate


@transaction.atomic
def create_application(*, actor, candidate, job_opening, first_stage=None) -> Application:
    """
    Place a candidate into a job's hiring pipeline at its first stage.

    Ordering is deliberate: every check that can fail runs before the first
    write, so the common rejection path never touches the database.

    `first_stage` may be passed by a caller that has already resolved it for
    this job — a bulk import placing 500 rows into one opening should not
    re-run that query 500 times.
    """
    require(actor, Resource.APPLICATION, Action.CREATE)

    # Scope check on the specific job, using the same narrowing that filters the
    # job list. Raises Http404 rather than 403 — see apps.recruitment.access.
    job_opening = resolve_job_opening(job_opening, user=actor)

    if not job_opening.workflow.is_published:
        raise IntakeError(
            {"job_opening": f"'{job_opening.workflow.name}' is a draft workflow."}
        )

    stage = first_stage or first_stage_or_refuse(job_opening)
    return place_application(
        candidate=candidate,
        job_opening=job_opening,
        stage=stage,
        actor=actor,
        actor_label=getattr(actor, "email", "") or "",
    )


def place_application(
    *, candidate, job_opening, stage, actor, actor_label: str, form_answers: dict | None = None
) -> Application:
    """
    The write side of an application: row, opening journal entry, and the
    "application received" email. Shared by the authorised path above and by
    the public application form, which has a token where the other has a
    user — so the two doors lead into the same room, and there is exactly one
    place an application comes into being.

    Not itself authorised: the caller has already established its right to
    place a candidate into this job (via `require()` and `resolve_job_opening`
    for a user; via a valid token on a published job for the public form).
    """
    application = Application.objects.create(
        candidate=candidate,
        job_opening=job_opening,
        current_stage=stage,
        created_by=actor,
        form_answers=form_answers or {},
    )

    # The journal's opening entry. `Kind.APPLIED` has existed since the model
    # was written and was never once recorded, so every candidate's history
    # began mid-story.
    ApplicationEvent.objects.create(
        application=application,
        kind=ApplicationEvent.Kind.APPLIED,
        to_stage=stage,
        actor=actor,
        actor_label=actor_label,
        created_by=actor,
    )

    comms.notify_candidate(
        application=application,
        kind=comms.Kind.APPLICATION_RECEIVED,
        dedupe_key=f"app:{application.pk}:received",
        actor=actor,
    )

    return application
