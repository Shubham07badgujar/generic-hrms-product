"""
Setting PAN / Aadhaar / UAN / bank details — the one write path.

HR (organisation-wide employee-edit) sets them; everyone reads them masked;
neither the employee themselves nor a team-scoped manager may write them —
a changed bank account is where salary lands.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.employees.models import Employee

pytestmark = pytest.mark.django_db


@pytest.fixture
def worker(make_user, org):
    user = make_user("employee", email="ident.self@x.test")
    return Employee.objects.create(
        employee_code="EMP07750", first_name="Ident", user=user,
        department=org["departments"]["operations"], date_of_joining=dt.date(2025, 1, 1),
    )


@pytest.fixture
def hr(make_user, org):
    user = make_user("hr_head", email="ident.hr@x.test")
    Employee.objects.create(
        employee_code="EMP07751", first_name="Hema", user=user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_hr_fills_the_identifiers_and_reads_stay_masked(hr, worker):
    client = _client(hr)
    response = client.patch(f"/api/v1/employees/{worker.pk}/identifiers/", {
        "pan": "abcde1234f",              # lowercase in, normalised out
        "uan": "100200300400",
        "bank_account_number": "123456789012",
        "bank_ifsc": "hdfc0001234",
        "bank_name": "HDFC Bank",
    }, format="json")
    assert response.status_code == 200, response.content

    body = response.json()
    # The response is the MASKED profile — never the raw values.
    assert body["pan"] == "ABCXX1234X"
    assert body["bank_account_number"].endswith("9012")
    assert "123456789012" not in str(body)
    assert body["bank_ifsc"] == "HDFC0001234"

    worker.refresh_from_db()
    assert worker.pan == "ABCDE1234F"          # stored normalised, encrypted at rest
    assert worker.bank_account_number == "123456789012"

    from apps.audit.models import AuditLog

    row = AuditLog.objects.filter(
        entity_type="employees.Employee", entity_id=str(worker.pk),
        reason__icontains="Identifiers updated",
    ).first()
    assert row is not None
    assert "123456789012" not in str(row.after)  # audit carries masked values only


def test_bad_formats_are_refused(hr, worker):
    client = _client(hr)
    for field, value in [
        ("pan", "1234567890"), ("uan", "12AB"), ("bank_ifsc", "HD0001234"),
        ("bank_account_number", "12"), ("aadhaar", "123"),
    ]:
        response = client.patch(
            f"/api/v1/employees/{worker.pk}/identifiers/", {field: value}, format="json"
        )
        assert response.status_code == 400, (field, response.content)


def test_neither_self_nor_a_team_manager_may_write_identifiers(worker, make_user, org):
    own = _client(worker.user)
    assert own.patch(f"/api/v1/employees/{worker.pk}/identifiers/",
                     {"bank_account_number": "999999999999"}, format="json").status_code == 403

    manager_user = make_user("senior_doctor", email="ident.mgr@x.test")
    Employee.objects.create(
        employee_code="EMP07752", first_name="Lead", user=manager_user,
        department=org["departments"]["operations"], date_of_joining=dt.date(2021, 1, 1),
    )
    worker.reporting_manager = Employee.objects.get(employee_code="EMP07752")
    worker.save(update_fields=["reporting_manager"])
    assert _client(manager_user).patch(
        f"/api/v1/employees/{worker.pk}/identifiers/",
        {"bank_account_number": "999999999999"}, format="json",
    ).status_code == 403

    worker.refresh_from_db()
    assert worker.bank_account_number == ""  # untouched
