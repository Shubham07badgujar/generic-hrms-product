"""
Uniform error envelope.

Every error response is `{"error": {"code", "message", "details"}}` so the SPA
has exactly one shape to handle.

The `code` is a stable machine string the frontend branches on; `message` is
human text that may change without breaking clients.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("hrms.access")

ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "authentication_required",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


class ConflictError(exceptions.APIException):
    """409 — the request is valid but conflicts with current state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This conflicts with the current state of the resource."
    default_code = "conflict"


class BusinessRuleError(exceptions.APIException):
    """
    422 — syntactically valid but refused by a domain rule.

    Distinct from 400 so the SPA can tell "you typed something wrong" from
    "the system will not allow this" — e.g. rejecting a candidate without a
    reason, or approving a payroll run that is not in review.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "This action is not permitted by a business rule."
    default_code = "business_rule_violation"


def exception_handler(exc, context):
    # Map Django-native exceptions onto DRF equivalents so services can raise
    # plain Django exceptions without knowing they are behind an API.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied(str(exc) or None)
    elif isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(
            exc.message_dict if hasattr(exc, "message_dict") else exc.messages
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled — let Django's 500 machinery and Sentry take it. Never
        # swallow it into a tidy envelope; that hides real bugs.
        return None

    detail = response.data
    code = getattr(exc, "default_code", None) or ERROR_CODES.get(
        response.status_code, "error"
    )
    # A permission class may name a more specific code than the exception's
    # default — `PasswordChangeRequired` says "password_change_required" so the
    # SPA can route to the change screen rather than show a generic refusal.
    # DRF carries it on the exception's detail; surface it when it is a single
    # specific string, and fall back to the default otherwise.
    if isinstance(exc, exceptions.PermissionDenied):
        specific = exc.get_codes()
        if isinstance(specific, str) and specific != exceptions.PermissionDenied.default_code:
            code = specific

    if isinstance(detail, dict) and "detail" in detail and len(detail) == 1:
        message, details = str(detail["detail"]), None
    elif isinstance(detail, dict):
        message, details = "Validation failed.", detail
    elif isinstance(detail, list):
        message, details = "Validation failed.", {"non_field_errors": detail}
    else:
        message, details = str(detail), None

    payload = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details

    request = context.get("request")
    request_id = getattr(request, "request_id", None)
    if request_id:
        payload["error"]["request_id"] = str(request_id)

    response.data = payload
    return response
