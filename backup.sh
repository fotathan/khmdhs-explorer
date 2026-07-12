#!/usr/bin/env bash
# ---------------------------------------------------------------------------- #
# backup.sh — timestamped, compressed pg_dump of the KHMDHS application data
# (the `proc` schema) into ./backups/. See BACKUP_RUNBOOK.md for the full drill,
# restore steps, and retention/upgrade guidance.
#
# Every backup is immediately (1) integrity-checked with `pg_restore --list` so a
# truncated/corrupt archive fails LOUDLY at creation, not at 3am during a
# restore, and (2) fingerprinted with a SHA-256 sidecar so tampering or bit-rot
# is detectable later. Optionally GPG-encrypted at rest (dumps hold PII).
#
# Usage:
#   ./backup.sh "postgresql://USER:PW@HOST:5432/postgres"   # explicit URL
#   DATABASE_URL=... ./backup.sh                            # from the environment
#   ./backup.sh --verify backups/khmdhs-proc-<UTC>.dump     # re-check an archive
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
#   * Encryption (opt-in): set BACKUP_GPG_RECIPIENT=<key-id/email> for public-key
#     encryption, or BACKUP_GPG_PASSPHRASE=<secret> for symmetric. When set, the
#     plaintext .dump is replaced by an encrypted .dump.gpg (+ its own .sha256).
# ---------------------------------------------------------------------------- #
set -euo pipefail

PG_DUMP="${PG_DUMP:-pg_dump}"
PG_RESTORE="${PG_RESTORE:-pg_restore}"
SCHEMA="${BACKUP_SCHEMA:-proc}"
OUT_DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-7}"

# SHA-256 helper — prints just the hex digest (portable across macOS/Linux).
_hash() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else echo "!! no shasum/sha256sum available for checksums" >&2; return 1; fi
}

# Write "<hash>  <basename>" sidecar next to a file.
_write_sum() { printf '%s  %s\n' "$(_hash "$1")" "$(basename "$1")" > "$1.sha256"; }

# --- verify mode: re-check an existing archive (checksum + structure) --------- #
if [ "${1:-}" = "--verify" ]; then
  FILE="${2:-}"
  [ -n "$FILE" ] && [ -f "$FILE" ] || { echo "usage: ./backup.sh --verify <file>" >&2; exit 2; }
  rc=0
  if [ -f "$FILE.sha256" ]; then
    want="$(awk '{print $1}' "$FILE.sha256")"
    got="$(_hash "$FILE")"
    if [ "$want" = "$got" ]; then echo "✓ checksum OK  ($got)"
    else echo "✗ CHECKSUM MISMATCH — want $want got $got" >&2; rc=1; fi
  else
    echo "… no .sha256 sidecar; skipping checksum"
  fi
  case "$FILE" in
    *.gpg) echo "… encrypted archive: decrypt first to run pg_restore --list" ;;
    *)     if "$PG_RESTORE" --list "$FILE" >/dev/null 2>&1; then
             echo "✓ archive structure OK (pg_restore --list readable)"
           else echo "✗ ARCHIVE UNREADABLE by pg_restore --list" >&2; rc=1; fi ;;
  esac
  [ "$rc" -eq 0 ] && echo "✓ verify passed: $FILE" || echo "✗ verify FAILED: $FILE" >&2
  exit "$rc"
fi

# --- backup mode -------------------------------------------------------------- #
URL="${1:-${DATABASE_URL:-}}"
if [ -z "$URL" ]; then
  echo "usage: ./backup.sh <DATABASE_URL>   (or export DATABASE_URL)" >&2
  echo "       ./backup.sh --verify <file>" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/khmdhs-${SCHEMA}-${TS}.dump"

echo "→ dumping schema '$SCHEMA' with $("$PG_DUMP" --version)"
if ! "$PG_DUMP" "$URL" \
  --schema="$SCHEMA" \
  --format=custom \
  --compress=9 \
  --file="$OUT"; then
  echo "✗ pg_dump failed — removing partial $OUT" >&2
  rm -f "$OUT"
  exit 1
fi

# Integrity gate: a well-formed custom archive lists its TOC. A truncated or
# corrupt dump fails here — delete it and fail, so we never keep a dud.
if ! "$PG_RESTORE" --list "$OUT" >/dev/null 2>&1; then
  echo "✗ dump failed integrity check (pg_restore --list) — removing $OUT" >&2
  rm -f "$OUT"
  exit 1
fi
SZ="$(du -h "$OUT" | cut -f1)"
echo "✓ backup written + integrity-checked: $OUT ($SZ)"

# Fingerprint the artifact.
_write_sum "$OUT"
echo "✓ checksum: $(cat "$OUT.sha256")"

# Optional at-rest encryption. Replaces the plaintext dump with a .gpg.
if [ -n "${BACKUP_GPG_RECIPIENT:-}" ] || [ -n "${BACKUP_GPG_PASSPHRASE:-}" ]; then
  command -v gpg >/dev/null 2>&1 || { echo "✗ gpg not found but encryption requested" >&2; exit 1; }
  ENC="$OUT.gpg"
  if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
    gpg --batch --yes --recipient "$BACKUP_GPG_RECIPIENT" --output "$ENC" --encrypt "$OUT"
  else
    printf '%s' "$BACKUP_GPG_PASSPHRASE" | \
      gpg --batch --yes --passphrase-fd 0 --symmetric --cipher-algo AES256 --output "$ENC" "$OUT"
  fi
  _write_sum "$ENC"
  rm -f "$OUT" "$OUT.sha256"       # drop the plaintext — keep only the encrypted artifact
  echo "✓ encrypted: $ENC (plaintext removed)"
  echo "✓ checksum: $(cat "$ENC.sha256")"
fi

# Retention: keep the newest $KEEP archives for this schema (plaintext OR .gpg),
# pruning each one's .sha256 sidecar with it.
if [ "$KEEP" -gt 0 ]; then
  ls -1t "$OUT_DIR"/khmdhs-"${SCHEMA}"-*.dump "$OUT_DIR"/khmdhs-"${SCHEMA}"-*.dump.gpg 2>/dev/null \
    | tail -n +$((KEEP + 1)) | while read -r old; do
      rm -f "$old" "$old.sha256" && echo "  pruned old backup: $old"
    done
fi
echo "✓ retention: newest $KEEP kept in $OUT_DIR/"
