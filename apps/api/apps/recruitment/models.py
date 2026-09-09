"""
Recruitment runtime state.

The workflow CONFIGURATION lives in `apps.workflows`. This module holds what
actually happens: jobs, candidates, applications moving through stages,
interviews, decisions, offers.

Every stage movement writes an `ApplicationEvent`, so the candidate journey is
reconstructable in full, permanently, regardless of later edits.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Length
from django.utils import timezone

from core.models import OrgOwnedModel
from core.validators import STORED_PATH_MAX
from core.phone import to_e164_in


def new_application_token() -> str:
    """32 URL-safe characters, ~190 bits from the OS CSPRNG."""
    import secrets

    return secrets.token_urlsafe(24)


class TsTzRange(models.Func):
    """`tstzrange(lower, upper, bounds)` — for the interview exclusion constraint."""

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()

# Enables `field__length__gte=N` in constraints. A regex such as `\S{20,}`
# looks equivalent but demands twenty CONSECUTIVE non-space characters, which
# no real sentence contains — it would reject every genuine reason while
# accepting one long meaningless token.
models.TextField.register_lookup(Length)
models.CharField.register_lookup(Length)


class JobStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ON_HOLD = "on_hold", "On hold"
    CLOSED = "closed", "Closed"
    FILLED = "filled", "Filled"


class ApplicationStatus(models.TextChoices):
    ACTIVE = "active", "In progress"
    SELECTED = "selected", "Selected"
    OFFER_SENT = "offer_sent", "Offer sent"
    OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
    OFFER_DECLINED = "offer_declined", "Offer declined"
    HIRED = "hired", "Hired"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class JobOpening(OrgOwnedModel):
    title = models.CharField(max_length=160)
    #: The workflow this job runs. The ONLY thing that differs between a
    #: Therapist pipeline and an Office Boy pipeline.
    workflow = models.ForeignKey(
        "workflows.HiringWorkflow", on_delete=models.PROTECT, related_name="job_openings"
    )
    department = models.ForeignKey(
        "organization.Department", on_delete=models.PROTECT, related_name="job_openings"
    )
    designation = models.ForeignKey(
        "organization.Designation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_openings",
    )
    location = models.ForeignKey(
        "organization.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_openings",
    )
    level = models.ForeignKey(
        "organization.EmployeeLevel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_openings",
    )
    #: The role a successful candidate is hired into. Validated against the
    #: department at conversion time by the existing hierarchy rules.
    target_role = models.ForeignKey(
        "accounts.Role", on_delete=models.PROTECT, related_name="job_openings"
    )

    description = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    openings_count = models.PositiveSmallIntegerField(default=1)
    employment_type = models.CharField(max_length=20, default="full_time")

    #: Posting criteria the office states on every opening. Optional at the
    #: model so existing rows and scripted fixtures stay valid; the job form
    #: asks for all three.
    age_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    gender_preference = models.CharField(
        max_length=10,
        choices=[("any", "Any"), ("male", "Male"), ("female", "Female")],
        default="any",
        blank=True,
    )
    #: Free text — a range ("₹4–6 LPA") is as common as a number.
    salary = models.CharField(max_length=120, blank=True)

    status = models.CharField(
        max_length=20, choices=JobStatus.choices, default=JobStatus.DRAFT, db_index=True
    )
    recruiter = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_as_recruiter",
    )
    hiring_manager = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="jobs_as_hiring_manager",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    #: The public application link is /apply/<application_token>. Generated
    #: once in save(), never reused, and long enough that guessing one is not
    #: a strategy: it is the ONLY thing standing between the open internet and
    #: this job's pipeline, so it carries well over 128 bits.
    application_token = models.CharField(
        max_length=64, unique=True, editable=False, db_index=True
    )
    #: Which questions the external form asks — see application_fields.py.
    #: Empty means the whole standard catalogue.
    application_fields = models.JSONField(default=list, blank=True)

    #: The external form built for this job, if a provider is configured.
    #: "hosted" is the HRMS's own page and always exists; "google_forms" is
    #: filled in when the Google Forms API is enabled and creation succeeded.
    external_form_provider = models.CharField(max_length=20, default="hosted", blank=True)
    external_form_id = models.CharField(max_length=120, blank=True)
    external_form_url = models.URLField(max_length=400, blank=True)
    external_form_synced_at = models.DateTimeField(null=True, blank=True)
    external_form_error = models.TextField(blank=True)
    #: Google question id -> our field key, captured when the form was built,
    #: so a response can be decoded without asking Google what it asked.
    external_form_item_map = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "department"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.application_token:
            self.application_token = new_application_token()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"application_token"}
        super().save(*args, **kwargs)

    @property
    def application_url(self) -> str:
        from django.conf import settings

        return f"{settings.FRONTEND_URL.rstrip('/')}/apply/{self.application_token}"

    @property
    def accepts_applications(self) -> bool:
        return self.is_active and self.status == JobStatus.PUBLISHED

    def clean(self):
        super().clean()
        if self.workflow_id and not self.workflow.is_published:
            raise ValidationError(
                {"workflow": f"'{self.workflow.name}' is a draft and cannot run a job."}
            )
        if (
            self.workflow_id
            and self.department_id
            and self.workflow.department_kind
            and self.workflow.department_kind != self.department.kind
        ):
            raise ValidationError(
                {
                    "workflow": (
                        f"'{self.workflow.name}' serves the "
                        f"{self.workflow.department_kind} function, but this job is "
                        f"in a {self.department.kind} department."
                    )
                }
            )
        # The same department↔designation rule `create_employee` enforces,
        # applied at the front of the pipeline: a job whose title belongs to
        # another department would only fail at conversion, months later.
        if (
            self.designation_id
            and self.department_id
            and self.designation.department_id
            and self.designation.department_id != self.department_id
        ):
            raise ValidationError(
                {
                    "designation": (
                        f"'{self.designation.title}' belongs to another department "
                        f"and cannot be used in '{self.department.name}'."
                    )
                }
            )


class LegalBasis(models.TextChoices):
    """
    The ground on which we process a candidate's personal data.

    DPDP Act 2023 has no "legitimate interests" catch-all. Processing rests
    either on consent (s.6) or on one of the enumerated certain legitimate uses
    (s.7). A boolean cannot carry that distinction, which is why this exists
    alongside `consent_given` rather than replacing it.
    """

    #: s.6 — the person affirmatively agreed, to US. Mirrors consent_given=True.
    CONSENT = "consent", "Consent given directly"
    #: s.7(a) — the person voluntarily provided their data for this purpose,
    #: e.g. by posting a profile on a job platform to be contacted about work.
    #: This is what a bulk import from a platform export claims.
    VOLUNTARILY_PROVIDED = "voluntarily_provided", "Voluntarily provided (s.7(a))"
    #: Sourced through the employer's own paid subscription to a platform.
    EMPLOYER_SUBSCRIPTION = "employer_subscription", "Employer platform subscription"
    #: Backfilled onto rows that predate this field. Not a claim that a basis
    #: existed — an honest admission that none was recorded.
    LEGACY_UNRECORDED = "legacy_unrecorded", "Legacy — basis not recorded"


class Candidate(OrgOwnedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)

    #: NULLABLE, and NULL rather than "" — see save().
    #:
    #: A WorkIndia export routinely carries a phone and no email, and inventing
    #: an address to satisfy a NOT NULL would put a fictional identifier into
    #: the column the rest of the system treats as identity. Postgres unique
    #: indexes treat NULLs as distinct, so any number of email-less candidates
    #: coexist under the partial unique index below.
    email = models.EmailField(null=True, blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True)

    #: Derived identity keys. Written only by save(), never by a serializer.
    #:
    #: Stored columns rather than functional indexes on LOWER(email): E.164 is
    #: not expressible in SQL, and one normalization mechanism with tests beats
    #: two where the interesting one is invisible to the ORM. They are also what
    #: lets a dedup preview say WHICH key matched.
    email_normalized = models.EmailField(
        null=True, blank=True, editable=False, db_index=True
    )
    phone_e164 = models.CharField(
        max_length=16, null=True, blank=True, editable=False, db_index=True
    )

    current_employer = models.CharField(max_length=160, blank=True)
    total_experience_years = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    expected_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notice_period_days = models.PositiveSmallIntegerField(null=True, blank=True)

    resume = models.FileField(
        upload_to="recruitment/resumes/", max_length=STORED_PATH_MAX, null=True, blank=True
    )
    #: Where this person came from: "direct", "referral", "workindia", "naukri".
    source = models.CharField(max_length=40, default="direct", db_index=True)
    #: Descriptive attributes an application form collects — city,
    #: qualification, skills, languages, education dates. Read on the profile
    #: page; computed on by nothing. See application_fields.py for the keys.
    profile = models.JSONField(default=dict, blank=True)

    # DPDP Act 2023
    #: TRUE means the person affirmatively consented TO US. It does not mean
    #: "we are allowed to hold this" — that is `legal_basis`. Keeping the two
    #: apart is what stops a bulk import from claiming a consent nobody gave.
    consent_given = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    legal_basis = models.CharField(
        max_length=32, choices=LegalBasis.choices, blank=True
    )
    #: When a privacy notice is owed before first contact. Set for candidates
    #: acquired without direct consent; cleared once notice is sent.
    notice_due_at = models.DateTimeField(null=True, blank=True)
    final_decision_at = models.DateTimeField(null=True, blank=True)
    retention_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Replaces uniq_active_candidate_email, which was case-SENSITIVE and
            # so admitted A@x.com alongside a@x.com as two people.
            models.UniqueConstraint(
                fields=["email_normalized"],
                condition=models.Q(is_active=True),
                name="uniq_active_candidate_email_norm",
            ),
            # Consent was previously enforced only in a serializer, so any
            # service, shell or management command could write a candidate with
            # no lawful basis at all. At the database, that is now impossible.
            models.CheckConstraint(
                condition=models.Q(consent_given=True) | ~models.Q(legal_basis=""),
                name="ck_candidate_has_lawful_basis",
            ),
        ]
        indexes = [
            # An INDEX, not a unique constraint, and the most consequential
            # choice here. Two people legitimately share one handset — a family
            # phone, a shop line — most often in exactly the blue-collar segment
            # WorkIndia serves. Phone is a matching SIGNAL, not an identity
            # guarantee. Ambiguity resolves to a row a human reviews, never to
            # an IntegrityError nobody can act on.
            models.Index(
                fields=["phone_e164"],
                condition=models.Q(is_active=True),
                name="ix_candidate_phone_active",
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Derive the identity keys.

        Follows `Interview.save()`, which derives `scheduled_end` for the same
        reason: the value has to be stored for the database to index it, so it
        is computed in exactly one place rather than at every call site.

        The empty string is converted to NULL deliberately and without
        exception. Postgres treats NULLs in a unique index as distinct but ""
        as an ordinary value, so the second email-less candidate would collide
        with the first.
        """
        self.email = (self.email or "").strip().lower() or None
        self.email_normalized = self.email
        self.phone_e164 = to_e164_in(self.phone)

        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "email",
                "email_normalized",
                "phone_e164",
            }
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ConsentRecord(OrgOwnedModel):
    """
    Per-candidate ledger of what we relied on to hold their data, and when.

    APPEND-ONLY. A candidate can be sourced twice on different grounds — a
    WorkIndia export in March, a direct application in June — and DPDP requires
    us to be able to show, per person, which ground applied at which time. A
    column on Candidate could hold only the latest, which is exactly the
    question a regulator does not ask.

    Three lifecycle stamps, none of which a boolean could carry:

      notice_sent_at  the privacy notice owed to someone whose data we acquired
                      without asking them. Owed BEFORE first contact.
      affirmed_at     the moment s.7(a) became s.6 — they replied, applied, or
                      ticked the box, and now genuinely consent to us.
      withdrawn_at    s.6(6) withdrawal. Without this column, withdrawal is
                      unimplementable, and an unimplementable right is not one.
    """

    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="consent_records"
    )
    basis = models.CharField(max_length=32, choices=LegalBasis.choices)

    #: How this basis came to be recorded: "hr_manual", "bulk_import",
    #: "candidate_reply". Free text rather than choices because the set grows
    #: with intake channels and a migration per channel buys nothing.
    recorded_via = models.CharField(max_length=40)
    #: NULL means the data subject recorded it themself — a candidate ticking
    #: the declaration on the public application form. Every other path has
    #: a user behind it, and keeps one.
    recorded_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="+", null=True, blank=True
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    #: What was relied on, captured at the time. For an import: the attestation
    #: wording shown to the HR user, the platform account, the export date.
    #: Snapshotted rather than referenced, for the same reason
    #: CandidateRejection keeps history_snapshot — the wording will change and
    #: this record must still say what was agreed to on the day.
    evidence = models.JSONField(default=dict, blank=True)

    #: The import that produced this basis, where one did. Added once
    #: ImportBatch existed rather than as a loose UUID column, so the link is
    #: a real foreign key and a batch cannot be deleted out from under the
    #: evidence that justified it.
    origin_batch = models.ForeignKey(
        "imports.ImportBatch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="consent_records",
    )

    notice_sent_at = models.DateTimeField(null=True, blank=True)
    affirmed_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["candidate", "-recorded_at"]),
            models.Index(fields=["basis", "affirmed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.candidate_id} · {self.basis}"

    @property
    def is_live(self) -> bool:
        """Still relied upon: not withdrawn."""
        return self.withdrawn_at is None


class CandidateExternalRef(OrgOwnedModel):
    """
    A platform's own identifier for a candidate.

    The strongest deduplication key available: an assertion of identity made by
    the source, rather than one inferred from a contact detail people mistype,
    share and abandon. One candidate may hold several — the same person sourced
    from both WorkIndia and Naukri — which is why this is a table and not a
    column.
    """

    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="external_refs"
    )
    #: Matches Candidate.source: "workindia", "naukri", …
    source = models.CharField(max_length=40, db_index=True)
    external_id = models.CharField(max_length=128)

    class Meta:
        ordering = ["source", "external_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=models.Q(is_active=True),
                name="uniq_active_external_ref",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id}"


class Application(OrgOwnedModel):
    """A candidate's progress through one job's workflow."""

    candidate = models.ForeignKey(Candidate, on_delete=models.PROTECT, related_name="applications")
    job_opening = models.ForeignKey(
        JobOpening, on_delete=models.PROTECT, related_name="applications"
    )
    current_stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.PROTECT, related_name="applications"
    )
    status = models.CharField(
        max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.ACTIVE,
        db_index=True,
    )
    applied_at = models.DateTimeField(default=timezone.now)
    #: Answers to the job's OWN extra questions, from the external form. The
    #: standard questions land on the Candidate; these belong to this
    #: application alone.
    form_answers = models.JSONField(default=dict, blank=True)

    #: Set by HR verification. Only verified applications may enter the
    #: department interview stages — enforced by the transition table, and
    #: re-asserted by the engine.
    is_verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-applied_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["candidate", "job_opening"], name="uniq_application_per_candidate_job"
            )
        ]
        indexes = [models.Index(fields=["job_opening", "current_stage", "status"])]

    def __str__(self) -> str:
        return f"{self.candidate.full_name} → {self.job_opening.title}"

    @property
    def department(self):
        return self.job_opening.department

    @property
    def is_open(self) -> bool:
        return self.status == ApplicationStatus.ACTIVE


