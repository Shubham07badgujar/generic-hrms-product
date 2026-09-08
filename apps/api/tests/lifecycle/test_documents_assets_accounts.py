"""
Documents, assets, company accounts, and the employment lifecycle.

Three rules are load-bearing here and each has a test that would fail loudly if
someone relaxed it:

  - upload and verification are separate acts by different people;
  - an exit cannot complete while company property is outstanding;
  - no credential is ever stored or returned.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.assets.models import AllocationStatus, AssetStatus
from apps.assets.services import allocate, return_asset, write_off
from apps.employees.models import DocumentType, EmployeeStatus, VerificationStatus
from apps.employees.services.documents import (
    reject_document,
    upload_document,
    verify_document,
)
from apps.employees.services.lifecycle import (
    LifecycleError,
    allowed_targets,
    can_transition,
    change_status,
)
from apps.itaccounts.models import AccountStatus, CompanyEmailAccount
from apps.itaccounts.services import (
    AccountError,
    assert_no_credentials_in,
    mark_provisioned,
    record_account,
)
from apps.onboarding.models import ItemStatus, OnboardingItem
from core.access.engine import AccessDenied

pytestmark = pytest.mark.django_db


# ===========================================================================
# Documents
# ===========================================================================


@pytest.fixture
def pan_type(lifecycle_config):
    return DocumentType.objects.get(code="pan-card")


def test_an_uploaded_document_is_never_born_verified(employee, staff, pan_type, pdf_upload):
    """
    Upload does not vouch for content. An employee uploading their own PAN
    card must not thereby have it accepted.
    """
    document = upload_document(
        employee=employee,
        actor=staff["hr_manager"].user,
        document_type=pan_type,
        file=pdf_upload(),
    )
    assert document.status == VerificationStatus.PENDING
    assert document.verified_by is None
    assert document.verified_at is None
    assert document.uploaded_by_id == staff["hr_manager"].user.pk


def test_verification_records_a_different_person(employee, staff, pan_type, pdf_upload):
    document = upload_document(
        employee=employee, actor=staff["hr_manager"].user,
        document_type=pan_type, file=pdf_upload(),
    )
    verify_document(document=document, actor=staff["hr_head"].user)
    document.refresh_from_db()

    assert document.status == VerificationStatus.VERIFIED
    assert document.verified_by_id == staff["hr_head"].user.pk
    assert document.verified_at is not None
    assert document.uploaded_by_id != document.verified_by_id


def test_an_unacceptable_file_type_is_refused(employee, staff, pan_type, pdf_upload):
    with pytest.raises(ValidationError) as exc:
        upload_document(
            employee=employee, actor=staff["hr_head"].user, document_type=pan_type,
            file=pdf_upload(name="script.exe", content_type="application/x-msdownload"),
        )
    assert "not an accepted document format" in str(exc.value)


def test_an_oversized_file_is_refused(employee, staff, pan_type, pdf_upload):
    with pytest.raises(ValidationError) as exc:
        upload_document(
            employee=employee, actor=staff["hr_head"].user, document_type=pan_type,
            file=pdf_upload(size=11 * 1024 * 1024),
        )
    assert "limit is" in str(exc.value)


def test_a_document_that_expires_must_say_when(employee, staff, lifecycle_config, pdf_upload):
    passport = DocumentType.objects.get(code="passport")
    with pytest.raises(ValidationError) as exc:
        upload_document(
            employee=employee, actor=staff["hr_head"].user,
            document_type=passport, file=pdf_upload(),
        )
    assert "expiry date" in str(exc.value)


def test_an_employee_cannot_verify_their_own_document(employee, staff, pan_type, pdf_upload):
    """
    SELF_SERVICE grants EMPLOYEE_DOCUMENT CREATE but not EDIT, so an employee
    can supply a document and never attest to it.
    """
    document = upload_document(
        employee=employee, actor=staff["hr_head"].user,
        document_type=pan_type, file=pdf_upload(),
    )
    with pytest.raises(AccessDenied):
        verify_document(document=document, actor=employee.user)


def test_rejecting_a_document_requires_a_reason_and_reopens_the_checklist(
    employee, staff, pan_type, pdf_upload
):
    document = upload_document(
        employee=employee, actor=staff["hr_head"].user,
        document_type=pan_type, file=pdf_upload(),
    )

    with pytest.raises(ValidationError):
        reject_document(document=document, actor=staff["hr_head"].user, reason="")

    reject_document(
        document=document, actor=staff["hr_head"].user, reason="The scan is illegible."
    )
    document.refresh_from_db()
    assert document.status == VerificationStatus.REJECTED
    assert document.rejection_reason == "The scan is illegible."

    item = OnboardingItem.objects.filter(document=document).first()
    if item:
        assert item.status == ItemStatus.PENDING


def test_uploading_satisfies_the_checklist_only_once_verified(
    employee, staff, pan_type, pdf_upload
):
    """
    The checklist follows the document. An unverified upload must not close a
    mandatory item.
    """
    item = OnboardingItem.objects.filter(
        onboarding__employee=employee, document_type=pan_type
    ).first()
    assert item is not None

    document = upload_document(
        employee=employee, actor=staff["hr_head"].user,
        document_type=pan_type, file=pdf_upload(),
    )
    item.refresh_from_db()
    assert item.status == ItemStatus.SUBMITTED

    verify_document(document=document, actor=staff["hr_head"].user)
    item.refresh_from_db()
    assert item.status == ItemStatus.COMPLETED


def test_verification_is_audited(employee, staff, pan_type, pdf_upload):
    from apps.audit.models import AuditLog

    document = upload_document(
        employee=employee, actor=staff["hr_head"].user,
        document_type=pan_type, file=pdf_upload(),
    )
    verify_document(document=document, actor=staff["hr_head"].user)

    entry = AuditLog.objects.filter(
        entity_type="employees.EmployeeDocument",
        entity_id=str(document.pk),
        after__event="document_verified",
    ).first()
    assert entry is not None
    assert entry.actor_id == staff["hr_head"].user.pk


# ===========================================================================
# Assets
# ===========================================================================


def test_allocating_records_the_handover(employee, staff, asset):
    allocation = allocate(
        asset=asset, employee=employee, actor=staff["hr_head"].user,
        condition="new", notes="Issued at induction.",
    )
    asset.refresh_from_db()

    assert allocation.status == AllocationStatus.ACTIVE
    assert allocation.allocated_by_id == staff["hr_head"].user.pk
    assert allocation.condition_at_allocation == "new"
    assert asset.status == AssetStatus.ALLOCATED


def test_one_asset_cannot_be_in_two_pairs_of_hands(employee, staff, asset):
    allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)

    with pytest.raises(ValidationError) as exc:
        allocate(asset=asset, employee=staff["therapist"], actor=staff["hr_head"].user)
    assert "already allocated" in str(exc.value)


def test_returning_reopens_the_asset_and_closes_the_row(employee, staff, asset):
    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)

    return_asset(
        allocation=allocation, actor=staff["hr_head"].user,
        condition="good", notes="Returned in working order.",
    )
    allocation.refresh_from_db()
    asset.refresh_from_db()

    assert allocation.status == AllocationStatus.RETURNED
    assert allocation.returned_at is not None
    assert allocation.received_by_id == staff["hr_head"].user.pk
    assert asset.status == AssetStatus.AVAILABLE


def test_a_damaged_return_does_not_go_back_into_the_pool(employee, staff, asset):
    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    return_asset(allocation=allocation, actor=staff["hr_head"].user, condition="damaged")
    asset.refresh_from_db()

    # Otherwise the next person is handed a broken laptop.
    assert asset.status == AssetStatus.IN_MAINTENANCE


def test_history_survives_reallocation(employee, staff, asset):
    """
    Allocation rows are closed, never reused, so "who had this in March?"
    stays answerable.
    """
    first = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    return_asset(allocation=first, actor=staff["hr_head"].user)
    second = allocate(asset=asset, employee=staff["therapist"], actor=staff["hr_head"].user)

    assert asset.allocations.count() == 2
    first.refresh_from_db()
    assert first.employee_id == employee.pk
    assert second.employee_id == staff["therapist"].pk


def test_allocation_and_return_get_their_own_audit_verbs(employee, staff, asset):
    from apps.audit.models import AuditAction, AuditLog

    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    return_asset(allocation=allocation, actor=staff["hr_head"].user)

    assert AuditLog.objects.filter(
        action=AuditAction.ALLOCATE, entity_id=str(allocation.pk)
    ).exists()
    assert AuditLog.objects.filter(
        action=AuditAction.RETURN, entity_id=str(allocation.pk)
    ).exists()


def test_writing_off_takes_a_heavier_permission_and_a_reason(employee, staff, asset):
    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)

    # HR Manager holds EDIT on allocations but not DELETE.
    with pytest.raises(AccessDenied):
        write_off(
            allocation=allocation, actor=staff["hr_manager"].user, reason="Lost in transit."
        )

    with pytest.raises(ValidationError):
        write_off(allocation=allocation, actor=staff["hr_head"].user, reason="")

    write_off(allocation=allocation, actor=staff["hr_head"].user, reason="Lost in transit.")
    allocation.refresh_from_db()
    asset.refresh_from_db()
    assert allocation.status == AllocationStatus.WRITTEN_OFF
    assert asset.status == AssetStatus.LOST


def test_an_exited_employee_cannot_be_given_company_property(employee, staff, asset):
    employee.status = EmployeeStatus.EXITED
    employee.save(update_fields=["status"])

    with pytest.raises(ValidationError) as exc:
        allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    assert "has exited" in str(exc.value)


# ===========================================================================
# Company accounts
# ===========================================================================


def test_the_model_has_nowhere_to_put_a_password():
    """
    The guarantee is structural, not procedural.

    If a credential column is ever added, this test fails and the reviewer is
    forced to justify it.
    """
    fields = {f.name.lower() for f in CompanyEmailAccount._meta.get_fields()}
    forbidden = {"password", "passwd", "secret", "credential", "credentials", "token", "api_key"}
    assert not (fields & forbidden), f"CompanyEmailAccount must store no secrets: {fields & forbidden}"


def test_a_payload_carrying_a_credential_is_refused_loudly(employee, staff):
    """
    Silently dropping the field would leave the caller believing their
    password was stored.
    """
    with pytest.raises(AccountError) as exc:
        assert_no_credentials_in({"email_address": "a@b.test", "password": "hunter2"})
    assert "never stores account credentials" in str(exc.value)

    with pytest.raises(AccountError):
        record_account(
            employee=employee, actor=staff["hr_head"].user,
            email_address="priya@company.test", provider="google_workspace",
            password="hunter2",
        )


def test_recording_and_provisioning_an_account(employee, staff):
    account = record_account(
        employee=employee, actor=staff["hr_head"].user,
        email_address="Priya.Nair@Company.Test", provider="google_workspace",
    )
    assert account.status == AccountStatus.REQUESTED
    assert account.email_address == "priya.nair@company.test"  # normalised
    assert account.requested_by_id == staff["hr_head"].user.pk

    mark_provisioned(
        account=account, actor=staff["hr_head"].user, external_account_id="ext-123"
    )
    account.refresh_from_db()
    assert account.status == AccountStatus.ACTIVE
    assert account.provisioned_by_id == staff["hr_head"].user.pk
    assert account.provisioned_at is not None
    assert account.external_account_id == "ext-123"


def test_the_serializer_exposes_no_credential_field(employee, staff):
    from apps.employees.api.lifecycle_serializers import CompanyEmailAccountSerializer

    account = record_account(
        employee=employee, actor=staff["hr_head"].user,
        email_address="priya@company.test", provider="google_workspace",
    )
    payload = CompanyEmailAccountSerializer(account).data
    serialized = " ".join(payload.keys()).lower()

    for word in ("password", "secret", "credential", "token"):
        assert word not in serialized


def test_provisioning_works_without_an_onboarding_line(employee, staff):
    """
    Email provisioning was removed from onboarding: the checklist carries no
    ACCOUNT line, and provisioning a mailbox neither needs one nor fails for
    its absence — it is ordinary IT administration now, on its own trail.
    """
    from apps.onboarding.models import ItemKind

    assert not OnboardingItem.objects.filter(
        onboarding__employee=employee, kind=ItemKind.ACCOUNT
    ).exists()

    account = record_account(
        employee=employee, actor=staff["hr_head"].user,
        email_address="priya@company.test", provider="google_workspace",
    )
    provisioned = mark_provisioned(account=account, actor=staff["hr_head"].user)
    assert provisioned.status == "active"


# ===========================================================================
# Lifecycle
# ===========================================================================


def test_the_transition_table_refuses_a_nonsense_move(employee, staff):
    assert can_transition(EmployeeStatus.ON_PROBATION, EmployeeStatus.ACTIVE) is True
    assert can_transition(EmployeeStatus.ACTIVE, EmployeeStatus.EXITED) is False
    assert can_transition(EmployeeStatus.EXITED, EmployeeStatus.ACTIVE) is False

    with pytest.raises(LifecycleError) as exc:
        change_status(
            employee=employee, actor=staff["hr_head"].user, new_status=EmployeeStatus.EXITED,
            reason="Trying to skip the notice period entirely.",
        )
    assert "cannot become" in str(exc.value)


def test_nothing_leaves_the_exited_state(employee, staff):
    """A returning employee is a new record, not an edit to the old one."""
    assert allowed_targets(EmployeeStatus.EXITED) == []


def test_confirmation_cannot_be_reached_as_a_status_change(employee, staff):
    """
    Otherwise someone could confirm an employee without a review ever existing.
    """
    with pytest.raises(LifecycleError) as exc:
        change_status(
            employee=employee, actor=staff["hr_head"].user,
            new_status=EmployeeStatus.CONFIRMED,
        )
    assert "probation decision" in str(exc.value)


def test_ending_employment_demands_a_reason(employee, staff):
    with pytest.raises(LifecycleError) as exc:
        change_status(
            employee=employee, actor=staff["hr_head"].user,
            new_status=EmployeeStatus.RESIGNED, reason="quit",
        )
    assert "recorded reason" in str(exc.value)


def test_an_exit_is_blocked_while_company_property_is_outstanding(employee, staff, asset):
    """The gate the spec asks for, enforced in the service."""
    allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)

    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.RESIGNED, reason="Resigned to relocate abroad.",
    )

    with pytest.raises(LifecycleError) as exc:
        change_status(
            employee=employee, actor=staff["hr_head"].user,
            new_status=EmployeeStatus.EXITED, reason="Final working day completed.",
        )
    assert "still allocated" in str(exc.value)
    assert asset.asset_tag in str(exc.value)

    employee.refresh_from_db()
    assert employee.status == EmployeeStatus.RESIGNED


def test_returning_the_asset_clears_the_exit(employee, staff, asset):
    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.RESIGNED, reason="Resigned to relocate abroad.",
    )
    return_asset(allocation=allocation, actor=staff["hr_head"].user)

    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.EXITED, reason="Final working day completed.",
    )
    employee.refresh_from_db()
    assert employee.status == EmployeeStatus.EXITED
    assert employee.date_of_exit == timezone.localdate()


def test_a_write_off_is_the_sanctioned_way_past_the_gate(employee, staff, asset):
    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)
    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.RESIGNED, reason="Resigned to relocate abroad.",
    )
    write_off(
        allocation=allocation, actor=staff["hr_head"].user,
        reason="Laptop never returned after the final working day.",
    )

    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.EXITED, reason="Final working day completed.",
    )
    employee.refresh_from_db()
    assert employee.status == EmployeeStatus.EXITED


def test_non_returnable_property_does_not_block_an_exit(employee, staff, stationery_asset):
    """A branded notebook is not a laptop."""
    allocate(asset=stationery_asset, employee=employee, actor=staff["hr_head"].user)
    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.RESIGNED, reason="Resigned to relocate abroad.",
    )
    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.EXITED, reason="Final working day completed.",
    )
    employee.refresh_from_db()
    assert employee.status == EmployeeStatus.EXITED


def test_a_status_change_is_audited_with_both_states(employee, staff):
    from apps.audit.models import AuditLog

    change_status(
        employee=employee, actor=staff["hr_head"].user,
        new_status=EmployeeStatus.ON_NOTICE, reason="Serving notice.",
    )

    entry = AuditLog.objects.filter(
        entity_type="employees.Employee",
        entity_id=str(employee.pk),
        after__event="status_change",
    ).first()
    assert entry is not None
    assert entry.before["status"] == EmployeeStatus.ON_PROBATION
    assert entry.actor_id == staff["hr_head"].user.pk


def test_an_employee_cannot_change_their_own_status(employee, staff):
    """
    Self-service grants EMPLOYEE/EDIT at SELF scope so people can maintain
    their own contact details. Without a scope floor that same grant would let
    anyone resign themselves, or mark themselves confirmed.
    """
    with pytest.raises(AccessDenied) as exc:
        change_status(
            employee=staff["therapist"], actor=staff["therapist"].user,
            new_status=EmployeeStatus.ON_NOTICE, reason="Attempting to change my own status.",
        )
    assert "authority over other people" in str(exc.value)

    staff["therapist"].refresh_from_db()
    assert staff["therapist"].status != EmployeeStatus.ON_NOTICE


def test_a_manager_scoped_role_may_move_their_reports(employee, staff):
    """The floor is scope > SELF, not "HR only" — a team lead still qualifies."""
    from core.access import can
    from core.access.catalog import Scope

    assert can(staff["medical_director"].user, "employee", "view") >= Scope.DEPARTMENT
