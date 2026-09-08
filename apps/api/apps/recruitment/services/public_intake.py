"""
A candidate applying from outside — the HRMS-hosted form or a Google Form.

The submission is anonymous: there is no user, so there is nothing for
`require()` to check. What authorises it is the JOB TOKEN — a 190-bit secret
that resolves to exactly one published job opening — plus the rate limit on
the endpoint. Everything downstream is the ordinary machinery: the same
identity rules the bulk importer uses, the same Candidate and Application rows,
the same first stage of the same workflow, the same "application received"
email. There is no second pipeline; there is a second front door.

IDEMPOTENCY, IN THREE LAYERS
----------------------------
1. Submission id. The hosted form mints one per page load; a Google response
   carries its own responseId. It is recorded as a `CandidateExternalRef` under
   the provider's source, so the identical POST replayed finds the candidate it
   already created and returns the same application.
2. Identity. Email and phone are resolved through `apps.imports.services.dedup`
   — the ONE place that knows what "already known" means — so a person who
   applied last month by email and again today by phone is one candidate.
3. One application per candidate per job is a database constraint. A second
   submission for the same job by the same person returns the existing
   application rather than creating another, and does not email them twice.

CONSENT
-------
The person filled the form themselves and ticked the box; that is consent under
s.6, recorded as such with the attestation text and time as evidence. This is
the one intake path where `consent_given=True` is honest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.recruitment.application_fields import (
    FILE_TYPE,
    RESUME_EXTENSIONS,
    RESUME_MAX_BYTES,
    fields_for,
)
from apps.recruitment.models import (
    Application,
    Candidate,
    CandidateExternalRef,
    ConsentRecord,
    JobOpening,
    LegalBasis,
)
from apps.recruitment.services import intake
from core.phone import to_e164_in

logger = logging.getLogger("hrms.recruitment.public")

#: What the candidate agrees to. Frozen onto the consent record verbatim so
#: the wording they saw is the wording on file, whatever the form says later.
CONSENT_TEXT = (
    "I confirm the information provided is accurate, and I consent to its use "
    "by the organisation for the purpose of considering my application and "
    "contacting me about it."
)

_SOURCES = frozenset({"hosted_form", "google_forms"})


class PublicIntakeError(ValidationError):
    """A refused public submission — bad token, closed job, invalid answers."""


@dataclass
class PublicApplyResult:
    application: Application
    candidate: Candidate
    created_candidate: bool
    created_application: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def reference(self) -> str:
        return f"APP-{str(self.application.pk)[:8].upper()}"


# ---------------------------------------------------------------- lookup


def job_for_token(token: str) -> JobOpening:
    """The one job this token names, or a 404-shaped refusal."""
    job = (
        JobOpening.objects.select_related("department", "location", "workflow", "designation")
        .filter(application_token=token, is_active=True)
        .first()
    )
    if job is None:
        from django.http import Http404

        raise Http404("No such application link.")
    return job


def public_job_summary(job: JobOpening) -> dict:
    """What an applicant may see: the posting, and the questions. Nothing internal."""
    return {
        "title": job.title,
        "department": job.department.name,
        "location": job.location.name if job.location_id else "",
        "employment_type": job.employment_type,
        "description": job.description,
        "requirements": job.requirements,
        "accepts_applications": job.accepts_applications,
        "fields": fields_for(job),
        "consent_text": CONSENT_TEXT,
    }


# ---------------------------------------------------------------- validation


def _clean_answers(job: JobOpening, answers: dict) -> tuple[dict, dict, dict]:
    """
    Split raw answers into (candidate columns, profile, job-specific answers),
    refusing anything missing or malformed. Values are trimmed and capped; a
    form is not a place to accept a megabyte in a text field.
    """
    if not isinstance(answers, dict):
        raise PublicIntakeError({"answers": "Answers must be an object."})

    errors: dict[str, str] = {}
    candidate_fields: dict = {}
    profile: dict = {}
    extras: dict = {}

    for spec in fields_for(job):
        key = spec["key"]
        if spec["type"] == FILE_TYPE:
            continue  # the upload arrives as a file part, handled by the caller
        raw = answers.get(key)
        value = raw.strip() if isinstance(raw, str) else raw
        if value in (None, ""):
            if spec["required"]:
                errors[key] = f"{spec['label']} is required."
            continue

        kind = spec["type"]
        try:
            if kind == "number":
                value = float(value)
                if value < 0 or value > 1_000_000_000:
                    raise ValueError
            elif kind == "select":
                if str(value) not in spec["options"]:
                    raise ValueError
                value = str(value)
            elif kind == "email":
                from django.core.validators import validate_email

                validate_email(str(value))
                value = str(value).lower()
            elif kind == "phone":
                value = str(value)[:20]
                if to_e164_in(value) is None:
                    raise ValueError
            elif kind == "url":
                # Kept as typed, capped, never a reason to lose an application.
                # People paste "drive.google.com/..." without a scheme, or a
                # local path by mistake; HR sees exactly what was given and can
                # ask for a proper link. Only a truly required link is checked.
                from django.core.validators import URLValidator

                value = str(value)[:400]
                if spec["required"]:
                    URLValidator(schemes=["http", "https"])(value)
            elif kind == "date":
                from django.utils.dateparse import parse_date

                if parse_date(str(value)) is None:
                    raise ValueError
                value = str(value)
            else:
                value = str(value)[: (4000 if kind == "textarea" else 500)]
        except (ValueError, ValidationError):
            errors[key] = f"{spec['label']} is not valid."
            continue

        target = spec["target"]
        if target == "candidate":
            candidate_fields[key] = value
        elif target == "profile":
            profile[key] = value
        else:
            extras[key] = value

    if errors:
        raise PublicIntakeError(errors)

    if not candidate_fields.get("email") and not candidate_fields.get("phone"):
        raise PublicIntakeError({"phone": "An email address or a mobile number is required."})

    return candidate_fields, profile, extras


def _attach_resume(candidate, resume_file, facts) -> None:
    """
    Store the upload on Candidate.resume under its sanitised original name;
    the field's upload_to places it, and the storage de-duplicates a clash.
    """
    resume_file.seek(0)
    candidate.resume.save(facts.original_name, resume_file, save=True)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    return parts[0][:100], " ".join(parts[1:])[:100]


# ---------------------------------------------------------------- the act


@transaction.atomic
def public_apply(
    *,
    job: JobOpening,
    submission_id: str,
    answers: dict,
    source: str = "hosted_form",
    consented: bool = False,
    submitted_at=None,
    remote_meta: dict | None = None,
    resume_file=None,
) -> PublicApplyResult:
    """
    Turn an external submission into a Candidate and an Application on THIS job.

    Ordering: every refusal (closed job, no consent, bad answers) runs before the
    first write. Then the job row is locked so two submissions arriving together
    for the same job serialise, which is what makes the identity check and the
    unique application constraint cooperate instead of race.
    """
    if source not in _SOURCES:
        raise PublicIntakeError({"source": f"Unknown submission source '{source}'."})
    if not job.accepts_applications:
        raise PublicIntakeError(
            {"job": "This position is no longer accepting applications."}
        )
    if not consented:
        raise PublicIntakeError({"consent": "Please confirm the declaration to apply."})
    submission_id = str(submission_id or "").strip()[:128]
    if not submission_id:
        raise PublicIntakeError({"submission_id": "A submission id is required."})

    candidate_fields, profile, extras = _clean_answers(job, answers)
    first_name, last_name = _split_name(candidate_fields.pop("full_name", ""))
    warnings: list[str] = []

    # The résumé, if one was uploaded. Checked BEFORE any write, like every
    # other refusal: an unreadable or oversized file costs the applicant a
    # clear message, not a half-created record.
    resume_facts = None
    if resume_file is not None:
        from core.validators import validate_upload

        try:
            resume_facts = validate_upload(
                resume_file, allowed_extensions=RESUME_EXTENSIONS,
                max_bytes=RESUME_MAX_BYTES, subject="résumé",
            )
        except ValidationError as exc:
            raise PublicIntakeError({"resume": exc.messages}) from exc

    # Serialise per job. select_for_update on the job row is the cheapest lock
    # that covers "same person, same job, twice at once".
    JobOpening.objects.select_for_update().filter(pk=job.pk).exists()

    # --- layer 1: the identical submission, replayed -----------------------
    ref = (
        CandidateExternalRef.objects.filter(
            source=source, external_id=submission_id, is_active=True
        )
        .select_related("candidate")
        .first()
    )
    if ref is not None:
        existing = Application.objects.filter(
            candidate=ref.candidate, job_opening=job
        ).first()
        if existing is not None:
            return PublicApplyResult(existing, ref.candidate, False, False, ["duplicate_submission"])

    # --- layer 2: is this somebody we know? --------------------------------
    from apps.imports.services import dedup
    from apps.imports.models import RowStatus

    row = SimpleNamespace(
        row_number=0,
        external_id="",  # a submission id identifies a submission, not a person
        first_name=first_name,
        last_name=last_name,
        email=candidate_fields.get("email"),
        email_normalized=(candidate_fields.get("email") or "").strip().lower() or None,
        phone=candidate_fields.get("phone", ""),
        phone_e164=to_e164_in(candidate_fields.get("phone")),
        current_employer=candidate_fields.get("current_employer", ""),
        total_experience_years=candidate_fields.get("total_experience_years"),
        expected_ctc=candidate_fields.get("expected_ctc"),
        notice_period_days=None,
    )
    resolution = dedup.resolve(row, platform=source)
    warnings.extend(w["code"] for w in resolution.warnings)

    candidate = resolution.candidate
    created_candidate = False
    if resolution.status == RowStatus.NEEDS_REVIEW:
        # An ambiguous phone match. The rules say never auto-merge, and there
        # is no operator here to ask, so this becomes a new person carrying the
        # warning. HR sees it on the profile and can merge deliberately.
        warnings.append("identity_needs_review")
        candidate = None

    if candidate is None:
        candidate = Candidate.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=row.email,
            phone=row.phone,
            current_employer=row.current_employer or "",
            total_experience_years=row.total_experience_years,
            expected_ctc=row.expected_ctc,
            source=source,
            profile=profile,
            consent_given=True,
            consent_at=submitted_at or timezone.now(),
            legal_basis=LegalBasis.CONSENT,
        )
        created_candidate = True
        if resume_facts is not None:
            _attach_resume(candidate, resume_file, resume_facts)
    else:
        conflicting_email = bool(
            row.email_normalized
            and candidate.email_normalized != row.email_normalized
            and Candidate.objects.filter(
                email_normalized=row.email_normalized, is_active=True
            ).exclude(pk=candidate.pk).exists()
        )
        dedup.enrich(candidate, row, conflicting_email=conflicting_email)
        # Profile blanks only, same enrich-only rule: HR's record wins.
        merged = dict(candidate.profile or {})
        added = {k: v for k, v in profile.items() if merged.get(k) in (None, "")}
        if added:
            merged.update(added)
            candidate.profile = merged
            candidate.save(update_fields=["profile", "updated_at"])
        # Enrich-only, like every other field: a known candidate who never
        # gave us a résumé gets this one; one who did keeps theirs.
        if resume_facts is not None and not candidate.resume:
            _attach_resume(candidate, resume_file, resume_facts)

    ConsentRecord.objects.create(
        candidate=candidate,
        basis=LegalBasis.CONSENT,
        recorded_via=source,
        recorded_at=submitted_at or timezone.now(),
        affirmed_at=submitted_at or timezone.now(),
        evidence={
            "attestation": CONSENT_TEXT,
            "job_opening_id": str(job.pk),
            "submission_id": submission_id,
            **(remote_meta or {}),
        },
    )
    try:
        CandidateExternalRef.objects.create(
            candidate=candidate, source=source, external_id=submission_id
        )
    except IntegrityError:
        # Lost a race with the identical submission; the application below
        # will resolve to the same row either way.
        pass

    # --- layer 3: one application per person per job -----------------------
    existing = Application.objects.filter(candidate=candidate, job_opening=job).first()
    if existing is not None:
        return PublicApplyResult(existing, candidate, created_candidate, False,
                                 warnings + ["already_applied"])

    stage = intake.first_stage_or_refuse(job)
    application = intake.place_application(
        candidate=candidate,
        job_opening=job,
        stage=stage,
        actor=None,
        actor_label=f"Candidate ({'application form' if source == 'hosted_form' else 'Google Form'})",
        form_answers={**extras, "_source": source, "_submission_id": submission_id,
                      **({"_warnings": warnings} if warnings else {})},
    )
    logger.info(
        "recruitment.public_apply job=%s application=%s candidate=%s new_candidate=%s source=%s",
        job.pk, application.pk, candidate.pk, created_candidate, source,
    )
    return PublicApplyResult(application, candidate, created_candidate, True, warnings)
