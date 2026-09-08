#!/bin/sh
#
# Container start-up.
#
# Migrations run here rather than in a separate job because this stack has one
# API replica; with several, this would race and belongs in a one-shot task.
#
# `set -e` matters: if a migration fails the container must die and the
# healthcheck must never pass. A half-migrated database serving traffic is
# worse than a deployment that visibly failed.
set -e

echo "[entrypoint] waiting for the database..."
python - <<'PY'
import os, sys, time
import psycopg

url = os.environ["DATABASE_URL"]
for attempt in range(1, 61):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        print("[entrypoint] database is up")
        sys.exit(0)
    except Exception as exc:
        if attempt % 10 == 0:
            print(f"[entrypoint] still waiting ({attempt}s): {exc}")
        time.sleep(1)
print("[entrypoint] database did not become available", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] running migrations..."
python manage.py migrate --noinput

echo "[entrypoint] collecting static files..."
python manage.py collectstatic --noinput --clear

# The system check fails the boot on any API view that is not RBAC-mapped.
# Running it here means a route someone forgot to secure stops the deploy
# rather than reaching production unguarded.
echo "[entrypoint] running system checks..."
python manage.py check --deploy --fail-level ERROR

echo "[entrypoint] starting: $*"
exec "$@"
