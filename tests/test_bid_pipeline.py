"""Bid pipeline on favourites — docs/specs/bid-pipeline.md, slice 1.

What is checked: who may set a stage and where it lives (only on the user's own
favourite), what a change stamps, the list filter and headline, and — the part
that must not regress — that the award ledger only ever SUGGESTS: detection
writes nothing, and confirming re-detects instead of trusting the form.
Needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from app import bid_pipeline as bp
from tests.helpers import get_csrf, grant, login, make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent

N1 = "BIDP00000001"        # a notice with a published award
N2 = "BIDP00000002"        # a notice with no award yet
AW1 = "BIDP00000011"       # the award of N1
AWX = "BIDP00000012"       # a CANCELLED award of N2 — must be ignored
OUR_VAT, OTHER_VAT = "111222333", "444555666"
DEADLINE = dt.datetime(2026, 12, 14, 12, 0, tzinfo=dt.timezone.utc)


def _cleanup(cur):
    """act_operator references acts and operators without cascading, so it
    goes first; runs on the way in too, after a failed run."""
    cur.execute("""DELETE FROM proc.act_operator WHERE adam LIKE 'BIDP%'""")
    cur.execute("DELETE FROM proc.act_link WHERE source_adam LIKE 'BIDP%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'BIDP%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = ANY(%s)",
                ([OUR_VAT, OTHER_VAT],))


@pytest.fixture()
def ledger(db):
    """N1 → AW1 won by OTHER (and by OUR when `we_won` is set); N2 → only a
    cancelled award."""
    cur = db.cursor()
    _cleanup(cur)
    ops = {}
    for vat, name in ((OUR_VAT, "ΕΜΕΙΣ ΑΕ"), (OTHER_VAT, "ΑΛΛΟΣ ΑΕ")):
        cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                       VALUES (%s, %s, true) RETURNING operator_id""", (vat, name))
        ops[vat] = cur.fetchone()["operator_id"]
    for adam, typ, cancelled in ((N1, "notice", False), (N2, "notice", False),
                                 (AW1, "auction", False), (AWX, "auction", True)):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, cancelled,
                          final_submission_date, submission_date, ingested_at)
                       VALUES (%s, %s, 'Δοκιμή αγωγού', 'import', 'khmdhs', %s,
                               %s, %s, now())""",
                    (adam, typ, cancelled, DEADLINE, DEADLINE - dt.timedelta(days=20)))
    cur.execute("""INSERT INTO proc.act_link (source_adam, target_adam, relation)
                   VALUES (%s, %s, 'notice_to_auction'), (%s, %s, 'notice_to_auction')""",
                (N1, AW1, N2, AWX))
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES (%s, %s, 'winner'), (%s, %s, 'winner')""",
                (AW1, ops[OTHER_VAT], AWX, ops[OUR_VAT]))
    yield cur, ops
    _cleanup(cur)


