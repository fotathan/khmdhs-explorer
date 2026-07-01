"""
main.py — FastAPI web app for the Greek procurement database.

What it gives you (v1)
----------------------
  GET  /                     — the search page (notices, with facets)
  GET  /search               — HTMX partial: results table + counters
  GET  /notice/{adam}        — full notice detail + related awards/contracts/payments
  GET  /healthz              — liveness check
  GET  /docs                 — auto-generated OpenAPI docs (also a JSON API)

The same endpoints work as JSON if the client sends Accept: application/json,
so the FastAPI app *is* the API — there's no separate API layer to build.

Run it
------
    pip install fastapi uvicorn jinja2 'psycopg[binary]'
    export DATABASE_URL="postgresql://postgres:pw@localhost:5432/procurement"
    # one-time, to install indexes:
    # docker exec -i khmdhs-pg psql -U postgres -d procurement < app_indexes.sql

    uvicorn app.main:app --reload --port 8000

Then open http://localhost:8000
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import date
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, Query, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from jinja2 import pass_context as _jinja_pass_context


# ---------------------------------------------------------------------------- #
# Config
# ---------------------------------------------------------------------------- #
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Set DATABASE_URL=postgresql://user:pass@host:port/db")

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Put this directory on sys.path so the tender-table extraction modules
# (extractors.py / exporter.py / ocr.py) can keep their flat, bare sibling
# imports — e.g. ocr.py's `from extractors import ...` — and still load when
# main.py is run as the `app` package (uvicorn app.main:app). This keeps those
# three modules byte-identical with the standalone Tender Tables tool, which is
# what lets fixes flow between the two projects by copying files.
import sys as _sys
if APP_DIR not in _sys.path:
    _sys.path.insert(0, APP_DIR)
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

# --- UI i18n (Greek default, English overlay) ------------------------------- #
# A context processor injects `lang` and a bound `t()` into every render, so any
# template can call {{ t("…") }} / {{ x|t }}. Record data is never translated —
# only UI chrome and fixed enum labels. See app/i18n.py.
try:
    from app import i18n as _i18n
except ImportError:  # flat layout (run with --app-dir=app)
    import i18n as _i18n


def _i18n_context(request):
    lang = _i18n.lang_from_request(request)
    return {"lang": lang, "t": (lambda s: _i18n.translate(s, lang))}


templates.context_processors.append(_i18n_context)


@_jinja_pass_context
def _t_filter(ctx, s):
    """{{ x|t }} — translate using the active language from the render context."""
    return _i18n.translate(s, ctx.get("lang", "el"))


templates.env.filters["t"] = _t_filter

# Sanity ceiling for contract values (with VAT). Contracts above this are
# treated as data errors (KHMDHS sometimes inflates values ~1000x) and excluded
# from analytics aggregates — see analytics_exclusion_migration.sql, which holds
# the authoritative copy of this number. Kept in sync here so the UI can badge
# excluded contracts. A contract is also "excluded" if flagged 'suspicious'.
ANALYTICS_VALUE_CEILING = 500_000_000
templates.env.globals["ANALYTICS_VALUE_CEILING"] = ANALYTICS_VALUE_CEILING

# Direct link to the official KHMDHS source document (PDF) for an act. The open
# API exposes each act's PDF at /{segment}/attachment/{ADAM}, addressable by the
# universal ADAM key. Segment matches the act type. This is the authoritative
# source record — the portal's web UI has no per-act permalink, but this does.
KHMDHS_DOC_BASE = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata"
KHMDHS_DOC_SEGMENT = {
    "request":  "request",
    "notice":   "notice",
    "auction":  "auction",
    "contract": "contract",
    "payment":  "payment",
}

def khmdhs_doc_url(act_type: str, adam: str) -> str | None:
    """Official source-PDF URL for an act, or None if the type is unknown."""
    seg = KHMDHS_DOC_SEGMENT.get(act_type)
    if not seg or not adam:
        return None
    return f"{KHMDHS_DOC_BASE}/{seg}/attachment/{adam}"

templates.env.globals["khmdhs_doc_url"] = khmdhs_doc_url


def source_doc_url(act) -> str | None:
    """Source-document URL for an act row, source-aware. Diavgeia (and any other
    non-KHMDHS source) links to its own document via source_url; KHMDHS acts use
    the opendata attachment endpoint."""
    if not act:
        return None
    ds = act.get("data_source")
    if ds and ds != "khmdhs":
        return act.get("source_url") or None
    return khmdhs_doc_url(act.get("act_type") or act.get("type"), act.get("adam"))

templates.env.globals["source_doc_url"] = source_doc_url

# Whether the tender-table extraction feature is mounted (see the /tables
# router registration below). Templates use this to show/hide the act-detail
# "Εξαγωγή πινάκων" button so it never points at a route that isn't there.
templates.env.globals["tables_enabled"] = (
    os.environ.get("TABLES_ENABLED", "1") == "1"
)

# Attachment upload/store + search-inside is LOCAL-ONLY for now (prod is a
# free-tier DB with no room for the raw files). Default OFF. When off, the edit
# hub tab is hidden and build_where emits no attachment clause — so prod never
# references proc.act_attachment (that table is applied to the local DB only).
ATTACHMENTS_ENABLED = os.environ.get("ATTACHMENTS_ENABLED", "0") == "1"
templates.env.globals["attachments_enabled"] = ATTACHMENTS_ENABLED

# --- Single source of truth for act-type display labels (Greek) ------------- #
# Used by every template via the |type_label filter. Internal codes ("notice",
# "auction", …) stay English everywhere they're a contract — URLs, form values,
# query params, DB enum values; ONLY the visible label changes.
TYPE_LABELS = {
    "notice":   "Προκήρυξη",
    "auction":  "Αποτέλεσμα",
    "contract": "Σύμβαση",
    "payment":  "Εντολή Πληρωμής",
    "request":  "Πρωτογενές Αίτημα",
}
# Which act types appear as filter OPTIONS, in display order. 'request' is kept
# in TYPE_LABELS (so any such act still gets a proper label if encountered) but
# excluded here — we don't import that type, so it shouldn't clutter the filter.
# Both the main page and the aggregations page iterate this, so the two stay
# uniform. The label for the "all types" option is also defined once here.
TYPE_FILTER_ORDER = ["notice", "auction", "contract", "payment"]
TYPE_ALL_LABEL = "Όλες οι πράξεις"

# Curated NUTS-2 regions (περιφέρειες) for the geography filter. The data has
# codes at mixed depths (EL, EL3, EL303, EL522…); showing them all is unusable.
# Because the NUTS filter does PREFIX matching, a region code like 'EL52' also
# catches EL521/EL522/… beneath it. These 13 are the standard Greek περιφέρειες
# plus a whole-country option, with stable official names — not derived from the
# messy data, so they're always clean and correct.
NUTS_REGIONS = [
    {"code": "EL30", "label": "Αττική"},
    {"code": "EL51", "label": "Αν. Μακεδονία & Θράκη"},
    {"code": "EL52", "label": "Κεντρική Μακεδονία"},
    {"code": "EL53", "label": "Δυτική Μακεδονία"},
    {"code": "EL54", "label": "Ήπειρος"},
    {"code": "EL61", "label": "Θεσσαλία"},
    {"code": "EL62", "label": "Ιόνια Νησιά"},
    {"code": "EL63", "label": "Δυτική Ελλάδα"},
    {"code": "EL64", "label": "Στερεά Ελλάδα"},
    {"code": "EL65", "label": "Πελοπόννησος"},
    {"code": "EL41", "label": "Βόρειο Αιγαίο"},
    {"code": "EL42", "label": "Νότιο Αιγαίο"},
    {"code": "EL43", "label": "Κρήτη"},
]
templates.env.globals["TYPE_FILTER_ORDER"] = TYPE_FILTER_ORDER
templates.env.globals["TYPE_ALL_LABEL"] = TYPE_ALL_LABEL
# Contract types (contractType) — the kind of object procured.
CONTRACT_TYPES = {
    "9":  "Υπηρεσίες",
    "10": "Έργα",
    "12": "Μελέτες",
    "13": "Προμήθειες",
    "14": "Τεχνικές ή λοιπές συναφείς Υπηρεσίες",
}
# Procedure types (typeOfProcedure) — the procurement method used.
PROCEDURE_TYPES = {
    "1":  "Ανοιχτή διαδικασία",
    "2":  "Κλειστή διαδικασία",
    "4":  "Ανταγωνιστικός διάλογος",
    "6":  "Απευθείας ανάθεση",
    "7":  "Ανταγωνιστική διαδικασία με διαπραγμάτευση",
    "11": "Σύμπραξη καινοτομίας",
    "12": "Διαπραγμάτευση χωρίς προηγούμενη δημοσίευση",
    "13": "Διαπραγμάτευση με προηγούμενη προκήρυξη διαγωνισμού (αρ.266)",
    "18": "Διαδικασία άρθρου 128 του ν.4412/16",
}
# Assignment criteria (assignCriteria / criteria) — basis on which the contract
# is awarded.
ASSIGN_CRITERIA = {
    "1": "Βάσει κόστους — βέλτιστη σχέση ποιότητας τιμής",
    "2": "Βάσει τιμής",
    "3": "Βάσει κόστους — κοστολόγηση κύκλου ζωής",
    "4": "Βάσει τιμής — άλλο",
}
templates.env.globals["TYPE_LABELS"] = TYPE_LABELS
templates.env.globals["CONTRACT_TYPES"] = CONTRACT_TYPES
templates.env.globals["PROCEDURE_TYPES"] = PROCEDURE_TYPES
templates.env.globals["ASSIGN_CRITERIA"] = ASSIGN_CRITERIA
# Enum-label filters are language-aware: they read the active `lang` from the
# render context (injected by _i18n_context) and resolve via i18n.enum_label,
# which falls back Greek -> raw code -> "—". They tolerate ints (a numeric code
# that arrived from JSON as int) — enum_label str()s before lookup.
@_jinja_pass_context
def _type_label(ctx, code):
    return _i18n.enum_label("type", code, TYPE_LABELS, ctx.get("lang", "el"))


@_jinja_pass_context
def _contract_type_label(ctx, code):
    return _i18n.enum_label("contract_type", code, CONTRACT_TYPES, ctx.get("lang", "el"))


@_jinja_pass_context
def _procedure_type_label(ctx, code):
    return _i18n.enum_label("procedure_type", code, PROCEDURE_TYPES, ctx.get("lang", "el"))


@_jinja_pass_context
def _assign_criteria_label(ctx, code):
    return _i18n.enum_label("assign_criteria", code, ASSIGN_CRITERIA, ctx.get("lang", "el"))


templates.env.filters["type_label"] = _type_label
templates.env.filters["contract_type_label"] = _contract_type_label
templates.env.filters["procedure_type_label"] = _procedure_type_label
templates.env.filters["assign_criteria_label"] = _assign_criteria_label


# {{ obj | vname }} — a category/subcategory dict's name in the active language
# (name_en when EN and present, else the Greek name). For the cached filter
# lookups that carry both columns.
@_jinja_pass_context
def _vname(ctx, obj):
    if ctx.get("lang") == "en":
        en = obj.get("name_en") if isinstance(obj, dict) else getattr(obj, "name_en", None)
        if en:
            return en
    return obj.get("name") if isinstance(obj, dict) else obj.name


templates.env.filters["vname"] = _vname


# ---------------------------------------------------------------------------- #
# DB pool (single long-lived connection, dict rows). For higher load you'd swap
# in psycopg_pool; one connection is fine for an internal tool.
# ---------------------------------------------------------------------------- #
_conn: Optional[psycopg.Connection] = None

def conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        # prepare_threshold=None disables psycopg3's automatic prepared
        # statements. Required when connecting through a transaction-mode
        # connection pooler (e.g. Supabase port 6543 / PgBouncer): there,
        # consecutive queries can land on different physical connections, so
        # server-side prepared statements collide ("prepared statement _pg3_N
        # already exists"). Disabling them keeps the app pooler-safe. Harmless
        # against a direct connection (local dev).
        _conn = psycopg.connect(DATABASE_URL, row_factory=dict_row,
                                autocommit=True, prepare_threshold=None)
    return _conn

@contextmanager
def cursor():
    """Yield a cursor on the shared connection. If the connection has gone
    stale (pooler dropped it, or it's in a broken state after an error),
    reconnect once and retry — so a single dead connection doesn't cause a
    run of 500s until the next restart."""
    global _conn
    try:
        with conn().cursor() as cur:
            yield cur
    except psycopg.OperationalError:
        # Connection likely dropped by the pooler; force a fresh one.
        try:
            if _conn is not None and not _conn.closed:
                _conn.close()
        except Exception:
            pass
        _conn = None
        with conn().cursor() as cur:
            yield cur


# ---------------------------------------------------------------------------- #
# Search query builder
# ---------------------------------------------------------------------------- #
# Whitelist of sortable columns to keep `?sort=` safe from injection.
SORT_COLS = {
    "submission_date":        "a.submission_date DESC NULLS LAST",
    "submission_date_asc":    "a.submission_date ASC  NULLS LAST",
    "signed_date":            "a.signed_date DESC NULLS LAST",
    "signed_date_asc":        "a.signed_date ASC  NULLS LAST",
    "value":                  "a.total_cost_with_vat DESC NULLS LAST",
    "value_asc":              "a.total_cost_with_vat ASC  NULLS LAST",
    "deadline":               "a.final_submission_date ASC NULLS LAST",
    # 'relevance' is handled specially in run_search (needs the query text);
    # mapped here to the default so it's a recognised key and falls back safely
    # when there's no full-text query to rank by.
    "relevance":              "a.submission_date DESC NULLS LAST",
}
DEFAULT_SORT = "submission_date"

# Columns we always need for the result row.
SELECT_COLS = """
    a.adam,
    a.type,
    a.title,
    a.signed_date,
    a.submission_date,
    a.final_submission_date,
    a.total_cost_with_vat,
    proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
    (proc.resolved_value(a.adam, a.total_cost_with_vat)
        IS DISTINCT FROM a.total_cost_with_vat)        AS is_corrected,
    a.cancelled,
    a.is_modified,
    a.contract_type_code,
    a.procedure_type_code,
    a.nuts_code,
    a.data_source,
    auth.org_id      AS authority_id,
    auth.name        AS authority_name
"""

# Extended multi-source act fields (authored / non-KHMDHS). Used by the detail
# page to decide whether to show the "additional details" panel at all — plain
# KHMDHS acts never have any of these, so the panel stays hidden for them.
EXTENDED_ACT_FIELDS = (
    "divided_into_lots", "is_framework_agreement", "type_of_bid_required",
    "alternative_offers_allowed", "number_of_offers", "prolongation_option",
    "prolongation_in_months", "vat_rate", "vat_included", "value_eur", "value_usd",
    "estimated_price_min", "estimated_price_max", "yearly_budget", "bid_bond_amount",
    "price_weighting", "eligibility_criteria", "eligibility_category",
    "journal_number", "eprocurement_portal", "contact_email", "contact_phone",
    "contact_fax", "street_address", "contact_url",
)


# Greek text-search clause builder shared by the full-text and tables filters.
#
# Two modes, chosen by a trailing '*':
#
#  • No star — stemmed full-text. websearch_to_tsquery over the stored tsvector:
#    friendly syntax (bare words = AND, "quoted phrase", OR, -exclude) and Greek
#    stemming, so ηλεκτρολογικά already finds ηλεκτρολογικών etc. This is the
#    default and the common case.
#
#  • Trailing star — literal prefix via ILIKE on the RAW text (NOT the tsvector).
#    Prefix search can't go through the tsvector: the stored lexemes are stems
#    (ηλεκτρολογικά → ηλεκτρολογ), so a typed prefix like ηλεκτρολογικ is LONGER
#    than the stem and :* matches nothing. Verified on real data. So in prefix
#    mode we bypass stemming entirely and substring-match the raw source text,
#    normalised the same way the title search is (unaccent + lower + final-sigma
#    ς→σ) so accents/case/sigma don't block matches. Every word in the query
#    becomes its own normalised ILIKE term, ANDed. Matching is substring (the
#    prefix can appear anywhere in a word), consistent with the title box.
#
# Because the two filters target different sources, the caller passes both the
# tsvector column (for stemmed mode) and the raw-text SQL expression (for ILIKE
# mode — a.full_text for documents, et.rows::text for tables).
#
# Returns (sql_clause, args) where sql_clause is a complete boolean SQL fragment
# with %s placeholders and args is the matching list of bind values. Returns
# (None, None) when there's nothing usable to search (caller skips the filter).
_NORM = "translate(proc.f_unaccent(lower({expr})), 'ς', 'σ')"


def _text_search_clause(raw: str, tsv_col: str, raw_text_expr: str
                        ) -> tuple[str | None, list | None]:
    raw = (raw or "").strip()
    if not raw:
        return None, None

    if raw.endswith("*"):
        # Prefix mode → normalised substring ILIKE on the raw text, per word.
        body = raw.rstrip("*").strip()
        terms = []
        args: list = []
        for tok in re.split(r"\s+", body):
            # Keep Greek (incl. extended/polytonic), Latin, digits; drop quotes,
            # parens, % and _ so the user can't inject LIKE wildcards.
            clean = re.sub(r"[^0-9A-Za-z\u0370-\u03FF\u1F00-\u1FFF]", "", tok)
            if not clean:
                continue
            # Normalise BOTH sides identically; substring match. Wildcards go in
            # the bind VALUE (not as %% in the SQL), matching the title search's
            # convention in this file so the % is data, never SQL.
            col_norm = _NORM.format(expr=raw_text_expr)
            pat_norm = _NORM.format(expr="%s")
            terms.append(f"{col_norm} LIKE {pat_norm}")
            args.append(f"%{clean}%")
        if not terms:
            return None, None
        return "(" + " AND ".join(terms) + ")", args

    # Normal mode → stemmed full-text match over the tsvector.
    return f"{tsv_col} @@ websearch_to_tsquery('greek', %s)", [raw]


def _as_list(v) -> list[str]:
    """Normalise a filter param to a clean list of values. Accepts a list
    (multi-select), a single string (legacy / single value), or None. Strips
    blanks. Lets build_where treat all five multi-filters uniformly while still
    tolerating a stray single string."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    return [s.strip() for s in v if s and s.strip()]


def build_where(params: dict) -> tuple[str, list]:
    """Translate query parameters into a parameterised WHERE clause."""
    where: list[str] = []
    args: list = []

    # Act type — multi-select. Empty/absent => all types. Validate each against
    # the known set so values are safe to cast to the enum.
    act_types = _as_list(params.get("type"))
    act_types = [t for t in act_types if t in TYPE_LABELS]
    if act_types:
        where.append("a.type = ANY(%s::proc.act_type[])")
        args.append(act_types)

    q = (params.get("q") or "").strip()
    if q:
        # Auto-detect: if the query looks like an ADAM (e.g. 25SYMV016143474 —
        # 2 digits, uppercase letters, then digits), search the adam column
        # directly. ADAMs are case-insensitive and matched as a prefix, so a
        # partial paste still finds it.
        if re.fullmatch(r"\d{2}[A-Za-z]{2,6}\d{0,15}", q):
            where.append("a.adam ILIKE %s")
            args.append(f"{q}%")
        else:
            # Unified keyword search: ONE box matches title + document full text
            # (combined search_tsv, GIN-indexed) OR the act's published extracted
            # tables. Greek-stemmed websearch syntax (AND words, "phrases", -excl).
            # Falls back to a title substring LIKE if the combined tsv column
            # isn't present yet (pre-migration), so search never breaks.
            parts, qargs = [], []
            main_sql, main_args = _text_search_clause(q, "a.search_tsv", "a.title")
            if main_sql:
                parts.append(main_sql)
                qargs.extend(main_args)
            tbl_sql, tbl_args = _text_search_clause(q, "et.content_tsv", "et.rows::text")
            if tbl_sql:
                parts.append(f"""EXISTS (
                    SELECT 1 FROM proc.extracted_table et
                    WHERE et.adam = a.adam AND et.is_published AND {tbl_sql}
                )""")
                qargs.extend(tbl_args)
            # Uploaded attachments (LOCAL-ONLY, flag-gated). Off in prod → clause
            # never emitted → proc.act_attachment need not exist there.
            if ATTACHMENTS_ENABLED:
                att_sql, att_args = _text_search_clause(q, "att.content_tsv", "att.filename")
                if att_sql:
                    parts.append(f"""EXISTS (
                        SELECT 1 FROM proc.act_attachment att
                        WHERE att.adam = a.adam AND {att_sql}
                    )""")
                    qargs.extend(att_args)
            if parts:
                where.append("(" + " OR ".join(parts) + ")")
                args.extend(qargs)
            else:
                # Fallback (e.g. tsv column missing): old title substring match.
                norm = "translate(proc.f_unaccent(lower({col})), 'ς', 'σ')"
                where.append(f"{norm.format(col='a.title')} LIKE {norm.format(col='%s')}")
                args.append(f"%{q}%")

    auth_ids = _as_list(params.get("authority"))
    if auth_ids:
        where.append("a.authority_id = ANY(%s)")
        args.append(auth_ids)

    # Data source — multi-select (e.g. 'khmdhs', 'diavgeia'). Empty => all.
    sources = _as_list(params.get("source"))
    if sources:
        where.append("a.data_source = ANY(%s)")
        args.append(sources)

    # Full-text search across the extracted document text (full_text_tsv is a
    # stored, Greek-stemmed tsvector — see full_text_tsv_migration.sql). Separate
    # from `q` (which searches title/ADAM) so users can search document CONTENT
    # independently or in combination. websearch_to_tsquery gives friendly
    # syntax: bare words = AND, "quoted phrase" = exact phrase, OR = alternation,
    # -word = exclude. Empty/whitespace query is ignored.
    fulltext = (params.get("fulltext") or "").strip()
    if fulltext:
        ft_sql, ft_args = _text_search_clause(
            fulltext, "a.full_text_tsv", "a.full_text")
        if ft_sql:
            where.append(ft_sql)
            args.extend(ft_args)

    # Search inside curator-extracted tables: keep only acts that have at least
    # one PUBLISHED extracted table whose cell content matches. Greek-stemmed,
    # same websearch_to_tsquery syntax as the full-text filter above, reading
    # the stored content_tsv (see extracted_table_tsv_migration.sql). Combinable
    # with every other filter — it's just one more EXISTS clause in the same
    # WHERE. Empty/whitespace ignored.
    tables_q = (params.get("tables_q") or "").strip()
    if tables_q:
        tq_sql, tq_args = _text_search_clause(
            tables_q, "et.content_tsv", "et.rows::text")
        if tq_sql:
            where.append(f"""EXISTS (
                SELECT 1 FROM proc.extracted_table et
                WHERE et.adam = a.adam
                  AND et.is_published
                  AND {tq_sql}
            )""")
            args.extend(tq_args)

    # CPV — multi-select, OR'd. An act matches if it has any line item whose
    # CPV starts with ANY of the selected codes/prefixes (e.g. '331' OR '4524').
    cpvs = _as_list(params.get("cpv"))
    if cpvs:
        # Each selected value is a prefix; build one EXISTS with an ANY(LIKE)
        # via a prefix-OR. Use a small OR of LIKEs so each acts as a prefix.
        like_terms = " OR ".join(["oc.cpv_code LIKE %s"] * len(cpvs))
        where.append(f"""EXISTS (
            SELECT 1 FROM proc.act_object_detail od
            JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
            WHERE od.adam = a.adam AND ({like_terms})
        )""")
        for cv in cpvs:
            args.append(f"{cv}%")

    # Category / subcategory — our custom taxonomy over CPV codes (see
    # tender_category_migration.sql). It is DERIVED: an act matches if any of
    # its line-item CPVs maps to a selected category or subcategory. Values
    # arrive under one `cat` param, prefixed "c:<id>" (whole category) or
    # "s:<id>" (subcategory), so a single grouped multi-select can offer both
    # levels. Selections OR together (like CPV); the group as a whole ANDs with
    # the other filters.
    cat_vals = _as_list(params.get("cat"))
    if cat_vals:
        cat_ids = [int(v[2:]) for v in cat_vals if v.startswith("c:") and v[2:].isdigit()]
        sub_ids = [int(v[2:]) for v in cat_vals if v.startswith("s:") and v[2:].isdigit()]
        conds = []
        if cat_ids:
            conds.append("m.category_id = ANY(%s)")
        if sub_ids:
            conds.append("m.subcategory_id = ANY(%s)")
        if conds:
            where.append(f"""EXISTS (
                SELECT 1 FROM proc.act_object_detail od
                JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                JOIN proc.cpv_category_map m ON m.cpv_code = oc.cpv_code
                WHERE od.adam = a.adam AND ({" OR ".join(conds)})
            )""")
            if cat_ids:
                args.append(cat_ids)
            if sub_ids:
                args.append(sub_ids)

    contract_types = _as_list(params.get("contract_type"))
    if contract_types:
        where.append("a.contract_type_code = ANY(%s)")
        args.append(contract_types)

    procedure_types = _as_list(params.get("procedure_type"))
    if procedure_types:
        where.append("a.procedure_family = ANY(%s)")
        args.append(procedure_types)

    nuts_vals = _as_list(params.get("nuts"))
    if nuts_vals:
        # Each value is a prefix ('EL' = all Greece, 'EL5' = a region cluster);
        # multi-select means match ANY of the chosen prefixes. An act matches on
        # its primary region (a.nuts_code) OR any place-of-performance in
        # proc.act_nuts, so secondary regions are findable too. Wildcards live in
        # the bind values (never SQL); args are appended in SQL order.
        primary = []
        for n in nuts_vals:
            primary.append("a.nuts_code LIKE %s")
            args.append(f"{n}%")
        secondary = []
        for n in nuts_vals:
            secondary.append("an.nuts_code LIKE %s")
            args.append(f"{n}%")
        where.append(
            "(" + " OR ".join(primary)
            + " OR EXISTS (SELECT 1 FROM proc.act_nuts an"
            + " WHERE an.adam = a.adam AND (" + " OR ".join(secondary) + ")))")

    # Date filter applies to *publication* date (KHMDHS submission) — that's
    # the "this became public" timestamp users care about for transparency.
    # signed_date stays exposed as a sort option but isn't filterable.
    date_from = params.get("date_from")
    if date_from:
        where.append("a.submission_date >= %s")
        args.append(date_from)
    date_to = params.get("date_to")
    if date_to:
        # Inclusive end-of-day for the upper bound.
        where.append("a.submission_date < (%s::date + interval '1 day')")
        args.append(date_to)

    # Deadline filter applies to final_submission_date (the bid submission
    # deadline) — lets users find tenders whose deadline falls in a range,
    # e.g. "still open" = deadline_from = today.
    deadline_from = params.get("deadline_from")
    if deadline_from:
        where.append("a.final_submission_date >= %s")
        args.append(deadline_from)
    deadline_to = params.get("deadline_to")
    if deadline_to:
        where.append("a.final_submission_date < (%s::date + interval '1 day')")
        args.append(deadline_to)

    value_min = params.get("value_min")
    if value_min not in (None, ""):
        where.append("a.total_cost_with_vat >= %s")
        args.append(value_min)
    value_max = params.get("value_max")
    if value_max not in (None, ""):
        where.append("a.total_cost_with_vat <= %s")
        args.append(value_max)

    status = params.get("status")
    if status == "active":
        where.append("a.cancelled = false")
    elif status == "cancelled":
        where.append("a.cancelled = true")
    elif status == "modified":
        where.append("a.is_modified = true")

    return (" AND ".join(where) if where else "TRUE"), args


def run_search(params: dict, limit: int, offset: int):
    where, args = build_where(params)

    # Relevance sort and snippet highlighting only apply to STEMMED full-text
    # (websearch_to_tsquery over the tsvector). Prefix mode (trailing *) uses
    # ILIKE on raw text — there's no tsquery to rank with or to feed ts_headline,
    # so in prefix mode we fall back to the normal column sort and show no
    # snippet. The WHERE clause itself is already handled by build_where for both
    # modes; this block only governs ranking + the highlighted excerpt.
    fulltext = (params.get("fulltext") or "").strip()
    sort_key = params.get("sort") or DEFAULT_SORT

    # Stemmed mode = a full-text query that is present and NOT a trailing-* prefix
    # query. We rebuild just the tsquery fragment here (websearch_to_tsquery) for
    # the rank and snippet; it mirrors what build_where emitted for this case.
    stemmed_ft = bool(fulltext) and not fulltext.endswith("*")

    rank_args: list = []
    if sort_key == "relevance" and stemmed_ft:
        order_by = "ts_rank(a.full_text_tsv, websearch_to_tsquery('greek', %s)) DESC"
        rank_args.append(fulltext)
    else:
        order_by = SORT_COLS.get(sort_key, SORT_COLS[DEFAULT_SORT])

    if stemmed_ft:
        # PERF: compute the highlighted snippet ONLY for the final page of rows.
        # ts_headline re-parses full_text (it can't use the stored tsvector), so
        # it's the expensive part. We do the match + rank + LIMIT in an inner
        # query first, then call ts_headline in the outer SELECT over just those
        # ≤50 rows — guaranteeing 50 headline computations regardless of how many
        # acts match. Bind order: snippet arg (outer SELECT, appears first in the
        # text) → inner WHERE args → rank arg → limit/offset.
        select_args: list = [fulltext]
        sql = f"""
            SELECT page.*,
                   ts_headline('greek', page.full_text,
                       websearch_to_tsquery('greek', %s),
                       'StartSel=<mark>, StopSel=</mark>, MaxFragments=2, '
                       'MaxWords=24, MinWords=8, FragmentDelimiter=" … "') AS snippet
            FROM (
                SELECT {SELECT_COLS}, a.full_text
                FROM proc.procurement_act a
                LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                WHERE {where}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
            ) AS page
        """
    else:
        select_args = []
        sql = f"""
            SELECT {SELECT_COLS}, NULL AS snippet
            FROM proc.procurement_act a
            LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """
    # Counter rework (perf): the old version summed proc.resolved_value() per
    # row, firing a subquery per row × 2.67M (~7.9s/load by EXPLAIN). The
    # corrected total equals sum(total_cost_with_vat) + sum(corrected - base)
    # over only the few overrides — both honour the same WHERE so the figure
    # matches the filtered set. Unfiltered count uses Postgres' instant
    # reltuples estimate instead of counting 2.67M rows exactly.
    count_sql = f"""
        SELECT count(*) AS n,
               coalesce(sum(a.total_cost_with_vat), 0) AS base_value
        FROM proc.procurement_act a
        WHERE {where}
    """
    delta_sql = f"""
        SELECT coalesce(sum(v.corrected_value - a.total_cost_with_vat), 0) AS delta
        FROM proc.procurement_act a
        JOIN proc.v_act_annotation_current v ON v.adam = a.adam
        WHERE {where}
          AND v.corrected_value IS NOT NULL
    """
    with cursor() as c:
        if where == "TRUE":
            c.execute("""SELECT reltuples::bigint AS n
                         FROM pg_class
                         WHERE oid = 'proc.procurement_act'::regclass""")
            n = c.fetchone()["n"]
            c.execute("""SELECT coalesce(sum(total_cost_with_vat), 0) AS base_value
                         FROM proc.procurement_act""")
            base_value = c.fetchone()["base_value"]
        else:
            c.execute(count_sql, args)
            base = c.fetchone()
            n = base["n"]
            base_value = base["base_value"]
        c.execute(delta_sql, args)
        delta = c.fetchone()
        c.execute(sql, select_args + args + rank_args + [limit, offset])
        rows = c.fetchall()

    # Make snippets safe to render: ts_headline wraps matched terms in <mark>
    # but does NOT escape the surrounding document text, so a document containing
    # markup could otherwise inject HTML. Escape everything, then re-enable only
    # the <mark> tags we asked ts_headline to add, and mark the result safe so
    # Jinja renders the highlight instead of showing literal tags.
    if stemmed_ft:
        import html as _html
        from markupsafe import Markup
        for r in rows:
            snip = r.get("snippet")
            if snip:
                safe = _html.escape(snip)
                safe = safe.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")
                r["snippet"] = Markup(safe)

    agg = {
        "n": n,
        "total_value": (base_value or 0) + (delta["delta"] or 0),
    }
    return rows, agg


# Controlled-vocabulary description column for the active UI language. The
# *_en columns (vocab_en_migration.sql) are NULL where the EN source didn't
# cover a row, so coalesce falls back to Greek. Args are table aliases / column
# names (never user input) — safe to f-string into a query.
def _desc_col(lang, alias="c", col="description"):
    return f"coalesce({alias}.{col}_en, {alias}.{col})" if lang == "en" else f"{alias}.{col}"


# ---------------------------------------------------------------------------- #
# Lookups for filter dropdowns (cached lazily to avoid hammering the DB)
# ---------------------------------------------------------------------------- #
_lookup_cache: dict = {}

def lookups() -> dict:
    if _lookup_cache:
        return _lookup_cache
    with cursor() as c:
        # Top authorities by notice volume — for the dropdown's first ~200.
        c.execute("""
            SELECT auth.org_id AS id, auth.name AS name, count(*) AS n
            FROM proc.procurement_act a
            JOIN proc.authority auth ON auth.org_id = a.authority_id
            GROUP BY auth.org_id, auth.name
            ORDER BY n DESC
            LIMIT 200
        """)
        _lookup_cache["authorities"] = c.fetchall()
        # Contract types present across all acts. Labels come from the
        # CONTRACT_TYPES dict so we don't need code_list populated.
        c.execute("""
            SELECT DISTINCT a.contract_type_code AS code
            FROM proc.procurement_act a
            WHERE a.contract_type_code IS NOT NULL
        """)
        rows = c.fetchall()
        ct = [{"code": r["code"], "label": CONTRACT_TYPES.get(str(r["code"]), r["code"])}
              for r in rows]
        ct.sort(key=lambda x: x["label"])
        _lookup_cache["contract_types"] = ct
        # Procedure families — already normalized in the procedure_family
        # column (see procedure_family_migration.sql), so just list the
        # distinct families present. No dict mapping or dedup needed.
        c.execute("""
            SELECT DISTINCT procedure_family AS code
            FROM proc.procurement_act
            WHERE procedure_family IS NOT NULL
            ORDER BY procedure_family
        """)
        pt = [{"code": r["code"], "label": r["code"]} for r in c.fetchall()]
        _lookup_cache["procedure_types"] = pt
        # NUTS regions present across all acts.
        c.execute("""
            SELECT DISTINCT a.nuts_code AS code,
                   coalesce(n.label, a.nuts_code) AS label
            FROM proc.procurement_act a
            LEFT JOIN proc.nuts_code n ON n.nuts_code = a.nuts_code
            WHERE a.nuts_code IS NOT NULL
            ORDER BY code
        """)
        _lookup_cache["nuts"] = c.fetchall()
        # Custom category taxonomy for the two-level filter (see
        # tender_category_migration.sql). Returned as categories each carrying
        # their subcategories, so the template can render one grouped
        # multi-select with optgroups. Ordered alphabetically (Greek collation).
        # Carry both Greek `name` and English `name_en` (vocab_en_migration.sql);
        # the template picks per the active UI language. name_en is NULL for the
        # rows the EN file didn't cover — those fall back to Greek in the template.
        c.execute("SELECT id, name, name_en FROM proc.tender_category ORDER BY name")
        cats = [{"id": r["id"], "name": r["name"], "name_en": r["name_en"], "subs": []}
                for r in c.fetchall()]
        by_id = {cat["id"]: cat for cat in cats}
        c.execute("""SELECT id, name, name_en, parent_category_id
                     FROM proc.tender_subcategory ORDER BY name""")
        for r in c.fetchall():
            parent = by_id.get(r["parent_category_id"])
            if parent is not None:
                parent["subs"].append({"id": r["id"], "name": r["name"], "name_en": r["name_en"]})
        _lookup_cache["categories"] = cats
    return _lookup_cache


# ---------------------------------------------------------------------------- #
# App + routes
# ---------------------------------------------------------------------------- #
app = FastAPI(title="Greek Procurement Explorer", version="0.1.0")


@app.get("/set-lang")
def set_lang(lang: str = "el", next: str = "/"):
    """Persist the UI language in a cookie and return to the current page.
    `next` is restricted to a same-site path to avoid open redirects."""
    from fastapi.responses import RedirectResponse
    target = next if (next.startswith("/") and not next.startswith("//")) else "/"
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie("lang", _i18n.normalize_lang(lang),
                    max_age=60 * 60 * 24 * 365, samesite="lax", path="/")
    return resp

# ---------------------------------------------------------------------------- #
# Optional HTTP Basic Auth gate (for the deployed, private instance).
#   Enabled ONLY when APP_PASSWORD is set in the environment. Locally (no env
#   var) the app is wide open, so your laptop workflow is unchanged.
#   - Single shared password; username is whatever APP_USERNAME says (default
#     "team"), so the browser prompt looks normal.
#   - Constant-time comparison to avoid timing attacks.
#   - /healthz is exempt so the host's health checks don't need credentials.
# ---------------------------------------------------------------------------- #
import base64
import secrets as _secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as _Response

_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_APP_USERNAME = os.environ.get("APP_USERNAME", "team")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Auth disabled if no password configured (local dev).
        if not _APP_PASSWORD:
            return await call_next(request)
        # Let liveness checks through unauthenticated.
        if request.url.path == "/healthz":
            return await call_next(request)

        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
                user, _, pw = raw.partition(":")
                # compare_digest on both fields; both must match.
                ok = (_secrets.compare_digest(user, _APP_USERNAME)
                      and _secrets.compare_digest(pw, _APP_PASSWORD))
            except Exception:
                ok = False
        if not ok:
            return _Response(
                "Authentication required.", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Procurement Explorer"'},
            )
        return await call_next(request)


app.add_middleware(BasicAuthMiddleware)

if os.path.isdir(os.path.join(APP_DIR, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")),
              name="static")

# Backfill admin UI (separate module, mounted under /admin).
try:
    from app.admin import make_router as _make_admin_router
except ImportError:
    from admin import make_router as _make_admin_router   # fallback if run with --app-dir=app
app.include_router(_make_admin_router(templates, cursor))

# Tender-table extraction UI (separate module, mounted under /tables). Gated by
# the TABLES_ENABLED env flag so the deployed Render copy can carry the feature
# turned off until the public conversation happens for real — locally it
# defaults ON. Like /admin, it sits behind the app's BasicAuthMiddleware, so
# it's curator-only without any per-route dependency. OCR inside it is gated
# SEPARATELY on ANTHROPIC_API_KEY, so enabling the feature and spending the API
# key on OCR stay two independent decisions. Templates live in templates/tables/
# and the three extraction modules (extractors.py, exporter.py, ocr.py) are kept
# byte-identical with the standalone Tender Tables tool.
if os.environ.get("TABLES_ENABLED", "1") == "1":
    try:
        from app.tables import make_router as _make_tables_router
    except ImportError:
        from tables import make_router as _make_tables_router  # run with --app-dir=app
    app.include_router(_make_tables_router(templates, cursor))


@app.get("/healthz")
def healthz():
    with cursor() as c:
        c.execute("SELECT 1 AS ok")
        return c.fetchone()


@app.get("/api/cpv-suggest", response_class=HTMLResponse)
def cpv_suggest(request: Request, term: str = Query(""), wild: int = Query(1)):
    """Autosuggest for the CPV filter. As the user types a code prefix (e.g.
    '331'), returns the most relevant CPV codes: first the prefix wildcard
    itself ('331*' → everything under 331), then the matching codes with their
    descriptions. Also matches by Greek description text when the term isn't a
    number. Returns an HTMX fragment of clickable options.

    wild=0 suppresses the prefix-wildcard option — used by the act-edit CPV
    picker, which must select real codes, not a search prefix."""
    term = term.strip()
    if not term:
        return HTMLResponse("")
    lang = _i18n.lang_from_request(request)
    dc = _desc_col(lang, "cpv_code")  # display in active language; search stays Greek
    digits = re.sub(r"\D", "", term)
    rows = []
    with cursor() as c:
        if digits:
            # Code-prefix matches, shortest (broadest) first.
            c.execute(f"""
                SELECT cpv_code, {dc} AS description
                FROM proc.cpv_code
                WHERE cpv_code LIKE %s
                ORDER BY length(cpv_code), cpv_code
                LIMIT 12
            """, (f"{digits}%",))
            rows = c.fetchall()
        else:
            # Description text match (Greek-stemmed) when they typed words.
            c.execute(f"""
                SELECT cpv_code, {dc} AS description
                FROM proc.cpv_code
                WHERE description_tsv @@ websearch_to_tsquery('greek', %s)
                ORDER BY length(cpv_code), cpv_code
                LIMIT 12
            """, (term,))
            rows = c.fetchall()
    return templates.TemplateResponse(
        request, "_cpv_suggest.html",
        {"term": term, "digits": digits, "rows": rows, "wild": bool(wild)})


@app.get("/api/nuts-suggest", response_class=HTMLResponse)
def nuts_suggest(request: Request, term: str = Query("")):
    """Autosuggest for the act-form NUTS picker. Matches Greek NUTS codes (EL*)
    by code (e.g. 'EL3', '303') or by Greek label (accent- and final-σ-
    insensitive, like the search). Returns an HTMX fragment of clickable options
    that call pickNuts(code, label)."""
    term = term.strip()
    if not term:
        return HTMLResponse("")
    with cursor() as c:
        c.execute("""
            SELECT nuts_code, label
            FROM proc.nuts_code
            WHERE nuts_code LIKE 'EL%%'
              AND (upper(nuts_code) LIKE upper(%s)
                   OR translate(proc.f_unaccent(lower(coalesce(label,''))), 'ς', 'σ')
                      LIKE translate(proc.f_unaccent(lower(%s)), 'ς', 'σ'))
            ORDER BY length(nuts_code), nuts_code
            LIMIT 15
        """, (f"%{term}%", f"%{term}%"))
        rows = c.fetchall()
    return templates.TemplateResponse(request, "_nuts_suggest.html", {"rows": rows})


@app.get("/api/postal-nuts")
def postal_nuts(zip: str = Query("")):
    """ZIP→NUTS lookup for the act form. Given a Greek postal code, returns the
    mapped NUTS-3 region as JSON {nuts_code, label}, or {} if unknown."""
    z = re.sub(r"\D", "", zip or "")
    if not z:
        return {}
    with cursor() as c:
        c.execute("""SELECT pn.nuts_code, nc.label
                     FROM proc.postal_nuts pn
                     JOIN proc.nuts_code nc ON nc.nuts_code = pn.nuts_code
                     WHERE pn.postal_code = %s""", (z,))
        row = c.fetchone()
    return {"nuts_code": row["nuts_code"], "label": row["label"]} if row else {}


def _cpv_level_sig(code: str) -> tuple[int, str]:
    """A CPV code's hierarchy level and significant digit-prefix. CPV is
    positional: division = first 2 digits (even when digit 2 is '0', e.g. '30'),
    then group/class/… up to 8 digits. level = greatest(2, last-non-zero-pos)."""
    d = re.sub(r"\D", "", code)[:8]
    if not d:
        return 0, ""
    lvl = max(2, len(d.rstrip("0")))
    return lvl, d[:lvl]


@app.get("/api/cpv-browse", response_class=HTMLResponse)
def cpv_browse(request: Request, parent: str = Query("")):
    """Hierarchy browser for the CPV picker dialog. Given a `parent` code (empty
    = root), returns its direct children (the next existing level down) plus a
    breadcrumb, built purely from the code prefixes in proc.cpv_code."""
    parent = parent.strip()
    lang = _i18n.lang_from_request(request)
    dc = _desc_col(lang, "cpv_code")
    lp, sig = _cpv_level_sig(parent) if parent else (0, "")
    like = (sig + "%") if sig else "%"
    with cursor() as c:
        c.execute(f"""
            WITH k AS (
              SELECT cpv_code, {dc} AS description,
                     left(cpv_code, greatest(2,length(rtrim(left(cpv_code,8),'0')))) AS sig,
                     greatest(2,length(rtrim(left(cpv_code,8),'0'))) AS lvl
              FROM proc.cpv_code
              WHERE cpv_code LIKE %s
                AND greatest(2,length(rtrim(left(cpv_code,8),'0'))) > %s
            )
            SELECT k.cpv_code, k.description,
                   EXISTS (SELECT 1 FROM proc.cpv_code d
                           WHERE d.cpv_code LIKE k.sig || '%%'
                             AND greatest(2,length(rtrim(left(d.cpv_code,8),'0'))) > k.lvl
                          ) AS has_children
            FROM k
            WHERE k.lvl = (SELECT min(lvl) FROM k)
            ORDER BY k.cpv_code
        """, (like, lp))
        children = c.fetchall()
        crumbs = []
        if parent:
            c.execute(f"""
                SELECT cpv_code, {dc} AS description,
                       greatest(2,length(rtrim(left(cpv_code,8),'0'))) AS lvl
                FROM proc.cpv_code
                WHERE greatest(2,length(rtrim(left(cpv_code,8),'0'))) <= %s
                  AND left(%s, greatest(2,length(rtrim(left(cpv_code,8),'0'))))
                      = left(cpv_code, greatest(2,length(rtrim(left(cpv_code,8),'0'))))
                ORDER BY lvl
            """, (lp, sig))
            crumbs = c.fetchall()
    return templates.TemplateResponse(
        request, "_cpv_browse.html",
        {"children": children, "crumbs": crumbs, "parent": parent})


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    """Dashboard of deduplicated AWARDED value (contracts only; payments and
    cancelled acts excluded; merged entities consolidated). Reads precomputed
    materialized views — refresh them with SELECT proc.refresh_analytics()."""
    lang = _i18n.lang_from_request(request)
    # The matview pre-bakes a Greek division label; in EN resolve the division's
    # English CPV name (root code), falling back to the baked Greek label.
    cpv_label = ("coalesce((SELECT cc.description_en FROM proc.cpv_code cc "
                 "WHERE substr(cc.cpv_code,1,2)=m.division AND substr(cc.cpv_code,3,6)='000000' "
                 "LIMIT 1), m.label)") if lang == "en" else "m.label"
    data: dict = {"available": True}
    try:
        with cursor() as c:
            c.execute("""SELECT n_contracts, awarded_value, n_authorities,
                                earliest, latest FROM proc.mv_analytics_totals""")
            data["totals"] = c.fetchone()

            # Top authorities — resolve canonical name from authority table.
            c.execute("""
                SELECT m.authority_id, auth.name, m.n_contracts, m.awarded_value
                FROM proc.mv_analytics_authorities m
                LEFT JOIN proc.authority auth ON auth.org_id = m.authority_id
                ORDER BY m.awarded_value DESC LIMIT 15
            """)
            data["authorities"] = c.fetchall()

            # Top contractors — resolve canonical name + merge flag.
            c.execute("""
                SELECT m.vat_number, eo.name, m.n_contracts, m.awarded_value,
                       EXISTS (SELECT 1 FROM proc.entity_member em
                               WHERE em.kind='contractor'
                                 AND em.member_key=m.vat_number) AS is_merged
                FROM proc.mv_analytics_contractors m
                LEFT JOIN proc.economic_operator eo ON eo.vat_number = m.vat_number
                ORDER BY m.awarded_value DESC LIMIT 15
            """)
            data["contractors"] = c.fetchall()

            c.execute("""SELECT month, n_contracts, awarded_value
                         FROM proc.mv_analytics_monthly ORDER BY month""")
            data["monthly"] = c.fetchall()

            # By-CPV-division — top 15 by contract value. Separate from the
            # awarded-value figures (line-item costs, without VAT).
            try:
                c.execute(f"""SELECT m.division, {cpv_label} AS label,
                                    m.contract_count, m.contract_value,
                                    m.notice_count, m.notice_value
                             FROM proc.mv_analytics_cpv m
                             WHERE m.contract_value > 0 OR m.notice_value > 0
                             ORDER BY m.contract_value DESC, m.notice_value DESC
                             LIMIT 15""")
                data["cpv"] = c.fetchall()
            except Exception:
                data["cpv"] = []
    except Exception:
        # Materialized views not created yet — show a friendly hint.
        data = {"available": False}

    data["nav_active"] = "analytics"
    return templates.TemplateResponse(request, "beta_analytics.html", data)


@app.get("/", response_class=HTMLResponse)
def home(request: Request,
         page: int = Query(1, ge=1),
         per_page: int = Query(10, ge=1, le=100)):
    """Main search page — dual mode.

    * Normal browser navigation (or reload): returns the full index.html page
      with results pre-rendered server-side. This is what makes URLs like
      /?q=καθαριότητας&type=… sharable and reload-safe.
    * HTMX request (HX-Request header set, sent by the filter form on every
      input change): returns the _results.html partial alone, which HTMX
      swaps into #results without disturbing the rest of the page.
    * Accept: application/json: returns the JSON shape (the silent API).

    Having one endpoint do all three keeps the URL the user sees identical to
    the URL they can share / reload — no /search "rendered partial naked"
    surprise.
    """
    params = _params_from(request)
    offset = (page - 1) * per_page
    rows, agg = run_search(params, per_page, offset)
    total_pages = max(1, (agg["n"] + per_page - 1) // per_page)

    if "application/json" in (request.headers.get("accept") or ""):
        from decimal import Decimal
        def _j(v):
            if hasattr(v, "isoformat"):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v
        return JSONResponse({
            "results": [{k: _j(v) for k, v in r.items()} for r in rows],
            "total_count": agg["n"],
            "total_value": float(agg["total_value"]) if agg["total_value"] else 0.0,
            "page": page, "per_page": per_page,
        })

    ctx = {
        "rows": rows,
        "total_count": agg["n"],
        "total_value": float(agg["total_value"] or 0),
        "page": page, "per_page": per_page, "total_pages": total_pages,
        "params": params,
    }
    if request.headers.get("hx-request") == "true":
        # HTMX fragment swap — partial only.
        return templates.TemplateResponse(request, "beta_results.html", ctx)
    # Full page render. The template inlines the partial so the first paint
    # already has results — no second roundtrip from a hx-trigger='load'.
    ctx["lk"] = lookups()
    ctx["nuts_regions"] = NUTS_REGIONS
    ctx["nav_active"] = "search"
    return templates.TemplateResponse(request, "beta_index.html", ctx)


def _params_from(request: Request) -> dict:
    """Pull all known query params into a plain dict, dropping empties.

    Five filters are MULTI-valued (the user can pick several): type, authority,
    contract_type, procedure_type, nuts. They arrive as repeated query params
    (?type=notice&type=contract) and are read as lists via getlist(). The rest
    stay single-valued strings. build_where handles both shapes.
    """
    single = ("q", "fulltext", "tables_q",
              "date_from", "date_to", "deadline_from", "deadline_to",
              "value_min", "value_max", "status", "sort")
    multi = ("type", "authority", "contract_type", "procedure_type", "nuts",
             "cpv", "cat", "source")
    out = {k: request.query_params.get(k, "") for k in single}
    for k in multi:
        # keep only non-empty values; preserves order, drops blanks
        out[k] = [v for v in request.query_params.getlist(k) if v.strip()]
    return out


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    """Aggregated breakdown of the *same* filtered set as the main search.
    Two tables: by authority and by contractor (both merge-aware, ranked by
    value). Honors the same analytics exclusions (cancelled / over-ceiling /
    suspicious-flagged) so totals are consistent with the dashboard."""
    params = _params_from(request)
    where, args = build_where(params)

    ceiling = ANALYTICS_VALUE_CEILING

    # FAST PATH: when only "coarse" filters are active — nothing, or just an act
    # type — read the precomputed explore matviews (instant). Fine filters (text
    # search, CPV, contract/procedure type, dates, NUTS, value range) require the
    # live aggregation below, but that population is narrower so it is acceptable.
    fine_keys = ("q", "fulltext", "cpv", "contract_type", "procedure_type",
                 "nuts", "date_from", "date_to", "deadline_from", "deadline_to",
                 "value_min", "value_max", "authority", "status")
    has_fine = any(_as_list(params.get(k)) if k in ("type", "authority",
                   "contract_type", "procedure_type", "nuts", "cpv")
                   else (params.get(k) or "").strip() for k in fine_keys)
    type_sel = _as_list(params.get("type"))
    type_sel = [t for t in type_sel if t in TYPE_LABELS]

    by_authority, by_contractor = [], []
    grand = {"n": 0, "value": 0.0}
    used_fast_path = False
    if not has_fine:
        # ---- matview fast path -------------------------------------------------
        try:
            with cursor() as c:
                tfilter = "WHERE ea.type = ANY(%s)" if type_sel else ""
                targs = (type_sel,) if type_sel else ()
                c.execute(f"""
                    SELECT ea.auth_key AS key,
                           max(nm.name) AS name,
                           sum(ea.n)     AS n,
                           sum(ea.value) AS value
                    FROM proc.mv_explore_authority ea
                    LEFT JOIN proc.mv_explore_authority_name nm
                           ON nm.auth_key = ea.auth_key
                    {tfilter}
                    GROUP BY ea.auth_key
                    ORDER BY value DESC NULLS LAST, n DESC
                    LIMIT 100
                """, targs)
                by_authority = c.fetchall()

                tfilter2 = "WHERE ec.type = ANY(%s)" if type_sel else ""
                c.execute(f"""
                    SELECT ec.contr_key AS key,
                           max(nm.name) AS name,
                           bool_or(nm.is_merged) AS is_merged,
                           sum(ec.n)     AS n,
                           sum(ec.value) AS value
                    FROM proc.mv_explore_contractor ec
                    LEFT JOIN proc.mv_explore_contractor_name nm
                           ON nm.contr_key = ec.contr_key
                    {tfilter2}
                    GROUP BY ec.contr_key
                    ORDER BY value DESC NULLS LAST, n DESC
                    LIMIT 100
                """, targs)
                by_contractor = c.fetchall()

                # grand totals from the authority matview (covers all acts with
                # an authority; consistent with the dashboard's scope).
                c.execute(f"""
                    SELECT COALESCE(sum(n),0) AS n, COALESCE(sum(value),0) AS value
                    FROM proc.mv_explore_authority ea {tfilter}
                """, targs)
                g = c.fetchone()
                grand = {"n": int(g["n"] or 0), "value": float(g["value"] or 0)}
                used_fast_path = True
        except Exception:
            # matviews not present yet → fall through to the live query.
            used_fast_path = False

    if not used_fast_path:
        # ---- live fallback (fine filters, or matviews missing) ----------------
        rv = "COALESCE(corr.corrected_value, a.total_cost_with_vat)"
        corr_join = ("LEFT JOIN proc.v_act_annotation_current corr "
                     "ON corr.adam = a.adam")
        eligible = f"""
            NOT a.cancelled
            AND ({rv} IS NULL OR {rv} <= %s)
            AND (corr.flag IS DISTINCT FROM 'suspicious')
        """
        try:
            with cursor() as c:
                c.execute(f"""
                    SELECT proc.canon_authority(a.authority_id) AS key,
                           max(auth.name)                       AS name,
                           count(*)                             AS n,
                           coalesce(sum({rv}), 0)               AS value
                    FROM proc.procurement_act a
                    {corr_join}
                    LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                    WHERE {where} AND a.authority_id IS NOT NULL AND {eligible}
                    GROUP BY proc.canon_authority(a.authority_id)
                    ORDER BY value DESC, n DESC
                    LIMIT 100
                """, (*args, ceiling))
                by_authority = c.fetchall()

                c.execute(f"""
                    SELECT proc.canon_contractor(eo.vat_number) AS key,
                           max(eo.name)                          AS name,
                           NULL::boolean                         AS is_merged,
                           count(DISTINCT a.adam)                AS n,
                           coalesce(sum(coalesce(ao.awarded_value_with_vat, {rv})), 0) AS value
                    FROM proc.procurement_act a
                    {corr_join}
                    JOIN proc.act_operator ao ON ao.adam = a.adam
                    JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
                    WHERE {where} AND {eligible}
                    GROUP BY proc.canon_contractor(eo.vat_number)
                    ORDER BY value DESC, n DESC
                    LIMIT 100
                """, (*args, ceiling))
                by_contractor = c.fetchall()

                c.execute(f"""
                    SELECT count(*) AS n,
                           coalesce(sum({rv}), 0) AS value
                    FROM proc.procurement_act a
                    {corr_join}
                    WHERE {where} AND {eligible}
                """, (*args, ceiling))
                g = c.fetchone()
                grand = {"n": g["n"], "value": float(g["value"] or 0)}
        except Exception:
            by_authority, by_contractor = [], []

    ctx = {"by_authority": by_authority,
           "by_contractor": by_contractor,
           "grand": grand,
           "params": params,
           "active_filters": {k: v for k, v in params.items() if v}}
    # HX request from the filter form → return just the results partial so only
    # that region swaps (and its loading overlay shows during the request).
    if request.headers.get("hx-request") == "true":
        return templates.TemplateResponse(request, "beta_explore_results.html", ctx)
    ctx["lk"] = lookups()
    ctx["nuts_regions"] = NUTS_REGIONS
    ctx["nav_active"] = "explore"
    return templates.TemplateResponse(request, "beta_explore.html", ctx)


@app.get("/search")
def search_redirect(request: Request):
    """Back-compat: old links to /search?... redirect to /?... preserving
    the query string. Anyone who bookmarked a /search URL keeps working."""
    from fastapi.responses import RedirectResponse
    qs = request.url.query
    return RedirectResponse(url=f"/?{qs}" if qs else "/", status_code=307)


@app.get("/notice/{adam}")
def notice_detail_legacy(adam: str):
    """Back-compat: old links to /notice/{adam} redirect to the unified /act/."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/act/{adam}", status_code=307)


@app.get("/act/{adam}", response_class=HTMLResponse)
def act_detail(adam: str, request: Request):
    """Detail page for any act type (notice / auction / contract / payment / request).

    The template branches on `n.type` to show type-specific fields (contract
    dates and bids for contracts, payment commitment for payments, etc.)."""
    lang = _i18n.lang_from_request(request)
    with cursor() as c:
        c.execute(f"""
            SELECT {SELECT_COLS},
                   a.type AS act_type,
                   a.budget, a.total_cost_without_vat,
                   a.criteria_code, a.legal_context_code, a.notice_type_code,
                   a.framework_agreement_adam, a.bidding_website,
                   a.amended_adam, a.cancellation_reason, a.cancellation_date,
                   -- contract-specific
                   a.contract_number, a.contract_signed_date,
                   a.start_date, a.end_date, a.no_end_date,
                   a.assign_criteria_code, a.bids_submitted, a.max_bids_submitted,
                   -- payment-specific
                   a.is_credit, a.payment_commitment_code, a.contract_value,
                   a.raw_json,
                   a.full_text, a.full_text_html,
                   a.full_text_extracted_at, a.full_text_source,
                   -- extended multi-source fields (authored / non-KHMDHS acts)
                   a.divided_into_lots, a.is_framework_agreement,
                   a.type_of_bid_required, a.alternative_offers_allowed,
                   a.number_of_offers, a.prolongation_option, a.prolongation_in_months,
                   a.vat_rate, a.vat_included, a.value_eur, a.value_usd,
                   a.estimated_price_min, a.estimated_price_max,
                   a.yearly_budget, a.bid_bond_amount, a.price_weighting,
                   a.eligibility_criteria, a.eligibility_category,
                   a.journal_number, a.eprocurement_portal,
                   a.contact_email, a.contact_phone, a.contact_fax,
                   a.street_address, a.contact_url,
                   a.source_url, a.source_status,
                   a.type_of_document, a.subtype_of_document,
                   nuts.label AS nuts_label
            FROM proc.procurement_act a
            LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
            LEFT JOIN proc.nuts_code nuts ON nuts.nuts_code = a.nuts_code
            WHERE a.adam = %s
        """, (adam,))
        notice = c.fetchone()
        if not notice:
            # Not ingested yet, but it may be referenced from links we did
            # ingest (e.g. a notice's auctionRefNo[] pointing at an auction
            # that's outside the date windows backfilled so far). Render a stub
            # rather than a flat 404, so the user understands the situation
            # and we keep the cross-links usable.
            c.execute("""
                SELECT source_adam, relation FROM proc.act_link
                WHERE target_adam = %s ORDER BY relation
            """, (adam,))
            referrers = c.fetchall()
            c.execute("""
                SELECT target_adam, relation FROM proc.act_link
                WHERE source_adam = %s ORDER BY relation
            """, (adam,))
            successors = c.fetchall()
            if not referrers and not successors:
                # Truly unknown ADAM — keep the 404.
                raise HTTPException(
                    status_code=404,
                    detail=f"act {adam} not found in database")
            # Infer type from the ADAM prefix where possible (purely cosmetic).
            prefix_to_type = {"PROC": "notice", "AWRD": "auction",
                              "SYMV": "contract", "REQ": "request"}
            inferred = next((t for p, t in prefix_to_type.items() if p in adam), None)
            return templates.TemplateResponse(
                request, "act_stub.html",
                {"adam": adam, "inferred_type": inferred,
                 "referrers": referrers, "successors": successors},
            )

        # Line items + their CPVs (some types use objectDetails, others
        # objectDetailsList; ingester normalises both into act_object_detail).
        # Aggregate as parallel arrays of (code, description) so the template
        # can render them inline. unit_code is joined to proc.unit_code to
        # resolve UNECE Rec 20 codes (e.g. LTR -> 'λίτρο').
        c.execute(f"""
            SELECT od.line_no,
                   od.short_description, od.quantity, od.unit_code,
                   u.name AS unit_name,
                   od.cost_without_vat,
                   proc.resolved_item_cost(od.adam, od.line_no, od.cost_without_vat) AS resolved_cost,
                   (proc.resolved_item_cost(od.adam, od.line_no, od.cost_without_vat)
                       IS DISTINCT FROM od.cost_without_vat) AS cost_corrected,
                   od.vat_rate, od.currency_code,
                   coalesce(
                     array_agg(jsonb_build_object('code', c.cpv_code,
                                                  'desc', {_desc_col(lang, "c")})
                               ORDER BY c.cpv_code)
                     FILTER (WHERE c.cpv_code IS NOT NULL),
                     '{{}}') AS cpvs
            FROM proc.act_object_detail od
            LEFT JOIN proc.object_detail_cpv x ON x.object_detail_id = od.id
            LEFT JOIN proc.cpv_code c ON c.cpv_code = x.cpv_code
            LEFT JOIN proc.unit_code u ON upper(u.code) = upper(od.unit_code)
            WHERE od.adam = %s
            GROUP BY od.id, od.line_no, u.name
            ORDER BY od.line_no
        """, (adam,))
        line_items = c.fetchall()

        # Awarded operators (contractors / consortium members), if any.
        c.execute("""
            SELECT eo.vat_number, eo.name, eo.is_greek_vat, eo.country,
                   ao.role, ao.awarded_value_without_vat, ao.awarded_value_with_vat
            FROM proc.act_operator ao
            JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
            WHERE ao.adam = %s
            ORDER BY ao.role, eo.name
        """, (adam,))
        operators = c.fetchall()

        # Act-level (curator-set) CPV codes.
        c.execute(f"""SELECT ac.cpv_code, {_desc_col(lang, "cc")} AS description
                     FROM proc.act_cpv ac
                     LEFT JOIN proc.cpv_code cc ON cc.cpv_code = ac.cpv_code
                     WHERE ac.adam = %s ORDER BY ac.ord, ac.cpv_code""", (adam,))
        act_cpvs = c.fetchall()

        # Matching categories/subcategories — DERIVED from this act's line-item
        # CPVs via cpv_category_map (see tender_category_migration.sql). Grouped
        # into {category, subs[]} so the template can link each to the search
        # filter (?cat=c:<id> / ?cat=s:<id>).
        c.execute(f"""
            SELECT DISTINCT cat.id AS category_id, {_desc_col(lang, "cat", "name")} AS category_name,
                   sub.id AS subcategory_id, {_desc_col(lang, "sub", "name")} AS subcategory_name
            FROM proc.act_object_detail od
            JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
            JOIN proc.cpv_category_map m ON m.cpv_code = oc.cpv_code
            JOIN proc.tender_category cat ON cat.id = m.category_id
            JOIN proc.tender_subcategory sub ON sub.id = m.subcategory_id
            WHERE od.adam = %s
            ORDER BY category_name, subcategory_name
        """, (adam,))
        act_categories: list[dict] = []
        _cat_idx: dict = {}
        for r in c.fetchall():
            cat = _cat_idx.get(r["category_id"])
            if cat is None:
                cat = {"id": r["category_id"], "name": r["category_name"], "subs": []}
                _cat_idx[r["category_id"]] = cat
                act_categories.append(cat)
            cat["subs"].append({"id": r["subcategory_id"], "name": r["subcategory_name"]})

        # Downstream chain — root-anchored recursion.
        # PERF: the old version queried proc.v_act_chain, a view whose recursion
        # starts from EVERY act_link row; Postgres built the whole 22.6M-row
        # closure and then filtered to this one root (~20s/page). Starting the
        # recursion from THIS adam walks only the few reachable rows (~0.03ms).
        # Same depth cap, cycle guard, relation allow-list, columns and order as
        # the view, so the rendered chain is identical — just fast.
        c.execute("""
            WITH RECURSIVE chain AS (
                SELECT %s::text AS adam, 0 AS depth,
                       ARRAY[%s::text] AS path, NULL::text AS via
                UNION ALL
                SELECT l.target_adam, c.depth + 1,
                       c.path || l.target_adam, l.relation::text
                FROM chain c
                JOIN proc.act_link l ON l.source_adam = c.adam
                WHERE c.depth < 12
                  AND NOT (l.target_adam = ANY (c.path))
                  AND l.relation = ANY (ARRAY[
                        'request_to_notice','request_to_auction',
                        'request_to_contract','request_to_payment',
                        'notice_to_auction','auction_to_contract',
                        'auction_to_payment','contract_to_payment',
                        'contract_next'
                      ]::proc.link_relation[])
            )
            SELECT ch.adam, ch.depth, ch.via,
                   a.type, a.title, a.signed_date, a.total_cost_with_vat,
                   a.cancelled
            FROM chain ch
            LEFT JOIN proc.procurement_act a ON a.adam = ch.adam
            WHERE ch.depth > 0
            ORDER BY ch.depth, ch.adam
        """, (adam, adam))
        downstream = c.fetchall()

        # Direct incoming edges (what points TO this act).
        c.execute("""
            SELECT l.source_adam, l.relation, a.type, a.title, a.signed_date
            FROM proc.act_link l
            LEFT JOIN proc.procurement_act a ON a.adam = l.source_adam
            WHERE l.target_adam = %s
            ORDER BY l.relation, a.signed_date DESC NULLS LAST
        """, (adam,))
        incoming = c.fetchall()

        # Current team annotation (overlay; never part of harvested data).
        annotation = None
        try:
            c.execute("""SELECT note, tags, flag, author, created_at,
                                corrected_value, corrected_value_without_vat
                         FROM proc.v_act_annotation_current WHERE adam=%s""", (adam,))
            annotation = c.fetchone()
        except Exception:
            # annotation table/view not migrated yet — degrade gracefully.
            annotation = None

    # Why (if at all) this contract is excluded from analytics totals, so the
    # detail page can show a badge explaining it. Two reasons, both surfaced.
    excluded_reason = None
    if notice.get("act_type") == "contract" and not notice.get("cancelled"):
        val = notice.get("total_cost_with_vat")
        if annotation and annotation.get("corrected_value") is not None:
            val = annotation.get("corrected_value")   # corrected value wins
        if val is not None and val > ANALYTICS_VALUE_CEILING:
            excluded_reason = "over_threshold"
        elif annotation and annotation.get("flag") == "suspicious":
            excluded_reason = "flagged"

    return templates.TemplateResponse(
        request, "beta_act.html",
        {"n": notice,
         "line_items": line_items,
         "operators": operators,
         "act_cpvs": act_cpvs,
         "act_categories": act_categories,
         "downstream": downstream,
         "incoming": incoming,
         "annotation": annotation,
         "excluded_reason": excluded_reason,
         "has_extended_fields": any(
             notice.get(f) is not None for f in EXTENDED_ACT_FIELDS),
         "nav_active": "search"},
    )




# ---------------------------------------------------------------------------- #
# Entity merge / identity resolution.
#   The official source contains duplicate entities (transposed VATs, spelling
#   variants). The entity_group / entity_member overlay groups raw keys into one
#   canonical entity WITHOUT mutating harvested rows, so merges survive backfills.
#   These helpers resolve a raw key to its group's full member set + canonical
#   identity. A key with no membership is simply its own standalone entity.
# ---------------------------------------------------------------------------- #
def resolve_entity_group(c, kind: str, key: str) -> dict | None:
    """Return {group_id, canonical_key, display_name, members:[keys]} for the
    group containing `key`, or None if `key` isn't part of any merge group."""
    c.execute("""SELECT group_id FROM proc.entity_member
                 WHERE kind=%s AND member_key=%s""", (kind, key))
    row = c.fetchone()
    if not row:
        return None
    gid = row["group_id"]
    c.execute("""SELECT canonical_key, display_name FROM proc.entity_group
                 WHERE id=%s""", (gid,))
    g = c.fetchone()
    c.execute("""SELECT member_key FROM proc.entity_member
                 WHERE group_id=%s ORDER BY member_key""", (gid,))
    members = [r["member_key"] for r in c.fetchall()]
    return {"group_id": gid, "canonical_key": g["canonical_key"],
            "display_name": g["display_name"], "members": members}


def merged_keys(c, kind: str, key: str) -> list[str]:
    """All raw keys that should be aggregated together for `key` (itself if
    not merged)."""
    grp = resolve_entity_group(c, kind, key)
    return grp["members"] if grp else [key]



def _entity_sort(sort: str, value_col: str) -> str:
    return {
        "activity": "n_acts DESC NULLS LAST",
        "name":     "name ASC",
        "name_desc": "name DESC",
    }.get(sort, "n_acts DESC NULLS LAST")


@app.get("/authorities", response_class=HTMLResponse)
def authority_index(request: Request,
                    q: str = Query(""),
                    sort: str = Query("activity"),
                    page: int = Query(1, ge=1),
                    per_page: int = Query(50, ge=1, le=200)):
    """Directory of awarding authorities — searchable, sortable.

    Merge-aware: a merged authority shows once (canonical), counts summed
    across all member org_ids, search matches any member's name.
    """
    q = q.strip()
    offset = (page - 1) * per_page
    order = {"activity": "n_acts DESC NULLS LAST",
             "name": "name ASC", "name_desc": "name DESC"}.get(sort,
             "n_acts DESC NULLS LAST")

    search_args: list = []
    search_clause = "TRUE"
    if q:
        search_clause = """(
            translate(proc.f_unaccent(lower(auth.name)),'ς','σ')
              LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
            OR auth.org_id ILIKE %s
            OR EXISTS (
              SELECT 1 FROM proc.entity_member m1
              JOIN proc.entity_member m2 ON m2.group_id = m1.group_id
              JOIN proc.authority sib ON sib.org_id = m2.member_key
              WHERE m1.kind='authority' AND m1.member_key = auth.org_id
                AND translate(proc.f_unaccent(lower(sib.name)),'ς','σ')
                      LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
            )
        )"""
        search_args = [f"%{q}%", f"%{q}%", f"%{q}%"]

    collapse = """NOT EXISTS (
        SELECT 1 FROM proc.entity_member m
        JOIN proc.entity_group g ON g.id = m.group_id
        WHERE m.kind='authority' AND m.member_key = auth.org_id
          AND g.canonical_key <> auth.org_id
    )"""
    where_sql = f"{search_clause} AND {collapse}"

    with cursor() as c:
        c.execute(f"""
            SELECT count(*) AS n FROM proc.authority auth WHERE {where_sql}
        """, search_args)
        total = c.fetchone()["n"]
        # Counts from the precomputed matview (proc.mv_authority_counts,
        # refreshed after ingest) — replaces the per-row LATERAL aggregation
        # over procurement_act that made this page slow. base = surviving
        # (canonical/unmerged) authority rows after search.
        c.execute(f"""
            WITH base AS (
              SELECT auth.org_id, auth.name, auth.vat_number,
                     EXISTS (SELECT 1 FROM proc.entity_member m
                             WHERE m.kind='authority' AND m.member_key=auth.org_id)
                       AS is_merged
              FROM proc.authority auth
              WHERE {where_sql}
            )
            SELECT b.org_id, b.name, b.vat_number, b.is_merged,
                   COALESCE(mc.n_acts, 0)      AS n_acts,
                   COALESCE(mc.n_notices, 0)   AS n_notices,
                   COALESCE(mc.n_contracts, 0) AS n_contracts
            FROM base b
            LEFT JOIN proc.mv_authority_counts mc ON mc.org_id = b.org_id
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, search_args + [per_page, offset])
        rows = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    ctx = {"rows": rows, "q": q, "sort": sort, "total": total,
           "page": page, "per_page": per_page, "total_pages": total_pages,
           "nav_active": "authorities"}
    if request.headers.get("hx-request") == "true":
        return templates.TemplateResponse(request, "beta_authorities_results.html", ctx)
    return templates.TemplateResponse(request, "beta_authorities.html", ctx)


@app.get("/contractors", response_class=HTMLResponse)
def contractor_index(request: Request,
                     q: str = Query(""),
                     sort: str = Query("activity"),
                     page: int = Query(1, ge=1),
                     per_page: int = Query(50, ge=1, le=200)):
    """Directory of contractors / suppliers — searchable, sortable.

    Merge-aware: a merged entity shows once (canonical row), its counts are
    summed across ALL member VATs, and search matches ANY member's name/VAT.
    """
    q = q.strip()
    offset = (page - 1) * per_page
    order = {"activity": "n_acts DESC NULLS LAST",
             "name": "name ASC", "name_desc": "name DESC"}.get(sort,
             "n_acts DESC NULLS LAST")

    # member_vats(canonical) = array of all VATs in the same group as this row
    # (just [vat] when unmerged). Used both to sum counts and to search names.
    # We express it inline as a correlated lookup.
    search_args: list = []
    search_clause = "TRUE"
    if q:
        # Match if THIS row's name/VAT matches, OR any group sibling's name does.
        search_clause = """(
            translate(proc.f_unaccent(lower(eo.name)),'ς','σ')
              LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
            OR eo.vat_number ILIKE %s
            OR EXISTS (
              SELECT 1 FROM proc.entity_member m1
              JOIN proc.entity_member m2 ON m2.group_id = m1.group_id
              JOIN proc.economic_operator sib ON sib.vat_number = m2.member_key
              WHERE m1.kind='contractor' AND m1.member_key = eo.vat_number
                AND (translate(proc.f_unaccent(lower(sib.name)),'ς','σ')
                       LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
                     OR sib.vat_number ILIKE %s)
            )
        )"""
        search_args = [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]

    # Collapse: only show canonical rows (or unmerged rows).
    collapse = """NOT EXISTS (
        SELECT 1 FROM proc.entity_member m
        JOIN proc.entity_group g ON g.id = m.group_id
        WHERE m.kind='contractor' AND m.member_key = eo.vat_number
          AND g.canonical_key <> eo.vat_number
    )"""

    where_sql = f"{search_clause} AND {collapse}"

    with cursor() as c:
        c.execute(f"""
            SELECT count(*) AS n FROM proc.economic_operator eo WHERE {where_sql}
        """, search_args)
        total = c.fetchone()["n"]

        # Counts come from the precomputed matview (proc.mv_contractor_counts,
        # refreshed after ingest) — see entity_counts_matview_migration.sql.
        # This turns the old ~4s full aggregation over act_operator+
        # procurement_act into a simple indexed sort+limit over ~134k rows.
        # canon = the surviving (canonical/unmerged) operator rows after search.
        c.execute(f"""
            WITH canon AS (
              SELECT eo.vat_number, eo.name, eo.is_greek_vat, eo.country,
                     EXISTS (SELECT 1 FROM proc.entity_member m
                             WHERE m.kind='contractor' AND m.member_key=eo.vat_number)
                       AS is_merged
              FROM proc.economic_operator eo
              WHERE {where_sql}
            )
            SELECT c.vat_number, c.name, c.is_greek_vat, c.country, c.is_merged,
                   COALESCE(mc.n_acts, 0)   AS n_acts,
                   COALESCE(mc.n_buyers, 0) AS n_buyers
            FROM canon c
            LEFT JOIN proc.mv_contractor_counts mc ON mc.vat_number = c.vat_number
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, search_args + [per_page, offset])
        rows = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    ctx = {"rows": rows, "q": q, "sort": sort, "total": total,
           "page": page, "per_page": per_page, "total_pages": total_pages,
           "nav_active": "contractors"}
    if request.headers.get("hx-request") == "true":
        return templates.TemplateResponse(request, "beta_contractors_results.html", ctx)
    return templates.TemplateResponse(request, "beta_contractors.html", ctx)


# ---------------------------------------------------------------------------- #
# Authority drill-down: /authority/{org_id}
#   Header with totals + paginated list of every act this authority issued.
# ---------------------------------------------------------------------------- #
@app.get("/authority/{org_id}", response_class=HTMLResponse)
def authority_detail(org_id: str, request: Request,
                     page: int = Query(1, ge=1),
                     per_page: int = Query(25, ge=1, le=100),
                     type: str = Query("", description="filter by act type")):
    """All acts issued by one awarding authority, with key aggregates."""
    lang = _i18n.lang_from_request(request)
    with cursor() as c:
        c.execute("""
            SELECT org_id, name, vat_number, is_greek_vat, aaht,
                   type_code, classification_code, nuts_code,
                   city, postal_code, country,
                   identifier, orgdb_id, street_address,
                   contact_email, contact_phone, contact_fax, contact_url
            FROM proc.authority WHERE org_id = %s
        """, (org_id,))
        auth = c.fetchone()
        if not auth:
            raise HTTPException(status_code=404,
                                detail=f"authority {org_id} not found")

        # Resolve merge group: aggregate across all member org_ids.
        grp = resolve_entity_group(c, "authority", org_id)
        member_ids = grp["members"] if grp else [org_id]
        merge_info = None
        if grp:
            c.execute("""SELECT org_id, name FROM proc.authority
                         WHERE org_id = ANY(%s)""", (member_ids,))
            member_rows = c.fetchall()
            canon_name = grp["display_name"]
            if not canon_name:
                c.execute("""SELECT name FROM proc.authority WHERE org_id=%s""",
                          (grp["canonical_key"],))
                cr = c.fetchone()
                canon_name = cr["name"] if cr else auth["name"]
            auth = dict(auth)
            auth["name"] = canon_name
            merge_info = {
                "canonical_key": grp["canonical_key"],
                "members": [{"org_id": r["org_id"], "name": r["name"]}
                            for r in member_rows],
                "n": len(member_rows),
            }

        # Aggregates per act type (always — regardless of the type filter, so the
        # user sees the full picture and can switch tabs).
        c.execute("""
            SELECT type,
                   count(*) AS n,
                   coalesce(sum(proc.resolved_value(adam, total_cost_with_vat)), 0) AS total_value,
                   sum(CASE WHEN cancelled THEN 1 ELSE 0 END) AS n_cancelled
            FROM proc.procurement_act
            WHERE authority_id = ANY(%s)
            GROUP BY type
            ORDER BY type
        """, (member_ids,))
        by_type = c.fetchall()

        # Top CPV divisions (first 2 digits) with the catalog label resolved
        # by taking the shortest CPV that starts with that division prefix —
        # the canonical division-level entry. The catalog uses real EU
        # checksums (e.g. 33000000-0, 45000000-7), so we can't hardcode them.
        c.execute(f"""
            WITH agg AS (
              SELECT substr(oc.cpv_code, 1, 2) AS division,
                     count(DISTINCT a.adam) AS n_acts,
                     coalesce(sum(a.total_cost_with_vat), 0) AS total_value
              FROM proc.procurement_act a
              JOIN proc.act_object_detail od ON od.adam = a.adam
              JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
              WHERE a.authority_id = ANY(%s) AND a.type = 'notice'
              GROUP BY substr(oc.cpv_code, 1, 2)
            )
            SELECT agg.division, agg.n_acts, agg.total_value,
              (SELECT {_desc_col(lang, "cpv_code")} FROM proc.cpv_code
               WHERE substr(cpv_code, 1, 2) = agg.division
                 AND substr(cpv_code, 3, 6) = '000000'
               LIMIT 1) AS label
            FROM agg
            ORDER BY agg.total_value DESC NULLS LAST
            LIMIT 8
        """, (member_ids,))
        top_cpv = c.fetchall()

        # Paginated act list, optionally filtered to one type.
        where = ["a.authority_id = ANY(%s)"]
        args: list = [member_ids]
        if type:
            where.append("a.type = %s::proc.act_type")
            args.append(type)
        where_sql = " AND ".join(where)
        c.execute(f"SELECT count(*) AS n FROM proc.procurement_act a WHERE {where_sql}", args)
        total = c.fetchone()["n"]

        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT a.adam, a.type, a.title, a.signed_date, a.submission_date,
                   a.total_cost_with_vat,
                   proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
                   (proc.resolved_value(a.adam, a.total_cost_with_vat)
                       IS DISTINCT FROM a.total_cost_with_vat) AS is_corrected,
                   a.cancelled, a.is_modified
            FROM proc.procurement_act a
            WHERE {where_sql}
            ORDER BY a.submission_date DESC NULLS LAST,
                     a.signed_date DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, args + [per_page, offset])
        acts = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    grand_total = sum((r["n"] or 0) for r in by_type)
    # Headline value is CONTRACTS ONLY — summing across types would double-count
    # (an auction and its follow-on contract are the same award). The by_type
    # table still shows every type's breakdown; only this headline is restricted.
    grand_value = sum(float(r["total_value"] or 0)
                      for r in by_type if r["type"] == "contract")

    return templates.TemplateResponse(
        request, "beta_authority.html",
        {"a": auth, "by_type": by_type, "top_cpv": top_cpv,
         "acts": acts, "total": total, "type_filter": type, "merge_info": merge_info,
         "grand_total": grand_total, "grand_value": grand_value,
         "page": page, "per_page": per_page, "total_pages": total_pages,
         "nav_active": "authorities"},
    )


# ---------------------------------------------------------------------------- #
# Contractor drill-down: /contractor/{vat}
#   All acts where this economic operator was a winner / member.
# ---------------------------------------------------------------------------- #
@app.get("/contractor/{vat}", response_class=HTMLResponse)
def contractor_detail(vat: str, request: Request,
                      page: int = Query(1, ge=1),
                      per_page: int = Query(25, ge=1, le=100)):
    """Every act this contractor is recorded on, with totals & top buyers."""
    lang = _i18n.lang_from_request(request)
    with cursor() as c:
        c.execute("""
            SELECT operator_id, vat_number, name, is_greek_vat, country,
                   first_seen, last_seen,
                   statistical_or_tax_number, contact_person, orgdb_id, ar_gemi,
                   city, postal_code, nuts_code, street_address,
                   contact_email, contact_phone, contact_fax, contact_url
            FROM proc.economic_operator WHERE vat_number = %s
        """, (vat,))
        op = c.fetchone()
        if not op:
            raise HTTPException(status_code=404,
                                detail=f"contractor with VAT {vat} not found")

        # Resolve merge group: gather ALL member VATs (this one if unmerged),
        # then all their operator_ids so we can aggregate across the duplicates.
        grp = resolve_entity_group(c, "contractor", vat)
        member_vats = grp["members"] if grp else [vat]
        c.execute("""SELECT operator_id, vat_number, name
                     FROM proc.economic_operator WHERE vat_number = ANY(%s)""",
                  (member_vats,))
        member_rows = c.fetchall()
        op_ids = [r["operator_id"] for r in member_rows]
        if not op_ids:
            op_ids = [op["operator_id"]]

        # Build the merge banner context (shown only when actually merged).
        merge_info = None
        if grp:
            # Canonical display name: override, else the canonical row's name.
            canon_name = grp["display_name"]
            if not canon_name:
                c.execute("""SELECT name FROM proc.economic_operator
                             WHERE vat_number=%s""", (grp["canonical_key"],))
                cr = c.fetchone()
                canon_name = cr["name"] if cr else op["name"]
            op = dict(op)
            op["name"] = canon_name
            op["vat_number"] = grp["canonical_key"]
            merge_info = {
                "canonical_key": grp["canonical_key"],
                "members": [{"vat": r["vat_number"], "name": r["name"]}
                            for r in member_rows],
                "n": len(member_rows),
            }

        # Aggregates per act type (mostly payment/contract for winners).
        c.execute("""
            SELECT a.type,
                   count(*) AS n,
                   coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                         proc.resolved_value(a.adam, a.total_cost_with_vat))), 0) AS total_value
            FROM proc.act_operator ao
            JOIN proc.procurement_act a ON a.adam = ao.adam
            WHERE ao.operator_id = ANY(%s)
            GROUP BY a.type
            ORDER BY a.type
        """, (op_ids,))
        by_type = c.fetchall()

        # Top buying authorities (who pays this contractor most). Contracts only
        # — auctions are the pre-contract stage and would double-count the same
        # award (an auction can also split into several contracts).
        c.execute("""
            SELECT auth.org_id, auth.name,
                   count(DISTINCT a.adam) AS n_acts,
                   coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                         proc.resolved_value(a.adam, a.total_cost_with_vat))), 0) AS total_value
            FROM proc.act_operator ao
            JOIN proc.procurement_act a ON a.adam = ao.adam
            LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
            WHERE ao.operator_id = ANY(%s) AND a.type = 'contract'
            GROUP BY auth.org_id, auth.name
            ORDER BY total_value DESC NULLS LAST
            LIMIT 10
        """, (op_ids,))
        top_buyers = c.fetchall()

        # Top CPV divisions this contractor has supplied. Contracts only, for
        # the same anti-double-count reason as above.
        c.execute(f"""
            WITH agg AS (
              SELECT substr(oc.cpv_code, 1, 2) AS division,
                     count(DISTINCT a.adam) AS n_acts,
                     coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                           proc.resolved_value(a.adam, a.total_cost_with_vat))), 0) AS total_value
              FROM proc.act_operator ao
              JOIN proc.procurement_act a ON a.adam = ao.adam
              JOIN proc.act_object_detail od ON od.adam = a.adam
              JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
              WHERE ao.operator_id = ANY(%s) AND a.type = 'contract'
              GROUP BY substr(oc.cpv_code, 1, 2)
            )
            SELECT agg.division, agg.n_acts, agg.total_value,
              (SELECT {_desc_col(lang, "cpv_code")} FROM proc.cpv_code
               WHERE substr(cpv_code, 1, 2) = agg.division
                 AND substr(cpv_code, 3, 6) = '000000'
               LIMIT 1) AS label
            FROM agg
            ORDER BY agg.total_value DESC NULLS LAST
            LIMIT 8
        """, (op_ids,))
        top_cpv = c.fetchall()

        # Paginated act list.
        c.execute("""
            SELECT count(*) AS n FROM proc.act_operator WHERE operator_id = ANY(%s)
        """, (op_ids,))
        total = c.fetchone()["n"]
        offset = (page - 1) * per_page
        c.execute("""
            SELECT a.adam, a.type, a.title, a.signed_date, a.submission_date,
                   a.total_cost_with_vat,
                   proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
                   (proc.resolved_value(a.adam, a.total_cost_with_vat)
                       IS DISTINCT FROM a.total_cost_with_vat) AS is_corrected,
                   a.cancelled,
                   ao.role, ao.awarded_value_with_vat,
                   auth.org_id AS authority_id, auth.name AS authority_name
            FROM proc.act_operator ao
            JOIN proc.procurement_act a ON a.adam = ao.adam
            LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
            WHERE ao.operator_id = ANY(%s)
            ORDER BY a.submission_date DESC NULLS LAST,
                     a.signed_date DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, (op_ids, per_page, offset))
        acts = c.fetchall()

        # ΓΕΜΗ registry enrichment (address / contact / ΚΑΔ), if we have it for
        # this ΑΦΜ. For a merged contractor, the enriched row may sit under any
        # member VAT, so try the canonical first, then the others. Only 'ok'
        # rows carry real data. ΑΦΜ may be stored 8-digit upstream, so compare
        # on the zero-padded 9-digit form the enrichment is keyed by.
        candidate_vats = [op["vat_number"]] + [
            r["vat_number"] for r in member_rows
            if r["vat_number"] != op["vat_number"]]
        gemi = None
        for cand in candidate_vats:
            if not cand:
                continue
            padded = cand.strip().upper().removeprefix("EL").strip().zfill(9)
            c.execute("""SELECT legal_name, trade_title, legal_type, status,
                                street, street_number, zip_code, city,
                                municipality, prefecture, phone, fax, email, url,
                                primary_kad, primary_kad_descr, activities_active,
                                ar_gemi, incorporation_date, fetched_at
                         FROM proc.gemi_enrichment
                         WHERE afm = %s AND fetch_status = 'ok'""", (padded,))
            gemi = c.fetchone()
            if gemi:
                break

    total_pages = max(1, (total + per_page - 1) // per_page)
    grand_total = sum((r["n"] or 0) for r in by_type)
    # Contracts only — an auction and its follow-on contract are the same award,
    # so summing across types double-counts. by_type still shows the breakdown.
    grand_value = sum(float(r["total_value"] or 0)
                      for r in by_type if r["type"] == "contract")

    return templates.TemplateResponse(
        request, "beta_contractor.html",
        {"op": op, "by_type": by_type, "top_buyers": top_buyers,
         "top_cpv": top_cpv,
         "acts": acts, "total": total, "merge_info": merge_info,
         "gemi": gemi,
         "gemi_refresh_url": f"/contractor/{op['vat_number']}/gemi-refresh",
         "grand_total": grand_total, "grand_value": grand_value,
         "page": page, "per_page": per_page, "total_pages": total_pages,
         "nav_active": "contractors"},
    )


# ---------------------------------------------------------------------------- #
# On-demand ΓΕΜΗ enrichment (admin-only via the app-wide BasicAuth middleware).
# A button on the contractor/authority page POSTs here; we call the registry
# for that single ΑΦΜ, upsert, and return the refreshed _gemi_block partial for
# HTMX to swap in place. Same fetch/flatten/upsert as the batch script (shared
# gemi_client module), so results are identical. Fetch-and-overwrite: creates
# the row first time, refreshes it thereafter.
# ---------------------------------------------------------------------------- #
def _render_gemi_block(request: Request, gemi_row, refresh_url: str,
                       message: str | None = None, tone: str = "ok"):
    return templates.TemplateResponse(
        request, "_gemi_block.html",
        {"gemi": gemi_row, "gemi_refresh_url": refresh_url,
         "gemi_flash": message, "gemi_flash_tone": tone},
    )


def _refetch_gemi(c, afm_raw: str):
    """Run enrich_one and return the freshly-stored row (or None) for display."""
    import gemi_client
    status, afm = gemi_client.enrich_one(c, afm_raw)
    gemi_row = None
    if afm:
        c.execute("""SELECT legal_name, trade_title, legal_type, status,
                            street, street_number, zip_code, city, municipality,
                            prefecture, phone, fax, email, url, primary_kad,
                            primary_kad_descr, activities_active, ar_gemi,
                            incorporation_date, fetched_at
                     FROM proc.gemi_enrichment
                     WHERE afm = %s AND fetch_status = 'ok'""", (afm,))
        gemi_row = c.fetchone()
    return status, gemi_row


@app.post("/contractor/{vat}/gemi-refresh", response_class=HTMLResponse)
def contractor_gemi_refresh(vat: str, request: Request):
    url = f"/contractor/{vat}/gemi-refresh"
    with cursor() as c:
        c.execute("SELECT vat_number FROM proc.economic_operator WHERE vat_number=%s",
                  (vat,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="contractor not found")
        try:
            status, gemi_row = _refetch_gemi(c, vat)
        except RuntimeError:
            return _render_gemi_block(
                request, None, url,
                "Το κλειδί ΓΕΜΗ (GEMI_API_KEY) δεν είναι ορισμένο στον διακομιστή.",
                "error")
    msg, tone = _gemi_status_message(status)
    return _render_gemi_block(request, gemi_row, url, msg, tone)


@app.get("/contractor/{vat}/name-cancel", response_class=HTMLResponse)
def contractor_name_cancel(vat: str, request: Request):
    with cursor() as c:
        c.execute("SELECT name FROM proc.economic_operator WHERE vat_number=%s",
                  (vat,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="contractor not found")
    return _name_heading(request, "contractor", vat, row["name"])


@app.get("/authority/{org_id}/name-cancel", response_class=HTMLResponse)
def authority_name_cancel(org_id: str, request: Request):
    with cursor() as c:
        c.execute("SELECT name FROM proc.authority WHERE org_id=%s", (org_id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="authority not found")
    return _name_heading(request, "authority", org_id, row["name"])


def _gemi_status_message(status: str) -> tuple[str, str]:
    return {
        "ok":        ("Τα στοιχεία ΓΕΜΗ ενημερώθηκαν.", "ok"),
        "not_found": ("Δεν βρέθηκε εγγραφή στο ΓΕΜΗ για αυτόν τον ΑΦΜ.", "warn"),
        "ambiguous": ("Βρέθηκαν πολλαπλές εγγραφές — εμφανίζεται η πιο σχετική.", "warn"),
        "bad_afm":   ("Μη έγκυρος ΑΦΜ — δεν έγινε αναζήτηση.", "error"),
        "error":     ("Σφάλμα κατά την επικοινωνία με το ΓΕΜΗ — δοκιμάστε ξανά.", "error"),
    }.get(status, ("", "ok"))


# ---------------------------------------------------------------------------- #
# Editable entity names (admin-only via app-wide BasicAuth). Curators can
# correct garbled / wrong names. The edit overwrites `name`; the first edit
# snapshots the original ingested value into name_original (recoverable). The
# heading on the detail page is an HTMX-swappable fragment: a pencil reveals an
# inline form, submit swaps the heading back with the new name.
#
# NOTE on future roles: these routes sit behind the same BasicAuth as the rest
# of the app, so they're curator-only today. When real ADMIN/curator roles
# arrive, gate THESE routes (and the gemi-refresh routes) behind the new
# permission check — the templates/logic don't change, only the gate.
# ---------------------------------------------------------------------------- #
def _name_heading(request: Request, kind: str, ident: str, name: str,
                  editing: bool = False, edited: bool = False):
    """Render the editable-name heading fragment. kind is 'contractor' or
    'authority'; ident is the URL id (vat or org_id)."""
    return templates.TemplateResponse(
        request, "_editable_name.html",
        {"kind": kind, "ident": ident, "name": name,
         "editing": editing, "edited": edited},
    )


@app.get("/contractor/{vat}/name-edit", response_class=HTMLResponse)
def contractor_name_form(vat: str, request: Request):
    with cursor() as c:
        c.execute("SELECT name FROM proc.economic_operator WHERE vat_number=%s",
                  (vat,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="contractor not found")
    return _name_heading(request, "contractor", vat, row["name"], editing=True)


@app.post("/contractor/{vat}/name-edit", response_class=HTMLResponse)
def contractor_name_save(vat: str, request: Request, name: str = Form(...)):
    new = (name or "").strip()
    with cursor() as c:
        c.execute("SELECT name FROM proc.economic_operator WHERE vat_number=%s",
                  (vat,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="contractor not found")
        if not new:
            # empty submission → just re-show current name, no change
            return _name_heading(request, "contractor", vat, row["name"])
        # snapshot original only on the first edit (name_original still NULL)
        c.execute("""UPDATE proc.economic_operator
                     SET name_original = COALESCE(name_original, name),
                         name = %s,
                         name_edited_at = now()
                     WHERE vat_number = %s
                     RETURNING name""", (new, vat))
        saved = c.fetchone()["name"]
    return _name_heading(request, "contractor", vat, saved, edited=True)


@app.get("/authority/{org_id}/name-edit", response_class=HTMLResponse)
def authority_name_form(org_id: str, request: Request):
    with cursor() as c:
        c.execute("SELECT name FROM proc.authority WHERE org_id=%s", (org_id,))
        row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="authority not found")
    return _name_heading(request, "authority", org_id, row["name"], editing=True)


@app.post("/authority/{org_id}/name-edit", response_class=HTMLResponse)
def authority_name_save(org_id: str, request: Request, name: str = Form(...)):
    new = (name or "").strip()
    with cursor() as c:
        c.execute("SELECT name FROM proc.authority WHERE org_id=%s", (org_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="authority not found")
        if not new:
            return _name_heading(request, "authority", org_id, row["name"])
        c.execute("""UPDATE proc.authority
                     SET name_original = COALESCE(name_original, name),
                         name = %s,
                         name_edited_at = now()
                     WHERE org_id = %s
                     RETURNING name""", (new, org_id))
        saved = c.fetchone()["name"]
    return _name_heading(request, "authority", org_id, saved, edited=True)
