"""
Employee documents.

Two rules shape this module:

  1. UPLOAD AND VERIFICATION ARE DIFFERENT ACTS. An employee uploading their
     own PAN card does not make it verified; verification is a separate
     permission, performed by someone else, and recorded with their name.

  2. FILES ARE NEVER PUBLICLY ADDRESSABLE. The API returns a document's
     METADATA, and the bytes are served by an authorising view that re-checks
     scope on every request. A storage URL in a JSON response would outlive the
     session that fetched it and be shareable by anyone who saw it.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.employees.models import (
    Employee,
    EmployeeDocument,
    VerificationStatus,
)
from apps.notifications import events
from core.access import Action, Resource, can, require
from core.validators import validate_upload

#: Documents are identity papers and certificates, not arbitrary uploads.
#:
#: Extensions, not content types. The previous version of this check read the
#: client-supplied multipart `Content-Type` header and skipped itself entirely
#: when that header was absent — so an unlabelled upload of anything at all was
#: accepted. `core.validators` decides from the extension and the bytes, and
#: records what the client claimed without trusting it.
ALLOWED_EXTENSIONS = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".webp"}
)
MAX_FILE_BYTES = 10 * 1024 * 1024

#: A rejection has to tell someone what to fix, which takes more than a line
#: and less than an essay. Counted in WORDS because that is the unit the policy
#: is written in. The frontend counts too, for a live hint; this count is the
#: authoritative one, because the frontend is a convenience and not a control.
MAX_REJECTION_WORDS = 250


class DocumentError(ValidationError):
    """A refused document operation."""


def count_words(text: str) -> int:
    """Words after collapsing whitespace — `str.split()` folds tabs and newlines."""
    return len(text.split())


def employees_a_user_may_file_against(user):
    """
    Whose file this user may add a document to.

    The upload route used to ask a DIFFERENT question — "which employees may
    you see?" — and treated the answer as permission to write. Those are not
    the same authority. A senior doctor can see their department's roster,
    which quietly let them attach a document to a colleague's permanent record;
    reading a roster is not the right to write to someone's file.

    Both questions are answered by the same scope engine. This one asks the
    right one: the EMPLOYEE_DOCUMENT/CREATE reach, walked over Employee rows.
    Someone holding it at SELF reaches exactly themselves, which is what
    "upload my own PAN card" needs and all it needs.
    """
    from core.access.engine import scope_queryset

    return scope_queryset(
        Employee.objects.all(),
        user,
        resource=Resource.EMPLOYEE,
        action=Action.VIEW,
        scope=can(user, Resource.EMPLOYEE_DOCUMENT, Action.CREATE),
    )


@transaction.atomic
def upload_document(
    *,
    employee: Employee,
    actor,
    document_type,
    file,
    issue_date=None,
    expires_on=None,
    notes: str = "",
) -> EmployeeDocument:
    """
    Record a document against an employee.

    Always lands in PENDING. There is no path that uploads something already
    verified, because that would let the uploader vouch for themselves.
    """
    require(actor, Resource.EMPLOYEE_DOCUMENT, Action.CREATE)

    facts = validate_upload(
        file,
        allowed_extensions=ALLOWED_EXTENSIONS,
        max_bytes=MAX_FILE_BYTES,
        subject="document",
    )

    if document_type.requires_expiry and not expires_on:
        raise DocumentError(
            {"expires_on": f"A {document_type.name} must record its expiry date."}
        )

    # The target must be someone this actor may FILE AGAINST, not merely
    # someone they can see. Checked here rather than only in the view, because
    # a management command or a shell reaches this function directly.
    if not employees_a_user_may_file_against(actor).filter(pk=employee.pk).exists():
        raise DocumentError(
            {"employee": "You may not add documents to this employee's file."}
        )

    document = EmployeeDocument.objects.create(
        employee=employee,
        document_type=document_type,
        file=file,
        original_filename=facts.original_name,
        content_type=facts.declared_content_type,
        size_bytes=facts.size_bytes,
        uploaded_by=actor,
        status=VerificationStatus.PENDING,
        issue_date=issue_date,
        expires_on=expires_on,
        notes=notes,
    )

    _link_onboarding_item(document, actor=actor)

    # AFTER COMMIT, not inline. Tied to the transaction, so a rolled-back
    # upload never announces itself — but outside it, so a busy notification
    # table can never fail somebody's upload. `notify()` swallows its own
    # errors on top of that.
    transaction.on_commit(lambda: events.document_uploaded(document))

    return document


def _link_onboarding_item(document: EmployeeDocument, *, actor) -> None:
    """
    Satisfy the matching onboarding line, if one is waiting.

    Marked SUBMITTED rather than COMPLETED: the checklist follows the document,
    and the document is not yet verified. Completing it here would let an
    unverified upload close a mandatory item.
    """
    from apps.onboarding.models import ItemKind, ItemStatus, OnboardingItem

    item = (
        OnboardingItem.objects.filter(
            onboarding__employee=document.employee,
            kind=ItemKind.DOCUMENT,
            document_type=document.document_type,
            status__in=[ItemStatus.PENDING, ItemStatus.IN_PROGRESS, ItemStatus.BLOCKED],
        )
        .order_by("order")
        .first()
    )
    if item is None:
        return
    item.document = document
    item.status = ItemStatus.SUBMITTED
    item.save(update_fields=["document", "status", "updated_at"])


@transaction.atomic
def verify_document(*, document: EmployeeDocument, actor) -> EmployeeDocument:
    """
    Confirm a document is genuine and legible.

    Requires APPROVE — not EDIT, and not CREATE. Collecting documents,
    attesting to them and refusing them are three different authorities, and
    an organisation that wants a coordinator who can gather papers without
    being able to vouch for them can only express that if they are separate
    grants.
    """
    require(actor, Resource.EMPLOYEE_DOCUMENT, Action.APPROVE)

    if document.status == VerificationStatus.VERIFIED:
        raise DocumentError({"document": "This document is already verified."})
    if document.status == VerificationStatus.REJECTED:
        raise DocumentError(
            {
                "document": (
                    "This document was rejected. Ask for a corrected copy — it "
                    "arrives as a new submission, which is the one to verify."
                )
            }
        )

    document.status = VerificationStatus.VERIFIED
    document.verified_by = actor
    document.verified_at = timezone.now()
    document.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])

    _complete_onboarding_item(document, actor=actor)
    _audit(document, actor=actor, event="document_verified")
    return document


@transaction.atomic
def reject_document(*, document: EmployeeDocument, actor, reason: str) -> EmployeeDocument:
    """
    Refuse a document, with a reason the employee can act on.

    Write-once. A rejection is the evidence for a decision somebody took at a
    point in time, and a record that can be quietly reworded afterwards is not
    evidence. A corrected document is a NEW submission — which is already how
    uploads behave, since every upload creates its own row — so the history
    reads as "rejected for X, then replaced" rather than losing the first half.
    """
    require(actor, Resource.EMPLOYEE_DOCUMENT, Action.REJECT)

    reason = (reason or "").strip()
    if not reason:
        raise DocumentError(
            {"reason": "Rejecting a document requires a reason, so the employee knows what to fix."}
        )

    words = count_words(reason)
    if words > MAX_REJECTION_WORDS:
        raise DocumentError(
            {
                "reason": (
                    f"The reason is {words} words; the limit is "
                    f"{MAX_REJECTION_WORDS}."
                )
            }
        )

    if document.status == VerificationStatus.REJECTED:
        raise DocumentError(
            {
                "document": (
                    "This document has already been rejected, and a rejection "
                    "cannot be reworded. Ask for a corrected copy instead — it "
                    "is recorded as a new submission."
                )
            }
        )

    document.status = VerificationStatus.REJECTED
    document.rejected_by = actor
    document.rejected_at = timezone.now()
    document.rejection_reason = reason
    document.save(
        update_fields=[
            "status", "rejected_by", "rejected_at", "rejection_reason", "updated_at",
        ]
    )

    # The checklist line goes back to pending — the document was not accepted,
    # so the requirement is outstanding again.
    from apps.onboarding.models import ItemStatus, OnboardingItem

    OnboardingItem.objects.filter(document=document).update(
        status=ItemStatus.PENDING, completed_at=None, completed_by=None
    )

    _audit(document, actor=actor, event="document_rejected", extra={"reason": reason})

    # The employee has something to do now, so they are told. After commit for
    # the same reason as the upload notification.
    transaction.on_commit(lambda: events.document_rejected(document))

    return document


def _complete_onboarding_item(document: EmployeeDocument, *, actor) -> None:
    from apps.onboarding.models import ItemStatus, OnboardingItem
    from apps.onboarding.services import _maybe_close

    items = OnboardingItem.objects.filter(document=document).select_related("onboarding")
    for item in items:
        item.status = ItemStatus.COMPLETED
        item.completed_at = timezone.now()
        item.completed_by = actor
        item.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
        _maybe_close(item.onboarding, actor=actor)


def _audit(document, *, actor, event: str, extra: dict | None = None) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.APPROVE if event == "document_verified" else AuditAction.UPDATE,
        resource=Resource.EMPLOYEE_DOCUMENT,
        entity_type="employees.EmployeeDocument",
        entity_id=str(document.pk),
        entity_label=str(document),
        after={
            "event": event,
            "document_type": document.document_type.name,
            "employee": document.employee.employee_code,
            **(extra or {}),
        },
        request_id=get_request_id(),
    )