class ApplicationEvent(OrgOwnedModel):
    """
    The candidate journey, append-only.

    Every stage movement, interview, decision, offer action and conversion
    writes a row. Reconstructs the full history even after the underlying
    records are edited.
    """

    class Kind(models.TextChoices):
        APPLIED = "applied", "Applied"
        STAGE_CHANGED = "stage_changed", "Stage changed"
        VERIFIED = "verified", "HR verified"
        INFO_REQUESTED = "info_requested", "More information requested"
        INTERVIEW_SCHEDULED = "interview_scheduled", "Interview scheduled"
        INTERVIEW_COMPLETED = "interview_completed", "Interview completed"
        FEEDBACK_SUBMITTED = "feedback_submitted", "Feedback submitted"
        RECOMMENDATION = "recommendation", "Department recommendation"
        HR_DECISION = "hr_decision", "HR Head decision"
        REJECTED = "rejected", "Rejected"
        OVERRIDE = "override", "Administrative override"
        OFFER_CREATED = "offer_created", "Offer created"
        OFFER_SENT = "offer_sent", "Offer sent"
        OFFER_RESPONDED = "offer_responded", "Offer responded to"
        CONVERTED = "converted", "Converted to employee"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30, choices=Kind.choices, db_index=True)
    from_stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    to_stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decision = models.CharField(max_length=30, blank=True)
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    actor_label = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["application", "created_at"])]

    def __str__(self) -> str:
        return f"{self.application_id} · {self.kind}"


class InterviewStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    RESCHEDULED = "rescheduled", "Rescheduled"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "Candidate did not attend"


#: Statuses that occupy an interviewer's calendar. Cancelled and completed
#: interviews do not block a new booking.
BLOCKING_INTERVIEW_STATUSES = (InterviewStatus.SCHEDULED, InterviewStatus.RESCHEDULED)


class Interview(OrgOwnedModel):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="interviews"
    )
    stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.PROTECT, related_name="interviews"
    )
    interviewer = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="interviews_conducted"
    )

    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=45)
    #: Stored, not derived, so the database exclusion constraint can index it.
    scheduled_end = models.DateTimeField(editable=False)

    mode = models.CharField(max_length=20, default="in_person")
    location_or_link = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20, choices=InterviewStatus.choices, default=InterviewStatus.SCHEDULED,
        db_index=True,
    )

    #: Google Calendar linkage. HRMS remains the source of truth for the
    #: interview; the calendar event mirrors it for invitations and Meet.
    #: A failed sync never blocks scheduling — it is recorded here and
    #: retryable from the interview panel.
    calendar_event_id = models.CharField(max_length=128, blank=True, editable=False)
    calendar_sync_status = models.CharField(
        max_length=12,
        blank=True,
        default="",
        choices=[
            ("", "Not attempted"),
            ("synced", "Synced"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
    )
    calendar_error = models.TextField(blank=True)

    class Meta:
        ordering = ["scheduled_at"]
        indexes = [
            models.Index(fields=["interviewer", "scheduled_at"]),
            models.Index(fields=["application", "stage"]),
        ]
        constraints = [
            # THE authoritative double-booking guarantee.
            #
            # The service's overlap query gives a readable error, but two
            # concurrent requests can both pass it — they read before either
            # writes. Only the database can refuse the second at COMMIT.
            #
            # `[)` bounds: an interview ending exactly when the next begins is
            # not an overlap, so back-to-back slots remain bookable. Scoped to
            # blocking statuses, so cancelling frees the slot.
            ExclusionConstraint(
                name="excl_interviewer_double_booking",
                expressions=[
                    ("interviewer", RangeOperators.EQUAL),
                    (
                        TsTzRange("scheduled_at", "scheduled_end", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(
                    status__in=[InterviewStatus.SCHEDULED, InterviewStatus.RESCHEDULED],
                    is_active=True,
                ),
            )
        ]

    def __str__(self) -> str:
        return f"{self.application.candidate.full_name} · {self.stage.name}"

    def save(self, *args, **kwargs):
        self.scheduled_end = self.scheduled_at + timedelta(minutes=self.duration_minutes)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"scheduled_end"}
        super().save(*args, **kwargs)

    @property
    def blocks_calendar(self) -> bool:
        return self.status in BLOCKING_INTERVIEW_STATUSES


class Recommendation(models.TextChoices):
    STRONG_HIRE = "strong_hire", "Strong hire"
    HIRE = "hire", "Hire"
    HOLD = "hold", "Hold"
    NO_HIRE = "no hire", "No hire"


class InterviewFeedback(OrgOwnedModel):
    """
    Structured assessment from one interview.

    ADVISORY. `recommendation` never changes application state by itself — the
    engine reads the stage's recorded decision, and only a final HR decision
    stage may carry a terminal one.
    """

    interview = models.OneToOneField(
        Interview, on_delete=models.CASCADE, related_name="feedback"
    )
    #: The structured assessment this round asked for, if the stage has one.
    #: NULL means the round was held without a form: the recommendation,
    #: rating, strengths and concerns below ARE the feedback, and `answers` is
    #: empty. Custom workflows routinely have no form for an HR round, and a
    #: verdict should not be unrecordable for want of a questionnaire.
    form = models.ForeignKey(
        "workflows.FeedbackForm", on_delete=models.PROTECT, related_name="responses",
        null=True, blank=True,
    )
    submitted_by = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="feedback_submitted"
    )
    submitted_at = models.DateTimeField(default=timezone.now)

    #: {field_key: value}. Validated against the form's fields on save.
    answers = models.JSONField(default=dict)
    strengths = models.TextField(blank=True)
    concerns = models.TextField(blank=True)
    recommendation = models.CharField(max_length=20, choices=Recommendation.choices)
    overall_rating = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self) -> str:
        return f"{self.interview} · {self.recommendation}"


class StageDecision(OrgOwnedModel):
    """
    A decision recorded at a stage.

    Covers HR verification, interview outcomes and department recommendations
    uniformly, because from the engine's point of view they are the same act:
    an authorised person records an allowed decision at the current stage.
    """

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="stage_decisions"
    )
    stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.PROTECT, related_name="decisions"
    )
    decision = models.CharField(max_length=30, db_index=True)
    decided_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(default=timezone.now)
    rationale = models.TextField(blank=True)
    interview = models.ForeignKey(
        Interview, on_delete=models.SET_NULL, null=True, blank=True, related_name="decisions"
    )

    class Meta:
        ordering = ["-decided_at"]
        indexes = [models.Index(fields=["application", "stage"])]

    def __str__(self) -> str:
        return f"{self.application_id} · {self.stage.name} · {self.decision}"


