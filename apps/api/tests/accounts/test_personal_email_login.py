"""
The personal email as a login identifier, and the only credentials inbox.

HR records both addresses at hire; the setup email goes ONLY to the personal
address, so that address must also open the door. A personal email shared by
two employees resolves to nobody — the same generic failure as any unknown
address, so account existence stays unenumerable.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.employees.models import Employee

pytestmark = pytest.mark.django_db

PASSWORD = "test-password-12345"


@pytest.fixture
def worker(make_user, org):
    user = make_user("employee", email="company.addr@clinic.test")
    return Employee.objects.create(
        employee_code="EMP07800", first_name="Personal", user=user,
        personal_email="my.own.inbox@gmail.test",
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )


def _login(email, password=PASSWORD):
    return APIClient().post(
        "/api/v1/auth/login/", {"email": email, "password": password}, format="json"
    )


def test_both_addresses_open_the_same_account(worker):
    assert _login("company.addr@clinic.test").status_code == 200
    response = _login("MY.OWN.INBOX@gmail.test")  # case-insensitive too
    assert response.status_code == 200
    assert response.json()["access"]


def test_wrong_password_via_personal_email_still_counts_against_lockout(worker):
    for _ in range(3):
        assert _login("my.own.inbox@gmail.test", "wrong-password").status_code == 400
    worker.user.refresh_from_db()
    assert worker.user.failed_login_count >= 3


def test_a_shared_personal_email_resolves_to_nobody(worker, make_user, org):
    twin_user = make_user("employee", email="company.two@clinic.test")
    Employee.objects.create(
        employee_code="EMP07801", first_name="Twin", user=twin_user,
        personal_email="my.own.inbox@gmail.test",
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )
    response = _login("my.own.inbox@gmail.test")
    assert response.status_code == 400
    assert "Invalid email or password" in response.content.decode()
    # Each person's company email keeps working.
    assert _login("company.addr@clinic.test").status_code == 200


def test_welcome_mail_recipient_and_wording(make_user, org, django_capture_on_commit_callbacks):
    from apps.employees.services.creation import create_employee

    hr_user = make_user("hr_head", email="hr.creator2@clinic.test")
    Employee.objects.create(
        employee_code="EMP07803", first_name="Hema", user=hr_user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )
    manager = Employee.objects.get(employee_code="EMP07803")

    with django_capture_on_commit_callbacks(execute=True):
        create_employee(
            actor=hr_user,
            first_name="Fresh", email="fresh.hire2@clinic.test",
            personal_email="fresh.personal2@gmail.test",
            role_code="employee",
            department_id=org["departments"]["operations"].pk,
            designation_id=org["any_designation"].pk,
            date_of_joining=dt.date.today(),
            reporting_manager_id=manager.pk,
        )

    message = mail.outbox[-1]
    # ONLY the personal inbox — the company address gets nothing.
    assert message.to == ["fresh.personal2@gmail.test"]
    assert "fresh.hire2@clinic.test" not in message.to
    # And the body no longer claims the personal address cannot log in.
    assert "cannot be used to log in" not in message.body
    assert "both open the same account" in message.body
