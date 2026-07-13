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

import uuid

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
    """Merge group `drop` into `keep`: move members, identifiers, and lots;
    reconcile duplicate lots by (source, source_key) — repointing scope links to
    the canonical lot and merging non-null fields — then drop the empty group.
    Never loses imported lot data. Caller owns the transaction boundary."""
    if keep == drop:
        return
    # 1. members
    c.execute("UPDATE proc.act_group_member SET group_id = %s WHERE group_id = %s",
              (keep, drop))
    # 2. identifiers — a (scheme,value) already on `keep` wins; drop's dup is discarded
    c.execute("""UPDATE proc.act_group_identifier d SET group_id = %s
                 WHERE d.group_id = %s
                   AND NOT EXISTS (SELECT 1 FROM proc.act_group_identifier k
                                   WHERE k.scheme = d.scheme AND k.value = d.value
                                     AND k.group_id = %s)""", (keep, drop, keep))
    c.execute("DELETE FROM proc.act_group_identifier WHERE group_id = %s", (drop,))
    # 3. reconcile lots that collide on (source, source_key)
    c.execute("""SELECT d.id AS drop_id, k.id AS keep_id
                 FROM proc.tender_lot d
                 JOIN proc.tender_lot k
                   ON k.group_id = %s AND k.source = d.source AND k.source_key = d.source_key
                 WHERE d.group_id = %s""", (keep, drop))
    for row in c.fetchall():
        di, ki = row["drop_id"], row["keep_id"]
        # repoint scope links from the dup lot to the canonical one (skip rows the
        # act already has for the canonical lot), then drop leftover dup links
        c.execute("""UPDATE proc.act_lot_scope s SET lot_id = %s
                     WHERE s.lot_id = %s
                       AND NOT EXISTS (SELECT 1 FROM proc.act_lot_scope s2
                                       WHERE s2.adam = s.adam AND s2.lot_id = %s)""",
                  (ki, di, ki))
        c.execute("DELETE FROM proc.act_lot_scope WHERE lot_id = %s", (di,))
        # merge non-null descriptive fields conservatively onto the kept lot
        c.execute("""UPDATE proc.tender_lot k SET
                        lot_number      = COALESCE(k.lot_number, d.lot_number),
                        title           = COALESCE(k.title, d.title),
                        description     = COALESCE(k.description, d.description),
                        status          = COALESCE(k.status, d.status),
                        estimated_value = COALESCE(k.estimated_value, d.estimated_value),
                        awarded_value   = COALESCE(k.awarded_value, d.awarded_value),
                        currency_code   = COALESCE(k.currency_code, d.currency_code),
                        raw_json        = COALESCE(k.raw_json, d.raw_json)
                     FROM proc.tender_lot d WHERE k.id = %s AND d.id = %s""", (ki, di))
        c.execute("""INSERT INTO proc.tender_lot_cpv (lot_id, cpv_code)
                     SELECT %s, cpv_code FROM proc.tender_lot_cpv WHERE lot_id = %s
                     ON CONFLICT DO NOTHING""", (ki, di))
        c.execute("""INSERT INTO proc.tender_lot_nuts (lot_id, nuts_code)
                     SELECT %s, nuts_code FROM proc.tender_lot_nuts WHERE lot_id = %s
                     ON CONFLICT DO NOTHING""", (ki, di))
        c.execute("DELETE FROM proc.tender_lot WHERE id = %s", (di,))
    # 4. move the surviving (non-duplicate) lots, then drop the now-empty group
    c.execute("UPDATE proc.tender_lot SET group_id = %s WHERE group_id = %s", (keep, drop))
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
    """Remove an act from its group. Deletes the act's own scope first (its lot
    links cascade), leaving the group's lots and other acts untouched. Dissolves
    the group only when the remainder has <2 members AND owns no lots and no
    identifiers. Refuses to strand a group's lots: removing the last member of a
    group that still owns lots raises 409."""
    gid = group_of(c, adam)
    if not gid:
        return
    c.execute("SELECT count(*) AS n FROM proc.act_group_member WHERE group_id = %s", (gid,))
    members = c.fetchone()["n"]
    c.execute("SELECT count(*) AS n FROM proc.tender_lot WHERE group_id = %s", (gid,))
    has_lots = c.fetchone()["n"] > 0
    c.execute("SELECT count(*) AS n FROM proc.act_group_identifier WHERE group_id = %s", (gid,))
    has_ids = c.fetchone()["n"] > 0

    if members <= 1 and has_lots:
        raise HTTPException(409, "group still owns lots; delete or reassign them first")

    c.execute("DELETE FROM proc.act_scope WHERE adam = %s", (adam,))       # links cascade
    c.execute("DELETE FROM proc.act_group_member WHERE adam = %s", (adam,))
    # dissolve a now-trivial group only when nothing else is anchored to it
    if members - 1 < 2 and not has_lots and not has_ids:
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
    """Curated group listing. Machine-created singleton groups (auto=true, one
    per TED publication/procedure) are hidden UNLESS they've grown past one
    member — otherwise thousands of auto groups would drown the curated ones.
    They stay reachable from each act's own interconnect page."""
    c.execute("""
        SELECT g.id, g.created_at, g.auto,
               count(m.adam) AS n,
               count(*) FILTER (WHERE m.is_duplicate) AS n_dup,
               (SELECT count(*) FROM proc.tender_lot l WHERE l.group_id = g.id) AS n_lots,
               (array_agg(a.title ORDER BY a.submission_date NULLS LAST))[1] AS sample_title
        FROM proc.act_group g
        JOIN proc.act_group_member m ON m.group_id = g.id
        JOIN proc.procurement_act a ON a.adam = m.adam
        GROUP BY g.id
        HAVING NOT g.auto OR count(m.adam) > 1
        ORDER BY g.created_at DESC
        LIMIT %s
    """, (limit,))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# lifecycle-group identifiers + ensure helpers (reused by TED projection)
