"""
Feature gating and suspension, enforced at the API.

Three claims are tested, and each one fails silently if it is wrong:

  * a module the plan excludes is refused, with a code the SPA can route on
    rather than a generic 403;
  * a DOWNGRADE does not make existing records unreachable -- writes stop,
    reads and exports continue for the grace window, because a customer must
    be able to retrieve records they are statutorily obliged to keep;
  * a suspended organization is refused everywhere except the screens that
    explain why, and is TOLD that rather than left to guess.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.platform.models import Plan
from apps.platform.services.provisioning import provision_organization
from apps.platform.services.subscriptions import change_plan, set_status

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "gate-password-12345"


@pytest.fixture
def plans(db):
    call_command("seed_plans", verbosity=0)
    return {p.code: p for p in Plan.objects.all()}


@pytest.fixture
def company(plans):
    """An organization on the full plan, with an admin who can sign in."""
    result = provision_organization(
        name="Northwind Health",
        slug="northwind",
        admin_email="admin@northwind.example",
        plan=plans["enterprise"],
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])
    return result


def _client(user):
    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.content[:200]
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
    return client


def _payroll_run(organization, actor):
    from apps.payroll.models import PayrollRun
    from core.middleware import acting_as

    with acting_as(None, organization=organization):
        return PayrollRun.objects.create(
            period_year=2025, period_month=6, run_by=actor
        )


def _code(response) -> str:
    body = response.json()
    return (body.get("error") or {}).get("code", "")


# ---------------------------------------------------------------------------
# A plan that includes the module
# ---------------------------------------------------------------------------


def test_an_included_module_works(company):
    """The positive control. Without it every refusal below proves nothing."""
    client = _client(company.admin)
    assert client.get("/api/v1/payroll/runs/").status_code == 200


def test_the_snapshot_lists_what_the_plan_includes(company):
    client = _client(company.admin)
    snapshot = client.get("/api/v1/me/permissions/").json()

    assert "payroll" in snapshot["features"]
    assert snapshot["organization_status"] == "pending_setup"


# ---------------------------------------------------------------------------
# A plan that excludes it
# ---------------------------------------------------------------------------


def test_an_excluded_module_is_refused_with_a_code_the_client_can_route_on(
    company, plans
):
    """
    Not a generic 403. The SPA has to tell "you may not" from "your employer
    did not buy this", because only one of them is worth showing an upgrade
    prompt for.
    """
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)

    response = client.post(
        "/api/v1/payroll/runs/",
        {"period_year": 2025, "period_month": 7},
        format="json",
    )
    assert response.status_code == 403, response.status_code
    assert _code(response) == "feature_not_available", response.content[:200]


def test_the_snapshot_stops_listing_it(company, plans):
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)

    snapshot = client.get("/api/v1/me/permissions/").json()
    assert "payroll" not in snapshot["features"]
    assert "leave" in snapshot["features"], "starter still includes leave"


def test_a_module_the_plan_keeps_is_unaffected(company, plans):
    """
    The gate is per-feature, not per-plan. Losing payroll must not disturb
    leave, which the same customer still pays for.
    """
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)
    assert client.get("/api/v1/leave-requests/").status_code == 200


def test_core_is_never_gated(company, plans):
    """
    An HRMS without employees is not a cheaper HRMS. No plan may disable CORE,
    and this asserts the consequence at the API rather than on the model.
    """
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)

    for path in ("/api/v1/employees/", "/api/v1/departments/", "/api/v1/audit/"):
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# The downgrade rule that matters
# ---------------------------------------------------------------------------


def test_a_downgrade_leaves_existing_records_readable(company, plans):
    """
    THE rule for a downgrade. The payroll run stays stored, and stays
    RETRIEVABLE: a customer who moves off payroll still has statutory
    obligations about the payslips they already produced, and a product that
    made them unreachable would have created the compliance problem.
    """
    run = _payroll_run(company.organization, company.admin)
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)

    listed = client.get("/api/v1/payroll/runs/")
    assert listed.status_code == 200, _code(listed)

    detail = client.get(f"/api/v1/payroll/runs/{run.pk}/")
    assert detail.status_code == 200, _code(detail)


def test_a_downgrade_stops_the_writes(company, plans):
    run = _payroll_run(company.organization, company.admin)
    change_plan(company.organization, plan=plans["starter"])
    client = _client(company.admin)

    response = client.patch(
        f"/api/v1/payroll/runs/{run.pk}/", {"notes": "edited"}, format="json"
    )
    assert response.status_code == 403
    assert _code(response) == "feature_not_available"


def test_reads_stop_once_the_grace_window_expires(company, plans):
    """
    The window is finite, and measured from when the plan narrowed. Otherwise
    "read-only forever" is just a slower way of never disabling anything.
    """
    from apps.platform.models import Subscription

    _payroll_run(company.organization, company.admin)
    change_plan(company.organization, plan=plans["starter"])

    subscription = Subscription.objects.get(organization=company.organization)
    subscription.features_narrowed_at = timezone.now() - dt.timedelta(
        days=subscription.read_only_grace_days + 1
    )
    subscription.save(update_fields=["features_narrowed_at"])

    client = _client(company.admin)
    response = client.get("/api/v1/payroll/runs/")
    assert response.status_code == 403
    assert _code(response) == "feature_not_available"


def test_a_module_never_included_has_no_grace_window(plans):
    """
    The window protects records the customer ENTERED under a module they once
    had. A customer who never had biometric devices has nothing to retrieve,
    so there is nothing to keep readable.

    PROVISIONED onto the narrower plan rather than downgraded onto it, and the
    distinction is the whole test: an earlier version of this downgraded from
    enterprise, which meant the module HAD been included, the window opened
    correctly, and the read succeeded. That was the code being right and the
    test being wrong.
    """
    result = provision_organization(
        name="Aperture Systems",
        slug="aperture",
        admin_email="admin@aperture.example",
        plan=plans["growth"],  # no biometric, and never had it
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])

    from apps.platform.models import Subscription

    subscription = Subscription.objects.get(organization=result.organization)
    assert subscription.features_narrowed_at is None, (
        "nothing narrowed, so no window should have opened"
    )

    client = _client(result.admin)
    response = client.get("/api/v1/essl/devices/")
    assert response.status_code == 403, response.status_code
    assert _code(response) == "feature_not_available"


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


def test_a_suspended_organization_is_refused_and_told_why(company):
    from apps.organization.models import OrgStatus

    client = _client(company.admin)
    assert client.get("/api/v1/employees/").status_code == 200

    company.organization.status = OrgStatus.SUSPENDED
    company.organization.save(update_fields=["status"])

    response = client.get("/api/v1/employees/")
    assert response.status_code == 403
    assert _code(response) == "organization_suspended", response.content[:200]


def test_a_suspended_organizations_users_can_still_see_who_they_are(company):
    """
    Otherwise the SPA cannot render the screen that explains the suspension --
    it would have nothing to render it from.
    """
    from apps.organization.models import OrgStatus

    client = _client(company.admin)
    company.organization.status = OrgStatus.SUSPENDED
    company.organization.save(update_fields=["status"])

    assert client.get("/api/v1/me/").status_code == 200
    assert client.get("/api/v1/me/permissions/").status_code == 200


def test_suspension_survives_a_fresh_login(company):
    """
    The gate is re-derived per request from the principal, not decided once at
    sign-in. A gate that only ran at login would let an existing session keep
    working after a suspension, which is the whole reason this is a permission
    class and not middleware.
    """
    from apps.organization.models import OrgStatus

    company.organization.status = OrgStatus.SUSPENDED
    company.organization.save(update_fields=["status"])

    client = _client(company.admin)
    response = client.get("/api/v1/employees/")
    assert response.status_code == 403
    assert _code(response) == "organization_suspended"


def test_restoring_an_organization_restores_access(company):
    from apps.organization.models import OrgStatus

    client = _client(company.admin)
    company.organization.status = OrgStatus.SUSPENDED
    company.organization.save(update_fields=["status"])
    assert client.get("/api/v1/employees/").status_code == 403

    company.organization.status = OrgStatus.ACTIVE
    company.organization.save(update_fields=["status"])
    assert client.get("/api/v1/employees/").status_code == 200


def test_a_cancelled_subscription_suspends_through_one_path(company, plans):
    """
    The commercial transition writes the access status, and the API refuses on
    the access status. Two fields, one authority.
    """
    from apps.organization.models import OrgStatus
    from apps.platform.models import SubscriptionStatus

    company.organization.status = OrgStatus.ACTIVE
    company.organization.save(update_fields=["status"])
    client = _client(company.admin)
    assert client.get("/api/v1/employees/").status_code == 200

    set_status(company.organization, status=SubscriptionStatus.CANCELLED)

    response = client.get("/api/v1/employees/")
    assert response.status_code == 403
    assert _code(response) == "organization_suspended"


# ---------------------------------------------------------------------------
# What the gate must NOT touch
# ---------------------------------------------------------------------------


def test_a_deployment_with_no_plans_is_not_gated():
    """
    A self-hosted single-company install has no subscription at all. Empty
    disabled-features must mean the whole product, not none of it -- this is
    the one place the access layer fails open, and it is deliberate.
    """
    assert not Plan.objects.exists()
    result = provision_organization(
        name="Solo Ltd", slug="solo", admin_email="admin@solo.example"
    )
    result.admin.set_password(PASSWORD)
    result.admin.must_change_password = False
    result.admin.save(update_fields=["password", "must_change_password"])

    client = _client(result.admin)
    assert client.get("/api/v1/payroll/runs/").status_code == 200
    assert client.get("/api/v1/leave-requests/").status_code == 200


def test_the_platform_console_is_not_governed_by_a_customers_plan(plans):
    """
    Platform views declare no resource, so there is nothing for the feature
    gate to look up. That is not an oversight -- an operator's console must
    never depend on what any customer bought.
    """
    from django.core.management import call_command as run

    from apps.accounts.models import User

    run("bootstrap_platform_admin", "--email", "ops@platform.example")
    operator = User.objects.get(email="ops@platform.example")
    operator.set_password(PASSWORD)
    operator.must_change_password = False
    operator.save(update_fields=["password", "must_change_password"])

    client = APIClient(HTTP_HOST="localhost")
    response = client.post(
        "/api/v1/auth/login/platform/",
        {"email": operator.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])

    assert client.get("/api/v1/platform/organizations/").status_code == 200
    assert client.get("/api/v1/platform/summary/").status_code == 200
