"""Fixtures for the employee lifecycle: onboarding, probation, documents, assets."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from core.access.catalog import DepartmentKind, Layer
from tests.conftest import bind_membership

PASSWORD = "test-password-12345"


@pytest.fixture
def lifecycle_config(db):
    """Document types, the default onboarding template, letters, asset categories."""
    from apps.onboarding.seeds import seed_all

    return seed_all()


@pytest.fixture
def staff(db, roles, org):
    """One person per role the lifecycle tests need."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    people: dict = {}
    counter = [2000]

    def hire(role_code: str, kind: str, name: str, layer) -> Employee:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@lifecycle.test", password=PASSWORD, first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)
        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:05d}",
            user=user,
            first_name=name,
            last_name=role_code.replace("_", " ").title(),
            department=org["departments"][kind],
            level=org["levels"][layer],
            location=org["location"],
            date_of_joining=dt.date(2020, 1, 1),
        )
        people[role_code] = employee
        return employee

    hire("hr_head", DepartmentKind.HR, "Hema", Layer.DEPARTMENT_HEAD)
    hire("hr_manager", DepartmentKind.HR, "Hari", Layer.MANAGER)
    hire("medical_director", DepartmentKind.MEDICAL, "Meera", Layer.DEPARTMENT_HEAD)
    hire("senior_doctor", DepartmentKind.MEDICAL, "Sanjay", Layer.MANAGER)
    hire("therapist", DepartmentKind.MEDICAL, "Tara", Layer.STAFF)
    hire("operational_head", DepartmentKind.OPERATIONS, "Oindrila", Layer.DEPARTMENT_HEAD)
    hire("recruiter", DepartmentKind.HR, "Ravi", Layer.EXECUTIVE)
    return people


@pytest.fixture
def admin_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="admin@lifecycle.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["admin"])
    bind_membership(user)
    return user


@pytest.fixture
def ceo_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="ceo@lifecycle.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["ceo"])
    bind_membership(user)
    return user


@pytest.fixture
def new_hire(db, staff, org, lifecycle_config, first_login_done):
    """
    A therapist created through the real service, so they arrive with a
    checklist and a probation clock exactly as a production hire would.
    """
    from apps.employees.services.creation import create_employee

    result = create_employee(
        actor=staff["hr_head"].user,
        first_name="Priya",
        last_name="Nair",
        email="priya.nair@lifecycle.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        location_id=org["location"].pk,
        reporting_manager_id=staff["medical_director"].pk,
        date_of_joining=timezone.localdate(),
        # A usable password, so the API tests can sign in AS this person and
        # exercise self-service scope rather than only asserting it in theory.
        temporary_password=PASSWORD,
    )
    # These suites sign in AS the new hire; they model someone who has already
    # completed the first-login password change.
    first_login_done(result.user)
    return result


@pytest.fixture
def employee(new_hire):
    return new_hire.employee


@pytest.fixture
def asset(db, org):
    """A laptop: serialised and returnable, so it blocks an exit."""
    from apps.assets.models import Asset, AssetCategory

    category = AssetCategory.objects.get(code="laptop")
    return Asset.objects.create(
        asset_tag="LAP-0001",
        category=category,
        name="ThinkPad T14",
        serial_number="SN-000001",
        location=org["location"],
    )


@pytest.fixture
def stationery_asset(db, lifecycle_config):
    """A non-returnable item, which must NOT block an exit."""
    from apps.assets.models import Asset, AssetCategory

    category = AssetCategory.objects.get(code="stationery")
    return Asset.objects.create(
        asset_tag="STA-0001", category=category, name="Notebook"
    )


@pytest.fixture
def pdf_upload():
    """A minimal valid PDF, for document upload tests."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    def _make(name="proof.pdf", content_type="application/pdf", size=1024):
        return SimpleUploadedFile(name, b"%PDF-1.4\n" + b"0" * size, content_type=content_type)

    return _make
