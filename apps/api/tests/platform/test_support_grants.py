"""
Support access: the customer lets an operator see its configuration, briefly.

The rules under test, each of which is the whole point of the feature rather
than a detail of it:

  * the operator ASKS; the customer's Admin decides -- nothing is visible
    until they do;
  * what is visible is configuration, from a code constant: roles, policies,
    structure. Never a person;
  * only the operator who asked, only for 24 hours, only until revoked;
  * the organization comes from the grant row, so a grant for one customer
    can never show another's configuration;
  * every step lands in the CUSTOMER's audit trail.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.platform.services.provisioning import provision_organization
from apps.platform.services.support import SUPPORT_VISIBLE
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "support-password-12345"
REASON = "Customer reports leave balances are wrong since the policy change."


def _usable(user):
    user.set_password(PASSWORD)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


def _client(user, door="/api/v1/auth/login/"):
    client = APIClient(HTTP_HOST="localhost")
    response = client.post(door, {"email": user.email, "password": PASSWORD}, format="json")
    assert response.status_code == 200, response.content[:300]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client


def _operator(email):
    from apps.accounts.models import User

    call_command("bootstrap_platform_admin", "--email", email)
    return _usable(User.objects.get(email=email))


def _customer(slug, *, employee_name):
    from apps.employees.models import Employee
    from apps.organization.models import Department, Designation, EmployeeLevel, Location
    from core.access.catalog import DepartmentKind, Layer

    result = provision_organization(
        name=slug.title(), slug=slug, admin_email=f"admin@{slug}.example"
    )
    _usable(result.admin)
    with acting_as(None, organization=result.organization):
        department = Department.objects.create(
            name=f"{slug} Operations", code="OPS", kind=DepartmentKind.OPERATIONS
        )
        Employee.objects.create(
            employee_code=f"{slug.upper()}-1", first_name=employee_name, last_name="Person",
            department=department,
            designation=Designation.objects.create(title="Associate", department=department),
            location=Location.objects.create(name="HO", code="HO", city="Pune", state="MH"),
            level=EmployeeLevel.objects.create(name="Staff", code="L5", layer=Layer.STAFF),
            date_of_joining=dt.date(2025, 1, 6),
        )
    return result


@pytest.fixture
def ours(db):
    return _customer("northwind", employee_name="Zenobia")


@pytest.fixture
def theirs(db):
    return _customer("southwind", employee_name="Quillon")


@pytest.fixture
def ops(db):
    return _operator("ops@platform.example")


@pytest.fixture
def console(ops):
    return _client(ops, "/api/v1/auth/login/platform/")


def _request(console, organization, reason=REASON):
    return console.post(
        f"/api/v1/platform/organizations/{organization.pk}/support-grants/",
        {"reason": reason}, format="json",
    )


def _decide(admin, grant_id, decision):
    return _client(admin).post(f"/api/v1/org/support-grants/{grant_id}/{decision}/")


def _configuration(console, grant_id):
    return console.get(f"/api/v1/platform/support-grants/{grant_id}/configuration/")


# ---------------------------------------------------------------------------
# The happy path, and what it does and does not show
# ---------------------------------------------------------------------------


def test_nothing_is_visible_until_the_customer_approves(console, ours):
    grant = _request(console, ours.organization).json()
    assert grant["status"] == "requested"

    refused = _configuration(console, grant["id"])
    assert refused.status_code == 422

    assert _decide(ours.admin, grant["id"], "approve").status_code == 200
    shown = _configuration(console, grant["id"])
    assert shown.status_code == 200, shown.content[:300]
    assert shown.json()["tables"]["accounts.Role"], "the customer's roles should be visible"


def test_configuration_only_never_a_person(console, ours):
    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")

    body = _configuration(console, grant["id"]).json()

    assert set(body["tables"]) == set(SUPPORT_VISIBLE)
    assert not any(label.startswith(("employees.Employee", "payroll.Payslip")) for label in body["tables"])
    # Not in any table, under any column: the employee's name.
    assert "Zenobia" not in _configuration(console, grant["id"]).content.decode()


def test_a_grant_shows_its_own_customer_and_never_another(console, ours, theirs):
    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")

    content = _configuration(console, grant["id"]).content.decode()

    assert "northwind Operations" in content
    assert "southwind Operations" not in content


# ---------------------------------------------------------------------------
# Who, and for how long
# ---------------------------------------------------------------------------


def test_the_reason_must_be_a_sentence(console, ours):
    response = _request(console, ours.organization, reason="support")
    assert response.status_code == 422
    assert "20 characters" in response.json()["error"]["message"]


def test_only_the_operator_who_asked_may_use_it(console, ours):
    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")

    colleague = _client(_operator("ops2@platform.example"), "/api/v1/auth/login/platform/")
    response = _configuration(colleague, grant["id"])

    assert response.status_code in (404, 422)
    assert "northwind Operations" not in response.content.decode()


def test_it_expires(console, ours):
    from apps.platform.models import SupportGrant

    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")
    SupportGrant.objects.all_orgs().filter(pk=grant["id"]).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )

    assert _configuration(console, grant["id"]).status_code == 422


def test_the_customer_can_revoke_it(console, ours):
    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")
    assert _configuration(console, grant["id"]).status_code == 200

    assert _decide(ours.admin, grant["id"], "revoke").status_code == 200
    assert _configuration(console, grant["id"]).status_code == 422


def test_a_denied_request_stays_denied(console, ours):
    grant = _request(console, ours.organization).json()
    assert _decide(ours.admin, grant["id"], "deny").status_code == 200

    assert _configuration(console, grant["id"]).status_code == 422
    # Decided once. A second opinion is a new request, not a re-decision.
    assert _decide(ours.admin, grant["id"], "approve").status_code == 422


def test_another_customer_cannot_see_or_decide_it(console, ours, theirs):
    grant = _request(console, ours.organization).json()

    listed = _client(theirs.admin).get("/api/v1/org/support-grants/").json()
    assert grant["id"] not in {row["id"] for row in listed}
    # 404, not 403: to the other customer this grant does not exist.
    assert _decide(theirs.admin, grant["id"], "approve").status_code == 404


def test_the_operator_cannot_approve_their_own_request(console, ours):
    grant = _request(console, ours.organization).json()
    response = console.post(f"/api/v1/org/support-grants/{grant['id']}/approve/")
    assert response.status_code in (403, 404)
    assert _configuration(console, grant["id"]).status_code == 422


def test_an_employee_without_settings_authority_cannot_approve(console, ours):
    from apps.accounts.models import Role, User, UserRole
    from apps.organization.models import OrganizationMembership

    with acting_as(None, organization=ours.organization):
        worker = User.objects.create_user(email="worker@northwind.example", password=PASSWORD)
        UserRole.objects.create(user=worker, role=Role.objects.get(code="employee"))
        OrganizationMembership.objects.create(organization=ours.organization, user=worker)

    grant = _request(console, ours.organization).json()
    assert _decide(worker, grant["id"], "approve").status_code == 403


def test_a_customer_admin_cannot_request_access_to_anyone(ours, theirs):
    """The request route is a platform route; an organization user is refused it."""
    response = _client(ours.admin).post(
        f"/api/v1/platform/organizations/{theirs.organization.pk}/support-grants/",
        {"reason": REASON}, format="json",
    )
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# The customer's record of it
# ---------------------------------------------------------------------------


def test_every_step_is_in_the_customers_trail(console, ops, ours):
    from apps.audit.models import AuditLog

    grant = _request(console, ours.organization).json()
    _decide(ours.admin, grant["id"], "approve")
    _configuration(console, grant["id"])
    _configuration(console, grant["id"])
    _decide(ours.admin, grant["id"], "revoke")

    events = list(
        AuditLog.objects.filter(
            organization=ours.organization, entity_type="platform.SupportGrant"
        )
        .order_by("id")
        .values_list("after__event", "actor_id")
    )
    assert events == [
        ("support_requested", ops.pk),
        ("support_approved", ours.admin.pk),
        ("support_access", ops.pk),
        ("support_access", ops.pk),
        ("support_revoked", ours.admin.pk),
    ]
    request = AuditLog.objects.get(
        organization=ours.organization, after__event="support_requested"
    )
    assert request.reason == REASON
