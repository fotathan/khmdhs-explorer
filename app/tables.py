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
  * The whole router is gated by the app's session AuthMiddleware already
    (/tables is an admin path in _is_admin_path), so it is curator-only
    without any per-route dependency. Opening it to the public later is a
    deployment decision, not a code change.
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
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg.types.json import Json

# Sibling extraction modules. These are kept byte-identical with the standalone
# tool, which runs flat (bare imports). Inside KHMDHS this module is loaded as
# `app.tables`, so try package-relative first, then fall back to the flat layout
# used when running with --app-dir=app. Same defensive pattern main.py uses for
# the admin router.
try:
    from app.exporter import export_separate, export_workbook
    from app.extractors import collect_files, compress_pages, extract_entry, extract_text_from_entry
    from app.ocr import (
        OcrError,
        api_key_present,
        ocr_entry,
        ocr_text_from_entry,
        page_count,
        render_full,
        render_thumb,
    )
except ImportError:
    from exporter import export_separate, export_workbook
    from extractors import collect_files, compress_pages, extract_entry, extract_text_from_entry
    from ocr import (
        OcrError,
        api_key_present,
        ocr_entry,
        ocr_text_from_entry,
        page_count,
        render_full,
        render_thumb,
    )

# Crash/hang-isolated wrappers around the native-PDFium render/OCR functions
# above (see render_safe.py). A malformed PDF that aborts PDFium must not take
# down the web worker, so the endpoints below call these instead of ocr.* direct.
try:
    from app.render_safe import (
        RenderUnavailable, safe_ocr_entry, safe_ocr_text_from_entry,
        safe_page_count, safe_render_full, safe_render_thumb,
    )
except ImportError:
    from render_safe import (
        RenderUnavailable, safe_ocr_entry, safe_ocr_text_from_entry,
        safe_page_count, safe_render_full, safe_render_thumb,
    )

# Item/service-list relevance classifier (KHMDHS-specific — annotates the
# extractor's table dicts so the preview can pre-select the useful tables).
try:
    from app.table_relevance import annotate as annotate_relevance
except ImportError:
    from table_relevance import annotate as annotate_relevance

# --------------------------------------------------------------------------- #
# HTML sanitisation for curator-authored rich full text
# --------------------------------------------------------------------------- #
# full_text_html is rendered with |safe on the detail page, so it MUST be
# sanitised before it is ever stored. We use nh3 (the Rust `ammonia` binding):
# its default allow-list keeps common formatting tags (p, br, h*, ul/ol/li,
# b/strong, i/em, u, a, blockquote, code, table…) and strips <script>, inline
# event handlers, javascript: URLs, etc. Links get rel="noopener noreferrer".
#
# If nh3 is not installed we DO NOT store HTML at all (return None) — the plain
# text in full_text is still saved, so the feature degrades safely rather than
# persisting unsanitised markup.
def sanitize_full_text_html(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        import nh3
    except ImportError:
        return None
    cleaned = nh3.clean(raw).strip()
    # Quill serialises an empty editor as "<p><br></p>"; treat that as no HTML.
    return cleaned or None


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


def _fetch_diavgeia_document(adam: str) -> tuple[bytes, str]:
    """Fetch a Diavgeia act's document (PDF) by ΑΔΑ, server-side.

    Built from the canonical opendata document endpoint with the ΑΔΑ
    percent-encoded — the stored source_url/documentUrl carries the raw Greek
    ΑΔΑ, which urllib can't ASCII-encode. Same caps / hardened SSL / streaming
    as _fetch_act_document. Returns (data, filename); raises ValueError with a
    human message on any problem.
    """
    import urllib.error
    import urllib.request

    if not adam:
        raise ValueError("Λείπει ο ΑΔΑ.")
    url = (
        "https://diavgeia.gov.gr/luminapi/opendata/decisions/"
        f"{quote(adam)}/document"
    )

    try:
        try:
            from app.ocr import _SSL_CTX  # noqa: WPS437 — intentional internal reuse
        except ImportError:
            from ocr import _SSL_CTX  # noqa: WPS437
        ctx = _SSL_CTX
    except Exception:  # pragma: no cover
        ctx = None

    req = urllib.request.Request(
        url, headers={"User-Agent": "KHMDHS-tables/1.0", "Accept": "application/pdf"})
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
                "Δεν βρέθηκε έγγραφο για αυτόν τον ΑΔΑ στη Διαύγεια."
            ) from exc
        raise ValueError(f"Σφάλμα λήψης από τη Διαύγεια (HTTP {exc.code}).") from exc
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Αποτυχία λήψης: {exc.__class__.__name__}.") from exc

    if not data:
        raise ValueError("Η Διαύγεια επέστρεψε κενό έγγραφο.")

    fname = f"{adam}.pdf"
    if data[:2] == b"PK":  # zip magic
        fname = f"{adam}.zip"
    return data, fname


