#!/bin/sh
# Runs ONCE, on first start of the generic-hrms-dev container, inside it.
#
# Creates the two roles beside the owner (the container's bootstrap user,
# generic_hrms_owner). Roles are cluster objects, so they live here; the
# per-table GRANTs live in a Django migration (apps/dbguard) so that every
# database the owner migrates -- this one, and pytest's test database --
# receives exactly the same privileges.
#
#   generic_hrms_app       LOGIN. The runtime role (release 2). Owns nothing,
#                          so row-level security applies to it and it cannot
#                          re-grant itself anything that is revoked.
#   generic_hrms_platform  NOLOGIN BYPASSRLS. Entered only with
#                          SET LOCAL ROLE inside core.access.platform_bypass.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$GENERIC_HRMS_APP_PASSWORD" <<'SQL'
CREATE ROLE generic_hrms_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE generic_hrms_platform NOLOGIN
  NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
-- The runtime role may enter the platform role, but only by an explicit
-- SET ROLE; NOINHERIT keeps its privileges from applying implicitly.
GRANT generic_hrms_platform TO generic_hrms_app;
ALTER ROLE generic_hrms_app NOINHERIT;
SQL
