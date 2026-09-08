"""
The simple asset module: HR Head runs the register, employees see their own.

The invariants the clinic actually cares about:
  - an Asset ID (tag) is unique,
  - an assigned asset can never be assigned to someone else,
  - several assets go to one person in one request, all-or-nothing,
  - a return frees the asset but the old assignment stays as history,
  - status is driven by assignment/return, never edited directly.
"""

from __future__ import annotations

import datetime as dt

import pytest
from rest_framework.test import APIClient

from apps.assets.models import AllocationStatus, Asset, AssetStatus
from apps.employees.models import Employee

pytestmark = pytest.mark.django_db


@pytest.fixture
def hr(make_user, org):
    user = make_user("hr_head", email="hema@x.test")
    Employee.objects.create(
        employee_code="EMP08001", first_name="Hema", user=user,
        department=org["departments"]["hr"], date_of_joining=dt.date(2020, 1, 1),
    )
    return user


@pytest.fixture
def adnan(make_user, org):
    user = make_user("employee", email="adnan@x.test")
    return Employee.objects.create(
        employee_code="EMP08002", first_name="Adnan", user=user,
        department=org["departments"]["operations"], date_of_joining=dt.date(2024, 1, 1),
    )


@pytest.fixture
def rahul(make_user, org):
    user = make_user("employee", email="rahul@x.test")
    return Employee.objects.create(
        employee_code="EMP08003", first_name="Rahul", user=user,
        department=org["departments"]["operations"], date_of_joining=dt.date(2024, 6, 1),
    )


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _create_asset(client, tag, name="Dell Laptop", **extra):
    return client.post(
        "/api/v1/assets/",
        {"asset_tag": tag, "name": name, **extra},
        format="json",
    )


# ------------------------------------------------------------ the register


def test_hr_creates_an_asset_with_the_simple_fields(hr, org):
    client = _client(hr)
    response = _create_asset(
        client, "AST-001",
        serial_number="5CG1234XYZ",
        location=str(org["location"].id),
        purchase_date="2026-01-15",
        notes="Charger included",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["status"] == AssetStatus.AVAILABLE  # born Available, always
    assert body["category_name"] == "General"       # no taxonomy required
    assert body["location_name"] == "Head Office"

    # Edit works the same surface.
    patched = client.patch(
        f"/api/v1/assets/{body['id']}/", {"notes": "Charger and sleeve"}, format="json"
    )
    assert patched.status_code == 200
    assert patched.json()["notes"] == "Charger and sleeve"


def test_asset_ids_are_unique(hr):
    client = _client(hr)
    assert _create_asset(client, "AST-002").status_code == 201
    duplicate = _create_asset(client, "AST-002", name="Another Laptop")
    assert duplicate.status_code == 400
    assert "asset_tag" in duplicate.json()["error"]["details"]


def test_the_register_is_closed_to_employees(hr, adnan):
    client = _client(adnan.user)
    assert _create_asset(client, "AST-NOPE").status_code == 403


# ------------------------------------------------------- bulk assignment


def test_hr_assigns_several_assets_at_once(hr, adnan):
    client = _client(hr)
    ids = [
        _create_asset(client, tag, name=name).json()["id"]
        for tag, name in [
            ("AST-010", "Dell Laptop"), ("AST-011", "Headphones"), ("AST-012", "SIM Card"),
        ]
    ]

    response = client.post(
        "/api/v1/asset-allocations/bulk/",
        {"employee": str(adnan.id), "assets": ids},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert len(response.json()) == 3
    assert Asset.objects.filter(pk__in=ids, status=AssetStatus.ALLOCATED).count() == 3
    assert adnan.asset_allocations.filter(status=AllocationStatus.ACTIVE).count() == 3


def test_an_assigned_asset_cannot_be_assigned_again_and_the_batch_rolls_back(
    hr, adnan, rahul
):
    client = _client(hr)
    taken = _create_asset(client, "AST-020").json()["id"]
    free = _create_asset(client, "AST-021", name="Mobile Phone").json()["id"]
    client.post(
        "/api/v1/asset-allocations/",
        {"asset": taken, "employee": str(adnan.id)},
        format="json",
    )

    response = client.post(
        "/api/v1/asset-allocations/bulk/",
        {"employee": str(rahul.id), "assets": [free, taken]},
        format="json",
    )
    assert response.status_code == 400
    # All-or-nothing: the free asset was NOT assigned either.
    assert Asset.objects.get(pk=free).status == AssetStatus.AVAILABLE
    assert rahul.asset_allocations.count() == 0


def test_employees_cannot_assign_assets(hr, adnan, rahul):
    asset_id = _create_asset(_client(hr), "AST-030").json()["id"]
    response = _client(adnan.user).post(
        "/api/v1/asset-allocations/bulk/",
        {"employee": str(adnan.id), "assets": [asset_id]},
        format="json",
    )
    assert response.status_code == 403


# ------------------------------------------------- return, history, reuse


def test_return_frees_the_asset_and_history_survives_reassignment(hr, adnan, rahul):
    """The spec's own example: AST-001 → Adnan → Returned → Rahul."""
    client = _client(hr)
    asset_id = _create_asset(client, "AST-040").json()["id"]

    first = client.post(
        "/api/v1/asset-allocations/",
        {"asset": asset_id, "employee": str(adnan.id)},
        format="json",
    ).json()

    returned = client.post(f"/api/v1/asset-allocations/{first['id']}/return/", {}, format="json")
    assert returned.status_code == 200
    assert Asset.objects.get(pk=asset_id).status == AssetStatus.AVAILABLE

    second = client.post(
        "/api/v1/asset-allocations/",
        {"asset": asset_id, "employee": str(rahul.id)},
        format="json",
    )
    assert second.status_code == 201

    history = client.get("/api/v1/asset-allocations/", {"asset": asset_id}).json()["data"]
    assert len(history) == 2
    by_status = {row["status"]: row["employee_name"] for row in history}
    assert by_status == {"returned": "Adnan", "active": "Rahul"}


def test_status_is_not_directly_editable(hr, adnan):
    client = _client(hr)
    body = _create_asset(client, "AST-050").json()
    client.post(
        "/api/v1/asset-allocations/",
        {"asset": body["id"], "employee": str(adnan.id)},
        format="json",
    )
    # A PATCH trying to force it back to available is silently a no-op on
    # status — the field is read-only because allocations own it.
    response = client.patch(
        f"/api/v1/assets/{body['id']}/", {"status": "available"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["status"] == AssetStatus.ALLOCATED


# ------------------------------------------------------------- visibility


def test_an_employee_sees_only_their_own_assets(hr, adnan, rahul):
    client = _client(hr)
    mine = _create_asset(client, "AST-060").json()["id"]
    theirs = _create_asset(client, "AST-061").json()["id"]
    client.post(
        "/api/v1/asset-allocations/", {"asset": mine, "employee": str(adnan.id)}, format="json"
    )
    client.post(
        "/api/v1/asset-allocations/", {"asset": theirs, "employee": str(rahul.id)}, format="json"
    )

    rows = _client(adnan.user).get("/api/v1/asset-allocations/").json()["data"]
    assert [row["asset_tag"] for row in rows] == ["AST-060"]
