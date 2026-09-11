"""
Tenant-isolated file storage.

WHAT THIS IS AND IS NOT

The path is NOT the security boundary, and the tests below do not pretend it
is. Every download goes through a view whose scoped `get_object()` is the
authorisation check, production sets `MEDIA_URL = None` so there is no public
URL to guess, and the tenancy route walker already proves a customer cannot
fetch another's document by id.

What the subtree buys is operational and still worth having:

  * deleting a tenant's files is `rm -rf <organization_uuid>/` rather than a
    query across ten tables -- which is what makes the purge in the retention
    policy something an operator can actually carry out and verify;
  * per-tenant bucket policies, lifecycle rules and storage accounting become
    expressible;
  * a cross-tenant path in a bug report or a log line is visible on sight.

The static check at the bottom is the one that matters most. A new
`FileField` added next year with `upload_to="something/"` would work perfectly,
be served by the same authorising view, and quietly leave that customer's data
outside the tree -- discovered when somebody tried to delete it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.platform.services.provisioning import provision_organization
from core.validators import ORGANIZATION_ROOT, scoped_storage_path

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


@pytest.fixture
def company(db):
    from apps.organization.models import (
        Department,
        Designation,
        EmployeeLevel,
        Location,
    )
    from core.access.catalog import DepartmentKind, Layer
    from core.middleware import acting_as

    result = provision_organization(
        name="Northwind Health",
        slug="northwind",
        admin_email="admin@northwind.example",
    )
    with acting_as(None, organization=result.organization):
        from apps.employees.models import Employee

        department = Department.objects.create(
            name="People", code="HR", kind=DepartmentKind.HR
        )
        result.employee = Employee.objects.create(
            employee_code="EMP001",
            first_name="Asha",
            last_name="Rao",
            department=department,
            designation=Designation.objects.create(title="Officer", department=None),
            location=Location.objects.create(name="HQ", code="HO"),
            level=EmployeeLevel.objects.create(
                name="Staff", code="L5", layer=Layer.STAFF
            ),
            date_of_joining=dt.date(2024, 1, 1),
        )
    return result


def _upload(name="evidence.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fixture", content_type="application/pdf")


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def test_a_stored_path_starts_at_the_organizations_subtree():
    path = scoped_storage_path(
        "employee-documents", "owner-1", "cv.pdf", organization_id="org-abc"
    )
    assert path.startswith(f"{ORGANIZATION_ROOT}/org-abc/employee-documents/")
    assert path.endswith(".pdf")


def test_the_organization_is_required_not_defaulted():
    """
    A default would mean a caller who forgot it wrote outside the tenant tree,
    and the file would work perfectly -- served by the same authorising view --
    until somebody tried to delete that customer's data and found some of it
    somewhere else.
    """
    with pytest.raises(TypeError):
        scoped_storage_path("x", "owner", "f.pdf")  # no organization_id

    with pytest.raises(ValueError, match="no organization"):
        scoped_storage_path("x", "owner", "f.pdf", organization_id=None)


def test_the_path_still_fits_the_column():
    """
    The subtree costs about fifty characters, and the budget is 255. An
    over-long name must still be truncated to fit rather than raising
    `SuspiciousFileOperation` from Django, which surfaces as a bare 400 on a
    request that looked completely ordinary.
    """
    from core.validators import STORED_PATH_MAX

    path = scoped_storage_path(
        "employee-documents",
        "11111111-1111-1111-1111-111111111111",
        "A" * 400 + ".pdf",
        organization_id="22222222-2222-2222-2222-222222222222",
    )
    assert len(path) <= STORED_PATH_MAX
    assert path.endswith(".pdf")


def test_two_organizations_never_share_a_subtree():
    first = scoped_storage_path("leave", "e1", "note.pdf", organization_id="org-a")
    second = scoped_storage_path("leave", "e1", "note.pdf", organization_id="org-b")

    assert first.split("/")[1] != second.split("/")[1]
    assert not first.startswith(f"{ORGANIZATION_ROOT}/org-b")
    assert not second.startswith(f"{ORGANIZATION_ROOT}/org-a")


# ---------------------------------------------------------------------------
# Real uploads
# ---------------------------------------------------------------------------


def test_an_uploaded_document_lands_under_its_own_organization(company):
    from apps.employees.models import DocumentCategory, DocumentType, EmployeeDocument
    from core.middleware import acting_as

    with acting_as(None, organization=company.organization):
        document = EmployeeDocument.objects.create(
            employee=company.employee,
            document_type=DocumentType.objects.create(
                name="PAN card", code="pan-file", category=DocumentCategory.IDENTITY
            ),
            file=_upload(),
            original_filename="evidence.pdf",
        )

    assert document.file.name.startswith(
        f"{ORGANIZATION_ROOT}/{company.organization.pk}/"
    )


def test_branding_lands_under_the_organization_that_owns_it(company):
    """
    The one path in the product where the instance IS the tenant, so its own
    primary key is the organization.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile as Upload

    organization = company.organization
    organization.logo = Upload("logo.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
    organization.save(update_fields=["logo"])

    assert organization.logo.name.startswith(
        f"{ORGANIZATION_ROOT}/{organization.pk}/branding/"
    )


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

#: Fields that legitimately do not resolve through the tenant tree, each with
#: its reason. Empty today, and it should stay that way -- an entry here is a
#: directory `rm -rf <organization_uuid>/` will not reach.
STORAGE_EXEMPT: dict[str, str] = {}


def _file_fields():
    from django.db import models

    from core.access.checks import FIRST_PARTY_APP_LABELS

    for model in django_apps.get_models():
        if model._meta.app_label not in FIRST_PARTY_APP_LABELS:
            continue
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField):
                yield f"{model._meta.label}.{field.name}", field


def test_every_file_field_resolves_through_the_tenant_tree():
    """
    The check that protects code nobody has written yet.

    A `FileField` declared with `upload_to="resumes/"` works perfectly: the
    file uploads, the authorising view serves it, every test passes. It simply
    sits outside the customer's subtree, and nothing says so until an operator
    runs a purge and the files are not where the policy says they are.

    So this asserts the SHAPE -- `upload_to` must be a callable, because a
    plain string cannot know which organization it is for.
    """
    offenders = []
    for label, field in _file_fields():
        if label in STORAGE_EXEMPT:
            continue
        upload_to = getattr(field, "upload_to", "")
        if not callable(upload_to):
            offenders.append(f"{label} -> upload_to={upload_to!r}")

    assert not offenders, (
        "These store outside the per-organization subtree, so deleting a "
        "customer's data would miss them:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_sees_every_file_field_there_is():
    """
    Guards the guard. If the collector stopped finding fields -- a renamed app
    label, a changed base class -- the assertion above would pass forever while
    inspecting nothing.
    """
    found = dict(_file_fields())
    assert len(found) >= 10, f"only found {len(found)} file fields: {sorted(found)}"
    for expected in (
        "employees.EmployeeDocument.file",
        "recruitment.Candidate.resume",
        "organization.Organization.logo",
        "onboarding.EmployeeLetter.pdf_file",
    ):
        assert expected in found, f"{expected} is no longer being inspected"
