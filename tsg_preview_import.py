#!/usr/bin/env python3
"""tsg_preview_import.py — put a tsg_probe sample into the LOCAL database, so the
Tender Service feed can be looked at in the app without spending API requests.

The real ingester is tsg_ingest.py (db.py tsg-backfill / tsg-catchup). This
preview shares its mapping — map_record, the amount and date parsers, the
authority/CPV/act writers all live there — so what the preview shows is what
the ingester writes. What stays here is the preview's own shape:
  * Input is the JSON tsg_probe.py already saved (runs/tsg-probe-*/records.json).
    It makes no API calls and writes no proc.tsg_record rows.
  * Acts are adam='TSG:<internalID>', data_source='tsg', origin='import'.
  * Records we ALREADY hold are skipped — an eProcurement record whose
    externalId is a ΑΔΑΜ we have, a Diavgeia record whose ΑΔΑ we have.
  * It refuses any database that is not on this machine.
  * An existing preview act is refreshed on re-run WITHOUT moving ingested_at,
    so a re-run does not put it back into anyone's digest window.
  * `--delete` removes every preview act and the authorities it created —
    and, since the acts are indistinguishable, every act tsg_ingest projected.

Usage
    python tsg_preview_import.py                          # every probe run
    python tsg_preview_import.py runs/tsg-probe-day-20260804
    python tsg_preview_import.py --dry-run
    python tsg_preview_import.py --delete

Env
    DATABASE_URL   default postgresql://postgres:pw@127.0.0.1:5433/procurement
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import pathlib
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from tsg_ingest import (  # noqa: E402,F401 — re-exported: the preview's tests pin them
    ATHENS, OUTSIDE_GREECE, PREFIX, TYPE_BY_LABEL, ada_of, adam_of, fold, map_record,
    outside_greece, parse_amount, parse_date, type_of, upsert_act,
    authority_id as _authority_id,
    nuts_known as _nuts_known,
    replace_cpvs as _replace_cpvs,
)

DEFAULT_DB = "postgresql://postgres:pw@127.0.0.1:5433/procurement"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
def held_reason(cur, r: dict) -> str | None:
    """Why this record is already in the database, or None."""
    ext = r.get("externalId")
    for kind, key in (("ΑΔΑΜ", adam_of(ext)), ("ΑΔΑ", ada_of(ext))):
        if key:
            cur.execute("SELECT 1 FROM proc.procurement_act WHERE adam = %s", (key,))
            if cur.fetchone():
                return kind
    return None


def _upsert_act(cur, cols: dict) -> bool:
    return upsert_act(cur, cols) == "inserted"


def run_import(conn, records: list[dict], today: dt.date | None = None) -> dict:
    from psycopg.types.json import Jsonb
    stats = collections.Counter()
    by_source = collections.Counter()
    issues = collections.Counter()
    skipped = collections.Counter()
    chars = [0, 0]
    cache: dict = {}
    nuts_cache: dict = {}
    with conn.cursor() as cur:
        for r in records:
            if not str(r.get("internalID") or "").strip():
                skipped["no internalID"] += 1
                continue
            if outside_greece(r):
                skipped[OUTSIDE_GREECE] += 1
                continue
            reason = held_reason(cur, r)
            if reason:
                skipped[f"already held ({reason})"] += 1
                continue
            cols, extras = map_record(r, today)
            # procurement_act.nuts_code is a foreign key; a code the catalogue
            # does not know is left empty rather than failing the whole import.
            if cols["nuts_code"] and not _nuts_known(cur, cols["nuts_code"], nuts_cache):
                stats["nuts not in catalogue"] += 1
                cols["nuts_code"] = None
            cols["authority_id"] = _authority_id(cur, extras["authority"], cache)
            cols["raw_json"] = Jsonb({k: v for k, v in r.items() if not k.startswith("_")})
            inserted = _upsert_act(cur, cols)
            stats["inserted" if inserted else "refreshed"] += 1
            stats[f"type:{cols['type']}"] += 1
            stats["cpv links"] += _replace_cpvs(cur, cols["adam"], cols, extras["cpvs"])
            stats["with full_text_html"] += bool(cols["full_text_html"])
            by_source[r.get("dataSource") or "?"] += 1
            chars[0] += extras["raw_chars"]
            chars[1] += len(cols["full_text_html"] or "")
            for i in extras["issues"]:
                issues[i.split(" '")[0].split(' "')[0]] += 1
    return {"stats": dict(stats), "skipped": dict(skipped), "by_source": dict(by_source),
            "issues": dict(issues), "html_chars": {"raw": chars[0], "normalised": chars[1]}}


def run_delete(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""DELETE FROM proc.procurement_act
                       WHERE adam LIKE %s AND data_source = 'tsg' AND origin = 'import'""", (PREFIX + "%",))
        acts = cur.rowcount
        cur.execute("""DELETE FROM proc.authority a
                       WHERE a.org_id LIKE %s AND a.source = 'tsg'
                         AND NOT EXISTS (SELECT 1 FROM proc.procurement_act p WHERE p.authority_id = a.org_id)""",
                    (PREFIX + "%",))
        return {"acts deleted": acts, "authorities deleted": cur.rowcount}


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def load_records(paths: list[str]) -> list[dict]:
    files = []
    for p in paths or ["runs"]:
        path = pathlib.Path(p)
        if path.is_file():
            files.append(path)
        else:
            files += sorted(pathlib.Path(x) for x in glob.glob(str(path / "**" / "records.json"), recursive=True))
    seen: dict[str, dict] = {}
    for f in files:
        for r in json.loads(f.read_text(encoding="utf-8")):
            key = str(r.get("internalID") or "")
            if key:
                seen[key] = r
    return list(seen.values())


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="probe run folders or records.json files (default: runs/)")
    ap.add_argument("--dry-run", action="store_true", help="do everything, then roll back")
    ap.add_argument("--delete", action="store_true", help="remove every preview act")
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL") or DEFAULT_DB
    if not is_local(url):
        print("Refusing: DATABASE_URL is not a database on this machine. This is a local preview.")
        return 2

    import psycopg
    with psycopg.connect(url) as conn:
        if args.delete:
            result = run_delete(conn)
        else:
            records = load_records(args.paths)
            print(f"{len(records)} distinct records found")
            result = run_import(conn, records)
        if args.dry_run:
            conn.rollback()
            print("DRY RUN — rolled back")
        else:
            conn.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
