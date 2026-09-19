"""
The operator's write surface, and the customer's read-only view of it.

The services already hold the rules, the row locking and the audit; these
endpoints are thin wrappers, and the tests are correspondingly about the seam
rather than the arithmetic: does a service refusal become a 422 the console can
render, is the reason actually required where it is load-bearing, and does the
customer's own plan page stay read-only.

The last one is a product decision rather than an omission. A customer does not
upgrade themselves through the HR product -- that is a commercial conversation,
and an endpoint that let whoever held the Admin role change the bill would be
the wrong person making it.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.platform.models import Plan, Subscription, SubscriptionStatus
from apps.platform.services.provisioning import provision_organization

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "console-password-12345"


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)
    return {p.code: p for p in Plan.objects.all()}


@pytest.fixture
def company(plans):
    result = provision_organization(
        name="Northwind Health",
        slug="northwind",
        admin_email="admin@northwind.example",
        plan=plans["starter"],
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])
    return result


@pytest.fixture
def operator(db):
    from apps.accounts.models import User

    call_command("bootstrap_platform_admin", "--email", "ops@platform.example")
    user = User.objects.get(email="ops@platform.example")
    user.set_password(PASSWORD)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    return user


def _signed_in(user, door="/api/v1/auth/login/"):
    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        door, {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200, response.content[:200]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client


@pytest.fixture
def console(operator):
    return _signed_in(operator, "/api/v1/auth/login/platform/")


def _code(response) -> str:
    return (response.json().get("error") or {}).get("code", "")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_the_console_lists_plans_with_their_subscriber_counts(console, company):
    response = console.get("/api/v1/platform/plans/")
    assert response.status_code == 200, response.content[:200]

    body = response.json()
    rows = {row["code"]: row for row in body.get("data", body)}
    assert rows["starter"]["subscriber_count"] == 1
    assert rows["enterprise"]["subscriber_count"] == 0
    assert "payroll" not in rows["starter"]["enabled_features"]


def test_an_organization_carries_its_subscription(console, company):
    response = console.get(
        f"/api/v1/platform/organizations/{company.organization.pk}/"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subscription"]["plan_code"] == "starter"
    assert body["subscription"]["employee_limit"] == 25


def test_plans_are_read_only_over_the_api(console, plans):
    """
    A plan is referenced by live subscriptions. An endpoint that could delete
    one would take customers with it, and one that could retune a seat limit
    would change what every subscriber is entitled to with no audit row naming
    any of them.
    """
    created = console.post(
        "/api/v1/platform/plans/", {"code": "free", "name": "Free"}, format="json"
    )
    assert created.status_code == 405, created.status_code

    deleted = console.delete(f"/api/v1/platform/plans/{plans['starter'].pk}/")
    assert deleted.status_code == 405, deleted.status_code


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_the_operator_changes_a_plan(console, company):
    response = console.post(
        f"/api/v1/platform/organizations/{company.organization.pk}/change-plan/",
        {"plan": "growth"},
        format="json",
    )
    assert response.status_code == 200, response.content[:250]
    assert response.json()["subscription"]["plan_code"] == "growth"


def test_an_unknown_plan_is_refused(console, company):
    response = console.post(
        f"/api/v1/platform/organizations/{company.organization.pk}/change-plan/",
        {"plan": "platinum"},
        format="json",
    )
    assert response.status_code == 422
    assert "platinum" in response.json()["error"]["message"]


def test_a_downgrade_below_the_headcount_is_422_not_500(console, company, plans):
    """
    The seam this file exists for: the service raises its own exception, and
    the console has to render it. Without the translation the operator would
    get a 500 and no idea which customer or which number was the problem.
    """
    from apps.platform.services.subscriptions import change_plan

    change_plan(company.organization, plan=plans["growth"])
    _fill(company, 40)

    response = console.post(
        f"/api/v1/platform/organizations/{company.organization.pk}/change-plan/",
        {"plan": "starter"},
        format="json",
    )
    assert response.status_code == 422, response.content[:250]
    assert "25 active employees" in response.json()["error"]["message"]


def test_forcing_a_downgrade_needs_a_reason(console, company, plans):
    from apps.platform.services.subscriptions import change_plan

    change_plan(company.organization, plan=plans["growth"])
    _fill(company, 40)
    url = f"/api/v1/platform/organizations/{company.organization.pk}/change-plan/"

    refused = console.post(url, {"plan": "starter", "force": True}, format="json")
    assert refused.status_code == 422
    assert "reason" in refused.json()["error"]["message"].lower()

    allowed = console.post(
        url,
        {"plan": "starter", "force": True, "reason": "Agreed until March 2027."},
        format="json",
    )
    assert allowed.status_code == 200, allowed.content[:250]
    assert allowed.json()["subscription"]["plan_code"] == "starter"


def test_the_operator_moves_the_commercial_status(console, company):
    from apps.organization.models import OrgStatus

    company.organization.status = OrgStatus.ACTIVE
    company.organization.save(update_fields=["status"])

    response = console.post(
        f"/api/v1/platform/organizations/{company.organization.pk}/"
        f"subscription-status/",
        {"status": SubscriptionStatus.CANCELLED, "reason": "Customer gave notice."},
        format="json",
    )
    assert response.status_code == 200, response.content[:250]

    company.organization.refresh_from_db()
    assert company.organization.status == OrgStatus.CANCELLED


def test_a_seat_override_requires_a_reason(console, company):
    url = f"/api/v1/platform/organizations/{company.organization.pk}/seat-override/"

    refused = console.post(url, {"employee_limit": 400}, format="json")
    assert refused.status_code == 400, refused.content[:200]

    allowed = console.post(
        url,
        {"employee_limit": 400, "reason": "Enterprise agreement signed 2026-09-01."},
        format="json",
    )
    assert allowed.status_code == 200, allowed.content[:250]
    assert allowed.json()["subscription"]["employee_limit"] == 400


def test_clearing_an_override_clears_its_reason(console, company):
    """
    A reason left behind describes a decision that no longer applies, which is
    worse than none at all.
    """
    url = f"/api/v1/platform/organizations/{company.organization.pk}/seat-override/"
    console.post(
        url, {"employee_limit": 400, "reason": "Signed agreement."}, format="json"
    )

    cleared = console.post(
        url, {"employee_limit": None, "reason": "Agreement ended."}, format="json"
    )
    assert cleared.status_code == 200, cleared.content[:250]

    subscription = Subscription.objects.get(organization=company.organization)
    assert subscription.employee_limit_override is None
    assert subscription.override_reason == ""
    assert subscription.employee_limit == 25, "back to the plan's own limit"


def test_every_platform_write_is_audited_against_the_customer(console, company):
    """
    "Who moved this customer's plan, and why" has to be answerable. The row is
    stamped with the AFFECTED organization and the PLATFORM ADMIN as actor --
    a platform action about an organization is not an org-less event.
    """
    from apps.audit.models import AuditLog

    console.post(
        f"/api/v1/platform/organizations/{company.organization.pk}/change-plan/",
        {"plan": "growth", "reason": "Upgraded on request."},
        format="json",
    )

    entry = (
        AuditLog.objects.filter(
            organization=company.organization, entity_type="platform.Subscription"
        )
        .order_by("-occurred_at")
        .first()
    )
    assert entry is not None
    assert entry.after["plan"] == "growth"
    assert entry.actor_email == "ops@platform.example"
    assert "Upgraded" in entry.reason


# ---------------------------------------------------------------------------
# The customer's own view
# ---------------------------------------------------------------------------


def test_a_customer_sees_their_plan_and_usage(company):
    client = _signed_in(company.admin)
    response = client.get("/api/v1/org/plan/")
    assert response.status_code == 200, response.content[:200]

    body = response.json()
    assert body["plan"]["code"] == "starter"
    assert body["employee_limit"] == 25
    assert body["seats_remaining"] == 25 - body["employees_used"]
    assert "payroll" not in body["features"]


def test_a_customer_cannot_change_their_own_plan(company):
    """
    Read only, deliberately. Upgrading is a commercial conversation, and an
    endpoint that let whoever holds the Admin role change the bill would be
    the wrong person making that decision.
    """
    client = _signed_in(company.admin)
    for method in ("post", "patch", "put", "delete"):
        response = getattr(client, method)("/api/v1/org/plan/")
        assert response.status_code == 405, (method, response.status_code)


def test_a_customer_cannot_reach_the_console(company):
    client = _signed_in(company.admin)
    org_id = company.organization.pk
    for path in (
        "/api/v1/platform/plans/",
        f"/api/v1/platform/organizations/{org_id}/",
        f"/api/v1/platform/organizations/{org_id}/change-plan/",
        f"/api/v1/platform/organizations/{org_id}/seat-override/",
    ):
        assert client.get(path).status_code == 403, path
        assert client.post(path, {}, format="json").status_code == 403, path


def test_a_deployment_with_no_plans_reports_unlimited():
    """Not an error. That is what a self-hosted install without plans is."""
    result = provision_organization(
        name="Solo Ltd", slug="solo", admin_email="admin@solo.example"
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])

    body = _signed_in(result.admin).get("/api/v1/org/plan/").json()
    assert body["plan"] is None
    assert body["employee_limit"] is None
    assert body["seats_remaining"] is None
    assert "payroll" in body["features"]


def _fill(company, count):
    import datetime as dt

    from apps.employees.models import Employee
    from apps.organization.models import Department, Designation, EmployeeLevel, Location
    from core.access.catalog import DepartmentKind, Layer
    from core.middleware import acting_as

    with acting_as(None, organization=company.organization):
        department = Department.objects.create(
            name="People", code="HR", kind=DepartmentKind.HR
        )
        location = Location.objects.create(name="HQ", code="HO")
        designation = Designation.objects.create(title="Officer", department=None)
        level = EmployeeLevel.objects.create(name="Staff", code="L5", layer=Layer.STAFF)
        for i in range(count):
            Employee.objects.create(
                employee_code=f"F{i:04d}",
                first_name="Filler",
                last_name=f"Person{i}",
                department=department,
                designation=designation,
                location=location,
                level=level,
                date_of_joining=dt.date(2024, 1, 1),
            )


# ---------------------------------------------------------------------------
# Provisioning: the entry point the service spent a stage without
# ---------------------------------------------------------------------------
#
# `provision_organization()` was atomic, audited and tested, and reachable from
# nothing but the test suite: the console listed organizations read-only and no
# command wrapped it, so onboarding a customer meant a Django shell. These
# cover both doors, and the thing neither may do -- put a live credential in a
# response body.


def test_the_console_provisions_a_customer(console, plans):
    from apps.accounts.models import Role, UserRole
    from apps.organization.models import Organization, OrganizationMembership

    response = console.post(
        "/api/v1/platform/organizations/",
        {
            "name": "Aperture Systems",
            "slug": "aperture",
            "admin_email": "admin@aperture.example",
            "admin_first_name": "Asha",
            "city": "Pune",
            "state": "MH",
            "plan": "starter",
        },
        format="json",
    )

    assert response.status_code == 201, response.content[:300]
    body = response.json()
    assert body["slug"] == "aperture"
    assert body["status"] == "pending_setup", "a new customer starts in setup"
    assert body["subscription"]["plan_code"] == "starter"
    assert body["admin_email"] == "admin@aperture.example"

    organization = Organization.objects.get(slug="aperture")
    admin = OrganizationMembership.objects.get(organization=organization).user
    assert admin.email == "admin@aperture.example"
    assert admin.must_change_password

    # The configuration came with it: an administrator lands in a working
    # company, not an empty row.
    assert Role.objects.all_orgs().filter(organization=organization).count() >= 18
    assert UserRole.objects.all_orgs().filter(
        user=admin, role__organization=organization, role__code="admin"
    ).exists()


def test_provisioning_never_returns_the_temporary_password(console, plans):
    """
    The credential goes by email, to the person who needs it.

    A response body travels through logs, proxies and browser tooling on its
    way to the console, and the password is stored nowhere afterwards -- so
    putting it here would be the one copy that leaks. `invitation_sent` is what
    the operator gets instead, because nobody should be told credentials were
    sent when they were not.
    """
    from apps.accounts.models import User

    response = console.post(
        "/api/v1/platform/organizations/",
        {"name": "Fairhaven Retail", "admin_email": "admin@fairhaven.example"},
        format="json",
    )
    assert response.status_code == 201, response.content[:300]

    serialized = response.content.decode()
    assert "password" not in serialized.lower(), serialized[:300]

    # The credential the service generated is genuinely absent, not merely
    # unnamed: nothing in the body authenticates as this administrator.
    admin = User.objects.get(email="admin@fairhaven.example")
    for value in response.json().values():
        assert not (isinstance(value, str) and admin.check_password(value))

    # The operator is told whether it reached them, which is the part they can
    # act on. False here rather than True: the send is scheduled with
    # `on_commit`, which does not fire inside a test's transaction. Its
    # accuracy is asserted where it can be -- see the command's own test.
    assert "invitation_sent" in response.json()


def test_the_console_refuses_a_slug_that_is_taken(console, company):
    response = console.post(
        "/api/v1/platform/organizations/",
        {"name": "Second Northwind", "slug": "northwind",
         "admin_email": "second@northwind.example"},
        format="json",
    )

    assert response.status_code == 422, response.content[:300]
    assert "northwind" in response.content.decode().lower()


def test_the_console_refuses_an_address_that_already_has_an_account(console, company):
    """
    The V1 identity clamp, surfaced where an operator will meet it: one login
    belongs to one organization, so the second company cannot have this admin.
    """
    response = console.post(
        "/api/v1/platform/organizations/",
        {"name": "Third Company", "admin_email": company.admin.email},
        format="json",
    )

    assert response.status_code == 422, response.content[:300]
    assert "one organization" in response.content.decode().lower()


def test_an_organization_admin_cannot_provision_a_company(company, plans):
    """
    The platform boundary, in the direction that matters commercially.

    A customer's Admin holds Scope.ALL across their own company; creating
    companies is not theirs, and the route refuses them like every other
    platform route.
    """
    from apps.organization.models import Organization

    client = _signed_in(company.admin)
    response = client.post(
        "/api/v1/platform/organizations/",
        {"name": "Self Serve", "admin_email": "self@serve.example"},
        format="json",
    )

    assert response.status_code in (403, 404), response.content[:200]
    assert not Organization.objects.filter(slug="self-serve").exists()


def test_organizations_cannot_be_edited_or_deleted_through_the_console(console, company):
    """
    Read and create, nothing else.

    Commercial state moves through `subscription-status`, which holds the rules
    and writes the audit row. There is deliberately no API that deletes a
    customer's data in one call: that is a lifecycle sequence with waiting
    periods, not a verb.
    """
    detail = f"/api/v1/platform/organizations/{company.organization.pk}/"

    assert console.patch(detail, {"name": "Renamed"}, format="json").status_code == 405
    assert console.put(detail, {"name": "Renamed"}, format="json").status_code == 405
    assert console.delete(detail).status_code == 405

    company.organization.refresh_from_db()
    assert company.organization.name == "Northwind Health"


def test_the_snapshot_says_which_product_this_session_is_for(console, company):
    """
    A platform operator and a customer's employee with no grants look
    identical in `grants` -- both empty. The SPA has to tell them apart, or it
    shows the operator the same empty HR app it shows the broken account.

    The flag grants nothing: platform routes re-check it on the User row and
    every tenant queryset still resolves to nothing for them. It decides which
    screens a browser draws, not which requests succeed.
    """
    operator = console.get("/api/v1/me/permissions/").json()
    assert operator["is_platform_admin"] is True
    assert operator["grants"] == {}, "an operator holds no grant in any organization"

    customer = _signed_in(company.admin).get("/api/v1/me/permissions/").json()
    assert customer["is_platform_admin"] is False
    assert customer["grants"], "the positive control: a customer admin holds grants"
