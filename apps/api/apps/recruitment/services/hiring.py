"""
Offers, the administrative override, and candidate-to-employee conversion.

Conversion reuses `apps.employees.services.creation.create_employee` rather
than writing its own — so a hire made through recruitment is subject to exactly
the same hierarchy rules, department checks, creator-seniority limits and
atomicity as one created by hand. Two creation paths would inevitably drift,
and the recruitment one would be the weaker.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    DecisionOverride,
    Offer,
    OfferStatus,
)
from core.access import Action, Resource, require
from . import communications as comms

from .engine import (
    CLOSED_STATUSES,
    MIN_REASON_LENGTH,
    _clear_final_decision,
    _event,
    _stamp_final_decision,
    align_stage_with_status,
)


@transaction.atomic
def create_offer(
    *,
    application: Application,
    actor,
    offered_ctc,
    joining_date,
    designation=None,
    level=None,
    reporting_manager=None,
    valid_until=None,
) -> Offer:
    """An offer may only follow HR Head's SELECT decision."""
    require(actor, Resource.OFFER, Action.CREATE)

    if application.status != ApplicationStatus.SELECTED:
        raise ValidationError(
            {
                "application": (
                    f"An offer requires the candidate to be selected first. This "
                    f"application is '{application.get_status_display()}'."
                )
            }
        )
    if hasattr(application, "offer"):
        raise ValidationError({"application": "An offer already exists for this application."})

    offer = Offer.objects.create(
        application=application,
        offered_ctc=offered_ctc,
        joining_date=joining_date,
        designation=designation or application.job_opening.designation,
        level=level or application.job_opening.level,
        reporting_manager=reporting_manager,
        valid_until=valid_until,
        created_by=actor,
    )
    _event(
        application,
        kind=ApplicationEvent.Kind.OFFER_CREATED,
        actor=actor,
        note=f"CTC {offered_ctc}, joining {joining_date}",
    )
    return offer


@transaction.atomic
def send_offer(*, offer: Offer, actor) -> Offer:
    require(actor, Resource.OFFER, Action.EDIT)

    if offer.status != OfferStatus.DRAFT:
        raise ValidationError({"offer": f"Only a draft offer can be sent; this is '{offer.status}'."})

    offer.status = OfferStatus.SENT
    offer.sent_at = timezone.now()
    offer.save(update_fields=["status", "sent_at", "updated_at"])

    # Generate and FREEZE the letter now: the candidate must forever hold the
    # letter this send produced, untouched by later letterhead or signatory
    # edits. The email pipeline attaches this stored file.
    from django.core.files.base import ContentFile

    from apps.recruitment.letters import offer_letter_pdf

    content, filename = offer_letter_pdf(offer)
    offer.letter_pdf.save(filename, ContentFile(content), save=True)

    offer.application.status = ApplicationStatus.OFFER_SENT
    offer.application.save(update_fields=["status", "updated_at"])

    _event(offer.application, kind=ApplicationEvent.Kind.OFFER_SENT, actor=actor)
    comms.notify_candidate(
        application=offer.application,
        kind=comms.Kind.OFFER_SENT,
        dedupe_key=f"offer:{offer.pk}:sent",
        extra_context=comms.offer_context(offer),
        actor=actor,
    )
    return offer


@transaction.atomic
def record_offer_response(*, offer: Offer, actor, accepted: bool, note: str = "") -> Offer:
    """Record the candidate's answer. HR records it on their behalf."""
    require(actor, Resource.OFFER, Action.EDIT)

    if offer.status != OfferStatus.SENT:
        raise ValidationError(
            {"offer": f"Only a sent offer can be responded to; this is '{offer.status}'."}
        )

    offer.status = OfferStatus.ACCEPTED if accepted else OfferStatus.DECLINED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at", "updated_at"])

    application = offer.application
    application.status = (
        ApplicationStatus.OFFER_ACCEPTED if accepted else ApplicationStatus.OFFER_DECLINED
    )
    application.save(update_fields=["status", "updated_at"])

    if not accepted:
        # A declined offer is a final decision for retention purposes: the
        # candidacy is over, so the DPDP clock starts.
        _stamp_final_decision(application.candidate)

    _event(
        application,
        kind=ApplicationEvent.Kind.OFFER_RESPONDED,
        actor=actor,
        decision="offer_accepted" if accepted else "offer_declined",
        note=note,
    )
    comms.notify_candidate(
        application=application,
        kind=comms.Kind.OFFER_ACCEPTED if accepted else comms.Kind.OFFER_DECLINED,
        dedupe_key=f"offer:{offer.pk}:{'accepted' if accepted else 'declined'}",
        extra_context=comms.offer_context(offer),
        actor=actor,
    )
    return offer


