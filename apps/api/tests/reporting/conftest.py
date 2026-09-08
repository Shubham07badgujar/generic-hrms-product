"""Fixtures for BI, notifications and audit."""

from __future__ import annotations

import datetime as dt

import pytest

from core.access.catalog import DepartmentKind, Layer

PASSWORD = "test-password-12345"


@pytest.fixture
def people(db, roles, org):
    """
    Two departments with their own heads, managers and staff.

    The shape matters: isolation cannot be tested with one department, and a
    manager needs a report for TEAM scope to mean anything.
    """
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    made: dict = {}
    counter = [8000]

    def hire(role_code, kind, name, layer, *, manager=None, email=None):
        counter[0] += 1
        user = User.objects.create_user(
            email=email or f"{role_code}@bi.test", password=PASSWORD, first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:05d}",
            user=user,
            first_name=name,
            last_name=role_code.replace("_", " ").title(),
            department=org["departments"][kind],
            level=org["levels"][layer],
            location=org["location"],
            date_of_joining=dt.date(2023, 1, 10),
            reporting_manager=manager,
        )
        made[email or role_code] = employee
        return employee

    # Medical
    med_head = hire("medical_director", DepartmentKind.MEDICAL, "Meera", Layer.DEPARTMENT_HEAD)
    med_mgr = hire("senior_doctor", DepartmentKind.MEDICAL, "Sanjay", Layer.MANAGER,
                   manager=med_head)
    hire("therapist", DepartmentKind.MEDICAL, "Tara", Layer.STAFF, manager=med_mgr)
    hire("clinic_doctor", DepartmentKind.MEDICAL, "Chandni", Layer.EXECUTIVE, manager=med_mgr)

    # Operations
    ops_head = hire("operational_head", DepartmentKind.OPERATIONS, "Oindrila",
                    Layer.DEPARTMENT_HEAD)
    ops_mgr = hire("operations_manager", DepartmentKind.OPERATIONS, "Omkar", Layer.MANAGER,
                   manager=ops_head)
    hire("cre", DepartmentKind.OPERATIONS, "Chetan", Layer.EXECUTIVE, manager=ops_mgr)
    hire("office_boy", DepartmentKind.OPERATIONS, "Om", Layer.STAFF, manager=ops_mgr)

    # HR and Finance
    hire("hr_head", DepartmentKind.HR, "Hema", Layer.DEPARTMENT_HEAD)
    hire("finance_head", DepartmentKind.FINANCE, "Farah", Layer.DEPARTMENT_HEAD)

    for role_code, kind in [
        ("medical_director", DepartmentKind.MEDICAL),
        ("operational_head", DepartmentKind.OPERATIONS),
        ("hr_head", DepartmentKind.HR),
        ("finance_head", DepartmentKind.FINANCE),
    ]:
        department = org["departments"][kind]
        department.head_employee = made[role_code]
        department.save(update_fields=["head_employee", "updated_at"])

    return made


@pytest.fixture
def ceo_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="ceo@bi.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["ceo"])
    return user


@pytest.fixture
def admin_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="admin@bi.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["admin"])
    return user


@pytest.fixture
def auth(api):
    def _auth(user):
        api.force_authenticate(user=user)
        return api

    return _auth
