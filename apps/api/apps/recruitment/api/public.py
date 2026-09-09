"""
The public face of a job opening: /api/v1/public/apply/<token>/.

Anonymous by design. The token IS the authorisation — it resolves to exactly
one job, it is unguessable, and it grants exactly one thing: the right to
submit an application to that job while it is published. Nothing about the
pipeline is readable through here beyond the posting itself and the questions
it asks; a submitter learns their reference number and nothing else.

Rate-limited per calling address, on top of the DRF-wide anonymous throttle,
because it is the only unauthenticated write in the API.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.middleware import acting_as

from apps.recruitment.services import public_intake


class PublicApplicationSerializer(serializers.Serializer):
    submission_id = serializers.CharField(max_length=128)
    answers = serializers.DictField()
    consent = serializers.BooleanField()
    resume = serializers.FileField(required=False, allow_null=True)


def _normalise_multipart(data):
    """
    The hosted form posts multipart when a résumé is attached: `answers` then
    arrives as a JSON string and `consent` as "true". Turn both back into what
    the JSON path sends, so one serializer serves both.
    """
    import json

    if hasattr(data, "getlist"):  # QueryDict
        flat = {k: data.get(k) for k in data.keys()}
        raw = flat.get("answers")
        if isinstance(raw, str):
            try:
                flat["answers"] = json.loads(raw)
            except ValueError:
                flat["answers"] = None
        consent = flat.get("consent")
        if isinstance(consent, str):
            flat["consent"] = consent.strip().lower() in ("1", "true", "yes", "on")
        if "resume" in data and hasattr(data, "get"):
            pass
        return flat
    return data


class PublicApplyView(APIView):
    """GET: the posting and its questions. POST: apply."""

    #: Genuinely public — see module docstring. `access.E001` demands this be
    #: said out loud rather than left implicit.
    access_exempt = True
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_apply"
    # JSON from the form without a file; multipart with one.
    from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, token: str):
        job = public_intake.job_for_token(token)
        return Response(public_intake.public_job_summary(job))

    def post(self, request, token: str):
        job = public_intake.job_for_token(token)
        incoming = _normalise_multipart(request.data)
        # The token names one job opening, and that job belongs to exactly one
        # organization -- so the candidate does too. Bound explicitly because
        # this route is `access_exempt`: RBACPermission short-circuits, the
        # access layer never resolves a context, and nothing else would give
        # these writes an owner. An anonymous applicant has no principal to
        # derive one from, which is precisely why the TOKEN has to carry it.
        if hasattr(request, "FILES") and request.FILES.get("resume") is not None:
            incoming = {**incoming, "resume": request.FILES["resume"]}
        payload = PublicApplicationSerializer(data=incoming)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            with acting_as(None, organization=job.organization_id):
                result = public_intake.public_apply(
                    job=job,
                    submission_id=data["submission_id"],
                    answers=data["answers"],
                    consented=data["consent"],
                    source="hosted_form",
                    remote_meta={"user_agent": request.META.get("HTTP_USER_AGENT", "")[:200]},
                    resume_file=data.get("resume"),
                )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise DRFValidationError(detail) from exc

        # The reference and nothing else. Whether this person was already known,
        # or had already applied, is the pipeline's business — not something the
        # open internet gets to learn by re-submitting a form.
        return Response(
            {"reference": result.reference, "job_title": job.title},
            status=status.HTTP_201_CREATED if result.created_application else status.HTTP_200_OK,
        )


class SlotSelectionSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=64)
    end = serializers.CharField(max_length=64)


class PublicSlotView(APIView):
    """
    GET: the round and its offered times. POST: the candidate's choice.

    Anonymous like the application form, and as narrow: the token names one
    invite, and the server only accepts a choice from what it offered.
    """

    access_exempt = True
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "public_apply"

    def get(self, request, token: str):
        from apps.recruitment.services import slots

        invite = slots.invite_for_token(token)
        return Response(slots.public_invite_summary(invite))

    def post(self, request, token: str):
        from apps.recruitment.services import slots

        invite = slots.invite_for_token(token)
        payload = SlotSelectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            # Same as the application form above: anonymous route, so the token
            # is the only thing that knows which organization this belongs to.
            with acting_as(None, organization=invite.organization_id):
                invite = slots.select_slot(
                    invite=invite,
                    start=payload.validated_data["start"],
                    end=payload.validated_data["end"],
                )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise DRFValidationError(detail) from exc
        return Response(
            {"status": invite.status, "selected": invite.selected_slot},
            status=status.HTTP_200_OK,
        )
