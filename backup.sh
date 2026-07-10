#!/usr/bin/env bash
# ---------------------------------------------------------------------------- #
# backup.sh — timestamped, compressed pg_dump of the KHMDHS application data
# (the `proc` schema) into ./backups/. See BACKUP_RUNBOOK.md for the full drill,
# restore steps, and retention/upgrade guidance.
#
# Usage:
#   ./backup.sh "postgresql://USER:PW@HOST:5432/postgres"   # explicit URL
#   DATABASE_URL=... ./backup.sh                            # from the environment
#
# Notes:
#   * Use a SESSION connection (Supabase: the session pooler / direct, port 5432),
#     NOT the transaction pooler on 6543 — pg_dump needs session-level features.
#   * The pg_dump MAJOR version must be >= the server's. Prod is Postgres 17, so
#     point PG_DUMP at a v17 client, e.g.:
#       PG_DUMP=/usr/local/opt/postgresql@17/bin/pg_dump ./backup.sh ...
#   * Custom format (-Fc): compressed and restorable selectively with pg_restore.
#     Ownership/ACLs are kept in the archive; strip them at restore time with
#     pg_restore --no-owner --no-privileges when targeting a different cluster.
# ---------------------------------------------------------------------------- #
set -euo pipefail

PG_DUMP="${PG_DUMP:-pg_dump}"
SCHEMA="${BACKUP_SCHEMA:-proc}"
OUT_DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-7}"
URL="${1:-${DATABASE_URL:-}}"

if [ -z "$URL" ]; then
  echo "usage: ./backup.sh <DATABASE_URL>   (or export DATABASE_URL)" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/khmdhs-${SCHEMA}-${TS}.dump"

echo "→ dumping schema '$SCHEMA' with $("$PG_DUMP" --version)"
"$PG_DUMP" "$URL" \
  --schema="$SCHEMA" \
  --format=custom \
  --compress=9 \
  --file="$OUT"

SZ="$(du -h "$OUT" | cut -f1)"
echo "✓ backup written: $OUT ($SZ)"

# Retention: keep the newest $KEEP dumps for this schema, delete older ones.
if [ "$KEEP" -gt 0 ]; then
  ls -1t "$OUT_DIR"/khmdhs-"${SCHEMA}"-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old" && echo "  pruned old backup: $old"
  done
fi
echo "✓ retention: newest $KEEP kept in $OUT_DIR/"
