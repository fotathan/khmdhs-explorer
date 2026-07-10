"""
act_export.py — serialize act rows to CSV / XLSX bytes.

Deliberately dumb and pure: the caller passes a list of header strings and a list
of already-formatted rows (native Python values — str / int / float / date /
bool / None). No query, label, or KHMDHS logic lives here, which keeps it
trivially unit-testable and reusable. openpyxl (already a dependency) is imported
lazily so importing this module stays cheap.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io


def _csv_cell(v):
    if v is None:
        return ""
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def rows_to_csv(headers, rows) -> bytes:
    """CSV with a UTF-8 BOM so Excel opens Greek text with the right encoding."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(headers))
    for r in rows:
        w.writerow([_csv_cell(c) for c in r])
    return buf.getvalue().encode("utf-8-sig")


def _xlsx_cell(v):
    # openpyxl refuses tz-aware datetimes/times ("Excel does not support
    # timezones"). Postgres timestamptz columns come back tz-aware, so drop the
    # tzinfo (keeping the same wall-clock value) before writing.
    if isinstance(v, (_dt.datetime, _dt.time)) and v.tzinfo is not None:
        return v.replace(tzinfo=None)
    return v


def rows_to_xlsx(headers, rows, sheet_title="Acts") -> bytes:
    """XLSX via openpyxl's write-only (streaming) workbook, so memory stays flat
    regardless of row count. Bold header row; native cell types preserved."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=(sheet_title or "Acts")[:31])
    bold = Font(bold=True)
    header_cells = []
    for h in headers:
        c = WriteOnlyCell(ws, value=h)
        c.font = bold
        header_cells.append(c)
    ws.append(header_cells)
    for r in rows:
        ws.append([_xlsx_cell(c) for c in r])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
