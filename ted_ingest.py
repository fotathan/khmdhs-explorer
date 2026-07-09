"""
ted_ingest.py — TED (EU Tenders Electronic Daily) as a third source.

Closest model is diavgeia_ingest.py, not khmdhs_ingest.py: TED is procedure/lot/
result oriented and doesn't map 1:1 onto the KHMDHS ADAM model, so we store
source-native rows (proc.ted_notice) authoritatively and project a DIGEST into
proc.procurement_act (data_source='ted', adam='TED:<publication-number>').

v1: Search API metadata only (POST /v3/notices/search, ITERATION mode — no 15k
retrieval cap, stable snapshot), Greece by default, publication-date windows in
proc.ted_ingest_window (resumable). XML parsing / richer lot-result data is a
fast-follow.

Reuses RateLimiter / windows / _as_jsonb from khmdhs_ingest.
"""

from __future__ import annotations

import datetime as dt
import os
import time
import xml.etree.ElementTree as ET

import requests

from khmdhs_ingest import RateLimiter, windows, _as_jsonb

# Opt-in full-text extraction (mirror khmdhs/diavgeia EXTRACT_FULLTEXT). When on,
# the backfill also fetches each notice's eForms XML and stores its description +
# body on proc.ted_notice (projected into procurement_act.full_text).
EXTRACT_FULLTEXT = os.environ.get("EXTRACT_FULLTEXT", "0") == "1"

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_RATE_PER_MIN = int(os.environ.get("TED_RATE_PER_MIN", "60"))
WINDOW_DAYS = int(os.environ.get("TED_WINDOW_DAYS", "30"))
PAGE_LIMIT = int(os.environ.get("TED_PAGE_LIMIT", "250"))
BACKOFF_BASE = 4.0
MAX_RETRIES = 5

# All confirmed-accepted Search API fields (live-probed).
FIELDS = [
    "publication-number", "notice-identifier", "procedure-identifier",
    "notice-type", "procedure-type", "publication-date",
    "notice-title", "buyer-name", "buyer-country",
    "classification-cpv", "main-classification-proc", "estimated-value-proc",
    "winner-name", "winner-identifier", "contract-conclusion-date", "links",
]


