#!/usr/bin/env python3
"""tsg_ingest.py — Tender Service (Data Export API v3) as a fourth source.

Tender Service aggregates Greek notices and award results from many upstream
portals: Diavgeia, eProcurement (ΚΗΜΔΗΣ), TED, and a long tail we have no
ingester for (hospital sites, isupplies, promitheus, regional gazettes, hand-
entered notices). That long tail is the reason to ingest it at all. Everything
else we already hold, and showing it twice is the failure this module is built
around.

Shape (closest model: ted_ingest.py)
  * proc.tsg_record is authoritative: one row per internalID — the raw payload,
    the keys the de-duplication and the windows need, and a content hash.
  * A record is projected into proc.procurement_act (adam 'TSG:<internalID>',
    data_source 'tsg') ONLY when none of its identity keys — ΑΔΑΜ, ΑΔΑ, TED
    publication number, read from externalId / sourceUrl — is an act we already
    hold from another source. When our own ingester catches up later, the
    projection is hidden (tsg_match). referenceNumber is NOT an identity:
    on a notice it is usually the REQUEST the notice came from.
  * Projection is Python, not set-based SQL: amounts are Greek-formatted strings
    and tenderText is page HTML (app/rich_text.import_full_text). It only visits
    records whose content hash moved since they were last projected, so a
    re-walk of an unchanged day costs requests but no writes.
  * A refreshed act keeps its ingested_at, so a re-walk never puts it back into
    anyone's digest window.
  * Beyond identity keys, tsg_match.py decides whether a notice is a tender we
    already show: an exact number (ΕΣΗΔΗΣ, quoted ΑΔΑΜ/request/ΑΔΑ, a Tender
    Service twin) hides it; a likely match only flags it, for alert labels and
    admin review. docs/specs/tender-service-duplicates.md.

What the API forces (measured by tsg_probe.py, and by ConnectContractors'
DATA_EXPORT_LIMITATIONS on the Romanian key — read both before changing this)
  * 10 records per page, and a query stops at offset 10,000 whatever its total.
    So a window is ONE DAY per slice, and a slice whose total is over the cap is
    recorded 'over_cap' instead of being silently truncated.
  * `_to` is EXCLUSIVE: one day is from=d&to=d+1.
  * Unfiltered means ACTIVE only; the archive needs status=EXPIRED explicitly,
    so every day is walked once per status.
  * An unknown parameter is IGNORED, not rejected — a filter that does nothing
    looks exactly like one that matched everything. tsg_probe re-checks this.
  * 300 requests / 10 min per key (Rate-Limit-Remaining), plus a 100/day cap
    that appeared once in Romania, signalled only by a 400 carrying
    customer_api_limit_reached. Hitting it stops the run cleanly and the window
    stays pending. Every run also has its own request budget (--max-requests).
  * Offset paging over a live set skips rows when records change status
    mid-walk. A window that delivered fewer distinct records than its reported
    total is 'incomplete' and is walked again — never 'done'.
  * ONE contractor tax number per notice, however many winners. A joint award
    links no operator rather than guess whose number it is.

Not in production yet: the commands refuse a database that is not on this
machine unless TSG_INGEST_REMOTE is on. New acts get ingested_at = now(), which
puts them into customers' digest windows — switching that on is a product
decision, not a deploy.

Commands: db.py tsg-backfill | tsg-catchup | tsg-project   (see --help)
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import html
import json
import os
import random
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from app.rich_text import import_full_text
from khmdhs_ingest import _as_jsonb
from tsg_probe import (PROD_BASE, extract_meta, extract_records, find_total,  # noqa: F401
                       load_key, parse_max_from_error, parse_rate_limit)

PREFIX = "TSG:"
# The API declares no timezone. Greek portals publish local time, so dates are
# read as Europe/Athens — an assumption, and the first thing to check if a
# deadline ever looks an hour or two out.
ATHENS = ZoneInfo("Europe/Athens")
# What /branch/dateFormat reports for the Greek key. check_date_format() refuses
# to run on anything else: a request date in the wrong format names the wrong day.
DATE_PATTERN = "dd.MM.yy"
DATE_FMT = "%d.%m.%y"

START_PAGE_SIZE = 10
OFFSET_CAP = 10_000
RATE_LIMIT_FLOOR = 20
MAX_ATTEMPTS = 4
TIMEOUT = (10, 60)
DEFAULT_MAX_REQUESTS = int(os.environ.get("TSG_MAX_REQUESTS", "2000"))
PROJECT_BATCH = 500

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}

# Backfill walks publication days; catch-up walks lastUpdated days. The
# catch-up slices do not filter by type, so a document type the backfill does
# not name still arrives through them.
PUBLICATION_SLICES = {
    "pub:tender:active": {"typeOfDocument": "TENDER"},
    "pub:tender:expired": {"typeOfDocument": "TENDER", "status": "EXPIRED"},
    "pub:result:active": {"typeOfDocument": "RESULT"},
    "pub:result:expired": {"typeOfDocument": "RESULT", "status": "EXPIRED"},
}
UPDATED_SLICES = {
    "upd:active": {},
    "upd:expired": {"status": "EXPIRED"},
}
SLICES = {**PUBLICATION_SLICES, **UPDATED_SLICES}

ADAM_RE = re.compile(r"^\d{2}(?:PROC|REQ|AWRD|SYMV|PAY)\d{6,}$")
# Tender Service appends a timestamp to a Diavgeia ΑΔΑ: '94ΩΕ46907Τ-ΚΜΗ-09-11143505'.
ADA_RE = re.compile(r"^([0-9Α-ΩA-Z]{6,10}-[0-9Α-ΩA-Z]{3})(?:-|$)")
_GREEK_CAP = re.compile(r"[Α-Ω]")
# TED as Tender Service spells it: externalId 'TED.00538898-2026', or the notice
# URL '...uri=TED:NOTICE:538898-2026:TEXT:EN:HTML'. Ours is 'TED:538898-2026'.
_TED_EXT = re.compile(r"^TED\.0*(\d{1,8}-(?:19|20)\d{2})$")
_TED_URL = re.compile(r"TED:NOTICE:0*(\d{1,8}-(?:19|20)\d{2})")
_VAT_GR = re.compile(r"^\d{9}$")
# A natural person packed with commas: 'ΕΠΩΝΥΜΟ,,ΟΝΟΜΑ,ΠΑΤΡΩΝΥΜΟ'.
_PERSON = re.compile(r"^[^,]+,[^,]*,[^,]+,[^,]*$")
_AMOUNT = re.compile(r"^\s*([\d.,]+)\s*([A-Z]{3})?\s*$")
_DMY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2}|\d{4})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?")

# Display label (folded) → our act type. 'Αποτέλεσμα' is an award decision,
# which is what KHMDHS calls 'auction' (ΑΔΑΜ kind AWRD).
TYPE_BY_LABEL = {
    "προκηρυξη": "notice",
    "αποτελεσμα": "auction",
    "συμβαση": "contract",
    "πληρωμη": "payment",
    "αιτημα": "request",
}


# --------------------------------------------------------------------------- #
# pure mapping (tests/test_tsg_ingest.py, tests/test_tsg_preview_import.py)
# --------------------------------------------------------------------------- #
def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").replace("ς", "σ")


def adam_of(external_id: str | None) -> str | None:
    s = (external_id or "").strip()
    return s if ADAM_RE.match(s) else None


def ada_of(external_id: str | None) -> str | None:
    m = ADA_RE.match((external_id or "").strip())
    return m.group(1) if m and _GREEK_CAP.search(m.group(1)) else None


def ted_of(external_id: str | None, source_url: str | None = None) -> str | None:
    m = _TED_EXT.match((external_id or "").strip()) or _TED_URL.search(source_url or "")
    return "TED:" + m.group(1) if m else None


def held_keys(r: dict) -> list[str]:
    """The adams this record would have if we already held it from another source.
    Identity fields only — see the module docstring on referenceNumber."""
    ext = str(r.get("externalId") or "").strip()
    keys: list[str] = []
    for k in (adam_of(ext), ada_of(ext), ted_of(ext, r.get("sourceUrl"))):
        if k and k not in keys:
            keys.append(k)
    return keys


OUTSIDE_GREECE = "outside Greece"


def outside_greece(r: dict) -> bool:
    """Every NUTS code the record names is foreign — the Greek key's profile also
    returns Cyprus (CY000). No NUTS at all is not evidence either way: kept."""
    codes = str(r.get("nutsCodes") or "").split()
    return bool(codes) and not any(c.upper().startswith("EL") for c in codes)


def type_of(label: str | None) -> tuple[str, bool]:
    """(act type, known). An unknown label is imported as a notice and counted."""
    t = TYPE_BY_LABEL.get(fold(label))
    return (t, True) if t else ("notice", False)


def _one_amount(s: str) -> tuple[Decimal | None, str | None]:
    m = _AMOUNT.match(s or "")
    if not m:
        return None, None
    # Greek: '.' groups thousands, ',' marks decimals — '3.145 EUR' is 3145.
    try:
        return Decimal(m.group(1).replace(".", "").replace(",", ".")), m.group(2)
    except InvalidOperation:
        return None, None


def parse_amount(value: str | None) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(value, upper bound or None, currency). '<low>/<high>' is a range; equal
    halves are a point estimate written twice."""
    if not value or not str(value).strip():
        return None, None, None
    halves = str(value).split("/")
    lo, cur = _one_amount(halves[0])
    hi, cur2 = _one_amount(halves[1]) if len(halves) > 1 else (None, None)
    cur = cur or cur2
    if lo is None:
        return hi, None, cur
    if hi is None or hi <= lo:
        return lo, None, cur
    return lo, hi, cur


