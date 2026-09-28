"""
The audit trail is protected by the database, not only by the application.

Migration 0005 installs a BEFORE UPDATE trigger that lets an audit row be
SCRUBBED (personal content erased, as the purge does) and nothing else: the
identity of the event -- who-what-when -- is immutable, and payload may be
blanked but never rewritten. Migration 0006 revokes DELETE and TRUNCATE from
the runtime role, so an audit row cannot be removed through the application
connection at all. Deletion is not guarded by a trigger because the test
harness itself TRUNCATEs as owner between some tests.
"""

from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction

from core.middleware import acting_as

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]


def _sql(query, params=None):
    with connection.cursor() as cursor:
        cursor.execute(query, params or [])
        return cursor.fetchall() if cursor.description else cursor.rowcount


def _refused(query, params=None, match=None):
    with pytest.raises(DatabaseError, match=match):
        with transaction.atomic():
            _sql(query, params)


@pytest.fixture
def audit_row(org_a):
    """
    One of A's audit rows, with A bound for the whole test.

    Release 2: the suite connects as the confined runtime role, so an UPDATE
    with nothing bound matches no rows and the trigger never fires -- the
    refusals below would "pass" by touching nothing. Bound, the statement
    reaches the row and the guard is what stops it.
    """
    with acting_as(None, organization=org_a.organization):
        yield org_a.rows["audit_log"]


@pytest.mark.parametrize(
    "column, value",
    [
        ("action", "delete"),
        ("resource", "payroll"),
        ("entity_type", "payroll.Payslip"),
        ("entity_id", "999"),
        ("occurred_at", "2001-01-01T00:00:00Z"),
    ],
)
def test_the_identity_of_an_event_can_never_change(audit_row, column, value):
    _refused(f'UPDATE "audit_auditlog" SET "{column}" = %s WHERE id = %s', [value, audit_row.pk],
             match="immutable|audit")


def test_an_event_cannot_be_moved_to_another_organization(audit_row, org_b):
    _refused('UPDATE "audit_auditlog" SET organization_id = %s WHERE id = %s',
             [org_b.organization.pk, audit_row.pk], match="immutable|audit")


def test_payload_may_be_erased_but_never_rewritten(audit_row):
    _refused('UPDATE "audit_auditlog" SET reason = %s WHERE id = %s',
             ["a more convenient story", audit_row.pk], match="audit")
    _refused("UPDATE \"audit_auditlog\" SET metadata = '{\"forged\": true}'::jsonb WHERE id = %s",
             [audit_row.pk], match="audit")


def test_a_scrub_is_allowed(audit_row):
    """Exactly what apps/audit/purge.py does."""
    with transaction.atomic():
        updated = _sql(
            "UPDATE \"audit_auditlog\" SET before = NULL, after = NULL, metadata = '{}'::jsonb, "
            "entity_label = '', reason = '', ip = NULL, user_agent = '', actor_email = '' "
            "WHERE id = %s",
            [audit_row.pk],
        )
    assert updated == 1


def test_the_purge_service_still_works_as_the_runtime_role(org_a):
    from apps.audit.models import AuditLog
    from apps.audit.purge import scrub_for_purge

    with acting_as(None, organization=org_a.organization):
        assert AuditLog.objects.exclude(entity_label="").exists(), (
            "nothing to scrub, so a clean result afterwards would prove nothing"
        )
        scrubbed = scrub_for_purge(org_a.organization, customer_user_ids=[org_a.hr.pk])
        assert scrubbed >= 1
        assert not AuditLog.objects.filter(
            organization=org_a.organization
        ).exclude(entity_label="").exists()


def test_the_runtime_role_cannot_delete_or_truncate_the_trail(audit_row):
    # Positive control: the row is visible to it, so a refusal is about
    # privilege and not about RLS hiding it.
    assert _sql('SELECT count(*) FROM "audit_auditlog" WHERE id = %s', [audit_row.pk])[0][0] == 1
    _refused('DELETE FROM "audit_auditlog" WHERE id = %s', [audit_row.pk], match="permission denied")
    _refused('TRUNCATE "audit_auditlog"', match="permission denied")


def test_the_platform_role_cannot_delete_the_trail_either(audit_row):
    """BYPASSRLS widens visibility, not privilege."""
    _sql("SET LOCAL ROLE generic_hrms_platform")
    _refused('DELETE FROM "audit_auditlog" WHERE id = %s', [audit_row.pk], match="permission denied")


def test_the_runtime_role_can_still_append(org_a):
    from apps.audit.models import AuditLog

    with acting_as(org_a.admin, organization=org_a.organization):
        before = AuditLog.objects.count()
        AuditLog.objects.create(
            organization=org_a.organization, action="update",
            entity_type="test.Thing", entity_id="1",
        )
        assert AuditLog.objects.count() == before + 1
