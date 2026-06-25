"""Throwaway worker that runs ONE crash-prone native-PDFium render / OCR op for
the /tables tool, isolated in its own process. A malformed document that
segfaults or aborts PDFium (e.g. a font-substitution heap crash) kills only this
process — the parent (render_safe) then reports it cleanly instead of the whole
uvicorn worker going down.

Protocol: a pickled request dict arrives on stdin; the pickled result is written
to the temp file named in argv[1] (NOT stdout — keeping a dedicated channel means
stray library output can't corrupt it). Only primitives cross the boundary, so
no app dataclass identity travels through pickle. Run as a plain script by
render_safe; it imports ocr/extractors flat the way ocr.py itself does.
"""
import os
import pickle
import sys


def _main() -> None:
    out_path = sys.argv[1]
    req = pickle.loads(sys.stdin.buffer.read())

    here = os.path.dirname(os.path.abspath(__file__))   # the app/ directory
    if here not in sys.path:
        sys.path.insert(0, here)

    try:
        import ocr
        from extractors import FileEntry

        op = req["op"]
        if op == "page_count":
            result = ocr.page_count(req["data"])
        else:
            e = req["entry"]
            entry = FileEntry(id=e["id"], source=e["source"], name=e["name"],
                              ext=e["ext"], size=e["size"], data=e["data"])
            pages = set(req["pages"]) if req.get("pages") is not None else None
            if op == "render_thumb":
                result = ocr.render_thumb(entry, req["page"])
            elif op == "render_full":
                result = ocr.render_full(entry, req["page"])
            elif op == "ocr_text_from_entry":
                result = ocr.ocr_text_from_entry(entry, pages=pages)
            elif op == "ocr_entry":
                rep = ocr.ocr_entry(entry, pages=pages)
                result = {"source": rep.source, "status": rep.status,
                          "detail": rep.detail, "tables": rep.tables,
                          "entry_id": rep.entry_id, "n_pages": rep.n_pages}
            else:
                raise ValueError(f"unknown op {op!r}")
        payload = {"ok": True, "result": result}
    except BaseException as exc:   # noqa: BLE001 — surface as a clean message
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    with open(out_path, "wb") as f:
        pickle.dump(payload, f)


if __name__ == "__main__":
    _main()
