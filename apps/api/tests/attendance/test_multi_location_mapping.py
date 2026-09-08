"""
One employee, several device IDs — one per site they work at.

The clinic's staff move between branches, and eSSL enrols them separately on
each site's device, so the same person is 1025 at Dadar and 2087 at Thane.
These tests hold the two halves of that: MANY ids may point at one employee,
and no id may ever point at two.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.attendance.models import AttendanceRecord, EsslEmployeeLink, RawPunch
from apps.attendance.services import is_mapped_to_a_device
from apps.attendance.services import sync as sync_service

pytestmark = pytest.mark.django_db

MAPPINGS = "/api/v1/essl/mappings/"


@pytest.fixture
def second_location(db, org):
    from apps.organization.models import Location

    return Location.objects.create(name="Thane Branch", city="Thane")


@pytest.fixture
def hr_head(db, org, roles):
    """An HR Head with an employee record — the role requires one."""
    from apps.accounts.models import User, UserRole
    from apps.employees.models import Employee

    user = User.objects.create_user(email="hrhead@essl.test", password="test-password-12345")
    UserRole.objects.create(user=user, role=roles["hr_head"])
    Employee.objects.create(
        employee_code="EMP09000", first_name="Hr", last_name="Head", user=user,
        department=org["departments"]["hr"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    return user


def _auth(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# --------------------------------------------------------- the model itself


def test_one_employee_may_hold_ids_at_several_locations(worker, org, second_location):
    """The requirement, at its most literal: Dadar 1025 and Thane 2087."""
    EsslEmployeeLink.objects.create(
        essl_user_id="1025", employee=worker, location=org["location"]
    )
    EsslEmployeeLink.objects.create(
        essl_user_id="2087", employee=worker, location=second_location
    )

    held = set(
        worker.essl_links.filter(is_active=True).values_list("essl_user_id", flat=True)
    )
    # '101' is the mapping the fixture made before locations existed.
    assert held == {"101", "1025", "2087"}


def test_an_id_can_never_be_claimed_by_a_second_employee(worker, org, roles):
    from apps.employees.models import Employee

    other = Employee.objects.create(
        employee_code="EMP09002", first_name="Someone", last_name="Else",
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )

    with pytest.raises(Exception) as refused:
        sync_service.map_essl_id(employee=other, essl_user_id="101")

    assert "already mapped to" in str(refused.value)
    assert EsslEmployeeLink.objects.get(essl_user_id="101").employee_id == worker.pk


# ------------------------------------------------------------ the sync path


def test_punches_from_every_mapped_id_land_on_the_same_person(
    essl_settings, device, worker, shift_rule, org, second_location
):
    """
    The point of the whole feature: a day worked partly at one branch and
    partly at another is ONE attendance record for ONE employee.
    """
    sync_service.map_essl_id(
        employee=worker, essl_user_id="1025", location=org["location"]
    )
    sync_service.map_essl_id(
        employee=worker, essl_user_id="2087", location=second_location
    )

    from tests.attendance.conftest import FakeTransport, soap_reply

    run = sync_service.sync_all(
        kind="manual",
        transport=FakeTransport([soap_reply([
            "1025\t2026-08-25 10:02:00",   # in at Dadar
            "2087\t2026-08-25 18:40:00",   # out at Thane
        ])]),
    )

    assert run.punches_unmapped == 0
    assert RawPunch.objects.filter(employee=worker).count() == 2

    record = AttendanceRecord.objects.get(employee=worker, date=dt.date(2026, 8, 25))
    assert record.first_in is not None and record.last_out is not None
    # The day spans both devices, so worked time is measured across them.
    assert record.worked_minutes > 8 * 60


def test_mapping_an_id_claims_the_punches_it_already_parked(
    essl_settings, device, worker, shift_rule, second_location
):
    """A branch's punches arriving before HR maps that branch's ID."""
    from tests.attendance.conftest import FakeTransport, soap_reply

    sync_service.sync_all(
        kind="manual",
        transport=FakeTransport([soap_reply(["2087\t2026-08-26 10:00:00"])]),
    )
    assert RawPunch.objects.filter(essl_user_id="2087", employee__isnull=True).count() == 1

    sync_service.map_essl_id(
        employee=worker, essl_user_id="2087", location=second_location
    )

    assert RawPunch.objects.filter(essl_user_id="2087", employee=worker).count() == 1


# ------------------------------------------------------------- HR's actions


def test_hr_adds_edits_and_removes_one_mapping_without_touching_the_others(
    hr_head, worker, org, second_location
):
    """Each site's mapping is independent — removing one keeps the rest."""
    client = _auth(hr_head)

    dadar = client.post(MAPPINGS, {
        "essl_user_id": "1025", "employee": str(worker.pk),
        "location": str(org["location"].pk),
    }, format="json")
    thane = client.post(MAPPINGS, {
        "essl_user_id": "2087", "employee": str(worker.pk),
        "location": str(second_location.pk),
    }, format="json")
    assert dadar.status_code == 201 and thane.status_code == 201

    # Both are listed against the one employee.
    listed = client.get(MAPPINGS, {"employee": str(worker.pk)}).json()
    assert {row["essl_user_id"] for row in listed} == {"101", "1025", "2087"}
    assert {row["location_name"] for row in listed if row["essl_user_id"] == "2087"} == {
        "Thane Branch"
    }

    # Edit ONE mapping's location.
    edited = client.patch(
        f"{MAPPINGS}{thane.json()['id']}/",
        {"location": str(org["location"].pk)},
        format="json",
    )
    assert edited.status_code == 200
    assert edited.json()["location_name"] == org["location"].name

    # Remove ONE mapping; the others survive untouched.
    removed = client.delete(f"{MAPPINGS}{dadar.json()['id']}/")
    assert removed.status_code in (200, 204)
    left = {
        row["essl_user_id"] for row in client.get(MAPPINGS, {"employee": str(worker.pk)}).json()
    }
    assert left == {"101", "2087"}