def parse_date(value: str | None, today: dt.date | None = None) -> dt.datetime | None:
    """'dd.MM.yy', 'dd.MM.yy HH:mm' or ISO. A year outside 2000..today+10 is
    refused: hand-entered archive records carry values like '12.11.55' for a
    notice that expired in 2020, which a 2000-2099 window reads as 2055."""
    s = (value or "").strip()
    if not s:
        return None
    today = today or dt.date.today()
    m = _DMY.match(s)
    if m:
        year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        parts = (year, int(m.group(2)), int(m.group(1)), m.group(4), m.group(5), m.group(6))
    else:
        m = _ISO.match(s)
        if not m:
            return None
        parts = (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5), m.group(6))
    year, month, day, hh, mm, ss = parts
    if year < 2000 or year > today.year + 10:
        return None
    try:
        return dt.datetime(year, month, day, int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=ATHENS)
    except ValueError:
        return None


def _bool(value) -> bool | None:
    v = fold(str(value)) if value is not None else ""
    if v in ("true", "1", "yes", "ναι"):
        return True
    if v in ("false", "0", "no", "οχι"):
        return False
    return None


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    """Stripped, entity-decoded ('&amp;' arrives literally in titles), or None."""
    s = html.unescape(str(value or "")).strip()
    return s or None


