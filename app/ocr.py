"""
ocr.py — table extraction from scanned PDFs and images via the Claude API.

Opt-in only: nothing here runs unless the user clicks "Run OCR" on a file.
Requires ANTHROPIC_API_KEY in the environment. Configuration (env vars):

    ANTHROPIC_API_KEY   required
    OCR_MODEL           default "claude-sonnet-4-6"
    MAX_OCR_PAGES       default 20   (hard cap per document, cost control)
    OCR_DPI             default 150  (render resolution for PDF pages)
"""

from __future__ import annotations

import base64
import io
import json
import os
import ssl
import urllib.error
import urllib.request

import pypdfium2 as pdfium
from PIL import Image

from extractors import FileEntry, FileReport, _make_table, _norm_row, compress_pages

# macOS Pythons often have no CA bundle wired into the default SSL context,
# which makes every urllib HTTPS call fail with CERTIFICATE_VERIFY_FAILED.
# Start from the system defaults and ADD certifi's bundle on top, so both
# bare macOS Pythons and corporate-proxy environments verify correctly.
_SSL_CTX = ssl.create_default_context()
try:
    import certifi
    _SSL_CTX.load_verify_locations(cafile=certifi.where())
except Exception:  # pragma: no cover — certifi missing or unreadable
    pass

OCR_MODEL = os.environ.get("OCR_MODEL", "claude-sonnet-4-6")
MAX_OCR_PAGES = int(os.environ.get("MAX_OCR_PAGES", "20"))
OCR_DPI = int(os.environ.get("OCR_DPI", "150"))
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """\
This is a scanned page from a Greek public procurement document \
(διακήρυξη, οικονομική προσφορά, απόφαση, etc.).

Extract EVERY table visible on the page.

Return ONLY a JSON array — no other text, no markdown fences. One element per table:
{
  "rows": [["cell", "cell", ...], ...],
  "continuation": true|false
}

Rules:
- "rows" contains all rows top to bottom, including the header row if present; \
use "" for empty cells; every row must have the same number of cells.
- Preserve Greek text exactly as printed; transcribe numbers exactly \
(keep Greek decimal commas, e.g. "1.234,56").
- "continuation" is true only if the table clearly continues from a previous \
page (starts mid-data without a header row, or begins with a row that is \
obviously a continuation of a numbered sequence).
- If the page contains no tables, return []."""


class OcrError(Exception):
    """User-facing OCR failure."""


# ---------------------------------------------------------------- previews

THUMB_SCALE = 0.35     # ~210 px wide for A4 — enough to spot a table
THUMB_MAX_PX = 260


def page_count(data: bytes) -> int:
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_full(entry: FileEntry, page: int) -> bytes:
    """Screen-readable JPEG of one PDF page (≈144 dpi) or full image."""
    if entry.ext == ".pdf":
        pdf = pdfium.PdfDocument(entry.data)
        try:
            if not 1 <= page <= len(pdf):
                raise ValueError("page out of range")
            pil = pdf[page - 1].render(scale=2.0).to_pil().convert("RGB")
        finally:
            pdf.close()
    else:
        pil = Image.open(io.BytesIO(entry.data)).convert("RGB")
    pil.thumbnail((1800, 2600))
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def render_thumb(entry: FileEntry, page: int) -> bytes:
    """Small JPEG preview of one PDF page or of an image file."""
    if entry.ext == ".pdf":
        pdf = pdfium.PdfDocument(entry.data)
        try:
            if not 1 <= page <= len(pdf):
                raise ValueError("page out of range")
            pil = pdf[page - 1].render(scale=THUMB_SCALE * 150 / 72).to_pil().convert("RGB")
        finally:
            pdf.close()
    else:
        pil = Image.open(io.BytesIO(entry.data)).convert("RGB")
    pil.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX * 2))
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------- rendering

def render_pdf_pages(data: bytes, pages: set[int] | None = None,
                     max_pages: int = MAX_OCR_PAGES) -> tuple[list[tuple[int, bytes]], int]:
    """Render (selected) PDF pages to JPEG. Returns ([(page_no, jpeg), ...], n_wanted).

    Page numbers are 1-based document page numbers, preserved through the
    pipeline so locators always reference the real page in the source PDF.
    """
    pdf = pdfium.PdfDocument(data)
    try:
        total = len(pdf)
        wanted = sorted(p for p in (pages or range(1, total + 1)) if 1 <= p <= total)
        out = []
        for p in wanted[:max_pages]:
            bitmap = pdf[p - 1].render(scale=OCR_DPI / 72)
            pil = bitmap.to_pil().convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=85)
            out.append((p, buf.getvalue()))
        return out, len(wanted)
    finally:
        pdf.close()


