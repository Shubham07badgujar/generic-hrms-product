"""
The lifecycle over HTTP.

The service tests prove the rules hold when called correctly; these prove they
hold when called directly by a real client with a real token. The profile
endpoint gets the most attention, because "you can see this employee" and "you
can see everything about them" are different questions and conflating them is
how sensitive data leaks.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"
EMPLOYEES = "/api/v1/employees/"
PROBATION = "/api/v1/probation-reviews/"
DOCUMENTS = "/api/v1/employee-documents/"
ACCOUNTS = "/api/v1/company-accounts/"
ALLOCATIONS = "/api/v1/asset-allocations/"
LETTERS = "/api/v1/letters/"


def _auth(api, user):
    token = api.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}
    ).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


# ===================================================== the profile


def test_hr_sees_every_section_of_a_profile(api, staff, employee, lifecycle_config):
    _auth(api, staff["hr_head"].user)
    response = api.get(f"{EMPLOYEES}{employee.pk}/profile/")

    assert response.status_code == 200
    body = response.data
    for section in (
        "employee", "documents", "onboarding", "probation_reviews",
        "asset_allocations", "company_account", "letters",
    ):
        assert section in body, f"HR should see the {section} section"
    assert body["employee"]["employee_code"] == employee.employee_code


def test_a_section_is_scoped_by_its_own_resource(
    api, staff, employee, lifecycle_config
):
    """
    Reaching a profile is not the same as reaching everything on it.

    A department head can open this employee, and holds LETTER only at SELF
    scope through self-service. The section may therefore appear, but it must
    NOT contain the employee's letters.
    """
    from apps.onboarding.letters import generate_letter

    generate_letter(
        employee=employee, actor=staff["hr_head"].user, letter_type="appointment"
    )

    _auth(api, staff["medical_director"].user)
    response = api.get(f"{EMPLOYEES}{employee.pk}/profile/")

    assert response.status_code == 200
    assert response.data.get("letters", []) == []

    # HR, whose grant is organisation-wide, does see it.
    _auth(api, staff["hr_head"].user)
    hr_view = api.get(f"{EMPLOYEES}{employee.pk}/profile/")
    assert len(hr_view.data["letters"]) == 1


def test_the_employee_sees_their_own_profile(api, employee, lifecycle_config):
    _auth(api, employee.user)
    response = api.get(f"{EMPLOYEES}{employee.pk}/profile/")

    assert response.status_code == 200
    assert response.data["employee"]["employee_code"] == employee.employee_code
    # Self-service carries onboarding and documents, so those sections appear.
    assert "onboarding" in response.data
    # But no authority to move themselves through the lifecycle.
    assert response.data["allowed_status_transitions"] == []


def test_an_employee_cannot_open_someone_elses_profile(api, employee, staff):
    _auth(api, employee.user)
    response = api.get(f"{EMPLOYEES}{staff['therapist'].pk}/profile/")
    # 404 rather than 403: out-of-scope rows do not confirm their existence.
    assert response.status_code == 404


def test_the_ceo_reads_a_profile_but_is_offered_no_transitions(
    api, ceo_user, employee, lifecycle_config
):
    _auth(api, ceo_user)
    response = api.get(f"{EMPLOYEES}{employee.pk}/profile/")

    assert response.status_code == 200
    assert response.data["allowed_status_transitions"] == []


def test_a_department_head_sees_only_their_own_department(api, staff, employee):
    _auth(api, staff["operational_head"].user)
    # The employee is in the medical department.
    assert api.get(f"{EMPLOYEES}{employee.pk}/profile/").status_code == 404

    _auth(api, staff["medical_director"].user)
    assert api.get(f"{EMPLOYEES}{employee.pk}/profile/").status_code == 200


# ===================================================== lifecycle over HTTP


def test_a_status_change_goes_through_the_transition_table(api, staff, employee):
    _auth(api, staff["hr_head"].user)

    refused = api.post(
        f"{EMPLOYEES}{employee.pk}/status/",
        {"status": "exited", "reason": "Trying to skip the notice period."},
        format="json",
    )
    assert refused.status_code == 400
    assert "cannot become" in str(refused.data)

    allowed = api.post(
        f"{EMPLOYEES}{employee.pk}/status/",
        {"status": "on_notice", "reason": "Serving notice."},
        format="json",
    )
    assert allowed.status_code == 200
    assert allowed.data["status"] == "on_notice"


def test_an_employee_cannot_change_their_own_status_over_http(api, employee):
    _auth(api, employee.user)
    response = api.post(
        f"{EMPLOYEES}{employee.pk}/status/",
        {"status": "resigned", "reason": "Resigning myself through the API."},
        format="json",
    )
    assert response.status_code == 403


# ===================================================== probation over HTTP


@pytest.fixture
def review(employee, staff):
    from apps.employees.models import ProbationReview

    return ProbationReview.objects.create(
        employee=employee,
        probation_end_date=timezone.localdate() + dt.timedelta(days=10),
        reviewer=staff["medical_director"],
    )


def test_a_manager_assesses_but_cannot_decide_over_http(api, staff, review):
    _auth(api, staff["medical_director"].user)

    assessed = api.post(
        f"{PROBATION}{review.pk}/assess/",
        {"recommendation": "confirm", "performance_rating": 4, "strengths": "Reliable."},
        format="json",
    )
    assert assessed.status_code == 200
    assert assessed.data["recommendation"] == "confirm"

    decided = api.post(f"{PROBATION}{review.pk}/decide/", {"decision": "confirm"}, format="json")
    assert decided.status_code == 403

    review.employee.refresh_from_db()
    assert review.employee.probation_status != "confirmed"


def test_hr_decides_and_the_letter_appears(api, staff, review, lifecycle_config):
    _auth(api, staff["hr_head"].user)

    response = api.post(f"{PROBATION}{review.pk}/decide/", {"decision": "confirm"}, format="json")
    assert response.status_code == 200
    assert response.data["decision"] == "confirm"
    assert response.data["confirmation_letter"] is not None

    review.employee.refresh_from_db()
    assert review.employee.probation_status == "confirmed"


def test_extending_without_a_rationale_is_refused_at_the_api(api, staff, review):
    _auth(api, staff["hr_head"].user)
    response = api.post(
        f"{PROBATION}{review.pk}/decide/",
        {"decision": "extend", "rationale": "too short", "extended_to": "2027-01-01"},
        format="json",
    )
    assert response.status_code == 400
    assert "rationale" in str(response.data)


def test_a_confirmation_letter_is_refused_before_the_decision(
    api, staff, employee, lifecycle_config
):
    """The letter route cannot be used to reach round the probation rule."""
    _auth(api, staff["hr_head"].user)
    response = api.post(
        LETTERS,
        {"employee": str(employee.pk), "letter_type": "confirmation"},
        format="json",
    )
    assert response.status_code == 400
    assert "probation confirmation" in str(response.data)


def test_an_appointment_letter_generates_a_pdf(api, staff, employee, lifecycle_config):
    _auth(api, staff["hr_head"].user)
    response = api.post(
        LETTERS,
        {"employee": str(employee.pk), "letter_type": "appointment"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["has_pdf"] is True
    assert response.data["template_version"] >= 1

    download = api.get(f"{LETTERS}{response.data['id']}/download/")
    assert download.status_code == 200
    assert download["Content-Type"] in ("application/pdf", "application/octet-stream")


# ===================================================== documents over HTTP


def test_a_document_response_never_carries_a_storage_url(
    api, staff, employee, lifecycle_config, pdf_upload
):
    from apps.employees.models import DocumentType
    from apps.employees.services.documents import upload_document

    document = upload_document(
        employee=employee, actor=staff["hr_head"].user,
        document_type=DocumentType.objects.get(code="pan-card"), file=pdf_upload(),
    )

    _auth(api, staff["hr_head"].user)
    listing = api.get(DOCUMENTS, {"employee": str(employee.pk)})
    assert listing.status_code == 200

    row = listing.data["data"][0]
    assert row["has_file"] is True
    # Presence, never location.
    assert "file" not in row
    assert "url" not in " ".join(row.keys()).lower()

    download = api.get(f"{DOCUMENTS}{document.pk}/download/")
    assert download.status_code == 200


def test_an_employee_cannot_download_someone_elses_document(
    api, staff, employee, lifecycle_config, pdf_upload
):
    from apps.employees.models import DocumentType
    from apps.employees.services.documents import upload_document

    other = upload_document(
        employee=staff["therapist"], actor=staff["hr_head"].user,
        document_type=DocumentType.objects.get(code="pan-card"), file=pdf_upload(),
    )

    _auth(api, employee.user)
    assert api.get(f"{DOCUMENTS}{other.pk}/download/").status_code == 404


# ===================================================== accounts over HTTP


def test_the_account_api_refuses_a_credential_and_returns_none(api, staff, employee):
    _auth(api, staff["hr_head"].user)

    refused = api.post(
        ACCOUNTS,
        {
            "employee": str(employee.pk),
            "email_address": "priya@company.test",
            "provider": "google_workspace",
            "password": "hunter2",
        },
        format="json",
    )
    assert refused.status_code == 400
    assert "never stores account credentials" in str(refused.data)

    created = api.post(
        ACCOUNTS,
        {
            "employee": str(employee.pk),
            "email_address": "priya@company.test",
            "provider": "google_workspace",
        },
        format="json",
    )
    assert created.status_code == 201
    body = " ".join(created.data.keys()).lower()
    for word in ("password", "secret", "credential", "token"):
        assert word not in body


# ===================================================== assets over HTTP


def test_allocation_and_return_over_http(api, staff, employee, asset):
    _auth(api, staff["hr_head"].user)

    allocated = api.post(
        ALLOCATIONS,
        {"asset": str(asset.pk), "employee": str(employee.pk), "condition": "new"},
        format="json",
    )
    assert allocated.status_code == 201, allocated.data
    assert allocated.data["status"] == "active"

    returned = api.post(
        f"{ALLOCATIONS}{allocated.data['id']}/return/",
        {"condition": "good"},
        format="json",
    )
    assert returned.status_code == 200
    assert returned.data["status"] == "returned"


def test_an_employee_sees_only_their_own_allocations(api, staff, employee, asset):
    from apps.assets.services import allocate
    from apps.onboarding.models import EmployeeOnboarding, OnboardingStatus

    allocate(asset=asset, employee=staff["therapist"], actor=staff["hr_head"].user)

    # The new hire's mandatory onboarding gates everything but onboarding
    # itself; this test is about allocation scoping, so their checklist is
    # closed first.
    EmployeeOnboarding.objects.filter(employee=employee).update(
        status=OnboardingStatus.COMPLETED
    )

    _auth(api, employee.user)
    response = api.get(ALLOCATIONS)
    assert response.status_code == 200
    assert response.data["data"] == []


def test_writing_off_needs_more_than_edit_over_http(api, staff, employee, asset):
    from apps.assets.services import allocate

    allocation = allocate(asset=asset, employee=employee, actor=staff["hr_head"].user)

    _auth(api, staff["hr_manager"].user)
    refused = api.post(
        f"{ALLOCATIONS}{allocation.pk}/write-off/",
        {"reason": "Never returned after the final working day."},
        format="json",
    )
    assert refused.status_code == 403

    _auth(api, staff["hr_head"].user)
    allowed = api.post(
        f"{ALLOCATIONS}{allocation.pk}/write-off/",
        {"reason": "Never returned after the final working day."},
        format="json",
    )
    assert allowed.status_code == 200


# ===================================================== the directory


def test_the_directory_filters_are_server_side(api, staff, employee):
    _auth(api, staff["hr_head"].user)

    by_department = api.get(EMPLOYEES, {"department": str(employee.department_id)})
    assert by_department.status_code == 200
    assert all(
        str(row["department"]) == str(employee.department_id)
        for row in by_department.data["data"]
    )

    by_status = api.get(EMPLOYEES, {"status": "on_probation"})
    assert all(row["status"] == "on_probation" for row in by_status.data["data"])


def test_scope_narrows_the_directory_not_a_query_parameter(api, staff, employee):
    """
    A department head asking for another department gets nothing, because the
    server decides the scope before the filter is applied.
    """
    _auth(api, staff["operational_head"].user)
    response = api.get(EMPLOYEES, {"department": str(employee.department_id)})
    assert response.status_code == 200
    assert response.data["data"] == []