def _lines(value) -> list[str]:
    return [p.strip() for p in re.split(r"[\r\n]+", str(value or "")) if p.strip()]


def _winner_name(raw: str) -> str:
    s = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    # Joined in the order given; the parts are not relabelled, because the feed
    # does not say which is which. A company name with ', ' is left alone.
    if _PERSON.match(s) and ", " not in s:
        s = " ".join(p.strip() for p in s.split(",") if p.strip())
    return s


def winners(r: dict) -> tuple[list[dict], list[str]]:
    """([{name, vat, price}], issues), one entry per COMPANY, not per line.

    contractors and contractorPrices are newline-separated, one line per award
    within the notice, so a company that won several lots is written several
    times; it is folded to one winner whose price is the sum of its lines.
    Prices are only paired with names when the line counts agree — pairing by
    position otherwise hands one company another's money.

    The tax number is a single value however many winners the notice names, so
    it belongs to a winner only when there is exactly one."""
    issues: list[str] = []
    names, prices = _lines(r.get("contractors")), _lines(r.get("contractorPrices"))
    if not names:
        return [], issues
    priced = len(prices) == len(names)
    if prices and not priced:
        issues.append(f"contractorPrices lines {len(prices)} != contractors lines {len(names)}")
    companies: list[dict] = []
    by_key: dict[str, dict] = {}
    for i, line in enumerate(names):
        name = _winner_name(line)
        company = by_key.get(fold(name))
        if company is None:
            company = by_key[fold(name)] = {"name": name, "vat": None, "price": None, "_prices": []}
            companies.append(company)
        if priced:
            company["_prices"].append(parse_amount(prices[i])[0])
    for company in companies:
        shares = company.pop("_prices")
        if shares and all(s is not None for s in shares):
            company["price"] = sum(shares, Decimal(0))
    tax = str(r.get("contractorStatisticalOrTaxNumber") or r.get("contractorVatUid") or "").strip()
    if tax:
        if len(companies) > 1:
            issues.append("joint award: one tax number for several winners, not attached")
        elif _VAT_GR.match(tax):
            companies[0]["vat"] = tax
        else:
            issues.append(f"contractor tax number {tax!r} is not a 9-digit ΑΦΜ")
    return companies, issues


def payload(r: dict) -> dict:
    """The record without the probe's bookkeeping keys ('_feed')."""
    return {k: v for k, v in r.items() if not str(k).startswith("_")}


