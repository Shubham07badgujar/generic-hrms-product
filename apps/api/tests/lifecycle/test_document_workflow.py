"""
The document workflow end to end: upload → notify → verify or reject → notify.

Three claims are under test, and each has been wrong at some point in this
codebase:

  1. Seeing an employee is not permission to write to their file. The upload
     route used to resolve its target through EMPLOYEE/VIEW, which let anyone
     who could read a department roster attach a document to a colleague's
     permanent record.

  2. Collecting, attesting and refusing are three authorities, not one. They
     were all EDIT, so granting a coordinator the ability to gather paperwork
     also let them vouch for it.

  3. A rejection is evidence. It must carry a reason, the reason must be
     bounded, and it must not be rewritable afterwards.

The notification assertions check WHO was told as carefully as they check that
somebody was — a document rejection reaching an unrelated employee would be a
disclosure, not a bug in delivery.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.employees.models import EmployeeDocument, VerificationStatus
from apps.employees.services import documents as service
from apps.notifications.models import Notification, NotificationKind
from core.access import Action, Resource, can
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db

BASE = "/api/v1/employee-documents/"


@pytest.fixture
def commit(django_capture_on_commit_callbacks):
    """
    Run a block and then fire the `on_commit` hooks it queued.

    The services deliberately notify AFTER commit, so a rolled-back upload
    never announces itself. pytest wraps each test in a transaction it rolls
    back, so without this the hooks would never fire and every notification
    assertion would pass or fail for the wrong reason.
    """
    from contextlib import contextmanager

    @contextmanager
    def _commit():
        with django_capture_on_commit_callbacks(execute=True):
            yield

    return _commit


@pytest.fixture
def pan_type(lifecycle_config):
    from apps.employees.models import DocumentType

    return DocumentType.objects.get(code="pan-card")


@pytest.fixture
def other_employee(staff):
    """
    Somebody who is emphatically NOT the subject of `document`.

    `employee` is `new_hire.employee`, so reaching for new_hire here would test
    a person against themselves and pass while proving nothing.
    """
    return staff["therapist"]


@pytest.fixture
def document(employee, staff, pan_type, pdf_upload, commit):
    with commit():
        return service.upload_document(
            employee=employee,
            actor=staff["hr_manager"].user,
            document_type=pan_type,
            file=pdf_upload(),
        )


def _notifications(kind, *, recipient=None):
    qs = Notification.objects.filter(kind=kind, is_active=True)
    if recipient is not None:
        qs = qs.filter(recipient=recipient)
    return qs


# ===================================================== upload authorization


def test_a_self_scoped_role_may_upload_for_themselves(new_hire, pan_type, pdf_upload):
    """The ordinary case: my own PAN card, into my own file."""
    document = service.upload_document(
        employee=new_hire.employee,
        actor=new_hire.user,
        document_type=pan_type,
        file=pdf_upload(),
    )

    assert document.employee_id == new_hire.employee.pk
    assert document.status == VerificationStatus.PENDING


def test_seeing_a_colleague_is_not_permission_to_file_against_them(
    staff, employee, pan_type, pdf_upload
):
    """
    The reported defect, as a test.

    A senior doctor can read their department's roster — EMPLOYEE/VIEW at
    DEPARTMENT — but holds EMPLOYEE_DOCUMENT/CREATE only at SELF. Being able to
    look somebody up must not let you write to their permanent record.
    """
    doctor = staff["senior_doctor"].user
    assert can(doctor, Resource.EMPLOYEE_DOCUMENT, Action.CREATE) == 1  # SELF

    with pytest.raises(ValidationError):
        service.upload_document(
            employee=employee,  # somebody else
            actor=doctor,
            document_type=pan_type,
            file=pdf_upload(),
        )


def test_the_route_refuses_it_too_and_conceals_the_employee(
    api, staff, employee, pan_type, pdf_upload
):
    """404, not 403 — a 403 would confirm the employee exists."""
    api.force_authenticate(user=staff["senior_doctor"].user)

    response = api.post(
        BASE,
        {
            "employee": str(employee.pk),
            "document_type": str(pan_type.pk),
            "file": pdf_upload(),
        },
        format="multipart",
    )

    assert response.status_code == 404


def test_hr_may_still_file_on_an_employees_behalf(staff, employee, pan_type, pdf_upload):
    """The legitimate case the fix must not break."""
    document = service.upload_document(
        employee=employee,
        actor=staff["hr_manager"].user,
        document_type=pan_type,
        file=pdf_upload(),
    )

    assert document.uploaded_by_id == staff["hr_manager"].user.pk
    assert document.employee_id == employee.pk


def test_a_document_belongs_to_its_subject_not_its_uploader(document, employee, staff):
    assert document.employee_id == employee.pk
    assert document.uploaded_by_id == staff["hr_manager"].user.pk


# ========================================================= upload notifications


def test_uploading_notifies_the_people_who_can_verify(document, staff):
    recipients = set(
        _notifications(NotificationKind.DOCUMENT_UPLOADED).values_list("recipient__email", flat=True)
    )

    assert staff["hr_head"].user.email in recipients
    assert staff["hr_manager"].user.email in recipients


def test_the_admin_is_notified(admin_user, document):
    assert _notifications(NotificationKind.DOCUMENT_UPLOADED, recipient=admin_user).exists()


def test_an_unrelated_employee_is_never_told_about_it(document, other_employee):
    """A colleague's paperwork is not their business."""
    assert not _notifications(
        NotificationKind.DOCUMENT_UPLOADED, recipient=other_employee.user
    ).exists()


