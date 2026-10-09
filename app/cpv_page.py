"""Public CPV pages: /cpv (the 45 divisions) and /cpv/<code>.

Slice 4 of docs/specs/public-detail-pages.md (owner, 2026-10-09: "new public
CPV pages"). A CPV code is public EU reference data, so the whole page is
public and indexable — the crawlable counterpart of the `?cpv=` search filter,
which robots.txt blocks.

Each page shows:
  - the exact official name (the reader's language, the other one beneath);
  - the stored AI paragraph (app/cpv_notes.py), labelled as AI-written, never
    when hidden;
  - where the code sits (ancestors) and what is under it (direct sub-codes);
  - our own 12-month figures for the code and everything below it
    (proc.mv_cpv_activity) and up to 5 open tenders.

The hierarchy comes from proc.cpv_code, which never changes between deploys;
it is built once per process and rebuilt hourly. Nothing here reads customer
data or act_ai_summary.
"""
from __future__ import annotations

import time

try:
    from app import cpv_notes as _cn
    from app.act_visibility import VISIBLE_SQL
except ImportError:  # flat layout
    import cpv_notes as _cn
    from act_visibility import VISIBLE_SQL

_TREE_TTL = 3600
_tree_cache: dict = {"at": 0.0, "codes": None, "parent": None, "children": None}


def tree(c) -> tuple[dict, dict, dict]:
    """(codes, parent_of, children_of) — cached for an hour."""
    now = time.monotonic()
    if _tree_cache["codes"] is None or now - _tree_cache["at"] > _TREE_TTL:
        codes = _cn.load_codes(c)
        parent, children = _cn.build_tree(codes)
        _tree_cache.update(at=now, codes=codes, parent=parent, children=children)
    return _tree_cache["codes"], _tree_cache["parent"], _tree_cache["children"]


def canonical(c, raw: str) -> str | None:
    """The official code for a URL segment: '50220000-3' as is, '50220000'
    (no check digit) resolved. None for anything else, supplementary codes
    included."""
    codes, _p, _ch = tree(c)
    raw = (raw or "").strip()
    if raw in codes:
        return raw
    if len(raw) == 8 and raw.isdigit():
        return next((k for k in codes if k[:8] == raw), None)
    return None


def stem(code: str) -> str:
    return _cn._stem(code)


def name(codes: dict, code: str, lang: str) -> str:
    n = codes[code]
    return (n["en"] or n["el"]) if lang == "en" else n["el"]


def _view_ready(c) -> bool:
    c.execute("""SELECT relispopulated FROM pg_class
                 WHERE oid = to_regclass('proc.mv_cpv_activity')""")
    r = c.fetchone()
    return bool(r and r["relispopulated"])


def activity(c, prefixes: list[str]) -> dict[str, dict]:
    """{prefix: figures} from the 12-month view; {} until it exists."""
    if not prefixes or not _view_ready(c):
        return {}
    c.execute("""SELECT prefix, n_notices, n_contracts, n_valued, value,
                        n_authorities, period_start, period_end
                 FROM proc.mv_cpv_activity WHERE prefix = ANY(%s)""", (prefixes,))
    return {r["prefix"]: r for r in c.fetchall()}


def open_tenders(c, prefix: str, limit: int = 5) -> list[dict]:
    """Visible, uncancelled notices still open, with a line item under the
    prefix, closing soonest first. Starts from the open notices (a few
    thousand at most), so a broad division and a leaf cost the same."""
    c.execute(f"""
        SELECT a.adam, a.title, a.final_submission_date, a.total_cost_with_vat,
               au.name AS authority_name
        FROM proc.procurement_act a
        LEFT JOIN proc.authority au ON au.org_id = a.authority_id
        WHERE a.type = 'notice' AND a.final_submission_date >= now()
          AND NOT coalesce(a.cancelled, false)
          AND {VISIBLE_SQL}
          AND EXISTS (SELECT 1 FROM proc.act_object_detail od
                      JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                      WHERE od.adam = a.adam AND oc.cpv_code LIKE %s)
        ORDER BY a.final_submission_date
        LIMIT %s""", (prefix + "%", limit))
    return c.fetchall()


def page(c, code: str, lang: str) -> dict:
    codes, parent, children = tree(c)
    other = "el" if lang == "en" else "en"
    kids = children.get(code) or []
    figures = activity(c, [stem(code)] + [stem(k) for k in kids])
    return {
        "code": code,
        "stem": stem(code),
        "level": _cn.level(code),
        "name": name(codes, code, lang),
        "other_name": codes[code][other],
        "note": _cn.note_for(c, code, lang),
        "ancestors": [{"code": a, "name": name(codes, a, lang)}
                      for a in _cn.ancestors(code, parent)],
        "children": [{"code": k, "name": name(codes, k, lang),
                      "figures": figures.get(stem(k))} for k in kids],
        "figures": figures.get(stem(code)),
        "tenders": open_tenders(c, stem(code)),
    }


def divisions(c, lang: str) -> list[dict]:
    codes, parent, _children = tree(c)
    roots = sorted(k for k, p in parent.items() if p is None)
    figures = activity(c, [stem(k) for k in roots])
    return [{"code": k, "name": name(codes, k, lang),
             "figures": figures.get(stem(k))} for k in roots]


def sitemap_codes(c) -> list[str]:
    """Codes worth offering to a crawler: those with a visible note. A page
    with only a name and no paragraph is the thin content this slice exists
    to avoid."""
    c.execute("SELECT cpv_code FROM proc.cpv_note WHERE hidden_at IS NULL "
              "ORDER BY cpv_code")
    return [r["cpv_code"] for r in c.fetchall()]
