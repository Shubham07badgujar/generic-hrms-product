"""
Cutover fixtures.

A full organisation: two operational departments with heads, managers and
staff, plus the system principals. Cross-department isolation cannot be tested
with one department, and TEAM scope means nothing without a real reporting
line, so the shape here is the minimum that makes the assertions honest.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.access.catalog import DepartmentKind, Layer
from tests.conftest import bind_membership

PASSWORD = "test-password-12345"

#: Every grantable role, with the department and layer it belongs in.
ROLE_PLACEMENT = [
    ("medical_director", DepartmentKind.MEDICAL, Layer.DEPARTMENT_HEAD),
    ("senior_doctor", DepartmentKind.MEDICAL, Layer.MANAGER),
    ("clinic_doctor", DepartmentKind.MEDICAL, Layer.EXECUTIVE),
    ("therapist", DepartmentKind.MEDICAL, Layer.STAFF),
    ("operational_head", DepartmentKind.OPERATIONS, Layer.DEPARTMENT_HEAD),
    ("operations_manager", DepartmentKind.OPERATIONS, Layer.MANAGER),
    ("cre", DepartmentKind.OPERATIONS, Layer.EXECUTIVE),
    ("executive", DepartmentKind.OPERATIONS, Layer.EXECUTIVE),
    ("office_boy", DepartmentKind.OPERATIONS, Layer.STAFF),
    ("employee", DepartmentKind.OPERATIONS, Layer.STAFF),
    ("hr_head", DepartmentKind.HR, Layer.DEPARTMENT_HEAD),
    ("hr_manager", DepartmentKind.HR, Layer.MANAGER),
    ("recruiter", DepartmentKind.HR, Layer.EXECUTIVE),
    ("finance_head", DepartmentKind.FINANCE, Layer.DEPARTMENT_HEAD),
    ("accounts_manager", DepartmentKind.FINANCE, Layer.MANAGER),
    ("payroll_executive", DepartmentKind.FINANCE, Layer.EXECUTIVE),
]

#: The two roles with no Employee record. Structural, not an omission.
SYSTEM_ROLES = ["admin", "ceo"]


@pytest.fixture(scope="package", autouse=True)
def _restore_urlconf():
    """
    Put the URLconf back the way this package found it.

    `test_ceo_write_walker` walks the resolver at MODULE IMPORT to build its
    parametrised route list, which imports `config.urls` during collection.
    The bootstrap suite reloads that module to prove an unset token removes the
    route, and it does not expect to find it already imported and resolved by
    something else. Reloading here on the way out means this package cannot
    change what a later suite sees.
    """
    yield

    import importlib

    from django.urls import clear_url_caches

    import apps.accounts.api.urls as auth_urls
    import config.urls as root_urls

    importlib.reload(auth_urls)
    importlib.reload(root_urls)
    clear_url_caches()


@pytest.fixture
def everyone(db, roles, org):
    """
    One live user per role, all 18.

    Returns `{role_code: user}`. Employees are wired into a real reporting tree
    so TEAM scope resolves to something other than an empty set.
    """
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    users: dict = {}
    employees: dict = {}
    counter = [9000]

    heads: dict = {}
    managers: dict = {}

    for role_code, kind, layer in ROLE_PLACEMENT:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@cutover.test",
            password=PASSWORD,
            first_name=role_code.replace("_", " ").title(),
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)

        manager = None
        if layer == Layer.MANAGER:
            manager = heads.get(kind)
        elif layer in (Layer.EXECUTIVE, Layer.STAFF):
            manager = managers.get(kind) or heads.get(kind)

        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:05d}",
            user=user,
            first_name=role_code.replace("_", " ").title(),
            last_name="Cutover",
            work_email=f"{role_code}@cutover.test",
            department=org["departments"][kind],
            level=org["levels"][layer],
            location=org["location"],
            date_of_joining=dt.date(2023, 1, 10),
            reporting_manager=manager,
        )

        if layer == Layer.DEPARTMENT_HEAD:
            heads[kind] = employee
        elif layer == Layer.MANAGER:
            managers[kind] = employee

        users[role_code] = user
        employees[role_code] = employee

    for kind, head in heads.items():
        department = org["departments"][kind]
        department.head_employee = head
        department.save(update_fields=["head_employee", "updated_at"])

    for role_code in SYSTEM_ROLES:
        user = User.objects.create_user(
            email=f"{role_code}@cutover.test", password=PASSWORD
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)
        users[role_code] = user

    users["_employees"] = employees
    return users


@pytest.fixture
def staff(everyone):
    """The Employee records, keyed by role code."""
    return everyone["_employees"]


@pytest.fixture
def auth(api):
    def _auth(user):
        api.force_authenticate(user=user)
        return api

    return _auth


@pytest.fixture
def anon(api):
    api.force_authenticate(user=None)
    return api