def content_hash(r: dict) -> str:
    body = json.dumps(payload(r), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def map_record(r: dict, today: dt.date | None = None) -> tuple[dict, dict]:
    """(procurement_act columns, extras: authority, cpvs, winners, held_keys, issues)."""
    issues: list[str] = []
    internal = str(r.get("internalID") or "").strip()
    act_type, known = type_of(r.get("typeOfDocument"))
    if not known:
        issues.append(f"unknown typeOfDocument {r.get('typeOfDocument')!r}")

    title = _text(r.get("title")) or _text(r.get("contentDescription"))
    desc = _text(r.get("contentDescription"))
    est, est_max, est_cur = parse_amount(r.get("estimatedPrices") or r.get("estimatedPricesBelow"))
    value, _, value_cur = parse_amount(r.get("contractValue"))
    wins, win_issues = winners(r)
    issues += win_issues
    if value is None:
        # Older awards carry the awarded value only per winner.
        shares = [w["price"] for w in wins]
        if shares and all(s is not None for s in shares):
            value = sum(shares, Decimal(0))
            value_cur = parse_amount(_lines(r.get("contractorPrices"))[0])[2]
    html_text, text = import_full_text(r.get("tenderText"))
    nuts = next(iter((r.get("nutsCodes") or "").split()), None)
    url = str(r.get("sourceUrl") or "").strip()

    for field in ("publicationDate", "deadlineDate"):
        if r.get(field) and parse_date(r.get(field), today) is None:
            issues.append(f"unreadable {field} {r.get(field)!r}")

    cols = {
        "adam": PREFIX + internal,
        "type": act_type,
        "data_source": "tsg",
        "origin": "import",
        "external_id": r.get("externalId"),
        "source_uuid": internal,
        "reference_number": r.get("referenceNumber"),
        "authority_reference": r.get("authorityReference") or None,
        "journal_number": r.get("journalNumber") or None,
        "source_url": url if url.startswith("http") else None,
        "title": title,
        "short_description": desc if desc and desc != title else None,
        "submission_date": parse_date(r.get("publicationDate"), today),
        "final_submission_date": parse_date(r.get("deadlineDate"), today),
        "budget": est,
        "total_cost_without_vat": est,
        "estimated_price_min": est,
        "estimated_price_max": est_max,
        "contract_value": value,
        "currency_code": est_cur or value_cur or ("EUR" if est is not None or value is not None else None),
        "nuts_code": nuts[:8] if nuts else None,
        "country": "GR",
        "city": r.get("realisationAddressesCity"),
        "type_of_document": r.get("typeOfDocument"),
        "subtype_of_document": r.get("subTypeOfDocument"),
        "nature_of_contract": r.get("natureOfContract"),
        "procedure_label": r.get("procedure"),
        "source_status": r.get("status"),
        "divided_into_lots": _bool(r.get("divisionIntoLots")),
        "is_framework_agreement": _bool(r.get("frameworkAgreement")),
        "number_of_offers": _int(r.get("numberOfOffers")),
        "full_text": text,
        "full_text_html": html_text,
        "full_text_source": "tsg" if text else None,
    }
    orgdb = (r.get("authorityOrgdbId") or "").strip()
    orgdb = orgdb[:-2] if re.fullmatch(r"\d+\.0", orgdb) else orgdb
    extras = {
        "authority": {
            "name": _text(r.get("authorities")),
            "orgdb_id": orgdb or None,
            "identifier": r.get("authorityIdentifier"),
        },
        "cpvs": [c.split("-")[0][:8] for c in (r.get("cpvCodes") or "").split() if c[:8].isdigit()],
        "winners": wins,
        "held_keys": held_keys(r),
        "issues": issues,
        "raw_chars": len(r.get("tenderText") or ""),
    }
    return cols, extras


def window_params(kind: str, day: dt.date) -> dict:
    """The query for one slice of one day. `_to` is exclusive, so it is the next day."""
    field = "publicationDate" if kind.startswith("pub:") else "lastUpdated"
    return {**SLICES[kind],
            f"{field}_from": day.strftime(DATE_FMT),
            f"{field}_to": (day + dt.timedelta(days=1)).strftime(DATE_FMT)}


def database_allowed(dsn: str | None, env: dict | None = None) -> bool:
    """A database on this machine, or TSG_INGEST_REMOTE switched on. Fails
    towards local-only: anything but an explicit yes keeps a remote refused."""
    env = os.environ if env is None else env
    if dsn and (urlparse(dsn).hostname or "") in LOCAL_HOSTS:
        return True
    return str(env.get("TSG_INGEST_REMOTE") or "").strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class ApiError(RuntimeError):
    def __init__(self, status: int | None, body: str, message: str | None = None):
        self.status, self.body = status, body or ""
        super().__init__(message or f"{status}: {self.body[:200]}")


class QuotaExhausted(RuntimeError):
    """The key's daily cap, or this run's own request budget. Stops the run."""


class OverCap(RuntimeError):
    def __init__(self, total: int):
        self.total = total
        super().__init__(f"{total} records — over the {OFFSET_CAP} offset cap")


class TsgClient:
    def __init__(self, key: str, base: str = PROD_BASE, max_requests: int = DEFAULT_MAX_REQUESTS,
                 session=None, sleep=time.sleep):
        self.key, self.base, self.max_requests = key, base, max_requests
        self.session = session or requests.Session()
        self.sleep = sleep
        self.spent = 0
        self.page_size = START_PAGE_SIZE
        self.remaining: int | None = None
        self.reset: int | None = None

    def _get(self, path: str, params: dict):
        if self.remaining is not None and self.remaining <= RATE_LIMIT_FLOOR:
            wait = max(1, self.reset or 60)
            print(f"  [tsg] rate limit low ({self.remaining} left); waiting {wait}s", flush=True)
            self.sleep(wait)
            self.remaining = None
        params = {k: v for k, v in params.items() if v not in (None, "")}
        last = "no attempt"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.spent >= self.max_requests:
                raise QuotaExhausted(f"this run's budget of {self.max_requests} requests is spent")
            self.spent += 1
            try:
                # The key goes in a header, never in a URL that could be logged.
                resp = self.session.get(self.base + path, params=params, timeout=TIMEOUT,
                                        headers={"X-API-Key": self.key, "Accept": "application/json"})
            except requests.RequestException as e:
                last = type(e).__name__
            else:
                self.remaining, self.reset = parse_rate_limit(resp.headers.get("Rate-Limit-Remaining"))
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        last = "200 with a body that is not JSON"
                else:
                    reason = resp.headers.get("x-error-message") or ""
                    text = resp.text or ""
                    # Signalled only by the body; Rate-Limit-Remaining still
                    # reports hundreds left when it happens.
                    if "customer_api_limit_reached" in (reason + text).lower():
                        raise QuotaExhausted("the key's daily API limit is reached (customer_api_limit_reached)")
                    if resp.status_code != 429 and resp.status_code < 500:
                        raise ApiError(resp.status_code, text,
                                       f"{resp.status_code} on {path}: {(reason or text)[:200]}")
                    last = f"{resp.status_code} {(reason or text)[:120]}"
            if attempt < MAX_ATTEMPTS:
                self.sleep(2 ** attempt * (0.5 + random.random()))
        raise ApiError(None, last, f"{path} failed after {MAX_ATTEMPTS} attempts: {last}")

    def check_date_format(self) -> str | None:
        body = self._get("/branch/dateFormat", {})
        pattern = (body.get("dateFormat") or body.get("format")) if isinstance(body, dict) else None
        if pattern and pattern != DATE_PATTERN:
            raise ApiError(None, pattern, f"the portal's date pattern is {pattern!r}, not "
                                          f"{DATE_PATTERN!r} — request dates would name the wrong day")
        return pattern

    def page(self, params: dict, offset: int) -> tuple[list[dict], int | None]:
        q = {**params, "format": "json", "max": self.page_size, "offset": offset}
        try:
            body = self._get("/branch/tenders", q)
        except ApiError as e:
            # The server names its real page limit in its 400. Once, for that message only.
            permitted = parse_max_from_error(e.body)
            if e.status != 400 or permitted in (None, self.page_size):
                raise
            self.page_size = permitted
            body = self._get("/branch/tenders", {**q, "max": permitted})
        return extract_records(body), find_total(extract_meta(body))

    def walk(self, params: dict, on_page) -> tuple[int | None, int]:
        """Every page of one query, handed to on_page as it arrives (so a stop
        mid-walk keeps what was fetched). Returns (reported total, distinct ids)."""
        seen: set[str] = set()
        offset, total = 0, None
        while True:
            recs, reported = self.page(params, offset)
            if offset == 0:
                total = reported
                if total is not None and total > OFFSET_CAP:
                    raise OverCap(total)
            fresh = [r for r in recs if str(r.get("internalID") or "").strip()]
            seen.update(str(r["internalID"]).strip() for r in fresh)
            if fresh:
                on_page(fresh)
            offset += len(recs)
            if len(recs) < self.page_size or (total is not None and offset >= total):
                break
            if offset >= OFFSET_CAP:
                raise OverCap(total or offset)
        return total, len(seen)


# --------------------------------------------------------------------------- #
# storage — proc.tsg_record (authoritative) + proc.tsg_ingest_window
# --------------------------------------------------------------------------- #
def upsert_record(db, r: dict, today: dt.date | None = None) -> str:
    """'new' | 'changed' | 'same' | 'no-id'. An unchanged payload only moves last_seen_at."""
    internal = str(r.get("internalID") or "").strip()
    if not internal:
        return "no-id"
    h = content_hash(r)
    rows = db.query("SELECT content_hash FROM proc.tsg_record WHERE internal_id = %s", (internal,))
    if rows and rows[0][0] == h:
        db.execute("UPDATE proc.tsg_record SET last_seen_at = now() WHERE internal_id = %s", (internal,))
        return "same"
    pub = parse_date(r.get("publicationDate"), today)
    vals = (r.get("typeOfDocument") or None, r.get("status") or None, r.get("dataSource") or None,
            r.get("externalId") or None, r.get("referenceNumber") or None,
            _text(r.get("title")), pub.date() if pub else None, held_keys(r), h, _as_jsonb(payload(r)))
    if not rows:
        db.execute("""INSERT INTO proc.tsg_record
                        (type_label, status, data_source, external_id, reference_number, title,
                         publication_date, held_keys, content_hash, raw_json, internal_id)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s::text[], %s, %s, %s)""",
                   vals + (internal,))
        return "new"
    db.execute("""UPDATE proc.tsg_record
                  SET type_label = %s, status = %s, data_source = %s, external_id = %s,
                      reference_number = %s, title = %s, publication_date = %s,
                      held_keys = %s::text[], content_hash = %s, raw_json = %s,
                      last_seen_at = now(), changed_at = now()
                  WHERE internal_id = %s""", vals + (internal,))
    return "changed"


def _rollback(db) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001 — the connection may already be clean
        pass


def _window_start(db, kind: str, day: dt.date) -> None:
    db.execute("""INSERT INTO proc.tsg_ingest_window (kind, day, status, started_at)
                  VALUES (%s, %s, 'running', now())
                  ON CONFLICT (kind, day) DO UPDATE
                    SET status = 'running', started_at = now(), finished_at = NULL, last_error = NULL""",
               (kind, day))
    db.commit()


def _window_finish(db, kind, day, status, counts, requests_used, *, total=None, fetched=None, error=None):
    db.execute("""UPDATE proc.tsg_ingest_window
                  SET status = %s, total = %s, fetched = %s, new_records = %s, changed_records = %s,
                      requests = %s, last_error = %s, finished_at = now()
                  WHERE kind = %s AND day = %s""",
               (status, total, fetched, counts["new"], counts["changed"], requests_used,
                (error or "")[:500] or None, kind, day))
    db.commit()


def _days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def _summary() -> dict:
    return {"windows": 0, "skipped": 0, "done": 0, "partial": 0, "incomplete": 0,
            "over_cap": 0, "errored": 0, "stored": collections.Counter(), "stopped": None}


def run_windows(db, client, kinds: list[str], days: list[dt.date], *, resume: bool,
                today: dt.date) -> dict:
    """Walk every (day, slice) window. A day that has not ended is 'partial'; a
    walk short of its reported total is 'incomplete'; neither counts as done."""
    s = _summary()
    done: set = set()
    if resume and days:
        done = {(k, d) for k, d in db.query(
            """SELECT kind, day FROM proc.tsg_ingest_window
               WHERE status = 'done' AND kind = ANY(%s) AND day BETWEEN %s AND %s""",
            (list(kinds), days[0], days[-1]))}
    for day in days:
        for kind in kinds:
            s["windows"] += 1
            if (kind, day) in done:
                s["skipped"] += 1
                continue
            _window_start(db, kind, day)
            counts: collections.Counter = collections.Counter()
            spent_before = client.spent

            def store(page, counts=counts):
                for r in page:
                    counts[upsert_record(db, r, today)] += 1
                db.commit()

            try:
                total, fetched = client.walk(window_params(kind, day), store)
            except QuotaExhausted as e:
                _rollback(db)
                _window_finish(db, kind, day, "pending", counts, client.spent - spent_before, error=str(e))
                s["stored"].update(counts)
                s["stopped"] = str(e)
                print(f"[tsg] {kind} {day} stopped: {e}", flush=True)
                return s
            except OverCap as e:
                _rollback(db)
                _window_finish(db, kind, day, "over_cap", counts, client.spent - spent_before,
                               total=e.total, error=f"{e}; needs a narrower slice")
                s["over_cap"] += 1
                print(f"[tsg] {kind} {day} OVER CAP: {e}", flush=True)
                continue
            except (ApiError, requests.RequestException) as e:
                _rollback(db)
                _window_finish(db, kind, day, "error", counts, client.spent - spent_before, error=str(e))
                s["errored"] += 1
                s["stored"].update(counts)
                print(f"[tsg] {kind} {day} ERROR: {e}", flush=True)
                continue
            if total is not None and fetched < total:
                status = "incomplete"
            elif day >= today:
                status = "partial"
            else:
                status = "done"
            _window_finish(db, kind, day, status, counts, client.spent - spent_before,
                           total=total, fetched=fetched)
            s[status] += 1
            s["stored"].update(counts)
            print(f"[tsg] {kind} {day} {status}: total={total} fetched={fetched} "
                  f"new={counts['new']} changed={counts['changed']}", flush=True)
    return s


def watermark(db) -> dt.date | None:
    """The last lastUpdated day BOTH catch-up slices completed."""
    rows = db.query("""SELECT CASE WHEN count(*) = %s THEN min(d) END
                       FROM (SELECT kind, max(day) AS d FROM proc.tsg_ingest_window
                             WHERE kind = ANY(%s) AND status = 'done' GROUP BY kind) m""",
                    (len(UPDATED_SLICES), list(UPDATED_SLICES)))
    return rows[0][0] if rows else None


def backfill(db, client, start: dt.date, end: dt.date, *, resume: bool = True,
             project: bool = True, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    days = list(_days(start, min(end, today)))
    client.check_date_format()
    s = run_windows(db, client, list(PUBLICATION_SLICES), days, resume=resume, today=today)
    s["from"], s["to"] = start, min(end, today)
    if project:
        s["projection"] = project_all(db, today=today, trigger="tsg-backfill")
    s["requests"] = client.spent
    return s


def catchup(db, client, *, start: dt.date | None = None, overlap_days: int = 1,
            project: bool = True, today: dt.date | None = None) -> dict:
    """Records whose lastUpdated falls from (watermark − overlap + 1 day) to today.
    overlap_days=1 re-walks the last completed day. No history: from yesterday."""
    today = today or dt.date.today()
    if start is None:
        wm = watermark(db)
        start = (wm + dt.timedelta(days=1 - max(overlap_days, 0))) if wm else today - dt.timedelta(days=1)
    start = min(start, today)
    days = list(_days(start, today))
    client.check_date_format()
    s = run_windows(db, client, list(UPDATED_SLICES), days, resume=False, today=today)
    s["from"], s["to"] = start, today
    if project:
        s["projection"] = project_all(db, today=today, trigger="tsg-catchup")
    s["requests"] = client.spent
    return s


# --------------------------------------------------------------------------- #
# projection into proc.procurement_act
# --------------------------------------------------------------------------- #
def authority_id(cur, a: dict, cache: dict) -> str | None:
    name = a.get("name")
    if not name:
        return None
    if name in cache:
        return cache[name]
    # Prefer an authority we already know, but only when the name is unambiguous.
    cur.execute("""SELECT org_id FROM proc.authority
                   WHERE proc.f_unaccent(lower(name)) = proc.f_unaccent(lower(%s))
                     AND org_id NOT LIKE %s
                   LIMIT 2""", (name, PREFIX + "%"))
    rows = cur.fetchall()
    if len(rows) == 1:
        org = rows[0][0]
    else:
        key = a.get("orgdb_id") or hashlib.md5(name.lower().encode()).hexdigest()[:16]
        org = PREFIX + key
        cur.execute("""INSERT INTO proc.authority (org_id, name, source, orgdb_id, identifier, country)
                       VALUES (%s, %s, 'tsg', %s, %s, 'GR')
                       ON CONFLICT (org_id) DO NOTHING""",
                    (org, name, a.get("orgdb_id"), a.get("identifier")))
    cache[name] = org
    return org


def nuts_known(cur, code: str, cache: dict) -> bool:
    if code not in cache:
        cur.execute("SELECT 1 FROM proc.nuts_code WHERE nuts_code = %s", (code,))
        cache[code] = cur.fetchone() is not None
    return cache[code]


def upsert_act(cur, cols: dict) -> str | None:
    """'inserted' | 'refreshed' | None when the adam belongs to an authored act.
    ingested_at is set on insert only — a refresh never re-enters a digest window."""
    names = list(cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in names if c not in ("adam", "origin"))
    cur.execute(
        f"""INSERT INTO proc.procurement_act ({", ".join(names)}, full_text_extracted_at, ingested_at)
            VALUES ({", ".join(["%s"] * len(names))}, now(), now())
            ON CONFLICT (adam) DO UPDATE SET {updates}, full_text_extracted_at = now()
            WHERE proc.procurement_act.origin = 'import'
              AND proc.procurement_act.data_source = 'tsg'
            RETURNING (xmax = 0)""",
        [cols[c] for c in names])
    row = cur.fetchone()
    return None if row is None else ("inserted" if row[0] else "refreshed")


def replace_cpvs(cur, adam: str, cols: dict, cpvs: list[str]) -> int:
    cur.execute("DELETE FROM proc.act_object_detail WHERE adam = %s", (adam,))
    if not cpvs:
        return 0
    cur.execute("""INSERT INTO proc.act_object_detail (adam, line_no, short_description, cost_without_vat, currency_code)
                   VALUES (%s, 1, %s, %s, %s) RETURNING id""",
                (adam, cols.get("title"), cols.get("total_cost_without_vat"), cols.get("currency_code")))
    od = cur.fetchone()[0]
    linked = 0
    for code in dict.fromkeys(cpvs):
        cur.execute("SELECT cpv_code FROM proc.cpv_code WHERE left(cpv_code, 8) = %s LIMIT 1", (code,))
        hit = cur.fetchone()
        if hit:
            cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING", (od, hit[0]))
            linked += 1
    return linked


def replace_winners(cur, adam: str, wins: list[dict]) -> int:
    """Winners with an ΑΦΜ → economic_operator + act_operator. An existing
    operator is never renamed: its name came from a registry, ours from a feed."""
    cur.execute("DELETE FROM proc.act_operator WHERE adam = %s AND role = 'winner'", (adam,))
    linked = 0
    for w in wins:
        if not w.get("vat"):
            continue
        cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, last_seen)
                       VALUES (%s, %s, now())
                       ON CONFLICT (vat_number) DO UPDATE SET last_seen = now()
                       RETURNING operator_id""", (w["vat"], w.get("name") or "(unknown)"))
        op = cur.fetchone()[0]
        cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                       VALUES (%s, %s, 'winner') ON CONFLICT (adam, operator_id, role) DO NOTHING""",
                    (adam, op))
        linked += 1
    return linked


