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

# Whether the tender-table extraction feature is mounted (see the /tables
# router registration below). Templates use this to show/hide the act-detail
# "Εξαγωγή πινάκων" button so it never points at a route that isn't there.
templates.env.globals["tables_enabled"] = (
    os.environ.get("TABLES_ENABLED", "1") == "1"
)

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
templates.env.filters["type_label"] = lambda code: TYPE_LABELS.get(code, code or "—")
# These need to tolerate ints too (in case the column stored as text contains a
# numeric string that arrived from JSON as int); we str() before lookup.
templates.env.filters["contract_type_label"] = (
    lambda code: CONTRACT_TYPES.get(str(code) if code is not None else "", code or "—"))
templates.env.filters["procedure_type_label"] = (
    lambda code: PROCEDURE_TYPES.get(str(code) if code is not None else "", code or "—"))
templates.env.filters["assign_criteria_label"] = (
    lambda code: ASSIGN_CRITERIA.get(str(code) if code is not None else "", code or "—"))


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
    auth.org_id      AS authority_id,
    auth.name        AS authority_name
"""


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
        # directly instead of the title. ADAMs are case-insensitive here and
        # matched as a prefix, so a partial paste still finds it. Otherwise it's
        # a normal title keyword search.
        if re.fullmatch(r"\d{2}[A-Za-z]{2,6}\d{0,15}", q):
            where.append("a.adam ILIKE %s")
            args.append(f"{q}%")
        else:
            # Both sides: unaccent + lower + map final-sigma (ς) to medial (σ),
            # so 'καθαριότητας' and 'καθαριοτητασ' match the same content.
            norm = "translate(proc.f_unaccent(lower({col})), 'ς', 'σ')"
            where.append(f"{norm.format(col='a.title')} LIKE {norm.format(col='%s')}")
            args.append(f"%{q}%")

    auth_ids = _as_list(params.get("authority"))
    if auth_ids:
        where.append("a.authority_id = ANY(%s)")
        args.append(auth_ids)

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

    cpv = (params.get("cpv") or "").strip()
    if cpv:
        # Notices that have any line item with this CPV (or its prefix).
        where.append("""EXISTS (
            SELECT 1 FROM proc.act_object_detail od
            JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
            WHERE od.adam = a.adam AND oc.cpv_code LIKE %s
        )""")
        # 8-digit CPV codes — let the user enter a prefix like '79' to match
        # everything under "Business services". Always treat as prefix.
        args.append(f"{cpv}%")

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
        # multi-select means match ANY of the chosen prefixes. Build an OR of
        # LIKE prefix tests, each wildcard living in the bind value (never SQL).
        ors = []
        for n in nuts_vals:
            ors.append("a.nuts_code LIKE %s")
            args.append(f"{n}%")
        where.append("(" + " OR ".join(ors) + ")")

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
    return _lookup_cache


# ---------------------------------------------------------------------------- #
# App + routes
# ---------------------------------------------------------------------------- #
app = FastAPI(title="Greek Procurement Explorer", version="0.1.0")

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


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    """Dashboard of deduplicated AWARDED value (contracts only; payments and
    cancelled acts excluded; merged entities consolidated). Reads precomputed
    materialized views — refresh them with SELECT proc.refresh_analytics()."""
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
                c.execute("""SELECT division, label, contract_count, contract_value,
                                    notice_count, notice_value
                             FROM proc.mv_analytics_cpv
                             WHERE contract_value > 0 OR notice_value > 0
                             ORDER BY contract_value DESC, notice_value DESC
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
    single = ("q", "fulltext", "tables_q", "cpv",
              "date_from", "date_to", "deadline_from", "deadline_to",
              "value_min", "value_max", "status", "sort")
    multi = ("type", "authority", "contract_type", "procedure_type", "nuts")
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
                   "contract_type", "procedure_type", "nuts")
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
                   a.full_text, a.full_text_extracted_at, a.full_text_source,
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
        c.execute("""
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
                                                  'desc', c.description)
                               ORDER BY c.cpv_code)
                     FILTER (WHERE c.cpv_code IS NOT NULL),
                     '{}') AS cpvs
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
         "downstream": downstream,
         "incoming": incoming,
         "annotation": annotation,
         "excluded_reason": excluded_reason,
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
    with cursor() as c:
        c.execute("""
            SELECT org_id, name, vat_number, is_greek_vat, aaht,
                   type_code, classification_code, nuts_code,
                   city, postal_code, country
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
        c.execute("""
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
              (SELECT description FROM proc.cpv_code
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
    with cursor() as c:
        c.execute("""
            SELECT operator_id, vat_number, name, is_greek_vat, country,
                   first_seen, last_seen
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
        c.execute("""
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
              (SELECT description FROM proc.cpv_code
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
