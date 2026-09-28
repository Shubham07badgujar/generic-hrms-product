"""Table privileges for the non-owner runtime roles, per database.

Roles are cluster objects (dev/postgres/init/01_roles.sh); privileges are per
database, so they live here and every database the owner migrates -- the dev
database and pytest's test database alike -- receives the same grants.
The audit table: SELECT, INSERT, UPDATE only. No DELETE, no TRUNCATE."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('dbguard', '0005_audit_guards'),
    ]

    operations = [
        migrations.RunSQL(
            sql='DO $$\nBEGIN\n  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'generic_hrms_app\')\n     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'generic_hrms_platform\') THEN\n    GRANT USAGE ON SCHEMA public TO generic_hrms_app, generic_hrms_platform;\n    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO generic_hrms_app, generic_hrms_platform;\n    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO generic_hrms_app, generic_hrms_platform;\n    REVOKE DELETE, TRUNCATE ON "audit_auditlog" FROM generic_hrms_app, generic_hrms_platform;\n    ALTER DEFAULT PRIVILEGES IN SCHEMA public\n      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO generic_hrms_app, generic_hrms_platform;\n    ALTER DEFAULT PRIVILEGES IN SCHEMA public\n      GRANT USAGE, SELECT ON SEQUENCES TO generic_hrms_app, generic_hrms_platform;\n  ELSE\n    RAISE NOTICE \'dbguard: generic_hrms_app/generic_hrms_platform roles absent; runtime grants skipped\';\n  END IF;\nEND $$;',
            reverse_sql="DO $$\nBEGIN\n  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'generic_hrms_app') THEN\n    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM generic_hrms_app, generic_hrms_platform;\n    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM generic_hrms_app, generic_hrms_platform;\n  END IF;\nEND $$;",
        ),
    ]