def _employment_type_for(job) -> str:
    """
    Job postings write the employment type freehand ('internship', 'Full
    Time'); the Employee record takes the closed enum. Normalise the synonyms
    rather than refusing a hire over spelling — an unrecognised value falls
    back to full-time, which HR can correct on the employee record.
    """
    from apps.employees.models import EmploymentType

    raw = (job.employment_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "internship": EmploymentType.INTERN,
        "intern": EmploymentType.INTERN,
        "trainee": EmploymentType.INTERN,
        "fulltime": EmploymentType.FULL_TIME,
        "full_time": EmploymentType.FULL_TIME,
        "permanent": EmploymentType.FULL_TIME,
        "parttime": EmploymentType.PART_TIME,
        "part_time": EmploymentType.PART_TIME,
        "contract": EmploymentType.CONTRACT,
        "contractor": EmploymentType.CONTRACT,
        "consultant": EmploymentType.CONSULTANT,
    }
    return aliases.get(raw, EmploymentType.FULL_TIME)


@transaction.atomic
def convert_to_employee(
    *,
    application: Application,
    actor,
    department=None,
    reporting_manager=None,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    personal_email: str | None = None,
    phone: str | None = None,
    designation=None,
    location=None,
    date_of_joining=None,
    temporary_password: str | None = None,
    **overrides,
):
    """
    Candidate → Employee, atomically.

    Delegates entirely to `create_employee`, so every hierarchy rule applies:
    the target role must suit the department, the reporting manager must be
    senior enough and in a permitted department, the designation must belong
    to the department, the creator may not mint someone above their own
    authority, and person + login + role commit or roll back together.

    The keyword overrides are the onboarding form: HR Head verifies the
    candidate's identity and placement before the account is minted. Anything
    left out falls back to the candidate, the job opening and the offer. The
    role and level are NOT overridable here — they come from the job's target
    role and the offer, so the hierarchy pairing is decided when the job is
    authored, not re-argued at the final step.
    """
    from apps.employees.services.creation import create_employee

    if application.status != ApplicationStatus.OFFER_ACCEPTED:
        raise ValidationError(
            {
                "application": (
                    f"Conversion requires an accepted offer. This application is "
                    f"'{application.get_status_display()}'."
                )
            }
        )

    offer = getattr(application, "offer", None)
    if offer is None:
        raise ValidationError({"application": "No offer exists for this application."})

    candidate = application.candidate
    job = application.job_opening

    # An email address is the login identifier, so an employee cannot exist
    # without one. Candidates can: a WorkIndia import carries phone-only rows,
    # and `Candidate.email` is nullable to avoid inventing addresses for them.
    #
    # Checked here rather than left to fail downstream, because
    # `UserManager._create_user` raises ValueError — not ValidationError — on an
    # empty email, which surfaces as a 500 instead of a message telling HR to
    # collect an address before hiring this person.
    work_email = (email or "").strip() or candidate.email
    if not work_email:
        raise ValidationError(
            {
                "candidate": (
                    f"{candidate.full_name} has no email address. An employee "
                    f"account is created from it, so record one on the "
                    f"candidate before converting."
                )
            }
        )

    result = create_employee(
        actor=actor,
        first_name=(first_name or "").strip() or candidate.first_name,
        last_name=(last_name if last_name is not None else candidate.last_name) or "",
        email=work_email,
        # The address the candidate applied with is their personal one; the
        # credential email goes there, and it can never log in.
        personal_email=(personal_email or "").strip() or (candidate.email or ""),
        phone=(phone if phone is not None else candidate.phone) or "",
        role_code=job.target_role.code,
        department_id=(department or job.department).pk,
        designation_id=(
            designation.pk if designation else (offer.designation_id or job.designation_id)
        ),
        # A job opening may carry no designation, and refusing here would
        # strand a candidate who has already accepted an offer. The title is
        # filled in on the employee record afterwards.
        require_designation=False,
        location_id=location.pk if location else job.location_id,
        # Seniority level is deliberately NOT set during onboarding: the field
        # was removed from the flow, `Employee.level` is nullable, and a
        # silently inherited job/offer level could fail the role–level
        # hierarchy check for a hire that is otherwise complete.
        reporting_manager_id=(
            reporting_manager.pk if reporting_manager else offer.reporting_manager_id
        ),
        date_of_joining=date_of_joining or offer.joining_date,
        employment_type=_employment_type_for(job),
        # The agreed pay travels with the hire: the accepted offer's CTC is
        # recorded on the employee (informational — payroll structures stay
        # Finance's authority).
        annual_ctc=offer.offered_ctc,
        temporary_password=temporary_password,
        **overrides,
    )

    # The deferred link, now that Candidate exists. Also what makes this
    # candidate permanently ineligible for the DPDP retention purge.
    result.employee.created_from_candidate = candidate
    result.employee.save(update_fields=["created_from_candidate", "updated_at"])

    application.status = ApplicationStatus.HIRED
    application.save(update_fields=["status", "updated_at"])

    _stamp_final_decision(candidate)

    _event(
        application,
        kind=ApplicationEvent.Kind.CONVERTED,
        actor=actor,
        note=f"Converted to employee {result.employee.employee_code}",
        detail={
            "employee_id": str(result.employee.pk),
            "employee_code": result.employee.employee_code,
            "role": result.role.code,
        },
    )
    return result


