"""
A customer's whole record, and who may take it when.

The promise under test is the lifecycle's: a CANCELLED organization can still
export its data for a window. Until this route existed that promise was
unkept -- a non-operational organization resolves to a grant-less context, so
the customer was locked out of statutory records they are obliged to keep.

What these tests hold, in order of how much damage breaking them would do:

  * the archive holds THIS organization's rows and never another's;
  * encrypted identifiers (PAN, Aadhaar, bank account) are withheld, and the
    README says so;
  * the exception is ONE route -- a cancelled Admin can export and still
    cannot read anything else;
  * the window closes, suspension refuses, non-Admins refuse, operators refuse.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.platform.services.provisioning import provision_organization
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "export-password-12345"
PAN = "ABCDE1234F"
AADHAAR = "123412341234"
BANK = "000111222333"


def _usable(user):
    user.set_password(PASSWORD)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


def _populate(organization, *, marker: str, first_name: str = "Asha"):
    """One employee with the identifiers that must NOT leave in bulk."""
    from apps.employees.models import Employee
    from apps.organization.models import Department, Designation, EmployeeLevel, Location
    from core.access.catalog import DepartmentKind, Layer

    with acting_as(None, organization=organization):
        department = Department.objects.create(
            name="Operations", code="OPS", kind=DepartmentKind.OPERATIONS
        )
        location = Location.objects.create(
            name="Head Office", code="HO", city="Pune", state="MH", is_head_office=True
        )
        level = EmployeeLevel.objects.create(name="Staff", code="L5", layer=Layer.STAFF)
        designation = Designation.objects.create(title="Associate", department=department)
        return Employee.objects.create(
            employee_code=marker,
            first_name=first_name,
            last_name=marker,
            department=department,
            designation=designation,
            location=location,
            level=level,
            date_of_joining=dt.date(2025, 1, 6),
            pan=PAN,
            aadhaar=AADHAAR,
            bank_account_number=BANK,
        )


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)


@pytest.fixture
def ours(plans):
    result = provision_organization(
        name="Northwind Health", slug="northwind", admin_email="admin@northwind.example"
    )
    _usable(result.admin)
    _populate(result.organization, marker="OURS-001")
    return result


@pytest.fixture
def theirs(plans):
    result = provision_organization(
        name="Southwind Clinics", slug="southwind", admin_email="admin@southwind.example"
    )
    _populate(result.organization, marker="THEIRS-001")
    return result


def _signed_in(user):
    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200, response.content[:300]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client


def _archive(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.content[:300]
    assert response["Content-Type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def _rows(archive, name):
    return list(csv.DictReader(io.StringIO(archive.read(name).decode())))


def _cancel(organization, *, days_ago: int = 0):
    from apps.platform.models import Subscription
    from apps.platform.services.subscriptions import set_status

    from tests.conftest import across_organizations

    set_status(organization, status="cancelled")
    if days_ago:
        with across_organizations():
            Subscription.objects.filter(
                organization=organization, is_active=True
            ).update(cancelled_at=timezone.now() - dt.timedelta(days=days_ago))
    organization.refresh_from_db()


# ---------------------------------------------------------------------------
# What is in it
# ---------------------------------------------------------------------------


def test_the_archive_holds_this_organization_and_no_other(ours, theirs):
    archive = _archive(_signed_in(ours.admin).get("/api/v1/org/export/"))

    codes = {row["employee_code"] for row in _rows(archive, "employees/employees.csv")}
    assert codes == {"OURS-001"}
    # Not just the employees file: nothing anywhere in the archive names the
    # other organization's employee.
    for name in archive.namelist():
        assert b"THEIRS-001" not in archive.read(name), f"{name} leaked another organization"
    members = {row["email"] for row in _rows(archive, "members.csv")}
    assert "admin@southwind.example" not in members
    assert "admin@northwind.example" in members


def test_encrypted_identifiers_are_withheld_and_named(ours, theirs):
    archive = _archive(_signed_in(ours.admin).get("/api/v1/org/export/"))

    header = _rows(archive, "employees/employees.csv")[0].keys()
    for column in ("pan", "aadhaar", "bank_account_number"):
        assert column not in header
    for name in archive.namelist():
        content = archive.read(name)
        for secret in (PAN, AADHAAR, BANK):
            assert secret.encode() not in content, f"{secret!r} left in {name}"

    readme = archive.read("README.txt").decode()
    assert "Withheld columns" in readme
    assert "pan, aadhaar, bank_account_number" in readme


def test_a_spreadsheet_formula_is_neutralised(plans):
    """
    The archive's contents were typed by users; opening a CSV runs `=` cells
    as formulas. The same neutralisation the import applies on the way in.
    """
    result = provision_organization(
        name="Formula Co", slug="formula", admin_email="admin@formula.example"
    )
    _usable(result.admin)
    _populate(result.organization, marker="F-1", first_name='=HYPERLINK("http://x.example")')

    archive = _archive(_signed_in(result.admin).get("/api/v1/org/export/"))
    first_names = [row["first_name"] for row in _rows(archive, "employees/employees.csv")]
    assert first_names == ["'=HYPERLINK(\"http://x.example\")"]


def test_the_export_is_in_the_customers_audit_trail(ours):
    from apps.audit.models import AuditLog

    _signed_in(ours.admin).get("/api/v1/org/export/")

    from tests.conftest import across_organizations

    with across_organizations():
        entry = AuditLog.objects.get(
            organization=ours.organization,
            entity_type="organization.Organization",
            after__event="organization_export",
        )
    assert entry.actor_id == ours.admin.pk
    assert entry.after["rows"]["employees/employees.csv"] == 1


# ---------------------------------------------------------------------------
# Who, and when
# ---------------------------------------------------------------------------


def test_a_cancelled_admin_can_export_and_read_nothing_else(ours):
    """
    The exception is ONE route. Cancelled means preserved, not published: the
    rest of the API still refuses, with the code the SPA routes on.
    """
    _cancel(ours.organization)
    client = _signed_in(ours.admin)

    archive = _archive(client.get("/api/v1/org/export/"))
    assert {r["employee_code"] for r in _rows(archive, "employees/employees.csv")} == {
        "OURS-001"
    }

    refused = client.get("/api/v1/employees/")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "organization_suspended"


def test_the_window_closes(ours):
    _cancel(ours.organization, days_ago=91)

    response = _signed_in(ours.admin).get("/api/v1/org/export/")

    assert response.status_code == 422
    assert "closed" in response.json()["error"]["message"]


def test_a_suspended_organization_is_restored_not_exported(ours):
    from apps.organization.models import OrgStatus

    ours.organization.status = OrgStatus.SUSPENDED
    ours.organization.save(update_fields=["status", "updated_at"])

    response = _signed_in(ours.admin).get("/api/v1/org/export/")

    assert response.status_code == 422
    assert "suspended" in response.json()["error"]["message"]


def test_only_the_admin_may_take_the_whole_record(ours):
    """An employee with a login -- even one with broad HR grants -- is not Admin."""
    from apps.accounts.models import Role, User, UserRole
    from apps.organization.models import OrganizationMembership

    with acting_as(None, organization=ours.organization):
        hr = User.objects.create_user(email="hr@northwind.example", password=PASSWORD)
        UserRole.objects.create(user=hr, role=Role.objects.get(code="hr_head"))
        OrganizationMembership.objects.create(organization=ours.organization, user=hr)

    response = _signed_in(hr).get("/api/v1/org/export/")

    assert response.status_code == 422
    assert "Admin" in response.json()["error"]["message"]


def test_the_platform_operator_does_not_export_customer_data(ours):
    from apps.accounts.models import User

    call_command("bootstrap_platform_admin", "--email", "ops@platform.example")
    operator = _usable(User.objects.get(email="ops@platform.example"))
    client = APIClient(HTTP_HOST="localhost")
    token = client.post(
        "/api/v1/auth/login/platform/",
        {"email": operator.email, "password": PASSWORD},
        format="json",
    ).json()["access"]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    response = client.get("/api/v1/org/export/")

    assert response.status_code == 422
    assert b"OURS-001" not in response.content


def test_me_offers_the_download_exactly_when_the_route_allows_it(ours):
    """The button and the answer come from one function, so they agree."""
    client = _signed_in(ours.admin)
    assert client.get("/api/v1/me/").json()["organization_export_available"] is True

    _cancel(ours.organization, days_ago=91)
    assert client.get("/api/v1/me/").json()["organization_export_available"] is False
    assert client.get("/api/v1/org/export/").status_code == 422
