"""
tables.py — Tender-table extraction, integrated into KHMDHS.

Mounted under /tables (see main.py). Lets a curator extract tables from a
tender's attachments into Excel, either by fetching the act's official
KHMDHS document by ΑΔΑΜ or by uploading files directly.

This module carries the three extraction modules of the standalone
"Tender Tables" tool UNCHANGED — `extractors.py`, `exporter.py`, `ocr.py`
are kept byte-identical between the two projects so fixes flow between them
by copying three files. All KHMDHS-specific glue lives here.

Design choices (the "public later" hinge):
  * The whole router is gated by the app's BasicAuthMiddleware already, so
    it is curator-only without any per-route dependency. Opening it to the
    public later is a deployment decision, not a code change.
  * OCR is gated SEPARATELY, on the presence of ANTHROPIC_API_KEY — so
    "enable the feature" and "spend my API key on OCR" stay two decisions.
  * The ΑΔΑΜ-fetch path enforces hard caps (max attachment size, max pages
    parsed) up front, so the limits are already in place if strangers ever
    arrive.

The extraction modules take bytes in and tables out; they have no knowledge
of KHMDHS. The only KHMDHS-aware piece is the attachment fetch, which reuses
the same /{segment}/attachment/{ADAM} document URL the detail pages link to.
"""

from __future__ import annotations

import os
import time
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

# Sibling extraction modules. These are kept byte-identical with the standalone
# tool, which runs flat (bare imports). Inside KHMDHS this module is loaded as
# `app.tables`, so try package-relative first, then fall back to the flat layout
# used when running with --app-dir=app. Same defensive pattern main.py uses for
# the admin router.
try:
    from app.exporter import export_separate, export_workbook
    from app.extractors import collect_files, compress_pages, extract_entry
    from app.ocr import (
        OcrError,
        api_key_present,
        ocr_entry,
        page_count,
        render_full,
        render_thumb,
    )
except ImportError:
    from exporter import export_separate, export_workbook
    from extractors import collect_files, compress_pages, extract_entry
    from ocr import (
        OcrError,
        api_key_present,
        ocr_entry,
        page_count,
        render_full,
        render_thumb,
    )

# --------------------------------------------------------------------------- #
# Config / caps
# --------------------------------------------------------------------------- #
# In-memory sessions: fine for an occasional, single-curator workload. Sessions
# are disposable — losing one to a server restart costs a click to re-fetch by
# ΑΔΑΜ, not a re-upload.
SESSIONS: dict[str, dict] = {}
SESSION_TTL_SECONDS = 4 * 3600

