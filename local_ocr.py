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


def ocr_image(data: bytes) -> str | None:
    """OCR a single already-rendered page image (PNG/JPEG bytes). Used by the
    interactive full-text editor, which renders pages itself (page-aware, handles
    PDFs and image files) and hands each one here. None if disabled / empty."""
    if not data or not enabled():
        return None
    txt = _ocr_png(data).strip()  # tesseract sniffs the format from content
    return txt or None


def _tesseract_tsv(png: bytes) -> str:
    """Run Tesseract in TSV mode on one image → its word-box TSV ('' on failure).
    TSV columns include left/top/width/height/conf/text per word — the geometry
    the table reconstruction below clusters into rows and columns."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png)
        path = f.name
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", TESS_LANG, "tsv"],
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


def _parse_tsv_words(tsv: str) -> list[dict]:
    """Parse Tesseract TSV into recognised word boxes (skips layout rows and
    empty/low-confidence text)."""
    lines = tsv.splitlines()
    if len(lines) < 2:
        return []
    header = lines[0].split("\t")
    idx = {k: i for i, k in enumerate(header)}
    if not all(k in idx for k in ("left", "top", "width", "height", "conf", "text")):
        return []
    words: list[dict] = []
    for ln in lines[1:]:
        c = ln.split("\t")
        if len(c) <= idx["text"]:
            continue
        text = c[idx["text"]].strip()
        if not text:
            continue
        try:
            conf = float(c[idx["conf"]])
            left, top = int(c[idx["left"]]), int(c[idx["top"]])
            w, h = int(c[idx["width"]]), int(c[idx["height"]])
        except ValueError:
            continue
        if conf < 0:                      # -1 marks non-text layout rows
            continue
        words.append({"text": text, "left": left, "top": top,
                      "width": w, "height": h,
                      "cx": left + w / 2, "cy": top + h / 2})
    return words


def _reconstruct_table(words: list[dict]) -> list[list[str]]:
    """Rebuild a grid from word boxes: cluster words into rows by vertical
    position, derive columns from the vertical-whitespace gaps in the page's
    x-projection, then drop each word into its row × nearest-column cell. A
    best-effort reconstruction for scanned tables — lower fidelity than the
    Claude vision path, meant to be corrected in the editable table UI."""
    if len(words) < 4:
        return []
    hs = sorted(w["height"] for w in words)
    h = hs[len(hs) // 2] or 10

    # rows — gap-cluster by vertical centre (running mean baseline)
    words.sort(key=lambda w: w["cy"])
    rows: list[list[dict]] = []
    cur: list[dict] = []
    ref = None
    for w in words:
        if ref is None or (w["cy"] - ref) <= 0.6 * h:
            cur.append(w)
            ref = sum(x["cy"] for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur, ref = [w], w["cy"]
    if cur:
        rows.append(cur)

    # columns — occupancy projection over x; gaps wider than ~1.2 char heights split
    minx = min(w["left"] for w in words)
    maxx = max(w["left"] + w["width"] for w in words)
    bin_w = max(1, int(h / 2))
    n_bins = (maxx - minx) // bin_w + 1
    occ = [0] * n_bins
    for w in words:
        a = (w["left"] - minx) // bin_w
        b = (w["left"] + w["width"] - minx) // bin_w
        for i in range(a, b + 1):
            if 0 <= i < n_bins:
                occ[i] += 1
    gap_bins = max(1, int(1.2 * h / bin_w))
    cols: list[tuple[float, float]] = []
    seg_start = None
    run = 0
    for i, v in enumerate(occ):
        if v > 0:
            if seg_start is None:
                seg_start = i
            run = 0
        elif seg_start is not None:
            run += 1
            if run >= gap_bins:
                cols.append((minx + seg_start * bin_w, minx + (i - run + 1) * bin_w))
                seg_start, run = None, 0
    if seg_start is not None:
        cols.append((minx + seg_start * bin_w, maxx))
    if len(cols) < 2:
        return []
    centers = [(a + b) / 2 for a, b in cols]

    grid: list[list[str]] = []
    for row in rows:
        cells: list[list[str]] = [[] for _ in cols]
        for w in sorted(row, key=lambda w: w["left"]):
            ci = min(range(len(cols)), key=lambda j: abs(w["cx"] - centers[j]))
            cells[ci].append(w["text"])
        grid.append([" ".join(c) for c in cells])
    return grid


def ocr_image_table(data: bytes) -> list[list[str]] | None:
    """OCR one rendered page image and reconstruct a table grid (list of rows of
    cell strings), or None if disabled/empty/not tabular. Free local counterpart
    of the Claude table-OCR path; the caller wraps the grid into the standard
    table dict and lets the curator edit it."""
    if not data or not enabled():
        return None
    tsv = _tesseract_tsv(data)
    if not tsv:
        return None
    grid = _reconstruct_table(_parse_tsv_words(tsv))
    return grid or None


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