def test_the_notification_carries_metadata_and_no_document_contents(document, staff):
    notification = _notifications(
        NotificationKind.DOCUMENT_UPLOADED, recipient=staff["hr_head"].user
    ).first()

    assert document.employee.employee_code in notification.body
    assert document.document_type.name in notification.body
    # Not the stored filename, and nothing read out of the file itself.
    assert document.original_filename not in notification.body
    assert notification.link_url.startswith("/documents")


def test_filing_on_someone_elses_behalf_is_called_out(document, staff):
    notification = _notifications(
        NotificationKind.DOCUMENT_UPLOADED, recipient=staff["hr_head"].user
    ).first()

    assert "on this employee's behalf" in notification.body


def test_a_self_upload_is_not_flagged_as_filed_by_someone_else(
    new_hire, pan_type, pdf_upload, staff, commit
):
    with commit():
        service.upload_document(
            employee=new_hire.employee,
            actor=new_hire.user,
            document_type=pan_type,
            file=pdf_upload(),
        )
    notification = _notifications(
        NotificationKind.DOCUMENT_UPLOADED, recipient=staff["hr_head"].user
    ).first()

    assert "on this employee's behalf" not in notification.body


def test_replaying_the_same_upload_notification_creates_one_row(document, staff):
    """
    Retry protection. `notify()` dedupes on an unread key, so a repeated
    delivery of the same event does not tell somebody twice.
    """
    from apps.notifications import events

    events.document_uploaded(document)
    events.document_uploaded(document)

    assert (
        _notifications(
            NotificationKind.DOCUMENT_UPLOADED, recipient=staff["hr_head"].user
        ).count()
        == 1
    )


# =============================================================== verification


def test_verifying_requires_approve_not_edit(document, staff):
    from apps.accounts.models import User

    verifier = staff["hr_head"].user
    assert can(verifier, Resource.EMPLOYEE_DOCUMENT, Action.APPROVE) == 4

    verified = service.verify_document(document=document, actor=verifier)

    assert verified.status == VerificationStatus.VERIFIED
    assert verified.verified_by_id == verifier.pk
    assert verified.verified_at is not None


