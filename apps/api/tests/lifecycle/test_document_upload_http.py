"""
Uploading a document over HTTP, as each role that is supposed to be able to.

The service-layer tests already prove `upload_document` works. These exercise
the route, which is where the reported failure lives: users report that
uploading from their profile either fails or produces a document nobody can
see afterwards.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

BASE = "/api/v1/employee-documents/"


@pytest.fixture
def pan_type(lifecycle_config):
    from apps.employees.models import DocumentType

    return DocumentType.objects.get(code="pan-card")


def _upload(api, user, employee, pan_type, pdf_upload):
    api.force_authenticate(user=user)
    return api.post(
        BASE,
        {
            "employee": str(employee.pk),
            "document_type": str(pan_type.pk),
            "file": pdf_upload(),
        },
        format="multipart",
    )


def test_hr_manager_can_upload_over_http(api, staff, employee, pan_type, pdf_upload):
    response = _upload(api, staff["hr_manager"].user, employee, pan_type, pdf_upload)

    assert response.status_code == 201, response.data


def test_an_employee_can_upload_their_own_document(api, new_hire, pan_type, pdf_upload):
    """
    The self-service case, and the one users are reporting. The employee holds
    EMPLOYEE_DOCUMENT CREATE at SELF scope.
    """
    response = _upload(api, new_hire.user, new_hire.employee, pan_type, pdf_upload)

    assert response.status_code == 201, response.data


def test_an_uploaded_document_is_then_listed(api, staff, employee, pan_type, pdf_upload):
    """Upload then look for it. This is the 'not visible' half of the report."""
    created = _upload(api, staff["hr_manager"].user, employee, pan_type, pdf_upload)
    assert created.status_code == 201, created.data

    listed = api.get(BASE, {"employee": str(employee.pk)})

    assert listed.status_code == 200
    rows = listed.data["data"] if "data" in listed.data else listed.data["results"]
    assert len(rows) == 1, rows
    assert rows[0]["has_file"] is True


def test_the_employee_can_see_their_own_document(api, new_hire, staff, pan_type, pdf_upload):
    """HR uploads it; the employee must be able to see it on their own profile."""
    _upload(api, staff["hr_manager"].user, new_hire.employee, pan_type, pdf_upload)

    api.force_authenticate(user=new_hire.user)
    listed = api.get(BASE)

    assert listed.status_code == 200
    rows = listed.data["data"] if "data" in listed.data else listed.data["results"]
    assert len(rows) == 1, rows


def test_the_document_can_be_downloaded(api, staff, employee, pan_type, pdf_upload):
    created = _upload(api, staff["hr_manager"].user, employee, pan_type, pdf_upload)

    response = api.get(f"{BASE}{created.data['id']}/download/")

    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"%PDF")


# --------------------------------------------------- filenames people use


#: What people actually call their files. Every one of these failed with a bare
#: 400 and no message, because the storage path spent 89 of its 100 characters
#: before the filename began and Django refused to truncate the stem to nothing.
#: The short names used everywhere else in the suite fitted, so the whole class
#: of failure was invisible.
REAL_FILENAMES = [
    "Aadhaar card.pdf",
    "My Aadhaar Card.pdf",
    "IMG_2201.jpg",
    "PAN card scan.pdf",
    "Address proof - electricity bill August 2026.pdf",
    "Degree certificate (physiotherapy) - Mumbai University.pdf",
    "WhatsApp Image 2026-08-17 at 10.42.55 AM.jpeg",
]


@pytest.mark.parametrize("filename", REAL_FILENAMES)
def test_an_ordinary_filename_uploads(api, staff, employee, pan_type, pdf_upload, filename):
    api.force_authenticate(user=staff["hr_manager"].user)

    response = api.post(
        BASE,
        {
            "employee": str(employee.pk),
            "document_type": str(pan_type.pk),
            "file": pdf_upload(name=filename),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["original_filename"]


def test_an_absurdly_long_filename_still_uploads(api, staff, employee, pan_type, pdf_upload):
    """
    Truncation, not refusal. The stored path is an implementation detail and
    `original_filename` keeps what the person actually called it.
    """
    api.force_authenticate(user=staff["hr_manager"].user)
    long_name = ("Scanned copy of my address proof document " * 8) + ".pdf"

    response = api.post(
        BASE,
        {
            "employee": str(employee.pk),
            "document_type": str(pan_type.pk),
            "file": pdf_upload(name=long_name),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data


def test_the_stored_path_always_fits_the_column(api, staff, employee, pan_type, pdf_upload):
    from apps.employees.models import EmployeeDocument

    api.force_authenticate(user=staff["hr_manager"].user)
    api.post(
        BASE,
        {
            "employee": str(employee.pk),
            "document_type": str(pan_type.pk),
            "file": pdf_upload(name="Address proof - electricity bill August 2026.pdf"),
        },
        format="multipart",
    )

    document = EmployeeDocument.objects.latest("uploaded_at")
    limit = EmployeeDocument._meta.get_field("file").max_length

    assert len(document.file.name) <= limit
