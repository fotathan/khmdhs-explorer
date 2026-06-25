"""Crash/hang isolation for native-PDFium rendering and OCR (app/ocr.py).

ocr.py is kept byte-identical with the standalone Tender Tables tool, so it can't
carry this; the isolation lives here in the KHMDHS glue and the /tables endpoints
call these `safe_*` wrappers instead of ocr.* directly.

Each call runs ocr's render/OCR in a throwaway subprocess (render_worker.py). A
native abort/segfault (e.g. PDFium's font-substitution heap crash) or a hang
takes down only that subprocess; here it becomes a RenderUnavailable exception
the request handlers already catch — the web worker survives. Only primitives
cross the process boundary.
"""
from __future__ import annotations

import os
import pickle
import subprocess
import sys
import tempfile

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_worker.py")

# Renders should be quick; OCR also calls the Claude API over the network, so it
# gets a much longer leash. Both env-tunable.
RENDER_TIMEOUT = float(os.environ.get("RENDER_TIMEOUT_SECONDS", "25"))
OCR_TIMEOUT = float(os.environ.get("OCR_RENDER_TIMEOUT_SECONDS", "240"))


class RenderUnavailable(Exception):
    """A page/document could not be rendered or OCR'd — native crash, timeout,
    or a parse error inside the isolated worker."""


def _run(req: dict, timeout: float):
    fd, out_path = tempfile.mkstemp(prefix="khmdhs-render-", suffix=".pkl")
    os.close(fd)
    try:
        try:
            proc = subprocess.run(
                [sys.executable, _WORKER, out_path],
                input=pickle.dumps(req),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RenderUnavailable(f"{req['op']}: timed out after {int(timeout)}s")

        data = b""
        try:
            with open(out_path, "rb") as f:
                data = f.read()
        except OSError:
            pass
        if not data:
            # No result written → the worker died before finishing. A negative
            # return code is a signal (e.g. -6 == SIGABRT from a PDFium abort).
            raise RenderUnavailable(
                f"{req['op']}: renderer crashed (exit {proc.returncode}) — unrenderable file")
        out = pickle.loads(data)
        if not out.get("ok"):
            raise RenderUnavailable(f"{req['op']}: {out.get('error', 'failed')}")
        return out["result"]
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _entry_dict(entry) -> dict:
    return {"id": entry.id, "source": entry.source, "name": entry.name,
            "ext": entry.ext, "size": entry.size, "data": entry.data}


def safe_render_thumb(entry, page: int) -> bytes:
    return _run({"op": "render_thumb", "entry": _entry_dict(entry), "page": page},
                RENDER_TIMEOUT)


def safe_render_full(entry, page: int) -> bytes:
    return _run({"op": "render_full", "entry": _entry_dict(entry), "page": page},
                RENDER_TIMEOUT)


def safe_page_count(data: bytes) -> int:
    return _run({"op": "page_count", "data": data}, RENDER_TIMEOUT)


def safe_ocr_text_from_entry(entry, pages=None) -> str:
    return _run({"op": "ocr_text_from_entry", "entry": _entry_dict(entry),
                 "pages": list(pages) if pages else None}, OCR_TIMEOUT)


def safe_ocr_entry(entry, pages=None):
    """Returns a FileReport rebuilt with the caller's own class (only primitives
    cross the process boundary, so there is no pickle class-identity issue)."""
    res = _run({"op": "ocr_entry", "entry": _entry_dict(entry),
                "pages": list(pages) if pages else None}, OCR_TIMEOUT)
    try:
        from app.extractors import FileReport
    except ImportError:
        from extractors import FileReport
    return FileReport(source=res["source"], status=res["status"],
                      detail=res["detail"], tables=res["tables"],
                      entry_id=res["entry_id"], n_pages=res["n_pages"])
