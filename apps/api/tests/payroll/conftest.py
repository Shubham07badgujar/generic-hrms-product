"""Fixtures for payroll."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from core.access.catalog import DepartmentKind, Layer

PASSWORD = "test-password-12345"
PERIOD = (2025, 6)


@pytest.fixture
def rate_sets(db):
    """
    Every statutory rate set, loaded as DRAFT — the state a fresh install is in.

    Tests that need approval verify them explicitly via `verified_rate_sets`, so
    the difference between "computed" and "approvable" stays visible in the test
    that depends on it rather than hidden in shared setup.
    """
    from django.core.management import call_command

    call_command("seed_statutory", verbosity=0)

    from apps.statutory.models import StatutoryRuleSet

    return list(StatutoryRuleSet.objects.all())


@pytest.fixture
def verified_rate_sets(rate_sets):
    """Rate sets signed off by Finance, so a run can actually be approved."""
    from django.utils import timezone

    from apps.statutory.models import StatutoryRuleSet, VerificationStatus

    for rule_set in StatutoryRuleSet.objects.all():
        StatutoryRuleSet.objects.filter(pk=rule_set.pk).update(
            verification_status=VerificationStatus.VERIFIED,
            verified_checksum=rule_set.checksum,
            verified_at=timezone.now(),
        )
    return list(StatutoryRuleSet.objects.all())


@pytest.fixture
def finance(db, roles, org):
    """The finance team, and the segregation of duties between them."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    people: dict = {}
    counter = [7000]

    def hire(role_code: str, kind: str, name: str, layer) -> Employee:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@pay.test", password=PASSWORD, first_name=name
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

    hire("finance_head", DepartmentKind.FINANCE, "Farah", Layer.DEPARTMENT_HEAD)
    hire("accounts_manager", DepartmentKind.FINANCE, "Anil", Layer.MANAGER)
    hire("payroll_executive", DepartmentKind.FINANCE, "Pooja", Layer.EXECUTIVE)
    hire("hr_head", DepartmentKind.HR, "Hema", Layer.DEPARTMENT_HEAD)
    hire("therapist", DepartmentKind.MEDICAL, "Tara", Layer.STAFF)
    return people


@pytest.fixture
def components(db, finance):
    """The default component catalogue."""
    from apps.payroll import services
    from apps.payroll.models import SalaryComponent

    actor = finance["finance_head"].user
    for spec in services.DEFAULT_COMPONENTS:
        services.save_component(actor=actor, data=dict(spec))
    return {c.code: c for c in SalaryComponent.objects.all()}


@pytest.fixture
def salaried(db, finance, components):
    """
    Everyone on a salary structure.

    Basic 25,000 + HRA 40% + Special 8,000 = 43,000 gross, of which 25,000 is
    wage. Chosen so PF binds against its 15,000 ceiling, ESI does NOT apply
    (gross is above the limit), and Professional Tax lands in Maharashtra's top
    band — three different statutory behaviours in one fixture.
    """
    from apps.payroll import services

    actor = finance["finance_head"].user
    for employee in finance.values():
        services.create_salary_structure(
            actor=actor,
            employee=employee,
            ctc_annual=Decimal("600000.00"),
            valid_from=dt.date(2024, 4, 1),
            revision_reason="Initial structure",
            lines=[
                {"component": components["BASIC"].pk, "value": Decimal("25000.00")},
                {"component": components["HRA"].pk, "value": Decimal("40")},
                {"component": components["SPECIAL"].pk, "value": Decimal("8000.00")},
            ],
        )
    return finance


@pytest.fixture
def draft_run(db, finance, salaried, rate_sets):
    """A run created by the payroll executive, not yet processed."""
    from apps.payroll import services

    return services.create_run(
        actor=finance["payroll_executive"].user,
        period_year=PERIOD[0],
        period_month=PERIOD[1],
    )


@pytest.fixture
def processed_run(db, draft_run, finance):
    from apps.payroll import services

    return services.process_run(draft_run, actor=finance["payroll_executive"].user)


@pytest.fixture
def approvable_run(db, finance, salaried, verified_rate_sets):
    """A processed run against VERIFIED rates — the only kind that can be approved."""
    from apps.payroll import services

    run = services.create_run(
        actor=finance["payroll_executive"].user,
        period_year=PERIOD[0],
        period_month=PERIOD[1],
    )
    return services.process_run(run, actor=finance["payroll_executive"].user)


@pytest.fixture
def auth(api):
    """Sign a user in and return the client."""

    def _auth(user):
        api.force_authenticate(user=user)
        return api

    return _auth
