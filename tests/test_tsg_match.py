"""Tender Service duplicate handling (tsg_match.py, docs/specs/tender-service-duplicates.md).

What matters: an exact number hides and nothing else does; a hospital's generic
title on the same deadline is not evidence; a hidden act is kept, never
deleted, and its old link still leads somewhere; an admin's confirm and reject
survive every re-import; a possible duplicate reaches customers WITH a label.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

import tsg_ingest as tg
import tsg_match as tm
from tests.test_tsg_ingest import (DAY, TODAY, FakeClient, _by_slice, _one,  # noqa: F401
                                   _rec, _tsg_schema, tdb)

AUTH = "99990001"
OURS = "26PROC019900001"
OURS2 = "26PROC019900002"
REQ = "26REQ019900009"
DEADLINE = dt.datetime(2026, 9, 14, 13, 15, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
def _notice(d, adam, title, *, net=None, gross=None, deadline=DEADLINE, source="khmdhs",
            raw=None, authority=AUTH):
    d.execute("""INSERT INTO proc.procurement_act
                   (adam, type, title, origin, data_source, authority_id, final_submission_date,
                    total_cost_without_vat, total_cost_with_vat, submission_date, raw_json, ingested_at)
                 VALUES (%s, 'notice', %s, 'import', %s, %s, %s, %s, %s, %s, %s::jsonb, now())""",
              (adam, title, source, authority, deadline, net, gross, deadline - dt.timedelta(days=5),
               json.dumps(raw or {})))
    d.commit()


def _subscription(c):
    from app import auth as _auth
    from app import digests
    uid = _auth.create_user(c, "tsgcustomer", "goodpassword1", role="customer",
                            email="tsg@example.test")["id"]
    pid = _auth.create_search_profile(c, name="Αντιδραστήρια", scope="customer", owner_id=uid,
                                      params={"q": "αντιδραστήρια"}, based_on_id=None, created_by=uid)
    return digests.upsert_subscription(c, user_id=uid, search_profile_id=pid, layout="deadline")


@pytest.fixture()
def mdb(tdb):  # noqa: F811 — the fixture imported above, used as one
    tdb.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, 'ΓΕΝΙΚΟ ΝΟΣΟΚΟΜΕΙΟ ΔΟΚΙΜΗΣ') "
                "ON CONFLICT DO NOTHING", (AUTH,))
    tdb.commit()
    yield tdb
    tdb.rollback()
    tdb.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
    tdb.execute("DELETE FROM proc.act_link WHERE source_adam = %s", (REQ,))
    # The test hospital goes too (_clean never truncates proc.authority). Every
    # act still naming it — ours, or a projection tdb would only wipe after
    # this — goes first: procurement_act.authority_id has no ON DELETE.
    tdb.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s) OR authority_id = %s",
                ([OURS, OURS2, REQ], AUTH))
    tdb.execute("DELETE FROM proc.authority WHERE org_id = %s", (AUTH,))
    tdb.commit()


def _load(d, *records):
    return tg.backfill(d, FakeClient(_by_slice({("TENDER", None): list(records)})), DAY, DAY,
                       resume=False, today=TODAY)


def _row(d, internal="900000101"):
    return _one(d, """SELECT t.match_outcome, t.match_rule, t.match_tier, t.matched_adam,
                             p.adam IS NOT NULL, p.duplicate_of
                      FROM proc.tsg_record t
                      LEFT JOIN proc.procurement_act p ON p.adam = 'TSG:' || t.internal_id
                      WHERE t.internal_id = %s""", internal)


def _cands(d, adam="TSG:900000101"):
    return d.query("""SELECT candidate_adam, tier, status FROM proc.duplicate_candidate
                      WHERE adam = %s ORDER BY rank""", (adam,))


def test_the_esidis_number_hides_a_promitheus_notice_at_first_sight(mdb):
    _notice(mdb, OURS, "ΠΡΟΜΗΘΕΙΑ ΚΑΥΣΙΜΩΝ", raw={"systemicNumbers": [{"systemicNumber": "521020"}]})
    s = _load(mdb, _rec(externalId="eproc-521020", dataSource="promitheus-gov-gr",
                        authorityIdentifier="6016"))
    assert s["projection"]["held"] == 1
    # Never projected: nothing to hide, nothing to keep.
    assert _row(mdb) == ("hidden", "esidis", None, OURS, False, None)
    assert s["projection"]["matching"]["rules"] == {"esidis": 1}


def test_a_quoted_adam_with_a_distant_deadline_is_only_flagged(mdb):
    # A re-tender quotes the notice that failed: same number, a month earlier.
    _notice(mdb, OURS, "ΠΡΟΜΗΘΕΙΑ ΓΑΝΤΙΩΝ", deadline=DEADLINE - dt.timedelta(days=30))
    s = _load(mdb, _rec(tenderText=f"<p>Επαναπροκήρυξη της {OURS}</p>"))
    assert _row(mdb) == ("flagged", None, 1, OURS, True, None)
    assert [tuple(r) for r in _cands(mdb)] == [(OURS, 1, "pending")]
    assert s["projection"]["matching"]["guards"] == {"exact_far_deadline": 1}
    assert any(w["code"] == "exact_far_deadline" for w in s["projection"]["matching"]["warnings"])


def test_a_quoted_request_that_led_to_one_notice_hides(mdb):
    _notice(mdb, OURS, "Κάτι άλλο εντελώς")
    mdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES (%s, 'request', 'Αίτημα', 'import', 'khmdhs')", (REQ,))
    mdb.execute("INSERT INTO proc.act_link (source_adam, target_adam, relation) "
                "VALUES (%s, %s, 'request_to_notice')", (REQ, OURS))
    mdb.commit()
    _load(mdb, _rec(referenceNumber=REQ, dataSource="promitheus-gov-gr"))
    assert _row(mdb)[:2] == ("hidden", "quoted_req")


def _request_to(mdb, *targets):
    mdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES (%s, 'request', 'Αίτημα', 'import', 'khmdhs')", (REQ,))
    for target in targets:
        mdb.execute("INSERT INTO proc.act_link (source_adam, target_adam, relation) "
                    "VALUES (%s, %s, 'request_to_notice')", (REQ, target))
    mdb.commit()


def test_a_procedure_published_twice_hides_behind_the_nearer_notice(mdb):
    # ΚΗΜΔΗΣ carries a ΠΕΡΙΛΗΨΗ and the full ΔΙΑΚΗΡΥΞΗ of one procedure.
    _notice(mdb, OURS, "ΠΕΡΙΛΗΨΗ ΔΙΑΚΗΡΥΞΗΣ", deadline=DEADLINE + dt.timedelta(days=2))
    _notice(mdb, OURS2, "ΔΙΑΚΗΡΥΞΗ", deadline=DEADLINE)
    _request_to(mdb, OURS, OURS2)
    s = _load(mdb, _rec(referenceNumber=REQ))
    assert _row(mdb)[:4] == ("hidden", "quoted_req", None, OURS2)
    assert s["projection"]["matching"]["guards"] == {"several_notices": 1}


def test_a_request_whose_notices_close_elsewhere_only_flags(mdb):
    # The same request led to a failed notice and its re-tender, both far away.
    _notice(mdb, OURS, "Α", deadline=DEADLINE - dt.timedelta(days=40))
    _notice(mdb, OURS2, "Β", deadline=DEADLINE + dt.timedelta(days=30))
    _request_to(mdb, OURS, OURS2)
    _load(mdb, _rec(referenceNumber=REQ))
    assert _row(mdb)[:3] == ("flagged", None, 1)
    assert {r[0] for r in _cands(mdb)} == {OURS, OURS2}


def test_title_and_budget_with_vat_flag_tier_1_and_stay_visible(mdb):
    _notice(mdb, OURS, "ΔΙΑΦΟΡΑ ΑΝΤΙΔΡΑΣΤΗΡΙΑ ΓΙΑ ΤΟ ΤΜΗΜΑ ΑΙΜΑΤΟΛΟΓΙΚΟ", net=Decimal("12578.00"))
    _load(mdb, _rec(title="Διάφορα αντιδραστήρια για το τμήμα αιματολογικό - αριθμός διαγωνισμού:4916",
                    estimatedPrices="13.332,68 EUR", authorityIdentifier=AUTH, dataSource="isupplies.gr"))
    assert _row(mdb) == ("flagged", None, 1, OURS, True, None)
    (sig,) = mdb.query("SELECT signals FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    assert sig["budget"] == 6 and sig["title"] == 1.0
    from app import main
    where, args = main.build_where({"source": ["tsg"]})
    rows = mdb.query(f"SELECT a.adam FROM proc.procurement_act a WHERE {where}", args)
    assert ("TSG:900000101",) in [tuple(r) for r in rows]


def test_a_hospitals_other_tender_on_the_same_day_is_not_evidence(mdb):
    _notice(mdb, OURS, "ΠΡΟΣΚΛΗΣΗ ΥΠΟΒΟΛΗΣ ΠΡΟΣΦΟΡΑΣ ΓΙΑ ΑΝΑΝΕΩΣΗ ΑΔΕΙΩΝ ΧΡΗΣΗΣ", net=Decimal("15100"))
    _load(mdb, _rec(title="19170 Έρευνα αγοράς για απολυμαντικά", estimatedPrices=None,
                    authorityIdentifier=AUTH))
    assert _row(mdb)[:3] == ("new", None, None)
    assert _cands(mdb) == []


def test_a_round_budget_alone_does_not_flag_among_many_candidates(mdb):
    for i in range(7):
        _notice(mdb, f"26PROC0199001{i:02d}", f"Άσχετος τίτλος αριθμός {i}", gross=Decimal("5000"))
    try:
        _load(mdb, _rec(title="Γάζα τραχειοστομίας", estimatedPrices="5.000,00 EUR", authorityIdentifier=AUTH))
        assert _row(mdb)[0] == "new"
    finally:
        mdb.rollback()
        mdb.execute("DELETE FROM proc.procurement_act WHERE adam LIKE '26PROC0199001%%'")
        mdb.commit()


def test_confirm_hides_keeps_history_and_survives_a_reprojection(mdb):
    _notice(mdb, OURS, "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ ΣΕ ΣΩΛΗΝΑΡΙΟ (1786)", net=Decimal("3000"))
    _load(mdb, _rec(title="Λιπαντικό ζελέ (1786)", estimatedPrices="3.720,00 EUR", authorityIdentifier=AUTH))
    # A reminder already went out for the copy.
    c = mdb.dict_cursor()
    sub = _subscription(c)
    c.execute("INSERT INTO proc.digest_deadline_notice (subscription_id, adam, lead_days, deadline) "
              "VALUES (%s, 'TSG:900000101', 7, %s)", (sub, DEADLINE))
    mdb.commit()
    (cid,) = mdb.query("SELECT id FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    tm.confirm(c, cid, None)
    mdb.commit()
    assert _row(mdb) == ("hidden", "admin", None, OURS, True, OURS)
    # The mark spent on the copy is spent on the act that replaces it.
    assert _one(mdb, "SELECT lead_days FROM proc.digest_deadline_notice WHERE subscription_id = %s AND adam = %s",
                sub, OURS) == (7,)
    from app import main
    where, args = main.build_where({})
    assert not mdb.query(f"SELECT 1 FROM proc.procurement_act a WHERE {where} AND a.adam = 'TSG:900000101'", args)

    tg.project_all(mdb, today=TODAY, reproject=True)
    assert _row(mdb)[:2] == ("hidden", "admin")


def test_reject_is_never_flagged_again_and_unhide_is_final(mdb):
    _notice(mdb, OURS, "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ ΣΕ ΣΩΛΗΝΑΡΙΟ (1786)", net=Decimal("3000"))
    _load(mdb, _rec(title="Λιπαντικό ζελέ (1786)", estimatedPrices="3.720,00 EUR", authorityIdentifier=AUTH))
    c = mdb.dict_cursor()
    (cid,) = mdb.query("SELECT id FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    tm.reject(c, cid, None)
    mdb.commit()
    assert _row(mdb)[0] == "new"
    tg.project_all(mdb, today=TODAY, reproject=True)
    assert _row(mdb)[0] == "new"
    assert [tuple(r) for r in _cands(mdb)] == [(OURS, 1, "rejected")]


def test_unhide_brings_the_act_back_and_rejects_the_pair(mdb):
    _notice(mdb, OURS, "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ ΣΕ ΣΩΛΗΝΑΡΙΟ (1786)", net=Decimal("3000"))
    _load(mdb, _rec(title="Λιπαντικό ζελέ (1786)", estimatedPrices="3.720,00 EUR", authorityIdentifier=AUTH))
    c = mdb.dict_cursor()
    (cid,) = mdb.query("SELECT id FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    tm.confirm(c, cid, None)
    tm.unhide(c, "TSG:900000101", None)
    mdb.commit()
    assert _row(mdb)[4:] == (True, None)
    assert [tuple(r) for r in _cands(mdb)] == [(OURS, 1, "rejected")]


def test_a_twin_that_arrives_later_hides_the_copy_without_deleting_it(mdb):
    _load(mdb, _rec(tenderText=f"<p>ΑΔΑΜ {OURS}</p>"))
    assert _row(mdb)[:2] == ("new", None)
    # A customer was already mailed the copy.
    c = mdb.dict_cursor()
    c.execute("INSERT INTO proc.digest_run (subscription_id, trigger, status) VALUES (%s, 'test', 'sent') "
              "RETURNING id", (_subscription(c),))
    run_id = c.fetchone()["id"]
    mdb.execute("INSERT INTO proc.digest_run_item (run_id, adam, ord, in_email) VALUES (%s, 'TSG:900000101', 0, true)",
                (run_id,))
    mdb.commit()
    _notice(mdb, OURS, "Προμήθεια γαντιών")          # our own ingester catches up
    out = tm.recheck(mdb, "khmdhs-catchup", today=TODAY)
    assert out["transitions"] == {"new→hidden": 1}
    assert _row(mdb) == ("hidden", "quoted_adam", None, OURS, True, OURS)
    assert _one(mdb, "SELECT count(*) FROM proc.digest_run_item WHERE run_id = %s", run_id) == (1,)


def test_a_hidden_act_comes_back_when_its_target_is_deleted(mdb):
    _load(mdb, _rec(tenderText=f"<p>ΑΔΑΜ {OURS}</p>"))
    _notice(mdb, OURS, "Προμήθεια γαντιών")
    tm.recheck(mdb, "khmdhs-catchup", today=TODAY)
    mdb.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (OURS,))
    mdb.commit()
    out = tm.recheck(mdb, "manual", today=TODAY)
    assert out["transitions"] == {"unhidden→new": 1}
    assert any(w["code"] == "unhidden" for w in out["warnings"])
    assert _row(mdb)[:2] == ("new", None) and _row(mdb)[5] is None


def test_a_tender_service_twin_keeps_the_better_source(mdb):
    ada = "ΨΚΕΘ46ΜΤΛΡ-ΑΒΓ"
    isup = _rec(internalID="900000201", dataSource="isupplies.gr", externalId="isup-1",
                tenderText=f"<p>ΑΔΑ: {ada}</p>")
    diav = _rec(internalID="900000202", dataSource="http://opendata.diavgeia.gov.gr",
                externalId=f"{ada}-09-10081243")
    _load(mdb, isup)                       # the worse source arrives first …
    assert _row(mdb, "900000201")[0] == "new"
    _load(mdb, isup, diav)                 # … then the Διαύγεια original
    assert _row(mdb, "900000202")[0] == "new"
    assert _row(mdb, "900000201") == ("hidden", "tsg_twin", None, "TSG:900000202", True, "TSG:900000202")


def test_the_old_link_of_a_hidden_act_redirects_to_the_act_we_show(mdb, client):
    _notice(mdb, OURS, "ΠΡΟΜΗΘΕΙΑ ΚΑΥΣΙΜΩΝ", raw={"systemicNumbers": [{"systemicNumber": "521020"}]})
    _load(mdb, _rec(externalId="eproc-521020", dataSource="promitheus-gov-gr"))       # never projected
    _load(mdb, _rec(internalID="900000301", tenderText=f"ΑΔΑΜ {OURS}"))            # projected, then hidden
    for adam in ("TSG:900000101", "TSG:900000301"):
        r = client.get(f"/act/{adam}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"/act/{OURS}?same_as={adam.replace(':', '%3A')}"
    page = client.get(f"/act/{OURS}?same_as=TSG%3A900000301")
    assert "αντίγραφο του ίδιου διαγωνισμού" in page.text
    # The banner is checked against the database, not taken from the URL.
    page = client.get(f"/act/{OURS}?same_as=TSG%3A999")
    assert "αντίγραφο του ίδιου διαγωνισμού" not in page.text


def test_alerts_label_a_possible_duplicate_in_every_layout(mdb):
    from app import digests
    _notice(mdb, OURS, "ΔΙΑΦΟΡΑ ΑΝΤΙΔΡΑΣΤΗΡΙΑ ΓΙΑ ΤΟ ΤΜΗΜΑ ΑΙΜΑΤΟΛΟΓΙΚΟ", net=Decimal("12578.00"))
    _load(mdb, _rec(title="Διάφορα αντιδραστήρια για το τμήμα αιματολογικό",
                    estimatedPrices="13.332,68 EUR", authorityIdentifier=AUTH))
    c = mdb.dict_cursor()
    c.execute(f"SELECT {digests.DIGEST_COLS} FROM proc.procurement_act a "
              "LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id "
              "WHERE a.adam IN ('TSG:900000101', %s) ORDER BY a.adam DESC", (OURS,))
    rows = [dict(r) for r in c.fetchall()]
    assert digests.annotate_duplicates(c, rows, shown=rows) == 1
    tsg_row = rows[0]
    assert tsg_row["dup"]["candidate_adam"] == OURS and tsg_row["dup"]["same_message"]
    now = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)
    for lang, word in (("el", "Πιθανή διπλοεγγραφή"), ("en", "Possible duplicate")):
        for layout in ("list", "deadline"):
            html, text = digests.render_digest(
                subscription={"lang": lang, "layout": layout}, profile={"name": "p"}, params={},
                rows=rows, total=2, since=now, until=now, intro="", subject="s")
            assert html.count(word) == 1 and text.count(word) == 1
            assert OURS in html
    stats = digests.window_stats(c, {}, now - dt.timedelta(days=3650), dt.datetime.now(dt.timezone.utc))
    assert stats["possible_duplicates"] >= 1
    html, text = digests.render_digest(
        subscription={"lang": "el", "layout": "summary"}, profile={"name": "p"}, params={},
        rows=rows, total=2, since=now, until=now, intro="", subject="s", stats=stats)
    assert "Πιθανές διπλοεγγραφές" in html and "Πιθανές διπλοεγγραφές" in text


def test_the_sent_label_is_frozen_on_the_run(mdb):
    from app import digests
    _notice(mdb, OURS, "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ ΣΕ ΣΩΛΗΝΑΡΙΟ (1786)", net=Decimal("3000"))
    _load(mdb, _rec(title="Λιπαντικό ζελέ (1786)", estimatedPrices="3.720,00 EUR", authorityIdentifier=AUTH))
    c = mdb.dict_cursor()
    rows = [{"adam": "TSG:900000101", "ingested_at": None}]
    digests.annotate_duplicates(c, rows)
    c.execute("INSERT INTO proc.digest_run (subscription_id, trigger, status) VALUES (%s, 'test', 'sent') "
              "RETURNING id", (_subscription(c),))
    run_id = c.fetchone()["id"]
    digests.record_run_items(c, run_id, rows, shown=1)
    (cid,) = mdb.query("SELECT id FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    tm.reject(c, cid, None)                       # decided after the send
    items = digests.run_item_acts(c, run_id)
    assert [(i["adam"], i["dup_candidate_adam"], i["dup_tier"]) for i in items] == [("TSG:900000101", OURS, 1)]
    mdb.rollback()


def test_push_bodies_carry_the_label():
    from app import notification_worker as nw
    assert nw._dup_suffix("el", {"dup": {"candidate_adam": OURS}}) == " · πιθανή διπλοεγγραφή"
    assert nw._dup_suffix("en", {"dup": {"candidate_adam": OURS}}) == " · possible duplicate"
    assert nw._dup_suffix("en", {}) == ""


def test_the_review_page_confirms_and_rejects(mdb, client):
    from tests.helpers import get_csrf, login, make_user
    make_user("tsgadmin", role="admin")
    _notice(mdb, OURS, "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ ΣΕ ΣΩΛΗΝΑΡΙΟ (1786)", net=Decimal("3000"))
    _load(mdb, _rec(title="Λιπαντικό ζελέ (1786)", estimatedPrices="3.720,00 EUR", authorityIdentifier=AUTH))
    (cid,) = mdb.query("SELECT id FROM proc.duplicate_candidate WHERE adam = 'TSG:900000101'")[0]
    login(client, "tsgadmin", "pw-123456")
    page = client.get("/admin/interconnect/tsg")
    assert page.status_code == 200 and "TSG:900000101" in page.text and OURS in page.text
    en = client.get("/admin/interconnect/tsg", cookies={"lang": "en"})
    assert en.status_code == 200
    r = client.post("/admin/interconnect/tsg/confirm", data={"id": cid, "csrf_token": get_csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    assert _row(mdb)[:2] == ("hidden", "admin")
    hidden = client.get("/act/TSG:900000101?hidden=1")
    assert hidden.status_code == 200 and "Κρυφή ως διπλοεγγραφή" in hidden.text


def test_run_warnings(mdb):
    c = mdb.dict_cursor()
    run = tm.Run(c, "manual")
    for i in range(20):                  # promitheus notices, none matched by ΕΣΗΔΗΣ
        run.note(f"p{i}", {"typeOfDocument": tm.NOTICE_LABEL, "externalId": f"eproc-{i:06d}",
                           "authorityIdentifier": "1"}, tm.Decision("new"), "none→new", recheck=False)
    for i in range(20):                  # 20 more notices, half with no authority code
        run.note(f"n{i}", {"typeOfDocument": tm.NOTICE_LABEL,
                           "authorityIdentifier": "1" if i % 2 else ""},
                 tm.Decision("new"), "none→new", recheck=False)
    run.note("p0", {"typeOfDocument": tm.NOTICE_LABEL, "externalId": "eproc-000000",
                    "authorityIdentifier": "1"}, tm.Decision("new"), "new→new", recheck=True)
    assert run.counts()["outcomes"] == {"new": 40}      # a record decided twice counts once
    codes = {w["code"] for w in run.warnings()}
    assert {"rule_went_quiet", "unknown_authority_rate"} <= codes
    mdb.rollback()



# --------------------------------------------------------------------------- #
# the Tender Service part of the migration (the core part: test_duplicate_visibility.py)
# --------------------------------------------------------------------------- #
TSG_MIGRATION = "migrations/20260917130324_tender_service_duplicates_tsg_record.sql"


def test_the_tsg_record_migration_is_paste_safe():
    from tests.test_duplicate_visibility import _code
    code = _code(TSG_MIGRATION)
    assert "$$" not in code and "DO " not in code.upper()


def test_the_tsg_record_migration_can_run_twice(_tsg_schema):  # noqa: F811 — fixture
    from tests.test_duplicate_visibility import _run_twice
    _run_twice(TSG_MIGRATION)
