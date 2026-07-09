#!/usr/bin/env python3
"""Lightweight, dependency-free schema migration tracker.

This repo evolved through many hand-applied ``*_migration.sql`` files with no
version table, so local and prod could drift silently. This tool records what
has run in ``proc.schema_migration`` and gives a repeatable apply path — without
re-running the migrations that were already applied by hand.

Workflow
--------
1. One time, per existing database (local + prod):
       DATABASE_URL=... python3 migrate.py baseline
   Records every manifest entry as already-applied (baseline=true). Runs NO SQL.
2. Going forward, author a new migration and apply it:
       python3 migrate.py new "add foo table"      # scaffolds migrations/<ts>_add_foo_table.sql
       # edit the file, then:
       DATABASE_URL=... python3 migrate.py up       # runs pending files (psql -f) + records them
3. Check drift any time:
       DATABASE_URL=... python3 migrate.py status

`up` shells out to ``psql -v ON_ERROR_STOP=1 -f`` so files run exactly as they
do by hand today (multi-statement, their own BEGIN/COMMIT, etc.). New migrations
should wrap their body in BEGIN/COMMIT so a failure leaves nothing half-applied.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    sys.exit("psycopg (v3) is required: pip install 'psycopg[binary]'")

ROOT = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(ROOT, "migrations", "manifest.txt")


def _dsn(args) -> str:
    dsn = getattr(args, "dsn", None) or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("No database: set DATABASE_URL or pass --dsn")
    return dsn


def _connect(args):
    return psycopg.connect(_dsn(args), autocommit=True,
                           prepare_threshold=None, row_factory=dict_row)


def _manifest() -> list[str]:
    if not os.path.exists(MANIFEST):
        sys.exit(f"manifest not found: {MANIFEST}")
    out = []
    with open(MANIFEST, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def _checksum(rel: str):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proc.schema_migration (
            filename   text PRIMARY KEY,
            checksum   text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now(),
            applied_by text,
            baseline   boolean NOT NULL DEFAULT false
        )""")


def _applied(conn) -> dict:
    rows = conn.execute("SELECT filename, checksum, applied_at, baseline "
                        "FROM proc.schema_migration").fetchall()
    return {r["filename"]: r for r in rows}


def cmd_init(args) -> None:
    with _connect(args) as c:
        _ensure_table(c)
    print("tracking table ready: proc.schema_migration")


def cmd_status(args) -> None:
    files = _manifest()
    with _connect(args) as c:
        _ensure_table(c)
        applied = _applied(c)
    pend = drift = 0
    for f in files:
        cs = _checksum(f)
        rec = applied.get(f)
        if rec is None:
            print(f"  PENDING  {f}"); pend += 1
        elif cs and rec["checksum"] and cs != rec["checksum"]:
            print(f"  DRIFT    {f}  (file changed since it was applied)"); drift += 1
        else:
            print(f"  ok       {f}  ({'baseline' if rec['baseline'] else 'applied'})")
    orphans = [k for k in applied if k not in set(files)]
    for k in orphans:
        print(f"  ORPHAN   {k}  (recorded but not in manifest)")
    print(f"\n{len(files)} in manifest · {pend} pending · {drift} drifted · "
          f"{len(orphans)} orphan")
    if (pend or drift) and getattr(args, "strict", False):
        sys.exit(1)


def cmd_baseline(args) -> None:
    files = _manifest()
    who = os.environ.get("USER", "baseline")
    missing = [f for f in files if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        sys.exit("manifest lists files that do not exist:\n  " + "\n  ".join(missing))
    with _connect(args) as c:
        _ensure_table(c)
        applied = _applied(c)
        todo = [f for f in files if f not in applied]
        for f in todo:
            c.execute("""INSERT INTO proc.schema_migration
                           (filename, checksum, applied_by, baseline)
                         VALUES (%s, %s, %s, true)
                         ON CONFLICT (filename) DO NOTHING""",
                      (f, _checksum(f), who))
    print(f"baseline: {len(todo)} newly recorded, "
          f"{len(files) - len(todo)} already tracked ({len(files)} total)")


def cmd_up(args) -> None:
    files = _manifest()
    with _connect(args) as c:
        _ensure_table(c)
        applied = _applied(c)
    pending = [f for f in files if f not in applied]
    if not pending:
        print("nothing to apply — up to date")
        return
    print(f"{len(pending)} pending: " + ", ".join(pending))
    if args.dry_run:
        print("(dry run — nothing applied)")
        return
    dsn = _dsn(args)
    who = os.environ.get("USER", "up")
    for f in pending:
        path = os.path.join(ROOT, f)
        print(f"\n=== applying {f} ===")
        r = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", path])
        if r.returncode != 0:
            sys.exit(f"FAILED on {f} (exit {r.returncode}); not recorded — fix and re-run")
        with _connect(args) as c:
            c.execute("""INSERT INTO proc.schema_migration
                           (filename, checksum, applied_by, baseline)
                         VALUES (%s, %s, %s, false)
                         ON CONFLICT (filename)
                         DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now()""",
                      (f, _checksum(f), who))
        print(f"recorded {f}")
    print(f"\napplied {len(pending)} migration(s)")


def cmd_new(args) -> None:
    # Timestamp prefix keeps new migrations sorted after existing ones.
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "_", args.name.strip().lower()).strip("_") or "migration"
    rel = f"migrations/{ts}_{slug}.sql"
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"-- {rel}\n-- {args.name}\n--\n-- Wrap the body so a failure leaves"
                 f" nothing half-applied. Keep it idempotent where practical.\n\n"
                 f"BEGIN;\n\n-- TODO: your DDL here\n\nCOMMIT;\n")
    with open(MANIFEST, "a", encoding="utf-8") as fh:
        fh.write(rel + "\n")
    print(f"created {rel} and appended it to migrations/manifest.txt")


def main() -> None:
    ap = argparse.ArgumentParser(description="Schema migration tracker")
    ap.add_argument("--dsn", help="DB connection string (else $DATABASE_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the tracking table")
    sp = sub.add_parser("status", help="show applied / pending / drifted")
    sp.add_argument("--strict", action="store_true", help="exit 1 if pending/drift (for CI)")
    sub.add_parser("baseline", help="record all manifest entries as applied (runs NO SQL)")
    sp = sub.add_parser("up", help="apply pending migrations (psql -f) and record them")
    sp.add_argument("--dry-run", action="store_true")
    sp = sub.add_parser("new", help="scaffold a new migration + append to the manifest")
    sp.add_argument("name")
    args = ap.parse_args()
    {"init": cmd_init, "status": cmd_status, "baseline": cmd_baseline,
     "up": cmd_up, "new": cmd_new}[args.cmd](args)


if __name__ == "__main__":
    main()