def _looks_garbled(text) -> bool:
    """Reuse the ingester's broken-font heuristic; fail-soft to False."""
    try:
        from khmdhs_ingest import looks_garbled
        return looks_garbled(text)
    except Exception:
        return False


def _local_ocr_enabled() -> bool:
    """Whether the free local OCR tier (Tesseract + Greek) is usable here."""
    try:
        import local_ocr
        return local_ocr.enabled()
    except Exception:
        return False


def _local_ocr_entry(entry, pages) -> str:
    """Local (Tesseract) OCR of one file entry, page-aware — mirrors the Claude
    path (safe_ocr_text_from_entry) but free and offline. Renders pages with the
    app's crash-isolated renderer and OCRs each via local_ocr. Returns text (''
    on anything missing/failed); never raises."""
    try:
        import local_ocr
    except Exception:
        return ""
    if not local_ocr.enabled():
        return ""
    if entry.ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        return ""
    if entry.ext == ".pdf":
        if pages:
            page_nums = sorted(pages)
        else:
            try:
                total = safe_page_count(entry.data)
            except Exception:
                total = 1
            page_nums = list(range(1, min(total, local_ocr.MAX_PAGES) + 1))
    else:
        page_nums = [1]  # a single image file
    parts: list[str] = []
    for pg in page_nums:
        try:
            img = safe_render_full(entry, pg)
        except Exception as e:
            # Don't swallow silently — the reason (e.g. "renderer crashed
            # (exit -9)" = OOM-killed, or "timed out") is the whole diagnosis
            # for "OCR works locally but yields nothing on a small prod instance".
            import logging
            logging.getLogger("khmdhs").warning(
                "local OCR render failed for %s page %s: %s", entry.source, pg, e)
            continue
        txt = local_ocr.ocr_image(img)
        if txt:
            parts.append(txt)
    return "\n".join(parts).strip()


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
        # Relevance pass: pre-select item/service lists; irrelevant tables stay
        # visible but unchecked (per-report so stitch fragments find their parent).
        n_relevant = sum(annotate_relevance(r.tables) for r in reports)
        return templates.TemplateResponse(
            request,
            "tables/results.html",
            {
                "session_id": session_id,
                "reports": reports,
                "n_tables": n_main,
                "n_stitched": n_stitched,
                "n_relevant": n_relevant,
                "ocr_available": api_key_present(),
                "page_sel": _page_sel_view(session),
                "adam": session.get("tables_adam", ""),
                "PREVIEW_ROWS": 8,
                "PREVIEW_COLS": 10,
            },
        )

    def _act_info(adam: str):
        """Resolve an act's type, data source and source document URL — so we
        fetch from the right place (KHMDHS attachment endpoint vs the Diavgeia
        document) and label the identifier (ΑΔΑΜ vs ΑΔΑ) correctly. Returns the
        row (dict) or None if the act isn't in our DB."""
        with cursor() as c:
            c.execute(
                "SELECT type, data_source, source_url "
                "FROM proc.procurement_act WHERE adam = %s", (adam,)
            )
            return c.fetchone()

    # ---- entry: landing page (upload + ΑΔΑΜ box) ----
    @router.get("", response_class=HTMLResponse)
    def tables_home(request: Request, adam: str = ""):
        # Per-act table work now lives in the act-edit hub's "Πίνακες" tab — send
        # any act-specific entry there. With no ΑΔΑΜ this stays the general,
        # standalone extraction tool (arbitrary upload / any ΑΔΑΜ).
        if adam.strip():
            return RedirectResponse(
                url=f"/admin/act/{adam.strip()}/edit?tab=tables", status_code=303)
        return templates.TemplateResponse(
            request, "tables/index.html",
            {"prefill_adam": "", "ocr_available": api_key_present()},
        )

    # ---- ΑΔΑΜ-fetch: pull the official document server-side, then scan ----
    @router.post("/fetch", response_class=HTMLResponse)
    def tables_fetch(request: Request, adam: str = Form(...)):
        _prune_sessions()
        adam = adam.strip()
        info = _act_info(adam)
        data_source = info["data_source"] if info else None
        try:
            if data_source == "diavgeia":
                # Diavgeia document lives at the act's source_url, not KHMDHS.
                data, fname = _fetch_diavgeia_document(adam)
                id_label = "ΑΔΑ"
            else:
                # KHMDHS (or unknown — fall back to the most common 'notice'
                # segment, but the type stays best-effort).
                act_type = (info["type"] if info else None) or "notice"
                data, fname = _fetch_act_document(act_type, adam)
                id_label = "ΑΔΑΜ"
        except ValueError as exc:
            return HTMLResponse(
                f"<div class='tt-flash tt-error'>{exc}</div>", status_code=422
            )

        entries, errors = collect_files(fname, data)
        session_id, session = _new_session(entries, errors)
        session["tables_adam"] = adam

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
                "source_label": f"{id_label} {adam}",
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
            jpeg = safe_render_thumb(entry, page)
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
            jpeg = safe_render_full(entry, page)
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
            n = safe_page_count(entry.data)
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
        try:
            n = safe_page_count(entry.data)
        except RenderUnavailable:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Αδυναμία ανάγνωσης PDF.</div>",
                status_code=422,
            )
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
            report = safe_ocr_entry(entry, pages=set(sel) if sel else None)
        except OcrError as exc:
            report = FileReport(entry.source, retry_status, f"OCR failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            report = FileReport(entry.source, retry_status,
                                f"OCR failed: {exc.__class__.__name__}: {exc}")
        report.entry_id = entry_id

        annotate_relevance(report.tables)
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

    # ------------------------------------------------------------------ #
    # SAVE TO ACT — persist selected tables to proc.extracted_table so they
    # can be shown inline on the act detail page. Same selection UI as Excel
    # export; this is the second submit button on results.html. Saved tables
    # are PUBLISHED on save (is_published = TRUE) so they appear on the act
    # detail page immediately; the curator can unpublish/delete per table from
    # the act edit hub if needed. A table dict carries no separate header
    # field: rows[0] is the header (matching exporter's _write_table, which
    # styles the first data row as the header). We store source + locator +
    # rows verbatim, so the inline render and the per-table Excel re-export
    # use the exact same data the curator previewed.
    # ------------------------------------------------------------------ #
    @router.post("/save", response_class=HTMLResponse)
    def tables_save(
        request: Request,
        session_id: str = Form(...),
        adam: str = Form(...),
        table_ids: list[str] = Form(default=[]),
    ):
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — "
                "ξεκινήστε ξανά.</div>", status_code=410,
            )
        adam = adam.strip()
        if not adam:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Λείπει ο ΑΔΑΜ — η αποθήκευση "
                "στην πράξη απαιτεί ΑΔΑΜ. Ξεκινήστε από κουμπί πράξης ή "
                "συμπληρώστε τον.</div>", status_code=400,
            )
        selected = [session["tables"][tid] for tid in table_ids
                    if tid in session["tables"]]
        if not selected:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Καμία επιλογή — επιλέξτε "
                "τουλάχιστον έναν πίνακα.</div>", status_code=400,
            )
        with cursor() as c:
            c.execute("SELECT 1 FROM proc.procurement_act WHERE adam=%s", (adam,))
            if not c.fetchone():
                return HTMLResponse(
                    "<div class='tt-flash tt-error'>Άγνωστη πράξη — ο ΑΔΑΜ δεν "
                    "αντιστοιχεί σε καταχωρημένη πράξη.</div>", status_code=404,
                )
            n = 0
            for t in selected:
                c.execute(
                    """INSERT INTO proc.extracted_table
                       (adam, source, locator, rows, n_rows, n_cols, is_published)
                       VALUES (%s,%s,%s,%s,%s,%s, TRUE)""",
                    (adam, t["source"], t["locator"],
                     Json(t["rows"]), int(t["n_rows"]), int(t["n_cols"])),
                )
                n += 1
        return HTMLResponse(
            f"<div class='tt-flash tt-ok'>Αποθηκεύτηκαν {n} πίνακ"
            f"{'ας' if n == 1 else 'ες'} στην πράξη (δημοσιευμένοι). "
            f"<a href='/admin/act/{adam}/edit#tables'>διαχείριση ›</a></div>"
        )

    # ------------------------------------------------------------------ #
    # ADMIN — per-table management for one act: list, publish/unpublish
    # (per table), delete. Rendered as a panel inside the act edit hub.
    # ------------------------------------------------------------------ #
    def _act_tables(adam: str) -> list[dict]:
        with cursor() as c:
            c.execute(
                """SELECT id, source, locator, rows, n_rows, n_cols, is_published
                   FROM proc.extracted_table WHERE adam=%s ORDER BY id""",
                (adam,),
            )
            return list(c.fetchall())

    @router.get("/admin/{adam}", response_class=HTMLResponse)
    def tables_admin_panel(request: Request, adam: str):
        """Panel listing every extracted table for an act, with per-table
        publish/delete. Embedded (lazy) in the act edit hub's Πίνακες tab."""
        return templates.TemplateResponse(
            request, "tables/_panel_extracted.html",
            {"adam": adam, "tables": _act_tables(adam),
             "PREVIEW_ROWS": 5, "PREVIEW_COLS": 10},
        )

    @router.post("/admin/{adam}/{tid}/toggle", response_class=HTMLResponse)
    def tables_admin_toggle(request: Request, adam: str, tid: int):
        """Flip one table's published state; return the single re-rendered row."""
        with cursor() as c:
            c.execute(
                """UPDATE proc.extracted_table
                   SET is_published = NOT is_published
                   WHERE id=%s AND adam=%s
                   RETURNING id, source, locator, rows, n_rows, n_cols,
                             is_published""",
                (tid, adam),
            )
            row = c.fetchone()
        if row is None:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request, "tables/_extracted_row.html",
            {"adam": adam, "xt": row, "PREVIEW_ROWS": 5, "PREVIEW_COLS": 10},
        )

    @router.delete("/admin/{adam}/{tid}", response_class=HTMLResponse)
    def tables_admin_delete(adam: str, tid: int):
        """Delete one extracted table. Returns empty body so the row is swapped
        out of the panel."""
        with cursor() as c:
            c.execute(
                "DELETE FROM proc.extracted_table WHERE id=%s AND adam=%s",
                (tid, adam),
            )
        return HTMLResponse("")

    def _one_table(adam: str, tid: int):
        with cursor() as c:
            c.execute(
                """SELECT id, source, locator, rows, n_rows, n_cols, is_published
                   FROM proc.extracted_table WHERE id=%s AND adam=%s""",
                (tid, adam),
            )
            return c.fetchone()

    @router.get("/admin/{adam}/{tid}/row", response_class=HTMLResponse)
    def tables_admin_row(request: Request, adam: str, tid: int):
        """Re-render one stored table's admin row unchanged (used to cancel an edit)."""
        xt = _one_table(adam, tid)
        if xt is None:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request, "tables/_extracted_row.html",
            {"adam": adam, "xt": xt, "PREVIEW_ROWS": 5, "PREVIEW_COLS": 10})

    @router.get("/admin/{adam}/{tid}/edit", response_class=HTMLResponse)
    def tables_admin_edit_form(request: Request, adam: str, tid: int):
        """Editable cell grid for one stored table (swapped in over its row)."""
        xt = _one_table(adam, tid)
        if xt is None:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request, "tables/_extracted_edit.html", {"adam": adam, "xt": xt})

    @router.post("/admin/{adam}/{tid}/edit", response_class=HTMLResponse)
    async def tables_admin_edit_save(request: Request, adam: str, tid: int):
        """Persist edited cells back to proc.extracted_table. content_tsv is a
        generated column over `rows`, so the table search re-indexes itself.
        Returns the re-rendered (read-only) admin row."""
        import json as _json
        form = await request.form()
        try:
            grid = _json.loads(form.get("rows") or "[]")
        except ValueError:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Μη έγκυρα δεδομένα.</div>",
                status_code=400)
        # Normalise to a list-of-lists of strings; drop fully-empty trailing rows.
        norm: list[list[str]] = []
        for r in grid if isinstance(grid, list) else []:
            if isinstance(r, list):
                norm.append(["" if c is None else str(c) for c in r])
        while norm and all(c.strip() == "" for c in norm[-1]):
            norm.pop()
        n_rows = len(norm)
        n_cols = max((len(r) for r in norm), default=0)
        with cursor() as c:
            c.execute(
                """UPDATE proc.extracted_table
                   SET rows=%s, n_rows=%s, n_cols=%s
                   WHERE id=%s AND adam=%s
                   RETURNING id, source, locator, rows, n_rows, n_cols, is_published""",
                (Json(norm), n_rows, n_cols, tid, adam))
            xt = c.fetchone()
        if xt is None:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request, "tables/_extracted_row.html",
            {"adam": adam, "xt": xt, "PREVIEW_ROWS": 5, "PREVIEW_COLS": 10})

    # ------------------------------------------------------------------ #
    # PUBLIC — published tables for an act, rendered as a lazy-loaded tab on
    # the detail page. Empty body when there are none (the tab hides itself).
    # ------------------------------------------------------------------ #
    @router.get("/public/{adam}", response_class=HTMLResponse)
    def tables_public(request: Request, adam: str):
        with cursor() as c:
            c.execute(
                """SELECT id, source, locator, rows, n_rows, n_cols
                   FROM proc.extracted_table
                   WHERE adam=%s AND is_published
                   ORDER BY id""",
                (adam,),
            )
            rows = list(c.fetchall())
        if not rows:
            return HTMLResponse("")
        return templates.TemplateResponse(
            request, "tables/_panel_pub_tables.html",
            {"adam": adam, "tables": rows},
        )

    # ------------------------------------------------------------------ #
    # PER-TABLE EXCEL — re-export one stored table to .xlsx, reusing the
    # standalone tool's export_workbook unchanged. We rebuild the exact table
    # dict shape it consumes (source/locator/rows/n_rows/n_cols) from JSONB.
    # ------------------------------------------------------------------ #
    @router.get("/public/{adam}/{tid}.xlsx")
    def tables_download_one(adam: str, tid: int):
        with cursor() as c:
            c.execute(
                """SELECT source, locator, rows, n_rows, n_cols
                   FROM proc.extracted_table WHERE id=%s AND adam=%s""",
                (tid, adam),
            )
            row = c.fetchone()
        if row is None:
            return Response(status_code=404)
        table = {
            "source": row["source"], "locator": row["locator"],
            "rows": row["rows"], "n_rows": row["n_rows"], "n_cols": row["n_cols"],
        }
        data, _filename, media_type = export_workbook([table])
        return Response(
            content=data, media_type=media_type,
            headers={"Content-Disposition":
                     f'attachment; filename="{adam}-table-{tid}.xlsx"'},
        )

    # ------------------------------------------------------------------ #
    # FULL TEXT — curator extraction of an act's attachment text into the
    # procurement_act.full_text field. Reuses the same fetch/select/thumbnail/
    # page-picker machinery as the table extractor above; the only difference
    # is the action (extract text → preview → save to the field) instead of
    # export-to-Excel. Manual save always overwrites (unlike the auto importer,
    # which is fill-only-if-empty).
    # ------------------------------------------------------------------ #

    def _act_for_fulltext(adam: str):
        with cursor() as c:
            c.execute(
                """SELECT adam, type, title, full_text, full_text_html,
                          full_text_extracted_at, full_text_source
                   FROM proc.procurement_act WHERE adam=%s""",
                (adam,),
            )
            return c.fetchone()

    @router.get("/fulltext/{adam}")
    def fulltext_form(adam: str):
        """Full-text editing now lives in the act-edit hub's "Πλήρες κείμενο"
        tab; redirect this standalone URL there (the POST extract/save endpoints
        below stay — the hub panel uses them)."""
        return RedirectResponse(
            url=f"/admin/act/{adam}/edit?tab=fulltext", status_code=303)

    @router.post("/fulltext/fetch", response_class=HTMLResponse)
    def fulltext_fetch(request: Request, adam: str = Form(...)):
        """Fetch the act's document and show the file/page picker for text."""
        _prune_sessions()
        adam = adam.strip()
        info = _act_info(adam)
        data_source = info["data_source"] if info else None
        try:
            if data_source == "diavgeia":
                data, fname = _fetch_diavgeia_document(adam)
            else:
                act_type = (info["type"] if info else None) or "notice"
                data, fname = _fetch_act_document(act_type, adam)
        except ValueError as exc:
            return HTMLResponse(
                f"<div class='tt-flash tt-error'>{exc}</div>", status_code=422
            )
        entries, errors = collect_files(fname, data)
        session_id, session = _new_session(entries, errors)
        session["fulltext_adam"] = adam
        return templates.TemplateResponse(
            request, "tables/_fulltext_select.html",
            {
                "session_id": session_id,
                "entries": entries,
                "errors": errors,
                "total_size": sum(e.size for e in entries),
                "adam": adam,
            },
        )

    @router.post("/fulltext/upload", response_class=HTMLResponse)
    async def fulltext_upload(request: Request,
                              adam: str = Form(...),
                              files: list[UploadFile] = None):
        """Alternative to fetch: curator uploads the file(s) directly."""
        _prune_sessions()
        adam = adam.strip()
        entries, errors, total = [], [], 0
        for f in (files or []):
            data = await f.read()
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                return HTMLResponse(
                    "<div class='tt-flash tt-error'>Πολύ μεγάλο αρχείο.</div>"
                )
            if not f.filename:
                continue
            ents, errs = collect_files(f.filename, data)
            entries.extend(ents)
            errors.extend(errs)
        session_id, session = _new_session(entries, errors)
        session["fulltext_adam"] = adam
        return templates.TemplateResponse(
            request, "tables/_fulltext_select.html",
            {
                "session_id": session_id,
                "entries": entries,
                "errors": errors,
                "total_size": sum(e.size for e in entries),
                "adam": adam,
            },
        )

    @router.post("/fulltext/extract", response_class=HTMLResponse)
    def fulltext_extract(
        request: Request,
        session_id: str = Form(...),
        file_ids: list[str] = Form(default=[]),
    ):
        """Run text extraction on the chosen files (respecting page selection)
        and return the combined text into an editable textarea for review."""
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — ξεκινήστε ξανά.</div>",
                status_code=410,
            )
        if not file_ids:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Δεν επιλέχθηκε κανένα αρχείο.</div>",
                status_code=400,
            )
        selected = set(file_ids)
        chunks: list[str] = []
        skipped: list[str] = []
        for eid in session["order"]:
            if eid in selected and eid in session["entries"]:
                entry = session["entries"][eid]
                sel = session["page_sel"].get(eid)
                txt = extract_text_from_entry(entry, pages=set(sel) if sel else None)
                if txt:
                    chunks.append(f"=== {entry.source} ===\n{txt}")
                else:
                    skipped.append(entry.source)
        combined = "\n\n".join(chunks).strip()

        # Plain extraction only. If the result is empty (scanned) or garbled
        # (broken fonts), the preview offers the FREE local OCR (Tesseract) button
        # first, then the paid Claude button as a last resort — an explicit,
        # tiered escalation the curator drives. (The headless mass/ingest paths
        # still auto-run Tesseract; only this interactive flow is manual.)
        return templates.TemplateResponse(
            request, "tables/_fulltext_preview.html",
            {
                "session_id": session_id,
                "adam": session.get("fulltext_adam", ""),
                "text": combined,
                "skipped": skipped,
                "char_count": len(combined),
                "file_ids": list(file_ids),
                "ocr_available": api_key_present(),
                "local_ocr_available": _local_ocr_enabled(),
                "via_ocr": False,
                "via_local_ocr": False,
                "garbled": bool(combined) and _looks_garbled(combined),
            },
        )

    @router.post("/fulltext/local-ocr", response_class=HTMLResponse)
    def fulltext_local_ocr(
        request: Request,
        session_id: str = Form(...),
        file_ids: list[str] = Form(default=[]),
    ):
        """Re-read the chosen files with the FREE local OCR tier (Tesseract) — the
        middle step between plain extraction and the paid Claude fallback. Same
        editable preview; the Claude button stays available if it's still not good
        enough. PDFs/images only."""
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — ξεκινήστε ξανά.</div>",
                status_code=410,
            )
        if not file_ids:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Δεν επιλέχθηκε κανένα αρχείο.</div>",
                status_code=400,
            )
        if not _local_ocr_enabled():
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Το τοπικό OCR (Tesseract) δεν είναι "
                "διαθέσιμο σε αυτό το περιβάλλον.</div>",
                status_code=400,
            )
        selected = set(file_ids)
        chunks: list[str] = []
        skipped: list[str] = []
        for eid in session["order"]:
            if eid in selected and eid in session["entries"]:
                entry = session["entries"][eid]
                sel = session["page_sel"].get(eid)
                ot = _local_ocr_entry(entry, set(sel) if sel else None)
                if ot:
                    chunks.append(f"=== {entry.source} (OCR) ===\n{ot}")
                else:
                    skipped.append(entry.source)
        combined = "\n\n".join(chunks).strip()
        return templates.TemplateResponse(
            request, "tables/_fulltext_preview.html",
            {
                "session_id": session_id,
                "adam": session.get("fulltext_adam", ""),
                "text": combined,
                "skipped": skipped,
                "char_count": len(combined),
                "file_ids": list(file_ids),
                "ocr_available": api_key_present(),
                "local_ocr_available": True,
                "via_ocr": False,
                "via_local_ocr": True,
                "garbled": bool(combined) and _looks_garbled(combined),
            },
        )

    @router.post("/fulltext/ocr", response_class=HTMLResponse)
    def fulltext_ocr(
        request: Request,
        session_id: str = Form(...),
        file_ids: list[str] = Form(default=[]),
    ):
        """Re-read the chosen files as PLAIN TEXT via Claude (for when ordinary
        extraction produced garbled Greek). Returns the transcribed text into the
        same editable preview. PDFs/images only — others fall back to skipped."""
        session = SESSIONS.get(session_id)
        if session is None:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Η συνεδρία έληξε — ξεκινήστε ξανά.</div>",
                status_code=410,
            )
        if not file_ids:
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Δεν επιλέχθηκε κανένα αρχείο.</div>",
                status_code=400,
            )
        if not api_key_present():
            return HTMLResponse(
                "<div class='tt-flash tt-error'>Το <code>ANTHROPIC_API_KEY</code> "
                "δεν είναι ορισμένο — η εξαγωγή μέσω Claude είναι ανενεργή.</div>",
                status_code=400,
            )
        selected = set(file_ids)
        chunks: list[str] = []
        skipped: list[str] = []
        for eid in session["order"]:
            if eid in selected and eid in session["entries"]:
                entry = session["entries"][eid]
                # Only rasterizable files can be re-read by Claude.
                if entry.ext not in (".pdf", ".png", ".jpg", ".jpeg",
                                     ".tif", ".tiff", ".webp"):
                    skipped.append(entry.source)
                    continue
                sel = session["page_sel"].get(eid)
                try:
                    txt = safe_ocr_text_from_entry(entry, pages=set(sel) if sel else None)
                except (OcrError, RenderUnavailable) as exc:
                    return HTMLResponse(
                        f"<div class='tt-flash tt-error'>Claude OCR απέτυχε: {exc}</div>",
                        status_code=502,
                    )
                if txt:
                    chunks.append(f"=== {entry.source} (Claude) ===\n{txt}")
                else:
                    skipped.append(entry.source)
        combined = "\n\n".join(chunks).strip()
        return templates.TemplateResponse(
            request, "tables/_fulltext_preview.html",
            {
                "session_id": session_id,
                "adam": session.get("fulltext_adam", ""),
                "text": combined,
                "skipped": skipped,
                "char_count": len(combined),
                "file_ids": list(file_ids),
                "ocr_available": api_key_present(),
                "local_ocr_available": _local_ocr_enabled(),
                "via_ocr": True,
                "via_local_ocr": False,
                "garbled": False,
            },
        )

    @router.post("/fulltext/save", response_class=HTMLResponse)
    def fulltext_save(
        request: Request,
        adam: str = Form(...),
        full_text: str = Form(""),
        full_text_html: str = Form(""),
    ):
        """Persist the (possibly hand-edited) text to procurement_act.
        Manual save always overwrites — curator intent wins.

        Stores TWO things in lock-step: the plain text (full_text, the search
        source) and the sanitised rich HTML (full_text_html, shown on the detail
        page). Emptiness is driven by the plain text: an empty editor clears both
        columns. The HTML is sanitised server-side regardless of what the client
        sent."""
        adam = adam.strip()
        text = (full_text or "").strip()
        html = sanitize_full_text_html(full_text_html) if text else None
        with cursor() as c:
            c.execute("SELECT 1 FROM proc.procurement_act WHERE adam=%s", (adam,))
            if not c.fetchone():
                return HTMLResponse(
                    "<div class='tt-flash tt-error'>Άγνωστη πράξη.</div>",
                    status_code=404,
                )
            c.execute(
                """UPDATE proc.procurement_act
                   SET full_text = %s,
                       full_text_html = %s,
                       full_text_extracted_at = now(),
                       full_text_source = %s
                   WHERE adam = %s""",
                (text or None,
                 html,
                 f"manual:{adam}" if text else "manual:cleared",
                 adam),
            )
        return HTMLResponse(
            "<div class='tt-flash tt-ok'>Αποθηκεύτηκε. "
            f"<a href='/act/{adam}'>προβολή πράξης ›</a></div>"
        )

    return router
