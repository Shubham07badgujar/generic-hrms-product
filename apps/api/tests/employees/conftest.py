"""Fixtures for the employee hierarchy."""

from __future__ import annotations

import datetime as dt

import pytest

from core.access.catalog import DepartmentKind, Layer


@pytest.fixture
def hr_head(db, roles, org):
    """
    An HR Head WITH an Employee record, so their permissions actually resolve.

    Built directly rather than through `create_employee`, because the first HR
    Head has no creator — the same chicken-and-egg the Admin bootstrap solves.
    """
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    user = User.objects.create_user(
        email="hrhead@example.test", password="test-password-12345", first_name="Hema"
    )
    UserRole.objects.create(user=user, role=roles["hr_head"])
    Employee.objects.create(
        employee_code="EMP00001",
        user=user,
        first_name="Hema",
        last_name="Rao",
        department=org["departments"][DepartmentKind.HR],
        level=org["levels"][Layer.DEPARTMENT_HEAD],
        location=org["location"],
        date_of_joining=dt.date(2020, 1, 1),
    )
    return user


@pytest.fixture
def medical_director(db, roles, org):
    """A Layer-2 head in the medical function, to serve as a reporting manager."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    user = User.objects.create_user(
        email="meddir@example.test", password="test-password-12345", first_name="Dev"
    )
    UserRole.objects.create(user=user, role=roles["medical_director"])
    employee = Employee.objects.create(
        employee_code="EMP00002",
        user=user,
        first_name="Dev",
        last_name="Kulkarni",
        department=org["departments"][DepartmentKind.MEDICAL],
        level=org["levels"][Layer.DEPARTMENT_HEAD],
        location=org["location"],
        date_of_joining=dt.date(2020, 1, 1),
    )
    return employee


@pytest.fixture
def valid_payload(org, medical_director):
    """A therapist under the Medical Director — the canonical valid creation."""
    from core.access.catalog import DepartmentKind as DK

    return {
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya.nair@example.test",
        "role_code": "therapist",
        "department_id": org["departments"][DK.MEDICAL].pk,
        "level_id": org["levels"][Layer.STAFF].pk,
        "location_id": org["location"].pk,
        "designation_id": org["designation"].pk,
        "reporting_manager_id": medical_director.pk,
        "date_of_joining": dt.date(2026, 1, 15),
    }
