"""
Recruitment serializers.

These validate SHAPE. They deliberately do not re-implement workflow rules —
who may act at a stage, whether a transition exists, whether the interviewer is
free — because those live in the services, which are also reachable from Celery
tasks and management commands. A rule enforced in a serializer is a rule the
non-HTTP callers do not have.

The one thing they do duplicate is the mandatory-reason floor, because the spec
requires it at DB, API and UI: three independent layers, so a client that skips
one still meets the others.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.employees.models import Employee
from apps.organization.models import Department, Designation, Location
from apps.recruitment.access import resolve_job_opening
from apps.recruitment.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Candidate,
    CandidateRejection,
    DecisionOverride,
    Interview,
    InterviewFeedback,
    JobOpening,
    Offer,
    StageDecision,
)
from apps.recruitment.services.engine import MIN_REASON_LENGTH
from apps.workflows.models import (
    FeedbackField,
    FeedbackForm,
    HiringWorkflow,
    StageTransition,
    WorkflowStage,
)


# --------------------------------------------------------------------------
# Workflow configuration (read-only over HTTP)
# --------------------------------------------------------------------------


from core.api.serializers import OrgScopedUniqueMixin

from core.api.serializers import ScopedRelationsMixin

class FeedbackFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackField
        fields = ["id", "key", "label", "kind", "choices", "order", "is_required"]


class FeedbackFormSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    fields_ = FeedbackFieldSerializer(source="fields", many=True, read_only=True)

    class Meta:
        model = FeedbackForm
        fields = ["id", "name", "description", "fields_"]


class StageTransitionSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    to_stage_name = serializers.CharField(source="to_stage.name", read_only=True)
    to_stage_order = serializers.IntegerField(source="to_stage.order", read_only=True)

    class Meta:
        model = StageTransition
        fields = ["id", "on_decision", "to_stage", "to_stage_name", "to_stage_order"]


class WorkflowStageSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    responsible_role_code = serializers.CharField(
        source="responsible_role.code", read_only=True, default=None
    )
    transitions = StageTransitionSerializer(
        source="outgoing_transitions", many=True, read_only=True
    )

    class Meta:
        model = WorkflowStage
        fields = [
            "id", "name", "order", "kind",
            "responsible_role", "responsible_role_code",
            "allowed_decisions", "requires_interview", "requires_feedback",
            "feedback_form", "is_final_hr_decision", "is_terminal", "is_won",
            "transitions",
        ]


class HiringWorkflowSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    stages = WorkflowStageSerializer(many=True, read_only=True)

    class Meta:
        model = HiringWorkflow
        fields = [
            "id", "name", "description", "department_kind",
            "is_published", "version", "stages",
        ]


class InterviewRoundSerializer(ScopedRelationsMixin, serializers.Serializer):
    """One interview round of a process description, in interview order."""

    name = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.CharField()
    feedback_form = serializers.PrimaryKeyRelatedField(
        queryset=FeedbackForm.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )


class WorkflowAuthorSerializer(serializers.Serializer):
    """
    What the Admin describes; the service derives the graph. There is
    deliberately no way to post raw stages or transitions — the canonical
    shapes live in one generator, where their invariants are load-bearing.
    """

    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    department_kind = serializers.CharField(required=False, allow_blank=True, default="")
    #: Defaults to the Recruiter — verification is their stage. Kept in ONE
    #: place would be better still; until then this must match
    #: workflows.services.create_workflow's default.
    verification_role = serializers.CharField(required=False, default="recruiter")
    interview_rounds = InterviewRoundSerializer(many=True)
    recommendation_role = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )


class HiringWorkflowListSerializer(OrgScopedUniqueMixin, serializers.ModelSerializer):
    stage_count = serializers.IntegerField(source="stages.count", read_only=True)

    class Meta:
        model = HiringWorkflow
        fields = ["id", "name", "description", "department_kind", "is_published", "stage_count"]


# --------------------------------------------------------------------------
# Jobs and candidates
# --------------------------------------------------------------------------


class JobOpeningSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    workflow_name = serializers.CharField(source="workflow.name", read_only=True)
    target_role_code = serializers.CharField(source="target_role.code", read_only=True)
    application_count = serializers.IntegerField(read_only=True, default=0)
    #: The public link — /apply/<token> on this deployment. Read-only: the
    #: token is minted once by the model and never chosen by a client.
    application_url = serializers.CharField(read_only=True)
    accepts_applications = serializers.BooleanField(read_only=True)
    #: Which questions the external form asks. Keys from the standard
    #: catalogue plus this job's own extras — see application_fields.py.
    application_fields = serializers.JSONField(required=False)
    resolved_application_fields = serializers.SerializerMethodField()

    class Meta:
        model = JobOpening
        fields = [
            "id", "title", "workflow", "workflow_name",
            "department", "department_name", "designation", "location", "level",
            "target_role", "target_role_code",
            "description", "requirements", "openings_count", "employment_type",
            "age_limit", "gender_preference", "salary",
            "status", "recruiter", "hiring_manager",
            "published_at", "closed_at", "application_count", "created_at",
            "application_token", "application_url", "accepts_applications",
            "application_fields", "resolved_application_fields",
            "external_form_provider", "external_form_id", "external_form_url",
            "external_form_synced_at", "external_form_error",
        ]
        read_only_fields = [
            "published_at", "closed_at", "created_at", "application_token",
            "external_form_provider", "external_form_id", "external_form_url",
            "external_form_synced_at", "external_form_error",
        ]

    def get_resolved_application_fields(self, job) -> list[dict]:
        from apps.recruitment.application_fields import fields_for

        return fields_for(job)

    def validate_application_fields(self, value):
        from apps.recruitment.application_fields import validate_configuration

        try:
            return validate_configuration(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        """Run the model's own cross-field rules — workflow published, kind matches."""
        instance = JobOpening(**{**self._current(), **attrs})
        instance.full_clean(exclude=self._skip_fields())
        return attrs

    def _current(self) -> dict:
        if self.instance is None:
            return {}
        return {
            f.name: getattr(self.instance, f.name)
            for f in JobOpening._meta.concrete_fields
            if f.name != "id"
        }

    def _skip_fields(self) -> list[str]:
        """full_clean() validates only what clean() needs; field checks already ran."""
        return [
            f.name for f in JobOpening._meta.concrete_fields
            if f.name not in ("workflow", "department")
        ]


class CandidateSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    #: Whether a résumé file is on record, and its name. Never the storage
    #: URL: media is deliberately not served publicly, so the model field must
    #: not reach the serializer as a FileField at all — DRF renders one by
    #: asking the storage for a URL, and a storage with no public URL answers
    #: with a ValueError that took the whole candidate list down. The file
    #: itself comes through the authorised /candidates/<id>/resume/ download.
    resume = serializers.SerializerMethodField()
    has_resume = serializers.SerializerMethodField()
    resume_name = serializers.SerializerMethodField()

    def get_resume(self, candidate) -> bool:
        return bool(candidate.resume)

    def get_has_resume(self, candidate) -> bool:
        return bool(candidate.resume)

    def get_resume_name(self, candidate) -> str:
        return candidate.resume.name.split("/")[-1] if candidate.resume else ""
    #: Every application this candidate has that the CALLER may see — the
    #: job, its status and stage. Filtered with the same scoper that filters
    #: the applications list, so a department-scoped user learns nothing
    #: about a candidate's applications in other departments.
    applications = serializers.SerializerMethodField()

    def get_applications(self, candidate) -> list[dict]:
        request = self.context.get("request")
        qs = candidate.applications.select_related(
            "job_opening__department", "current_stage"
        ).filter(is_active=True).order_by("-applied_at")
        if request is not None and getattr(request, "user", None) is not None:
            from apps.recruitment.access import pipeline_scope_filter
            from core.access import Resource

            qs = pipeline_scope_filter(
                qs, request.user, resource=Resource.APPLICATION,
                department_path="job_opening__department_id__in",
                assigned_path="interviews__interviewer_id",
            )
        return [
            {
                "id": str(a.pk),
                "job_opening": str(a.job_opening_id),
                "job_title": a.job_opening.title,
                "department_name": a.job_opening.department.name,
                "status": a.status,
                "stage_name": a.current_stage.name,
                "applied_at": a.applied_at,
            }
            for a in qs
        ]

    class Meta:
        model = Candidate
        fields = [
            "id", "first_name", "last_name", "full_name", "email", "phone",
            "current_employer", "total_experience_years", "expected_ctc",
            "notice_period_days", "resume", "has_resume", "resume_name", "source", "profile", "applications",
            "consent_given", "consent_at", "legal_basis", "notice_due_at",
            "final_decision_at", "retention_until", "created_at",
        ]
        read_only_fields = [
            "applications", "has_resume", "resume_name", "consent_at", "legal_basis", "notice_due_at",
            "final_decision_at", "retention_until", "created_at",
        ]
        extra_kwargs = {
            # Optional at the API because it is nullable at the model: a
            # phone-only candidate is a real candidate. `validate()` below still
            # requires SOME identity key.
            "email": {"required": False, "allow_null": True, "allow_blank": True},
        }

    def validate_consent_given(self, value):
        """
        Consent, on the direct-entry path only.

        This route is someone typing a candidate in and ticking the box on their
        behalf, so consent genuinely is the basis and `False` is refused.

        On UPDATE the rule softens: a candidate acquired by bulk import holds a
        legal basis rather than consent, and forcing an HR user to tick a
        consent box to edit that person's phone number would be asking them to
        assert something untrue. `validate()` enforces the real invariant —
        that SOME lawful basis exists — and the database check constraint
        enforces it against every path, including this one.
        """
        if not value and self.instance is None:
            raise serializers.ValidationError(
                "A candidate record requires recorded consent to process personal data."
            )
        return value

    def validate(self, attrs):
        instance = self.instance

        # Some way to recognise this person again. Without one, every re-import
        # and every duplicate check manufactures a new record — and a candidate
        # nobody can contact is not a candidate.
        email = attrs.get("email", getattr(instance, "email", None))
        phone = attrs.get("phone", getattr(instance, "phone", ""))
        if not (email or "").strip() and not (phone or "").strip():
            raise serializers.ValidationError(
                {"email": "Record either an email address or a phone number."}
            )

        # Mirrors ck_candidate_has_lawful_basis. Stated here so the API answers
        # with a field error rather than letting the database answer with an
        # IntegrityError.
        consent = attrs.get("consent_given", getattr(instance, "consent_given", False))
        basis = attrs.get("legal_basis", getattr(instance, "legal_basis", ""))
        if not consent and not basis:
            raise serializers.ValidationError(
                {
                    "consent_given": (
                        "This candidate has no recorded consent and no other "
                        "lawful basis for processing."
                    )
                }
            )
        return attrs


# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------


class ApplicationListSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    candidate_name = serializers.CharField(source="candidate.full_name", read_only=True)
    job_title = serializers.CharField(source="job_opening.title", read_only=True)
    department_name = serializers.CharField(
        source="job_opening.department.name", read_only=True
    )
    stage_name = serializers.CharField(source="current_stage.name", read_only=True)
    stage_kind = serializers.CharField(source="current_stage.kind", read_only=True)
    allowed_decisions = serializers.JSONField(
        source="current_stage.allowed_decisions", read_only=True
    )
    #: Who may act at the current stage (the workflow's rule, layer 2 of the
    #: engine's authorisation). The UI uses it to offer the decision buttons
    #: only to someone the engine would accept, and to say who that is.
    stage_responsible_role = serializers.CharField(
        source="current_stage.responsible_role.code", read_only=True, default=None
    )
    stage_responsible_role_name = serializers.CharField(
        source="current_stage.responsible_role.name", read_only=True, default=None
    )
    candidate_email = serializers.CharField(source="candidate.email", read_only=True, default="")
    #: The open slot invite for the CURRENT stage, if any: what the candidate
    #: was offered / chose, for the scheduler to confirm.
    slot_invite = serializers.SerializerMethodField()

    def get_slot_invite(self, application):
        invite = next(
            (
                i for i in application.slot_invites.all()
                if i.stage_id == application.current_stage_id
                and i.is_active and i.status in ("pending", "selected")
            ),
            None,
        )
        if invite is None:
            return None
        return {
            "id": str(invite.pk),
            "status": invite.status,
            "options": invite.options,
            "selected_slot": invite.selected_slot or None,
            "selected_at": invite.selected_at,
            "expires_at": invite.expires_at,
            # Staff-facing: the live booking link (to copy/re-send) and how
            # many invites this round has needed (1 = normal, more = rebooked).
            "selection_url": invite.selection_url,
            "invite_count": sum(
                1 for i in application.slot_invites.all()
                if i.stage_id == application.current_stage_id and i.is_active
            ),
        }

    #: Whether an interview exists for the CURRENT stage. The UI gates the
    #: Pass/Reject buttons on it — decisions open only once the round is
    #: actually scheduled. The engine enforces the same rule server-side.
    stage_interview_scheduled = serializers.SerializerMethodField()

    def get_stage_interview_scheduled(self, application):
        return application.interviews.filter(
            stage_id=application.current_stage_id, is_active=True
        ).exclude(status="cancelled").exists()

    #: Whether the CURRENT stage's interviewer has submitted their feedback.
    #: The UI keeps Pass / recommend closed until this is true; the engine
    #: enforces the same rule server-side.
    stage_feedback_submitted = serializers.SerializerMethodField()

    def get_stage_feedback_submitted(self, application):
        return application.interviews.filter(
            stage_id=application.current_stage_id, is_active=True,
            feedback__isnull=False,
        ).exclude(status="cancelled").exists()

    #: What the onboarding form should open pre-filled with, once the offer is
    #: accepted: the candidate's identity and the job/offer placement. HR Head
    #: verifies or corrects each value; the server falls back to these same
    #: sources for anything left untouched.
    conversion_defaults = serializers.SerializerMethodField()

    def get_conversion_defaults(self, application):
        if application.status != ApplicationStatus.OFFER_ACCEPTED:
            return None
        candidate = application.candidate
        job = application.job_opening
        offer = getattr(application, "offer", None)
        return {
            "first_name": candidate.first_name,
            "last_name": candidate.last_name,
            "email": candidate.email or "",
            # The address the candidate applied with is their PERSONAL one.
            "personal_email": candidate.email or "",
            "phone": candidate.phone or "",
            "department": str(job.department_id) if job.department_id else None,
            "designation": str(
                (offer and offer.designation_id) or job.designation_id
            ) if ((offer and offer.designation_id) or job.designation_id) else None,
            "location": str(job.location_id) if job.location_id else None,
            "reporting_manager": str(offer.reporting_manager_id)
            if offer and offer.reporting_manager_id
            else None,
            "date_of_joining": offer.joining_date if offer else None,
        }

    class Meta:
        model = Application
        fields = [
            "id", "candidate", "candidate_name", "job_opening", "job_title",
            "department_name", "current_stage", "stage_name", "stage_kind",
            "allowed_decisions", "status", "is_verified", "applied_at",
            "form_answers", "stage_responsible_role", "stage_responsible_role_name",
            "candidate_email", "slot_invite", "stage_interview_scheduled",
            "stage_feedback_submitted", "conversion_defaults",
        ]


