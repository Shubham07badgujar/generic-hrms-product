"""
Two complete, independent organizations.

Everything here is built twice, by the same factory, from nothing shared: two
organizations, each with its own role catalogue, structure, principals and HR
data. That symmetry is the point -- a test can then ask "can A reach B's row?"
and the answer cannot be an accident of one side being set up differently.

Deliberately does NOT use the session organization or the autouse binding from
tests/conftest.py. A test about isolation must not inherit its tenant from a
fixture; each block below binds its own explicitly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from core.access.catalog import DepartmentKind, Layer
from core.middleware import acting_as

from ._extra import build_extra_rows

PASSWORD = "test-password-12345"


@dataclass
class World:
    """One organization and everything in it."""

    organization: object
    admin: object
    hr: object
    worker: object          # a plain employee principal
    department: object
    location: object
    designation: object
    level: object
    hr_employee: object
    worker_employee: object
    rows: dict = field(default_factory=dict)   # label -> instance, for the matrix

    @property
    def slug(self) -> str:
        return self.organization.slug


def _build(slug: str, name: str) -> World:
    from apps.accounts.models import User, UserRole
    from apps.accounts.services.roles import seed_roles
    from apps.assets.models import Asset, AssetCategory
    from apps.attendance.models import AttendanceRecord
    from apps.employees.models import Employee
    from apps.leave.models import LeaveRequest, LeaveType
    from apps.organization.models import (
        Department,
        Designation,
        EmployeeLevel,
        Location,
        Organization,
        OrganizationMembership,
        OrgStatus,
    )
    from apps.payroll.models import PayrollRun
    from apps.recruitment.models import Candidate

    # Creating a tenant is a PLATFORM act, so it runs with no organization
    # bound -- which is what provisioning does, and what makes the audit row
    # for "this organization was created" org-less rather than stamped with
    # whichever tenant happened to be in force. Without this the fixture wrote
    # that row against the session organization, which is both untrue and a
    # foreign key into a row this file never creates.
    with acting_as(None, organization=None):
        organization = Organization.objects.create(
            name=name, slug=slug, status=OrgStatus.ACTIVE
        )

    with acting_as(None, organization=organization):
        seed_roles(organization=organization)
        from apps.accounts.models import Role

        roles = {r.code: r for r in Role.objects.filter(organization=organization)}

        location = Location.objects.create(
            name=f"{name} HQ", code="HO", city="Pune", state="MH", is_head_office=True
        )
        department = Department.objects.create(
            name="People", code="HR", kind=DepartmentKind.HR
        )
        level = EmployeeLevel.objects.create(
            name="Staff", code="L5", layer=Layer.STAFF
        )
        head_level = EmployeeLevel.objects.create(
            name="Head", code="L2", layer=Layer.DEPARTMENT_HEAD
        )
        designation = Designation.objects.create(title="Officer", department=None)

        def principal(role_code, email_local, *, employee=True, code=None, lvl=None):
            user = User.objects.create_user(
                email=f"{email_local}@{slug}.example",
                password=PASSWORD,
                first_name=role_code.replace("_", " ").title(),
            )
            UserRole.objects.create(user=user, role=roles[role_code])
            OrganizationMembership.objects.create(organization=organization, user=user)
            record = None
            if employee:
                record = Employee.objects.create(
                    employee_code=code,
                    user=user,
                    first_name=role_code.replace("_", " ").title(),
                    last_name=name.split()[0],
                    department=department,
                    designation=designation,
                    location=location,
                    level=lvl or level,
                    date_of_joining=dt.date(2024, 1, 1),
                )
            return user, record

        admin, _ = principal("admin", "admin", employee=False)
        hr, hr_employee = principal(
            "hr_head", "hr", code="EMP001", lvl=head_level
        )
        worker, worker_employee = principal("employee", "worker", code="EMP002")

        # No user, no role: a record for the offboarding rows to hang off, so
        # the employee the rest of the fixture uses is not simultaneously
        # resigning. `Employee.user` is nullable precisely for people who never
        # get a login.
        spare_employee = Employee.objects.create(
            employee_code="EMP003",
            first_name="Spare",
            last_name=name.split()[0],
            department=department,
            designation=designation,
            location=location,
            level=level,
            date_of_joining=dt.date(2024, 1, 1),
        )

        # A row per resource the brief names, so the matrix has something to
        # ask for on both sides.
        leave_type = LeaveType.objects.create(name="Casual Leave", code="CL")
        rows = {
            "employee": worker_employee,
            "department": department,
            "location": location,
            "leave_type": leave_type,
            "leave_request": LeaveRequest.objects.create(
                employee=worker_employee,
                leave_type=leave_type,
                start_date=dt.date(2025, 6, 2),
                end_date=dt.date(2025, 6, 2),
                days=Decimal("1.0"),
                reason="Cross-tenant fixture",
            ),
            "attendance": AttendanceRecord.objects.create(
                employee=worker_employee, date=dt.date(2025, 6, 3)
            ),
            "payroll_run": PayrollRun.objects.create(
                period_year=2025, period_month=6, run_by=hr
            ),
            "asset": Asset.objects.create(
                asset_tag="AST-001",
                category=AssetCategory.objects.create(name="Laptops", code="LAPTOP"),
                name="ThinkPad",
            ),
            # Consent is required by a CheckConstraint -- the DPDP lawful-basis
            # rule. A candidate row cannot exist without one, which is the
            # product working as designed.
            "candidate": Candidate.objects.create(
                first_name="Priya", last_name="Sharma",
                email=f"priya@{slug}.example",
                consent_given=True,
            ),
        }

        # Everything else the product exposes. Kept in its own module because
        # it is bulk, and because the walker's coverage should grow by adding
        # to a list rather than by editing the fixture's shape.
        rows.update(
            build_extra_rows(
                organization=organization,
                slug=slug,
                roles=roles,
                department=department,
                location=location,
                designation=designation,
                level=level,
                hr=hr,
                hr_employee=hr_employee,
                worker_employee=worker_employee,
                spare_employee=spare_employee,
                leave_type=leave_type,
                asset=rows["asset"],
                candidate=rows["candidate"],
                payroll_run=rows["payroll_run"],
            )
        )

    return World(
        organization=organization,
        admin=admin,
        hr=hr,
        worker=worker,
        department=department,
        location=location,
        designation=designation,
        level=level,
        hr_employee=hr_employee,
        worker_employee=worker_employee,
        rows=rows,
    )


@pytest.fixture
def org_a(db) -> World:
    return _build("acme-health", "Acme Health")


@pytest.fixture
def org_b(db) -> World:
    return _build("globex-tech", "Globex Tech")


@pytest.fixture
def api_for():
    """An APIClient authenticated as a given principal."""
    from rest_framework.test import APIClient

    def _for(user):
        client = APIClient(HTTP_HOST="localhost")
        response = client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200, (user.email, response.content[:200])
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
        return client

    return _for