# --------------------------------------------------------------------------- #
# value helpers — TED fields are often multilingual objects and/or lists
# --------------------------------------------------------------------------- #
def _first(v):
    """First scalar of a list-or-scalar (None-safe)."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _ml(v):
    """Resolve a multilingual value to one string: ell → eng → any; each value
    may itself be a str or a list."""
    if v is None:
        return None
    if isinstance(v, dict):
        for lang in ("ell", "eng"):
            if v.get(lang):
                return _ml(v[lang])
        for val in v.values():          # any remaining language
            if val:
                return _ml(val)
        return None
    if isinstance(v, list):
        for el in v:
            r = _ml(el)
            if r:
                return r
        return None
    s = str(v).strip()
    return s or None


def _num(v):
    v = _first(v)
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _date(v):
    """Parse a TED date like '2026-06-01+02:00' → date (take the date part)."""
    v = _first(v)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _cpvs(v):
    out, seen = [], set()
    for c in (v or []):
        c = (str(c).strip() if c is not None else "")
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _links(raw):
    lk = raw.get("links") or {}
    def pick(group, *langs):
        g = lk.get(group) or {}
        for L in langs:
            if g.get(L):
                return g[L]
        return next(iter(g.values()), None) if g else None
    return (pick("xml", "MUL"),
            pick("html", "ELL", "ENG") or pick("htmlDirect", "ELL", "ENG"),
            pick("pdf", "ELL", "ENG"))


def map_notice(raw: dict) -> dict | None:
    pub = raw.get("publication-number")
    if not pub:
        return None
    xml_url, html_url, pdf_url = _links(raw)
    return {
        "publication_number": pub,
        "notice_identifier": _first(raw.get("notice-identifier")),
        "procedure_identifier": _first(raw.get("procedure-identifier")),
        "notice_type": _first(raw.get("notice-type")),
        "procedure_type": _first(raw.get("procedure-type")),
        "publication_date": _date(raw.get("publication-date")),
        "title": _ml(raw.get("notice-title")),
        "buyer_name": _ml(raw.get("buyer-name")),
        "buyer_country": _first(raw.get("buyer-country")),
        "estimated_value": _num(raw.get("estimated-value-proc")),
        "currency": None,
        "winner_name": _ml(raw.get("winner-name")),
        "winner_identifier": _first(raw.get("winner-identifier")),
        "contract_conclusion_date": _date(raw.get("contract-conclusion-date")),
        "xml_url": xml_url, "html_url": html_url, "pdf_url": pdf_url,
        "cpvs": _cpvs(raw.get("classification-cpv")),
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class TedClient:
    def __init__(self):
        self.session = requests.Session()
        self.limiter = RateLimiter(per_minute=TED_RATE_PER_MIN)

    def _post(self, body: dict) -> dict:
        for attempt in range(MAX_RETRIES):
            self.limiter.wait()
            try:
                r = self.session.post(TED_SEARCH_URL, json=body, timeout=60,
                                      headers={"Accept": "application/json"})
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(BACKOFF_BASE * (2 ** attempt))
        return {}

    def get_xml(self, url: str) -> str:
        """GET a notice XML document (rate-limited, retried)."""
        for attempt in range(MAX_RETRIES):
            self.limiter.wait()
            try:
                r = self.session.get(url, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.text
            except requests.RequestException:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(BACKOFF_BASE * (2 ** attempt))
        return ""

    def iterate(self, query: str):
        """Yield every notice matching `query` via ITERATION mode (no 15k cap)."""
        token = None
        while True:
            body = {"query": query, "fields": FIELDS, "limit": PAGE_LIMIT,
                    "paginationMode": "ITERATION"}
            if token:
                body["iterationNextToken"] = token
            data = self._post(body)
            notices = data.get("notices") or []
            if not notices:
                break
            for n in notices:
                yield n
            token = data.get("iterationNextToken")
            if not token:
                break


def _query(country: str, date_from: dt.date, date_to: dt.date) -> str:
    return (f"buyer-country={country} "
            f"AND publication-date>={date_from:%Y%m%d} "
            f"AND publication-date<={date_to:%Y%m%d}")


# --------------------------------------------------------------------------- #
# full text — parse the eForms/UBL XML (stdlib, namespace-agnostic)
# --------------------------------------------------------------------------- #
def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_fulltext(xml_text: str):
    """(summary, full_text) from a TED notice XML. summary = the top-level
    ProcurementProject's Greek description (Συνοπτική Παρουσίαση); full_text =
    every Greek (ELL) Description/Note joined, summary first."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None
    summary = None
    for pp in root.findall("{*}ProcurementProject"):     # top-level, not lots
        for d in pp.iter():
            if _localname(d.tag) == "Description" and d.get("languageID") == "ELL" \
                    and (d.text or "").strip():
                summary = d.text.strip()
                break
        if summary:
            break
    seen, parts = set(), []
    if summary:
        parts.append(summary)
        seen.add(summary)
    for el in root.iter():
        if _localname(el.tag) in ("Description", "Note") and el.get("languageID") == "ELL":
            t = (el.text or "").strip()
            if t and t not in seen:
                seen.add(t)
                parts.append(t)
    return summary, ("\n\n".join(parts) if parts else None)


def fetch_fulltext(client, xml_url: str):
    """Fetch + parse a notice's full text; (None, None) on any failure."""
    if not xml_url:
        return None, None
    try:
        return parse_fulltext(client.get_xml(xml_url))
    except Exception:      # noqa: BLE001 — treat as "tried, empty"
        return None, None


