"""The redesigned act page's extra blocks (docs/specs/public-detail-pages.md
slice 5, after Tender Service's tender-detail design).

  related   open notices in the same CPV division (same authority first) —
            other acts' titles, buyers, deadlines and values: their public hero
  buyer     the authority's 12-month profile and sentence (app/authority_profile)
  learn     glossary cards for the terms this act uses
  cpvs      the act's CPV codes with their official names and stored AI
            paragraphs — SUBSCRIBERS ONLY (CPV stays subscriber-only on act
            pages, owner 2026-10-09); never built for a gated reader

What a gated reader may see is unchanged by this module: it never returns a
field of THIS act that the teaser hides. The act's CPV divisions are read to
choose the related tenders, but never rendered for a gated reader.
"""
from __future__ import annotations

try:
    from app import authority_profile as _ap
    from app import field_notes as _fn
    from app import glossary as _gl
    from app.act_visibility import VISIBLE_SQL
except ImportError:  # flat layout
    import authority_profile as _ap
    import field_notes as _fn
    import glossary as _gl
    from act_visibility import VISIBLE_SQL

RELATED_MAX = 4


def divisions(c, adam: str) -> list[str]:
    c.execute("""SELECT DISTINCT substr(oc.cpv_code, 1, 2) AS d
                 FROM proc.act_object_detail od
                 JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                 WHERE od.adam = %s ORDER BY 1""", (adam,))
    return [r["d"] for r in c.fetchall()]


def related(c, notice: dict, divs: list[str], limit: int = RELATED_MAX) -> list[dict]:
    """Open notices sharing a CPV division, the same authority first, then the
    soonest deadline. Starts from the open notices (a few thousand), so the
    cost does not depend on how broad the division is."""
    if not divs:
        return []
    c.execute(f"""
        SELECT a.adam, a.title, a.final_submission_date, a.total_cost_with_vat,
               au.name AS authority_name
        FROM proc.procurement_act a
        LEFT JOIN proc.authority au ON au.org_id = a.authority_id
        WHERE a.type = 'notice' AND a.final_submission_date >= now()
          AND NOT coalesce(a.cancelled, false) AND a.adam <> %s
          AND {VISIBLE_SQL}
          AND EXISTS (SELECT 1 FROM proc.act_object_detail od
                      JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                      WHERE od.adam = a.adam AND substr(oc.cpv_code, 1, 2) = ANY(%s))
        ORDER BY (a.authority_id IS NOT DISTINCT FROM %s) DESC, a.final_submission_date
        LIMIT %s""", (notice["adam"], divs, notice.get("authority_id"), limit))
    return c.fetchall()


def buyer(c, notice: dict, member_ids: list[str], lang: str) -> dict | None:
    if not notice.get("authority_id"):
        return None
    p = _ap.load(c, member_ids, lang)
    return {"profile": p,
            "sentence": _ap.sentence(p, notice.get("authority_name") or "", lang)}


def learn(notice: dict, *, gated: bool, lang: str) -> list[dict]:
    """Glossary cards for the terms this act actually uses: its type, its
    procedure (subscribers — the procedure is a subscriber field), then the
    general ones every bidder meets."""
    slugs = []
    t = _fn.ACT_TYPES.get(notice.get("act_type") or "notice")
    if t and t.get("glossary"):
        slugs.append(t["glossary"])
    if not gated:
        pr = _fn.PROCEDURES.get(notice.get("procedure_family") or "")
        if pr and pr.get("glossary"):
            slugs.append(pr["glossary"])
    slugs += ["cpv", "kritirio-anathesis", "eggyitiki", "eees"]
    L = "en" if lang == "en" else "el"
    out, seen = [], set()
    for s in slugs:
        term = _gl.get(s)
        if term and s not in seen:
            seen.add(s)
            section = next((x for x in _gl.SECTIONS if x["slug"] == term["section"]), None)
            out.append({"slug": s, "term": term[L]["term"], "short": term[L]["short"],
                        "section": section[L] if section else ""})
    return out[:4]


def _has_notes(c) -> bool:
    """proc.cpv_note arrives with slices 3-4 (migration 20261009150000); until
    then the codes are listed with their official names only."""
    c.execute("SELECT to_regclass('proc.cpv_note') IS NOT NULL AS ok")
    return bool(c.fetchone()["ok"])


def cpvs(c, adam: str, lang: str) -> list[dict]:
    """The act's CPV codes (curated act-level first, then line items), each
    with its official name and, once the notes exist, its visible AI
    paragraph. Subscribers only."""
    desc = "coalesce(cc.description_en, cc.description)" if lang == "en" else "cc.description"
    if _has_notes(c):
        note = "CASE WHEN n.hidden_at IS NULL THEN " + ("n.text_en" if lang == "en" else "n.text_el") + " END"
        join = "LEFT JOIN proc.cpv_note n ON n.cpv_code = f.cpv_code"
    else:
        note, join = "NULL::text", ""
    c.execute(f"""
        WITH codes AS (
            SELECT ac.cpv_code, 0 AS src, ac.ord AS ord FROM proc.act_cpv ac WHERE ac.adam = %s
            UNION ALL
            SELECT oc.cpv_code, 1, od.line_no
            FROM proc.act_object_detail od
            JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
            WHERE od.adam = %s
        ), firsts AS (
            SELECT cpv_code, min(src * 100000 + coalesce(ord, 0)) AS k FROM codes GROUP BY 1
        )
        SELECT f.cpv_code, {desc} AS name, {note} AS note
        FROM firsts f
        LEFT JOIN proc.cpv_code cc ON cc.cpv_code = f.cpv_code
        {join}
        ORDER BY f.k, f.cpv_code
        LIMIT 12""", (adam, adam))
    return c.fetchall()


def build(c, notice: dict, *, member_ids: list[str], gated: bool, lang: str) -> dict:
    """Everything the redesigned page adds, in one call. A block that fails is
    simply absent — the page never 500s for a nicety."""
    out = {"related": [], "buyer": None, "learn": [], "cpvs": [], "divisions": []}
    try:
        out["divisions"] = divisions(c, notice["adam"])
        out["related"] = related(c, notice, out["divisions"])
    except Exception:        # noqa: BLE001
        pass
    try:
        out["buyer"] = buyer(c, notice, member_ids, lang)
    except Exception:        # noqa: BLE001
        out["buyer"] = None
    out["learn"] = learn(notice, gated=gated, lang=lang)
    if not gated:
        try:
            out["cpvs"] = cpvs(c, notice["adam"], lang)
        except Exception:    # noqa: BLE001
            out["cpvs"] = []
    return out