# --------------------------------------------------------------------------- #
def ensure_group_for_act(c, adam, *, created_by=None) -> int:
    """Return the act's group, creating a singleton for it if it has none."""
    gid = group_of(c, adam)
    if gid:
        return gid
    c.execute("INSERT INTO proc.act_group (created_by) VALUES (%s) RETURNING id", (created_by,))
    gid = c.fetchone()["id"]
    _add_member(c, gid, adam, created_by)
    return gid


def group_by_identifier(c, scheme, value):
    c.execute("SELECT group_id FROM proc.act_group_identifier WHERE scheme = %s AND value = %s",
              (scheme, value))
    row = c.fetchone()
    return row["group_id"] if row else None


def ensure_group_identifier(c, adam, scheme, value, *, created_by=None) -> int:
    """Attach `adam` to the lifecycle group carrying (scheme, value), creating an
    `auto` singleton group + the identifier if none exists yet. If the act is
    already in a different group, merge that group into the identifier's group
    (the identifier's group is kept). Returns the resulting group id."""
    gid = group_by_identifier(c, scheme, value)
    cur = group_of(c, adam)
    if gid is None:
        gid = cur
        if gid is None:
            c.execute("INSERT INTO proc.act_group (created_by, auto) VALUES (%s, true) RETURNING id",
                      (created_by,))
            gid = c.fetchone()["id"]
        c.execute("""INSERT INTO proc.act_group_identifier (group_id, scheme, value)
                     VALUES (%s, %s, %s) ON CONFLICT (scheme, value) DO NOTHING""",
                  (gid, scheme, value))
    if cur is not None and cur != gid:
        merge_groups(c, gid, cur)
    elif cur is None:
        _add_member(c, gid, adam, created_by)
    return gid


# --------------------------------------------------------------------------- #
# lots (canonical, group-owned) — CRUD + reads
# --------------------------------------------------------------------------- #
_LOT_EDITABLE = ("lot_number", "title", "description", "status",
                 "estimated_value", "awarded_value", "currency_code")


