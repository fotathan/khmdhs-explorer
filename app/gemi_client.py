"""
gemi_client.py — shared ΓΕΜΗ (business registry) fetch + flatten + upsert logic.

Used by BOTH the offline backfill (gemi_enrich.py) and the on-demand web route
(admin button on contractor/authority pages), so the parsing and storage stay
identical. No CLI, no DB connection management here — callers pass a cursor.

The API key is read from the GEMI_API_KEY environment variable; it is never
logged or stored.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import httpx
from psycopg.types.json import Json

API_BASE = "https://opendata-api.businessportal.gr/api/opendata/v1/companies"

_AFM_RE = re.compile(r"^\d{9}$")


def normalize_afm(raw: str | None) -> str | None:
    """Clean 9-digit Greek ΑΦΜ or None. Handles whitespace, EL/ΕΛ prefix, and
    the stripped-leading-zero case (8 digits → pad to 9)."""
    if not raw:
        return None
    s = raw.strip().upper()
    for pre in ("EL", "ΕΛ"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    s = re.sub(r"\s+", "", s)
    if not s.isdigit():
        return None
    if len(s) == 8:
        s = s.zfill(9)
    return s if len(s) == 9 else None


def auth_header(api_key: str) -> dict:
    contact = os.environ.get("GEMI_CONTACT", "")
    ua = "KHMDHS-enrichment/1.0 (public-procurement-transparency)"
    if contact:
        ua += f"; contact={contact}"
    return {"api_key": api_key, "Accept": "application/json", "User-Agent": ua}


def fetch_company(client: httpx.Client, api_key: str, afm: str,
                  max_retries: int = 2) -> tuple[str, dict | None, int]:
    """Return (status, record, total_count).
    status: 'ok' | 'not_found' | 'ambiguous' | 'error'."""
    params = {"afm": afm, "resultsSortBy": "+arGemi",
              "resultsOffset": 0, "resultsSize": 10}
    for attempt in range(1, max_retries + 1):
        try:
            r = client.get(API_BASE, params=params,
                           headers=auth_header(api_key), timeout=30.0)
        except httpx.RequestError:
            if attempt == max_retries:
                return "error", None, 0
            continue

        if r.status_code == 429:
            # one polite wait then retry; a manual button shouldn't hammer
            if attempt == max_retries:
                return "error", None, 0
            import time
            time.sleep(float(r.headers.get("Retry-After", 5)))
            continue
        if r.status_code in (401, 403):
            return "error", None, 0
        if r.status_code == 404:
            return "not_found", None, 0
        if r.status_code >= 500:
            if attempt == max_retries:
                return "error", None, 0
            continue
        if r.status_code != 200:
            return "error", None, 0

        try:
            data = r.json()
        except ValueError:
            return "error", None, 0

        total = (data.get("searchMetadata") or {}).get("totalCount", 0) or 0
        results = data.get("searchResults") or []
        if total == 0 or not results:
            return "not_found", None, 0
        exact = [x for x in results if str(x.get("afm")) == afm]
        chosen = exact[0] if exact else results[0]
        status = "ok" if (total == 1 or exact) else "ambiguous"
        return status, chosen, total

    return "error", None, 0


def _active_activities(rec: dict) -> tuple[str | None, str | None, list]:
    acts = rec.get("activities") or []
    active = [a for a in acts if a.get("dtTo") in (None, "", "null")]
    if not active:
        return None, None, []

    def ver(a):
        return ((a.get("activity") or {}).get("kadVersion") or "")
    latest = max((ver(a) for a in active), default="")
    if latest:
        active = [a for a in active if ver(a) == latest]

    flat, primary_id, primary_descr = [], None, None
    for a in active:
        act = a.get("activity") or {}
        item = {"id": act.get("id"), "descr": act.get("descr"),
                "type": a.get("type"), "kadVersion": act.get("kadVersion")}
        flat.append(item)
        if (a.get("type") or "").strip() == "Κύρια" and primary_id is None:
            primary_id, primary_descr = item["id"], item["descr"]
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


_UPSERT = """
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


# Seed the editable contractor working-copy fields from a successful ΓΕΜΗ pull.
# FILL-ONLY-IF-EMPTY: COALESCE(NULLIF(col,''), new) keeps any value already
# there (a curator's edit in the contractor form, or an earlier fill), so a
# later re-pull never clobbers hand-entered data. Matches on the 9-digit AFM
# (vat_number is the natural, indexed key on economic_operator).
_FILL_OPERATOR = """
UPDATE proc.economic_operator SET
    ar_gemi        = COALESCE(NULLIF(ar_gemi, ''),        %(ar_gemi)s),
    city           = COALESCE(NULLIF(city, ''),           %(city)s),
    postal_code    = COALESCE(NULLIF(postal_code, ''),    %(zip_code)s),
    street_address = COALESCE(NULLIF(street_address, ''), %(street_address)s),
    contact_phone  = COALESCE(NULLIF(contact_phone, ''),  %(phone)s),
    contact_fax    = COALESCE(NULLIF(contact_fax, ''),    %(fax)s),
    contact_email  = COALESCE(NULLIF(contact_email, ''),  %(email)s),
    contact_url    = COALESCE(NULLIF(contact_url, ''),    %(url)s)
WHERE vat_number = %(afm)s OR vat_number = %(afm_alt)s
"""


def upsert(cur, afm: str, status: str, rec: dict | None, total_count: int):
    """Write one enrichment row. cur is a live DB cursor (caller owns the
    transaction). Always records the status, even for not_found/error, so the
    attempt is visible. On a successful pull, also seeds the editable contractor
    fields fill-only-if-empty (so the data shows on the contractor edit page)."""
    row = {"afm": afm, "raw": None, "match_count": total_count,
           "fetch_status": status}
    if rec is not None:
        row.update(flatten(rec))
        row["raw"] = Json(rec)
        row["activities_active"] = Json(row["activities_active"])
    else:
        for k in ("ar_gemi", "legal_name", "trade_title", "legal_type",
                  "status", "status_id", "is_branch", "street", "street_number",
                  "zip_code", "city", "municipality", "prefecture", "phone",
                  "fax", "email", "url", "primary_kad", "primary_kad_descr",
                  "incorporation_date"):
            row.setdefault(k, None)
        row["activities_active"] = Json([])
    cur.execute(_UPSERT, row)

    if rec is not None:
        st = (row.get("street") or "").strip()
        sn = (row.get("street_number") or "").strip()
        cur.execute(_FILL_OPERATOR, {
            "afm": afm,
            "afm_alt": afm.lstrip("0") or afm,   # also match an un-padded vat
            "ar_gemi": row.get("ar_gemi"),
            "city": row.get("city"),
            "zip_code": row.get("zip_code"),
            "street_address": (st + " " + sn).strip() or None,
            "phone": row.get("phone"),
            "fax": row.get("fax"),
            "email": row.get("email"),
            "url": row.get("url"),
        })


def enrich_one(cur, afm_raw: str) -> tuple[str, str | None]:
    """High-level: normalise → fetch → upsert one ΑΦΜ. Returns (status, afm).
    Reads GEMI_API_KEY from env. cur is a live cursor. Raises RuntimeError if
    the key is missing."""
    key = os.environ.get("GEMI_API_KEY")
    if not key:
        raise RuntimeError("GEMI_API_KEY not set")
    afm = normalize_afm(afm_raw)
    if not afm:
        return "bad_afm", None
    with httpx.Client() as client:
        status, rec, total = fetch_company(client, key, afm)
    upsert(cur, afm, status, rec, total)
    return status, afm
