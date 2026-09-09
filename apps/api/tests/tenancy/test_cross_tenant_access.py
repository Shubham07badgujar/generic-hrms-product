"""
The question the whole design exists to answer.

    Company A user -> Company A record  -> ALLOW
    Company A user -> Company B record  -> DENY

Asserted twice over, because the two failures look nothing alike:

  * LISTS must not CONTAIN the other organization's rows. A leak here is silent
    -- the caller sees more than they should and nothing anywhere errors.
  * DETAIL routes must REFUSE the other organization's id, with 404 rather than
    403. A 403 confirms the row exists, which turns any detail route into an
    oracle for enumerating another tenant's data.

Run as `admin`, deliberately. Admin holds Scope.ALL on almost everything, and
`Scope.ALL` used to mean "every row in the database" -- the early return in
scope_queryset() that the tenant predicate now precedes. A principal with
narrow scope would pass these tests even if tenancy were entirely absent.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


#: label -> (list endpoint, detail endpoint template)
ENDPOINTS = {
    "employee": ("/api/v1/employees/", "/api/v1/employees/{pk}/"),
    "department": ("/api/v1/departments/", "/api/v1/departments/{pk}/"),
    "location": ("/api/v1/locations/", "/api/v1/locations/{pk}/"),
    "leave_type": ("/api/v1/leave-types/", "/api/v1/leave-types/{pk}/"),
    "leave_request": ("/api/v1/leave-requests/", "/api/v1/leave-requests/{pk}/"),
    "attendance": ("/api/v1/attendance-records/", "/api/v1/attendance-records/{pk}/"),
    "payroll_run": ("/api/v1/payroll/runs/", "/api/v1/payroll/runs/{pk}/"),
    "asset": ("/api/v1/assets/", "/api/v1/assets/{pk}/"),
    "candidate": ("/api/v1/candidates/", "/api/v1/candidates/{pk}/"),
}


def _ids(payload):
    rows = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    return {str(row["id"]) for row in rows if isinstance(row, dict) and "id" in row}


# ------------------------------------------------------------------- the ORM


@pytest.mark.parametrize("label", sorted(ENDPOINTS))
def test_the_queryset_layer_hides_the_other_organization(org_a, org_b, label):
    """
    Below the API, at `scope_queryset` -- so a viewset that forgot its mixin
    could not make this pass by accident.
    """
    from core.access import Action, Resource, scope_queryset
    from core.access.registry import RESOURCE_SPECS

    mine, theirs = org_a.rows[label], org_b.rows[label]
    model = type(mine)
    resource = next(
        (r for r, spec in RESOURCE_SPECS.items() if spec.model_label == model._meta.label),
        None,
    )
    if resource is None:
        pytest.skip(f"{model._meta.label} is not a scoped resource")

    visible = scope_queryset(
        model.objects.all_orgs(), org_a.admin, resource=resource, action=Action.VIEW
    ).values_list("pk", flat=True)

    assert theirs.pk not in set(visible), f"{label}: A's admin can see B's row"


# ------------------------------------------------------------------- the API


@pytest.mark.parametrize("label", sorted(ENDPOINTS))
def test_a_list_never_contains_the_other_organization(org_a, org_b, api_for, label):
    list_url, _ = ENDPOINTS[label]
    client = api_for(org_a.admin)

    response = client.get(list_url)

    assert response.status_code == 200, (label, response.content[:200])
    ids = _ids(response.json())
    assert str(org_b.rows[label].pk) not in ids, f"{label}: B's row leaked into A's list"


@pytest.mark.parametrize("label", sorted(ENDPOINTS))
def test_a_detail_route_refuses_the_other_organizations_id(org_a, org_b, api_for, label):
    """
    404, not 403. A 403 says "this exists but is not yours", which is enough to
    enumerate another tenant's records by walking ids.
    """
    _, detail = ENDPOINTS[label]
    client = api_for(org_a.admin)

    response = client.get(detail.format(pk=org_b.rows[label].pk))

    assert response.status_code == 404, (
        f"{label}: expected 404 for another organization's id, got "
        f"{response.status_code}"
    )


@pytest.mark.parametrize("label", sorted(ENDPOINTS))
def test_the_same_route_serves_your_own_row(org_a, api_for, label):
    """
    The other half. A system that refuses everything passes every test above
    while being useless, so each refusal is paired with the permission it is
    supposed to leave intact.
    """
    _, detail = ENDPOINTS[label]
    client = api_for(org_a.admin)

    response = client.get(detail.format(pk=org_a.rows[label].pk))

    assert response.status_code == 200, (
        f"{label}: A's admin cannot read A's own row ({response.status_code})"
    )


# ------------------------------------------------------------------- writes


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_writes_against_the_other_organization_are_refused(org_a, org_b, api_for, method):
    client = api_for(org_a.admin)
    url = f"/api/v1/employees/{org_b.rows['employee'].pk}/"

    response = getattr(client, method)(url, {"first_name": "Renamed"}, format="json")

    assert response.status_code == 404, (
        f"{method.upper()} on another organization's employee returned "
        f"{response.status_code}"
    )
    org_b.rows["employee"].refresh_from_db()
    assert org_b.rows["employee"].first_name != "Renamed"
