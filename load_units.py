"""
load_units.py — bulk-load a CSV of UNECE Rec 20 unit codes + Greek names
into proc.unit_code.

Usage
-----
    export DATABASE_URL="postgresql://postgres:pw@localhost:5432/procurement"
    python3 load_units.py path/to/UNECE_Rec20_EL.csv

Expectations on the CSV
-----------------------
* Has a header row (any column names; we use position, not names).
* Two columns: code, name.
* Codes are short alphanumeric strings (e.g. "LTR", "MON", "H87", "3I").
* Encoding: UTF-8 (with or without BOM); falls back to cp1253 / iso-8859-7.
* Delimiter is auto-detected from ';', '\t', ',' (same logic as load_cpvs.py;
  '`;`' wins on European CSV exports, which is what your file is).

Behaviour
---------
* INSERT ... ON CONFLICT (code) DO UPDATE — re-runs refresh names without
  duplicating rows.
* Reports inserted/updated/skipped counts.
* Skips rows where code is empty.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys


def detect_delimiter(path: str) -> str:
    fh = open_csv(path)
    try:
        first = fh.readline()
    finally:
        fh.close()
    counts = {sep: first.count(sep) for sep in (";", "\t", ",")}
    best = max(counts, key=lambda s: (counts[s], s == ";"))
    return best if counts[best] > 0 else ","


def open_csv(path: str):
    for enc in ("utf-8-sig", "utf-8", "cp1253", "iso-8859-7"):
        try:
            return open(path, newline="", encoding=enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"could not decode {path} — tried utf-8, cp1253, iso-8859-7")


def normalise_code(raw: str) -> str | None:
    """UNECE codes are short alphanumeric strings. Whitelist conservatively:
    1–4 chars, [A-Za-z0-9]. This rejects accidental blank/garbled rows."""
    if not raw:
        return None
    code = raw.strip()
    if not (1 <= len(code) <= 4):
        return None
    if not all(c.isalnum() for c in code):
        return None
    return code.upper()


def main():
    ap = argparse.ArgumentParser(description="Load UNECE unit codes into proc.unit_code.")
    ap.add_argument("csv_path", help="path to the UNECE units CSV file")
    ap.add_argument("--no-header", action="store_true",
                    help="file has no header row")
    ap.add_argument("--delimiter",
                    help="column separator (default: auto-detect from ; \\t ,)")
    args = ap.parse_args()

    if not os.path.exists(args.csv_path):
        sys.exit(f"file not found: {args.csv_path}")

    delimiter = args.delimiter or detect_delimiter(args.csv_path)
    print(f"using delimiter: {delimiter!r}")

    try:
        import psycopg                  # psycopg 3
        Connection = psycopg.connect
    except ImportError:
        try:
            import psycopg2 as psycopg  # type: ignore
            Connection = psycopg.connect
        except ImportError:
            sys.exit("install a Postgres driver: pip install 'psycopg[binary]'")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("set DATABASE_URL first, e.g. "
                 "postgresql://postgres:pw@localhost:5432/procurement")

    fh = open_csv(args.csv_path)
    reader = csv.reader(fh, delimiter=delimiter)
    if not args.no_header:
        try:
            next(reader)
        except StopIteration:
            sys.exit("CSV is empty")

    inserted = updated = skipped = 0
    bad_examples: list[str] = []
    rows: list[tuple[str, str]] = []
    for row in reader:
        if not row:
            continue
        raw_code = row[0] if len(row) > 0 else ""
        name = (row[1] if len(row) > 1 else "").strip()
        code = normalise_code(raw_code)
        if not code:
            skipped += 1
            if len(bad_examples) < 5:
                bad_examples.append(raw_code)
            continue
        rows.append((code, name))
    fh.close()

    if not rows:
        sys.exit("no usable rows found in the CSV")
    print(f"parsed {len(rows)} valid unit rows (skipped {skipped})")
    if bad_examples:
        print(f"  examples of skipped lines: {bad_examples}")

    conn = Connection(dsn)
    try:
        with conn.cursor() as cur:
            for code, name in rows:
                cur.execute(
                    """INSERT INTO proc.unit_code (code, name)
                       VALUES (%s, %s)
                       ON CONFLICT (code) DO UPDATE
                         SET name = EXCLUDED.name
                       RETURNING (xmax = 0) AS was_inserted""",
                    (code, name),
                )
                row = cur.fetchone()
                if row and row[0]:
                    inserted += 1
                else:
                    updated += 1
        conn.commit()
    finally:
        conn.close()

    print(f"inserted: {inserted}   updated: {updated}   skipped: {skipped}")


if __name__ == "__main__":
    main()
