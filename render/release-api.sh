#!/usr/bin/env bash
#
# Migrations, run as the DATABASE OWNER.
#
# Run this as a Render "pre-deploy command" on hrms-api, or by hand from a
# shell on that service. It is separate from the build for two reasons: three
# services build in parallel and must not all migrate, and a build can happen
# for a deploy that never goes live.
#
# WHY THE SWAP. From release 2 the application connects as an unprivileged
# role that owns nothing, so row-level security applies to it. Schema changes
# need ownership, and `manage.py migrate` REFUSES under any other role rather
# than half-applying a migration and leaving the schema in between. So the
# migration runs with DATABASE_URL pointed at the owner for the length of this
# script, and nothing else ever sees those credentials.
#
# With DATABASE_OWNER_URL unset this falls back to DATABASE_URL, which is
# correct for a single-role deployment -- see docs/DEPLOY_ON_RENDER.md for what
# you give up in that case.
set -euo pipefail

if [[ -n "${DATABASE_OWNER_URL:-}" ]]; then
  echo "--- migrating as the owner role"
  export DATABASE_URL="$DATABASE_OWNER_URL"
else
  echo "--- DATABASE_OWNER_URL is not set; migrating with DATABASE_URL"
  echo "    (single-role deployment: row-level security will not apply to the"
  echo "     runtime, because a table's owner is exempt from it)"
fi

python manage.py migrate --noinput

echo "--- seeding the plan catalogue (idempotent)"
# Plans are deployment-wide and provisioning refuses without them, so a fresh
# database needs this before the first customer can be created.
python manage.py seed_plans

echo "--- release complete"
echo
echo "Next, ONCE, to create the first platform operator:"
echo "    python manage.py bootstrap_platform_admin --email you@example.com"
