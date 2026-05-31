"""
db.py — minimal PostgreSQL layer for the KHMDHS ingester, plus a CLI runner.

It exposes exactly the three methods khmdhs_ingest.Repository expects:
    db.execute(sql, params)            -> None
    db.execute_returning(sql, params)  -> the first column of the first row
    db.commit()                        -> None
(placeholders are psycopg2-style "%s", which is what the ingester already uses.)

Connection settings come from a DATABASE_URL env var, e.g.
    export DATABASE_URL="postgresql://user:pass@localhost:5432/procurement"
or from the individual PG* libpq vars (PGHOST, PGUSER, ...). Nothing is
hard-coded and no password is stored by this script.

Usage
-----
    pip install "psycopg[binary]"          # psycopg 3   (preferred)
        # or:  pip install psycopg2-binary  # psycopg 2  (also supported)

    # 1) create the schema (runs schema.sql once; safe to re-run if you DROP first)
    python3 db.py init-schema

    # 2) backfill a date range, all five act types, in <=180-day windows
    python3 db.py backfill --start 2023-01-01 --end 2024-12-31

    # 3) sanity counts
    python3 db.py stats

The runner reuses khmdhs_ingest.py for all the API + mapping logic; this file
only owns the database connection and the command-line surface.
"""

from __future__ import annotations
import argparse
import datetime as dt
import os
import sys

# --- driver shim: prefer psycopg3, fall back to psycopg2 -------------------- #
_DRIVER = None
try:
    import psycopg                      # psycopg 3
    _DRIVER = "psycopg3"
except ImportError:
    try:
        import psycopg2                 # psycopg 2
        import psycopg2.extras
        _DRIVER = "psycopg2"
    except ImportError:
        _DRIVER = None


class Database:
    """Thin wrapper giving the ingester its execute/execute_returning/commit API.

    A single connection + single cursor is intentional: the ingester is
    single-threaded and commits once per 180-day window, which keeps memory flat
    and makes a failed window easy to retry without half-applied state.
    """

    def __init__(self, dsn: str | None = None, autocommit: bool = False):
        if _DRIVER is None:
            sys.exit("No Postgres driver. Install one: pip install 'psycopg[binary]'")
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        # If no DSN, psycopg reads standard PG* env vars (PGHOST, PGDATABASE...).
        if _DRIVER == "psycopg3":
            self.conn = psycopg.connect(self.dsn) if self.dsn else psycopg.connect()
        else:
            self.conn = psycopg2.connect(self.dsn) if self.dsn else psycopg2.connect()
        self.conn.autocommit = autocommit
        self.cur = self.conn.cursor()

    # ---- the three methods Repository depends on --------------------------- #
    @staticmethod
    def _coerce_params(params):
        """Defensive net: psycopg cannot bind a bare dict to %s. The mapper is
        supposed to hand us scalars (or a Json wrapper for jsonb columns), but
        live KHMDHS data occasionally surfaces a {key,value} object in a field
        we didn't anticipate. Rather than crash a whole 180-day window, reduce
        any stray plain dict to its 'value' (falling back to 'key'). Json
        wrapper objects from the driver are left untouched.
        """
        def fix(v):
            if isinstance(v, dict):
                # leave driver Json wrappers / non {key,value} dicts to psycopg
                if "value" in v or "key" in v:
                    return v.get("value", v.get("key"))
            return v
        if isinstance(params, dict):
            return {k: fix(v) for k, v in params.items()}
        if isinstance(params, (list, tuple)):
            return type(params)(fix(v) for v in params)
        return params

    def execute(self, sql: str, params=()):
        self.cur.execute(sql, self._coerce_params(params))

    def execute_returning(self, sql: str, params=()):
        """Run an INSERT ... RETURNING and give back the first column."""
        self.cur.execute(sql, self._coerce_params(params))
        row = self.cur.fetchone()
        return row[0] if row else None

    def commit(self):
        self.conn.commit()

    # ---- helpers used by the CLI (not by the ingester) --------------------- #
    def rollback(self):
        self.conn.rollback()

    def query(self, sql: str, params=()):
        self.cur.execute(sql, params)
        return self.cur.fetchall()

    def close(self):
        try:
            self.cur.close()
        finally:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        self.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))