def test_a_role_without_approve_cannot_verify(document, staff):
    with pytest.raises(AccessDenied):
        service.verify_document(document=document, actor=staff["senior_doctor"].user)


def test_verification_is_audited(document, staff):
    from apps.audit.models import AuditLog

    service.verify_document(document=document, actor=staff["hr_head"].user)

    assert AuditLog.objects.filter(
        entity_type="employees.EmployeeDocument", entity_id=str(document.pk)
    ).exists()


def test_a_rejected_document_cannot_then_be_verified(document, staff):
    service.reject_document(
        document=document, actor=staff["hr_head"].user, reason="The scan is unreadable at the edges."
    )
    document.refresh_from_db()

    with pytest.raises(ValidationError):
        service.verify_document(document=document, actor=staff["hr_head"].user)


# ================================================================= rejection


def test_rejection_requires_a_reason(document, staff):
    with pytest.raises(ValidationError):
        service.reject_document(document=document, actor=staff["hr_head"].user, reason="   ")


def test_a_reason_over_two_hundred_and_fifty_words_is_refused(document, staff):
    with pytest.raises(ValidationError):
        service.reject_document(
            document=document,
            actor=staff["hr_head"].user,
            reason=" ".join(["word"] * (service.MAX_REJECTION_WORDS + 1)),
        )


def test_exactly_two_hundred_and_fifty_words_is_accepted(document, staff):
    """The limit is inclusive. An off-by-one here refuses a legitimate reason."""
    reason = " ".join(["word"] * service.MAX_REJECTION_WORDS)

    rejected = service.reject_document(
        document=document, actor=staff["hr_head"].user, reason=reason
    )

    assert rejected.status == VerificationStatus.REJECTED


def test_whitespace_is_trimmed_before_the_count(document, staff):
    """
    Padding must not spend the budget. 250 words wrapped in newlines and tabs
    is 250 words.
    """
    reason = "\n\t " + "  ".join(["word"] * service.MAX_REJECTION_WORDS) + " \n\n"

    rejected = service.reject_document(
        document=document, actor=staff["hr_head"].user, reason=reason
    )

    assert service.count_words(rejected.rejection_reason) == service.MAX_REJECTION_WORDS
    assert not rejected.rejection_reason.startswith(("\n", " ", "\t"))


def test_rejection_records_who_and_when_and_persists_the_reason(document, staff):
    reason = "The date of issue is not legible; please send a clearer scan."

    rejected = service.reject_document(
        document=document, actor=staff["hr_head"].user, reason=reason
    )
    rejected.refresh_from_db()

    assert rejected.status == VerificationStatus.REJECTED
    assert rejected.rejection_reason == reason
    assert rejected.rejected_by_id == staff["hr_head"].user.pk
    assert rejected.rejected_at is not None
    # The verification pair stays empty — nobody verified this.
    assert rejected.verified_by_id is None
    assert rejected.verified_at is None


def test_a_rejection_cannot_be_reworded(document, staff):
    """
    Write-once. A record that can be quietly edited afterwards is not evidence
    of a decision somebody took.
    """
    service.reject_document(
        document=document, actor=staff["hr_head"].user, reason="Illegible at the edges."
    )
    document.refresh_from_db()

    with pytest.raises(ValidationError):
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="Actually, wrong document type."
        )

    document.refresh_from_db()
    assert document.rejection_reason == "Illegible at the edges."


def test_a_corrected_document_is_a_new_submission(document, staff, employee, pan_type, pdf_upload):
    """Re-upload adds a row; it never rewrites the rejected one."""
    service.reject_document(
        document=document, actor=staff["hr_head"].user, reason="Illegible at the edges."
    )

    replacement = service.upload_document(
        employee=employee,
        actor=staff["hr_manager"].user,
        document_type=pan_type,
        file=pdf_upload(),
    )

    assert replacement.pk != document.pk
    assert replacement.status == VerificationStatus.PENDING
    document.refresh_from_db()
    assert document.status == VerificationStatus.REJECTED
    assert document.rejection_reason == "Illegible at the edges."