def test_the_api_refuses_an_id_another_employee_already_holds(hr_head, worker, org, roles):
    from apps.employees.models import Employee

    other = Employee.objects.create(
        employee_code="EMP09003", first_name="Second", last_name="Person",
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )
    client = _auth(hr_head)

    response = client.post(MAPPINGS, {
        "essl_user_id": "101", "employee": str(other.pk),
    }, format="json")

    assert response.status_code == 400
    assert b"already mapped to" in response.content
    assert other.essl_links.filter(is_active=True).count() == 0


def test_an_id_removed_earlier_can_be_mapped_again(hr_head, worker, org):
    """
    Removal is a soft delete, and the unique constraint spans removed rows —
    so re-adding the same ID must revive that row, not collide with it.
    """
    client = _auth(hr_head)
    created = client.post(MAPPINGS, {
        "essl_user_id": "1025", "employee": str(worker.pk),
    }, format="json")
    client.delete(f"{MAPPINGS}{created.json()['id']}/")

    again = client.post(MAPPINGS, {
        "essl_user_id": "1025", "employee": str(worker.pk),
        "location": str(org["location"].pk),
    }, format="json")

    assert again.status_code == 201, again.content
    assert EsslEmployeeLink.objects.filter(essl_user_id="1025", is_active=True).count() == 1


# ------------------------------------------------------ nothing else breaks


def test_an_employee_with_one_mapping_still_works_exactly_as_before(
    essl_settings, device, worker, shift_rule
):
    """The upgrade must not disturb mappings made before it."""
    from tests.attendance.conftest import FakeTransport, soap_reply

    run = sync_service.sync_all(
        kind="manual",
        transport=FakeTransport([soap_reply(["101\t2026-08-27 10:00:00"])]),
    )

    assert run.punches_unmapped == 0
    assert RawPunch.objects.filter(essl_user_id="101", employee=worker).exists()
    assert is_mapped_to_a_device(worker) is True


def test_an_unmapped_employee_reads_as_unmapped(db, org, roles):
    """
    The reverse accessor is a manager and always exists, so the old
    `hasattr(employee, "essl_link")` test would answer True for everyone.
    Payroll and the leave module both trust this answer.
    """
    from apps.employees.models import Employee

    nobody = Employee.objects.create(
        employee_code="EMP09004", first_name="Not", last_name="Enrolled",
        department=org["departments"]["operations"], location=org["location"],
        date_of_joining=dt.date(2026, 1, 1),
    )

    assert is_mapped_to_a_device(nobody) is False
