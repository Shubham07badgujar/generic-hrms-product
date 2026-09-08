"""Fixtures for the exit workflow."""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from core.access.catalog import DepartmentKind, Layer

PASSWORD = "test-password-12345"


@pytest.fixture
def exit_config(db):
    """The default clearance template, plus the onboarding config a hire needs."""
    from apps.offboarding.seeds import seed_all as seed_exit
    from apps.onboarding.seeds import seed_all as seed_onboarding

    seed_onboarding()
    return seed_exit()


@pytest.fixture
def staff(db, roles, org):
    """One person per role the exit workflow touches."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    people: dict = {}
    counter = [3000]

    def hire(role_code: str, kind: str, name: str, layer) -> Employee:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@exit.test", password=PASSWORD, first_name=name
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
            date_of_joining=dt.date(2020, 1, 1),
        )
        people[role_code] = employee
        return employee

    hire("hr_head", DepartmentKind.HR, "Hema", Layer.DEPARTMENT_HEAD)
    hire("hr_manager", DepartmentKind.HR, "Hari", Layer.MANAGER)
    hire("medical_director", DepartmentKind.MEDICAL, "Meera", Layer.DEPARTMENT_HEAD)
    hire("senior_doctor", DepartmentKind.MEDICAL, "Sanjay", Layer.MANAGER)
    hire("finance_head", DepartmentKind.FINANCE, "Farah", Layer.DEPARTMENT_HEAD)
    hire("accounts_manager", DepartmentKind.FINANCE, "Anil", Layer.MANAGER)
    hire("operational_head", DepartmentKind.OPERATIONS, "Oindrila", Layer.DEPARTMENT_HEAD)

    # Departments need a head for the DEPARTMENT clearance bucket to resolve to
    # a person. Without it the item is created unassigned — legitimate, but not
    # what these tests are exercising.
    for role_code, kind in [
        ("medical_director", DepartmentKind.MEDICAL),
        ("hr_head", DepartmentKind.HR),
        ("finance_head", DepartmentKind.FINANCE),
        ("operational_head", DepartmentKind.OPERATIONS),
    ]:
        department = org["departments"][kind]
        department.head_employee = people[role_code]
        department.save(update_fields=["head_employee", "updated_at"])

    return people


@pytest.fixture
def admin_user(db, roles):
    from apps.accounts.models import User, UserRole

    user = User.objects.create_user(email="admin@exit.test", password=PASSWORD)
    UserRole.objects.create(user=user, role=roles["admin"])
    return user


@pytest.fixture
def leaver(db, staff, org, exit_config, first_login_done):
    """
    An employee created through the real service, reporting to the Medical
    Director — so department and manager clearance items resolve to real people.
    """
    from apps.employees.services.creation import create_employee

    result = create_employee(
        actor=staff["hr_head"].user,
        first_name="Priya",
        last_name="Nair",
        email="priya.nair@exit.test",
        role_code="therapist",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        designation_id=org["any_designation"].pk,
        level_id=org["levels"][Layer.STAFF].pk,
        location_id=org["location"].pk,
        reporting_manager_id=staff["medical_director"].pk,
        date_of_joining=dt.date(2024, 1, 15),
        temporary_password=PASSWORD,
    )
    # These tests sign in AS the leaver; they model someone who has already
    # completed the first-login password change.
    first_login_done(result.user)
    employee = result.employee
    # Confirmed rather than on probation: an exit from probation is a different
    # path, and these tests are about the standard one.
    from apps.employees.models import EmployeeStatus, ProbationStatus

    employee.status = EmployeeStatus.CONFIRMED
    employee.probation_status = ProbationStatus.CONFIRMED
    employee.confirmation_date = dt.date(2024, 7, 15)
    employee.save()
    # A leaver joined long ago: their onboarding is history, and the
    # onboarding gate must not stand between them and their resignation.
    from apps.onboarding.models import EmployeeOnboarding, OnboardingStatus

    EmployeeOnboarding.objects.filter(employee=employee).update(
        status=OnboardingStatus.COMPLETED
    )
    return employee


@pytest.fixture
def last_working_date():
    return timezone.localdate() + dt.timedelta(days=30)


@pytest.fixture
def exit_workflow(db, leaver, staff, last_working_date):
    """An open exit, mid notice period, with its clearance checklist issued."""
    from apps.offboarding.models import ExitType
    from apps.offboarding.services import start_exit

    return start_exit(
        employee=leaver,
        actor=staff["hr_head"].user,
        exit_type=ExitType.RESIGNATION,
        last_working_date=last_working_date,
        reason="Resignation accepted for the purposes of this test.",
    )


@pytest.fixture
def laptop(db, leaver, org, exit_config):
    """A returnable asset allocated to the leaver, so the asset gate binds."""
    from apps.assets.models import Asset, AssetCategory
    from apps.assets.services import allocate
    from apps.accounts.models import User

    category = AssetCategory.objects.get(code="laptop")
    asset = Asset.objects.create(
        asset_tag="LAP-EXIT-1",
        category=category,
        name="ThinkPad T14",
        serial_number="SN-EXIT-1",
        location=org["location"],
    )
    allocate(asset=asset, employee=leaver, actor=User.objects.get(email="hr_head@exit.test"))
    return asset


@pytest.fixture
def clear_everything():
    """
    Satisfy every gate so the approval path can be exercised.

    Deliberately a fixture rather than a helper inside each test: the gates are
    the subject of several tests, and clearing them by hand each time invites
    one of them being quietly skipped.
    """

    def _clear(workflow, *, actor, finance_actor=None):
        from apps.itaccounts.models import AccountStatus, CompanyEmailAccount
        from apps.offboarding.models import ClearanceStatus
        from django.utils import timezone as tz

        # Clearance items, bypassing ownership by writing directly — the
        # ownership rule has its own tests.
        workflow.clearance_items.filter(is_required=True).update(
            status=ClearanceStatus.COMPLETED, completed_at=tz.now(), completed_by=actor
        )

        # Assets.
        from apps.assets.models import AllocationStatus
        from apps.assets.services import return_asset

        for allocation in workflow.employee.asset_allocations.filter(
            status=AllocationStatus.ACTIVE
        ):
            return_asset(allocation=allocation, actor=actor)

        # Company account.
        account = CompanyEmailAccount.objects.filter(employee=workflow.employee).first()
        if account:
            account.status = AccountStatus.DEPROVISIONED
            account.deprovisioned_at = tz.now()
            account.save()

        # Settlement.
        from apps.offboarding.services import clear_settlement

        clear_settlement(settlement=workflow.settlement, actor=finance_actor or actor)
        workflow.refresh_from_db()
        return workflow

    return _clear
