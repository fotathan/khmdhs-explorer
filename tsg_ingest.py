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
# Seconds between two requests. 0 = as fast as the rate limit allows (bursts of
# ~280, then a wait). The single-source trial runs at 4.0: 150 / 10 min, half
# the key's 300 / 10 min, so a long backfill never runs the limit down.
DEFAULT_MIN_INTERVAL = float(os.environ.get("TSG_MIN_INTERVAL", "0") or 0)
# On a 429 we wait at least this long, not the short retry backoff.
TOO_MANY_WAIT = 60
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

# Single-source mode (docs/specs/tender-service-single-source.md §5). One walk per
# day with BOTH statuses, because a bare query means ACTIVE only (a day four weeks
# back loses ~94% of its notices) and two filtered walks miss a record that
# switches status mid-walk. A day over the offset cap is walked again per
# document type. The enum is the API's own: an unknown value is a 400, not
# "ignored", and ACTIVE_AND_EXPIRED was measured to equal ACTIVE + EXPIRED.
STATUS_ALL = "ACTIVE_AND_EXPIRED"
DOCUMENT_TYPES = ("TENDER", "PRIOR_INFORMATION", "CORRECTION_CANCELLATION", "RESULT",
                  "OTHER_INFORMATION", "PROCUREMENT_PLAN", "CORRECTION", "CANCELLATION",
                  "PAYMENT_ORDER", "CONSULTATION", "CONTRACT", "BUDGET", "DECISION", "LOT")
ALL_PUBLICATION = "pub:all"
TYPE_PUBLICATION = {f"pub:type:{t.lower()}": {"status": STATUS_ALL, "typeOfDocument": t}
                    for t in DOCUMENT_TYPES}
SINGLE_SOURCE_SLICES = {ALL_PUBLICATION: {"status": STATUS_ALL}, **TYPE_PUBLICATION}
# A whole publication day with nothing in it is not believed: a renamed status
# value answers 0, and 0 is also what a silently broken filter would look like.
SUSPECT_IF_EMPTY = {ALL_PUBLICATION}

