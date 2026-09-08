#!/usr/bin/env bash
#
# Backs up the HRMS v2 database and uploaded documents.
#
# Two things, and the second is the one people forget: the encryption key.
# PAN, Aadhaar and bank details are encrypted at rest with FIELD_ENCRYPTION_KEY.
# A database backup WITHOUT that key restores rows whose sensitive columns are
# permanently unreadable. The key is not written here — it must be kept
# somewhere this script cannot reach, because a backup containing both the
# ciphertext and its key is not an encrypted backup.
#
#   ./backup.sh              # write a backup
#   ./backup.sh --verify     # write one, then prove it restores
#
set -euo pipefail

PROJECT="hrmsv2"
BACKUP_DIR="${BACKUP_DIR:-/opt/hrms-v2/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

DB_CONTAINER="$(docker compose -p "$PROJECT" ps -q db)"
if [ -z "$DB_CONTAINER" ]; then
    echo "FATAL: the $PROJECT db container is not running." >&2
    exit 1
fi

DB_USER="${POSTGRES_USER:-hrms}"
DB_NAME="${POSTGRES_DB:-hrms}"

# --- database -----------------------------------------------------------
# Custom format (-Fc): compressed, and restorable table-by-table, which is what
# you want at 3am when one table is wrong rather than the whole database.
DUMP="$BACKUP_DIR/hrms-$STAMP.dump"
echo "[backup] dumping database..."
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$DUMP"

# --- uploaded documents -------------------------------------------------
MEDIA="$BACKUP_DIR/media-$STAMP.tar.gz"
echo "[backup] archiving uploaded documents..."
docker run --rm \
    -v "${PROJECT}_hrmsv2_media:/media:ro" \
    -v "$BACKUP_DIR:/out" \
    alpine:3.20 \
    tar czf "/out/media-$STAMP.tar.gz" -C /media . 2>/dev/null || {
        echo "[backup] WARNING: media volume not found or empty" >&2
    }

# --- verify -------------------------------------------------------------
# A backup nobody has restored is a hope, not a backup. This restores into a
# throwaway database in the same container and counts the rows.
if [ "${1:-}" = "--verify" ]; then
    echo "[backup] verifying by restoring into a scratch database..."
    SCRATCH="verify_$STAMP"
    docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$SCRATCH"
    trap 'docker exec "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$SCRATCH" >/dev/null 2>&1 || true' EXIT

    docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$SCRATCH" --no-owner < "$DUMP" >/dev/null 2>&1

    EMPLOYEES=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$SCRATCH" -tAc \
        "SELECT count(*) FROM employees_employee" 2>/dev/null || echo "ERROR")
    AUDIT=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$SCRATCH" -tAc \
        "SELECT count(*) FROM audit_auditlog" 2>/dev/null || echo "ERROR")

    if [ "$EMPLOYEES" = "ERROR" ] || [ "$AUDIT" = "ERROR" ]; then
        echo "[backup] VERIFY FAILED — the dump did not restore." >&2
        exit 1
    fi
    echo "[backup] verified: $EMPLOYEES employees, $AUDIT audit rows restored."
fi

# --- retention ----------------------------------------------------------
find "$BACKUP_DIR" -name 'hrms-*.dump'    -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media-*.tar.gz' -mtime "+$KEEP_DAYS" -delete

echo "[backup] done:"
ls -lh "$BACKUP_DIR" | tail -4
echo
echo "REMINDER: FIELD_ENCRYPTION_KEY is not in this backup, by design."
echo "Without it, encrypted PAN / Aadhaar / bank columns cannot be read back."
