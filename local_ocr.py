"""
local_ocr.py — local OCR tier (Tesseract) between pdfplumber text extraction and
the Anthropic API OCR.

The full-text pipeline is a cascade:
  1. pdfplumber  — text layer of digital PDFs (free, instant)
  2. THIS module — render pages (pdfium) + Tesseract ell+eng, for documents whose
     text layer is missing (scanned) or garbled (broken cid fonts). Free, ~1-3s
     per doc, handles the large majority of garbled KHMDHS PDFs.
  3. Anthropic API OCR (app/ocr.py) — only the hard cases the local tier can't
     read, opt-in and gated on ANTHROPIC_API_KEY.

KHMDHS-specific glue — deliberately NOT one of the byte-identical sibling modules
(app/extractors.py, app/exporter.py, app/ocr.py). Uses pypdfium2 (already a dep)
for rendering and the `tesseract` binary via subprocess (no new Python dep). The
binary + Greek data are a deployment dependency (see Dockerfile:
tesseract-ocr, tesseract-ocr-ell). Everything is fail-soft: any problem returns
None so the caller falls through to the existing behaviour.

Env knobs:
  LOCAL_OCR=0                 disable the tier (default on when tesseract present)
  TESSERACT_LANG=ell+eng      OCR languages (eng included so Latin ΑΔΑΜ codes read)
  LOCAL_OCR_MAX_PAGES=20      cap pages OCR'd per document (CPU guard for batches)
  LOCAL_OCR_RENDER_SCALE=2.2  pdfium render scale (~158 dpi)
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile

TESS_LANG = os.environ.get("TESSERACT_LANG", "ell+eng")
MAX_PAGES = int(os.environ.get("LOCAL_OCR_MAX_PAGES", "20"))
RENDER_SCALE = float(os.environ.get("LOCAL_OCR_RENDER_SCALE", "2.2"))
_PER_PAGE_TIMEOUT = int(os.environ.get("LOCAL_OCR_PAGE_TIMEOUT", "120"))

_available_cache: bool | None = None


def enabled() -> bool:
    """The tier is on unless LOCAL_OCR=0 and Tesseract (with Greek) is present."""
    if os.environ.get("LOCAL_OCR", "1") != "1":
        return False
    return available()


def available() -> bool:
    """True if the `tesseract` binary and the Greek (`ell`) language are present.
    Cached — the answer doesn't change within a process."""
    global _available_cache
    if _available_cache is not None:
        return _available_cache
    ok = False
    if shutil.which("tesseract"):
        try:
            langs = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True, timeout=15,
            ).stdout.decode("utf-8", "replace")
            ok = "ell" in langs.split()
        except Exception:
            ok = False
    _available_cache = ok
    return ok


def _ocr_png(png: bytes) -> str:
    """Run Tesseract on one PNG image (via a temp file — the most portable path
    across tesseract builds). Returns the recognised text ('' on failure)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        path = f.name
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", TESS_LANG],
            capture_output=True, timeout=_PER_PAGE_TIMEOUT,
        )
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def ocr_pdf(data: bytes, max_pages: int | None = None) -> str | None:
    """Render a PDF's pages and OCR them with Tesseract. Returns the concatenated
    text, or None if the tier is disabled/unavailable, the bytes aren't a PDF, or
    nothing was recognised. Never raises."""
    if not data or data[:4] != b"%PDF" or not enabled():
        return None
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    cap = MAX_PAGES if max_pages is None else max_pages
    parts: list[str] = []
    pdf = None
    try:
        pdf = pdfium.PdfDocument(data)
        n = min(len(pdf), cap)
        for i in range(n):
            try:
                pil = pdf[i].render(scale=RENDER_SCALE).to_pil()
                buf = io.BytesIO()
                pil.save(buf, "PNG")
                txt = _ocr_png(buf.getvalue())
                if txt:
                    parts.append(txt)
            except Exception:
                continue
    except Exception:
        return None
    finally:
        if pdf is not None:
            try:
                pdf.close()
            except Exception:
                pass
    text = "\n".join(parts).strip()
    return text or None
