"""
Fixtures for candidate import.

Every spreadsheet here is synthesised in-process. No sample file on disk, no
export downloaded from a real platform, and no real person's contact details:
addresses use the RFC 6761 reserved `.test` domain and phone numbers come from
the documentation range.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

# Re-exported rather than rebuilt, following tests/e2e: `staff` stands up an
# Employee per role, which roles flagged requires_employee=True need before
# the access engine will resolve any grant for them at all.
from tests.recruitment.conftest import staff, workflows  # noqa: F401

WORKINDIA_HEADERS = [
    "Candidate ID", "Candidate Name", "Mobile Number", "Email",
    "Current Company", "Experience", "Expected Salary", "Notice Period",
    "Age", "Gender", "English",
]

NAUKRI_HEADERS = [
    "Applicant ID", "Candidate Name", "Email ID", "Mobile",
    "Current Employer", "Total Experience", "Expected CTC", "Notice Period",
]


def _xlsx(headers, rows) -> bytes:
    """A real, openpyxl-readable workbook."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _csv(headers, rows, *, delimiter=",", encoding="utf-8") -> bytes:
    lines = [delimiter.join(str(h) for h in headers)]
    for row in rows:
        lines.append(delimiter.join("" if v is None else str(v) for v in row))
    return "\n".join(lines).encode(encoding)


@pytest.fixture
def upload():
    def _make(name, payload, content_type=""):
        return SimpleUploadedFile(name, payload, content_type=content_type)

    return _make


@pytest.fixture
def workindia_xlsx(upload):
    """
    A representative WorkIndia export.

    Row 2 is the case this whole feature exists for: a phone number and no
    email at all.
    """
    def _make(rows=None, name="workindia.xlsx"):
        rows = rows if rows is not None else [
            ["WI-1001", "Priya Deshmukh", "9876543210", "priya@example.test",
             "City Physio", "4.5", "6.5 lakh", "30 days", "29", "F", "Good"],
            ["WI-1002", "Ramesh Kumar", "9876543211", "",
             "", "2 years", "", "Immediate", "34", "M", "Fluent"],
        ]
        return upload(name, _xlsx(WORKINDIA_HEADERS, rows))

    return _make


@pytest.fixture
def workindia_csv(upload):
    def _make(rows=None, name="workindia.csv", **kwargs):
        rows = rows if rows is not None else [
            ["WI-2001", "Anita Joshi", "9876543212", "anita@example.test",
             "Clinic Co", "3", "500000", "60", "31", "F", "Good"],
        ]
        return upload(name, _csv(WORKINDIA_HEADERS, rows, **kwargs))

    return _make


@pytest.fixture
def naukri_xlsx(upload):
    def _make(rows=None, name="naukri.xlsx"):
        rows = rows if rows is not None else [
            ["NK-500", "Sanjay Iyer", "sanjay@example.test", "+91 98765 43213",
             "Hospital Ltd", "8 years 6 months", "12 lakh", "90"],
        ]
        return upload(name, _xlsx(NAUKRI_HEADERS, rows))

    return _make


@pytest.fixture
def naukri_csv(upload):
    def _make(rows=None, name="naukri.csv", **kwargs):
        rows = rows if rows is not None else [
            ["NK-600", "Meera Kulkarni", "meera@example.test", "9876543214",
             "Care Group", "10", "1500000", "30"],
        ]
        return upload(name, _csv(NAUKRI_HEADERS, rows, **kwargs))

    return _make