@transaction.atomic
def override_decision(
    *, application: Application, actor, new_status: str, reason: str
) -> DecisionOverride:
    """
    The Admin exceptional override.

    Deliberately NOT a rejection path. Admin does not hold CANDIDATE/REJECT and
    cannot reach `record_decision`; this is a separate action, on a separate
    permission, writing a separate record, so an override is never mistakable
    for a normal HR decision in the history.
    """
    require(actor, Resource.APPLICATION, Action.OVERRIDE)

    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise ValidationError(
            {
                "reason": (
                    f"An administrative override requires a reason of at least "
                    f"{MIN_REASON_LENGTH} characters. It is permanently recorded and "
                    f"reported to oversight."
                )
            }
        )

    if new_status not in ApplicationStatus.values:
        raise ValidationError({"new_status": f"Unknown status '{new_status}'."})

    # Lock the row and pull the workflow context the stage resolver needs.
    application = (
        Application.objects.select_for_update()
        .select_related("current_stage", "job_opening__workflow", "candidate")
        .get(pk=application.pk)
    )

    previous_status = application.status
    previous_stage = application.current_stage

    # THE STAGE MOVE IS THE ENGINE'S DECISION, NOT THIS FUNCTION'S.
    #
    # Reopening a rejected candidate has to put them somewhere they can
    # actually be worked on, and where that is depends entirely on the
    # configured workflow. Resolving it here would mean hard-coding a stage and
    # would break the moment a pipeline with different stages was added — so
    # the engine derives it from the transition table and from what it recorded
    # at the time of the rejection. An unreachable configuration raises rather
    # than guessing.
    new_stage = align_stage_with_status(application, new_status=new_status)

    override = DecisionOverride.objects.create(
        application=application,
        overridden_by=actor,
        previous_status=previous_status,
        new_status=new_status,
        previous_stage=previous_stage,
        new_stage=new_stage,
        reason=reason.strip(),
    )

    application.status = new_status
    application.current_stage = new_stage
    application.save(update_fields=["status", "current_stage", "updated_at"])

    # A rejection is "overridden" when the override moves the application OFF
    # rejected — not when it moves it onto rejected. The rejection row is
    # flagged rather than edited or removed: HR's decision, their reason, the
    # actor and the timestamp all stay on the record permanently, next to the
    # fact that an Admin later set it aside.
    reopened = (
        previous_status in CLOSED_STATUSES and new_status not in CLOSED_STATUSES
    )
    if reopened:
        rejection = getattr(application, "rejection", None)
        if rejection is not None:
            rejection.is_overridden = True
            rejection.save(update_fields=["is_overridden", "updated_at"])
        # Back in a live pipeline, so the retention countdown stops.
        _clear_final_decision(application.candidate)

    _event(
        application,
        kind=ApplicationEvent.Kind.OVERRIDE,
        actor=actor,
        from_stage=previous_stage,
        to_stage=new_stage,
        note=reason.strip(),
        detail={
            "previous_status": previous_status,
            "new_status": new_status,
            "previous_stage": previous_stage.name if previous_stage else None,
            "new_stage": new_stage.name if new_stage else None,
            "reopened": reopened,
        },
    )

    _audit_override(override, actor)
    return override


def _audit_override(override, actor) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.OVERRIDE,
        resource=Resource.APPLICATION,
        entity_type="recruitment.Application",
        entity_id=str(override.application_id),
        entity_label=str(override.application),
        # Split across before/after rather than describing both inside `after`.
        # The audit viewer renders a previous/new column pair, and an override
        # is the record a reviewer opens first — putting the prior state in
        # `after` left the "Previous" column blank on exactly the event that
        # most needs one.
        before={
            "status": override.previous_status,
            # The stage move is part of what was done: "reopened to HR Head
            # final decision" is the fact an auditor needs, and status alone
            # does not carry it.
            "stage": override.previous_stage.name if override.previous_stage else None,
        },
        after={
            "event": "administrative_override",
            "status": override.new_status,
            "stage": override.new_stage.name if override.new_stage else None,
            "reason": override.reason,
        },
        # Lifted onto its own column so the viewer can surface it without
        # knowing this writer's payload shape.
        reason=override.reason,
        request_id=get_request_id(),
    )
