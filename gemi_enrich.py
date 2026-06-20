#!/usr/bin/env python3
"""
gemi_enrich.py — backfill ΓΕΜΗ (business registry) data for the ΑΦΜ values in
KHMDHS, into proc.gemi_enrichment.

STANDALONE, on-demand script — NOT part of the web app. Run it from the project
root with the DB and API key in the environment:

    export DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement"
    export GEMI_API_KEY="…your opendata.businessportal.gr key…"
    python gemi_enrich.py --scope suppliers          # economic_operator only
    python gemi_enrich.py --scope authorities        # authority only
    python gemi_enrich.py --scope both               # both (default)
    python gemi_enrich.py --scope both --limit 50     # try a small batch first
    python gemi_enrich.py --refresh-days 90           # re-fetch rows older than N days

It validates each ΑΦΜ, calls the companies endpoint, flattens the fields we use,
keeps only ACTIVE ΚΑΔ (dtTo IS NULL) at the latest kadVersion, and upserts one
row per ΑΦΜ. Rows that 0-match or error are still recorded (fetch_status) so you
can see coverage. Throttled + retried so it stays within fair use — tune --delay
and respect whatever rate limit ΓΕΜΗ stated for your key.

The API key is read ONLY from the environment; it is never logged or stored.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg
from psycopg.types.json import Json
from psycopg.rows import dict_row

API_BASE = "https://opendata-api.businessportal.gr/api/opendata/v1/companies"

# Greek ΑΦΜ: 9 digits. (We don't verify the check digit; the API will 0-match a
# bad one and we record that.) Strip spaces / a leading EL.
_AFM_RE = re.compile(r"^\d{9}$")


def normalize_afm(raw: str | None) -> str | None:
    """Return a clean 9-digit Greek ΑΦΜ, or None if not recoverable.
    Handles: surrounding whitespace, an EL/ΕΛ country prefix, and the common
    stripped-leading-zero case (8 all-digit chars → left-pad to 9). Anything
    that isn't 8-or-9 pure digits after that is rejected (foreign VATs, junk,
    concatenated values)."""
    if not raw:
        return None
    s = raw.strip().upper()
    # strip Latin EL or Greek ΕΛ prefix if present
    for pre in ("EL", "ΕΛ"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    s = re.sub(r"\s+", "", s)
    if not s.isdigit():
        return None
    if len(s) == 8:          # recover a stripped leading zero
        s = s.zfill(9)
    return s if len(s) == 9 else None


def auth_header(api_key: str) -> dict:
    # The ΓΕΜΗ Open Data API authenticates with the issued api_key. Header name
    # per their docs; if your key uses a different scheme (query param or a
    # different header), adjust HERE only.
    #
    # We also send an identifying User-Agent. Being a KNOWN, contactable client
    # is protective: a registry is far more likely to email "please slow down"
    # than to silently revoke a key it can see belongs to a real, legitimate
    # project. Set GEMI_CONTACT to your email so they can reach you. (Do NOT use
    # this to disguise the client — identifiability is the point.)
    contact = os.environ.get("GEMI_CONTACT", "")
    ua = "KHMDHS-enrichment/1.0 (public-procurement-transparency)"
    if contact:
        ua += f"; contact={contact}"
    return {"api_key": api_key, "Accept": "application/json", "User-Agent": ua}


class Pacer:
    """Adaptive inter-call delay. Starts at `delay`, increases each time the
    server returns 429 (so the run settles UNDER the rate limit instead of
    repeatedly slamming into it), and slowly decays after a long clean streak.
    Avoids having to guess the right fixed delay for a multi-day backfill."""

    def __init__(self, delay: float, max_delay: float = 30.0):
        self.delay = delay
        self.base = delay
        self.max_delay = max_delay
        self._clean = 0

    def on_429(self):
        # multiplicative increase, capped
        self.delay = min(self.delay * 1.5 + 0.5, self.max_delay)
        self._clean = 0

    def on_ok(self):
        # after a sustained clean streak, ease back down a little toward base
        self._clean += 1
        if self._clean >= 40 and self.delay > self.base:
            self.delay = max(self.base, self.delay * 0.9)
            self._clean = 0

    def sleep(self):
        time.sleep(self.delay)


def fetch_company(client: httpx.Client, api_key: str, afm: str,
                  pacer: "Pacer | None" = None,
                  max_retries: int = 4) -> tuple[str, dict | None, int]:
    """Return (status, record, total_count).
    status: 'ok' | 'not_found' | 'ambiguous' | 'error'.
    record: the chosen searchResults[0] dict (or None).
    """
    params = {
        "afm": afm,
        "resultsSortBy": "+arGemi",
        "resultsOffset": 0,
        "resultsSize": 10,
    }
    backoff = 1.5
    for attempt in range(1, max_retries + 1):
        try:
            r = client.get(API_BASE, params=params,
                           headers=auth_header(api_key), timeout=30.0)
        except httpx.RequestError as e:
            if attempt == max_retries:
                print(f"  ! network error for {afm}: {e}", file=sys.stderr)
                return "error", None, 0
            time.sleep(backoff ** attempt)
            continue

        if r.status_code == 429:  # rate limited — back off, raise steady pace
            wait = float(r.headers.get("Retry-After", backoff ** attempt))
            if pacer is not None:
                pacer.on_429()
            print(f"  · 429 rate-limited on {afm}; sleeping {wait:.0f}s "
                  f"(steady delay now {pacer.delay:.1f}s)" if pacer else
                  f"  · 429 rate-limited on {afm}; sleeping {wait:.0f}s",
                  file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code in (401, 403):
            print(f"  ! auth rejected ({r.status_code}) — check GEMI_API_KEY",
                  file=sys.stderr)
            return "error", None, 0
        if r.status_code == 404:
            # This API returns 404 when no company matches the ΑΦΜ — that's a
            # normal "not found", not a failure.
            return "not_found", None, 0
        if r.status_code >= 500:
            if attempt == max_retries:
                return "error", None, 0
            time.sleep(backoff ** attempt)
            continue
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code} for {afm}", file=sys.stderr)
            return "error", None, 0

        try:
            data = r.json()
        except ValueError:
            return "error", None, 0

        total = (data.get("searchMetadata") or {}).get("totalCount", 0) or 0
        results = data.get("searchResults") or []
        if total == 0 or not results:
            return "not_found", None, 0
        # Usually 1. If several, prefer an exact afm match, else the first; flag.
        exact = [x for x in results if str(x.get("afm")) == afm]
        chosen = exact[0] if exact else results[0]
        status = "ok" if (total == 1 or exact) else "ambiguous"
        return status, chosen, total

    return "error", None, 0


def _active_activities(rec: dict) -> tuple[str | None, str | None, list]:
    """Keep ΚΑΔ rows with dtTo IS NULL (currently active). If multiple
    kadVersions are active, prefer the lexicographically-latest version string
    (e.g. kad_2026 > kad_2008). Returns (primary_id, primary_descr, list)."""
    acts = rec.get("activities") or []
    active = [a for a in acts if a.get("dtTo") in (None, "", "null")]
    if not active:
        return None, None, []

    # latest kadVersion among active rows
    def ver(a):
        return ((a.get("activity") or {}).get("kadVersion") or "")
    latest = max((ver(a) for a in active), default="")
    if latest:
        active = [a for a in active if ver(a) == latest]

    flat = []
    primary_id = primary_descr = None
    for a in active:
        act = a.get("activity") or {}
        item = {
            "id": act.get("id"),
            "descr": act.get("descr"),
            "type": a.get("type"),
            "kadVersion": act.get("kadVersion"),
        }
        flat.append(item)
        if (a.get("type") or "").strip() == "Κύρια" and primary_id is None:
            primary_id, primary_descr = item["id"], item["descr"]
    # fallback: if no explicit Κύρια, take the first
    if primary_id is None and flat:
        primary_id, primary_descr = flat[0]["id"], flat[0]["descr"]
    return primary_id, primary_descr, flat


def _date_or_none(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def flatten(rec: dict) -> dict:
    """Map one ΓΕΜΗ searchResults record to gemi_enrichment columns."""
    def descr(obj):
        return (obj or {}).get("descr")

    titles = rec.get("coTitlesEl") or []
    trade_title = titles[0].strip() if titles else None

    primary_kad, primary_kad_descr, active = _active_activities(rec)

    return {
        "ar_gemi": rec.get("arGemi"),
        "legal_name": rec.get("coNameEl"),
        "trade_title": trade_title,
        "legal_type": descr(rec.get("legalType")),
        "status": descr(rec.get("status")),
        "status_id": (rec.get("status") or {}).get("id"),
        "is_branch": rec.get("isBranch"),
        "street": rec.get("street"),
        "street_number": rec.get("streetNumber"),
        "zip_code": rec.get("zipCode"),
        "city": rec.get("city"),
        "municipality": descr(rec.get("municipality")),
        "prefecture": descr(rec.get("prefecture")),
        "phone": rec.get("phone"),
        "fax": rec.get("fax"),
        "email": rec.get("email"),
        "url": rec.get("url"),
        "primary_kad": primary_kad,
        "primary_kad_descr": primary_kad_descr,
        "activities_active": active,
        "incorporation_date": _date_or_none(rec.get("incorporationDate")),
    }


UPSERT = """
INSERT INTO proc.gemi_enrichment (
    afm, ar_gemi, legal_name, trade_title, legal_type, status, status_id,
    is_branch, street, street_number, zip_code, city, municipality, prefecture,
    phone, fax, email, url, primary_kad, primary_kad_descr, activities_active,
    incorporation_date, raw, match_count, fetched_at, fetch_status
) VALUES (
    %(afm)s, %(ar_gemi)s, %(legal_name)s, %(trade_title)s, %(legal_type)s,
    %(status)s, %(status_id)s, %(is_branch)s, %(street)s, %(street_number)s,
    %(zip_code)s, %(city)s, %(municipality)s, %(prefecture)s, %(phone)s,
    %(fax)s, %(email)s, %(url)s, %(primary_kad)s, %(primary_kad_descr)s,
    %(activities_active)s, %(incorporation_date)s, %(raw)s, %(match_count)s,
    now(), %(fetch_status)s
)
ON CONFLICT (afm) DO UPDATE SET
    ar_gemi=EXCLUDED.ar_gemi, legal_name=EXCLUDED.legal_name,
    trade_title=EXCLUDED.trade_title, legal_type=EXCLUDED.legal_type,
    status=EXCLUDED.status, status_id=EXCLUDED.status_id,
    is_branch=EXCLUDED.is_branch, street=EXCLUDED.street,
    street_number=EXCLUDED.street_number, zip_code=EXCLUDED.zip_code,
    city=EXCLUDED.city, municipality=EXCLUDED.municipality,
    prefecture=EXCLUDED.prefecture, phone=EXCLUDED.phone, fax=EXCLUDED.fax,
    email=EXCLUDED.email, url=EXCLUDED.url, primary_kad=EXCLUDED.primary_kad,
    primary_kad_descr=EXCLUDED.primary_kad_descr,
    activities_active=EXCLUDED.activities_active,
    incorporation_date=EXCLUDED.incorporation_date, raw=EXCLUDED.raw,
    match_count=EXCLUDED.match_count, fetched_at=now(),
    fetch_status=EXCLUDED.fetch_status;