class CandidateRejection(OrgOwnedModel):
    """
    The FINAL rejection. HR Head only.

    A reason is mandatory at three layers: this CheckConstraint, the service,
    and the serializer. `department_recommendation` links the advisory decision
    that preceded it, so the record shows both what the department advised and
    what HR decided.
    """

    application = models.OneToOneField(
        Application, on_delete=models.PROTECT, related_name="rejection"
    )
    candidate = models.ForeignKey(Candidate, on_delete=models.PROTECT, related_name="rejections")
    rejected_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    rejected_at = models.DateTimeField(default=timezone.now)
    rejection_stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.PROTECT, related_name="+"
    )
    reason = models.TextField()
    department_recommendation = models.ForeignKey(
        StageDecision, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: Snapshot of all feedback at rejection time, so the justification survives
    #: any later edit to the underlying records.
    history_snapshot = models.JSONField(default=dict, blank=True)
    is_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ["-rejected_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason__length__gte=20),
                name="ck_rejection_reason_minimum_length",
            )
        ]

    def __str__(self) -> str:
        return f"Rejected: {self.candidate.full_name}"


class DecisionOverride(OrgOwnedModel):
    """
    An Admin override of a decision Admin does not normally own.

    Deliberately a separate model from `CandidateRejection`: an override is not
    a rejection, and conflating them would hide the exception inside the normal
    record. Every override is high-visibility by construction.
    """

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="overrides"
    )
    overridden_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    overridden_at = models.DateTimeField(default=timezone.now)
    previous_status = models.CharField(max_length=30)
    new_status = models.CharField(max_length=30)
    #: Where the application sat before and after. Recorded because an override
    #: that reopens a rejected candidate MOVES them — from the terminal stage
    #: back to an actionable one — and an audit record showing only the status
    #: change would not explain how the application became workable again.
    previous_stage = models.ForeignKey(
        "workflows.WorkflowStage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    new_stage = models.ForeignKey(
        "workflows.WorkflowStage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.TextField()

    class Meta:
        ordering = ["-overridden_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason__length__gte=20),
                name="ck_override_reason_minimum_length",
            )
        ]

    def __str__(self) -> str:
        return f"Override on {self.application_id} by {self.overridden_by_id}"


class OfferStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Offer(OrgOwnedModel):
    application = models.OneToOneField(
        Application, on_delete=models.PROTECT, related_name="offer"
    )
    offered_ctc = models.DecimalField(max_digits=12, decimal_places=2)
    joining_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    designation = models.ForeignKey(
        "organization.Designation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="offers",
    )
    level = models.ForeignKey(
        "organization.EmployeeLevel", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="offers",
    )
    reporting_manager = models.ForeignKey(
        "employees.Employee", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="offers_as_manager",
    )
    status = models.CharField(
        max_length=20, choices=OfferStatus.choices, default=OfferStatus.DRAFT, db_index=True
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    #: The letter the candidate was actually sent, generated and frozen at
    #: send time — later edits to the letterhead or signatory must never
    #: silently change what a candidate already holds.
    letter_pdf = models.FileField(
        upload_to="offers/letters/", max_length=512, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Offer · {self.application.candidate.full_name}"


class CandidateNotificationKind(models.TextChoices):
    """
    Every email a candidate can receive, one per template.

    Each maps to a real transition in the existing workflow and is sent by the
    service that performs it — never by a view — so what a candidate is told is
    always what the Application record says.
    """

    APPLICATION_RECEIVED = "application_received", "Application received"
    HR_VERIFICATION_PASSED = "hr_verification_passed", "HR verification passed"
    HR_VERIFICATION_REJECTED = "hr_verification_rejected", "HR verification rejected"
    INTERVIEW_SLOT_INVITE = "interview_slot_invite", "Interview slot selection"
    INTERVIEW_REBOOKING = "interview_rebooking", "Interview time to be rebooked"
    INTERVIEW_SCHEDULED = "interview_scheduled", "Interview scheduled"
    INTERVIEW_RESCHEDULED = "interview_rescheduled", "Interview rescheduled"
    INTERVIEW_CANCELLED = "interview_cancelled", "Interview cancelled"
    INTERVIEW_SELECTED = "interview_selected", "Selected for next round"
    INTERVIEW_NOT_SELECTED = "interview_not_selected", "Not selected after interview"
    INTERVIEW_ON_HOLD = "interview_on_hold", "Application under further review"
    DEPARTMENT_DECISION = "department_decision", "Department decision"
    FINAL_SELECTION = "final_selection", "Final selection"
    FINAL_REJECTION = "final_rejection", "Final rejection"
    OFFER_SENT = "offer_sent", "Offer sent"
    OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
    OFFER_DECLINED = "offer_declined", "Offer declined"


class CandidateNotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped — no email address"


class CandidateNotification(OrgOwnedModel):
    """
    One email to one candidate about one application: the communication log.

    A row is written BEFORE the send is attempted and updated with the outcome,
    so a crash mid-send leaves a visible PENDING row rather than silence. The
    dedupe key is unique: the same event on the same application produces one
    row however many times the request is replayed, and a retry updates that
    row instead of adding another.

    The rendered subject and body are kept so what the candidate was told is on
    the record verbatim — the template may change later; this cannot.
    """

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="candidate_notifications"
    )
    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="notifications"
    )
    job_opening = models.ForeignKey(
        JobOpening, on_delete=models.CASCADE, related_name="candidate_notifications"
    )
    kind = models.CharField(
        max_length=40, choices=CandidateNotificationKind.choices, db_index=True
    )
    recipient_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    #: The variables the template was filled from — what an HR user reads when
    #: they want to know what the candidate was told, without the prose.
    context = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=12,
        choices=CandidateNotificationStatus.choices,
        default=CandidateNotificationStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    triggered_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    dedupe_key = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["application", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} -> {self.recipient_email or '-'} [{self.status}]"


