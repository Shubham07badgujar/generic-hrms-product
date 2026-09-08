"""
Two emails, one login, and the onboarding gate.

The claims:
  1. An employee carries a Personal Email and a Company Email; the Company
     Email is the canonical account identifier, and the credential email is
     delivered ONLY to the Personal Email.
  2. EITHER address signs the person in over HTTP: the login resolves a
     personal email to its owning account when it names exactly one active
     employee. The canonical username stays the company email, so a raw
     authenticate() call still only accepts that one.
  3. Until their mandatory onboarding items are done, a new joiner reaches
     only onboarding, documents, and their own identity — nothing else.
  4. HR approving the last mandatory document auto-completes the checklist,
     and the gate opens by itself.
  5. Employees cannot complete their own DOCUMENT items — upload is not
     approval; HR verification is what completes the line.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import authenticate
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.employees.models import DocumentType
from apps.employees.services import documents as doc_service
from apps.employees.services.creation import create_employee
from apps.onboarding.models import (
    EmployeeOnboarding,
    ItemOwner,
    ItemStatus,
    OnboardingStatus,
)
from apps.onboarding.services import OnboardingError, complete_item, waive_item
from core.access.catalog import DepartmentKind, Layer

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"
LOGIN = "/api/v1/auth/login/"


@pytest.fixture
def richa(db, staff, org, lifecycle_config, first_login_done,
          django_capture_on_commit_callbacks):
    """The spec's exact scenario: personal gmail, company login address."""
    with django_capture_on_commit_callbacks(execute=True):
        result = create_employee(
            actor=staff["hr_head"].user,
            first_name="Richa",
            last_name="Satra",
            email="recruitment@company.test",           # Company Email = login
            personal_email="richa.personal@gmail.test",  # credentials go here
            role_code="recruiter",
            department_id=org["departments"][DepartmentKind.HR].pk,
            designation_id=org["any_designation"].pk,
            level_id=org["levels"][Layer.EXECUTIVE].pk,
            location_id=org["location"].pk,
            reporting_manager_id=staff["hr_head"].pk,
            date_of_joining=timezone.localdate(),
            temporary_password=PASSWORD,
        )
    first_login_done(result.user)
    return result


def _pdf(name="doc.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 body", content_type="application/pdf")


# ============================================ two emails, one login identity


def test_both_emails_are_stored_and_the_company_one_is_the_login(richa):
    employee = richa.employee
    assert employee.work_email == "recruitment@company.test"
    assert employee.personal_email == "richa.personal@gmail.test"
    # ONE user account, identified by the Company Email.
    assert richa.user.email == "recruitment@company.test"


def test_the_credential_email_goes_to_the_personal_address(richa):
    message = mail.outbox[-1]
    # ONLY the personal inbox — the company address is never a recipient.
    assert message.to == ["richa.personal@gmail.test"]
    assert "recruitment@company.test" in message.body      # the login email
    assert "cannot be used to log in" not in message.body.lower()
    assert "both open the same account" in message.body


def test_either_address_signs_the_person_in(api, richa):
    """The credentials go only to the personal inbox, so that address must
    open the door too — resolved to the one account that owns it."""
    # The canonical username is still the company email…
    assert authenticate(username="recruitment@company.test", password=PASSWORD)
    assert authenticate(username="richa.personal@gmail.test", password=PASSWORD) is None

    # …but the login endpoint accepts either address.
    ok = api.post(LOGIN, {"email": "recruitment@company.test", "password": PASSWORD})
    assert ok.status_code == 200 and "access" in ok.data

    api.credentials()
    personal = api.post(LOGIN, {"email": "RICHA.PERSONAL@gmail.test", "password": PASSWORD})
    assert personal.status_code == 200 and "access" in personal.data


def test_no_second_account_exists_for_the_personal_email(richa):
    from apps.accounts.models import User

    assert not User.objects.filter(email="richa.personal@gmail.test").exists()
    assert User.objects.filter(email="recruitment@company.test").count() == 1


def test_the_api_requires_both_emails_and_allows_one_shared_address(api, staff, org):
    from apps.employees.api.serializers import EmployeeCreateSerializer

    base = {
        "first_name": "One",
        "email": "one@company.test",
        "role_code": "therapist",
        "department_id": str(org["departments"][DepartmentKind.MEDICAL].pk),
        "designation_id": str(org["any_designation"].pk),
        "date_of_joining": "2026-09-01",
    }
    missing = EmployeeCreateSerializer(data=base)
    assert not missing.is_valid() and "personal_email" in missing.errors

    # The SAME address in both fields is one address, not a clash: an owner or
    # head without a company mailbox logs in and receives setup mail at it.
    same = EmployeeCreateSerializer(data={**base, "personal_email": "one@company.test"})
    assert same.is_valid(), same.errors


# ============================================================ the gate


def _auth(api, user):
    token = api.post(LOGIN, {"email": user.email, "password": PASSWORD}).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def test_a_gated_joiner_reaches_onboarding_and_nothing_else(api, richa):
    _auth(api, richa.user)

    # Blocked: any ordinary module.
    blocked = api.get("/api/v1/asset-allocations/")
    assert blocked.status_code == 403
    assert blocked.data["error"]["code"] == "onboarding_pending"

    # Allowed: identity, onboarding, documents.
    assert api.get("/api/v1/me/").status_code == 200
    assert api.get("/api/v1/me/").data["onboarding_pending"] is True
    assert api.get("/api/v1/onboarding/").status_code == 200
    assert api.get("/api/v1/employee-documents/").status_code == 200
    assert api.get("/api/v1/document-types/").status_code == 200


