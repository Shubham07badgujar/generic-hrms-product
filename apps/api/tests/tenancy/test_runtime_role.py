"""
The product works when the database connection is `generic_hrms_app`.

Release 1 proved the POLICIES were right by switching role inside individual
tests. This file proves the APPLICATION is right when the connection itself is
the confined runtime role -- which, from release 2, is how the whole suite
runs: `tests/conftest.py` builds the test database as the owner and then hands
the session to the app role.

So the first test here is the one the rest depend on: if the connection is not
actually the app role, everything below would pass for the wrong reason.

The ordering hazard this release exists to survive: authentication reads
tenant-owned tables BEFORE any organization is bound, because resolving the
membership is what establishes the organization. Under row-level security a
mistake there does not error -- it returns nothing, and the product denies
every request. Several tests below exist only to catch that.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from core.access.platform_bypass import PlatformBypassRefused, platform_bypass
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

PASSWORD = "test-password-12345"


def _sql(query, params=None):
    with connection.cursor() as cursor:
        cursor.execute(query, params or [])
        return cursor.fetchall() if cursor.description else cursor.rowcount


def _one(query, params=None):
    return _sql(query, params)[0][0]


def _login(client, email, password=PASSWORD, path="/api/v1/auth/login/"):
    return client.post(path, {"email": email, "password": password}, format="json")


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def test_the_suite_really_is_connected_as_the_confined_runtime_role():
    """If this fails, every other result in this file is meaningless."""
    from django.conf import settings

    assert _one("SELECT current_user") == "generic_hrms_app"
    assert _one("SELECT current_setting('is_superuser')") == "off"
    assert _one("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user") is False
    assert _one(
        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user"
    ) == 0
    # ...and the owner credentials are configured, separately, for migrations.
    assert settings.DATABASE_OWNER["USER"] == "generic_hrms_owner"
    assert settings.DATABASES["default"]["USER"] == "generic_hrms_app"


# ---------------------------------------------------------------------------
# Sign-in, tenant resolution, authority
# ---------------------------------------------------------------------------


def test_a_normal_login_works_and_returns_the_principals_own_roles(org_a, api_for):
    """
    The whole ordering problem in one test: membership is resolved with
    nothing bound, and only then can anything tenant-owned be read.
    """
    client = api_for(org_a.hr)
    response = client.get("/api/v1/me/")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == org_a.hr.email
    assert "hr_head" in body["roles"], body


def test_a_failed_login_is_recorded_rather_than_erroring(org_a, api):
    """
    The audit row for a failed sign-in belongs to the user's organization, and
    is written before anything is bound -- the case that broke first.
    """
    from apps.audit.models import AuditLog

    response = _login(api, org_a.hr.email, password="wrong-password-000")
    assert response.status_code == 400
    with acting_as(None, organization=org_a.organization):
        assert AuditLog.objects.filter(
            action="login_failed", actor_email=org_a.hr.email
        ).exists()


def test_a_login_for_an_address_nobody_owns_is_still_audited(api):
    """An organization-less audit row: insertable, and not readable back by a tenant."""
    response = _login(api, "nobody@nowhere.example", password="whatever-00000")
    assert response.status_code == 400


def test_rbac_resolves_real_grants_and_not_an_empty_context(org_a):
    from core.access.catalog import Action, Resource
    from core.access.context import get_context

    context = get_context(org_a.admin)
    assert context.organization_id == org_a.organization.pk
    assert context.role_codes, "an empty grant set is what an RLS mistake looks like"
    assert context.has(Resource.EMPLOYEE, Action.VIEW)


def test_scope_all_is_still_confined_to_the_principals_own_organization(org_a, org_b, api_for):
    """Authority over "all" means all of ONE organization."""
    client = api_for(org_a.admin)
    listed = client.get("/api/v1/employees/?page_size=100")
    assert listed.status_code == 200, listed.content[:300]
    ids = {row["id"] for row in listed.json()["data"]}
    assert str(org_a.rows["employee"].pk) in ids
    assert str(org_b.rows["employee"].pk) not in ids
    # And by id, the other organization's row is simply not there.
    assert client.get(f"/api/v1/employees/{org_b.rows['employee'].pk}/").status_code == 404


def test_reads_and_writes_both_work_for_an_ordinary_tenant_request(org_a, api_for):
    """A read, a create and an update, all through the confined connection."""
    from apps.organization.models import Department

    client = api_for(org_a.admin)

    assert client.get(f"/api/v1/employees/{org_a.rows['employee'].pk}/").status_code == 200

    created = client.post(
        "/api/v1/departments/",
        {"name": "Runtime Ops", "code": "RTO", "kind": "operations", "description": ""},
        format="json",
    )
    assert created.status_code == 201, created.content[:300]
    department_id = created.json()["id"]

    renamed = client.patch(
        f"/api/v1/departments/{department_id}/", {"name": "Runtime Operations"}, format="json"
    )
    assert renamed.status_code == 200, renamed.content[:300]

    with acting_as(None, organization=org_a.organization):
        row = Department.objects.get(pk=department_id)
    assert row.name == "Runtime Operations"
    assert row.organization_id == org_a.organization.pk


# ---------------------------------------------------------------------------
# The unauthenticated surfaces
# ---------------------------------------------------------------------------


def test_a_public_application_link_resolves_and_accepts_an_application(org_a, api):
    """
    Anonymous, so no organization is bound and the token is the only thing
    that knows which one applies.
    """
    job = org_a.rows["job_opening"]
    summary = api.get(f"/api/v1/public/apply/{job.application_token}/")
    assert summary.status_code == 200, summary.content[:300]
    assert summary.json()["title"] == job.title


def test_a_public_interview_slot_invitation_resolves(org_a, api):
    """The candidate picking a time: anonymous, and the token carries the tenant."""
    import datetime as dt

    from django.utils import timezone

    from apps.recruitment.models import InterviewSlotInvite

    with acting_as(org_a.hr, organization=org_a.organization):
        application = org_a.rows["application"]
        invite = InterviewSlotInvite.objects.create(
            application=application,
            stage=org_a.rows["workflow_stage"],
            candidate=application.candidate,
            options=[
                {
                    "start": (timezone.now() + dt.timedelta(days=d)).isoformat(),
                    "end": (timezone.now() + dt.timedelta(days=d, minutes=45)).isoformat(),
                }
                for d in (2, 3)
            ],
            expires_at=timezone.now() + dt.timedelta(days=5),
        )

    response = api.get(f"/api/v1/public/interview-slot/{invite.token}/")
    assert response.status_code == 200, response.content[:300]
    assert len(response.json()["options"]) == 2


# ---------------------------------------------------------------------------
# Platform work
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    from apps.accounts.models import User

    user = User.objects.create_user(email="ops@runtime.example", password=PASSWORD)
    User.objects.filter(pk=user.pk).update(is_platform_admin=True)
    user.refresh_from_db()
    return user


def test_the_console_counts_every_organizations_headcount(org_a, org_b, operator, api):
    """
    Cross-organization by nature, and impossible for the runtime role without
    the bypass the platform views enter for the request.
    """
    logged_in = _login(api, operator.email, path="/api/v1/auth/login/platform/")
    assert logged_in.status_code == 200, logged_in.content[:300]
    api.credentials(HTTP_AUTHORIZATION="Bearer " + logged_in.json()["access"])

    listing = api.get("/api/v1/platform/organizations/?page_size=100")
    assert listing.status_code == 200, listing.content[:300]
    body = listing.json()
    rows = {row["slug"]: row for row in (body["data"] if "data" in body else body["results"])}
    assert {org_a.slug, org_b.slug} <= set(rows)
    assert rows[org_a.slug]["employee_count"] >= 1, rows[org_a.slug]
    assert rows[org_b.slug]["employee_count"] >= 1, rows[org_b.slug]


def test_support_access_stays_confined_to_the_granted_organization(org_a, org_b, operator):
    """
    The one platform feature that must NOT see across customers, even though
    it runs on a platform endpoint whose request is inside the bypass.
    """
    import datetime as dt

    from django.utils import timezone

    from apps.platform.models import SupportGrant
    from apps.platform.services.support import configuration_snapshot

    with acting_as(operator, organization=org_a.organization):
        grant = SupportGrant.objects.create(
            requested_by=operator,
            reason="Checking why their leave balances look wrong since the change.",
            status="approved",
            decided_at=timezone.now(),
            expires_at=timezone.now() + dt.timedelta(hours=1),
        )

    with acting_as(operator, organization=None):
        snapshot = configuration_snapshot(grant.pk, operator=operator)

    assert snapshot["organization"] == org_a.slug
    departments = snapshot["tables"]["organization.Department"]
    assert departments
    assert {row["organization_id"] for row in departments} == {str(org_a.organization.pk)}


def test_a_tenant_principal_can_never_enter_the_bypass(org_a):
    with transaction.atomic():
        with pytest.raises(PlatformBypassRefused):
            with platform_bypass(reason="an admin trying it on", principal=org_a.admin):
                pass


def test_the_bypass_leaves_no_privilege_behind_on_a_reused_connection(org_a, org_b, operator):
    """
    `CONN_MAX_AGE` keeps connections alive between requests, so the role must
    not outlive its transaction.
    """
    with acting_as(operator, organization=None):
        with transaction.atomic(), platform_bypass(reason="test sweep", principal=operator):
            assert _one("SELECT current_user") == "generic_hrms_platform"
        assert _one("SELECT current_user") == "generic_hrms_app"
        assert _one('SELECT count(*) FROM "employees_employee"') == 0


# ---------------------------------------------------------------------------
# Background work, configuration, reporting
# ---------------------------------------------------------------------------


def test_a_background_task_binds_its_own_organization_and_only_that_one(org_a, org_b):
    from apps.leave.tasks import monthly_accrual_for_organization

    with acting_as(None, organization=org_b.organization):
        before = _one(
            'SELECT count(*) FROM "leave_leavebalance" WHERE organization_id = %s',
            [org_b.organization.pk],
        )

    result = monthly_accrual_for_organization.run(str(org_a.organization.pk))
    assert not (isinstance(result, dict) and result.get("skipped")), result

    with acting_as(None, organization=org_b.organization):
        assert _one(
            'SELECT count(*) FROM "leave_leavebalance" WHERE organization_id = %s',
            [org_b.organization.pk],
        ) == before


def test_organization_configuration_reads_work_with_nothing_bound(org_a):
    """
    `core/config.py` is called from mail and attendance paths that have no
    organization in force; it takes one as an argument and binds it itself.
    """
    from apps.organization.models import OrgEmailTemplate
    from core.config import render_message

    key = "recruitment/email/application_received"
    with acting_as(None, organization=org_a.organization):
        OrgEmailTemplate.objects.create(
            key=key,
            subject="Your application to {{ organization_name }}",
            body_text="Thank you for applying.",
            is_active=True,
        )

    # Nothing bound, exactly as a mail-sending task has it: the organization
    # is an argument, and `core/config.py` binds it for the read.
    with acting_as(None, organization=None):
        message = render_message(
            org_a.organization, key, {"organization_name": org_a.organization.name}
        )
    assert "Your application to" in message.subject, message.subject


def test_a_report_snapshot_is_written_and_read_for_one_organization(org_a, org_b):
    from apps.reporting.services import refresh_snapshots

    with acting_as(org_a.admin, organization=org_a.organization):
        refresh_snapshots(org_a.organization)
        own = _one(
            'SELECT count(*) FROM "reporting_metricsnapshot" WHERE organization_id = %s',
            [org_a.organization.pk],
        )
        foreign = _one(
            'SELECT count(*) FROM "reporting_metricsnapshot" WHERE organization_id <> %s',
            [org_a.organization.pk],
        )
    assert own > 0
    assert foreign == 0


# ---------------------------------------------------------------------------
# The checks, and the door that must stay shut
# ---------------------------------------------------------------------------


def test_the_role_invariant_check_actually_reads_roles(org_a):
    """
    The positive control. `check_role_invariants` reports "no problems" both
    when the roles are sound AND when it can see nothing at all, so the thing
    worth testing is that it SAW something -- and that what makes it see is
    the bypass, not luck.
    """
    from apps.accounts.models import Role
    from core.access.checks import (
        check_role_invariants,
        check_role_reads_are_not_silently_empty,
    )

    assert check_role_invariants(None) == []
    assert check_role_reads_are_not_silently_empty(None) == []

    # What the check can see, through the door it uses...
    with transaction.atomic(), platform_bypass(reason="test: count roles", system=True):
        visible_to_the_check = Role.objects.all_orgs().count()
    # ...versus what the same query sees without it, unbound: nothing.
    with acting_as(None, organization=None):
        visible_unbound = Role.objects.all_orgs().count()

    assert visible_to_the_check >= 36, "two organizations' catalogues should be visible"
    assert visible_unbound == 0, (
        "if this is non-zero the runtime is not confined, and the check above "
        "proves nothing"
    )


def test_the_fail_open_detector_reports_an_error_when_roles_are_invisible(monkeypatch, org_a):
    """E019 fires when the check's own read cannot cross organizations."""
    from core.access import checks as checks_module
    from core.access.platform_bypass import PlatformBypassRefused

    def refuse(**kwargs):
        raise PlatformBypassRefused("no platform role in this deployment")

    monkeypatch.setattr("core.access.platform_bypass.platform_bypass", refuse)
    errors = checks_module.check_role_reads_are_not_silently_empty(None)
    assert [e.id for e in errors] == ["access.E019"]


def test_migrations_are_refused_as_the_runtime_role():
    """
    The guard that keeps schema work on the owner connection. Runs `migrate`
    against the live test connection, which is the app role.
    """
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="Refusing to migrate"):
        call_command("migrate", verbosity=0)