def cmd_init_schema(args):
    schema_path = args.file or os.path.join(HERE, "schema.sql")
    if not os.path.exists(schema_path):
        sys.exit(f"schema file not found: {schema_path}")
    sql = open(schema_path, encoding="utf-8").read()
    with Database(autocommit=True) as db:   # DDL: commit each statement as it runs
        # psycopg can execute a multi-statement script in one call.
        db.cur.execute(sql)
    print(f"schema applied from {schema_path}")


def cmd_backfill(args):
    # Import here so `init-schema` works even before deps for the API exist.
    import khmdhs_ingest as ingest

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    types = args.types or ["request", "notice", "auction", "contract", "payment"]

    with Database() as db:
        client = ingest.KhmdhsClient()
        repo = ingest.Repository(db)
        totals = {"windows": 0, "done": 0, "skipped": 0, "errored": 0}
        for act_type in types:
            print(f"\n=== backfilling {act_type}: {start} .. {end}"
                  f"{' (resume)' if args.resume else ''} ===")
            s = ingest.ingest_type(client, repo, act_type, start, end,
                                    resume=args.resume)
            for k in totals: totals[k] += s[k]
    print(f"\nbackfill complete. windows={totals['windows']} "
          f"done={totals['done']} skipped={totals['skipped']} "
          f"errored={totals['errored']}")
    if totals["errored"]:
        print("  (errored windows are recorded with status='error' in proc.ingest_window;"
              " re-run with --resume to retry them, or inspect last_error column.)")


def cmd_stats(args):
    with Database() as db:
        rows = db.query("""
            SELECT type, count(*) FROM proc.procurement_act GROUP BY type ORDER BY type
        """)
        print("procurement_act by type:")
        for t, c in rows:
            print(f"  {t:9s} {c:>12,}")
        for tbl in ("authority", "economic_operator", "act_link",
                    "act_object_detail", "act_operator"):
            (n,) = db.query(f"SELECT count(*) FROM proc.{tbl}")[0]
            print(f"  {tbl:18s} {n:>12,}")


def cmd_progress(args):
    with Database() as db:
        if args.errors_only:
            sql = """SELECT act_type, date_from, date_to, status, last_error
                     FROM proc.ingest_window
                     WHERE status='error' """
            params = []
            if args.type:
                sql += " AND act_type=%s"
                params.append(args.type)
            sql += " ORDER BY act_type, date_from"
            rows = db.query(sql, tuple(params))
            if not rows:
                print("no errored windows.")
                return
            for t, df, dt_, _s, err in rows:
                print(f"  {t:9s} {df} .. {dt_}  ERROR: {err}")
            return

        # summary by (type, status)
        sql = """SELECT act_type, status, count(*), min(date_from), max(date_to)
                 FROM proc.ingest_window """
        params = []
        if args.type:
            sql += " WHERE act_type=%s"
            params.append(args.type)
        sql += " GROUP BY act_type, status ORDER BY act_type, status"
        rows = db.query(sql, tuple(params))
        if not rows:
            print("no ingest_window rows yet — run a backfill first.")
            return
        cur_type = None
        for t, status, n, mn, mx in rows:
            if t != cur_type:
                print(f"\n[{t}]")
                cur_type = t
            print(f"  {status:8s} {n:>5}   ({mn} .. {mx})")


def main():
    ap = argparse.ArgumentParser(description="KHMDHS database bootstrap & runner.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-schema", help="apply schema.sql to the database")
    p_init.add_argument("--file", help="path to schema.sql (default: alongside db.py)")
    p_init.set_defaults(func=cmd_init_schema)

    p_bf = sub.add_parser("backfill", help="harvest a date range into the database")
    p_bf.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_bf.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p_bf.add_argument("--types", nargs="+",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="subset of act types (default: all five)")
    p_bf.add_argument("--resume", action="store_true",
                      help="skip windows already marked 'done'; retry running/error/pending")
    p_bf.set_defaults(func=cmd_backfill)

    p_st = sub.add_parser("stats", help="print row counts")
    p_st.set_defaults(func=cmd_stats)

    p_pr = sub.add_parser("progress",
                          help="show backfill window status (pending/running/done/error)")
    p_pr.add_argument("--type",
                      choices=["request", "notice", "auction", "contract", "payment"],
                      help="filter by act type")
    p_pr.add_argument("--errors-only", action="store_true",
                      help="only show windows with status='error', with last_error")
    p_pr.set_defaults(func=cmd_progress)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