def test_login_reports_the_gate_so_the_spa_can_redirect(api, richa):
    body = api.post(LOGIN, {"email": richa.user.email, "password": PASSWORD}).data
    assert body["onboarding_pending"] is True


def test_the_people_who_run_onboarding_are_never_gated(api, staff, lifecycle_config):
    # HR Head has no checklist here, but even with one they must not be
    # gated: gating the approver would deadlock the mechanism.
    from apps.onboarding.services import start_onboarding

    start_onboarding(employee=staff["hr_head"], actor=staff["hr_head"].user)
    _auth(api, staff["hr_head"].user)
    assert api.get("/api/v1/asset-allocations/").status_code == 200
    assert api.get("/api/v1/me/").data["onboarding_pending"] is False


# ================================== upload → verify → auto-complete → open


def test_upload_is_not_approval_and_hr_verification_completes_the_item(richa, staff):
    onboarding = EmployeeOnboarding.objects.get(employee=richa.employee)
    pan_item = onboarding.items.get(document_type__code="pan-card")
    assert pan_item.owner == ItemOwner.EMPLOYEE
    assert pan_item.status == ItemStatus.PENDING  # NOT_UPLOADED

    document = doc_service.upload_document(
        employee=richa.employee,
        actor=richa.user,
        document_type=DocumentType.objects.get(code="pan-card"),
        file=_pdf("pan.pdf"),
    )
    pan_item.refresh_from_db()
    assert pan_item.status == ItemStatus.SUBMITTED  # PENDING_HR_APPROVAL

    # The employee cannot tick their own document item past HR.
    with pytest.raises(OnboardingError, match="verification"):
        complete_item(item=pan_item, actor=richa.user, file=_pdf())

    doc_service.verify_document(document=document, actor=staff["hr_head"].user)
    pan_item.refresh_from_db()
    assert pan_item.status == ItemStatus.COMPLETED  # APPROVED


def test_rejection_reason_reaches_the_employee_and_reopens_the_item(richa, staff):
    document = doc_service.upload_document(
        employee=richa.employee,
        actor=richa.user,
        document_type=DocumentType.objects.get(code="aadhaar"),
        file=_pdf("aadhaar.pdf"),
    )
    doc_service.reject_document(
        document=document, actor=staff["hr_head"].user,
        reason="Aadhaar document is unclear. Please upload a clearer copy.",
    )
    onboarding = EmployeeOnboarding.objects.get(employee=richa.employee)
    item = onboarding.items.get(document_type__code="aadhaar")
    assert item.status == ItemStatus.PENDING  # outstanding again
    document.refresh_from_db()
    assert document.rejection_reason.startswith("Aadhaar document is unclear")

    # The corrected copy is a NEW submission; the round-trip repeats.
    replacement = doc_service.upload_document(
        employee=richa.employee, actor=richa.user,
        document_type=DocumentType.objects.get(code="aadhaar"),
        file=_pdf("aadhaar-v2.pdf"),
    )
    item.refresh_from_db()
    assert item.status == ItemStatus.SUBMITTED
    doc_service.verify_document(document=replacement, actor=staff["hr_head"].user)
    item.refresh_from_db()
    assert item.status == ItemStatus.COMPLETED


def test_everything_approved_auto_completes_and_the_gate_opens(api, richa, staff):
    hr = staff["hr_head"].user
    onboarding = EmployeeOnboarding.objects.get(employee=richa.employee)

    # The employee uploads every mandatory document; HR verifies each one.
    for code in ("pan-card", "aadhaar", "degree-certificate", "bank-proof",
                 "signed-agreement"):
        document = doc_service.upload_document(
            employee=richa.employee,
            actor=richa.user if code != "signed-agreement" else hr,
            document_type=DocumentType.objects.get(code=code),
            file=_pdf(f"{code}.pdf"),
        )
        doc_service.verify_document(document=document, actor=hr)

    # HR completes the remaining mandatory tasks and acknowledgement…
    for item in onboarding.items.filter(is_mandatory=True).exclude(
        status__in=[ItemStatus.COMPLETED, ItemStatus.WAIVED]
    ):
        if item.kind == "acknowledgement":
            complete_item(item=item, actor=richa.user)
        else:
            complete_item(item=item, actor=hr, notes="done")

    # …and the checklist closed ITSELF. Nobody clicked "complete onboarding".
    onboarding.refresh_from_db()
    assert onboarding.status == OnboardingStatus.COMPLETED

    # The gate is open: normal role-based access, no flag anywhere to flip.
    _auth(api, richa.user)
    assert api.get("/api/v1/asset-allocations/").status_code == 200
    assert api.get("/api/v1/me/").data["onboarding_pending"] is False


def test_optional_items_never_block_but_waiving_needs_a_reason(richa, staff):
    """The one optional line left on the minimal checklist: the device."""
    from apps.onboarding.models import ItemKind

    onboarding = EmployeeOnboarding.objects.get(employee=richa.employee)
    optional = onboarding.items.get(kind=ItemKind.ASSET)
    assert optional.is_mandatory is False

    with pytest.raises(OnboardingError, match="reason"):
        waive_item(item=optional, actor=staff["hr_head"].user, reason="   ")

    waive_item(
        item=optional, actor=staff["hr_head"].user,
        reason="Not applicable — this position does not need a company device.",
    )
    optional.refresh_from_db()
    assert optional.status == ItemStatus.WAIVED
    assert "Not applicable" in optional.notes