def _we_also_won(cur, ops):
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES (%s, %s, 'winner')""", (AW1, ops[OUR_VAT]))


def _member(client, db, name="bidcust", *, vat=None, entitled=True):
    uid = make_user(name)
    if entitled:
        grant(uid)
    if vat:
        db.cursor().execute(
            """INSERT INTO proc.customer_profile (user_id, company, vat_number)
               VALUES (%s, 'ΕΜΕΙΣ ΑΕ', %s)
               ON CONFLICT (user_id) DO UPDATE SET vat_number = EXCLUDED.vat_number""",
            (uid, vat))
    login(client, name, "pw-123456")
    return uid


def _h(client):
    return {"X-CSRF-Token": get_csrf(client), "HX-Request": "true"}


def _fav(client, adam):
    assert client.post(f"/account/favorites/{adam}", headers=_h(client)).status_code == 200


def _stage(client, adam, stage, note="", **kw):
    return client.post(f"/account/favorites/{adam}/stage", headers=_h(client),
                       data={"stage": stage, "note": note, **kw})


def _confirm(client, adam, **data):
    return client.post(f"/account/favorites/{adam}/stage/confirm",
                       headers=_h(client), data=data)


def _row(db, uid, adam):
    c = db.cursor()
    c.execute("""SELECT bid_stage, bid_note, bid_stage_at, bid_stage_source,
                        bid_outcome_adam
                   FROM proc.user_favorite_act WHERE user_id=%s AND adam=%s""",
              (uid, adam))
    return c.fetchone()


# --------------------------------------------------------------------------- #
# Setting a stage
# --------------------------------------------------------------------------- #
def test_a_stage_is_written_on_the_favourite_and_the_panel_answers(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    r = _stage(client, N2, "bidding", "ετοιμάζουμε φάκελο")
    assert r.status_code == 200
    assert 'value="bidding" selected' in r.text
    assert f'id="bid-stage-{N2}"' in r.text
    row = _row(db, uid, N2)
    assert row["bid_stage"] == "bidding"
    assert row["bid_note"] == "ετοιμάζουμε φάκελο"
    assert row["bid_stage_source"] == "user"
    assert row["bid_stage_at"] is not None


def test_a_stage_needs_the_star(ledger, client, db):
    _member(client, db)
    assert _stage(client, N2, "bidding").status_code == 404


def test_an_unknown_stage_is_refused(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    assert _stage(client, N2, "maybe").status_code == 400
    assert _row(db, uid, N2)["bid_stage"] is None


def test_a_stage_write_needs_the_csrf_header(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    r = client.post(f"/account/favorites/{N2}/stage", data={"stage": "bidding"})
    assert r.status_code == 403
    assert _row(db, uid, N2)["bid_stage"] is None


def test_nobody_can_stage_someone_elses_favourite(ledger, client, db):
    owner = _member(client, db, "bidowner")
    _fav(client, N2)
    client.cookies.clear()
    _member(client, db, "bidother")
    assert _stage(client, N2, "won").status_code == 404
    assert _row(db, owner, N2)["bid_stage"] is None


def test_clearing_the_stage_clears_its_source(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    _stage(client, N2, "bidding")
    _stage(client, N2, "")
    row = _row(db, uid, N2)
    assert row["bid_stage"] is None and row["bid_stage_source"] is None


def test_the_note_is_capped(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    _stage(client, N2, "no_bid", "α" * 500)
    assert len(_row(db, uid, N2)["bid_note"]) == bp.NOTE_MAX


def test_editing_only_the_note_keeps_a_ledger_confirmation(ledger, client, db):
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    _stage(client, N1, "submitted")
    assert _confirm(client, N1).status_code == 200
    before = _row(db, uid, N1)
    _stage(client, N1, "won", "υπογραφή σύμβασης τον Νοέμβριο")
    after = _row(db, uid, N1)
    assert after["bid_stage_source"] == "ledger"
    assert after["bid_outcome_adam"] == AW1
    assert after["bid_stage_at"] == before["bid_stage_at"]


def test_overruling_the_ledger_by_hand_drops_the_award(ledger, client, db):
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    _stage(client, N1, "submitted")
    _confirm(client, N1)
    _stage(client, N1, "lost")
    row = _row(db, uid, N1)
    assert row["bid_stage_source"] == "user" and row["bid_outcome_adam"] is None


def test_removing_the_star_forgets_the_stage(ledger, client, db):
    uid = _member(client, db)
    _fav(client, N2)
    _stage(client, N2, "bidding")
    client.delete(f"/account/favorites/{N2}", headers=_h(client))
    _fav(client, N2)
    assert _row(db, uid, N2)["bid_stage"] is None


# --------------------------------------------------------------------------- #
# The list
# --------------------------------------------------------------------------- #
def test_the_list_filters_by_stage_and_counts_the_whole_list(ledger, client, db):
    _member(client, db)
    _fav(client, N1)
    _fav(client, N2)
    _stage(client, N2, "bidding")
    r = client.get("/account/favorites?stage=bidding")
    assert N2 in r.text and N1 not in r.text
    assert 'href="/account/favorites?stage=bidding" aria-current="page"' in r.text
    r = client.get("/account/favorites?stage=none")
    assert N1 in r.text and N2 not in r.text
    # An unknown filter shows everything rather than erroring.
    r = client.get("/account/favorites?stage=bogus")
    assert r.status_code == 200 and N1 in r.text and N2 in r.text


def test_the_headline_counts_decided_tenders_for_the_win_rate(ledger, client, db):
    _member(client, db)
    _fav(client, N1)
    _fav(client, N2)
    _stage(client, N1, "won")
    _stage(client, N2, "lost")
    r = client.get("/account/favorites")
    assert "50%" in r.text


def test_summary_arithmetic():
    s = bp.summary({"submitted": 2, "won": 1, "lost": 3, "no_bid": 4, None: 9})
    assert s == {"submitted": 6, "won": 1, "lost": 3, "win_rate": 25}
    assert bp.summary({"bidding": 1})["win_rate"] is None


# --------------------------------------------------------------------------- #
# The ledger suggests; it never writes
# --------------------------------------------------------------------------- #
def test_a_detected_win_is_shown_but_not_written(ledger, client, db):
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    _stage(client, N1, "submitted")
    r = client.get("/account/favorites")
    assert "Η ανάθεση σας αναφέρει ως ανάδοχο." in r.text
    assert f"/account/favorites/{N1}/stage/confirm" in r.text
    assert _row(db, uid, N1)["bid_stage"] == "submitted"


def test_confirming_a_win_records_the_award(ledger, client, db):
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    _stage(client, N1, "submitted")
    r = _confirm(client, N1)
    assert r.status_code == 200 and 'value="won" selected' in r.text
    row = _row(db, uid, N1)
    assert (row["bid_stage"], row["bid_stage_source"], row["bid_outcome_adam"]) \
        == ("won", "ledger", AW1)


def test_confirm_takes_nothing_from_the_form(ledger, client, db):
    """The award names only others: whatever the form claims, the ledger
    decides, and it says lost."""
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    _stage(client, N1, "submitted")
    _confirm(client, N1, stage="won", award_adam=AWX)
    row = _row(db, uid, N1)
    assert (row["bid_stage"], row["bid_outcome_adam"]) == ("lost", AW1)


def test_without_a_linked_vat_others_winning_is_not_a_loss(ledger, client, db):
    """We cannot tell 'not them' without knowing who they are: the award is
    shown, but there is nothing to confirm."""
    uid = _member(client, db)                      # no ΑΦΜ
    _fav(client, N1)
    _stage(client, N1, "submitted")
    r = client.get("/account/favorites")
    assert "ΑΛΛΟΣ ΑΕ" in r.text
    assert "/stage/confirm" not in r.text
    r = _confirm(client, N1)
    assert "Δεν βρέθηκε κάτι να επιβεβαιωθεί." in r.text
    assert _row(db, uid, N1)["bid_stage"] == "submitted"


def test_a_cancelled_award_is_not_an_outcome(ledger, client, db):
    uid = _member(client, db, vat=OUR_VAT)
    _fav(client, N2)
    _stage(client, N2, "submitted")
    r = client.get("/account/favorites")
    assert "Η ανάθεση σας αναφέρει ως ανάδοχο." not in r.text
    _confirm(client, N2)
    assert _row(db, uid, N2)["bid_stage"] == "submitted"


@pytest.mark.parametrize("stage", ["", "won", "lost", "no_bid"])
def test_only_open_stages_ask_the_ledger(ledger, client, db, stage):
    cur, ops = ledger
    _we_also_won(cur, ops)
    _member(client, db, vat=OUR_VAT)
    _fav(client, N1)
    if stage:
        _stage(client, N1, stage)
    assert "Η ανάθεση σας αναφέρει ως ανάδοχο." not in client.get("/account/favorites").text


def test_a_lapsed_customer_keeps_stages_but_sees_no_award(ledger, client, db):
    """Winner names are act data; the stage is the customer's own."""
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = _member(client, db, "bidlapsed", vat=OUR_VAT, entitled=False)
    _fav(client, N1)
    assert _stage(client, N1, "submitted").status_code == 200
    assert "ΑΛΛΟΣ ΑΕ" not in client.get("/account/favorites").text
    assert _confirm(client, N1).status_code == 403
    assert _row(db, uid, N1)["bid_stage"] == "submitted"