# --------------------------------------------------------------------------- #
# repository
# --------------------------------------------------------------------------- #
class TedRepository:
    def __init__(self, db):
        self.db = db

    def upsert_notice(self, n: dict) -> None:
        db = self.db
        db.execute("""
            INSERT INTO proc.ted_notice
              (publication_number, notice_identifier, procedure_identifier,
               notice_type, procedure_type, publication_date, title,
               buyer_name, buyer_country, estimated_value, currency,
               winner_name, winner_identifier, contract_conclusion_date,
               xml_url, html_url, pdf_url, raw_json, ingested_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (publication_number) DO UPDATE SET
               notice_identifier=EXCLUDED.notice_identifier,
               procedure_identifier=EXCLUDED.procedure_identifier,
               notice_type=EXCLUDED.notice_type, procedure_type=EXCLUDED.procedure_type,
               publication_date=EXCLUDED.publication_date, title=EXCLUDED.title,
               buyer_name=EXCLUDED.buyer_name, buyer_country=EXCLUDED.buyer_country,
               estimated_value=EXCLUDED.estimated_value, currency=EXCLUDED.currency,
               winner_name=EXCLUDED.winner_name, winner_identifier=EXCLUDED.winner_identifier,
               contract_conclusion_date=EXCLUDED.contract_conclusion_date,
               xml_url=EXCLUDED.xml_url, html_url=EXCLUDED.html_url, pdf_url=EXCLUDED.pdf_url,
               raw_json=EXCLUDED.raw_json, ingested_at=now()
        """, (n["publication_number"], n["notice_identifier"], n["procedure_identifier"],
              n["notice_type"], n["procedure_type"], n["publication_date"], n["title"],
              n["buyer_name"], n["buyer_country"], n["estimated_value"], n["currency"],
              n["winner_name"], n["winner_identifier"], n["contract_conclusion_date"],
              n["xml_url"], n["html_url"], n["pdf_url"], _as_jsonb(n["raw"])))
        db.execute("DELETE FROM proc.ted_notice_cpv WHERE publication_number=%s",
                   (n["publication_number"],))
        for i, cpv in enumerate(n["cpvs"], 1):
            db.execute("""INSERT INTO proc.ted_notice_cpv (publication_number, cpv_code, ord)
                          VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                       (n["publication_number"], cpv[:10], i))

    def set_fulltext(self, pub, summary, full_text):
        """Store extracted text (or mark 'tried' with NULLs so re-runs skip)."""
        self.db.execute("""UPDATE proc.ted_notice
                           SET description=%s, full_text=%s, full_text_extracted_at=now()
                           WHERE publication_number=%s""", (summary, full_text, pub))


# --------------------------------------------------------------------------- #
# orchestration — windowed, resumable (mirrors diavgeia ingest_type)
# --------------------------------------------------------------------------- #
def _done_windows(db, country):
    rows = db.query("""SELECT date_from, date_to FROM proc.ted_ingest_window
                       WHERE country=%s AND status='done'""", (country,))
    return {(r[0], r[1]) for r in rows}


def watermark(db, country):
    rows = db.query("""SELECT max(date_to) FROM proc.ted_ingest_window
                       WHERE country=%s AND status='done'""", (country,))
    return rows[0][0] if rows and rows[0][0] else None


def fulltext_pass(client, repo, limit=5000):
    """Fetch + store full text for notices ALREADY imported without it (never
    tried: full_text NULL and full_text_extracted_at NULL). Bounded, resumable."""
    db = repo.db
    rows = db.query("""SELECT publication_number, xml_url FROM proc.ted_notice
                       WHERE full_text IS NULL AND full_text_extracted_at IS NULL
                         AND xml_url IS NOT NULL
                       ORDER BY publication_date DESC NULLS LAST
                       LIMIT %s""", (int(limit),))
    n = {"seen": 0, "extracted": 0, "empty": 0}
    for pub, xml_url in rows:
        n["seen"] += 1
        summary, ft = fetch_fulltext(client, xml_url)
        repo.set_fulltext(pub, summary, ft)
        n["extracted" if ft else "empty"] += 1
        db.commit()
    return n


def ingest_country(client, repo, country, start, end, *, resume=True):
    db = repo.db
    summary = {"windows": 0, "notices": 0, "done": 0, "skipped": 0, "errored": 0}
    all_windows = list(windows(start, end, size_days=WINDOW_DAYS))
    summary["windows"] = len(all_windows)
    done = _done_windows(db, country) if resume else set()

    for w_from, w_to in all_windows:
        db.execute("""INSERT INTO proc.ted_ingest_window (country, date_from, date_to, status)
                      VALUES (%s,%s,%s,'pending')
                      ON CONFLICT (country, date_from, date_to) DO NOTHING""",
                   (country, w_from, w_to))
        db.commit()
        if (w_from, w_to) in done:
            summary["skipped"] += 1
            print(f"[TED {country}] {w_from}..{w_to} SKIPPED (done)")
            continue
        db.execute("""UPDATE proc.ted_ingest_window SET status='running', started_at=now(),
                      last_error=NULL WHERE country=%s AND date_from=%s AND date_to=%s""",
                   (country, w_from, w_to))
        db.commit()
        n = 0
        try:
            for raw in client.iterate(_query(country, w_from, w_to)):
                m = map_notice(raw)
                if m:
                    repo.upsert_notice(m)
                    if EXTRACT_FULLTEXT and m.get("xml_url"):
                        ft_summary, ft_body = fetch_fulltext(client, m["xml_url"])
                        repo.set_fulltext(m["publication_number"], ft_summary, ft_body)
                    n += 1
            db.execute("""UPDATE proc.ted_ingest_window SET status='done', notices=%s,
                          finished_at=now() WHERE country=%s AND date_from=%s AND date_to=%s""",
                       (n, country, w_from, w_to))
            db.commit()
            summary["done"] += 1
            summary["notices"] += n
            print(f"[TED {country}] {w_from}..{w_to} done ({n} notices)")
        except Exception as err:            # noqa: BLE001 — record + continue
            try:
                db.rollback()
            except Exception:
                pass
            db.execute("""UPDATE proc.ted_ingest_window SET status='error', last_error=%s,
                          finished_at=now() WHERE country=%s AND date_from=%s AND date_to=%s""",
                       (str(err)[:500], country, w_from, w_to))
            db.commit()
            summary["errored"] += 1
            print(f"[TED {country}] {w_from}..{w_to} ERROR: {err}")
    return summary


# --------------------------------------------------------------------------- #
# projection into proc.procurement_act (digest) — mirrors diavgeia project_all
# --------------------------------------------------------------------------- #
# TED notice-type prefix → app act_type. TED has no payment/auction concept.
# (left()/= instead of LIKE '..%' so the no-param query has no literal '%',
# which psycopg would misparse as a placeholder.)
_PROJECT_TYPE_SQL = """
    CASE
      WHEN left(t.notice_type, 3) = 'pin' THEN 'request'
      WHEN left(t.notice_type, 3) = 'can' THEN 'contract'
      ELSE 'notice'
    END"""


def project_all(db) -> int:
    """Project TED notices into proc.procurement_act (+ synthetic authorities +
    CPV line items) so the app surfaces them. Idempotent; never touches an act a
    curator authored (origin='authored')."""
    # 1. Synthetic authority per distinct TED buyer (no ΑΦΜ in the search API;
    #    cross-source dedup is a fast-follow). Stable org_id keyed by name.
    db.execute("""
        INSERT INTO proc.authority (org_id, name)
        SELECT DISTINCT 'TED:'||left(md5(lower(t.buyer_name)), 16), t.buyer_name
        FROM proc.ted_notice t
        WHERE t.buyer_name IS NOT NULL
        ON CONFLICT (org_id) DO NOTHING
    """)
    db.commit()

    # 2. Header rows.
    db.execute(f"""
        INSERT INTO proc.procurement_act
          (adam, type, data_source, origin, external_id, title, submission_date,
           signed_date, budget, total_cost_without_vat, source_url, authority_id,
           full_text, full_text_source, full_text_extracted_at,
           raw_json, ingested_at)
        SELECT 'TED:'||t.publication_number, ({_PROJECT_TYPE_SQL})::proc.act_type,
               'ted', 'import', t.publication_number, t.title, t.publication_date,
               t.contract_conclusion_date, t.estimated_value, t.estimated_value,
               t.html_url,
               CASE WHEN t.buyer_name IS NOT NULL
                    THEN 'TED:'||left(md5(lower(t.buyer_name)), 16) END,
               t.full_text,
               CASE WHEN t.full_text IS NOT NULL THEN 'ted' END,
               t.full_text_extracted_at,
               t.raw_json, now()
        FROM proc.ted_notice t
        ON CONFLICT (adam) DO UPDATE SET
           type=EXCLUDED.type, data_source=EXCLUDED.data_source, title=EXCLUDED.title,
           submission_date=EXCLUDED.submission_date, signed_date=EXCLUDED.signed_date,
           budget=EXCLUDED.budget, total_cost_without_vat=EXCLUDED.total_cost_without_vat,
           source_url=EXCLUDED.source_url, authority_id=EXCLUDED.authority_id,
           full_text=COALESCE(EXCLUDED.full_text, proc.procurement_act.full_text),
           full_text_source=COALESCE(EXCLUDED.full_text_source, proc.procurement_act.full_text_source),
           full_text_extracted_at=COALESCE(EXCLUDED.full_text_extracted_at, proc.procurement_act.full_text_extracted_at),
           raw_json=EXCLUDED.raw_json, ingested_at=now()
        WHERE proc.procurement_act.origin <> 'authored'
    """)
    db.commit()

    # 3. Synthetic line item carrying the CPVs (so CPV/category filters find TED
    #    acts), guarded to CPVs known to proc.cpv_code. Delete+reinsert = idempotent.
    ted_adams = "SELECT 'TED:'||publication_number FROM proc.ted_notice"
    db.execute(f"""DELETE FROM proc.act_object_detail
                   WHERE adam IN ({ted_adams})
                     AND adam NOT IN (SELECT adam FROM proc.procurement_act WHERE origin='authored')""")
    db.execute(f"""
        INSERT INTO proc.act_object_detail (adam, line_no, short_description, cost_without_vat)
        SELECT 'TED:'||t.publication_number, 1, t.title, t.estimated_value
        FROM proc.ted_notice t
        WHERE EXISTS (SELECT 1 FROM proc.ted_notice_cpv c
                      WHERE c.publication_number=t.publication_number)
          AND 'TED:'||t.publication_number NOT IN
              (SELECT adam FROM proc.procurement_act WHERE origin='authored')
    """)
    # TED CPVs are 8-digit ('33111730'); the catalog stores the 10-char form
    # with the check digit ('33111730-7'). Match on the 8-digit prefix and store
    # the catalog code (which object_detail_cpv references).
    db.execute("""
        INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
        SELECT DISTINCT od.id, cc.cpv_code
        FROM proc.act_object_detail od
        JOIN proc.ted_notice_cpv tc ON 'TED:'||tc.publication_number = od.adam
        JOIN LATERAL (SELECT cpv_code FROM proc.cpv_code
                      WHERE left(cpv_code, 8) = tc.cpv_code LIMIT 1) cc ON true
        WHERE od.line_no = 1 AND left(od.adam, 4) = 'TED:'
        ON CONFLICT DO NOTHING
    """)
    db.commit()

    rows = db.query("SELECT count(*) FROM proc.ted_notice")
    return rows[0][0] if rows else 0