def _remove_projections(cur, adams: list[str]) -> int:
    """Out of scope only (Cyprus). A DUPLICATE is never removed: it is hidden
    (procurement_act.duplicate_of, tsg_match.apply), because a delete cascades
    through emailed history, reminder ledgers and favourites."""
    cur.execute("""DELETE FROM proc.procurement_act
                   WHERE adam = ANY(%s::text[]) AND data_source = 'tsg' AND origin = 'import'""",
                (adams,))
    return cur.rowcount


def project_record(db, internal: str, r: dict, h: str, caches: dict,
                   today: dt.date | None = None) -> str:
    """Project one record and decide whether it is a tender we already show
    (tsg_match). An exact match seen for the first time is never projected at
    all; everything else is projected, then matched against the acts around it."""
    import tsg_match as tm
    cur, c, run, s = db.cur, caches["dict"], caches["run"], caches["settings"]
    adam = PREFIX + internal
    tm.record_scope(c, internal, r, today)
    if outside_greece(r):
        _remove_projections(cur, [adam])
        cur.execute("""UPDATE proc.tsg_record
                       SET held_adam = NULL, projected_adam = NULL, projected_hash = %s,
                           projection_error = NULL, skip_reason = %s, projected_at = now(),
                           match_outcome = 'out_of_scope', match_rule = NULL, match_tier = NULL,
                           matched_adam = NULL, matched_at = now()
                       WHERE internal_id = %s""", (h, OUTSIDE_GREECE, internal))
        run.note(internal, r, tm.Decision("out_of_scope"), "none→out_of_scope", recheck=False)
        return f"skipped ({OUTSIDE_GREECE})"

    cur.execute("SELECT 1 FROM proc.procurement_act WHERE adam = %s", (adam,))
    exists = cur.fetchone() is not None
    first = tm.decide(c, internal, r, act_exists=exists, s=s, today=today, phase="identity")
    if first.outcome == "hidden" and not exists:
        t = tm.apply(c, internal, r, first, act_exists=False, run_id=run.id)
        cur.execute("""UPDATE proc.tsg_record
                       SET projected_hash = %s, projection_error = NULL, skip_reason = NULL,
                           projected_at = now()
                       WHERE internal_id = %s""", (h, internal))
        run.note(internal, r, first, t, recheck=False)
        return "held"

    cols, extras = map_record(r, today)
    # procurement_act.nuts_code is a foreign key; a code the catalogue does not
    # know is left empty rather than failing the record.
    if cols["nuts_code"] and not nuts_known(cur, cols["nuts_code"], caches["nuts"]):
        cols["nuts_code"] = None
    cols["authority_id"] = authority_id(cur, extras["authority"], caches["authority"])
    cols["raw_json"] = _as_jsonb(payload(r))
    written = upsert_act(cur, cols)
    if written:
        replace_cpvs(cur, adam, cols, extras["cpvs"])
        replace_winners(cur, adam, extras["winners"])
    cur.execute("""UPDATE proc.tsg_record
                   SET projected_adam = %s, projected_hash = %s,
                       projection_error = NULL, skip_reason = NULL, projected_at = now()
                   WHERE internal_id = %s""", (adam if written else None, h, internal))
    if not written:
        return "authored"
    d = first if first.outcome == "hidden" else tm.decide(c, internal, r, act_exists=True, s=s, today=today)
    t = tm.apply(c, internal, r, d, act_exists=True, run_id=run.id)
    run.note(internal, r, d, t, recheck=False)
    caches["recheck"].update(d.recheck)
    return "held" if d.outcome == "hidden" else written


