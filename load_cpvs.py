"""
load_cpvs.py — bulk-load a CSV of CPV codes + Greek descriptions into the DB.

Usage
-----
    export DATABASE_URL="postgresql://postgres:pw@localhost:5432/procurement"
    python3 load_cpvs.py path/to/cpvs.csv

Expectations on the CSV
-----------------------
* Has a header row (any column names; we use position, not names).
* Two columns: code, description.
* Codes are full 8-digit-with-checksum (e.g. "33100000-1"). Codes without
  the dash are accepted too and normalised.
* Encoding: UTF-8 preferred. Falls back to cp1253 / iso-8859-7 (common for
  legacy Greek files) if UTF-8 decode fails.

Behaviour
---------
* INSERT ... ON CONFLICT (cpv_code) DO UPDATE — re-running the script with an
  updated file refreshes descriptions without duplicating rows.
* Reports how many rows were inserted vs updated vs skipped.
* Skips rows where the code doesn't look like a CPV (8 digits + optional -d).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys


CPV_RE = re.compile(r"^\s*(\d{8})(?:-(\d))?\s*$")


def normalise_code(raw: str) -> str | None:
    """Accept '33100000', '33100000-1', or padded variants; return canonical
    'XXXXXXXX-Y'. If no checksum is given, default to '-0' (acceptable for
    lookups since we always match by the full code; the catalog you load
    presumably has the right checksum)."""
    if not raw:
        return None
    m = CPV_RE.match(raw)
    if not m:
        return None
    code, check = m.group(1), m.group(2) or "0"
    return f"{code}-{check}"


def detect_delimiter(path: str) -> str:
    """Sniff the delimiter from the first line.

    Standard csv.Sniffer is unreliable on small samples and can guess wrong
    on Greek text, so we just count common candidates on the header. ';' is
    common in European CSVs (Excel on locales where ',' is the decimal mark).
    """
    fh = open_csv(path)
    try:
        first = fh.readline()
    finally:
        fh.close()
    # Pick the candidate with the highest count, with ';' winning ties over
    # ',' since this codebase sees more European data than US.
    counts = {sep: first.count(sep) for sep in (";", "\t", ",")}
    best = max(counts, key=lambda s: (counts[s], s == ";"))
    return best if counts[best] > 0 else ","


def open_csv(path: str):
    """Try UTF-8 first, then a couple of common Greek legacy encodings."""
    for enc in ("utf-8-sig", "utf-8", "cp1253", "iso-8859-7"):
        try:
            return open(path, newline="", encoding=enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"could not decode {path} — tried utf-8, cp1253, iso-8859-7")


def main():
    ap = argparse.ArgumentParser(description="Load CPV codes into proc.cpv_code.")
    ap.add_argument("csv_path", help="path to the CPV CSV file")
    ap.add_argument("--no-header", action="store_true",
                    help="file has no header row")
    ap.add_argument("--delimiter",
                    help="column separator (default: auto-detect from ; \\t ,)")
    args = ap.parse_args()

    if not os.path.exists(args.csv_path):
        sys.exit(f"file not found: {args.csv_path}")

    delimiter = args.delimiter or detect_delimiter(args.csv_path)
    print(f"using delimiter: {delimiter!r}")

    # Import here so the script can show --help without the driver installed.
    try:
        import psycopg                 # psycopg 3
        Connection = psycopg.connect
    except ImportError:
        try:
            import psycopg2 as psycopg # type: ignore
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
        # tolerate extra columns: take first two
        raw_code = row[0] if len(row) > 0 else ""
        desc = (row[1] if len(row) > 1 else "").strip()
        code = normalise_code(raw_code)
        if not code:
            skipped += 1
            if len(bad_examples) < 5:
                bad_examples.append(raw_code)
            continue
        rows.append((code, desc))
    fh.close()

    if not rows:
        sys.exit("no usable rows found in the CSV")

    print(f"parsed {len(rows)} valid CPV rows (skipped {skipped})")
    if bad_examples:
        print(f"  examples of skipped lines: {bad_examples}")

    # We need per-row insert-or-update counts; one statement, RETURNING xmax=0
    # is the idiomatic trick (xmax=0 ⇒ this was an INSERT, not UPDATE).
    conn = Connection(dsn)
    try:
        with conn.cursor() as cur:
            for code, desc in rows:
                cur.execute(
                    """INSERT INTO proc.cpv_code (cpv_code, description)
                       VALUES (%s, %s)
                       ON CONFLICT (cpv_code) DO UPDATE
                         SET description = EXCLUDED.description
                       RETURNING (xmax = 0) AS was_inserted""",
                    (code, desc),
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
    print(f"total rows in proc.cpv_code can be checked with: "
          f"SELECT count(*) FROM proc.cpv_code;")


if __name__ == "__main__":
    main()