class CandidateNotificationSerializer(serializers.ModelSerializer):
    """
    One row of the candidate's communication history.

    Metadata and the frozen subject: enough for HR to see what went out, when,
    and whether it arrived. The full body is available on the detail row so a
    reviewer can read exactly what the candidate read.
    """

    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    triggered_by_email = serializers.CharField(
        source="triggered_by.email", read_only=True, default=""
    )

    class Meta:
        from apps.recruitment.models import CandidateNotification

        model = CandidateNotification
        fields = [
            "id", "application", "candidate", "job_opening", "kind", "kind_display",
            "recipient_email", "subject", "body_text", "status", "status_display",
            "attempts", "last_attempt_at", "sent_at", "error", "triggered_by_email",
            "created_at",
        ]
        read_only_fields = fields


class RetryNotificationSerializer(serializers.Serializer):
    notification = serializers.UUIDField()


class SlotOptionsSerializer(serializers.Serializer):
    """The windows HR offers the candidate; content is the service's to validate."""

    options = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class InterviewCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class InterviewRejectTimeSerializer(serializers.Serializer):
    """The interviewer's 'this time does not work' — reason mandatory."""

    reason = serializers.CharField(max_length=500)
    #: Optional interviewer-proposed windows [{start, end} ISO]. Omitted →
    #: the standard windows are offered. Validated by the slots service.
    options = serializers.JSONField(required=False, allow_null=True)


class ApplicationCreateSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = ["candidate", "job_opening"]

    def validate(self, attrs):
        # The caller's right to use THIS job opening is checked first, and by
        # the same code that filters the job list. Without it, a principal who
        # could see only their own department's jobs could still attach a
        # candidate to any job whose id they could guess — safe until now only
        # because nobody below Scope.ALL holds APPLICATION/CREATE, which is a
        # fact about the permission matrix rather than about this code.
        #
        # Raises Http404, not 403: confirming the row exists would make this an
        # enumeration oracle over the pipeline.
        job = resolve_job_opening(
            attrs["job_opening"], user=self.context["request"].user
        )
        if not job.workflow.is_published:
            raise serializers.ValidationError(
                {"job_opening": f"'{job.workflow.name}' is a draft workflow."}
            )
        attrs["job_opening"] = job
        return attrs


class ApplicationEventSerializer(serializers.ModelSerializer):
    from_stage_name = serializers.CharField(source="from_stage.name", read_only=True, default=None)
    to_stage_name = serializers.CharField(source="to_stage.name", read_only=True, default=None)

    class Meta:
        model = ApplicationEvent
        fields = [
            "id", "kind", "from_stage_name", "to_stage_name", "decision",
            "actor_label", "note", "detail", "created_at",
        ]


class StageDecisionSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    stage_name = serializers.CharField(source="stage.name", read_only=True)

    class Meta:
        model = StageDecision
        fields = ["id", "stage", "stage_name", "decision", "decided_by", "decided_at", "rationale"]


class CandidateRejectionSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    rejected_by_email = serializers.CharField(source="rejected_by.email", read_only=True)
    stage_name = serializers.CharField(source="rejection_stage.name", read_only=True)

    class Meta:
        model = CandidateRejection
        fields = [
            "id", "rejected_by", "rejected_by_email", "rejected_at",
            "rejection_stage", "stage_name", "reason",
            "department_recommendation", "is_overridden",
        ]


class DecisionOverrideSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    overridden_by_email = serializers.CharField(source="overridden_by.email", read_only=True)
    #: Named rather than id'd: an override that reopens a candidate MOVES them,
    #: and "Rejected → HR Head final decision" is the fact a reader needs.
    previous_stage_name = serializers.CharField(
        source="previous_stage.name", read_only=True, default=None
    )
    new_stage_name = serializers.CharField(source="new_stage.name", read_only=True, default=None)

    class Meta:
        model = DecisionOverride
        fields = [
            "id", "overridden_by", "overridden_by_email", "overridden_at",
            "previous_status", "new_status",
            "previous_stage", "previous_stage_name",
            "new_stage", "new_stage_name",
            "reason",
        ]


# --------------------------------------------------------------------------
# Action payloads
# --------------------------------------------------------------------------


class _ReasonField(serializers.CharField):
    """A justification that is permanently recorded, so it must actually say something."""

    def __init__(self, **kwargs):
        kwargs.setdefault("min_length", MIN_REASON_LENGTH)
        kwargs.setdefault("trim_whitespace", True)
        kwargs.setdefault(
            "error_messages",
            {
                "min_length": (
                    f"A reason of at least {MIN_REASON_LENGTH} characters is required. "
                    f"It is recorded permanently against the candidate."
                )
            },
        )
        super().__init__(**kwargs)


class DecisionSerializer(ScopedRelationsMixin, serializers.Serializer):
    """Payload for any stage decision that is not terminal."""

    decision = serializers.CharField()
    rationale = serializers.CharField(required=False, allow_blank=True, default="")
    interview = serializers.PrimaryKeyRelatedField(
        queryset=Interview.objects.all(), required=False, allow_null=True
    )


class RecommendationSerializer(DecisionSerializer):
    """A department recommendation always carries a rationale."""

    rationale = _ReasonField()


class RejectionSerializer(serializers.Serializer):
    reason = _ReasonField()


class OverrideSerializer(serializers.Serializer):
    new_status = serializers.CharField()
    reason = _ReasonField()


# --------------------------------------------------------------------------
# Interviews
# --------------------------------------------------------------------------


class InterviewSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    candidate_name = serializers.CharField(
        source="application.candidate.full_name", read_only=True
    )
    interviewer_name = serializers.CharField(source="interviewer.full_name", read_only=True)
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    #: The assessment form this interview must be reported on. Included here so
    #: a client can render the feedback form from the interview alone — without
    #: it, resolving the form means fetching every workflow's detail payload and
    #: searching for the stage, which is absurd for a modal.
    stage_feedback_form = serializers.PrimaryKeyRelatedField(
        source="stage.feedback_form", read_only=True
    )
    feedback_submitted = serializers.SerializerMethodField()

    class Meta:
        model = Interview
        fields = [
            "id", "application", "candidate_name", "stage", "stage_name",
            "stage_feedback_form",
            "interviewer", "interviewer_name",
            "scheduled_at", "scheduled_end", "duration_minutes",
            "mode", "location_or_link", "status", "feedback_submitted",
            "calendar_event_id", "calendar_sync_status", "calendar_error",
        ]
        read_only_fields = [
            "scheduled_end", "status",
            "calendar_event_id", "calendar_sync_status", "calendar_error",
        ]

    def get_feedback_submitted(self, obj) -> bool:
        return hasattr(obj, "feedback")


class InterviewScheduleSerializer(ScopedRelationsMixin, serializers.Serializer):
    application = serializers.PrimaryKeyRelatedField(queryset=Application.objects.all())
    stage = serializers.PrimaryKeyRelatedField(queryset=WorkflowStage.objects.all())
    interviewer = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.filter(is_active=True)
    )
    scheduled_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(default=45, min_value=5, max_value=480)
    mode = serializers.CharField(default="in_person")
    location_or_link = serializers.CharField(required=False, allow_blank=True, default="")


class InterviewRescheduleSerializer(serializers.Serializer):
    scheduled_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(
        required=False, allow_null=True, min_value=5, max_value=480
    )


class FeedbackSubmitSerializer(serializers.Serializer):
    answers = serializers.DictField(required=True)
    recommendation = serializers.CharField()
    strengths = serializers.CharField(required=False, allow_blank=True, default="")
    concerns = serializers.CharField(required=False, allow_blank=True, default="")
    overall_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=5
    )


class InterviewFeedbackSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    submitted_by_name = serializers.CharField(source="submitted_by.full_name", read_only=True)
    stage_name = serializers.CharField(source="interview.stage.name", read_only=True)

    class Meta:
        model = InterviewFeedback
        fields = [
            "id", "interview", "stage_name", "form", "submitted_by", "submitted_by_name",
            "submitted_at", "answers", "strengths", "concerns",
            "recommendation", "overall_rating",
        ]


# --------------------------------------------------------------------------
# Offers
# --------------------------------------------------------------------------


class OfferSerializer(ScopedRelationsMixin, serializers.ModelSerializer):
    candidate_name = serializers.CharField(
        source="application.candidate.full_name", read_only=True
    )

    class Meta:
        model = Offer
        fields = [
            "id", "application", "candidate_name", "offered_ctc", "joining_date",
            "valid_until", "designation", "level", "reporting_manager",
            "status", "sent_at", "responded_at", "created_at",
        ]
        read_only_fields = ["status", "sent_at", "responded_at", "created_at"]


class OfferResponseSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ConvertSerializer(ScopedRelationsMixin, serializers.Serializer):
    """
    The onboarding form: what HR Head verifies before the employee is minted.

    Every field is optional — anything omitted falls back to the candidate,
    the job opening or the offer, exactly as before. The role and level are
    deliberately NOT here: they come from the job's target role and the
    offer's level, so the hierarchy pairing cannot be broken by hand.
    """

    first_name = serializers.CharField(required=False, allow_blank=True, max_length=80)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=80)
    #: The COMPANY email the login is created on — the employee's one and
    #: only HRMS login identifier. Defaults to the address the candidate
    #: applied with only when omitted.
    email = serializers.EmailField(required=False, allow_blank=True)
    #: The PERSONAL email, where the credential email is sent. Defaults to
    #: the address the candidate applied with — which IS their personal one.
    personal_email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True), required=False, allow_null=True
    )
    designation = serializers.PrimaryKeyRelatedField(
        queryset=Designation.objects.filter(is_active=True), required=False, allow_null=True
    )
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.filter(is_active=True), required=False, allow_null=True
    )
    reporting_manager = serializers.PrimaryKeyRelatedField(
        queryset=Employee.objects.filter(is_active=True), required=False, allow_null=True
    )
    date_of_joining = serializers.DateField(required=False, allow_null=True)