def lots_for_group(c, gid):
    """All lots of a group with their CPV/NUTS codes and #scoped acts."""
    c.execute("""
        SELECT l.id, l.group_id, l.source, l.source_key, l.origin, l.lot_number,
               l.title, l.description, l.status, l.estimated_value, l.awarded_value,
               l.currency_code, l.created_by, l.created_at, l.updated_at,
               (SELECT array_agg(cpv_code ORDER BY cpv_code)
                  FROM proc.tender_lot_cpv WHERE lot_id = l.id) AS cpvs,
               (SELECT array_agg(nuts_code ORDER BY nuts_code)
                  FROM proc.tender_lot_nuts WHERE lot_id = l.id) AS nuts,
               (SELECT count(*) FROM proc.act_lot_scope s WHERE s.lot_id = l.id) AS n_acts
        FROM proc.tender_lot l
        WHERE l.group_id = %s
        ORDER BY l.source, l.lot_number NULLS LAST, l.source_key
    """, (gid,))
    return c.fetchall()


def create_lot(c, gid, *, created_by=None, **fields):
    """Create an AUTHORED lot in a group (source='manual', stable MANUAL-<uuid>)."""
    key = "MANUAL-" + uuid.uuid4().hex
    cols = {k: fields.get(k) for k in _LOT_EDITABLE}
    c.execute("""INSERT INTO proc.tender_lot
                   (group_id, source, source_key, origin, created_by,
                    lot_number, title, description, status,
                    estimated_value, awarded_value, currency_code)
                 VALUES (%s, 'manual', %s, 'authored', %s, %s, %s, %s, %s, %s, %s, %s)
                 RETURNING id""",
              (gid, key, created_by, cols["lot_number"], cols["title"],
               cols["description"], cols["status"], cols["estimated_value"],
               cols["awarded_value"], cols["currency_code"]))
    return c.fetchone()["id"]


def update_lot(c, lot_id, **fields):
    """Edit an AUTHORED lot (imported lots are read-only)."""
    sets, vals = [], []
    for k in _LOT_EDITABLE:
        if k in fields:
            sets.append(f"{k} = %s")
            vals.append(fields[k])
    if not sets:
        return
    vals.append(lot_id)
    c.execute(f"UPDATE proc.tender_lot SET {', '.join(sets)} "
              f"WHERE id = %s AND origin = 'authored'", vals)


def delete_lot(c, lot_id, *, force=False):
    """Delete an AUTHORED lot. If it has act scope links, refuse (409) unless
    `force`; on force, its links cascade and any act thereby left with a
    specific_lots scope but no lots is reset to 'unknown' (never leaves an act
    claiming specific lots it no longer has)."""
    c.execute("SELECT origin FROM proc.tender_lot WHERE id = %s", (lot_id,))
    row = c.fetchone()
    if not row:
        return 0
    if row["origin"] != "authored":
        raise HTTPException(409, "imported lots are read-only")
    c.execute("SELECT adam FROM proc.act_lot_scope WHERE lot_id = %s", (lot_id,))
    affected = [r["adam"] for r in c.fetchall()]
    if affected and not force:
        raise HTTPException(409, f"lot is referenced by {len(affected)} act(s); confirm removal")
    c.execute("DELETE FROM proc.tender_lot WHERE id = %s AND origin = 'authored'", (lot_id,))
    for adam in affected:
        c.execute("SELECT 1 FROM proc.act_lot_scope WHERE adam = %s LIMIT 1", (adam,))
        if not c.fetchone():
            c.execute("""UPDATE proc.act_scope SET scope_kind = 'unknown', updated_at = now()
                         WHERE adam = %s AND scope_kind = 'specific_lots'""", (adam,))
    return 1


# --------------------------------------------------------------------------- #
# act scope (which part of the tender an act applies to)
# --------------------------------------------------------------------------- #
def scope_for_act(c, adam):
    """The act's scope: {kind, lot_ids, source, note}. Absent row == unknown."""
    c.execute("SELECT scope_kind, scope_source, note FROM proc.act_scope WHERE adam = %s", (adam,))
    row = c.fetchone()
    c.execute("SELECT lot_id FROM proc.act_lot_scope WHERE adam = %s ORDER BY lot_id", (adam,))
    lot_ids = [r["lot_id"] for r in c.fetchall()]
    return {"kind": row["scope_kind"] if row else "unknown",
            "lot_ids": lot_ids,
            "source": row["scope_source"] if row else None,
            "note": row["note"] if row else None}


def scope_is_curator(c, adam) -> bool:
    """True if a curator set this act's scope (ingestion must not overwrite it)."""
    c.execute("SELECT scope_source FROM proc.act_scope WHERE adam = %s", (adam,))
    row = c.fetchone()
    return bool(row and row["scope_source"] == "curator")


