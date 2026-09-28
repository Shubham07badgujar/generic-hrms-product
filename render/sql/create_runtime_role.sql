-- Create the confined runtime role on a managed Postgres (Render).
--
-- WHY. Release 2 splits the database roles: migrations own the tables, and the
-- application connects as a role that owns nothing, so row-level security
-- applies to it. A table's owner is exempt from RLS -- so on a single-role
-- deployment the policies exist and enforce nothing. This script is how you
-- get the second role on a host that does not give you a superuser.
--
-- RUN IT AS THE DATABASE OWNER (Render's default user), from psql:
--
--     psql "$DATABASE_URL" -v app_password="'choose-a-strong-password'" \
--          -f render/sql/create_runtime_role.sql
--
-- IT MAY REFUSE, and that is not a failure of this script. Creating a role
-- needs CREATEROLE, which a managed provider may withhold. If it does, you
-- have two honest options, both described in docs/DEPLOY_ON_RENDER.md:
--   * run single-role for now -- composite foreign keys and the audit trigger
--     still hold, RLS does not; or
--   * use a provider that gives you role creation.
--
-- Check afterwards with:
--     SELECT rolname, rolbypassrls, rolcanlogin FROM pg_roles
--      WHERE rolname LIKE 'generic_hrms%';

\set ON_ERROR_STOP on

-- The runtime role: LOGIN, owns nothing, cannot bypass RLS.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'generic_hrms_app') THEN
        EXECUTE format(
            'CREATE ROLE generic_hrms_app LOGIN PASSWORD %L '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
            :'app_password'
        );
        RAISE NOTICE 'created generic_hrms_app';
    ELSE
        RAISE NOTICE 'generic_hrms_app already exists; leaving it alone';
    END IF;
END
$$;

-- The platform role: NOLOGIN, BYPASSRLS. Reached only through
-- `SET LOCAL ROLE` inside core/access/platform_bypass.py, never connected to
-- directly, which is why it has no password.
--
-- BYPASSRLS normally requires a superuser to grant. If this block fails, the
-- platform console and every other cross-organization path will not work, so
-- prefer single-role over a half-applied split -- again, see the deploy doc.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'generic_hrms_platform') THEN
        CREATE ROLE generic_hrms_platform NOLOGIN NOSUPERUSER BYPASSRLS;
        RAISE NOTICE 'created generic_hrms_platform';
    ELSE
        RAISE NOTICE 'generic_hrms_platform already exists; leaving it alone';
    END IF;
END
$$;

-- The runtime role may BECOME the platform role, but does not INHERIT it:
-- NOINHERIT means the bypass has to be asked for explicitly, by name, in one
-- reviewable place, rather than being carried silently by every query.
GRANT generic_hrms_platform TO generic_hrms_app;
ALTER ROLE generic_hrms_app NOINHERIT;

-- The table privileges themselves are granted by `dbguard.0006_runtime_grants`
-- the next time migrations run -- including the REVOKE of DELETE and TRUNCATE
-- on the audit trail. That migration skips silently when these roles are
-- absent, so run it AFTER this script:
--
--     DATABASE_URL=$DATABASE_OWNER_URL python manage.py migrate dbguard
