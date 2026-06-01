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
from contextlib import contextmanager
from datetime import date
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, Query, HTTPException
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
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

# Sanity ceiling for contract values (with VAT). Contracts above this are
# treated as data errors (KHMDHS sometimes inflates values ~1000x) and excluded
# from analytics aggregates — see analytics_exclusion_migration.sql, which holds
# the authoritative copy of this number. Kept in sync here so the UI can badge
# excluded contracts. A contract is also "excluded" if flagged 'suspicious'.
ANALYTICS_VALUE_CEILING = 500_000_000
templates.env.globals["ANALYTICS_VALUE_CEILING"] = ANALYTICS_VALUE_CEILING

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
    a.cancelled,
    a.is_modified,
    a.contract_type_code,
    a.procedure_type_code,
    a.nuts_code,
    auth.org_id      AS authority_id,
    auth.name        AS authority_name
"""


def build_where(params: dict) -> tuple[str, list]:
    """Translate query parameters into a parameterised WHERE clause."""
    where: list[str] = []
    args: list = []

    # Act type — the primary filter. Empty/absent => all types. Validate against
    # the known set so the value is safe to cast to the enum.
    act_type = (params.get("type") or "").strip()
    if act_type in TYPE_LABELS:
        where.append("a.type = %s::proc.act_type")
        args.append(act_type)

    q = (params.get("q") or "").strip()
    if q:
        # Both sides: unaccent + lower + map final-sigma (ς) to medial (σ), so
        # 'καθαριότητας' and 'καθαριοτητασ' match the same content.
        norm = "translate(proc.f_unaccent(lower({col})), 'ς', 'σ')"
        where.append(f"{norm.format(col='a.title')} LIKE {norm.format(col='%s')}")
        args.append(f"%{q}%")

    auth_id = (params.get("authority") or "").strip()
    if auth_id:
        where.append("a.authority_id = %s")
        args.append(auth_id)

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

    contract_type = (params.get("contract_type") or "").strip()
    if contract_type:
        where.append("a.contract_type_code = %s")
        args.append(contract_type)

    procedure_type = (params.get("procedure_type") or "").strip()
    if procedure_type:
        where.append("a.procedure_family = %s")
        args.append(procedure_type)

    nuts = (params.get("nuts") or "").strip()
    if nuts:
        # Prefix match: 'EL' matches all of Greece, 'EL5' matches a region cluster.
        where.append("a.nuts_code LIKE %s")
        args.append(f"{nuts}%")

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
    sort = SORT_COLS.get(params.get("sort") or DEFAULT_SORT, SORT_COLS[DEFAULT_SORT])
    sql = f"""
        SELECT {SELECT_COLS}
        FROM proc.procurement_act a
        LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
        WHERE {where}
        ORDER BY {sort}
        LIMIT %s OFFSET %s
    """
    count_sql = f"""
        SELECT count(*) AS n,
               coalesce(sum(a.total_cost_with_vat), 0) AS total_value
        FROM proc.procurement_act a
        WHERE {where}
    """
    with cursor() as c:
        c.execute(count_sql, args)
        agg = c.fetchone()
        c.execute(sql, args + [limit, offset])
        rows = c.fetchall()
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

    return templates.TemplateResponse(request, "analytics.html", data)


@app.get("/", response_class=HTMLResponse)
def home(request: Request,
         page: int = Query(1, ge=1),
         per_page: int = Query(25, ge=1, le=100)):
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
        return templates.TemplateResponse(request, "_results.html", ctx)
    # Full page render. The template inlines the partial so the first paint
    # already has results — no second roundtrip from a hx-trigger='load'.
    ctx["lk"] = lookups()
    return templates.TemplateResponse(request, "index.html", ctx)


def _params_from(request: Request) -> dict:
    """Pull all known query params into a plain dict, dropping empties."""
    keys = ("type", "q", "authority", "cpv", "contract_type", "procedure_type", "nuts",
            "date_from", "date_to", "deadline_from", "deadline_to",
            "value_min", "value_max",
            "status", "sort")
    return {k: request.query_params.get(k, "") for k in keys}


@app.get("/explore", response_class=HTMLResponse)
def explore(request: Request):
    """Aggregated breakdown of the *same* filtered set as the main search.
    Two tables: by authority and by contractor (both merge-aware, ranked by
    value). Honors the same analytics exclusions (cancelled / over-ceiling /
    suspicious-flagged) so totals are consistent with the dashboard."""
    params = _params_from(request)
    where, args = build_where(params)

    # Same exclusion the analytics use, expressed inline so it applies to the
    # filtered population. Cancelled already handled by status filter sometimes,
    # but we enforce it here too for consistency.
    eligible = """
        NOT a.cancelled
        AND (a.total_cost_with_vat IS NULL
             OR a.total_cost_with_vat <= %s)
        AND NOT EXISTS (SELECT 1 FROM proc.v_act_annotation_current an
                        WHERE an.adam = a.adam AND an.flag = 'suspicious')
    """
    ceiling = ANALYTICS_VALUE_CEILING

    by_authority, by_contractor = [], []
    grand = {"n": 0, "value": 0.0}
    try:
        with cursor() as c:
            # --- by authority ---
            c.execute(f"""
                SELECT proc.canon_authority(a.authority_id) AS key,
                       max(auth.name)                       AS name,
                       count(*)                             AS n,
                       coalesce(sum(a.total_cost_with_vat), 0) AS value
                FROM proc.procurement_act a
                LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                WHERE {where} AND a.authority_id IS NOT NULL AND {eligible}
                GROUP BY proc.canon_authority(a.authority_id)
                ORDER BY value DESC, n DESC
                LIMIT 100
            """, (*args, ceiling))
            by_authority = c.fetchall()

            # --- by contractor (needs the operator join; value is contract-only) ---
            c.execute(f"""
                SELECT proc.canon_contractor(eo.vat_number) AS key,
                       max(eo.name)                          AS name,
                       count(DISTINCT a.adam)                AS n,
                       coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                             a.total_cost_with_vat)), 0) AS value
                FROM proc.procurement_act a
                JOIN proc.act_operator ao ON ao.adam = a.adam
                JOIN proc.economic_operator eo ON eo.operator_id = ao.operator_id
                WHERE {where} AND {eligible}
                GROUP BY proc.canon_contractor(eo.vat_number)
                ORDER BY value DESC, n DESC
                LIMIT 100
            """, (*args, ceiling))
            by_contractor = c.fetchall()

            # --- grand totals for the filtered, eligible set ---
            c.execute(f"""
                SELECT count(*) AS n,
                       coalesce(sum(a.total_cost_with_vat), 0) AS value
                FROM proc.procurement_act a
                WHERE {where} AND {eligible}
            """, (*args, ceiling))
            g = c.fetchone()
            grand = {"n": g["n"], "value": float(g["value"] or 0)}
    except Exception:
        # If annotation view or merge functions aren't present, degrade to empty.
        by_authority, by_contractor = [], []

    return templates.TemplateResponse(
        request, "explore.html",
        {"by_authority": by_authority,
         "by_contractor": by_contractor,
         "grand": grand,
         "params": params,
         "lk": lookups(),
         "active_filters": {k: v for k, v in params.items() if v}},
    )


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
            SELECT od.short_description, od.quantity, od.unit_code,
                   u.name AS unit_name,
                   od.cost_without_vat, od.vat_rate, od.currency_code,
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
            GROUP BY od.id, u.name
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

        # Downstream chain via the forward-only view we fixed earlier.
        c.execute("""
            SELECT v.adam, v.depth, v.via,
                   a.type, a.title, a.signed_date, a.total_cost_with_vat,
                   a.cancelled
            FROM proc.v_act_chain v
            LEFT JOIN proc.procurement_act a ON a.adam = v.adam
            WHERE v.root = %s AND v.depth > 0
            ORDER BY v.depth, v.adam
        """, (adam,))
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
            c.execute("""SELECT note, tags, flag, author, created_at
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
        if val is not None and val > ANALYTICS_VALUE_CEILING:
            excluded_reason = "over_threshold"
        elif annotation and annotation.get("flag") == "suspicious":
            excluded_reason = "flagged"

    return templates.TemplateResponse(
        request, "notice.html",
        {"n": notice,
         "line_items": line_items,
         "operators": operators,
         "downstream": downstream,
         "incoming": incoming,
         "annotation": annotation,
         "excluded_reason": excluded_reason},
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
        c.execute(f"""
            WITH base AS (
              SELECT auth.org_id, auth.name, auth.vat_number,
                     EXISTS (SELECT 1 FROM proc.entity_member m
                             WHERE m.kind='authority' AND m.member_key=auth.org_id)
                       AS is_merged
              FROM proc.authority auth
              WHERE {where_sql}
            ),
            members AS (
              SELECT b.org_id AS canon,
                     COALESCE(
                       (SELECT array_agg(m2.member_key)
                        FROM proc.entity_member m1
                        JOIN proc.entity_member m2 ON m2.group_id=m1.group_id
                        WHERE m1.kind='authority' AND m1.member_key=b.org_id),
                       ARRAY[b.org_id]
                     ) AS ids
              FROM base b
            )
            SELECT b.org_id, b.name, b.vat_number, b.is_merged,
                   COALESCE(agg.n_acts, 0) AS n_acts,
                   COALESCE(agg.n_notices, 0) AS n_notices,
                   COALESCE(agg.n_contracts, 0) AS n_contracts
            FROM base b
            JOIN members mm ON mm.canon = b.org_id
            LEFT JOIN LATERAL (
              SELECT count(a.adam) AS n_acts,
                     count(a.adam) FILTER (WHERE a.type='notice')   AS n_notices,
                     count(a.adam) FILTER (WHERE a.type='contract') AS n_contracts
              FROM proc.procurement_act a
              WHERE a.authority_id = ANY(mm.ids)
            ) agg ON true
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, search_args + [per_page, offset])
        rows = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request, "authorities_index.html",
        {"rows": rows, "q": q, "sort": sort, "total": total,
         "page": page, "per_page": per_page, "total_pages": total_pages})


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

        # Strategy: aggregate act counts per operator in ONE grouped pass
        # (uses ix on act_operator.operator_id), map each operator to its
        # canonical key, sum per canonical, then sort+limit. Only the buyers
        # distinct-count is deferred to the page's rows. This avoids a
        # per-row correlated subquery over the whole acts table.
        c.execute(f"""
            WITH canon AS (
              -- every surviving (canonical or unmerged) operator row
              SELECT eo.vat_number, eo.name, eo.is_greek_vat, eo.country,
                     EXISTS (SELECT 1 FROM proc.entity_member m
                             WHERE m.kind='contractor' AND m.member_key=eo.vat_number)
                       AS is_merged
              FROM proc.economic_operator eo
              WHERE {where_sql}
            ),
            -- map ANY vat -> its canonical vat (itself if unmerged)
            keymap AS (
              SELECT eo.vat_number AS member_vat, eo.operator_id,
                     COALESCE(g.canonical_key, eo.vat_number) AS canon_vat
              FROM proc.economic_operator eo
              LEFT JOIN proc.entity_member m
                ON m.kind='contractor' AND m.member_key = eo.vat_number
              LEFT JOIN proc.entity_group g ON g.id = m.group_id
            ),
            counts AS (
              SELECT k.canon_vat,
                     count(ao.adam) AS n_acts,
                     count(DISTINCT a.authority_id) AS n_buyers
              FROM keymap k
              JOIN proc.act_operator ao ON ao.operator_id = k.operator_id
              LEFT JOIN proc.procurement_act a ON a.adam = ao.adam
              WHERE k.canon_vat IN (SELECT vat_number FROM canon)
              GROUP BY k.canon_vat
            )
            SELECT c.vat_number, c.name, c.is_greek_vat, c.country, c.is_merged,
                   COALESCE(ct.n_acts, 0)   AS n_acts,
                   COALESCE(ct.n_buyers, 0) AS n_buyers
            FROM canon c
            LEFT JOIN counts ct ON ct.canon_vat = c.vat_number
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, search_args + [per_page, offset])
        rows = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request, "contractors_index.html",
        {"rows": rows, "q": q, "sort": sort, "total": total,
         "page": page, "per_page": per_page, "total_pages": total_pages})


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
                   coalesce(sum(total_cost_with_vat), 0) AS total_value,
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
               WHERE cpv_code LIKE agg.division || '000000-_'
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
                   a.total_cost_with_vat, a.cancelled, a.is_modified
            FROM proc.procurement_act a
            WHERE {where_sql}
            ORDER BY a.submission_date DESC NULLS LAST,
                     a.signed_date DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, args + [per_page, offset])
        acts = c.fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    grand_total = sum((r["n"] or 0) for r in by_type)
    grand_value = sum(float(r["total_value"] or 0) for r in by_type)

    return templates.TemplateResponse(
        request, "authority.html",
        {"a": auth, "by_type": by_type, "top_cpv": top_cpv,
         "acts": acts, "total": total, "type_filter": type, "merge_info": merge_info,
         "grand_total": grand_total, "grand_value": grand_value,
         "page": page, "per_page": per_page, "total_pages": total_pages},
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
                                         a.total_cost_with_vat)), 0) AS total_value
            FROM proc.act_operator ao
            JOIN proc.procurement_act a ON a.adam = ao.adam
            WHERE ao.operator_id = ANY(%s)
            GROUP BY a.type
            ORDER BY a.type
        """, (op_ids,))
        by_type = c.fetchall()

        # Top buying authorities (who pays this contractor most).
        c.execute("""
            SELECT auth.org_id, auth.name,
                   count(DISTINCT a.adam) AS n_acts,
                   coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                         a.total_cost_with_vat)), 0) AS total_value
            FROM proc.act_operator ao
            JOIN proc.procurement_act a ON a.adam = ao.adam
            LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
            WHERE ao.operator_id = ANY(%s)
            GROUP BY auth.org_id, auth.name
            ORDER BY total_value DESC NULLS LAST
            LIMIT 10
        """, (op_ids,))
        top_buyers = c.fetchall()

        # Top CPV divisions this contractor has supplied across all their acts.
        # Division-level label resolved via prefix LIKE (the catalog has real
        # EU checksums on the division-level entries, not always '-0').
        c.execute("""
            WITH agg AS (
              SELECT substr(oc.cpv_code, 1, 2) AS division,
                     count(DISTINCT a.adam) AS n_acts,
                     coalesce(sum(coalesce(ao.awarded_value_with_vat,
                                           a.total_cost_with_vat)), 0) AS total_value
              FROM proc.act_operator ao
              JOIN proc.procurement_act a ON a.adam = ao.adam
              JOIN proc.act_object_detail od ON od.adam = a.adam
              JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
              WHERE ao.operator_id = ANY(%s)
              GROUP BY substr(oc.cpv_code, 1, 2)
            )
            SELECT agg.division, agg.n_acts, agg.total_value,
              (SELECT description FROM proc.cpv_code
               WHERE cpv_code LIKE agg.division || '000000-_'
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
                   a.total_cost_with_vat, a.cancelled,
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

    total_pages = max(1, (total + per_page - 1) // per_page)
    grand_total = sum((r["n"] or 0) for r in by_type)
    grand_value = sum(float(r["total_value"] or 0) for r in by_type)

    return templates.TemplateResponse(
        request, "contractor.html",
        {"op": op, "by_type": by_type, "top_buyers": top_buyers,
         "top_cpv": top_cpv,
         "acts": acts, "total": total, "merge_info": merge_info,
         "grand_total": grand_total, "grand_value": grand_value,
         "page": page, "per_page": per_page, "total_pages": total_pages},
    )