def _upsert_scope(c, adam, kind, *, source, by):
    c.execute("""INSERT INTO proc.act_scope (adam, scope_kind, scope_source, updated_by, updated_at)
                 VALUES (%s, %s, %s, %s, now())
                 ON CONFLICT (adam) DO UPDATE SET
                    scope_kind = EXCLUDED.scope_kind, scope_source = EXCLUDED.scope_source,
                    updated_by = EXCLUDED.updated_by, updated_at = now()""",
              (adam, kind, source, by))


def set_scope_unknown(c, adam, *, source="curator", by=None):
    c.execute("DELETE FROM proc.act_lot_scope WHERE adam = %s", (adam,))
    _upsert_scope(c, adam, "unknown", source=source, by=by)


def set_scope_whole(c, adam, *, source="curator", by=None):
    c.execute("DELETE FROM proc.act_lot_scope WHERE adam = %s", (adam,))
    _upsert_scope(c, adam, "whole_tender", source=source, by=by)


def set_scope_lots(c, adam, lot_ids, *, source="curator", by=None):
    """Point the act at one or more specific lots (all in the act's group).
    Enforces >=1 lot and group membership here (the DB trigger backs this up)."""
    lot_ids = [int(x) for x in lot_ids]
    if not lot_ids:
        raise HTTPException(400, "specific lots requires at least one lot")
    gid = group_of(c, adam)
    if gid is None:
        raise HTTPException(409, "act is not in a group")
    c.execute("SELECT id FROM proc.tender_lot WHERE group_id = %s AND id = ANY(%s)",
              (gid, lot_ids))
    valid = {r["id"] for r in c.fetchall()}
    missing = [x for x in lot_ids if x not in valid]
    if missing:
        raise HTTPException(400, f"lots not in this group: {missing}")
    # scope_kind must be specific_lots BEFORE the bridge rows are inserted (trigger)
    _upsert_scope(c, adam, "specific_lots", source=source, by=by)
    c.execute("DELETE FROM proc.act_lot_scope WHERE adam = %s", (adam,))
    for lid in valid:
        c.execute("INSERT INTO proc.act_lot_scope (adam, lot_id) VALUES (%s, %s) "
                  "ON CONFLICT DO NOTHING", (adam, lid))


