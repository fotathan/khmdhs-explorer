"""Duplicate handling, the part the WEB APP runs (docs/specs/tender-service-duplicates.md).

Independent of the Tender Service ingester, so it runs where the Tender Service
tables do not exist (production, CI without them): a hidden act never reaches a
list, its old link leads to the act we keep, a possible duplicate is labelled in
every alert layout and the label is frozen on the run, and the core migration is
paste-safe and needs nothing Tender Service. The matching rules themselves are
tested in test_tsg_match.py, next to the ingester.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import subprocess
from decimal import Decimal

import pytest

import tsg_match as tm

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE_MIGRATION = "migrations/20260917130323_tender_service_duplicates.sql"
SETTINGS = dict(tm.DEFAULTS)
OURS = "26PROC019910001"
COPY = "TSG:910000001"
DEADLINE = dt.datetime(2026, 9, 14, 13, 15, tzinfo=dt.timezone.utc)


def _code(rel):
    sql = (ROOT / rel).read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).replace("DO NOTHING", "")


def _run_twice(rel):
    for _ in range(2):
        r = subprocess.run(["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1", "-q",
                            "-f", str(ROOT / rel)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- #
# pure
# --------------------------------------------------------------------------- #
def test_an_ada_counts_only_next_to_its_label_and_an_adam_label_is_not_one():
    r = {"title": "Πρόσκληση", "tenderText":
         "<p>ΑΔΑ: ΨΚΕΘ46ΜΤΛΡ-ΑΒΓ</p><p>ΑΔΑΜ: 26PROC019900001</p><p>σκέτο 9ΖΖΖ46ΜΤΛΡ-ΔΕΖ</p>"
         "<p>αίτημα 26REQ019900009</p>"}
    q = tm.quoted_numbers(r)
    assert q == {"adam": ["26PROC019900001"], "req": ["26REQ019900009"], "ada": ["ΨΚΕΘ46ΜΤΛΡ-ΑΒΓ"]}


def test_record_keys_include_the_records_own_ada():
    r = {"externalId": "eproc-521020", "title": "x"}
    assert tm.record_keys(r, "ΨΚΕΘ46ΜΤΛΡ-ΑΒΓ") == ["esidis:521020", "ada:ΨΚΕΘ46ΜΤΛΡ-ΑΒΓ"]
    assert tm.esidis_of({"externalId": "isup-480901"}) is None


@pytest.mark.parametrize("title, nums", [
    ("Παραγγελία τμήματος 26/0006050 αποθήκη", ["6050"]),
    ("(24337-Κεντρικη αποστείρωση) κεφαλή εκτυπωτή - αριθμός διαγωνισμού:39010", ["24337"]),
    ("Πρόσκληση με α.π. 14240 για το 2026", ["14240"]),
    ("Προμήθεια 5.948,52 EUR", []),
    ("Χωρίς αριθμούς", []),
])
def test_title_numbers_skip_years_amounts_and_portal_suffixes(title, nums):
    assert tm.title_numbers(title) == nums


def test_portal_boilerplate_is_stripped():
    assert tm.strip_boilerplate("Λιπαντικό ζελέ (1786) - αριθμός διαγωνισμού:14090") == "Λιπαντικό ζελέ (1786)"


def test_similarity_ignores_case_accents_and_final_sigma():
    assert tm.similarity("ΔΙΑΦΟΡΑ ΑΝΤΙΔΡΑΣΤΗΡΙΑ ΓΙΑ ΤΟ ΤΜΗΜΑ",
                         "Διάφορα αντιδραστήρια για το τμήμα") == 1.0
    assert tm.similarity("Γάντια", "Τόνερ εκτυπωτών") < 0.1


def test_budget_matches_with_vat_added_or_on_the_gross_total():
    assert tm.budget_match(Decimal("13332.68"), Decimal("12578.00"), None) == 6
    assert tm.budget_match(Decimal("31000"), Decimal("25000"), None) == 24
    assert tm.budget_match(Decimal("300"), None, Decimal("300.00")) == "gross"
    assert tm.budget_match(Decimal("29890"), Decimal("29000"), None) is None
    assert tm.budget_match(None, Decimal("1"), None) is None


@pytest.mark.parametrize("sig, tier", [
    ({"title": 0.9, "budget": 24}, 1),
    ({"title": 0.9, "ref_no": True}, 1),
    ({"title": 0.1, "budget": 6, "ref_no": True}, 1),
    ({"guard": "exact_far_deadline"}, 1),
    ({"title": 0.6}, 2),
    ({"title": 0.1, "budget": 0}, 3),
    ({"title": 0.1, "ref_no": True}, 3),
    ({"title": 0.4}, None),                                   # same authority + deadline only
    ({"title": 0.1, "budget": 0, "round_budget": True, "n_candidates": 30}, None),
    ({"title": 0.1, "budget": 0, "round_budget": True, "n_candidates": 3}, 3),
])
def test_tiers(sig, tier):
    assert tm.tier_of(sig, SETTINGS) == tier


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #
def test_the_core_migration_is_paste_safe():
    """The Supabase dashboard editor split a DO $$ … $$ block and the whole
    script failed to parse (2026-09-17). No dollar quotes, no DO blocks."""
    code = _code(CORE_MIGRATION)
    assert "$$" not in code and "DO " not in code.upper()


def test_the_core_migration_does_not_need_the_tender_service_tables():
    code = _code(CORE_MIGRATION)
    assert "tsg_record" not in code and "tsg_ingest_window" not in code


def test_the_core_migration_can_run_twice(_schema):
    _run_twice(CORE_MIGRATION)


# --------------------------------------------------------------------------- #
# the web app, without the Tender Service tables
# --------------------------------------------------------------------------- #
@pytest.fixture()
def acts(db, monkeypatch):
    """Our notice, a Tender Service copy of it, and a possible-duplicate pair."""
    monkeypatch.setattr(tm, "tsg_tables", lambda c: False)
    c = db.cursor()
    for adam, source, title, gross in ((OURS, "khmdhs", "ΛΙΠΑΝΤΙΚΟ ΖΕΛΕ (1786)", Decimal("3720")),
                                       (COPY, "tsg", "Λιπαντικό ζελέ (1786)", Decimal("3720"))):
        c.execute("""INSERT INTO proc.procurement_act
                       (adam, type, title, origin, data_source, final_submission_date,
                        submission_date, total_cost_with_vat, ingested_at)
                     VALUES (%s, 'notice', %s, 'import', %s, %s, %s, %s, now())""",
                  (adam, title, source, DEADLINE, DEADLINE - dt.timedelta(days=5), gross))
    c.execute("""INSERT INTO proc.duplicate_candidate (adam, candidate_adam, tier, signals)
                 VALUES (%s, %s, 1, '{"title": 0.9, "budget": 24}')""", (COPY, OURS))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([COPY, OURS],))


def _hide(c):
    c.execute("SELECT id FROM proc.duplicate_candidate WHERE adam = %s", (COPY,))
    tm.confirm(c, c.fetchone()["id"], None)


def test_unfiltered_search_still_uses_the_instant_counter():
    from app import main
    where, args = main.build_where({})
    assert where == main.VISIBLE_SQL and args == []


def test_a_hidden_act_leaves_every_list(acts):
    from app import main
    where, args = main.build_where({})
    acts.execute(f"SELECT a.adam FROM proc.procurement_act a WHERE {where} AND a.adam = ANY(%s)",
                 args + [[COPY, OURS]])
    assert {r["adam"] for r in acts.fetchall()} == {COPY, OURS}
    _hide(acts)
    acts.execute(f"SELECT a.adam FROM proc.procurement_act a WHERE {where} AND a.adam = ANY(%s)",
                 args + [[COPY, OURS]])
    assert [r["adam"] for r in acts.fetchall()] == [OURS]
    acts.execute("SELECT duplicate_of FROM proc.procurement_act WHERE adam = %s", (COPY,))
    assert acts.fetchone()["duplicate_of"] == OURS           # kept, not deleted


def test_the_old_link_redirects_and_the_banner_is_checked(acts, client):
    _hide(acts)
    r = client.get(f"/act/{COPY}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/act/{OURS}?same_as=TSG%3A910000001"
    assert "αντίγραφο του ίδιου διαγωνισμού" in client.get(r.headers["location"]).text
    # The banner is checked against the database, not taken from the URL.
    assert "αντίγραφο του ίδιου διαγωνισμού" not in client.get(f"/act/{OURS}?same_as=TSG%3A999").text
    # An unknown Tender Service id is a plain 404, with no Tender Service tables to ask.
    assert client.get("/act/TSG:123456", follow_redirects=False).status_code == 404


def test_an_admin_can_open_and_unhide_a_hidden_act(acts, client):
    from tests.helpers import get_csrf, login, make_user
    make_user("dupadmin", role="admin")
    _hide(acts)
    login(client, "dupadmin", "pw-123456")
    page = client.get(f"/act/{COPY}?hidden=1")
    assert page.status_code == 200 and "Κρυφή ως διπλοεγγραφή" in page.text
    assert client.get("/admin/interconnect/tsg").status_code == 200     # empty queue, no tables
    r = client.post("/admin/interconnect/tsg/unhide",
                    data={"adam": COPY, "csrf_token": get_csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    acts.execute("SELECT duplicate_of FROM proc.procurement_act WHERE adam = %s", (COPY,))
    assert acts.fetchone()["duplicate_of"] is None
    acts.execute("SELECT status FROM proc.duplicate_candidate WHERE adam = %s", (COPY,))
    assert acts.fetchone()["status"] == "rejected"


def test_every_alert_layout_labels_a_possible_duplicate(acts):
    from app import digests
    acts.execute(f"""SELECT {digests.DIGEST_COLS} FROM proc.procurement_act a
                     LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                     WHERE a.adam = ANY(%s) ORDER BY a.adam DESC""", ([COPY, OURS],))
    rows = [dict(r) for r in acts.fetchall()]
    assert digests.annotate_duplicates(acts, rows, shown=rows) == 1
    assert rows[0]["dup"]["candidate_adam"] == OURS and rows[0]["dup"]["same_message"]
    now = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)
    for lang, word in (("el", "Πιθανή διπλοεγγραφή"), ("en", "Possible duplicate")):
        for layout in ("list", "deadline"):
            html, text = digests.render_digest(
                subscription={"lang": lang, "layout": layout}, profile={"name": "p"}, params={},
                rows=rows, total=2, since=now, until=now, intro="", subject="s")
            assert html.count(word) == 1 and text.count(word) == 1 and f"/act/{OURS}" in html
    stats = digests.window_stats(acts, {}, now - dt.timedelta(days=3650), dt.datetime.now(dt.timezone.utc))
    assert stats["possible_duplicates"] == 1
    html, text = digests.render_digest(
        subscription={"lang": "el", "layout": "summary"}, profile={"name": "p"}, params={},
        rows=rows, total=2, since=now, until=now, intro="", subject="s", stats=stats)
    assert "Πιθανές διπλοεγγραφές" in html and "Πιθανές διπλοεγγραφές" in text


def test_the_sent_label_is_frozen_on_the_run(acts):
    from app import auth as _auth
    from app import digests
    rows = [{"adam": COPY, "ingested_at": None}]
    digests.annotate_duplicates(acts, rows)
    uid = _auth.create_user(acts, "dupcustomer", "goodpassword1", role="customer",
                            email="dup@example.test")["id"]
    pid = _auth.create_search_profile(acts, name="p", scope="customer", owner_id=uid,
                                      params={"q": "ζελέ"}, based_on_id=None, created_by=uid)
    sub = digests.upsert_subscription(acts, user_id=uid, search_profile_id=pid)
    run_id = digests.record_run(acts, subscription_id=sub, trigger="test", status="sent")
    digests.record_run_items(acts, run_id, rows, shown=1)
    acts.execute("SELECT id FROM proc.duplicate_candidate WHERE adam = %s", (COPY,))
    tm.reject(acts, acts.fetchone()["id"], None)          # decided after the send
    items = digests.run_item_acts(acts, run_id)
    assert [(i["adam"], i["dup_candidate_adam"], i["dup_tier"]) for i in items] == [(COPY, OURS, 1)]


def test_confirm_carries_the_spent_reminder_to_the_kept_act(acts):
    from app import auth as _auth
    from app import digests
    uid = _auth.create_user(acts, "dupremind", "goodpassword1", role="customer",
                            email="remind@example.test")["id"]
    pid = _auth.create_search_profile(acts, name="p", scope="customer", owner_id=uid,
                                      params={"q": "ζελέ"}, based_on_id=None, created_by=uid)
    sub = digests.upsert_subscription(acts, user_id=uid, search_profile_id=pid, layout="deadline")
    acts.execute("INSERT INTO proc.digest_deadline_notice (subscription_id, adam, lead_days, deadline) "
                 "VALUES (%s, %s, 7, %s)", (sub, COPY, DEADLINE))
    _hide(acts)
    acts.execute("SELECT lead_days FROM proc.digest_deadline_notice WHERE subscription_id = %s AND adam = %s",
                 (sub, OURS))
    assert [r["lead_days"] for r in acts.fetchall()] == [7]