@pytest.fixture
def malicious():
    """Files built to attack the parser. Each targets one specific control."""

    def _zip(members: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("xl/workbook.xml", "<workbook/>")
            for name, data in members.items():
                zf.writestr(name, data)
        return buffer.getvalue()

    return {
        "macro": _zip({"xl/vbaProject.bin": b"\x00macro payload"}),
        "zip_bomb": _zip({"xl/worksheets/sheet1.xml": b"\x00" * (80 * 1024 * 1024)}),
        "not_a_zip": b"this is not a workbook at all",
        "csv_with_nulls": b"name,phone\n\x00\x00\x00\x00,\x00\x00\x00\x00\n" * 200,
    }


@pytest.fixture
def formula_xlsx(upload):
    """
    A workbook whose name column holds a formula.

    openpyxl writes no cached value, so `data_only=True` reads None. The point
    is that the literal `=cmd|...` text NEVER reaches a candidate field.
    """
    def _make(name="formulas.xlsx"):
        rows = [["WI-3001", "=cmd|'/c calc'!A0", "9876543215", "f@example.test",
                 "", "", "", "", "", "", ""]]
        return upload(name, _xlsx(WORKINDIA_HEADERS, rows))

    return _make


@pytest.fixture
def import_job(db, org, roles):
    """A published job opening with a workflow that has stages."""
    from apps.recruitment.models import JobOpening
    from apps.workflows.seeds import seed_workflows
    from core.access.catalog import DepartmentKind

    workflows = {w.department_kind: w for w in seed_workflows()}
    return JobOpening.objects.create(
        title="Therapist",
        workflow=workflows[DepartmentKind.MEDICAL],
        department=org["departments"][DepartmentKind.MEDICAL],
        target_role=roles["therapist"],
        status="published",
    )


@pytest.fixture
def ops_job(db, org, roles):
    """A job in a DIFFERENT department, for scope-isolation tests."""
    from apps.recruitment.models import JobOpening
    from apps.workflows.seeds import seed_workflows
    from core.access.catalog import DepartmentKind

    workflows = {w.department_kind: w for w in seed_workflows()}
    return JobOpening.objects.create(
        title="Office Boy",
        workflow=workflows[DepartmentKind.OPERATIONS],
        department=org["departments"][DepartmentKind.OPERATIONS],
        target_role=roles["office_boy"],
        status="published",
    )


@pytest.fixture
def importer_user(staff):
    """
    A Recruiter — one of the four roles holding CANDIDATE/IMPORT.

    Taken from `staff` rather than `make_user`, because the recruiter role is
    `requires_employee=True`: step 7 of the access resolution empties the grants
    of any such role that has no Employee record, so a bare user resolves to no
    permissions at all.
    """
    return staff["recruiter"].user


@pytest.fixture
def user_for(staff, make_user):
    """A user holding `role`, with an Employee record where the role needs one."""

    def _get(role_code: str):
        if role_code in staff:
            return staff[role_code].user
        # admin and ceo are requires_employee=False by design.
        return make_user(role_code)

    return _get


@pytest.fixture
def attested_batch(importer_user, import_job):
    """Upload + attest, ready to commit."""
    from apps.imports.platforms import WORKINDIA
    from apps.imports.services import importer

    def _make(file, platform=WORKINDIA, job=None, actor=None):
        actor = actor or importer_user
        batch = importer.create_batch(
            actor=actor, platform=platform, job_opening=job or import_job, file=file
        )
        importer.attest(
            actor=actor,
            batch=batch,
            legal_basis="voluntarily_provided",
            legal_basis_note=(
                "Sourced from our own employer dashboard export; candidates "
                "posted profiles to be contacted about work."
            ),
            attestation_text="I confirm a lawful basis exists for this import.",
        )
        return batch

    return _make


@pytest.fixture
def everyone_grant(make_user):
    """Give one principal one extra grant, without touching the shipped matrix."""
    from apps.accounts.models import UserPermissionOverride
    from core.access.context import invalidate

    admin = make_user("admin")

    def _grant(user, resource, action, scope):
        UserPermissionOverride.objects.create(
            user=user, resource=resource, action=action, scope=scope,
            reason="Pinning import scope isolation in a test.",
            granted_by=admin,
        )
        invalidate(user.pk)

    return _grant