class SlotInviteStatus(models.TextChoices):
    PENDING = "pending", "Awaiting the candidate's choice"
    SELECTED = "selected", "Slot chosen — awaiting HR confirmation"
    CONFIRMED = "confirmed", "Interview scheduled"
    EXPIRED = "expired", "Expired unanswered"
    CANCELLED = "cancelled", "Withdrawn"


class InterviewSlotInvite(OrgOwnedModel):
    """
    One round's invitation to pick an interview time.

    The antechamber to the interview system, never a replacement for it: the
    candidate's choice lives here until somebody with INTERVIEW/CREATE turns
    it into a real Interview through the ordinary scheduling path, which
    settles this row. See services/slots.py for the rules.
    """

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="slot_invites"
    )
    stage = models.ForeignKey(
        "workflows.WorkflowStage", on_delete=models.PROTECT, related_name="slot_invites"
    )
    candidate = models.ForeignKey(
        Candidate, on_delete=models.CASCADE, related_name="slot_invites"
    )
    #: The only credential the candidate holds. Same construction as the job
    #: application token: unguessable, single-purpose.
    token = models.CharField(max_length=64, unique=True, editable=False, db_index=True)

    #: What was offered: [{"start": iso, "end": iso}, ...]. The choice is
    #: validated against THIS list, never trusted from the request.
    options = models.JSONField(default=list)
    selected_slot = models.JSONField(default=dict, blank=True)
    selected_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=SlotInviteStatus.choices,
        default=SlotInviteStatus.PENDING, db_index=True,
    )
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["application", "status"])]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = new_application_token()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"token"}
        super().save(*args, **kwargs)

    @property
    def selection_url(self) -> str:
        from django.conf import settings

        return f"{settings.FRONTEND_URL.rstrip('/')}/interview-slot/{self.token}"

    def __str__(self) -> str:
        return f"{self.candidate_id} · {self.stage_id} [{self.status}]"