def group_lot_panel(c, gid):
    """Read model for act pages / group page: the group's lots plus, per lot, the
    acts scoped to it; plus the whole-tender and unknown act buckets. Returns
    None when the group has no lots."""
    lots = lots_for_group(c, gid)
    if not lots:
        return None
    # acts scoped specifically, per lot
    c.execute("""
        SELECT s.lot_id, s.adam, a.type, a.title
        FROM proc.act_lot_scope s
        JOIN proc.procurement_act a ON a.adam = s.adam
        WHERE s.lot_id IN (SELECT id FROM proc.tender_lot WHERE group_id = %s)
        ORDER BY a.submission_date NULLS LAST, s.adam
    """, (gid,))
    by_lot = {}
    for r in c.fetchall():
        by_lot.setdefault(r["lot_id"], []).append(r)
    # whole-tender acts in this group
    c.execute("""
        SELECT sc.adam, a.type, a.title
        FROM proc.act_scope sc
        JOIN proc.act_group_member m ON m.adam = sc.adam
        JOIN proc.procurement_act a ON a.adam = sc.adam
        WHERE m.group_id = %s AND sc.scope_kind = 'whole_tender'
        ORDER BY a.submission_date NULLS LAST, sc.adam
    """, (gid,))
    whole = c.fetchall()
    # members whose scope is unknown / unset — the "Not determined" bucket
    c.execute("""
        SELECT m.adam, a.type, a.title
        FROM proc.act_group_member m
        JOIN proc.procurement_act a ON a.adam = m.adam
        LEFT JOIN proc.act_scope sc ON sc.adam = m.adam
        WHERE m.group_id = %s AND COALESCE(sc.scope_kind, 'unknown') = 'unknown'
        ORDER BY a.submission_date NULLS LAST, m.adam
    """, (gid,))
    unknown = c.fetchall()
    return {"lots": lots, "acts_by_lot": by_lot,
            "whole_tender": whole, "unknown": unknown}


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
    def group_page(gid: int, request: Request, ok: str = None, err: str = None):
        with cursor() as c:
            members = group_members(c, gid)
            if not members:
                raise HTTPException(404, "group not found")
            lots = lots_for_group(c, gid)
            scopes = {m["adam"]: scope_for_act(c, m["adam"]) for m in members}
        return templates.TemplateResponse(request, "admin_interconnect_group.html", {
            "gid": gid, "members": members, "lots": lots, "scopes": scopes,
            "ok": ok, "err": err, "admin_tab": "interconnect"})

    @router.post("/relate")
    async def do_relate(request: Request):
        form = await request.form()
        a, b = (form.get("a") or "").strip(), (form.get("b") or "").strip()
        back = (form.get("back") or f"/admin/interconnect/act/{a}")
        with cursor() as c, c.connection.transaction():
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
        with cursor() as c, c.connection.transaction():
            remove_member(c, adam)
        return RedirectResponse(f"{back}?ok=removed", status_code=303)

    # ------- lots + scope (group page) ------------------------------------- #
    def _num(v):
        v = (v or "").strip().replace(",", ".")
        try:
            return float(v) if v else None
        except ValueError:
            return None

    @router.post("/lot/create")
    async def do_lot_create(request: Request):
        form = await request.form()
        gid = int(form.get("gid"))
        with cursor() as c, c.connection.transaction():
            create_lot(c, gid, created_by=_by(request),
                       lot_number=(form.get("lot_number") or "").strip() or None,
                       title=(form.get("title") or "").strip() or None,
                       description=(form.get("description") or "").strip() or None,
                       status=(form.get("status") or "").strip() or None,
                       estimated_value=_num(form.get("estimated_value")),
                       awarded_value=_num(form.get("awarded_value")),
                       currency_code=(form.get("currency_code") or "").strip() or None)
        return RedirectResponse(f"/admin/interconnect/group/{gid}?ok=lot_created", status_code=303)

    @router.post("/lot/edit")
    async def do_lot_edit(request: Request):
        form = await request.form()
        gid, lot_id = int(form.get("gid")), int(form.get("lot_id"))
        with cursor() as c, c.connection.transaction():
            update_lot(c, lot_id,
                       lot_number=(form.get("lot_number") or "").strip() or None,
                       title=(form.get("title") or "").strip() or None,
                       description=(form.get("description") or "").strip() or None,
                       status=(form.get("status") or "").strip() or None,
                       estimated_value=_num(form.get("estimated_value")),
                       awarded_value=_num(form.get("awarded_value")),
                       currency_code=(form.get("currency_code") or "").strip() or None)
        return RedirectResponse(f"/admin/interconnect/group/{gid}?ok=lot_saved", status_code=303)

    @router.post("/lot/delete")
    async def do_lot_delete(request: Request):
        form = await request.form()
        gid, lot_id = int(form.get("gid")), int(form.get("lot_id"))
        force = form.get("force") == "1"
        try:
            with cursor() as c, c.connection.transaction():
                delete_lot(c, lot_id, force=force)
        except HTTPException as e:
            return RedirectResponse(f"/admin/interconnect/group/{gid}?err=lot_ref",
                                    status_code=303)
        return RedirectResponse(f"/admin/interconnect/group/{gid}?ok=lot_deleted", status_code=303)

    @router.post("/scope")
    async def do_scope(request: Request):
        form = await request.form()
        gid = int(form.get("gid"))
        adam = (form.get("adam") or "").strip()
        kind = (form.get("scope_kind") or "unknown").strip()
        lot_ids = [int(x) for x in form.getlist("lot_ids") if x]
        by = _by(request)
        try:
            with cursor() as c, c.connection.transaction():
                if kind == "whole_tender":
                    set_scope_whole(c, adam, by=by)
                elif kind == "specific_lots":
                    set_scope_lots(c, adam, lot_ids, by=by)
                else:
                    set_scope_unknown(c, adam, by=by)
        except HTTPException:
            return RedirectResponse(f"/admin/interconnect/group/{gid}?err=scope",
                                    status_code=303)
        return RedirectResponse(f"/admin/interconnect/group/{gid}?ok=scope_saved", status_code=303)

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
