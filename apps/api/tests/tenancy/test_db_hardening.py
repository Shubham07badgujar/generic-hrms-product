"""
The database refuses cross-organization access on its own.

These tests hold the DATABASE layer (apps/dbguard) to account, independently of
the application layer above it, which stays in force and has its own suites.
They use the tenancy builder's two broad organizations and REAL ids from the
other one -- a random UUID would be refused for not existing and prove nothing.

RELEASE 2 changed the premise underneath this file, for the better: the suite
now CONNECTS as `generic_hrms_app`, so `_become_app_role()` is a no-op
assertion rather than a switch, and anything needing the god's-eye view --
counting what really exists, or writing a row into another organization to
prove the database refuses it -- goes through `across_organizations()`, the
same named door the platform uses. Composite FKs
are DEFERRABLE INITIALLY DEFERRED and a test transaction never commits, so those
tests say SET CONSTRAINTS ALL IMMEDIATE to make the database check at once.

Each refusal has a positive control, so a table that is simply empty, or a
role that sees nothing at all, cannot pass for isolation.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.dbguard.manifest import (
    ORG_FK_EDGES,
    RLS_AUDIT_TABLE,
    RLS_MEMBERSHIP_TABLE,
    RLS_SUBSCRIPTION_TABLE,
    RLS_TENANT_TABLES,
)
from core.access.platform_bypass import PlatformBypassRefused, platform_bypass
from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

APP_ROLE = "generic_hrms_app"


def _sql(query, params=None):
    with connection.cursor() as cursor:
        cursor.execute(query, params or [])
        return cursor.fetchall() if cursor.description else cursor.rowcount


def _one(query, params=None):
    return _sql(query, params)[0][0]


def _columns(table):
    with connection.cursor() as cursor:
        return [c.name for c in connection.introspection.get_table_description(cursor, table)]


def _become_app_role():
    """
    Assert the confined runtime role is in force.

    Release 1 switched into it here; release 2 connects as it, so this is now
    a check that the premise still holds -- and it stays, because a test that
    silently ran as the owner would pass while proving nothing.
    """
    assert _one("SELECT current_user") == APP_ROLE


def _counts_everywhere(organization):
    """Rows each tenant table really holds for an organization, RLS set aside."""
    from .conftest import across_organizations

    out = {}
    with across_organizations():
        for table in RLS_TENANT_TABLES:
            out[table] = _one(
                f'SELECT count(*) FROM "{table}" WHERE organization_id = %s', [organization.pk]
            )
    return out


# ---------------------------------------------------------------------------
# The roles are what the design says
# ---------------------------------------------------------------------------


def test_the_runtime_role_owns_nothing_and_cannot_bypass_rls():
    rows = dict(_sql(
        "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname IN "
        "('generic_hrms_app', 'generic_hrms_platform')"
    ))
    assert rows == {"generic_hrms_app": False, "generic_hrms_platform": True}
    assert _one("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=%s", [APP_ROLE]) == 0


# ---------------------------------------------------------------------------
# Composite foreign keys
# ---------------------------------------------------------------------------


def test_every_manifest_edge_has_a_validated_constraint_in_this_database():
    """The test database was migrated from zero; every one of the 151 is there."""
    present = {
        (row[0], row[1])
        for row in _sql(
            "SELECT c.conrelid::regclass::text, a.attname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND c.conname LIKE 'dbg_fk%%' AND c.convalidated "
            "AND array_length(c.conkey, 1) = 2"
        )
    }
    missing = [(s, c) for s, c, _t, _pk in ORG_FK_EDGES if (s.strip('"'), c) not in present]
    assert len(ORG_FK_EDGES) >= 150
    assert missing == []


@pytest.mark.parametrize(
    "source_row, fk_column, target_row",
    [
        ("employee", "department_id", "department"),
        ("employee", "location_id", "location"),
        ("leave_request", "employee_id", "employee"),
        ("leave_request", "leave_type_id", "leave_type"),
        ("attendance", "employee_id", "employee"),
        ("payslip", "payroll_run_id", "payroll_run"),
        ("asset_allocation", "asset_id", "asset"),
        ("application", "candidate_id", "candidate"),
    ],
)
def test_the_database_refuses_a_row_pointing_at_another_organizations_parent(
    org_a, org_b, source_row, fk_column, target_row
):
    from .conftest import across_organizations

    source = org_a.rows[source_row]
    foreign_target = org_b.rows[target_row]
    own_target = org_a.rows[target_row]
    table = source._meta.db_table

    # Bound to A, the legitimate write lands: the constraint is what this
    # tests, so the row has to be reachable in the first place.
    with acting_as(None, organization=org_a.organization):
        _sql("SET CONSTRAINTS ALL IMMEDIATE")
        with transaction.atomic():
            updated = _sql(
                f'UPDATE "{table}" SET "{fk_column}" = %s WHERE id = %s',
                [own_target.pk, source.pk],
            )
        assert updated == 1, "A cannot reach its own row, so the refusal below proves nothing"

    # The cross-organization write is attempted with RLS set aside, so that
    # what refuses it is the composite FOREIGN KEY and not the policy. Both
    # protect this; only one of them is the subject here.
    with across_organizations():
        _sql("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _sql(
                    f'UPDATE "{table}" SET "{fk_column}" = %s WHERE id = %s',
                    [foreign_target.pk, source.pk],
                )


def test_a_null_optional_fk_is_still_allowed(org_a):
    """MATCH SIMPLE: a NULL skips the check, as an optional relation must."""
    employee = org_a.rows["employee"]
    with acting_as(None, organization=org_a.organization):
        _sql("SET CONSTRAINTS ALL IMMEDIATE")
        with transaction.atomic():
            updated = _sql(
                'UPDATE "employees_employee" SET "team_id" = NULL WHERE id = %s', [employee.pk]
            )
    assert updated == 1


# ---------------------------------------------------------------------------
# Row-level security: reads
# ---------------------------------------------------------------------------


def test_a_query_that_omits_the_organization_filter_still_sees_one_organization(org_a, org_b):
    """
    The accidental case RLS exists for: raw SQL with no WHERE organization_id,
    over EVERY tenant table, as the runtime role bound to A.
    """
    a_rows, b_rows = _counts_everywhere(org_a.organization), _counts_everywhere(org_b.organization)
    shared = [t for t in RLS_TENANT_TABLES if a_rows[t] and b_rows[t]]
    assert len(shared) >= 40, f"only {len(shared)} tables populated in both organizations"

    _become_app_role()
    with acting_as(org_a.admin, organization=org_a.organization):
        leaked = {}
        for table in RLS_TENANT_TABLES:
            total, foreign = _sql(
                f'SELECT count(*), count(*) FILTER (WHERE organization_id <> %s) FROM "{table}"',
                [org_a.organization.pk],
            )[0]
            if foreign:
                leaked[table] = foreign
            if table in shared:
                assert total == a_rows[table], f"{table}: A should see all {a_rows[table]} of its own rows"
    assert leaked == {}


def test_the_orm_escape_hatch_is_confined_too(org_a, org_b):
    """`all_orgs()` deliberately drops the application filter; the database does not."""
    from apps.employees.models import Employee

    from .conftest import across_organizations

    # Ground truth: B really does have employees, so the confinement below is
    # the policy and not an empty table.
    with across_organizations():
        assert Employee.objects.all_orgs().filter(organization=org_b.organization).exists()

    _become_app_role()
    with acting_as(org_a.admin, organization=org_a.organization):
        orgs = set(Employee.objects.all_orgs().values_list("organization_id", flat=True))
    assert orgs == {org_a.organization.pk}


def test_nothing_bound_means_nothing_visible(org_a, org_b):
    """Fail closed: no organization in context => no tenant row at all."""
    _become_app_role()
    with acting_as(None, organization=None):
        visible = {
            table: _one(f'SELECT count(*) FROM "{table}"')
            for table in ("employees_employee", "payroll_payslip", "leave_leaverequest",
                          "recruitment_candidate", RLS_AUDIT_TABLE)
        }
    assert set(visible.values()) == {0}, visible


def test_scope_all_admin_and_ceo_are_confined_like_everyone_else(org_a, org_b):
    """Scope.ALL is an application concept; the database does not know or care."""
    _become_app_role()
    for principal in (org_a.admin, org_a.hr):
        with acting_as(principal, organization=org_a.organization):
            assert _one(
                'SELECT count(*) FROM "employees_employee" WHERE organization_id = %s',
                [org_b.organization.pk],
            ) == 0
            assert _one('SELECT count(*) FROM "employees_employee"') > 0


def test_the_membership_table_resolves_a_user_before_any_organization_is_bound(org_a, org_b):
    """
    Membership is what DERIVES the organization, so its policy also admits the
    user's own row by user id -- and nobody else's.
    """
    from core.middleware import _current_org, _current_user

    _become_app_role()
    user_token, org_token = _current_user.set(org_a.hr), _current_org.set(None)
    try:
        rows = _sql(f'SELECT user_id, organization_id FROM "{RLS_MEMBERSHIP_TABLE}"')
    finally:
        _current_org.reset(org_token)
        _current_user.reset(user_token)
    assert rows == [(org_a.hr.pk, org_a.organization.pk)]


def test_a_customer_reads_its_own_subscription_and_no_other(org_a, org_b):
    from apps.platform.models import Plan, Subscription

    from .conftest import across_organizations

    # A subscription is the PLATFORM's row about a customer: the customer's own
    # role may read it and nothing more, so the fixture writes it as billing
    # does.
    with across_organizations():
        plan = Plan.objects.create(code="rls-test", name="RLS test")
        for world in (org_a, org_b):
            Subscription.objects.update_or_create(
                organization=world.organization, defaults={"plan": plan}
            )

    _become_app_role()
    with acting_as(org_a.admin, organization=org_a.organization):
        orgs = {row[0] for row in _sql(f'SELECT organization_id FROM "{RLS_SUBSCRIPTION_TABLE}"')}
        # Read-only to a customer, and REFUSED rather than silently ignored.
        # `subscription_lock` (dbguard.0007) admits the organization's own row
        # to a locking read -- which the seat check needs -- with
        # `WITH CHECK (false)`, so an actual write raises. Billing changes are
        # platform work, through the bypass.
        with pytest.raises(DatabaseError, match="row-level security"):
            with transaction.atomic():
                _sql(f'UPDATE "{RLS_SUBSCRIPTION_TABLE}" SET status = status')
    assert orgs == {org_a.organization.pk}


# ---------------------------------------------------------------------------
# Row-level security: writes
# ---------------------------------------------------------------------------


def test_another_organizations_rows_cannot_be_updated_or_deleted_by_id(org_a, org_b):
    b_employee = org_b.rows["employee"]
    b_leave = org_b.rows["leave_request"]
    a_employee = org_a.rows["employee"]

    _become_app_role()
    with acting_as(org_a.admin, organization=org_a.organization):
        # Positive control: A's own row IS updatable.
        assert _sql('UPDATE "employees_employee" SET middle_name = %s WHERE id = %s',
                    ["A", a_employee.pk]) == 1
        assert _sql('UPDATE "employees_employee" SET middle_name = %s WHERE id = %s',
                    ["hijacked", b_employee.pk]) == 0
        assert _sql('DELETE FROM "leave_leaverequest" WHERE id = %s', [b_leave.pk]) == 0

    # Checked from outside afterwards: B's rows are untouched, and still there.
    from .conftest import across_organizations

    with across_organizations():
        assert _one(
            'SELECT middle_name FROM "employees_employee" WHERE id = %s', [b_employee.pk]
        ) != "hijacked"
        assert _one('SELECT count(*) FROM "leave_leaverequest" WHERE id = %s', [b_leave.pk]) == 1


def test_a_row_cannot_be_written_into_another_organization(org_a, org_b):
    """WITH CHECK: an INSERT or an UPDATE that would land in B is refused."""
    a_department = org_a.rows["department"]

    _become_app_role()
    with acting_as(org_a.admin, organization=org_a.organization):
        # A copy of A's own department, re-labelled into B.
        columns = [c for c in _columns("organization_department") if c not in ("id", "organization_id", "name", "code")]
        column_list = ", ".join(f'"{c}"' for c in columns)
        with pytest.raises(DatabaseError, match="row-level security"):
            with transaction.atomic():
                _sql(
                    f'INSERT INTO "organization_department" (id, organization_id, name, code, {column_list}) '
                    f'SELECT gen_random_uuid(), %s, %s, %s, {column_list} FROM "organization_department" WHERE id = %s',
                    [org_b.organization.pk, "Planted", "PLANT", a_department.pk],
                )
        with pytest.raises(DatabaseError, match="row-level security"):
            with transaction.atomic():
                _sql('UPDATE "organization_department" SET organization_id = %s WHERE id = %s',
                     [org_b.organization.pk, a_department.pk])


# ---------------------------------------------------------------------------
# Platform operators and the one named bypass
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    from apps.accounts.models import User

    user = User.objects.create_user(email="ops@dbguard.example", password="not-used-12345")
    User.objects.filter(pk=user.pk).update(is_platform_admin=True)
    user.refresh_from_db()
    return user


def test_a_platform_operator_sees_no_tenant_row_without_the_bypass(org_a, org_b, operator):
    _become_app_role()
    with acting_as(operator, organization=None):
        assert _one('SELECT count(*) FROM "employees_employee"') == 0


def test_the_bypass_crosses_organizations_for_a_platform_operator_only_inside_it(org_a, org_b, operator):
    _become_app_role()
    with acting_as(operator, organization=None):
        with transaction.atomic(), platform_bypass(reason="test: console headcount", principal=operator):
            orgs = {row[0] for row in _sql('SELECT DISTINCT organization_id FROM "employees_employee"')}
            assert _one("SELECT current_user") == "generic_hrms_platform"
        assert {org_a.organization.pk, org_b.organization.pk} <= orgs
        # Back to the runtime role, and confined again, the moment it ends.
        assert _one("SELECT current_user") == APP_ROLE
        assert _one('SELECT count(*) FROM "employees_employee"') == 0


def test_a_tenant_request_can_never_enter_the_bypass(org_a):
    """Whatever its roles -- the Admin holds Scope.ALL almost everywhere."""
    with transaction.atomic():
        with pytest.raises(PlatformBypassRefused):
            with platform_bypass(reason="an admin trying", principal=org_a.admin):
                pass


def test_the_bypass_demands_a_reason_and_a_transaction(operator):
    with transaction.atomic():
        with pytest.raises(PlatformBypassRefused, match="reason"):
            with platform_bypass(reason="  ", principal=operator):
                pass


def test_the_bypass_refuses_outside_a_transaction(operator, monkeypatch):
    monkeypatch.setattr(type(connection), "in_atomic_block", False, raising=False)
    with pytest.raises(PlatformBypassRefused, match="transaction"):
        with platform_bypass(reason="no transaction", principal=operator):
            pass


# ---------------------------------------------------------------------------
# Background work and Support Access -- the other bypass-shaped paths
# ---------------------------------------------------------------------------


def test_a_per_organization_celery_subtask_touches_only_its_organization(org_a, org_b):
    from apps.leave.tasks import monthly_accrual_for_organization

    b_before = _one('SELECT count(*) FROM "leave_leavebalance" WHERE organization_id = %s',
                    [org_b.organization.pk])
    _become_app_role()
    # The subtask binds its organization from its ARGUMENT, as every
    # @organization_task does; nothing is inherited.
    result = monthly_accrual_for_organization.run(str(org_a.organization.pk))
    assert not (isinstance(result, dict) and result.get("skipped")), result
    _sql("RESET ROLE")
    assert _one('SELECT count(*) FROM "leave_leavebalance" WHERE organization_id = %s',
                [org_b.organization.pk]) == b_before


def test_support_access_shows_only_the_granted_organization(org_a, org_b, operator):
    from django.utils import timezone

    from apps.platform.models import SupportGrant
    from apps.platform.services.support import configuration_snapshot

    with acting_as(operator, organization=org_a.organization):
        grant = SupportGrant.objects.create(
            requested_by=operator,
            reason="Checking why leave balances look wrong since the change.",
            status="approved",
            decided_at=timezone.now(),
            expires_at=timezone.now() + dt.timedelta(hours=1),
        )

    _become_app_role()
    with acting_as(operator, organization=None):
        snapshot = configuration_snapshot(grant.pk, operator=operator)
    department_orgs = {row["organization_id"] for row in snapshot["tables"]["organization.Department"]}
    assert department_orgs == {str(org_a.organization.pk)}
    assert _one("SELECT current_user") == APP_ROLE


# ---------------------------------------------------------------------------
# Coverage and the session sync
# ---------------------------------------------------------------------------


def test_every_organization_owned_table_has_rls_and_a_policy():
    """A model added later without RLS fails here, as E018 does for FKs."""
    from django.apps import apps

    from core.models import OrgOwnedModel, OrgOwnedTimestampedModel

    owned = {
        m._meta.db_table for m in apps.get_models()
        if issubclass(m, (OrgOwnedModel, OrgOwnedTimestampedModel)) and not m._meta.proxy
    }
    assert owned <= set(RLS_TENANT_TABLES), f"not covered by RLS: {sorted(owned - set(RLS_TENANT_TABLES))}"

    expected = set(RLS_TENANT_TABLES) | {RLS_MEMBERSHIP_TABLE, RLS_AUDIT_TABLE, RLS_SUBSCRIPTION_TABLE}
    enabled = {row[0] for row in _sql(
        "SELECT relname FROM pg_class WHERE relrowsecurity AND relnamespace = 'public'::regnamespace"
    )}
    with_policy = {row[0] for row in _sql("SELECT DISTINCT tablename FROM pg_policies WHERE schemaname='public'")}
    assert expected <= enabled
    assert expected <= with_policy


def test_the_global_tables_are_deliberately_left_without_rls():
    enabled = {row[0] for row in _sql(
        "SELECT relname FROM pg_class WHERE relrowsecurity AND relnamespace = 'public'::regnamespace"
    )}
    for table in ("organization_organization", "accounts_user", "platform_plan", "statutory_statutoryruleset"):
        assert table not in enabled, f"{table} is global by design and must not carry RLS"


def test_the_session_setting_follows_the_bound_organization_and_resets(org_a, org_b):
    with acting_as(None, organization=org_a.organization):
        assert _one("SELECT current_setting('app.org_id', true)") == str(org_a.organization.pk)
        with acting_as(None, organization=org_b.organization):
            assert _one("SELECT current_setting('app.org_id', true)") == str(org_b.organization.pk)
        assert _one("SELECT current_setting('app.org_id', true)") == str(org_a.organization.pk)
    with acting_as(None, organization=None):
        assert _one("SELECT current_setting('app.org_id', true)") == ""


def test_a_rolled_back_savepoint_does_not_leave_a_stale_organization(org_a, org_b):
    """
    set_config is transactional; after a savepoint that bound B rolls back, the
    database must not keep believing either A or B wrongly.
    """
    with acting_as(None, organization=org_a.organization):
        assert _one("SELECT current_setting('app.org_id', true)") == str(org_a.organization.pk)
        try:
            with transaction.atomic():
                with acting_as(None, organization=org_b.organization):
                    assert _one("SELECT current_setting('app.org_id', true)") == str(org_b.organization.pk)
                    raise RuntimeError("roll back this savepoint")
        except RuntimeError:
            pass
        assert _one("SELECT current_setting('app.org_id', true)") == str(org_a.organization.pk)


def test_e018_fails_the_build_for_an_unenforced_org_fk(monkeypatch):
    from apps.dbguard import manifest
    from core.access.checks import check_org_fks_are_enforced_by_the_database

    assert check_org_fks_are_enforced_by_the_database(None) == []
    monkeypatch.setattr(manifest, "ORG_FK_EDGES", manifest.ORG_FK_EDGES[1:])
    errors = check_org_fks_are_enforced_by_the_database(None)
    assert [e.id for e in errors] == ["access.E018"]