def image_to_jpeg(data: bytes) -> bytes:
    """Normalize any uploaded image to JPEG for the API."""
    pil = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------- API call

def _call_claude(image_jpeg: bytes) -> list[dict]:
    """Send one page image, get back a list of {rows, continuation} dicts."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise OcrError(
            "ANTHROPIC_API_KEY is not set. Get a key at console.anthropic.com, then "
            "run:  export ANTHROPIC_API_KEY=sk-ant-...  before starting uvicorn."
        )
    body = json.dumps({
        "model": OCR_MODEL,
        "max_tokens": 8192,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.b64encode(image_jpeg).decode(),
                }},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=240, context=_SSL_CTX).read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise OcrError("The Anthropic API rejected the key (401). "
                           "Check ANTHROPIC_API_KEY and restart uvicorn.") from e
        raise OcrError(f"Anthropic API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OcrError(f"Could not reach the Anthropic API: {e.reason}") from e

    text = "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.startswith("json") else text
        text = text.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise OcrError(f"Model returned non-JSON output: {text[:200]}") from e
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        rows = item.get("rows") if isinstance(item, dict) else None
        if rows and isinstance(rows, list):
            out.append({
                "rows": [[str(c) if c is not None else "" for c in row] for row in rows],
                "continuation": bool(item.get("continuation")),
            })
    return out


# ---------------------------------------------------------------- pipeline

def _width(rows: list[list[str]]) -> int:
    return max((len(r) for r in rows), default=0)


def ocr_entry(entry: FileEntry, pages: set[int] | None = None) -> FileReport:
    """Run OCR on one scanned PDF or image and return a FileReport with tables.

    `pages` (PDF only): restrict OCR to these 1-based page numbers."""
    if entry.ext == ".pdf":
        images, n_wanted = render_pdf_pages(entry.data, pages)
        truncated = n_wanted > len(images)
    else:
        images, n_wanted, truncated = [(1, image_to_jpeg(entry.data))], 1, False

    # per page: list of raw table dicts (real document page numbers)
    page_tables: list[tuple[int, list[dict]]] = []
    for page_no, img in images:
        page_tables.append((page_no, _call_claude(img)))

    # group continuations across pages (model-judged + width check)
    groups: list[list[dict]] = []
    for page_no, tbls in page_tables:
        for idx, t in enumerate(tbls):
            frag = {"page": page_no, "rows": t["rows"]}
            link = (
                idx == 0 and t["continuation"] and groups
                and groups[-1][-1]["page"] == page_no - 1
                and _width(groups[-1][-1]["rows"]) == _width(t["rows"])
            )
            if link:
                groups[-1].append(frag)
            else:
                groups.append([frag])

    tables: list[dict] = []
    for group in groups:
        if len(group) == 1:
            f = group[0]
            t = _make_table(entry.source, f"Page {f['page']} (OCR)", f["rows"])
            if t:
                tables.append(t)
            continue
        header = _norm_row(group[0]["rows"][0]) if group[0]["rows"] else ()
        stitched_rows = list(group[0]["rows"])
        for f in group[1:]:
            rows = f["rows"]
            if rows and header and _norm_row(rows[0]) == header:
                rows = rows[1:]
            stitched_rows.extend(rows)
        p1, p2 = group[0]["page"], group[-1]["page"]
        stitched = _make_table(
            entry.source, f"Pages {p1}–{p2} (OCR, stitched from {len(group)} pages)",
            stitched_rows,
        )
        if stitched:
            stitched["role"] = "stitched"
            tables.append(stitched)
        for k, f in enumerate(group, start=1):
            t = _make_table(entry.source, f"Page {f['page']} (OCR, part {k}/{len(group)})",
                            f["rows"])
            if t:
                if stitched:
                    t["role"] = "fragment"
                    t["group"] = stitched["id"]
                tables.append(t)

    done = [p for p, _ in images]
    detail = (f"OCR via {OCR_MODEL}, {len(done)} page{'' if len(done) == 1 else 's'} processed"
              + (f" (pages {compress_pages(done)})" if entry.ext == ".pdf" and pages else "")
              + ".")
    if truncated:
        detail += (f" {n_wanted} pages were requested; only the first {len(done)} were "
                   f"processed (MAX_OCR_PAGES={MAX_OCR_PAGES} — raise it via env var if needed).")
    if not tables:
        return FileReport(entry.source, "no_tables",
                          detail + " No tables were found on the processed pages.",
                          )
    report = FileReport(entry.source, "ok", detail, tables=tables)
    return report
