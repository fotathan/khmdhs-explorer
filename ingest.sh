#!/usr/bin/env bash
# =============================================================================
# ingest.sh — safe wrapper around db.py for KHMDHS backfills.
#
# Why this exists: so you never paste a connection string, never mix up which
# database you're writing to, and never start a production write by accident.
#
# ---------------------------------------------------------------------------
# ONE-TIME SETUP
# ---------------------------------------------------------------------------
#   1. Put this file in your project root (next to db.py).
#   2. Make it runnable:   chmod +x ingest.sh
#   3. Fill in PROD_DB_URL below with your Supabase DIRECT connection string
#      (port 5432, the one WITHOUT "pooler" doing transaction mode — the direct
#      one). Leave LOCAL_DB_URL as-is unless your local DB differs.
#      Better still: don't hardcode the password — see PROD_DB_URL note below.
#
# ---------------------------------------------------------------------------
# USAGE
# ---------------------------------------------------------------------------
#   ./ingest.sh local  backfill --start 2026-06-01 --end 2026-06-19 --types notice
#   ./ingest.sh local  backfill --start 2026-06-01 --types notice --fulltext
#   ./ingest.sh prod   catchup  --types notice contract
#   ./ingest.sh prod   backfill --start 2026-06-01 --fulltext
#
#   First word after the script:  local | prod    (which database)
#   Second word:                  backfill | catchup
#   Then the normal db.py flags, PLUS optionally:  --fulltext
#
# Safety:
#   * Always prints the target DB (host masked) and waits for confirmation.
#   * For prod, you must type the word PRODUCTION to proceed.
#   * Refuses to run if the chosen DB URL is empty or unreachable.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# CONFIG — fill these in once.
# ---------------------------------------------------------------------------

# Local database (adjust port/password if yours differ).
LOCAL_DB_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement"

# Production (Supabase) — DIRECT connection, port 5432.
# SAFEST: leave this empty and instead set it in your shell when needed:
#     export KHMDHS_PROD_DB_URL="postgresql://...:5432/postgres"
# The script reads that env var first; only falls back to the line below.
PROD_DB_URL="${KHMDHS_PROD_DB_URL:-}"

# ---------------------------------------------------------------------------
# Internals — you shouldn't need to edit below here.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n'  "$*"; }

die() { red "✗ $*"; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  ./ingest.sh <local|prod> <command> [db.py flags...] [--fulltext]

  KHMDHS commands : backfill | catchup | fulltext-backfill
  Diavgeia commands: diavgeia-backfill | diavgeia-catchup | diavgeia-resolve | diavgeia-project
  TED commands     : ted-backfill | ted-catchup | ted-project
                     (--types notice award contract; windows on issue date.
                      backfill auto-runs resolve + project; project surfaces
                      decisions in the web app via procurement_act)

Examples:
  ./ingest.sh local backfill          --start 2026-06-01 --end 2026-06-19 --types notice
  ./ingest.sh local backfill          --start 2026-06-01 --types notice --fulltext
  ./ingest.sh prod  catchup           --types notice contract
  ./ingest.sh local diavgeia-backfill --start 2026-06-01 --end 2026-06-19 --types notice award contract
  ./ingest.sh prod  diavgeia-catchup  --types notice award contract

Add --fulltext to also extract attachment text (KHMDHS only; ignored by Diavgeia).
The 'fulltext-backfill' command extracts text for already-imported acts that
have none yet (resumable; use --limit to do it in batches).
EOF
  exit 1
}

# ---- parse the first two positional args -----------------------------------
[ $# -ge 2 ] || usage
TARGET="$1";  shift
COMMAND="$1"; shift

case "$TARGET" in
  local) DB_URL="$LOCAL_DB_URL"; DB_LABEL="LOCAL (your Mac)";;
  prod)  DB_URL="$PROD_DB_URL";  DB_LABEL="PRODUCTION (Supabase)";;
  *) die "first argument must be 'local' or 'prod' (got '$TARGET')";;
esac

case "$COMMAND" in
  backfill|catchup|fulltext-backfill|diavgeia-backfill|diavgeia-catchup|diavgeia-resolve|diavgeia-project|ted-backfill|ted-catchup|ted-project) ;;
  *) die "second argument must be one of: backfill, catchup, fulltext-backfill, diavgeia-backfill, diavgeia-catchup, diavgeia-resolve, diavgeia-project, ted-backfill, ted-catchup, ted-project (got '$COMMAND')";;
esac

# ---- pull out our custom --fulltext flag before passing the rest to db.py ---
FULLTEXT=0
DBPY_ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--fulltext" ]; then
    FULLTEXT=1
  else
    DBPY_ARGS+=("$arg")
  fi
done

# ---- validate the chosen DB URL --------------------------------------------
[ -n "$DB_URL" ] || die "No connection string for '$TARGET'. \
For prod, run:  export KHMDHS_PROD_DB_URL=\"postgresql://...:5432/postgres\"  \
then re-run, or fill PROD_DB_URL in this script."

# Mask the credentials when displaying (show host/db, hide user:pass).
MASKED="$(printf '%s' "$DB_URL" | sed -E 's#(//)[^@]*@#\1****:****@#')"

# ---- check the database is reachable BEFORE doing anything -----------------
command -v psql >/dev/null 2>&1 || die "psql not found on PATH."
yellow "Checking connection to $DB_LABEL ..."
if ! psql "$DB_URL" -c "SELECT 1;" >/dev/null 2>&1; then
  die "Cannot connect to $DB_LABEL.
URL (masked): $MASKED
Check the connection string, network, and that the DB is up."
fi
green "✓ connected."

# ---- show the plan and confirm ---------------------------------------------
echo
bold  "About to run:"
echo  "  database : $DB_LABEL"
echo  "  url      : $MASKED"
echo  "  command  : db.py $COMMAND ${DBPY_ARGS[*]:-}"
if [ "$FULLTEXT" = "1" ]; then
  yellow "  fulltext : ON  (will fetch & parse every new act's attachment — slower)"
else
  echo  "  fulltext : off"
fi
echo

if [ "$TARGET" = "prod" ]; then
  red   "This writes to PRODUCTION. Type the word PRODUCTION to proceed."
  read -r -p "> " CONFIRM
  [ "$CONFIRM" = "PRODUCTION" ] || die "Cancelled (you typed '$CONFIRM')."
else
  read -r -p "Proceed against LOCAL? [y/N] " CONFIRM
  case "$CONFIRM" in y|Y|yes|YES) ;; *) die "Cancelled.";; esac
fi

# ---- run --------------------------------------------------------------------
export DATABASE_URL="$DB_URL"
if [ "$FULLTEXT" = "1" ]; then
  export EXTRACT_FULLTEXT=1
else
  unset EXTRACT_FULLTEXT 2>/dev/null || true
fi

echo
green "▶ starting $COMMAND against $DB_LABEL ..."
echo
# Run db.py from the project dir so its relative imports/paths work.
cd "$SCRIPT_DIR"
exec python3 db.py "$COMMAND" "${DBPY_ARGS[@]}"