def test_detection_lists_the_customer_first(ledger, db):
    cur, ops = ledger
    _we_also_won(cur, ops)
    uid = make_user("biddirect")
    cur.execute("""INSERT INTO proc.customer_profile (user_id, company, vat_number)
                   VALUES (%s, 'x', %s)""", (uid, OUR_VAT))
    found = bp.detect_outcomes(cur, uid, [N1, N2])
    assert set(found) == {N1}
    assert found[N1]["kind"] == "won"
    assert found[N1]["winners"][0] == "ΕΜΕΙΣ ΑΕ"
    assert found[N1]["award_adam"] == AW1


# --------------------------------------------------------------------------- #
# Calendar and isolation
# --------------------------------------------------------------------------- #
def test_not_bidding_leaves_the_calendar(ledger, client, db):
    from app import calendar_feed
    uid = _member(client, db)
    _fav(client, N1)
    _fav(client, N2)
    token = calendar_feed.issue(db.cursor(), uid, "el")
    body = client.get(f"/calendar/{token}.ics").text
    assert N1 in body and N2 in body
    _stage(client, N2, "no_bid")
    body = client.get(f"/calendar/{token}.ics").text
    assert N1 in body and N2 not in body


def test_the_pipeline_never_reads_the_shared_ai_summary():
    """fit.py's isolation rule applies here too: customer data on one side,
    the one-per-act summary on the other."""
    from tests.test_fit import _code_strings
    code = _code_strings(str(ROOT / "app" / "bid_pipeline.py"))
    for forbidden in ("act_ai_summary", "ai_summary_job", "ai_summary"):
        assert forbidden not in code, forbidden