# Hard resource caps. These bite on BOTH the upload path and the ΑΔΑΜ-fetch
# path, so the limits are already enforced before the feature is ever exposed
# beyond curators. Override via env if a machine can take more.
MAX_UPLOAD_BYTES = int(os.environ.get("TABLES_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_FETCH_BYTES = int(os.environ.get("TABLES_MAX_FETCH_MB", "80")) * 1024 * 1024

# Official KHMDHS source-document base. Same endpoint the detail pages link to;
# the act type maps to a URL segment. Kept local to this module so the feature
# is self-contained, but it mirrors main.khmdhs_doc_url exactly.
KHMDHS_DOC_BASE = "https://cerpp.eprocurement.gov.gr/khmdhs-opendata"
KHMDHS_DOC_SEGMENT = {
    "request": "request",
    "notice": "notice",
    "auction": "auction",
    "contract": "contract",
    "payment": "payment",
}


def _prune_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for sid in [s for s, v in SESSIONS.items() if v["created"] < cutoff]:
        SESSIONS.pop(sid, None)


def _new_session(entries, errors) -> tuple[str, dict]:
    session_id = uuid.uuid4().hex
    session = {
        "created": time.time(),
        "entries": {e.id: e for e in entries},
        "order": [e.id for e in entries],
        "errors": errors,
        "tables": {},
        "page_sel": {},  # entry_id -> sorted list of selected page numbers
    }
    SESSIONS[session_id] = session
    return session_id, session


def _fetch_act_document(act_type: str, adam: str) -> tuple[bytes, str]:
    """Fetch one act's official KHMDHS document by ΑΔΑΜ, server-side.

    Returns (data, filename). Raises ValueError with a human message on any
    problem (unknown type, network error, oversize, empty). The size cap is
    enforced while streaming, so an oversized response is rejected without
    being fully buffered.
    """
    import urllib.error
    import urllib.request

    seg = KHMDHS_DOC_SEGMENT.get(act_type)
    if not seg:
        raise ValueError(f"Άγνωστος τύπος πράξης: {act_type!r}")
    if not adam:
        raise ValueError("Λείπει ο ΑΔΑΜ.")

    url = f"{KHMDHS_DOC_BASE}/{seg}/attachment/{quote(adam)}"

    # Reuse the OCR module's hardened SSL context (system roots + certifi) so
    # this works on bare macOS Pythons and behind corporate proxies alike.
    try:
        try:
            from app.ocr import _SSL_CTX  # noqa: WPS437 — intentional internal reuse
        except ImportError:
            from ocr import _SSL_CTX  # noqa: WPS437
        ctx = _SSL_CTX
    except Exception:  # pragma: no cover
        ctx = None

    req = urllib.request.Request(url, headers={"User-Agent": "KHMDHS-tables/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FETCH_BYTES:
                    raise ValueError(
                        f"Το έγγραφο ξεπερνά το όριο "
                        f"({MAX_FETCH_BYTES // (1024 * 1024)} MB) — "
                        "κατεβάστε το χειροκίνητα και ανεβάστε ό,τι χρειάζεστε."
                    )
                chunks.append(chunk)
            data = b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError(
                "Δεν βρέθηκε επίσημο έγγραφο για αυτόν τον ΑΔΑΜ στο ΚΗΜΔΗΣ."
            ) from exc
        raise ValueError(f"Σφάλμα λήψης από το ΚΗΜΔΗΣ (HTTP {exc.code}).") from exc
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Αποτυχία λήψης: {exc.__class__.__name__}.") from exc

    if not data:
        raise ValueError("Το ΚΗΜΔΗΣ επέστρεψε κενό έγγραφο.")

    # The attachment endpoint hands back a document (usually a PDF, sometimes a
    # zip of τεύχη). Name it after the ΑΔΑΜ; collect_files sniffs by extension,
    # so give it a sensible one from the content-type when we can.
    fname = f"{adam}.pdf"
    if data[:2] == b"PK":  # zip magic
        fname = f"{adam}.zip"
    return data, fname


# --------------------------------------------------------------------------- #
# Router factory (mirrors admin.make_router)
# --------------------------------------------------------------------------- #
def make_router(templates, cursor) -> APIRouter:
    """Build the /tables router. `cursor` is the same context-manager factory
    main.py hands to the admin router; used here to resolve an act's type from
    its ΑΔΑΜ so we hit the right attachment segment."""
    router = APIRouter(prefix="/tables", tags=["tables"])

    def _page_sel_view(session: dict) -> dict:
        out = {}
        for eid, pages in session.get("page_sel", {}).items():
            out[eid] = {"pages": pages, "label": compress_pages(pages), "n": len(pages)}
        return out

    def _render_results(request, session, session_id, reports):
        tables = {t["id"]: t for r in reports for t in r.tables}
        session["tables"] = tables
        n_main = sum(1 for t in tables.values() if t.get("role") != "fragment")
        n_stitched = sum(1 for t in tables.values() if t.get("role") == "stitched")
        return templates.TemplateResponse(
            request,
            "tables/results.html",
            {
                "session_id": session_id,
                "reports": reports,
                "n_tables": n_main,
                "n_stitched": n_stitched,
                "ocr_available": api_key_present(),
                "page_sel": _page_sel_view(session),
                "PREVIEW_ROWS": 8,
                "PREVIEW_COLS": 10,
            },
        )

    def _act_type(adam: str) -> str | None:
        """Resolve an act's type from the DB (so we fetch the right segment)."""
        with cursor() as c:
            c.execute(
                "SELECT type FROM proc.procurement_act WHERE adam = %s", (adam,)
            )
            row = c.fetchone()
        return row["type"] if row else None

    # ---- entry: landing page (upload + ΑΔΑΜ box) ----
    @router.get("", response_class=HTMLResponse)
    def tables_home(request: Request, adam: str = ""):
        # `adam` prefilled when arriving from an act detail page's button.
        return templates.TemplateResponse(
            request, "tables/index.html",
            {"prefill_adam": adam, "ocr_available": api_key_present()},
        )

    # ---- ΑΔΑΜ-fetch: pull the official document server-side, then scan ----
    @router.post("/fetch", response_class=HTMLResponse)
    def tables_fetch(request: Request, adam: str = Form(...)):
        _prune_sessions()
        adam = adam.strip()
        act_type = _act_type(adam)
        if act_type is None:
            # Not in our DB — fall back to 'notice' segment, which is the most
            # common, but tell the curator we couldn't confirm the type.
            act_type = "notice"
        try:
            data, fname = _fetch_act_document(act_type, adam)
        except ValueError as exc:
            return HTMLResponse(
                f"<div class='tt-flash tt-error'>{exc}</div>", status_code=422
            )

        entries, errors = collect_files(fname, data)
        session_id, session = _new_session(entries, errors)

        # One file, no surprises: scan straight away.
        if len(entries) <= 1 and not errors:
            reports = []
            for e in entries:
                r = extract_entry(e)
                r.entry_id = e.id
                reports.append(r)
            return _render_results(request, session, session_id, reports)

        return templates.TemplateResponse(
            request, "tables/select.html",
            {
                "session_id": session_id,
                "entries": entries,
                "errors": errors,
                "total_size": sum(e.size for e in entries),
                "source_label": f"ΑΔΑΜ {adam}",
            },
        )

    # ---- upload path (secondary) ----
    @router.post("/upload", response_class=HTMLResponse)
    async def tables_upload(request: Request, files: list[UploadFile]):
        _prune_sessions()
        entries, errors, total = [], [], 0
        for f in files:
            data = await f.read()
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                return HTMLResponse(
                    "<div class='tt-flash tt-error'>Πολύ μεγάλο αρχείο "
                    f"(πάνω από {MAX_UPLOAD_BYTES // (1024 * 1024)} MB). "
                    "Χωρίστε το σε μικρότερες παρτίδες.</div>"
                )
            if not f.filename:
                continue
            ents, errs = collect_files(f.filename, data)
            entries.extend(ents)
            errors.extend(errs)

        session_id, session = _new_session(entries, errors)

        if len(entries) <= 1 and not errors:
            reports = []
            for e in entries:
                r = extract_entry(e)
                r.entry_id = e.id
                reports.append(r)
            return _render_results(request, session, session_id, reports)

        return templates.TemplateResponse(
            request, "tables/select.html",
            {
                "session_id": session_id,
                "entries": entries,
                "errors": errors,
                "total_size": sum(e.size for e in entries),
                "source_label": None,
            },
        )

    # ---- scan selected files ----
    @router.post("/scan", response_class=HTMLResponse)
    def tables_scan(
        request: Request,
        session_id: str = Form(...),
        file_ids: list[str] = Form(default=[]),
    ):
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — "
                "ξεκινήστε ξανά.</div>", status_code=410,
            )
        if not file_ids:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Δεν επιλέχθηκε κανένα αρχείο.</div>",
                status_code=400,
            )
        selected = set(file_ids)
        reports = list(session["errors"])
        for eid in session["order"]:
            if eid in selected and eid in session["entries"]:
                sel = session["page_sel"].get(eid)
                r = extract_entry(
                    session["entries"][eid], pages=set(sel) if sel else None
                )
                r.entry_id = eid
                reports.append(r)
        return _render_results(request, session, session_id, reports)

    # ---- page thumbnails / full renders / raw passthrough ----
    @router.get("/thumb")
    def tables_thumb(session_id: str, entry_id: str, page: int = 1):
        session = SESSIONS.get(session_id)
        entry = session["entries"].get(entry_id) if session else None
        if entry is None:
            return Response(status_code=404)
        try:
            jpeg = render_thumb(entry, page)
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    @router.get("/full")
    def tables_full(session_id: str, entry_id: str, page: int = 1):
        session = SESSIONS.get(session_id)
        entry = session["entries"].get(entry_id) if session else None
        if entry is None:
            return Response(status_code=404)
        try:
            jpeg = render_full(entry, page)
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})

    _RAW_TYPES = {
        ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp",
    }

    @router.get("/raw")
    def tables_raw(session_id: str, entry_id: str):
        session = SESSIONS.get(session_id)
        entry = session["entries"].get(entry_id) if session else None
        if entry is None:
            return Response(status_code=404)
        media = _RAW_TYPES.get(entry.ext, "application/octet-stream")
        return Response(
            content=entry.data, media_type=media,
            headers={"Content-Disposition":
                     f"inline; filename*=UTF-8''{quote(entry.name)}"},
        )

    # ---- page picker (PDF page subset) ----
    @router.get("/pages", response_class=HTMLResponse)
    def tables_pages_picker(request: Request, session_id: str, entry_id: str):
        session = SESSIONS.get(session_id)
        entry = session["entries"].get(entry_id) if session else None
        if entry is None or entry.ext != ".pdf":
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Δεν υπάρχει προεπισκόπηση.</div>",
                status_code=404,
            )
        try:
            n = page_count(entry.data)
        except Exception:  # noqa: BLE001
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Αδυναμία ανάγνωσης PDF.</div>",
                status_code=422,
            )
        selected = set(session["page_sel"].get(entry_id, range(1, n + 1)))
        return templates.TemplateResponse(
            request, "tables/_page_picker.html",
            {"session_id": session_id, "entry_id": entry_id,
             "n_pages": n, "selected": selected},
        )

    @router.post("/pages", response_class=HTMLResponse)
    def tables_pages_apply(
        request: Request,
        session_id: str = Form(...),
        entry_id: str = Form(...),
        pages: list[int] = Form(default=[]),
    ):
        session = SESSIONS.get(session_id)
        entry = session["entries"].get(entry_id) if session else None
        if entry is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε.</div>",
                status_code=410,
            )
        n = page_count(entry.data)
        chosen = sorted({p for p in pages if 1 <= p <= n})
        if chosen and len(chosen) < n:
            session["page_sel"][entry_id] = chosen
        else:
            session["page_sel"].pop(entry_id, None)
            chosen = list(range(1, n + 1))
        return templates.TemplateResponse(
            request, "tables/_page_summary.html",
            {"session_id": session_id, "entry_id": entry_id,
             "n_pages": n, "n_sel": len(chosen),
             "label": compress_pages(chosen),
             "restricted": entry_id in session["page_sel"]},
        )

    # ---- OCR (gated separately on ANTHROPIC_API_KEY) ----
    @router.post("/ocr", response_class=HTMLResponse)
    def tables_ocr(
        request: Request,
        session_id: str = Form(...),
        entry_id: str = Form(...),
    ):
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — "
                "ξεκινήστε ξανά.</div>", status_code=410,
            )
        entry = session["entries"].get(entry_id)
        if entry is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Άγνωστο αρχείο.</div>",
                status_code=404,
            )

        try:
            from app.extractors import FileReport
        except ImportError:
            from extractors import FileReport
        retry_status = "scanned" if entry.ext == ".pdf" else "image"
        sel = session["page_sel"].get(entry_id)
        try:
            report = ocr_entry(entry, pages=set(sel) if sel else None)
        except OcrError as exc:
            report = FileReport(entry.source, retry_status, f"OCR failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            report = FileReport(entry.source, retry_status,
                                f"OCR failed: {exc.__class__.__name__}: {exc}")
        report.entry_id = entry_id

        for t in report.tables:
            session["tables"][t["id"]] = t

        return templates.TemplateResponse(
            request, "tables/_file_card.html",
            {
                "report": report,
                "session_id": session_id,
                "PREVIEW_ROWS": 8,
                "PREVIEW_COLS": 10,
                "ocr_available": api_key_present(),
                "ocr_swap": True,
                "page_sel": _page_sel_view(session),
            },
        )

    # ---- export ----
    @router.post("/export")
    def tables_export(
        session_id: str = Form(...),
        mode: str = Form("workbook"),
        table_ids: list[str] = Form(default=[]),
    ):
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<h3>Η συνεδρία έληξε</h3><p>Ξεκινήστε ξανά.</p>",
                status_code=410,
            )
        selected = [session["tables"][tid] for tid in table_ids
                    if tid in session["tables"]]
        if not selected:
            return HTMLResponse(
                "<h3>Καμία επιλογή</h3><p>Επιστρέψτε και επιλέξτε τουλάχιστον "
                "έναν πίνακα.</p>", status_code=400,
            )
        if mode == "separate":
            data, filename, media_type = export_separate(selected)
        else:
            data, filename, media_type = export_workbook(selected)
        return Response(
            content=data, media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