def project_all(db, *, limit: int | None = None, today: dt.date | None = None,
                reproject: bool = False, trigger: str = "tsg-project",
                job_id: int | None = None) -> dict:
    """Project every record whose content changed since its last projection,
    then re-check the answers that can still change (tsg_match.match_open).
    A record that fails is recorded (projection_error) and not retried until its
    content changes, so one bad payload cannot stall the queue.

    reproject=True revisits EVERY stored record — what a mapping or scope rule
    change needs, since unchanged payloads are otherwise never looked at again.

    Returns the projection counters plus 'matching' (tsg_match.Run.finish)."""
    import tsg_match as tm
    today = today or dt.date.today()
    out: collections.Counter = collections.Counter()
    if reproject:
        db.execute("UPDATE proc.tsg_record SET projected_hash = NULL WHERE projected_hash IS NOT NULL")
    c = db.dict_cursor()
    run = tm.Run(c, trigger, job_id)
    db.commit()
    caches: dict = {"authority": {}, "nuts": {}, "dict": c, "run": run,
                    "settings": tm.settings(c), "recheck": set()}
    # Answers that changed because something else arrived (our own ingesters
    # may have brought the twin). Hidden-at-first-sight records that are shown
    # now get their projection hash cleared here and are projected below.
    out["rechecked"] = tm.match_open(c, run, today=today)
    db.commit()
    remaining = limit
    while remaining is None or remaining > 0:
        n = PROJECT_BATCH if remaining is None else min(PROJECT_BATCH, remaining)
        rows = db.query("""SELECT internal_id, raw_json, content_hash FROM proc.tsg_record
                           WHERE projected_hash IS DISTINCT FROM content_hash
                           ORDER BY changed_at, internal_id
                           LIMIT %s""", (n,))
        if not rows:
            break
        for internal, raw, h in rows:
            raw = raw if isinstance(raw, dict) else json.loads(raw)
            db.cur.execute("SAVEPOINT tsg_project")
            try:
                outcome = project_record(db, internal, raw, h, caches, today)
                db.cur.execute("RELEASE SAVEPOINT tsg_project")
            except Exception as e:  # noqa: BLE001 — recorded on the row
                db.cur.execute("ROLLBACK TO SAVEPOINT tsg_project")
                caches["authority"], caches["nuts"] = {}, {}   # may name rows the rollback undid
                db.cur.execute("""UPDATE proc.tsg_record
                                  SET projected_hash = %s, projection_error = %s, projected_at = now()
                                  WHERE internal_id = %s""",
                               (h, f"{type(e).__name__}: {e}"[:500], internal))
                outcome = "error"
            out[outcome] += 1
        db.commit()
        if remaining is not None:
            remaining -= len(rows)
    # A record that outranks a Tender Service twin already shown: the twin is
    # hidden behind it now, not at the twin's next change.
    s = caches["settings"]
    for internal in sorted(caches["recheck"]):
        rows = db.query("SELECT raw_json FROM proc.tsg_record WHERE internal_id = %s", (internal,))
        if rows:
            raw = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
            tm.match_one(c, run, internal, raw, s=s, today=today, recheck=True)
    result = dict(out)
    result["matching"] = run.finish()
    db.commit()
    return result


def format_summary(s: dict) -> str:
    lines = [f"windows={s['windows']} done={s['done']} partial={s['partial']} "
             f"incomplete={s['incomplete']} over_cap={s['over_cap']} errored={s['errored']} "
             f"skipped={s['skipped']}",
             "records: " + (", ".join(f"{k}={v}" for k, v in sorted(s["stored"].items())) or "none")]
    if "requests" in s:
        lines.append(f"requests spent: {s['requests']}")
    if s.get("projection") is not None:
        proj = {k: v for k, v in s["projection"].items() if k != "matching"}
        lines.append("projection: " + ", ".join(f"{k}={v}" for k, v in sorted(proj.items())))
        if s["projection"].get("matching"):
            import tsg_match as tm
            lines.append(tm.format_counts(s["projection"]["matching"]))
    if s.get("stopped"):
        lines.append(f"STOPPED: {s['stopped']} — the window stays pending; re-run to continue.")
    return "\n".join(lines)