def test_the_database_refuses_a_reasonless_rejection(document):
    """
    The constraint, not the service. A shell, a data migration or a future
    caller that skips the service must still not be able to write one.
    """
    from django.db.utils import IntegrityError

    with pytest.raises(IntegrityError):
        EmployeeDocument.objects.filter(pk=document.pk).update(
            status=VerificationStatus.REJECTED, rejection_reason=""
        )


def test_rejection_is_audited_with_the_reason(document, staff, commit):
    from apps.audit.models import AuditLog

    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )

    entry = AuditLog.objects.filter(
        entity_type="employees.EmployeeDocument",
        entity_id=str(document.pk),
        after__event="document_rejected",
    ).first()
    assert entry is not None
    assert entry.after.get("reason") == "The scan is unreadable."
    assert entry.actor_id == staff["hr_head"].user.pk


# =================================================== rejection notifications


def test_the_affected_employee_is_notified(document, staff, employee, commit):
    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )

    assert _notifications(
        NotificationKind.DOCUMENT_REJECTED, recipient=employee.user
    ).exists()


def test_the_rejection_notification_says_what_to_do(document, staff, employee, commit):
    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )
    notification = _notifications(
        NotificationKind.DOCUMENT_REJECTED, recipient=employee.user
    ).first()

    assert document.document_type.name in notification.title
    assert "The scan is unreadable." in notification.body
    assert staff["hr_head"].user.email in notification.body
    assert "corrected copy" in notification.body
    assert notification.link_url == "/me"


def test_no_other_employee_receives_the_rejection(document, staff, other_employee, commit):
    """
    The disclosure test. That a named person's paperwork was refused, and why,
    goes to them and to nobody else.
    """
    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )

    assert not _notifications(
        NotificationKind.DOCUMENT_REJECTED, recipient=other_employee.user
    ).exists()
    assert _notifications(NotificationKind.DOCUMENT_REJECTED).count() == 1


def test_an_employee_cannot_read_another_employees_notification(
    api, document, staff, employee, other_employee, commit
):
    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )
    notification = _notifications(
        NotificationKind.DOCUMENT_REJECTED, recipient=employee.user
    ).first()

    api.force_authenticate(user=other_employee.user)
    response = api.get(f"/api/v1/notifications/{notification.pk}/")

    assert response.status_code in (403, 404)


def test_replaying_the_rejection_notification_creates_one_row(document, staff, employee, commit):
    from apps.notifications import events

    with commit():
        service.reject_document(
            document=document, actor=staff["hr_head"].user, reason="The scan is unreadable."
        )
    events.document_rejected(document)

    assert (
        _notifications(NotificationKind.DOCUMENT_REJECTED, recipient=employee.user).count() == 1
    )


# ============================================== listing, scope and disclosure


def test_an_employee_sees_only_their_own_documents(
    api, document, other_employee, pan_type, pdf_upload
):
    service.upload_document(
        employee=other_employee,
        actor=other_employee.user,
        document_type=pan_type,
        file=pdf_upload(),
    )

    api.force_authenticate(user=other_employee.user)
    response = api.get(BASE)

    rows = response.data["data"] if "data" in response.data else response.data["results"]
    assert {str(row["employee"]) for row in rows} == {str(other_employee.pk)}


def test_hr_sees_every_document(api, document, staff, other_employee, pan_type, pdf_upload):
    service.upload_document(
        employee=other_employee,
        actor=other_employee.user,
        document_type=pan_type,
        file=pdf_upload(),
    )

    api.force_authenticate(user=staff["hr_head"].user)
    response = api.get(BASE)

    rows = response.data["data"] if "data" in response.data else response.data["results"]
    assert len(rows) == 2


