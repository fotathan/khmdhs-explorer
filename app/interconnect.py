"""
interconnect.py — Act Interconnection (admin overlay, mounted at /admin/interconnect).

Relates acts that belong to the same tender lifecycle (and flags duplicates),
guided by weighted match conditions (proc.match_rule) that produce a 0–100
confidence score. Separate from proc.act_link (the official source graph).

Scoring is driven by INDEXED identifier equality (contract/protocol/commitment
number); "same authority" only *boosts* an already-found candidate — it never
generates candidates on its own (thousands of acts share an authority). Two
guards keep matches meaningful and candidate sets bounded:
  * FORMAT  — an identifier must be ≥3 chars and contain an alphanumeric
              (drops '-------', '....', 'ΔΥ', punctuation-only placeholders).
  * FREQUENCY — a value shared by more than `max_shared` acts is treated as
              junk ('1', '-', '0', 'Δ.Υ.' …) and generates no candidates.

Data helpers take an open dict-row cursor `c`, mirroring app/auth.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Identifier columns a rule may reference — an allowlist, since the column name
# is interpolated into SQL (the values come from the seeded match_rule rows,
# never from the request, but we validate anyway).
_ID_FIELDS = ("contract_number", "protocol_number", "commitment_no")


# --------------------------------------------------------------------------- #
# rules & settings
# --------------------------------------------------------------------------- #
def load_rules(c):
    c.execute("SELECT code, label, kind, field, weight, is_active "
              "FROM proc.match_rule ORDER BY weight DESC, code")
    return c.fetchall()


def rules_map(c):
    return {r["code"]: dict(r) for r in load_rules(c)}


def set_rule(c, code, weight, is_active):
    c.execute("UPDATE proc.match_rule SET weight = %s, is_active = %s WHERE code = %s",
              (max(0, int(weight)), bool(is_active), code))


def load_settings(c):
    c.execute("SELECT key, value FROM proc.match_setting")
    return {r["key"]: r["value"] for r in c.fetchall()}


def set_setting(c, key, value):
    c.execute("UPDATE proc.match_setting SET value = %s WHERE key = %s",
              (int(value), key))


def _id_ok(v) -> bool:
    """Format guard: a usable identifier is ≥3 chars and has an alphanumeric."""
    v = (v or "").strip()
    return len(v) >= 3 and any(ch.isalnum() for ch in v)


# --------------------------------------------------------------------------- #
# scoring / candidates
# --------------------------------------------------------------------------- #
_ACT_COLS = """a.adam, a.type, a.title, a.authority_id, a.submission_date,
               a.signed_date, a.total_cost_with_vat,
               a.contract_number, a.protocol_number, a.commitment_no"""


def _get_act(c, adam):
    c.execute(f"""SELECT {_ACT_COLS},
                    (SELECT name FROM proc.authority WHERE org_id = a.authority_id) AS authority_name
                  FROM proc.procurement_act a WHERE a.adam = %s""", (adam,))
    return c.fetchone()


def candidates_for(c, adam, limit=50):
    """Ranked candidate related acts for `adam` (≥ review_min), each with its
    matched-signal codes, score, and flags (grouped / already_linked)."""
    act = _get_act(c, adam)
    if not act:
        return []
    rules = rules_map(c)
    st = load_settings(c)
    review_min = st.get("review_min", 40)
    max_shared = st.get("max_shared", 30)

    id_rules = [r for r in rules.values()
                if r["kind"] == "identifier" and r["is_active"]
                and r["field"] in _ID_FIELDS and _id_ok(act.get(r["field"]))]

    cand: dict[str, dict] = {}
    for r in id_rules:
        field, val = r["field"], (act[r["field"]] or "").strip()
        c.execute(f"""
            SELECT {_ACT_COLS},
                   (SELECT name FROM proc.authority WHERE org_id = a.authority_id) AS authority_name
            FROM proc.procurement_act a
            WHERE a.adam <> %s AND btrim(a.{field}) = %s
              AND (SELECT count(*) FROM proc.procurement_act x
                   WHERE btrim(x.{field}) = %s) <= %s
        """, (adam, val, val, max_shared))
        for b in c.fetchall():
            e = cand.setdefault(b["adam"], {"row": dict(b), "signals": set()})
            e["signals"].add(r["code"])

    if not cand:
        return []

    auth_rule = rules.get("authority")
    auth_on = bool(auth_rule and auth_rule["is_active"] and act.get("authority_id"))
    for e in cand.values():
        if auth_on and e["row"].get("authority_id") == act["authority_id"]:
            e["signals"].add("authority")
        e["score"] = min(100, sum(rules[s]["weight"] for s in e["signals"] if s in rules))

    # group + official-link context
    my_group = group_of(c, adam)
    for b_adam, e in cand.items():
        e["group_id"] = group_of(c, b_adam)
        c.execute("""SELECT 1 FROM proc.act_link
                     WHERE (source_adam=%s AND target_adam=%s)
                        OR (source_adam=%s AND target_adam=%s) LIMIT 1""",
                  (adam, b_adam, b_adam, adam))
        e["already_linked"] = c.fetchone() is not None

    out = [dict(e["row"], score=e["score"], signals=sorted(e["signals"]),
               grouped=e["group_id"], already_linked=e["already_linked"],
               same_group=(my_group is not None and e["group_id"] == my_group))
           for e in cand.values()
           if e["score"] >= review_min and not (my_group and e["group_id"] == my_group)]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def score_pair(c, adam_a, adam_b):
    """Score + matched signals for an explicit pair (manual compare)."""
    a, b = _get_act(c, adam_a), _get_act(c, adam_b)
    if not a or not b:
        return {"score": 0, "signals": []}
    rules = rules_map(c)
    signals = set()
    for r in rules.values():
        if not r["is_active"]:
            continue
        if r["kind"] == "identifier" and r["field"] in _ID_FIELDS:
            va, vb = (a.get(r["field"]) or "").strip(), (b.get(r["field"]) or "").strip()
            if _id_ok(va) and va == vb:
                signals.add(r["code"])
        elif r["kind"] == "authority" and a.get("authority_id") \
                and a["authority_id"] == b.get("authority_id"):
            signals.add(r["code"])
    score = min(100, sum(rules[s]["weight"] for s in signals))
    return {"score": score, "signals": sorted(signals)}


_CMP_FIELDS = [
    ("type", "Τύπος"), ("authority_name", "Αναθέτουσα"),
    ("submission_date", "Δημοσίευση"), ("signed_date", "Υπογραφή"),
    ("total_cost_with_vat", "Αξία (με ΦΠΑ)"),
    ("contract_number", "Αριθμός σύμβασης"),
    ("protocol_number", "Αριθμός πρωτοκόλλου"),
    ("commitment_no", "Αριθμός δέσμευσης"),
    ("title", "Τίτλος"),
]


def compare_pair(c, adam_a, adam_b):
    """Field-by-field comparison of two acts, with a per-field 'differs' flag."""
    a, b = _get_act(c, adam_a), _get_act(c, adam_b)
    if not a or not b:
        raise HTTPException(404, "act not found")
    rows = []
    for key, label in _CMP_FIELDS:
        va, vb = a.get(key), b.get(key)
        rows.append({"label": label, "a": va, "b": vb,
                     "differs": (va or None) != (vb or None)})
    return {"a": a, "b": b, "rows": rows, **score_pair(c, adam_a, adam_b)}


# --------------------------------------------------------------------------- #
# group operations
# --------------------------------------------------------------------------- #
def group_of(c, adam):
    c.execute("SELECT group_id FROM proc.act_group_member WHERE adam = %s", (adam,))
    row = c.fetchone()
    return row["group_id"] if row else None


def group_members(c, gid):
    c.execute("""
        SELECT m.adam, m.is_duplicate, m.duplicate_of, m.added_at,
               a.type, a.title, a.submission_date, a.signed_date, a.total_cost_with_vat,
               (SELECT name FROM proc.authority WHERE org_id = a.authority_id) AS authority_name
        FROM proc.act_group_member m
        JOIN proc.procurement_act a ON a.adam = m.adam
        WHERE m.group_id = %s
        ORDER BY a.submission_date NULLS LAST, a.signed_date NULLS LAST, m.adam
    """, (gid,))
    return c.fetchall()


def _add_member(c, gid, adam, by):
    c.execute("""INSERT INTO proc.act_group_member (adam, group_id, added_by)
                 VALUES (%s, %s, %s)
                 ON CONFLICT (adam) DO UPDATE SET group_id = EXCLUDED.group_id""",
              (adam, gid, by))


def merge_groups(c, keep, drop):
    if keep == drop:
        return
    c.execute("UPDATE proc.act_group_member SET group_id = %s WHERE group_id = %s",
              (keep, drop))
    c.execute("DELETE FROM proc.act_group WHERE id = %s", (drop,))


def relate(c, adam_a, adam_b, by=None):
    """Relate two acts: create a group, extend one, or merge two groups."""
    if adam_a == adam_b:
        return
    if not _get_act(c, adam_a) or not _get_act(c, adam_b):
        raise HTTPException(404, "act not found")
    ga, gb = group_of(c, adam_a), group_of(c, adam_b)
    if ga and gb:
        merge_groups(c, ga, gb)
    elif ga:
        _add_member(c, ga, adam_b, by)
    elif gb:
        _add_member(c, gb, adam_a, by)
    else:
        c.execute("INSERT INTO proc.act_group (created_by) VALUES (%s) RETURNING id", (by,))
        gid = c.fetchone()["id"]
        _add_member(c, gid, adam_a, by)
        _add_member(c, gid, adam_b, by)


def remove_member(c, adam):
    """Remove an act from its group; dissolve the group if <2 members remain."""
    gid = group_of(c, adam)
    if not gid:
        return
    c.execute("DELETE FROM proc.act_group_member WHERE adam = %s", (adam,))
    c.execute("SELECT count(*) AS n FROM proc.act_group_member WHERE group_id = %s", (gid,))
    if c.fetchone()["n"] < 2:
        c.execute("DELETE FROM proc.act_group WHERE id = %s", (gid,))  # cascades the lone member


def set_duplicate(c, adam, original, by=None):
    """Mark `adam` as a duplicate of `original` — relating them first if needed."""
    if adam == original:
        return
    relate(c, adam, original, by)
    c.execute("""UPDATE proc.act_group_member
                 SET is_duplicate = true, duplicate_of = %s WHERE adam = %s""",
              (original, adam))


def clear_duplicate(c, adam):
    c.execute("""UPDATE proc.act_group_member
                 SET is_duplicate = false, duplicate_of = NULL WHERE adam = %s""", (adam,))


def list_groups(c, limit=200):
    c.execute("""
        SELECT g.id, g.created_at,
               count(m.adam) AS n,
               count(*) FILTER (WHERE m.is_duplicate) AS n_dup,
               (array_agg(a.title ORDER BY a.submission_date NULLS LAST))[1] AS sample_title
        FROM proc.act_group g
        JOIN proc.act_group_member m ON m.group_id = g.id
        JOIN proc.procurement_act a ON a.adam = m.adam
        GROUP BY g.id
        ORDER BY g.created_at DESC
        LIMIT %s
    """, (limit,))
    return c.fetchall()


def group_panel(c, adam):
    """For the read-only act-detail panel: the act's group members (excluding
    itself), or None."""
    gid = group_of(c, adam)
    if not gid:
        return None
    members = [m for m in group_members(c, gid) if m["adam"] != adam]
    return {"group_id": gid, "members": members} if members else None


# --------------------------------------------------------------------------- #
# scan: bulk auto-group high-confidence identifier + same-authority pairs
# --------------------------------------------------------------------------- #
def scan(c, limit=300):
    """Preview pairs that share a discriminating identifier AND the same
    authority, scoring ≥ auto_min (the safe, high-precision auto-group set)."""
    rules = rules_map(c)
    st = load_settings(c)
    auto_min, max_shared = st.get("auto_min", 90), st.get("max_shared", 30)
    auth_w = rules["authority"]["weight"] if rules.get("authority", {}).get("is_active") else 0

    pairs = {}
    for r in (x for x in rules.values()
              if x["kind"] == "identifier" and x["is_active"] and x["field"] in _ID_FIELDS):
        field, w = r["field"], r["weight"]
        score = min(100, w + auth_w)
        if score < auto_min:
            continue
        c.execute(f"""
            WITH grp AS (
              SELECT btrim({field}) AS val, authority_id, array_agg(adam) AS adams
              FROM proc.procurement_act
              WHERE btrim({field}) IS NOT NULL
                AND char_length(btrim({field})) >= 3
                AND btrim({field}) !~ '^[[:punct:][:space:]]+$'
                AND authority_id IS NOT NULL
              GROUP BY btrim({field}), authority_id
              HAVING count(*) BETWEEN 2 AND %s
            )
            SELECT adams FROM grp LIMIT %s
        """, (max_shared, limit))
        for row in c.fetchall():
            adams = sorted(row["adams"])
            for i in range(len(adams)):
                for j in range(i + 1, len(adams)):
                    pairs.setdefault((adams[i], adams[j]), score)
    # keep only pairs not already grouped together
    out = []
    for (a, b), score in pairs.items():
        ga, gb = group_of(c, a), group_of(c, b)
        if ga and ga == gb:
            continue
        out.append({"a": a, "b": b, "score": score})
        if len(out) >= limit:
            break
    return out


def apply_scan(c, by=None, limit=300):
    """Relate every pair the scan surfaces. Returns how many pairs were applied."""
    n = 0
    for p in scan(c, limit):
        relate(c, p["a"], p["b"], by)
        n += 1
    return n


# --------------------------------------------------------------------------- #
# router
# --------------------------------------------------------------------------- #
def make_interconnect_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin/interconnect", tags=["interconnect"])

    def _by(request):
        u = getattr(request.state, "user", None)
        return u.get("username") if u else None

    @router.get("", response_class=HTMLResponse)
    def home(request: Request, q: str = "", ok: str = None):
        q = (q or "").strip()
        found = None
        with cursor() as c:
            if q:
                c.execute("""
                    SELECT a.adam, a.type, a.title
                    FROM proc.procurement_act a
                    WHERE a.adam = %s
                       OR translate(proc.f_unaccent(lower(a.title)),'ς','σ')
                          LIKE translate(proc.f_unaccent(lower(%s)),'ς','σ')
                    ORDER BY a.submission_date DESC NULLS LAST LIMIT 25
                """, (q, f"%{q}%"))
                found = c.fetchall()
            groups = list_groups(c)
            rules = load_rules(c)
            settings = load_settings(c)
        return templates.TemplateResponse(request, "admin_interconnect.html", {
            "q": q, "found": found, "groups": groups, "rules": rules,
            "settings": settings, "ok": ok, "admin_tab": "interconnect"})

    @router.get("/act/{adam}", response_class=HTMLResponse)
    def act_page(adam: str, request: Request, ok: str = None):
        with cursor() as c:
            act = _get_act(c, adam)
            if not act:
                raise HTTPException(404, "act not found")
            cands = candidates_for(c, adam)
            gid = group_of(c, adam)
            members = [m for m in group_members(c, gid) if m["adam"] != adam] if gid else []
        return templates.TemplateResponse(request, "admin_interconnect_act.html", {
            "act": act, "candidates": cands, "group_id": gid, "members": members,
            "ok": ok, "admin_tab": "interconnect"})

    @router.get("/compare", response_class=HTMLResponse)
    def compare(request: Request, a: str, b: str):
        with cursor() as c:
            cmp = compare_pair(c, a, b)
            ga, gb = group_of(c, a), group_of(c, b)
        return templates.TemplateResponse(request, "admin_interconnect_compare.html", {
            "cmp": cmp, "a": a, "b": b, "ga": ga, "gb": gb, "admin_tab": "interconnect"})

    @router.get("/group/{gid}", response_class=HTMLResponse)
    def group_page(gid: int, request: Request, ok: str = None):
        with cursor() as c:
            members = group_members(c, gid)
            if not members:
                raise HTTPException(404, "group not found")
        return templates.TemplateResponse(request, "admin_interconnect_group.html", {
            "gid": gid, "members": members, "ok": ok, "admin_tab": "interconnect"})

    @router.post("/relate")
    async def do_relate(request: Request):
        form = await request.form()
        a, b = (form.get("a") or "").strip(), (form.get("b") or "").strip()
        back = (form.get("back") or f"/admin/interconnect/act/{a}")
        with cursor() as c:
            relate(c, a, b, _by(request))
        return RedirectResponse(f"{back}?ok=related", status_code=303)

    @router.post("/duplicate")
    async def do_duplicate(request: Request):
        form = await request.form()
        adam, original = (form.get("adam") or "").strip(), (form.get("original") or "").strip()
        back = (form.get("back") or f"/admin/interconnect/act/{original}")
        with cursor() as c:
            set_duplicate(c, adam, original, _by(request))
        return RedirectResponse(f"{back}?ok=duplicate", status_code=303)

    @router.post("/unduplicate")
    async def do_unduplicate(request: Request):
        form = await request.form()
        adam = (form.get("adam") or "").strip()
        back = (form.get("back") or f"/admin/interconnect/act/{adam}")
        with cursor() as c:
            clear_duplicate(c, adam)
        return RedirectResponse(f"{back}?ok=duplicate", status_code=303)

    @router.post("/remove")
    async def do_remove(request: Request):
        form = await request.form()
        adam = (form.get("adam") or "").strip()
        back = (form.get("back") or f"/admin/interconnect/act/{adam}")
        with cursor() as c:
            remove_member(c, adam)
        return RedirectResponse(f"{back}?ok=removed", status_code=303)

    @router.post("/rules")
    async def save_rules(request: Request):
        form = await request.form()
        with cursor() as c:
            for r in load_rules(c):
                w = form.get(f"w_{r['code']}")
                active = form.get(f"a_{r['code']}") == "on"
                if w is not None:
                    set_rule(c, r["code"], w, active)
            for key in ("review_min", "auto_min", "max_shared"):
                v = form.get(key)
                if v not in (None, ""):
                    set_setting(c, key, v)
        return RedirectResponse("/admin/interconnect?ok=rules", status_code=303)

    @router.post("/scan")
    async def do_scan(request: Request):
        form = await request.form()
        apply = form.get("apply") == "1"
        with cursor() as c:
            if apply:
                n = apply_scan(c, _by(request))
                return RedirectResponse(f"/admin/interconnect?ok=scan_{n}", status_code=303)
            preview = scan(c)
            # enrich preview rows with titles for display
            rows = []
            for p in preview[:200]:
                a, b = _get_act(c, p["a"]), _get_act(c, p["b"])
                rows.append({**p, "a_title": a["title"] if a else "", "b_title": b["title"] if b else ""})
            groups = list_groups(c)
            rules = load_rules(c)
            settings = load_settings(c)
        return templates.TemplateResponse(request, "admin_interconnect.html", {
            "q": "", "found": None, "groups": groups, "rules": rules,
            "settings": settings, "scan_rows": rows, "admin_tab": "interconnect"})

    return router
