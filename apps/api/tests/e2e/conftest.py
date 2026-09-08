"""
End-to-end fixtures.

Re-exports the recruitment fixtures rather than rebuilding them. The whole
point of an E2E test is that it drives the SAME machinery the focused tests
drive; a parallel set of fixtures here would let the two drift, and the E2E
would then be certifying a pipeline nobody else runs.
"""

from __future__ import annotations

import pytest

from tests.conftest import bind_membership

# The import fixtures too, so an E2E can start from a platform export.
from tests.imports.conftest import (  # noqa: F401  (re-exported fixtures)
    upload,
    workindia_xlsx,
)
from tests.recruitment.conftest import (  # noqa: F401  (re-exported fixtures)
    admin_user,
    at_stage,
    completed_interview,
    drive_to_selection,
    make_application,
    office_boy_job,
    staff,
    therapist_job,
    workflows,
)


@pytest.fixture
def finance_staff(db, roles, org, staff):
    """
    The finance roles, added to the recruitment cast.

    The recruitment fixture stops at the roles those two workflows need. The
    E2E carries on into payroll, which needs someone to process a run and a
    DIFFERENT someone to approve it — the segregation gate refuses otherwise,
    so a single finance user would fail the test for the right reason at the
    wrong moment.
    """
    import datetime as dt

    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee
    from core.access.catalog import DepartmentKind

    counter = [4000]

    def hire(role_code: str, name: str) -> Employee:
        counter[0] += 1
        user = User.objects.create_user(
            email=f"{role_code}@e2e.test", password="test-password-12345", first_name=name
        )
        UserRole.objects.create(user=user, role=roles[role_code])
        bind_membership(user)
        employee = Employee.objects.create(
            employee_code=f"EMP{counter[0]:05d}",
            user=user,
            first_name=name,
            last_name=role_code.replace("_", " ").title(),
            department=org["departments"][DepartmentKind.FINANCE],
            location=org["location"],
            date_of_joining=dt.date(2020, 1, 1),
        )
        staff[role_code] = employee
        return employee

    hire("finance_head", "Farah")
    hire("accounts_manager", "Anil")
    return staff


@pytest.fixture
def onboarding_config(db):
    """Templates the conversion path needs."""
    from apps.onboarding.seeds import seed_all

    return seed_all()


@pytest.fixture
def statutory_rates(db):
    """
    Verified rates, so payroll can be approved.

    Verification is done here explicitly rather than by the seeder — the seeder
    cannot produce a verified rate set, by design, and an E2E that reached
    approval without someone signing off would be testing a system that does
    not exist.
    """
    from django.core.management import call_command
    from django.utils import timezone

    from apps.statutory.models import StatutoryRuleSet, VerificationStatus

    call_command("seed_statutory", verbosity=0)
    for rule_set in StatutoryRuleSet.objects.all():
        StatutoryRuleSet.objects.filter(pk=rule_set.pk).update(
            verification_status=VerificationStatus.VERIFIED,
            verified_checksum=rule_set.checksum,
            verified_at=timezone.now(),
        )
    return StatutoryRuleSet.objects.all()


@pytest.fixture
def auth(api):
    def _auth(user):
        api.force_authenticate(user=user)
        return api

    return _auth