def test_the_payload_flags_a_document_filed_by_somebody_else(api, document, staff):
    api.force_authenticate(user=staff["hr_head"].user)

    response = api.get(f"{BASE}{document.pk}/")

    assert response.data["filed_by_someone_else"] is True
    assert response.data["employee_code"] == document.employee.employee_code


def test_filters_narrow_but_never_widen(api, document, other_employee):
    """
    An employee filtering by a colleague's id gets nothing, not a leak. The
    scope is applied before the filter, so a filter can only remove rows.
    """
    api.force_authenticate(user=other_employee.user)

    response = api.get(BASE, {"employee": str(document.employee.pk)})

    rows = response.data["data"] if "data" in response.data else response.data["results"]
    assert list(rows) == []


def test_search_matches_a_person_by_name(api, document, staff):
    api.force_authenticate(user=staff["hr_head"].user)

    response = api.get(BASE, {"search": document.employee.first_name})

    rows = response.data["data"] if "data" in response.data else response.data["results"]
    assert len(rows) == 1


def test_the_status_filter_works(api, document, staff):
    api.force_authenticate(user=staff["hr_head"].user)

    pending = api.get(BASE, {"status": "pending"})
    verified = api.get(BASE, {"status": "verified"})

    assert len(pending.data["data"]) == 1
    assert verified.data["data"] == []


def test_downloading_requires_authorisation(api, document, other_employee):
    """Somebody else's identity document is not reachable by guessing its id."""
    api.force_authenticate(user=other_employee.user)

    response = api.get(f"{BASE}{document.pk}/download/")

    assert response.status_code in (403, 404)


def test_the_owner_can_download_their_own(api, document, employee):
    api.force_authenticate(user=employee.user)

    response = api.get(f"{BASE}{document.pk}/download/")

    assert response.status_code == 200


def test_no_storage_path_is_ever_disclosed(api, document, staff):
    """
    A payload carries presence, never location. A path in JSON outlives the
    session that fetched it.
    """
    api.force_authenticate(user=staff["hr_head"].user)

    response = api.get(f"{BASE}{document.pk}/")

    assert response.data["has_file"] is True
    assert "file" not in response.data
    assert document.file.name not in str(response.data)


# ============================================================ over the route


def test_the_reject_route_enforces_the_word_limit(api, document, staff):
    api.force_authenticate(user=staff["hr_head"].user)

    response = api.post(
        f"{BASE}{document.pk}/reject/",
        {"reason": " ".join(["word"] * (service.MAX_REJECTION_WORDS + 1))},
        format="json",
    )

    assert response.status_code == 400
    assert "reason" in response.data.get("error", {}).get("details", response.data)


def test_the_reject_route_requires_the_reject_action(api, document, staff):
    """medical_director holds VIEW at DEPARTMENT and neither APPROVE nor REJECT."""
    api.force_authenticate(user=staff["medical_director"].user)

    response = api.post(
        f"{BASE}{document.pk}/reject/", {"reason": "Not legible enough to accept."}, format="json"
    )

    assert response.status_code == 403


def test_the_verify_route_requires_the_approve_action(api, document, staff):
    api.force_authenticate(user=staff["medical_director"].user)

    response = api.post(f"{BASE}{document.pk}/verify/")

    assert response.status_code == 403


def test_the_full_route_workflow(api, document, staff, employee, commit):
    """Upload → notified → reject with a reason → employee notified."""
    api.force_authenticate(user=staff["hr_head"].user)

    with commit():
        response = api.post(
            f"{BASE}{document.pk}/reject/",
            {"reason": "The corners are cut off; please rescan the whole page."},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["status"] == "rejected"
    assert response.data["rejected_by_email"] == staff["hr_head"].user.email
    assert response.data["rejection_reason"]
    assert _notifications(
        NotificationKind.DOCUMENT_REJECTED, recipient=employee.user
    ).exists()
