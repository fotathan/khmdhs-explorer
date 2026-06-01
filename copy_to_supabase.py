"""
copy_to_supabase.py — copy a subset of your LOCAL database up to Supabase
(or any remote Postgres), stripping the bulky raw_json column.

Why this exists
---------------
You already harvested data into your local Docker Postgres. Re-downloading it
from the KHMDHS API into Supabase would be slow. This copies what you already
have, for a chosen set of act types, directly DB→DB.

What it copies
--------------
  * Reference/parent tables IN FULL (small): authority, economic_operator,
    nuts_code, org_unit, signer  — and the merge/annotation overlay tables if
    present (entity_group, entity_member, act_annotation).
  * procurement_act rows of the chosen --types (raw_json set to NULL).
  * All child rows tied to those acts: act_object_detail, object_detail_cpv,
    act_operator, act_link, act_nuts, act_funding, act_systemic_number,
    act_additional_contract_type, act_centralized_market, act_diavgeia_link.
  * cpv_code / unit_code are NOT copied (you load those on Supabase directly).

Usage
-----
    # both URLs as env vars; LOCAL is your docker DB, REMOTE is Supabase
    export LOCAL="postgresql://postgres:pw@localhost:5433/procurement"
    export REMOTE="$SUPA"
    python3 copy_to_supabase.py --types notice contract payment

Safe to re-run: uses upsert (ON CONFLICT DO NOTHING) so existing rows are kept.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    import psycopg
except ImportError:
    sys.exit("Run inside your venv: source khmdhs-env/bin/activate")


# Parent tables copied in full (small, and children reference them).
FULL_TABLES = [
    ("proc.nuts_code", None),
    ("proc.cpv_code", None),
    ("proc.unit_code", None),
    ("proc.authority", None),
    ("proc.economic_operator", None),
    ("proc.org_unit", None),
    ("proc.signer", None),
]

# Overlay tables — copy in full if they exist (merges/annotations you made).
OVERLAY_TABLES = ["proc.entity_group", "proc.entity_member", "proc.act_annotation"]

# Child tables keyed by adam — filtered to the selected acts.
CHILD_TABLES_BY_ADAM = [
    "proc.act_object_detail",
    "proc.act_operator",
    "proc.act_nuts",
    "proc.act_funding",
    "proc.act_systemic_number",
    "proc.act_additional_contract_type",
    "proc.act_centralized_market",
    "proc.act_diavgeia_link",
]


def colnames(cur, table):
    schema, name = table.split(".")
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema=%s AND table_name=%s
                   ORDER BY ordinal_position""", (schema, name))
    return [r[0] for r in cur.fetchall()]


def table_exists(cur, table):
    schema, name = table.split(".")
    cur.execute("""SELECT 1 FROM information_schema.tables
                   WHERE table_schema=%s AND table_name=%s""", (schema, name))
    return cur.fetchone() is not None


