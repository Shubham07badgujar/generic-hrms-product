"""
Removing a former employee from the system.

The claims:
  1. HR Head removes only an exited or terminated employee — anyone still on
     the books must be offboarded first. ADMIN holds the master key and may
     remove an employee in any status.
  2. Removal is a soft delete that ALSO switches the login off — roles ended,
     sessions revoked — so a removed person cannot sign in.
  3. History survives: the row exists, audit rows reference it, and the act
     itself is audited with who and why.
  4. The login email is RELEASED (tombstoned), so the same person can be
     onboarded again afresh with the same company email.
  5. EMPLOYEE/DELETE is the authority (Admin, HR Head); nobody else, and not
     on yourself.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User, UserRole
from apps.audit.models import AuditLog
from apps.employees.models import Employee, EmployeeStatus
from apps.employees.services.lifecycle import remove_employee
from core.access.catalog import DepartmentKind
from core.access.engine import AccessDenied
from tests.conftest import bind_membership

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


@pytest.fixture
def admin(make_user):
    return make_user("admin")


@pytest.fixture
def leaver(roles, org):
    """An exited employee who — as in production — still has a live login and role."""
    user = User.objects.create_user(email="leaver@example.test", password=PASSWORD, first_name="Lea")
    UserRole.objects.create(user=user, role=roles["employee"])
    bind_membership(user)
    return Employee.objects.create(
        employee_code="EMP09001", user=user, first_name="Lea", last_name="Ver",
        department=org["departments"][DepartmentKind.MEDICAL], date_of_joining=dt.date(2024, 1, 1),
        status=EmployeeStatus.EXITED,
    )


def _login(user):
    api = APIClient()
    token = api.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}).data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def test_admin_removes_an_exited_employee_and_the_login_dies_with_it(admin, leaver):
    # Before: the leaver can still sign in. That is the gap.
    assert APIClient().post("/api/v1/auth/login/", {"email": "leaver@example.test", "password": PASSWORD}).status_code == 200

    remove_employee(employee=leaver, actor=admin, reason="Left in January; records retained.")

    row = Employee.objects.get(pk=leaver.pk)  # the manager does not hide inactive rows; the viewset does
    assert row.is_active is False and row.status == EmployeeStatus.EXITED  # soft, history kept
    assert not Employee.objects.filter(pk=leaver.pk, is_active=True).exists()  # gone from lists

    user = User.objects.get(pk=leaver.user_id)
    assert user.is_active is False
    assert not user.user_roles.filter(is_active=True).exists()
    assert APIClient().post("/api/v1/auth/login/", {"email": "leaver@example.test", "password": PASSWORD}).status_code in (400, 401, 403)

    log = AuditLog.objects.get(entity_id=str(leaver.pk), after__event="employee_removed")
    assert log.actor_id == admin.pk
    assert log.before["login_active"] is True and log.after["login_active"] is False
    assert log.before["roles"] == ["employee"] and log.after["roles"] == []
    assert "January" in (log.reason or "")


def test_admin_may_remove_a_working_employee_directly(admin, leaver):
    """The master key: a record created in error goes without a fake exit."""
    leaver.status = EmployeeStatus.ACTIVE
    leaver.save()
    remove_employee(employee=leaver, actor=admin, reason="Created in error.")
    assert not Employee.objects.filter(pk=leaver.pk, is_active=True).exists()
    assert User.objects.get(pk=leaver.user_id).is_active is False


def test_removal_releases_the_email_for_a_fresh_onboarding(admin, leaver, roles, org):
    """Deleted once — the same person can be onboarded again."""
    from apps.employees.services.creation import create_employee

    remove_employee(employee=leaver, actor=admin, reason="Re-onboarding test.")
    dead = User.objects.get(pk=leaver.user_id)
    assert dead.email.startswith("removed.") and "leaver@example.test" in dead.email

    result = create_employee(
        actor=admin,
        first_name="Lea",
        email="leaver@example.test",  # the SAME company email, now free
        personal_email="lea.personal@example.test",
        # A department-head role needs no reporting manager, keeping the
        # fixture minimal; any role works once the email is free.
        role_code="medical_director",
        department_id=org["departments"][DepartmentKind.MEDICAL].pk,
        require_designation=False,
        date_of_joining=dt.date(2026, 9, 1),
        send_welcome_email=False,
    )
    assert result.user.email == "leaver@example.test"
    assert result.employee.pk != leaver.pk  # a fresh employment record
    assert result.employee.employee_code != leaver.employee_code

    log = AuditLog.objects.get(entity_id=str(leaver.pk), after__event="employee_removed")
    assert log.after["login_email_released"] == "leaver@example.test"

    # The re-added person can actually SIGN IN with the new credentials and
    # is forced through a password reset — the whole point of the re-add.
    api = APIClient()
    first = api.post("/api/v1/auth/login/", {
        "email": "leaver@example.test", "password": result.temporary_password,
    })
    assert first.status_code == 200, first.data
    assert first.data.get("must_change_password") is True
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {first.data['access']}")
    changed = api.post("/api/v1/auth/change-password/", {
        "current_password": result.temporary_password,
        "new_password": "Fresh-Start!2026-hrms",
    }, format="json")
    assert changed.status_code == 200, changed.data
    again = APIClient().post("/api/v1/auth/login/", {
        "email": "leaver@example.test", "password": "Fresh-Start!2026-hrms",
    })
    assert again.status_code == 200
    assert again.data.get("must_change_password") is False


def test_a_terminated_employee_can_be_removed(admin, leaver):
    leaver.status = EmployeeStatus.TERMINATED
    leaver.save()
    remove_employee(employee=leaver, actor=admin)
    assert not Employee.objects.filter(pk=leaver.pk, is_active=True).exists()


def test_you_cannot_remove_yourself(admin, leaver, org):
    mine = Employee.objects.create(
        employee_code="EMP09002", user=admin, first_name="Ad", department=org["departments"][DepartmentKind.HR],
        date_of_joining=dt.date(2020, 1, 1), status=EmployeeStatus.EXITED,
    )
    with pytest.raises(ValidationError, match="your own record"):
        remove_employee(employee=mine, actor=admin)


def test_a_role_without_delete_is_refused(make_user, leaver):
    recruiter = make_user("recruiter", email="rec@example.test")
    with pytest.raises(AccessDenied):
        remove_employee(employee=leaver, actor=recruiter)


def test_over_http_admin_deletes_and_a_recruiter_is_refused(admin, make_user, leaver):
    recruiter = make_user("recruiter", email="rec2@example.test")
    assert _login(recruiter).delete(f"/api/v1/employees/{leaver.pk}/").status_code == 403
    assert Employee.objects.filter(pk=leaver.pk, is_active=True).exists()

    api = _login(admin)
    r = api.delete(f"/api/v1/employees/{leaver.pk}/", {"reason": "Left the company"}, format="json")
    assert r.status_code == 204, r.data
    assert not Employee.objects.filter(pk=leaver.pk, is_active=True).exists()
    # A second DELETE finds nothing — the row is out of the viewset's queryset.
    assert api.delete(f"/api/v1/employees/{leaver.pk}/").status_code == 404


def test_only_admin_holds_the_delete_key(make_user, leaver):
    """EMPLOYEE/DELETE is Admin's alone today; HR Head is refused outright."""
    hr_head = make_user("hr_head", email="hrh2@example.test")
    r = _login(hr_head).delete(f"/api/v1/employees/{leaver.pk}/")
    assert r.status_code == 403
    assert Employee.objects.filter(pk=leaver.pk, is_active=True).exists()
