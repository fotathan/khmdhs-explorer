"""Regression: the full-text upload endpoint must accept an EMPTY adam. When
creating a new act there is no ΑΔΑΜ yet, so the widget posts adam="" — a
required Form(...) 422s the whole upload before OCR can run."""
import re


def test_upload_adam_is_optional():
    src = open("app/tables.py", encoding="utf-8").read()
    m = re.search(r"async def fulltext_upload\(.*?adam: str = Form\((.*?)\)",
                  src, re.S)
    assert m, "fulltext_upload(adam=...) not found"
    default = m.group(1).strip()
    assert default != "...", "adam must default to '' (create has no ΑΔΑΜ) — a " \
        "required Form(...) 422s uploads on the create-act form"
    assert default in ('""', "''")