def copy_rows(lcur, rcur, table, where="", args=(), null_cols=()):
    """Read rows from local (lcur), insert into remote (rcur)."""
    if not table_exists(lcur, table):
        return 0
    if not table_exists(rcur, table):
        print(f"  {table:32s}   (skipped — not on remote; run its migration)")
        return 0
    cols = colnames(lcur, table)
    collist = ", ".join(cols)
    lcur.execute(f"SELECT {collist} FROM {table} {where}", args)
    rows = lcur.fetchall()
    if not rows:
        return 0
    # NULL out requested columns (e.g. raw_json).
    null_idx = {cols.index(c) for c in null_cols if c in cols}
    placeholders = ", ".join(["%s"] * len(cols))
    insert = (f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
              f"ON CONFLICT DO NOTHING")
    batch = []
    for r in rows:
        r = list(r)
        for i in null_idx:
            r[i] = None
        batch.append(r)
    # insert in chunks
    n = 0
    for i in range(0, len(batch), 500):
        chunk = batch[i:i+500]
        rcur.executemany(insert, chunk)
        n += len(chunk)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", nargs="+", required=True,
                    help="act types to copy, e.g. notice contract payment")
    ap.add_argument("--since", help="only acts with submission_date >= this "
                    "(YYYY-MM-DD). Use to copy just a recent slice.")
    ap.add_argument("--until", help="only acts with submission_date <= this "
                    "(YYYY-MM-DD, inclusive). Default: no upper bound.")
    ap.add_argument("--keep-raw-json", action="store_true",
                    help="copy raw_json too (default: strip it)")
    args = ap.parse_args()

    local = os.environ.get("LOCAL")
    remote = os.environ.get("REMOTE")
    if not local or not remote:
        sys.exit("Set LOCAL and REMOTE env vars first (see top of this file).")

    null_cols = () if args.keep_raw_json else ("raw_json",)

    # Build the act-selection predicate once, reused for the acts query and
    # every child-table subquery so they stay consistent. Optional date range
    # narrows by submission_date (publication date).
    act_pred = "type = ANY(%s)"
    act_args: list = [args.types]
    if args.since:
        act_pred += " AND submission_date >= %s"
        act_args.append(args.since)
    if args.until:
        act_pred += " AND submission_date < (%s::date + interval '1 day')"
        act_args.append(args.until)
    act_args = tuple(act_args)
    # For child subqueries the same predicate is embedded; psycopg needs the
    # args repeated in order each place it appears.
    span = ""
    if args.since or args.until:
        span = f"  (since={args.since or '—'}, until={args.until or 'today'})"

    lconn = psycopg.connect(local)
    rconn = psycopg.connect(remote)
    lconn.autocommit = False
    rconn.autocommit = False
    try:
        lcur = lconn.cursor()
        rcur = rconn.cursor()

        print(f"Copying act types: {', '.join(args.types)}{span}")
        print("Parent/reference tables (full):")
        for table, _ in FULL_TABLES:
            n = copy_rows(lcur, rcur, table)
            print(f"  {table:32s} {n:>8} rows")
        rconn.commit()

        print("Overlay tables (merges/annotations, if present):")
        for table in OVERLAY_TABLES:
            n = copy_rows(lcur, rcur, table)
            print(f"  {table:32s} {n:>8} rows")
        rconn.commit()

        # The acts themselves.
        print("Acts:")
        n = copy_rows(lcur, rcur, "proc.procurement_act",
                      where=f"WHERE {act_pred}", args=act_args,
                      null_cols=null_cols)
        print(f"  {'proc.procurement_act':32s} {n:>8} rows"
              f"{' (raw_json stripped)' if not args.keep_raw_json else ''}")
        rconn.commit()

        # Child tables tied to those acts (filter by adam IN selected acts).
        print("Child tables (tied to those acts):")
        adam_filter = (f"WHERE adam IN (SELECT adam FROM proc.procurement_act "
                       f"WHERE {act_pred})")
        for table in CHILD_TABLES_BY_ADAM:
            n = copy_rows(lcur, rcur, table, where=adam_filter, args=act_args)
            print(f"  {table:32s} {n:>8} rows")
        rconn.commit()

        # object_detail_cpv references act_object_detail.id AND cpv_code. Only
        # copy rows whose cpv_code exists remotely (avoids FK abort if a code
        # is missing from the catalog).
        if table_exists(lcur, "proc.object_detail_cpv"):
            rcur.execute("SELECT cpv_code FROM proc.cpv_code")
            remote_cpvs = {r[0] for r in rcur.fetchall()}
            cols = colnames(lcur, "proc.object_detail_cpv")
            collist = ", ".join(cols)
            lcur.execute(f"""
                SELECT {collist} FROM proc.object_detail_cpv
                WHERE object_detail_id IN (
                  SELECT id FROM proc.act_object_detail
                  WHERE adam IN (SELECT adam FROM proc.procurement_act
                                 WHERE {act_pred}))
            """, act_args)
            cpv_idx = cols.index("cpv_code")
            rows = [r for r in lcur.fetchall() if r[cpv_idx] in remote_cpvs]
            if rows:
                ph = ", ".join(["%s"] * len(cols))
                ins = (f"INSERT INTO proc.object_detail_cpv ({collist}) "
                       f"VALUES ({ph}) ON CONFLICT DO NOTHING")
                for i in range(0, len(rows), 500):
                    rcur.executemany(ins, rows[i:i+500])
            print(f"  {'proc.object_detail_cpv':32s} {len(rows):>8} rows")
        rconn.commit()

        # act_link: both endpoints should ideally exist; copy links whose
        # source is among the selected acts (targets may be stubs — fine).
        if table_exists(lcur, "proc.act_link"):
            cols = colnames(lcur, "proc.act_link")
            collist = ", ".join(cols)
            lcur.execute(f"""
                SELECT {collist} FROM proc.act_link
                WHERE source_adam IN (SELECT adam FROM proc.procurement_act
                                      WHERE {act_pred})
            """, act_args)
            rows = lcur.fetchall()
            if rows:
                ph = ", ".join(["%s"] * len(cols))
                ins = (f"INSERT INTO proc.act_link ({collist}) "
                       f"VALUES ({ph}) ON CONFLICT DO NOTHING")
                for i in range(0, len(rows), 500):
                    rcur.executemany(ins, rows[i:i+500])
            print(f"  {'proc.act_link':32s} {len(rows):>8} rows")
        rconn.commit()

        print("\nDone. Verify on Supabase with:")
        print('  psql "$REMOTE" -c "SELECT type, count(*) FROM proc.procurement_act GROUP BY type;"')
    except Exception as e:
        rconn.rollback()
        sys.exit(f"\nERROR (nothing committed for the failing step): {e!r}")
    finally:
        lconn.close()
        rconn.close()


if __name__ == "__main__":
    main()
