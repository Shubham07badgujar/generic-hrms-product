"""
Employee email rules: one address may serve both fields; duplicates across
OTHER people are refused by name, case-insensitively, before anything exists.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.employees.models import Employee

pytestmark = pytest.mark.django_db


@pytest.fixture
def hr(make_user, org):
    user = make_user("hr_head", email="hr.emailrules@x.test")
    Employee.objects.create(
        employee_code="EMP07900", first_name="Hema", user=user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )
    return user


def _create(hr, org, **overrides):
    client = APIClient()
    client.force_authenticate(user=hr)
    payload = {
        "first_name": "Upendra", "last_name": "Kelkar",
        "email": "upendra.same@gmail.test",
        "personal_email": "upendra.same@gmail.test",
        "role_code": "operational_head",
        "department_id": str(org["departments"]["operations"].pk),
        "designation_id": str(org["any_designation"].pk),
        "date_of_joining": str(dt.date.today()),
        **overrides,
    }
    return client.post("/api/v1/employees/", payload, format="json")


def test_the_same_address_may_be_both_company_and_personal(hr, org):
    response = _create(hr, org)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["user"]["email"] == "upendra.same@gmail.test"
    # A head (L2) needs no reporting manager.
    assert body["employee"]["full_name"] == "Upendra Kelkar"

    # …and that one address signs in.
    employee = Employee.objects.get(pk=body["employee"]["id"])
    assert employee.personal_email == "upendra.same@gmail.test"
    login = APIClient().post("/api/v1/auth/login/", {
        "email": "upendra.same@gmail.test", "password": "irrelevant-wrong",
    }, format="json")
    # Wrong password (none was set via API here) still proves the ACCOUNT
    # resolved: a generic 400, not a missing-user path difference — and the
    # user row exists with that login email.
    assert login.status_code == 400
    assert User.objects.filter(email="upendra.same@gmail.test").exists()


def test_duplicate_company_email_is_refused_by_name(hr, org):
    assert _create(hr, org).status_code == 201
    # Case and stray spaces must not smuggle a duplicate through.
    response = _create(hr, org, email="  UPENDRA.SAME@GMAIL.TEST ",
                       personal_email="fresh.personal@gmail.test")
    assert response.status_code == 400
    assert "Company Email already exists." in response.content.decode()
    # Nothing half-created.
    assert User.objects.filter(email__iexact="fresh.personal@gmail.test").count() == 0


def test_duplicate_personal_email_is_refused_by_name(hr, org):
    assert _create(hr, org).status_code == 201
    response = _create(hr, org, email="different.company@gmail.test",
                       personal_email=" Upendra.Same@gmail.test ")
    assert response.status_code == 400
    assert "Personal Email already exists." in response.content.decode()
    assert not User.objects.filter(email="different.company@gmail.test").exists()


def test_welcome_mail_goes_to_the_single_shared_address(
    hr, org, django_capture_on_commit_callbacks
):
    from django.core import mail

    with django_capture_on_commit_callbacks(execute=True):
        response = _create(hr, org)
    assert response.status_code == 201
    message = mail.outbox[-1]
    assert message.to == ["upendra.same@gmail.test"]
