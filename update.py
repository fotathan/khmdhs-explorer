"""
update.py — one command for the daily routine:
  1) catch up the LOCAL database with new records since the last run, then
  2) push those act types up to Supabase (REMOTE),
  3) refresh the analytics materialized views on both.

This wraps the pieces you already have (db.py catchup + copy_to_supabase.py)
so "get today's new data everywhere" is a single command instead of several
steps with environment juggling.

Environment
-----------
    export LOCAL="postgresql://postgres:pw@localhost:5433/procurement"
    export REMOTE="postgresql://postgres.xxxx:...pooler.supabase.com:6543/postgres"

Usage
-----
    # full daily update (catch up local, push to Supabase, refresh analytics)
    python3 update.py --types notice contract payment

    # local only (skip the Supabase push) — e.g. offline, or Supabase down
    python3 update.py --types notice contract payment --local-only

    # tune the late-record overlap (default 7 days)
    python3 update.py --types notice contract --overlap-days 14

Notes
-----
* Ingestion runs against LOCAL. The push reuses copy_to_supabase.py, which is
  idempotent (skips rows already present), so re-runs are safe.
* Analytics views are refreshed at the end so the dashboard isn't stale.
* This keeps the laptop-driven, watch-it-run model: you run it, you see the
  output. It does not schedule anything.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable  # the venv's python, since you run this inside the venv


def run(cmd, env=None, label=""):
    """Run a subprocess, streaming output; raise on failure."""
    if label:
        print(f"\n{'='*64}\n  {label}\n{'='*64}")
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        sys.exit(f"\nSTOPPED: step failed ({label or cmd}). "
                 f"Nothing further was run.")


def refresh_analytics(dsn: str, env_name: str):
    """Refresh the analytics materialized views on the given database."""
    try:
        import psycopg
    except ImportError:
        sys.exit("Run inside your venv: source khmdhs-env/bin/activate")
    print(f"\n  refreshing analytics on {env_name} …")
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT proc.refresh_analytics();")
            conn.commit()
        print("    done.")
    except Exception as e:
        # Non-fatal: the data is in; analytics just shows last refresh.
        print(f"    WARNING: could not refresh analytics on {env_name}: {e!r}")
        print("    (data is loaded; run SELECT proc.refresh_analytics(); later.)")


def main():
    ap = argparse.ArgumentParser(description="Daily update: catch up local, "
                                             "push to Supabase, refresh analytics.")
    ap.add_argument("--types", nargs="+", required=True,
                    choices=["request", "notice", "auction", "contract", "payment"],
                    help="act types to update")
    ap.add_argument("--overlap-days", type=int, default=7,
                    help="re-fetch this many days before the watermark (default 7)")
    ap.add_argument("--start", help="YYYY-MM-DD; only for types never backfilled")
    ap.add_argument("--local-only", action="store_true",
                    help="skip the Supabase push (and remote refresh)")
    args = ap.parse_args()

    local = os.environ.get("LOCAL") or os.environ.get("DATABASE_URL")
    remote = os.environ.get("REMOTE")
    if not local:
        sys.exit("Set LOCAL (or DATABASE_URL) to your local database.")
    if not args.local_only and not remote:
        sys.exit("Set REMOTE to your Supabase URL, or pass --local-only.")

    # ---- Step 1: catch up the LOCAL database ----
    catchup_env = dict(os.environ, DATABASE_URL=local)
    cmd = [PY, os.path.join(HERE, "db.py"), "catchup",
           "--types", *args.types, "--overlap-days", str(args.overlap_days)]
    if args.start:
        cmd += ["--start", args.start]
    run(cmd, env=catchup_env, label="STEP 1 · catch up LOCAL database")

    # ---- Step 1b: refresh analytics locally ----
    refresh_analytics(local, "LOCAL")

    if args.local_only:
        print("\n--local-only: skipping Supabase push. Done.")
        return

    # ---- Step 2: push the same types up to Supabase ----
    push_env = dict(os.environ, LOCAL=local, REMOTE=remote)
    cmd = [PY, os.path.join(HERE, "copy_to_supabase.py"), "--types", *args.types]
    run(cmd, env=push_env, label="STEP 2 · push to Supabase")

    # ---- Step 2b: refresh analytics on Supabase ----
    refresh_analytics(remote, "REMOTE (Supabase)")

    print("\nAll done. Local caught up, Supabase updated, analytics refreshed.")


if __name__ == "__main__":
    main()