"""


def collect_afms(conn, scope: str, refresh_days: int | None, limit: int | None,
                 priority: bool = False):
    """Distinct ΑΦΜ to process, skipping ones enriched within refresh_days.

    Only plausible Greek ΑΦΜ are selected: after stripping an optional EL/ΕΛ
    prefix the value must be 8 or 9 digits (8 = stripped leading zero, recovered
    in Python), and not an all-zeros placeholder. This skips the dirty
    vat_number rows that can't go to ΓΕΜΗ anyway.

    priority=True: order suppliers by NUMBER OF CONTRACTS won (most first), so a
    capped run enriches the entities that matter most for analysis before the
    long tail. (We rank by contract COUNT, not summed awarded value, because the
    act_operator.awarded_value_* columns are not populated in this dataset —
    count is the available importance signal, and "supplier with the most
    contracts" is a good proxy for a watchdog anyway.) Priority applies to the
    supplier side; authorities (no contract count here) sort after, by ΑΦΜ.
    """
    # plausible = optional EL/ΕΛ prefix, then exactly 8 or 9 digits, AND not an
    # all-zeros placeholder. We deliberately DON'T filter by leading zeros —
    # real ΑΦΜ can start with 0 (e.g. 094014201) — only the all-zero case.
    digits = "regexp_replace(upper(vat_number), '^(EL|ΕΛ)', '')"
    plausible = (f"{digits} ~ '^[0-9]{{8,9}}$' "
                 f"AND {digits} !~ '^0+$'")

    if refresh_days is not None:
        done_skip = f"""fetch_status = 'ok'
                        AND fetched_at > now() - interval '{int(refresh_days)} days'"""
    else:
        done_skip = "fetch_status = 'ok'"

    norm = "lpad(regexp_replace(upper(s.afm), '^(EL|ΕΛ)', ''), 9, '0')"

    if priority and scope in ("suppliers", "both"):
        # Rank suppliers by number of award rows (contract count); authorities
        # appended after with rank 0. eo = economic_operator, ao = act_operator.
        sql = f"""
            SELECT s.afm FROM (
                SELECT eo.vat_number AS afm, COUNT(ao.id) AS rank
                FROM proc.economic_operator eo
                LEFT JOIN proc.act_operator ao ON ao.operator_id = eo.operator_id
                WHERE eo.vat_number IS NOT NULL
                  AND {plausible.replace('vat_number', 'eo.vat_number')}
                GROUP BY eo.vat_number
        """
        if scope == "both":
            sql += f"""
                UNION ALL
                SELECT a.vat_number AS afm, 0 AS rank
                FROM proc.authority a
                WHERE a.vat_number IS NOT NULL
                  AND {plausible.replace('vat_number', 'a.vat_number')}
            """
        sql += f"""
            ) s
            WHERE {norm} NOT IN (SELECT afm FROM proc.gemi_enrichment WHERE {done_skip})
            ORDER BY s.rank DESC, s.afm
        """
    else:
        parts = []
        if scope in ("suppliers", "both"):
            parts.append(f"SELECT vat_number AS afm FROM proc.economic_operator "
                         f"WHERE vat_number IS NOT NULL AND {plausible}")
        if scope in ("authorities", "both"):
            parts.append(f"SELECT vat_number AS afm FROM proc.authority "
                         f"WHERE vat_number IS NOT NULL AND {plausible}")
        union = " UNION ".join(parts)
        sql = f"""
            SELECT s.afm FROM (
                SELECT DISTINCT s.afm,
                       length(regexp_replace(upper(s.afm), '^(EL|ΕΛ)', '')) AS afm_len
                FROM ({union}) s
                WHERE s.afm IS NOT NULL
                  AND {norm} NOT IN (SELECT afm FROM proc.gemi_enrichment WHERE {done_skip})
            ) s
            ORDER BY s.afm_len DESC, s.afm
        """

    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as c:
        c.execute(sql)
        return [r[0] for r in c.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["suppliers", "authorities", "both"],
                    default="both")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of ΑΦΜ this run (test with a small value)")
    ap.add_argument("--refresh-days", type=int, default=None,
                    help="re-fetch rows older than N days (else skip all done)")
    ap.add_argument("--priority", action="store_true",
                    help="enrich suppliers by total awarded contract value "
                         "(highest first) — recommended: most analytical value "
                         "from the fewest calls, keeps volume modest")
    ap.add_argument("--max-calls", type=int, default=None,
                    help="stop after this many API calls this run (self-imposed "
                         "daily budget; resume tomorrow — done rows are skipped)")
    ap.add_argument("--afm", default=None,
                    help="test a single specific ΑΦΜ directly (skips the DB "
                         "selection); useful for verifying auth + parsing")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="starting seconds between API calls. The script adapts "
                         "upward automatically on 429s, so this is just a floor; "
                         "raise it if you want to start gentler.")
    args = ap.parse_args()

    db = os.environ.get("DATABASE_URL")
    key = os.environ.get("GEMI_API_KEY")
    if not db:
        sys.exit("DATABASE_URL not set")
    if not key:
        sys.exit("GEMI_API_KEY not set")

    with psycopg.connect(db, autocommit=True) as conn:
        if args.afm:
            afms = [args.afm]
            print(f"Single-ΑΦΜ test: {args.afm}")
        else:
            afms = collect_afms(conn, args.scope, args.refresh_days, args.limit,
                                priority=args.priority)
        total = len(afms)
        if not args.afm:
            print(f"To process: {total} ΑΦΜ (scope={args.scope})")
        if not total:
            return

        ok = nf = amb = err = skipped_bad = 0
        calls = 0
        pacer = Pacer(args.delay)
        with httpx.Client() as client, conn.cursor() as cur:
            for i, raw_afm in enumerate(afms, 1):
                afm = normalize_afm(raw_afm)
                if not afm:
                    skipped_bad += 1
                    continue

                if args.max_calls is not None and calls >= args.max_calls:
                    print(f"  · reached --max-calls={args.max_calls}; stopping. "
                          f"Re-run later to continue (done rows are skipped).")
                    break

                status, rec, total_count = fetch_company(client, key, afm, pacer)
                calls += 1
                row = {"afm": afm, "raw": None, "match_count": total_count,
                       "fetch_status": status}
                if rec is not None:
                    row.update(flatten(rec))
                    row["raw"] = Json(rec)
                    row["activities_active"] = Json(row["activities_active"])
                else:
                    # ensure all columns exist for the insert
                    for k in ("ar_gemi", "legal_name", "trade_title",
                              "legal_type", "status", "status_id", "is_branch",
                              "street", "street_number", "zip_code", "city",
                              "municipality", "prefecture", "phone", "fax",
                              "email", "url", "primary_kad", "primary_kad_descr",
                              "incorporation_date"):
                        row.setdefault(k, None)
                    row["activities_active"] = Json([])

                cur.execute(UPSERT, row)

                ok += status == "ok"
                nf += status == "not_found"
                amb += status == "ambiguous"
                err += status == "error"
                if status != "error":
                    pacer.on_ok()

                if i % 25 == 0 or i == total:
                    print(f"  [{i}/{total}] ok={ok} not_found={nf} "
                          f"ambiguous={amb} error={err} "
                          f"(delay {pacer.delay:.1f}s)")
                pacer.sleep()

        print(f"\nDone. ok={ok} not_found={nf} ambiguous={amb} error={err} "
              f"bad_afm={skipped_bad}")


if __name__ == "__main__":
    main()
