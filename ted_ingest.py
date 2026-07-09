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


def _txt(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _pick(el, path: str) -> str:
    """First text at `path` under `el`, preferring the Greek (ELL) variant."""
    if el is None:
        return ""
    cands = el.findall(path)
    for c in cands:
        if c.get("languageID") in (None, "ELL") and (c.text or "").strip():
            return c.text.strip()
    for c in cands:
        if (c.text or "").strip():
            return c.text.strip()
    return ""


# eForms code-list → Greek (the handful worth resolving; the rest are noise for
# a searchable blob). CPV/NUTS resolve from proc.cpv_code / proc.nuts_code.
_NATURE = {"supplies": "Αγαθά", "services": "Υπηρεσίες", "works": "Έργα"}
_STATUS = {"selec-w": "Επιλέχθηκε ανάδοχος", "selec-nw": "Δεν επιλέχθηκε ανάδοχος",
           "clos-nw": "Κλειστή, χωρίς ανάδοχο", "open-nw": "Ανοικτή, χωρίς ανάδοχο"}
_UNIT = {"YEAR": "Έτη", "MONTH": "Μήνες", "WEEK": "Εβδομάδες", "DAY": "Ημέρες"}


def load_label_maps(db):
    """CPV (8-digit → Greek) and NUTS (code → Greek) label dicts, loaded once
    per pass so parse_fulltext can render TED's resolved labels."""
    cpv, nuts = {}, {}
    try:
        for k, v in db.query("SELECT left(cpv_code,8), description FROM proc.cpv_code"):
            if k:
                cpv.setdefault(k, v)
    except Exception:      # noqa: BLE001 — labels are best-effort
        pass
    try:
        for k, v in db.query("SELECT nuts_code, label FROM proc.nuts_code"):
            nuts[k] = v
    except Exception:      # noqa: BLE001
        pass
    return cpv, nuts


def parse_fulltext(xml_text: str, cpv_labels: dict | None = None,
                   nuts_labels: dict | None = None):
    """Render (summary, full_text) from a TED eForms/UBL notice XML.

    eForms keeps almost everything in *typed* fields (codes, amounts, party
    names), not prose — so a plain Description/Note scrape captures next to
    nothing. This walks the structured tree (project → lots → results →
    organizations), resolving CPV/NUTS codes to Greek labels, then appends any
    remaining Greek prose so call-for-tenders bodies survive too.

    summary = the Συνοπτική Παρουσίαση header (title + CPV/NUTS/value/nature);
    full_text = that plus per-lot detail, per-lot results, and the organization
    directory (which names every winner) — a searchable mirror of TED's view.
    """
    cpv_labels = cpv_labels or {}
    nuts_labels = nuts_labels or {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None

    def cpv_lbl(c): return cpv_labels.get((c or "")[:8], "")
    def nuts_lbl(c): return nuts_labels.get((c or "").strip(), "")

    def amount(el):
        if el is None or not _txt(el):
            return ""
        return f"{_txt(el)} {el.get('currencyID') or ''}".strip()

    def render_purpose(pp, indent=""):
        out = []
        c = _txt(pp.find(".//{*}MainCommodityClassification/{*}ItemClassificationCode")) \
            or _txt(pp.find(".//{*}ItemClassificationCode"))
        if c:
            out.append(f"{indent}CPV: {c} {cpv_lbl(c)}".rstrip())
        nature = _txt(pp.find(".//{*}ProcurementTypeCode"))
        if nature:
            out.append(f"{indent}Είδος σύμβασης: {_NATURE.get(nature, nature)}")
        nuts = _txt(pp.find(".//{*}RealizedLocation//{*}CountrySubentityCode")) \
            or _txt(pp.find(".//{*}CountrySubentityCode"))
        if nuts:
            out.append(f"{indent}Τόπος (NUTS): {nuts} {nuts_lbl(nuts)}".rstrip())
        v = amount(pp.find(".//{*}EstimatedOverallContractAmount"))
        if v:
            out.append(f"{indent}Εκτιμώμενη αξία (χωρίς ΦΠΑ): {v}")
        dm = pp.find(".//{*}PlannedPeriod/{*}DurationMeasure")
        if dm is not None and _txt(dm):
            unit = _UNIT.get(dm.get("unitCode"), dm.get("unitCode") or "")
            out.append(f"{indent}Διάρκεια: {_txt(dm)} {unit}".rstrip())
        return out

    # header — the top-level ProcurementProject (Συνοπτική Παρουσίαση)
    top = root.find("{*}ProcurementProject")
    header, title = [], ""
    if top is not None:
        title = _pick(top, "{*}Name") or _pick(top, "{*}Description")
        if title:
            header.append(title)
        d = _pick(top, "{*}Description")
        if d and d != title:
            header.append(d)
        header += render_purpose(top)
    summary = "\n".join(header).strip() or None

    # lots
    lot_lines = []
    for lot in root.findall("{*}ProcurementProjectLot"):
        lid = _txt(lot.find("{*}ID"))
        pp = lot.find("{*}ProcurementProject")
        nm = _pick(pp, "{*}Name") if pp is not None else ""
        lot_lines.append(f"\n{lid}: {nm}".rstrip())
        if pp is not None:
            dd = _pick(pp, "{*}Description")
            if dd and dd != nm:
                lot_lines.append("  " + dd)
            lot_lines += render_purpose(pp, indent="  ")
        crit = _pick(lot, ".//{*}AwardingCriterion//{*}Description") \
            or _pick(lot, ".//{*}AwardingCriterion//{*}CalculationExpression")
        if crit:
            lot_lines.append(f"  Κριτήριο ανάθεσης: {crit}")

    # results (award notices)
    res_lines = []
    for lr in root.iter():
        if _localname(lr.tag) != "LotResult":
            continue
        lot_ref = _txt(lr.find(".//{*}TenderLot/{*}ID"))
        status = _txt(lr.find("{*}TenderResultCode"))
        mx = amount(lr.find(".//{*}MaximumValueAmount"))
        parts = []
        if status:
            parts.append(_STATUS.get(status, status))
        if mx:
            parts.append(f"μέγιστη αξία {mx}")
        if parts:
            res_lines.append(f"  {lot_ref or 'Αποτέλεσμα'}: " + " · ".join(parts))
    if res_lines:
        res_lines.insert(0, "\nΑποτελέσματα")

    # organizations (buyer + every winner/tenderer, by name)
    org_lines = []
    for org in root.iter():
        if _localname(org.tag) != "Organization":
            continue
        comp = org.find("{*}Company")
        if comp is None:
            continue
        nm = _pick(comp, ".//{*}PartyName/{*}Name")
        if not nm:
            continue
        bits = [nm]
        cid = _txt(comp.find(".//{*}PartyLegalEntity/{*}CompanyID"))
        if cid:
            bits.append(f"Αρ. καταχ.: {cid}")
        city = _txt(comp.find(".//{*}PostalAddress/{*}CityName"))
        if city:
            bits.append(city)
        email = _txt(comp.find(".//{*}Contact/{*}ElectronicMail"))
        if email:
            bits.append(email)
        org_lines.append("  " + " · ".join(bits))
    if org_lines:
        org_lines.insert(0, "\nΟργανισμοί")

    parts = ([summary] if summary else []) + lot_lines + res_lines + org_lines
    structured = "\n".join(parts).strip()

    # catch-all: any Greek prose Description/Note not already captured, so
    # prose-rich call-for-tenders keep their body text.
    extra = []
    for el in root.iter():
        if _localname(el.tag) in ("Description", "Note") and el.get("languageID") in (None, "ELL"):
            tx = (el.text or "").strip()
            if len(tx) > 40 and tx not in structured and tx not in "\n".join(extra):
                extra.append(tx)
    if extra:
        structured = (structured + "\n\nΠρόσθετες πληροφορίες\n" + "\n".join(extra)).strip()

    return summary, (structured or None)


def fetch_fulltext(client, xml_url: str, cpv_labels: dict | None = None,
                   nuts_labels: dict | None = None):
    """Fetch + parse a notice's full text; (None, None) on any failure."""
    if not xml_url:
        return None, None
    try:
        return parse_fulltext(client.get_xml(xml_url), cpv_labels, nuts_labels)
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
    cpv_labels, nuts_labels = load_label_maps(db)
    n = {"seen": 0, "extracted": 0, "empty": 0}
    for pub, xml_url in rows:
        n["seen"] += 1
        summary, ft = fetch_fulltext(client, xml_url, cpv_labels, nuts_labels)
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
    cpv_labels, nuts_labels = load_label_maps(db) if EXTRACT_FULLTEXT else ({}, {})

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
                        ft_summary, ft_body = fetch_fulltext(
                            client, m["xml_url"], cpv_labels, nuts_labels)
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
