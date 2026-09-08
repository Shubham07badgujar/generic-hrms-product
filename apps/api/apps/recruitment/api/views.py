"""
Recruitment API.

Every mutating endpoint is a thin wrapper over a service. The view resolves
objects, validates payload shape, and calls in — it makes no authorization
decision of its own beyond the route-level RBAC gate, because the same services
are reachable from Celery and management commands and must be safe there too.

THE ACTION ROUTES ARE SPLIT BY PERMISSION, NOT BY CONVENIENCE
-------------------------------------------------------------
`advance`, `recommend`, `select`, `reject` and `override` could have been one
endpoint taking a `decision` parameter. They are separate because each maps to
a different (resource, action) pair, and a single route would have to be gated
at the weakest of them. Splitting them lets `manage.py check` and the
permission matrix see exactly who can reach each authority — in particular that
`reject` is HR Head's alone and `override` is Admin's alone.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.fields import DateTimeField
from rest_framework.response import Response

from apps.recruitment.models import (
    Application,
    ApplicationStatus,
    Candidate,
    Interview,
    JobOpening,
    JobStatus,
    Offer,
    OfferStatus,
)
from apps.recruitment.services import hiring, intake, slots
from apps.recruitment.services.engine import (
    build_history_snapshot,
    record_decision,
)
from apps.recruitment.services.interviews import (
    cancel_interview,
    eligible_interviewers,
    find_conflict,
    interviewer_pool_problem,
    reschedule_interview,
    schedule_interview,
    submit_feedback,
)
from apps.recruitment.services import communications
from apps.workflows.models import (
    ADVISORY_DECISIONS,
    TERMINAL_DECISIONS,
    Decision,
    HiringWorkflow,
    FeedbackForm,
    StageKind,
    WorkflowStage,
)
from apps.recruitment.access import pipeline_scope_filter
from core.access import Action, Resource
from apps.workflows import services as workflow_services
from core.access.drf import ScopedModelViewSet, ScopedReadOnlyModelViewSet

from . import serializers as s


class PipelineScopedMixin:
    """
    Scoping for recruitment rows, which hang off a JOB, not a person.

    The generic `scope_queryset()` narrows by walking a path to the owning
    Employee. A candidate has no owning employee — an application belongs to a
    job opening, and the job opening belongs to a department. Those resources
    are registered `person_scoped=False`, which makes the generic engine deny
    anything below `Scope.ALL`, so the narrowing happens here instead:

      ALL         → everything (HR, Admin, CEO)
      DEPARTMENT  → rows whose job sits in the caller's department closure
      TEAM/SELF   → rows the caller is personally assigned to, via `assigned_path`

    SELF is what "assigned candidates only" means for an interviewer: the
    applications they actually have an interview on, and nothing else.
    """

    #: Path to the owning JobOpening's department, as an `__in` lookup.
    department_path: str = "job_opening__department_id__in"
    #: Path from this model to the Employee personally attached to the row.
    #: None means SELF is not expressible here, and resolves to nothing.
    assigned_path: str | None = "interviews__interviewer_id"

    def get_queryset(self):
        # Deliberately NOT super().get_queryset() — that is the generic scoper,
        # which returns none() for these resources by design.
        #
        # The narrowing itself lives in `apps.recruitment.access` so that this
        # list filter and the single-object check used by the services are the
        # same code. When they were two implementations, the object check was
        # simply missing and nobody noticed.
        return pipeline_scope_filter(
            self.queryset,
            self.request.user,
            resource=self.access_resource,
            department_path=self.department_path,
            assigned_path=self.assigned_path,
        )


def _service_call(fn, **kwargs):
    """Run a service, translating its Django ValidationError into a DRF 400."""
    try:
        return fn(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
        raise DRFValidationError(detail) from exc


# ==========================================================================
# Workflow configuration — read-only over HTTP
# ==========================================================================


class HiringWorkflowViewSet(ScopedModelViewSet):
    """
    The pipelines, as data — readable by the forms, authored by the Admin.

    Authoring is PROCESS-shaped, not stage-shaped: the client describes who
    verifies, who conducts each round and who recommends, and the service
    generates the stage/transition graph with its invariants intact. Drafts
    are editable and invisible to job forms; publishing freezes structure,
    because editing a live pipeline mid-hire moves candidates' ground beneath
    them — the concern that once kept this endpoint read-only, now enforced
    as a rule rather than an absence.
    """

    access_resource = Resource.HIRING_WORKFLOW
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    access_actions = {"publish": Action.EDIT}

    def create(self, request, *args, **kwargs):
        payload = s.WorkflowAuthorSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = dict(payload.validated_data)
        rounds = [
            {
                "name": r.get("name", ""),
                "role": r["role"],
                "feedback_form": r["feedback_form"].pk if r.get("feedback_form") else None,
            }
            for r in data.pop("interview_rounds")
        ]
        workflow = _service_call(
            workflow_services.create_workflow,
            actor=request.user,
            interview_rounds=rounds,
            **data,
        )
        return Response(s.HiringWorkflowSerializer(workflow).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        workflow = self.get_object()
        payload = s.WorkflowAuthorSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        data = dict(payload.validated_data)
        kwargs_for_service = {}
        for field in ("name", "description", "department_kind", "verification_role"):
            if field in data:
                kwargs_for_service[field] = data[field]
        if "interview_rounds" in data:
            kwargs_for_service["interview_rounds"] = [
                {
                    "name": r.get("name", ""),
                    "role": r["role"],
                    "feedback_form": r["feedback_form"].pk if r.get("feedback_form") else None,
                }
                for r in data["interview_rounds"]
            ]
        if "recommendation_role" in data:
            kwargs_for_service["recommendation_role"] = data["recommendation_role"] or None
        workflow = _service_call(
            workflow_services.update_workflow,
            actor=request.user,
            workflow=workflow,
            **kwargs_for_service,
        )
        return Response(s.HiringWorkflowSerializer(workflow).data)

    def perform_destroy(self, instance):
        _service_call(
            workflow_services.deactivate_workflow, actor=self.request.user, workflow=instance
        )

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        workflow = _service_call(
            workflow_services.publish_workflow, actor=request.user, workflow=self.get_object()
        )
        return Response(s.HiringWorkflowSerializer(workflow).data)
    queryset = (
        HiringWorkflow.objects.filter(is_active=True)
        .prefetch_related(
            "stages__responsible_role",
            "stages__feedback_form",
            "stages__outgoing_transitions__to_stage",
        )
        .order_by("name")
    )
    serializer_class = s.HiringWorkflowListSerializer

    def get_serializer_class(self):
        if self.action == "retrieve":
            return s.HiringWorkflowSerializer
        return s.HiringWorkflowListSerializer


class FeedbackFormViewSet(ScopedReadOnlyModelViewSet):
    access_resource = Resource.HIRING_WORKFLOW
    queryset = FeedbackForm.objects.filter(is_active=True).prefetch_related("fields")
    serializer_class = s.FeedbackFormSerializer


# ==========================================================================
# Jobs
# ==========================================================================


class JobOpeningViewSet(PipelineScopedMixin, ScopedModelViewSet):
    access_resource = Resource.JOB_OPENING
    department_path = "department_id__in"
    #: A recruiter is 'assigned' to the jobs they own; an interviewer to the
    #: jobs whose applications they are booked to interview. Both must be able
    #: to open the job they are working — the second path is what lets a
    #: cross-department interviewer read the vacancy their round belongs to.
    assigned_path = ("recruiter_id", "applications__interviews__interviewer_id")

    queryset = (
        JobOpening.objects.select_related(
            "workflow", "department", "designation", "location", "level", "target_role"
        )
        .filter(is_active=True)
        .annotate(application_count=Count("applications"))
    )
    serializer_class = s.JobOpeningSerializer
    filterset_fields = ["status", "department", "workflow", "employment_type"]
    search_fields = ["title"]
    ordering_fields = ["created_at", "title"]

    access_actions = {"publish": Action.EDIT, "close": Action.EDIT}

    def perform_create(self, serializer):
        """
        A job gets its Google Form the moment it exists, so the link is ready
        to share as soon as it is published. Built after commit; never blocks
        the create; the hosted /apply/<token> page exists regardless.
        """
        job = serializer.save()
        from django.db import transaction as _tx
        from apps.recruitment.services.external_forms import create_external_form

        _tx.on_commit(lambda: create_external_form(job))

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        job = self.get_object()
        if job.status != JobStatus.DRAFT:
            raise DRFValidationError({"status": "Only a draft job can be published."})
        job.status = JobStatus.PUBLISHED
        job.published_at = timezone.now()
        job.save(update_fields=["status", "published_at", "updated_at"])
        # The external form is built once the publish has committed, and never
        # blocks it: a Google outage leaves the job published with the hosted
        # link and a note in `external_form_error`.
        from django.db import transaction as _tx
        from apps.recruitment.services.external_forms import create_external_form

        _tx.on_commit(lambda: create_external_form(job))
        job.refresh_from_db()
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        job = self.get_object()
        job.status = JobStatus.CLOSED
        job.closed_at = timezone.now()
        job.save(update_fields=["status", "closed_at", "updated_at"])
        return Response(self.get_serializer(job).data)


# ==========================================================================
# Candidates
# ==========================================================================


class CandidateViewSet(PipelineScopedMixin, ScopedModelViewSet):
    """
    A candidate is visible through the applications you can see.

    One person may apply to several jobs in several departments, so a
    department head sees a candidate only while that candidate has an
    application in their department — not because the person exists.
    """

    access_resource = Resource.CANDIDATE
    department_path = "applications__job_opening__department_id__in"
    assigned_path = "applications__interviews__interviewer_id"
    queryset = Candidate.objects.filter(is_active=True)
    serializer_class = s.CandidateSerializer
    search_fields = ["first_name", "last_name", "email", "phone"]
    ordering_fields = ["created_at", "first_name"]

    access_actions = {"history": Action.VIEW, "resume": Action.VIEW}

    @action(detail=True, methods=["get"])
    def resume(self, request, pk=None):
        """
        GET /candidates/<id>/resume/ — the uploaded résumé, as an attachment.

        Reached through get_object(), so whoever may see the candidate may read
        their résumé and nobody else can; media is never served directly.
        """
        from django.http import FileResponse, Http404

        candidate = self.get_object()
        if not candidate.resume:
            raise Http404("No résumé is on record for this candidate.")
        return FileResponse(
            candidate.resume.open("rb"), as_attachment=True,
            filename=candidate.resume.name.split("/")[-1],
        )

    def perform_create(self, serializer):
        # Through the service, so the importer and this form share one path.
        # `consent_at` is stamped here because this route IS the direct-consent
        # route: someone typed the candidate in and ticked the box on their
        # behalf. Bulk import records a legal basis instead, and must not reuse
        # this stamp.
        candidate = _service_call(
            intake.create_candidate,
            actor=self.request.user,
            consent_at=timezone.now(),
            **serializer.validated_data,
        )
        serializer.instance = candidate

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """Everything recorded about this candidate, across every application."""
        candidate = self.get_object()
        applications = Application.objects.filter(candidate=candidate).select_related(
            "job_opening", "current_stage"
        )
        # The SAME shape per application as /applications/<id>/history/, so the
        # candidate page and the application page read one contract. Returning
        # the bare snapshot here (no id, status or events) is what left the
        # profile page reading `.events.length` of nothing.
        return Response(
            {
                "candidate": s.CandidateSerializer(candidate).data,
                "applications": [
                    _application_history_payload(application) for application in applications
                ],
            }
        )


def _application_history_payload(application) -> dict:
    """The complete journey of one application: snapshot + events + rejection + overrides."""
    rejection = getattr(application, "rejection", None)
    return {
        "id": str(application.pk),
        **build_history_snapshot(application),
        "status": application.status,
        "current_stage": application.current_stage.name,
        "events": s.ApplicationEventSerializer(
            application.events.select_related("from_stage", "to_stage").order_by("created_at"),
            many=True,
        ).data,
        "rejection": s.CandidateRejectionSerializer(rejection).data if rejection else None,
        "overrides": s.DecisionOverrideSerializer(application.overrides.all(), many=True).data,
    }


# ==========================================================================
# Applications — the workflow surface
# ==========================================================================


class ApplicationViewSet(PipelineScopedMixin, ScopedModelViewSet):
    access_resource = Resource.APPLICATION

    queryset = (
        Application.objects.select_related(
            "candidate", "job_opening__department", "current_stage__responsible_role"
        )
        .prefetch_related("slot_invites")
        .filter(is_active=True)
    )
    serializer_class = s.ApplicationListSerializer
    #: The job-specific candidate list filters on these. Ranges on applied
    #: date and experience are what "applied this month" and "3+ years" mean
    #: as query strings.
    filterset_fields = {
        "status": ["exact"],
        "job_opening": ["exact"],
        "job_opening__location": ["exact"],
        "current_stage": ["exact"],
        "current_stage__kind": ["exact"],
        "is_verified": ["exact"],
        "applied_at": ["gte", "lte", "date"],
        "candidate__total_experience_years": ["gte", "lte"],
    }
    search_fields = ["candidate__first_name", "candidate__last_name", "candidate__email", "candidate__phone"]
    ordering_fields = ["applied_at", "candidate__first_name"]
    http_method_names = ["get", "post", "head", "options"]

    #: Each action's real authority. `reject` is REJECT, which only HR Head
    #: holds; `override` is OVERRIDE, which only Admin holds. Neither is
    #: reachable by anyone else even with a hand-built request.
    access_actions = {
        "history": Action.VIEW,
        "notifications": Action.VIEW,
        "retry_notification": Action.EDIT,
        "pending_hr_decision": Action.VIEW,
        #: VIEW, deliberately weaker than the act: the decision's REAL
        #: authority depends on the stage and the decision (a pass at an
        #: interview round is the interviewer's INTERVIEW_FEEDBACK/CREATE, a
        #: verification is APPLICATION/EDIT) and the ENGINE resolves and
        #: enforces exactly that, plus the workflow's stage role. Demanding
        #: EDIT here blocked every pure interviewer role from recording the
        #: pass the engine was built to accept from them.
        "advance": Action.VIEW,
        "recommend": Action.RECOMMEND,
        "select": Action.APPROVE,
        "reject": Action.REJECT,
        "override": Action.OVERRIDE,
        "convert": Action.CREATE,
        "slot_invite": Action.CREATE,
    }
    #: A recommendation is a write on DEPARTMENT_DECISION; a conversion is a
    #: write on EMPLOYEE; configuring interview slots is scheduling authority,
    #: a write on INTERVIEW. Gating any of them against APPLICATION would
    #: check the wrong permission.
    access_resources = {
        "recommend": Resource.DEPARTMENT_DECISION,
        "convert": Resource.EMPLOYEE,
        "slot_invite": Resource.INTERVIEW,
    }

    def get_serializer_class(self):
        if self.action == "create":
            return s.ApplicationCreateSerializer
        return s.ApplicationListSerializer

    def perform_create(self, serializer):
        # Through the service, not around it. The bulk importer creates
        # applications too, and two creation paths would drift.
        data = serializer.validated_data
        application = _service_call(
            intake.create_application,
            actor=self.request.user,
            candidate=data["candidate"],
            job_opening=data["job_opening"],
        )
        serializer.instance = application

    # -------------------------------------------------- reads

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """
        The complete journey: every stage, interview, decision and override.

        `build_history_snapshot` supplies the same view that gets FROZEN into a
        rejection record, so what HR Head reads here is exactly what the
        justification will preserve. The event stream, rejection and overrides
        are added on top, because those continue after the snapshot is taken.
        """
        return Response(_application_history_payload(self.get_object()))

    @action(detail=True, methods=["get"])
    def notifications(self, request, pk=None):
        """
        Everything the candidate has been emailed about THIS application.

        Reached through `get_object()`, so it is scoped exactly like the
        application itself: someone who cannot see the application cannot see
        what its candidate was told.
        """
        application = self.get_object()
        rows = application.candidate_notifications.select_related("triggered_by").order_by(
            "-created_at"
        )
        return Response(s.CandidateNotificationSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"], url_path="retry-notification")
    def retry_notification(self, request, pk=None):
        """Try a failed candidate email again. Requires EDIT on the application."""
        application = self.get_object()
        payload = s.RetryNotificationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        row = application.candidate_notifications.filter(
            pk=payload.validated_data["notification"]
        ).select_related("candidate", "triggered_by").first()
        if row is None:
            raise DRFValidationError({"notification": "No such notification on this application."})
        row = _service_call(communications.retry, notification=row, actor=request.user)
        return Response(s.CandidateNotificationSerializer(row).data)

    @action(detail=False, methods=["get"], url_path="pending-hr-decision")
    def pending_hr_decision(self, request):
        """HR Head's queue: everything sitting at a final-decision stage."""
        queryset = self.filter_queryset(
            self.get_queryset().filter(
                current_stage__is_final_hr_decision=True,
                status=ApplicationStatus.ACTIVE,
            )
        )
        page = self.paginate_queryset(queryset)
        serializer = s.ApplicationListSerializer(page or queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @action(detail=True, methods=["post"], url_path="slot-invite")
    def slot_invite(self, request, pk=None):
        """
        Configure the current round's available times and send the candidate
        the booking link. This is the ONLY way a slot-selection link goes out:
        arriving at an interview stage sends nothing until someone with
        scheduling authority sets the times here.
        """
        application = self.get_object()
        payload = s.SlotOptionsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invite = _service_call(
            slots.configure_slot_invite,
            application=application,
            actor=request.user,
            options=payload.validated_data["options"],
        )
        return Response(
            {
                "id": str(invite.pk),
                "status": invite.status,
                "options": invite.options,
                "expires_at": invite.expires_at,
                "selection_url": invite.selection_url,
            },
            status=status.HTTP_201_CREATED,
        )

    # -------------------------------------------------- stage movement

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        """
        Ordinary forward movement: pass, verify, request more information.

        Explicitly refuses advisory and terminal decisions so this route cannot
        be used to reach an authority it was not gated for.
        """
        payload = s.DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        decision = payload.validated_data["decision"]

        if decision in TERMINAL_DECISIONS:
            raise DRFValidationError(
                {"decision": "A final decision is made at /select/ or /reject/."}
            )
        if decision in ADVISORY_DECISIONS:
            # An interviewer's recommend_reject is that ROUND's own verdict and
            # rides this route — the engine resolves its real authority as
            # INTERVIEW_FEEDBACK/CREATE, exactly like their pass. A department
            # recommendation keeps its own route and its RECOMMEND gate.
            if self.get_object().current_stage.kind != StageKind.INTERVIEW:
                raise DRFValidationError(
                    {"decision": "A department recommendation is made at /recommend/."}
                )
        return self._decide(payload.validated_data)

    @action(detail=True, methods=["post"])
    def recommend(self, request, pk=None):
        """
        The department's advisory decision. Never terminal.

        A Department Head recommending rejection routes the application to HR
        Head — it does not reject anyone. That is the whole point of the
        two-level design, and it is enforced by the transition table, not here.
        """
        payload = s.RecommendationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data["decision"] not in ADVISORY_DECISIONS:
            raise DRFValidationError(
                {"decision": "This route records recommendations only."}
            )
        return self._decide(payload.validated_data)

    @action(detail=True, methods=["post"])
    def select(self, request, pk=None):
        """HR Head's final selection."""
        return self._decide({"decision": Decision.SELECT, "rationale": ""})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """
        HR HEAD ONLY. The terminal rejection, with a permanently recorded reason.

        Department heads reach 403 here through the permission matrix; Admin
        reaches 403 too, because Admin holds OVERRIDE, not REJECT.
        """
        payload = s.RejectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._decide(
            {"decision": Decision.REJECT, "rationale": payload.validated_data["reason"]}
        )

    @action(detail=True, methods=["post"])
    def override(self, request, pk=None):
        """ADMIN ONLY. The exceptional override — a separate record, always audited."""
        payload = s.OverrideSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        result = _service_call(
            hiring.override_decision,
            application=self.get_object(),
            actor=request.user,
            new_status=payload.validated_data["new_status"],
            reason=payload.validated_data["reason"],
        )
        return Response(s.DecisionOverrideSerializer(result).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        """Candidate → Employee, atomically, through the shared creation service."""
        payload = s.ConvertSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        data = payload.validated_data
        result = _service_call(
            hiring.convert_to_employee,
            application=self.get_object(),
            actor=request.user,
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            email=data.get("email"),
            personal_email=data.get("personal_email"),
            phone=data.get("phone"),
            department=data.get("department"),
            designation=data.get("designation"),
            location=data.get("location"),
            reporting_manager=data.get("reporting_manager"),
            date_of_joining=data.get("date_of_joining"),
        )
        return Response(
            {
                "employee_id": str(result.employee.pk),
                "employee_code": result.employee.employee_code,
                "user_id": str(result.user.pk),
                "role": result.role.code,
                "temporary_password": result.temporary_password,
            },
            status=status.HTTP_201_CREATED,
        )

    def _decide(self, data: dict) -> Response:
        application = self.get_object()
        result = _service_call(
            record_decision,
            application=application,
            actor=self.request.user,
            decision=data["decision"],
            rationale=data.get("rationale", ""),
            interview=data.get("interview"),
        )
        application.refresh_from_db()
        return Response(
            {
                "application": s.ApplicationListSerializer(application).data,
                "from_stage": result.from_stage.name,
                "to_stage": result.to_stage.name if result.to_stage else None,
                "decision": data["decision"],
            }
        )


# ==========================================================================
# Interviews
# ==========================================================================


class InterviewViewSet(ScopedModelViewSet):
    """
    Scoped by INTERVIEWER, via the registry.

    That is what "assigned candidates only" means concretely: a Clinic Doctor
    at SELF scope sees the interviews booked with them and nothing else.
    """

    access_resource = Resource.INTERVIEW
    queryset = (
        Interview.objects.select_related(
            "application__candidate", "interviewer", "stage__feedback_form"
        )
        .filter(is_active=True)
    )
    serializer_class = s.InterviewSerializer
    filterset_fields = ["status", "interviewer", "application", "stage"]
    ordering_fields = ["scheduled_at"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "check_conflict": Action.VIEW,
        "eligible_interviewers": Action.CREATE,
        "reschedule": Action.EDIT,
        "cancel": Action.EDIT,
        "reject_time": Action.EDIT,
        "retry_calendar": Action.EDIT,
        "feedback": Action.CREATE,
    }
    #: Feedback is its own resource: an interviewer may write feedback without
    #: holding the right to schedule interviews.
    access_resources = {"feedback": Resource.INTERVIEW_FEEDBACK}

    def create(self, request, *args, **kwargs):
        """
        POST /interviews/ — schedule, refusing any overlap for the interviewer.

        `SchedulingConflict` is a ValidationError, so a clash returns 400 with
        the clashing interview named, rather than a 500 from the database
        constraint underneath.
        """
        payload = s.InterviewScheduleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        interview = _service_call(
            schedule_interview, actor=request.user, **payload.validated_data
        )
        return Response(
            s.InterviewSerializer(interview).data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["get"], url_path="check-conflict")
    def check_conflict(self, request):
        """
        Layer 3 of double-booking prevention: the live check the form calls.

        Advisory only — it tells the UI what will happen. The service and the
        database still decide, because between this call and the submit the
        slot may be taken.
        """
        from apps.employees.models import Employee

        interviewer_id = request.query_params.get("interviewer")
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if not (interviewer_id and start and end):
            raise DRFValidationError(
                {"detail": "interviewer, start and end are all required."}
            )

        field = DateTimeField()
        interviewer = Employee.objects.filter(pk=interviewer_id).first()
        if interviewer is None:
            raise DRFValidationError({"interviewer": "Unknown interviewer."})

        clash = find_conflict(
            interviewer=interviewer,
            start=field.to_internal_value(start),
            end=field.to_internal_value(end),
            exclude_pk=request.query_params.get("exclude"),
        )
        return Response(
            {
                "conflict": clash is not None,
                "interview": s.InterviewSerializer(clash).data if clash else None,
            }
        )

    @action(detail=False, methods=["get"], url_path="eligible-interviewers")
    def eligible_interviewers(self, request):
        """
        Who may take a given round — the scheduling form's source of truth.

        Deliberately NOT the employee list: that is scoped to what the caller
        may see across the organisation, so a round whose interviewer sits in
        another department vanished from the picker and the round could not
        be booked at all. Eligibility to interview is a property of the STAGE
        (its role) and the person (still employed), not of the scheduler's
        directory reach — and whoever may schedule interviews may see the
        people they are allowed to schedule.

        `problem` is filled when the list is empty, saying which of the two
        fixable situations it is.
        """
        stage_id = request.query_params.get("stage")
        if not stage_id:
            raise DRFValidationError({"stage": "A stage id is required."})
        stage = WorkflowStage.objects.filter(pk=stage_id).select_related(
            "responsible_role"
        ).first()
        if stage is None:
            raise DRFValidationError({"stage": "Unknown stage."})

        rows = [
            {
                "id": str(e.pk),
                "full_name": e.full_name,
                "employee_code": e.employee_code,
                "department_name": e.department.name if e.department_id else "",
                "designation_title": e.designation.title if e.designation_id else "",
                "status": e.status,
            }
            for e in eligible_interviewers(stage)
        ]
        return Response({
            "stage": str(stage.pk),
            "stage_name": stage.name,
            "role_code": stage.responsible_role.code if stage.responsible_role_id else None,
            "role_name": stage.responsible_role.name if stage.responsible_role_id else "",
            "data": rows,
            "problem": interviewer_pool_problem(stage),
        })

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        payload = s.InterviewRescheduleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        interview = _service_call(
            reschedule_interview,
            interview=self.get_object(),
            actor=request.user,
            scheduled_at=payload.validated_data["scheduled_at"],
            duration_minutes=payload.validated_data.get("duration_minutes"),
        )
        return Response(s.InterviewSerializer(interview).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payload = s.InterviewCancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        interview = _service_call(
            cancel_interview,
            interview=self.get_object(),
            actor=request.user,
            reason=payload.validated_data.get("reason", ""),
        )
        return Response(s.InterviewSerializer(interview).data)

    @action(detail=True, methods=["post"], url_path="reject-time")
    def reject_time(self, request, pk=None):
        """
        The interviewer rejects the TIME, not the candidate: interview
        cancelled, calendar event withdrawn, old booking links dead, and the
        candidate automatically re-invited to pick a new slot.
        """
        from apps.recruitment.services.interviews import reject_interview_time

        payload = s.InterviewRejectTimeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        interview = _service_call(
            reject_interview_time,
            interview=self.get_object(),
            actor=request.user,
            reason=payload.validated_data["reason"],
            options=payload.validated_data.get("options") or None,
        )
        return Response(s.InterviewSerializer(interview).data)

    @action(detail=True, methods=["post"], url_path="retry-calendar")
    def retry_calendar(self, request, pk=None):
        """Retry a failed Google Calendar sync. Never raises past the row."""
        from apps.recruitment.services.calendar import retry_sync

        interview = self.get_object()
        retry_sync(interview, actor=request.user)
        interview.refresh_from_db()
        return Response(s.InterviewSerializer(interview).data)

    @action(detail=True, methods=["post"])
    def feedback(self, request, pk=None):
        payload = s.FeedbackSubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        result = _service_call(
            submit_feedback,
            interview=self.get_object(),
            actor=request.user,
            **payload.validated_data,
        )
        return Response(
            s.InterviewFeedbackSerializer(result).data, status=status.HTTP_201_CREATED
        )


# ==========================================================================
# Offers
# ==========================================================================


class OfferViewSet(ScopedModelViewSet):
    access_resource = Resource.OFFER
    queryset = Offer.objects.select_related("application__candidate").filter(is_active=True)
    serializer_class = s.OfferSerializer
    filterset_fields = ["status", "application"]
    http_method_names = ["get", "post", "head", "options"]

    access_actions = {
        "send": Action.EDIT,
        "respond": Action.EDIT,
        # A different rendering of an offer the caller may already see.
        "letter": Action.VIEW,
    }

    @action(detail=True, methods=["get"])
    def letter(self, request, pk=None):
        """
        The offer letter PDF.

        A SENT offer serves the frozen letter the candidate actually
        received; a draft renders a live preview from today's data and
        letterhead, so HR sees exactly what Send Offer will produce.
        """
        from django.http import HttpResponse

        from apps.recruitment.letters import offer_letter_pdf

        offer = self.get_object()
        if offer.letter_pdf:
            content = offer.letter_pdf.read()
            filename = f"Offer Letter - {offer.application.candidate.full_name}.pdf"
        else:
            content, filename = offer_letter_pdf(offer)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def create(self, request, *args, **kwargs):
        payload = s.OfferSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        offer = _service_call(
            hiring.create_offer,
            application=data["application"],
            actor=request.user,
            offered_ctc=data["offered_ctc"],
            joining_date=data["joining_date"],
            designation=data.get("designation"),
            level=data.get("level"),
            reporting_manager=data.get("reporting_manager"),
            valid_until=data.get("valid_until"),
        )
        return Response(s.OfferSerializer(offer).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        offer = _service_call(hiring.send_offer, offer=self.get_object(), actor=request.user)
        return Response(s.OfferSerializer(offer).data)

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        payload = s.OfferResponseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        offer = _service_call(
            hiring.record_offer_response,
            offer=self.get_object(),
            actor=request.user,
            accepted=payload.validated_data["accepted"],
            note=payload.validated_data.get("note", ""),
        )
        return Response(s.OfferSerializer(offer).data)
