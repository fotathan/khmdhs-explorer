"""Structured lots + act scope: service helpers, DB-level enforcement, group
merge/member-removal, and the admin/public routes. DB-backed — skips without
TEST_DATABASE_URL (via the _clean/_schema fixtures)."""
import psycopg
import pytest

from app import interconnect as ic
from tests.helpers import connect, get_csrf, login, make_user


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def lots_clean(_clean):
    """Also clear the lot/scope/group graph between tests (schema-only snapshot,
    so procurement_act/ted_notice start empty)."""
    with connect() as c:
        c.execute("TRUNCATE proc.act_group, proc.procurement_act, proc.ted_notice CASCADE")
    yield


def _acts(cur, *adams):
    for a in adams:
        cur.execute("""INSERT INTO proc.procurement_act (adam, type, data_source, origin, title)
                       VALUES (%s,'notice','manual','authored',%s)
                       ON CONFLICT (adam) DO NOTHING""", (a, a))


def _cur():
    conn = connect()
    return conn, conn.cursor()


# --------------------------------------------------------------------------- #
# service-level behaviour
# --------------------------------------------------------------------------- #
def test_singleton_group_for_act(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    assert gid and ic.group_of(c, "A1") == gid
    assert ic.ensure_group_for_act(c, "A1") == gid       # idempotent
    conn.close()


def test_authored_lot_crud(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    lid = ic.create_lot(c, gid, title="Lot A", lot_number="1")
    lots = ic.lots_for_group(c, gid)
    assert len(lots) == 1 and lots[0]["title"] == "Lot A" and lots[0]["origin"] == "authored"
    ic.update_lot(c, lid, title="Lot A2")
    assert ic.lots_for_group(c, gid)[0]["title"] == "Lot A2"
    assert ic.delete_lot(c, lid) == 1
    assert ic.lots_for_group(c, gid) == []
    conn.close()


def test_scope_modes(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    l1 = ic.create_lot(c, gid, title="L1")
    l2 = ic.create_lot(c, gid, title="L2")

    ic.set_scope_whole(c, "A1")
    assert ic.scope_for_act(c, "A1")["kind"] == "whole_tender"

    ic.set_scope_lots(c, "A1", [l1, l2])
    sc = ic.scope_for_act(c, "A1")
    assert sc["kind"] == "specific_lots" and set(sc["lot_ids"]) == {l1, l2}

    # narrowing to one lot clears the stale bridge row
    ic.set_scope_lots(c, "A1", [l1])
    assert ic.scope_for_act(c, "A1")["lot_ids"] == [l1]

    ic.set_scope_unknown(c, "A1")
    assert ic.scope_for_act(c, "A1") == {"kind": "unknown", "lot_ids": [],
                                         "source": "curator", "note": None}
    conn.close()


def test_empty_specific_lots_rejected(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    ic.create_lot(c, gid, title="L1")
    with pytest.raises(Exception):
        ic.set_scope_lots(c, "A1", [])
    conn.close()


def test_cross_group_link_rejected_at_db(lots_clean):
    """The DB trigger — not just the service — rejects a lot from another group."""
    conn, c = _cur()
    _acts(c, "A1", "A2")
    g1 = ic.ensure_group_for_act(c, "A1")
    g2 = ic.ensure_group_for_act(c, "A2")
    lot_in_g2 = ic.create_lot(c, g2, title="foreign")
    # bypass the service: set scope directly, then try the raw bridge insert
    c.execute("INSERT INTO proc.act_scope (adam, scope_kind) VALUES ('A1','specific_lots')")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        c.execute("INSERT INTO proc.act_lot_scope (adam, lot_id) VALUES ('A1', %s)", (lot_in_g2,))
    conn.close()


def test_ingestion_wont_overwrite_curator_scope(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    l1 = ic.create_lot(c, gid, title="L1")
    ic.set_scope_lots(c, "A1", [l1], source="curator")
    assert ic.scope_is_curator(c, "A1") is True
    # an importer must check scope_is_curator before writing; the flag is the guard
    assert ic.scope_for_act(c, "A1")["source"] == "curator"
    conn.close()


# --------------------------------------------------------------------------- #
# group merge + member removal
# --------------------------------------------------------------------------- #
def test_merge_unique_lots_preserved(lots_clean):
    conn, c = _cur()
    _acts(c, "A1", "A2")
    g1 = ic.ensure_group_for_act(c, "A1")
    g2 = ic.ensure_group_for_act(c, "A2")
    ic.create_lot(c, g1, title="only-in-1")
    ic.create_lot(c, g2, title="only-in-2")
    ic.merge_groups(c, g1, g2)
    titles = {l["title"] for l in ic.lots_for_group(c, g1)}
    assert titles == {"only-in-1", "only-in-2"}
    assert ic.group_of(c, "A2") == g1
    conn.close()


def test_merge_colliding_source_key_reconciled(lots_clean):
    conn, c = _cur()
    _acts(c, "A1", "A2")
    g1 = ic.ensure_group_for_act(c, "A1")
    g2 = ic.ensure_group_for_act(c, "A2")
    # same TED lot key in both groups + a scope link on the dropped group's lot
    c.execute("INSERT INTO proc.tender_lot (group_id, source, source_key, title) "
              "VALUES (%s,'ted','LOT-1','keep') RETURNING id", (g1,))
    keep_lot = c.fetchone()["id"]
    c.execute("INSERT INTO proc.tender_lot (group_id, source, source_key, title) "
              "VALUES (%s,'ted','LOT-1','drop') RETURNING id", (g2,))
    drop_lot = c.fetchone()["id"]
    ic.set_scope_lots(c, "A2", [drop_lot])       # A2 scoped to the soon-merged dup

    ic.merge_groups(c, g1, g2)
    lots = ic.lots_for_group(c, g1)
    assert len(lots) == 1 and lots[0]["id"] == keep_lot   # one canonical lot survives
    # A2's scope was repointed to the surviving lot, not lost
    assert ic.scope_for_act(c, "A2")["lot_ids"] == [keep_lot]
    conn.close()


def test_remove_last_member_with_lots_refused(lots_clean):
    conn, c = _cur()
    _acts(c, "A1")
    gid = ic.ensure_group_for_act(c, "A1")
    ic.create_lot(c, gid, title="orphan-risk")
    with pytest.raises(Exception):
        ic.remove_member(c, "A1")               # would strand the group's lots → 409
    assert ic.group_of(c, "A1") == gid          # unchanged
    conn.close()


def test_remove_member_clears_only_its_scope(lots_clean):
    conn, c = _cur()
    _acts(c, "A1", "A2")
    g1 = ic.ensure_group_for_act(c, "A1")
    ic._add_member(c, g1, "A2", None)
    l1 = ic.create_lot(c, g1, title="L1")
    ic.set_scope_lots(c, "A1", [l1])
    ic.set_scope_whole(c, "A2")
    ic.remove_member(c, "A2")                    # multi-member + lots → allowed
    assert ic.group_of(c, "A2") is None
    c.execute("SELECT 1 FROM proc.act_scope WHERE adam='A2'")
    assert c.fetchone() is None                  # A2's scope gone
    assert ic.scope_for_act(c, "A1")["lot_ids"] == [l1]   # A1 untouched
    conn.close()


# --------------------------------------------------------------------------- #
# routes (admin gating + public rendering)
# --------------------------------------------------------------------------- #
def _admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")


def _group_with_member(adam="A1"):
    with connect() as c:
        cur = c.cursor()
        _acts(cur, adam)
        gid = ic.ensure_group_for_act(cur, adam)
        return gid


def test_admin_lot_and_scope_flow(client, lots_clean):
    gid = _group_with_member("A1")
    _admin(client)
    tok = get_csrf(client)
    # create an authored lot via the route
    r = client.post("/admin/interconnect/lot/create",
                    data={"gid": gid, "title": "Route lot", "lot_number": "1"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 303
    with connect() as c:
        lots = ic.lots_for_group(c.cursor(), gid)
    assert len(lots) == 1 and lots[0]["title"] == "Route lot"
    lot_id = lots[0]["id"]

    # set the act's scope to that lot
    r = client.post("/admin/interconnect/scope",
                    data={"gid": gid, "adam": "A1", "scope_kind": "specific_lots",
                          "lot_ids": [lot_id]},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    with connect() as c:
        assert ic.scope_for_act(c.cursor(), "A1")["lot_ids"] == [lot_id]

    # the group page shows the lot + Applies-to control
    html = client.get(f"/admin/interconnect/group/{gid}").text
    assert "Route lot" in html


def test_admin_routes_require_admin(client, lots_clean):
    gid = _group_with_member("A1")
    # not logged in → gated/redirected, never a 303 success
    r = client.post("/admin/interconnect/lot/create",
                    data={"gid": gid, "title": "x"}, follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
    if r.status_code == 303:
        assert "/login" in r.headers.get("location", "")


def test_public_act_page_buckets(client, lots_clean):
    _admin(client)      # anonymous callers are gated (no below-the-fold panels)
    # A1 (specific lot), A2 (whole), A3 (unknown) in one group with a lot
    with connect() as c:
        cur = c.cursor()
        _acts(cur, "A1", "A2", "A3")
        gid = ic.ensure_group_for_act(cur, "A1")
        ic._add_member(cur, gid, "A2", None)
        ic._add_member(cur, gid, "A3", None)
        l1 = ic.create_lot(cur, gid, title="Public Lot")
        ic.set_scope_lots(cur, "A1", [l1])
        ic.set_scope_whole(cur, "A2")
    html = client.get("/act/A1").text
    assert "Public Lot" in html
    # whole-tender + not-determined buckets rendered (Greek labels)
    assert "Σε όλο τον διαγωνισμό" in html
    assert "Δεν έχει προσδιοριστεί" in html
