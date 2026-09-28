"""Audit rows: UPDATE may only erase personal content, never rewrite history.

Applies to every role, the owner included. DELETE and TRUNCATE are refused to
the runtime roles by privilege (0006), not by trigger: pytest resets tables
with TRUNCATE as the owner, and the owner is migration-only anyway."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('dbguard', '0004_rls_policies'),
    ]

    operations = [
        migrations.RunSQL(
            sql='CREATE OR REPLACE FUNCTION dbguard_audit_scrub_only() RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n  -- Identity and history: never rewritten, by anyone.\n  IF NEW.id IS DISTINCT FROM OLD.id\n     OR NEW.organization_id IS DISTINCT FROM OLD.organization_id\n     OR NEW.action IS DISTINCT FROM OLD.action\n     OR NEW.resource IS DISTINCT FROM OLD.resource\n     OR NEW.entity_type IS DISTINCT FROM OLD.entity_type\n     OR NEW.entity_id IS DISTINCT FROM OLD.entity_id\n     OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at\n     OR NEW.request_id IS DISTINCT FROM OLD.request_id THEN\n    RAISE EXCEPTION \'audit_auditlog row % : identity/history fields are immutable\', OLD.id\n      USING ERRCODE = \'insufficient_privilege\';\n  END IF;\n  -- Personal content: may only be ERASED (the purge scrub, and actor SET NULL\n  -- when a login is deleted) -- never replaced with something else.\n  IF (NEW.before IS DISTINCT FROM OLD.before AND NEW.before IS NOT NULL)\n     OR (NEW.after IS DISTINCT FROM OLD.after AND NEW.after IS NOT NULL)\n     OR (NEW.metadata IS DISTINCT FROM OLD.metadata AND NEW.metadata <> \'{}\'::jsonb)\n     OR (NEW.entity_label IS DISTINCT FROM OLD.entity_label AND NEW.entity_label <> \'\')\n     OR (NEW.reason IS DISTINCT FROM OLD.reason AND NEW.reason <> \'\')\n     OR (NEW.ip IS DISTINCT FROM OLD.ip AND NEW.ip IS NOT NULL)\n     OR (NEW.user_agent IS DISTINCT FROM OLD.user_agent AND NEW.user_agent <> \'\')\n     OR (NEW.actor_email IS DISTINCT FROM OLD.actor_email AND NEW.actor_email <> \'\')\n     OR (NEW.actor_id IS DISTINCT FROM OLD.actor_id AND NEW.actor_id IS NOT NULL)\n     OR (NEW.subject_employee_id IS DISTINCT FROM OLD.subject_employee_id AND NEW.subject_employee_id IS NOT NULL) THEN\n    RAISE EXCEPTION \'audit_auditlog row % : content may only be scrubbed, not rewritten\', OLD.id\n      USING ERRCODE = \'insufficient_privilege\';\n  END IF;\n  RETURN NEW;\nEND $$;\nCREATE TRIGGER dbguard_audit_scrub_only BEFORE UPDATE ON "audit_auditlog"\n  FOR EACH ROW EXECUTE FUNCTION dbguard_audit_scrub_only();',
            reverse_sql='DROP TRIGGER IF EXISTS dbguard_audit_scrub_only ON "audit_auditlog"; DROP FUNCTION IF EXISTS dbguard_audit_scrub_only();',
        ),
    ]