SLICES = {**PUBLICATION_SLICES, **UPDATED_SLICES, **SINGLE_SOURCE_SLICES}

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
# which is what KHMDHS calls 'auction' (ΑΔΑΜ kind AWRD). The keys are the
# labels as the API spells them, folded: «Εντολή πληρωμής» folds to
# 'εντολη πληρωμησ', which a bare 'πληρωμη' key never matched, so payment
# orders were filed as notices. Labels with no type of ours yet (Διόρθωση,
# Ακύρωση, Άλλες Πληροφορίες, Πλάνο προμηθειών, Διαβούλευση) still fall back
# to notice and are counted as unknown (owner, 2026-10-03: decide later).
TYPE_BY_LABEL = {
    "προκηρυξη": "notice",
    "αποτελεσμα": "auction",
    "συμβαση": "contract",
    "εντολη πληρωμησ": "payment",
    "πληρωμη": "payment",
    "αιτημα": "request",
    "προηγουμενεσ πληροφοριεσ": "prior_info",
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


_YES = {"ναι", "yes", "true", "1"}
_NO = {"οχι", "no", "false", "0"}


def _flag(s: str | None) -> bool | None:
    f = fold(s)
    return True if f in _YES else False if f in _NO else None


def _rate(s: str | None) -> Decimal | None:
    try:
        r = Decimal(str(s).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return r if r.is_finite() and 0 <= r <= 100 else None


def net_and_gross(amounts, included, rates, *,
                  unflagged_is_both: bool = False) -> tuple[Decimal | None, Decimal | None]:
    """(without VAT, with VAT), summed over the lines (one per lot).

    Tender Service sends each amount with its own "VAT included?" flag and
    rate (estimatedPricesBelowVatIncluded / …VatRate for an estimate,
    contractVatIncluded / contractVatRate for an award). Measured against the
    same acts in ΚΗΜΔΗΣ (2026-10-03): an estimate flagged «Όχι» equals the
    without-VAT total in 4,021 of 4,056, one flagged «Ναι» the with-VAT total;
    an award flagged NO is the value without VAT (its text says «χωρίς ΦΠΑ»).
    With no flag and no rate the amount is the without-VAT one (other portals:
    16 of 20 matched), except from Diavgeia, which never says: there it is
    both, exactly as our own Diavgeia ingester stores it (unflagged_is_both).

    A line whose VAT cannot be worked out leaves that total EMPTY, never a
    partial sum: a missing value is visible, an understated one is not."""
    net, gross = Decimal(0), Decimal(0)
    if not amounts:
        return None, None
    for i, a in enumerate(amounts):
        if a is None:
            return None, None
        inc = included[i] if i < len(included) else (included[0] if len(included) == 1 else None)
        r = rates[i] if i < len(rates) else (rates[0] if len(rates) == 1 else None)
        if inc is None and not r:
            n, g = a, (a if unflagged_is_both else None)
        elif inc:
            n, g = (a / (1 + r / 100) if r is not None else None), a
        else:
            n, g = a, (a * (1 + r / 100) if r is not None else None)
        net = None if net is None or n is None else net + n
        gross = None if gross is None or g is None else gross + g
    q = Decimal("0.01")
    return (net.quantize(q) if net is not None else None,
            gross.quantize(q) if gross is not None else None)


def estimate_lines(r: dict) -> tuple[list, str | None]:
    """The estimate, one amount per lot line, and its currency."""
    raw = r.get("estimatedPricesBelow") or r.get("estimatedPrices")
    out, cur = [], None
    for line in _lines(raw):
        a, _, c = parse_amount(line)
        out.append(a)
        cur = cur or c
    return out, cur


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
    # Values (net_and_gross). An estimate is what a notice is worth; an award,
    # contract or payment is worth its awarded value, never its estimate
    # (ΚΗΜΔΗΣ puts the awarded amount in total_cost_* for those types too).
    from_diavgeia = "diavgeia" in str(r.get("dataSource") or "")
    est_lines, est_lines_cur = estimate_lines(r)
    est_net, est_gross = net_and_gross(
        est_lines,
        [_flag(x) for x in _lines(r.get("estimatedPricesBelowVatIncluded"))],
        [_rate(x) for x in _lines(r.get("estimatedPricesBelowVatRate"))],
        unflagged_is_both=from_diavgeia)
    if len(est_lines) > 1:
        est, est_max, est_cur = est_net, None, est_lines_cur
    val_net, val_gross = net_and_gross(
        [value] if value is not None else [],
        [_flag(r.get("contractVatIncluded"))] if r.get("contractVatIncluded") else [],
        [_rate(r.get("contractVatRate"))] if r.get("contractVatRate") not in (None, "") else [],
        unflagged_is_both=from_diavgeia)
    if act_type in ("auction", "contract", "payment"):
        net, gross = val_net, val_gross
    else:
        net, gross = est_net, est_gross
    bond = bond_of(r)
    if bond is None and str(r.get("bidBond") or "").strip():
        issues.append(f"bid bond not stored: {r.get('bidBond')!r}")
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
        "budget": est_net if est_net is not None else est,
        "total_cost_without_vat": net,
        "total_cost_with_vat": gross,
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
        "bid_bond_amount": bond,
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
                 session=None, sleep=time.sleep, min_interval: float = DEFAULT_MIN_INTERVAL,
                 clock=time.monotonic):
        self.key, self.base, self.max_requests = key, base, max_requests
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.min_interval = max(0.0, min_interval)
        self._last_sent: float | None = None
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
        too_many = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.spent >= self.max_requests:
                raise QuotaExhausted(f"this run's budget of {self.max_requests} requests is spent")
            self._pace()
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
                    if resp.status_code == 429:
                        # Told to slow down: wait the whole reset, never the short backoff.
                        too_many += 1
                        wait = max(TOO_MANY_WAIT, self.reset or 0)
                        print(f"  [tsg] 429 from the API; waiting {wait}s", flush=True)
                        self.sleep(wait)
                        self.remaining = None
                        continue
            if attempt < MAX_ATTEMPTS:
                self.sleep(2 ** attempt * (0.5 + random.random()))
        if too_many == MAX_ATTEMPTS:
            # Still refused after waiting each time: stop the run, do not keep knocking.
            raise QuotaExhausted(f"the API answered 429 {too_many} times in a row; stopping")
        raise ApiError(None, last, f"{path} failed after {MAX_ATTEMPTS} attempts: {last}")

    def _pace(self) -> None:
        """Keep at least min_interval seconds between two requests."""
        if self.min_interval and self._last_sent is not None:
            wait = self.min_interval - (self.clock() - self._last_sent)
            if wait > 0:
                self.sleep(wait)
        self._last_sent = self.clock()

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
        # min/max, not first/last: the single-source backfill walks newest first.
        done = {(k, d) for k, d in db.query(
            """SELECT kind, day FROM proc.tsg_ingest_window
               WHERE status = 'done' AND kind = ANY(%s) AND day BETWEEN %s AND %s""",
            (list(kinds), min(days), max(days)))}
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
            if kind in SUSPECT_IF_EMPTY and not total and not fetched:
                _window_finish(db, kind, day, "error", counts, client.spent - spent_before,
                               total=total, fetched=fetched,
                               error="no records for a whole day: not believed (a renamed "
                                     "status value also answers 0); walked again next run")
                s["errored"] += 1
                print(f"[tsg] {kind} {day} SUSPECT: no records at all", flush=True)
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


def _windows_by_status(db, kinds, days) -> dict:
    rows = db.query("""SELECT kind, day, status, total FROM proc.tsg_ingest_window
                       WHERE kind = ANY(%s) AND day BETWEEN %s AND %s""",
                    (list(kinds), min(days), max(days)))
    return {(k, d): (st, tot) for k, d, st, tot in rows}


def backfill_single_source(db, client, start: dt.date, end: dt.date, *, resume: bool = True,
                           today: dt.date | None = None) -> dict:
    """The trial's backfill (spec §5a): newest day first, one ACTIVE_AND_EXPIRED
    walk per publication day, and per document type for a day over the offset
    cap. Stores into tsg_record only. Projection is a separate, offline step
    (tsg-project): it can be redone any number of times without one request."""
    today = today or dt.date.today()
    days = sorted(_days(start, min(end, today)), reverse=True)
    out = _summary()
    out["from"], out["to"], out["split_days"], out["type_gap"] = start, min(end, today), 0, {}
    if not days:
        out["requests"] = client.spent
        return out
    client.check_date_format()
    type_kinds = list(TYPE_PUBLICATION)

    def merge(s):
        for k in ("windows", "skipped", "done", "partial", "incomplete", "over_cap", "errored"):
            out[k] += s[k]
        out["stored"].update(s["stored"])
        out["stopped"] = out["stopped"] or s["stopped"]

    for day in days:
        state = _windows_by_status(db, [ALL_PUBLICATION, *type_kinds], [day]) if resume else {}
        whole = state.get((ALL_PUBLICATION, day), (None, None))
        if whole[0] == "done":
            out["windows"] += 1
            out["skipped"] += 1
            continue
        if whole[0] != "over_cap":
            s = run_windows(db, client, [ALL_PUBLICATION], [day], resume=False, today=today)
            merge(s)
            if s["stopped"]:
                break
            whole = _windows_by_status(db, [ALL_PUBLICATION], [day]).get((ALL_PUBLICATION, day), (None, None))
            if whole[0] != "over_cap":
                continue
            # The over-cap window is the day's split marker, not a failure.
            out["over_cap"] -= 1
        out["split_days"] += 1
        s = run_windows(db, client, type_kinds, [day], resume=resume, today=today)
        merge(s)
        if s["stopped"]:
            break
        typed = _windows_by_status(db, type_kinds, [day])
        if all(typed.get((k, day), ("", 0))[0] in ("done", "partial") for k in type_kinds):
            got = sum(t or 0 for _, t in typed.values())
            gap = (whole[1] or 0) - got
            out["type_gap"][day.isoformat()] = gap
            # Records without one of the 14 types cannot be fetched by type; say so.
            db.execute("""UPDATE proc.tsg_ingest_window SET last_error = %s
                          WHERE kind = %s AND day = %s""",
                       (f"split by document type: the types hold {got} of {whole[1]} "
                        f"(gap {gap})", ALL_PUBLICATION, day))
            db.commit()
    out["requests"] = client.spent
    return out


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


def bond_of(r: dict) -> Decimal | None:
    """The record's bid bond as we store it: euro, above zero, else None."""
    bond, _, cur = parse_amount(r.get("bidBond"))
    if bond is None or bond <= 0 or (cur or "EUR") != "EUR":
        return None
    return bond


def sync_bid_bonds(db) -> dict:
    """Copy Tender Service bid bonds onto the acts we show instead of them.

    ΚΗΜΔΗΣ publishes no bid bond, and most bonds Tender Service sends belong to
    a tender we already show from ΚΗΜΔΗΣ, so its record is hidden (or never
    projected) and the bond would be lost. The source is ONLY an exact
    duplicate: match_outcome 'hidden' (an exact number, a twin, or an admin's
    confirm), whose matched_adam is the act we keep. A fuzzy flag never feeds
    a value into another source's act.

    Fill only if empty. Copies that disagree on the amount write nothing
    (counted as 'conflict'). proc.act_bid_bond_fill is the ledger: when the
    match is undone or the amount changes, the act is cleared only while it
    still holds exactly what we wrote, so an admin's own value always survives
    and its ledger row is simply dropped. Runs at the end of project_all; an
    admin confirm/reject on the web is picked up by the next run."""
    rows = db.query("""SELECT t.matched_adam, t.internal_id, t.raw_json
                         FROM proc.tsg_record t
                         JOIN proc.procurement_act p ON p.adam = t.matched_adam
                        WHERE t.match_outcome = 'hidden'
                          AND t.raw_json ? 'bidBond'""")
    found: dict[str, dict[Decimal, list[str]]] = collections.defaultdict(dict)
    for adam, internal, raw in rows:
        raw = raw if isinstance(raw, dict) else json.loads(raw)
        bond = bond_of(raw)
        if bond is not None:
            found[adam].setdefault(bond, []).append(internal)
    want = {adam: next(iter(b.items())) for adam, b in found.items() if len(b) == 1}
    out = {"conflict": len(found) - len(want), "filled": 0, "reverted": 0,
           "released": 0, "kept": 0}

    cur = db.cur
    cur.execute("SELECT f.adam, f.amount, p.bid_bond_amount FROM proc.act_bid_bond_fill f "
                "JOIN proc.procurement_act p ON p.adam = f.adam")
    for adam, written, now in cur.fetchall():
        w = want.get(adam)
        if now is not None and now != written:
            # Someone else's value now (an admin, the act's own source): theirs.
            out["released"] += 1
            want.pop(adam, None)
        elif w and w[0] == written and now == written:
            out["kept"] += 1
            cur.execute("UPDATE proc.act_bid_bond_fill SET internal_ids = %s WHERE adam = %s",
                        (sorted(w[1]), adam))
            continue
        elif now == written:
            # Ours, and no longer wanted at this amount: clear it (the fill
            # below writes the new amount, if there is one).
            cur.execute("UPDATE proc.procurement_act SET bid_bond_amount = NULL WHERE adam = %s",
                        (adam,))
            out["reverted"] += 1
        # now is None: already empty; the fill below writes it again if wanted.
        cur.execute("DELETE FROM proc.act_bid_bond_fill WHERE adam = %s", (adam,))
    for adam, (bond, internals) in want.items():
        cur.execute("""UPDATE proc.procurement_act SET bid_bond_amount = %s
                        WHERE adam = %s AND bid_bond_amount IS NULL""", (bond, adam))
        if cur.rowcount:
            cur.execute("""INSERT INTO proc.act_bid_bond_fill (adam, amount, internal_ids)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (adam) DO UPDATE
                           SET amount = EXCLUDED.amount, internal_ids = EXCLUDED.internal_ids,
                               filled_at = now()""", (adam, bond, sorted(internals)))
            out["filled"] += 1
    db.commit()
    return out


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
    result["bid_bonds"] = sync_bid_bonds(db)
    return result


# --------------------------------------------------------------------------- #
# single-source projection (trial, spec §3) — no duplicate matching: there is
# no second source to be a duplicate of.
# --------------------------------------------------------------------------- #
# The ΑΔΑΜ names its own kind; it decides the act type before any label does.
ADAM_KIND_TYPE = {"REQ": "request", "PROC": "notice", "AWRD": "auction",
                  "SYMV": "contract", "PAY": "payment"}
_ADAM_KIND = re.compile(r"^\d{2}([A-Z]+)\d")


def single_source_key(internal: str, r: dict) -> tuple[str, str]:
    """(our key, which identity it is). The key an act already has in our other
    ingesters, so favourites, alert history and URLs survive (spec §3):
    ΑΔΑΜ → the ΑΔΑΜ; Diavgeia → the bare ΑΔΑ; TED → 'TED:n-yyyy'; else TSG:id."""
    ext = str(r.get("externalId") or "").strip()
    for kind, key in (("adam", adam_of(ext)), ("ada", ada_of(ext)),
                      ("ted", ted_of(ext, r.get("sourceUrl")))):
        if key:
            return key, kind
    return PREFIX + internal, "tsg"


def single_source_type(key: str, kind: str, r: dict) -> tuple[str, bool]:
    """(act type, known). «Προηγούμενες Πληροφορίες» first, whatever the
    number (owner, 2026-10-03: one label, one type; most carry a ΚΗΜΔΗΣ REQ
    ΑΔΑΜ); then the ΑΔΑΜ kind; otherwise the display label."""
    by_label = type_of(r.get("typeOfDocument"))
    if by_label[0] == "prior_info":
        return by_label
    if kind == "adam":
        m = _ADAM_KIND.match(key)
        if m and m.group(1) in ADAM_KIND_TYPE:
            return ADAM_KIND_TYPE[m.group(1)], True
    return type_of(r.get("typeOfDocument"))


def _project_single(cur, internal: str, r: dict, h: str, caches: dict,
                    today: dt.date | None) -> str:
    key, kind = single_source_key(internal, r)
    if outside_greece(r):
        cur.execute("""DELETE FROM proc.procurement_act
                       WHERE adam = (SELECT projected_adam FROM proc.tsg_record WHERE internal_id = %s)
                         AND data_source = 'tsg' AND origin = 'import'""", (internal,))
        cur.execute("""UPDATE proc.tsg_record
                       SET projected_adam = NULL, projected_hash = %s, projection_error = NULL,
                           skip_reason = %s, projected_at = now()
                       WHERE internal_id = %s""", (h, OUTSIDE_GREECE, internal))
        return "out_of_scope"
    # A record whose identity changed (externalId corrected) leaves its old act behind.
    cur.execute("SELECT projected_adam FROM proc.tsg_record WHERE internal_id = %s", (internal,))
    old = cur.fetchone()
    if old and old[0] and old[0] != key:
        cur.execute("""DELETE FROM proc.procurement_act
                       WHERE adam = %s AND data_source = 'tsg' AND origin = 'import'""", (old[0],))
    # Two records claiming one key (a notice published twice, a re-issued
    # Diavgeia decision): the first one projected keeps it, the other is
    # recorded, never silently merged.
    cur.execute("""SELECT internal_id FROM proc.tsg_record
                   WHERE projected_adam = %s AND internal_id <> %s LIMIT 1""", (key, internal))
    holder = cur.fetchone()
    if holder:
        cur.execute("""UPDATE proc.tsg_record
                       SET projected_adam = NULL, projected_hash = %s, projection_error = NULL,
                           skip_reason = %s, projected_at = now()
                       WHERE internal_id = %s""", (h, f"same key {key} as {holder[0]}", internal))
        return "same_key"
    cols, extras = map_record(r, today)
    cols["adam"] = key
    cols["type"], known = single_source_type(key, kind, r)
    if not known:
        caches["unknown_labels"][r.get("typeOfDocument")] += 1
    if cols["nuts_code"] and not nuts_known(cur, cols["nuts_code"], caches["nuts"]):
        cols["nuts_code"] = None
    cols["authority_id"] = authority_id(cur, extras["authority"], caches["authority"])
    cols["raw_json"] = _as_jsonb(payload(r))
    written = upsert_act(cur, cols)
    if written:
        replace_cpvs(cur, key, cols, extras["cpvs"])
        replace_winners(cur, key, extras["winners"])
    cur.execute("""UPDATE proc.tsg_record
                   SET projected_adam = %s, projected_hash = %s, projection_error = NULL,
                       skip_reason = NULL, projected_at = now()
                   WHERE internal_id = %s""", (key if written else None, h, internal))
    if not written:
        return "authored"
    return f"{written}:{kind}"


def refresh_summaries(db) -> str | None:
    """Rebuild the summary views (proc.refresh_analytics) that /authorities,
    /explore and /analytics read: they are snapshots, so a projection is not
    on those pages until this runs. A few seconds on the trial. Returns the
    error, if any; the projection itself has already been committed."""
    try:
        db.execute("SELECT proc.refresh_analytics()")
        db.commit()
        return None
    except Exception as e:  # noqa: BLE001 — reported, never fails the projection
        db.rollback()
        return f"{type(e).__name__}: {e}"


def project_single_source(db, *, limit: int | None = None, today: dt.date | None = None,
                          reproject: bool = False) -> dict:
    """Project every stored record whose content changed since its last
    projection, under the single-source key rule. No API calls. Newest
    publication first, so a key collision is decided the same way every run."""
    today = today or dt.date.today()
    out: collections.Counter = collections.Counter()
    if reproject:
        db.execute("UPDATE proc.tsg_record SET projected_hash = NULL WHERE projected_hash IS NOT NULL")
        db.commit()
    caches: dict = {"authority": {}, "nuts": {}, "unknown_labels": collections.Counter()}
    remaining = limit
    while remaining is None or remaining > 0:
        n = PROJECT_BATCH if remaining is None else min(PROJECT_BATCH, remaining)
        rows = db.query("""SELECT internal_id, raw_json, content_hash FROM proc.tsg_record
                           WHERE projected_hash IS DISTINCT FROM content_hash
                           ORDER BY publication_date DESC NULLS LAST, internal_id
                           LIMIT %s""", (n,))
        if not rows:
            break
        for internal, raw, h in rows:
            raw = raw if isinstance(raw, dict) else json.loads(raw)
            db.cur.execute("SAVEPOINT tsg_single")
            try:
                outcome = _project_single(db.cur, internal, raw, h, caches, today)
                db.cur.execute("RELEASE SAVEPOINT tsg_single")
            except Exception as e:  # noqa: BLE001 — recorded on the row
                db.cur.execute("ROLLBACK TO SAVEPOINT tsg_single")
                caches["authority"], caches["nuts"] = {}, {}
                db.cur.execute("""UPDATE proc.tsg_record
                                  SET projected_hash = %s, projection_error = %s, projected_at = now()
                                  WHERE internal_id = %s""",
                               (h, f"{type(e).__name__}: {e}"[:500], internal))
                outcome = "error"
            out[outcome] += 1
        db.commit()
        if remaining is not None:
            remaining -= len(rows)
    result = dict(out)
    result["unknown_labels"] = dict(caches["unknown_labels"])
    return result


def format_summary(s: dict) -> str:
    lines = [f"windows={s['windows']} done={s['done']} partial={s['partial']} "
             f"incomplete={s['incomplete']} over_cap={s['over_cap']} errored={s['errored']} "
             f"skipped={s['skipped']}",
             "records: " + (", ".join(f"{k}={v}" for k, v in sorted(s["stored"].items())) or "none")]
    if "requests" in s:
        lines.append(f"requests spent: {s['requests']}")
    if s.get("split_days"):
        gaps = {d: g for d, g in s.get("type_gap", {}).items() if g}
        lines.append(f"days split by document type: {s['split_days']}"
                     + (f"; records outside the 14 types: {gaps}" if gaps else ""))
    if s.get("projection") is not None:
        proj = {k: v for k, v in s["projection"].items() if k not in ("matching", "bid_bonds")}
        lines.append("projection: " + ", ".join(f"{k}={v}" for k, v in sorted(proj.items())))
        if s["projection"].get("bid_bonds"):
            lines.append("bid bonds copied onto acts we show: " + ", ".join(
                f"{k}={v}" for k, v in s["projection"]["bid_bonds"].items()))
        if s["projection"].get("matching"):
            import tsg_match as tm
            lines.append(tm.format_counts(s["projection"]["matching"]))
    if s.get("stopped"):
        lines.append(f"STOPPED: {s['stopped']} — the window stays pending; re-run to continue.")
    return "\n".join(lines)
