# -*- coding: utf-8 -*-
"""Competition indicator (app/competition.py, docs/specs/competition-indicator.md).

ARITHMETIC  summarise() on hand-built histograms: exact median, bucket edges,
            the confidence thresholds, and a period that is the central 90%
            of the contracts rather than a min..max one bad date stretches.

COUNTING    which contracts reach the views: 1..100 bids only; not cancelled,
            not TED, not a hidden duplicate; direct awards apart.

SURFACES    the authority panel (merged across an entity group), the notice
            line (like with like, empty below the minimum), /analytics and the
            admin monitor; every one of them closed to a gated reader.

ISOLATION   competition.py reads acts only: no profile, no AI summary.
"""
import datetime as dt
import pathlib
import re

import pytest

from app import competition as cx
from tests.helpers import connect, grant, login, make_user

ORG = "CMPX-AUTH-1"          # the authority under test
TWIN = "CMPX-AUTH-2"         # merged with ORG in one entity group
SMALL = "CMPX-AUTH-3"        # too few contracts to say anything
CPV = "99000000-3"           # a division root of its own: '99'
NOTICE = "CMPX-NOTICE-OPEN"
NOTICE_DIRECT = "CMPX-NOTICE-DIRECT"
NOTICE_SMALL = "CMPX-NOTICE-SMALL"

VIEWS = ("mv_competition_authority", "mv_competition_cpv", "mv_competition_fill")


def _h(*triples):
    """Histogram rows from (bids, 'YYYY-MM', n)."""
    return [{"bids": b, "month": dt.date(int(m[:4]), int(m[5:]), 1), "n": n}
            for b, m, n in triples]


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #
def test_median_is_exact_for_odd_and_even_counts():
    assert cx.summarise(_h((1, "2025-05", 2), (3, "2025-05", 1)))["median"] == 1
    assert cx.summarise(_h((1, "2025-05", 2), (4, "2025-05", 2)))["median"] == 2.5
    assert cx.summarise(_h((2, "2025-05", 1), (9, "2025-05", 1),
                           (100, "2025-05", 1)))["median"] == 9


def test_no_mean_survives_an_outlier():
    s = cx.summarise(_h((1, "2025-05", 10), (100, "2025-05", 1)))
    assert s["median"] == 1 and "mean" not in s


def test_bucket_edges():
    s = cx.summarise(_h(*[(b, "2025-05", 1) for b in (1, 2, 3, 4, 6, 7, 50)]))
    shares = dict(s["buckets"])
    assert [label for label, _ in s["buckets"]] == ["1", "2–3", "4–6", "7+"]
    assert shares["1"] == pytest.approx(1 / 7)
    assert shares["2–3"] == pytest.approx(2 / 7)
    assert shares["4–6"] == pytest.approx(2 / 7)
    assert shares["7+"] == pytest.approx(2 / 7)
    assert s["single_share"] == pytest.approx(1 / 7)


@pytest.mark.parametrize("n, level", [(9, "none"), (10, "limited"),
                                      (29, "limited"), (30, "ok")])
def test_confidence_thresholds(n, level):
    assert cx.summarise(_h((2, "2025-05", n)))["confidence"] == level


def test_nothing_to_summarise_is_none():
    assert cx.summarise([]) is None
    assert cx.summarise(_h((1, "2025-05", 0))) is None


def test_period_is_the_central_ninety_percent_not_min_to_max():
    """One contract dated 2020 and one 2026 must not stretch the period."""
    rows = _h((1, "2020-01", 1), (1, "2025-04", 49), (2, "2026-01", 49),
              (1, "2026-08", 1))
    s = cx.summarise(rows)
    assert (s["first"], s["last"]) == (dt.date(2025, 4, 1), dt.date(2026, 1, 1))
    assert s["trimmed"] is True
    assert cx.period_label(s) == "Απρ 2025 – Ιαν 2026"
    assert cx.period_label(s, "en") == "Apr 2025 – Jan 2026"


def test_a_small_sample_is_not_trimmed_and_says_so():
    s = cx.summarise(_h((1, "2024-01", 1), (1, "2025-06", 11)))
    assert s["trimmed"] is False
    assert (s["first"], s["last"]) == (dt.date(2024, 1, 1), dt.date(2025, 6, 1))


def test_labels():
    assert cx.median_label(2.0) == "2" and cx.median_label(2.5) == "2,5"
    assert cx.median_label(2.5, "en") == "2.5"
    assert cx.pct(0.505) == "50%" or cx.pct(0.505) == "51%"
    assert cx.pct(None) == "—"
    s = cx.summarise(_h((1, "2025-05", 12)))
    assert cx.period_label(s) == "Μαΐ 2025"          # one month, not "X – X"


def test_split_keeps_direct_awards_apart():
    rows = ([{**r, "competitive": True} for r in _h((3, "2025-05", 10))]
            + [{**r, "competitive": False} for r in _h((1, "2025-05", 40))])
    out = cx.split(rows)
    assert out["competitive"]["n"] == 10 and out["competitive"]["median"] == 3
    assert out["direct"]["n"] == 40 and out["direct"]["single_share"] == 1


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def test_competition_reads_acts_only():
    src = pathlib.Path(cx.__file__).read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)          # docstrings may name them
    for forbidden in ("company_profile", "act_ai_summary", "from app import fit",
                      "import fit"):
        assert forbidden not in code, forbidden


def test_the_migration_has_no_do_blocks():
    mig = pathlib.Path("migrations/20261001082952_competition_indicator.sql")
    sql = "\n".join(line for line in mig.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    assert "DO $$" not in sql


# --------------------------------------------------------------------------- #
# Fixtures: real rows, refreshed views
# --------------------------------------------------------------------------- #
def _refresh(cur):
    for v in VIEWS:
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v}")


def _cleanup(cur):
    cur.execute("DELETE FROM proc.entity_member WHERE member_key LIKE 'CMPX-%'")
    cur.execute("DELETE FROM proc.entity_group WHERE canonical_key LIKE 'CMPX-%'")
    cur.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE adam LIKE 'CMPX-%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'CMPX-%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'CMPX-%'")
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code = %s", (CPV,))


def _act(cur, adam, org, bids, *, family="Ανοιχτή διαδικασία", month="2025-06",
         atype="contract", source="khmdhs", cancelled=False):
    cur.execute("""
        INSERT INTO proc.procurement_act
          (adam, type, title, origin, data_source, authority_id, total_cost_with_vat,
           bids_submitted, procedure_family, contract_signed_date, cancelled)
        VALUES (%s, %s::proc.act_type, 'Δοκιμή ανταγωνισμού', 'import', %s, %s, 1000,
                %s, %s, %s, %s)""",
        (adam, atype, source, org, bids, family,
         dt.date(int(month[:4]), int(month[5:]), 15) if atype == "contract" else None,
         cancelled))
    cur.execute("""INSERT INTO proc.act_object_detail (adam) VALUES (%s) RETURNING id""",
                (adam,))
    od = cur.fetchone()["id"]
    cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) VALUES (%s, %s)",
                (od, CPV))


@pytest.fixture()
def acts(db):
    """ORG: 12 open-procedure contracts (6 single-bid, 6 with 3 bids) and 10
    direct awards (all single). TWIN (merged with ORG): 8 more open contracts
    with 5 bids. SMALL: 3 contracts. Plus rows that must NOT count."""
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description, description_en) "
                "VALUES (%s, 'Δοκιμαστικός τομέας', 'Test division')", (CPV,))
    for org, name in ((ORG, "ΔΗΜΟΣ ΑΝΤΑΓΩΝΙΣΜΟΥ"), (TWIN, "ΔΗΜΟΣ ΑΝΤΑΓΩΝΙΣΜΟΥ (2)"),
                      (SMALL, "ΜΙΚΡΟΣ ΦΟΡΕΑΣ")):
        cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, %s)", (org, name))
    for i in range(6):
        _act(cur, f"CMPX-O1-{i}", ORG, 1)
        _act(cur, f"CMPX-O3-{i}", ORG, 3)
    for i in range(10):
        _act(cur, f"CMPX-D-{i}", ORG, 1, family="Απευθείας ανάθεση")
    for i in range(8):
        _act(cur, f"CMPX-T-{i}", TWIN, 5)
    for i in range(3):
        _act(cur, f"CMPX-S-{i}", SMALL, 2)
    # --- must not count ---
    _act(cur, "CMPX-X-ZERO", ORG, 0)
    _act(cur, "CMPX-X-HUGE", ORG, 150)
    _act(cur, "CMPX-X-CANCEL", ORG, 40, cancelled=True)
    _act(cur, "CMPX-X-TED", ORG, 40, source="ted")
    _act(cur, "CMPX-X-HIDDEN", ORG, 40)
    cur.execute("UPDATE proc.procurement_act SET duplicate_of = 'CMPX-O1-0' "
                "WHERE adam = 'CMPX-X-HIDDEN'")
    # --- the notices the act-page line is asked about ---
    _act(cur, NOTICE, ORG, None, atype="notice")
    _act(cur, NOTICE_DIRECT, ORG, None, atype="notice", family="Απευθείας ανάθεση")
    _act(cur, NOTICE_SMALL, SMALL, None, atype="notice")
    _refresh(cur)
    yield
    _cleanup(cur)
    for v in VIEWS:        # back to the test schema's unpopulated state
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v} WITH NO DATA")


@pytest.fixture()
def merged(acts, db):
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.entity_group (kind, canonical_key, display_name)
                   VALUES ('authority', %s, 'ΔΗΜΟΣ ΑΝΤΑΓΩΝΙΣΜΟΥ') RETURNING id""", (ORG,))
    gid = cur.fetchone()["id"]
    for key in (ORG, TWIN):
        cur.execute("INSERT INTO proc.entity_member (group_id, kind, member_key) "
                    "VALUES (%s, 'authority', %s)", (gid, key))


@pytest.fixture()
def reader(client):
    uid = make_user("cmpx_reader", "goodpassword1")
    grant(uid, "pro", days=30)
    login(client, "cmpx_reader", "goodpassword1")
    return client


@pytest.fixture()
def unpaid(client):
    make_user("cmpx_unpaid", "goodpassword1")
    login(client, "cmpx_unpaid", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #
def test_only_valid_eligible_visible_contracts_are_counted(acts):
    with connect() as c:
        figs = cx.for_authority(c.cursor(), [ORG])
    comp, direct = figs["competitive"], figs["direct"]
    assert comp["n"] == 12, "0, 150, cancelled, TED and hidden must all be out"
    assert comp["single_share"] == pytest.approx(0.5)
    assert comp["median"] == 2              # six 1s and six 3s
    assert direct["n"] == 10 and direct["single_share"] == 1


def test_the_cpv_view_counts_each_contract_once_per_division(acts):
    with connect() as c:
        rows = cx.by_division(c.cursor())
    mine = [d for d in rows if d["division"] == "99"]
    assert mine and mine[0]["n"] == 12 + 8 + 3
    assert mine[0]["label"] == "Δοκιμαστικός τομέας"


def test_the_fill_monitor_counts_khmdhs_contracts_and_exclusions(acts):
    with connect() as c:
        mon = cx.fill_monitor(c.cursor())
    june = [r for r in mon["rows"] if r["month"] == dt.date(2025, 6, 1)]
    # every khmdhs contract the fixture made: 12 + 10 + 8 + 3 + zero/huge/
    # cancelled/hidden (TED is not khmdhs). All state a count.
    assert june and june[0]["n_contracts"] == 37 and june[0]["n_filled"] == 37
    assert mon["n_excluded"] >= 2           # the 0 and the 150


# --------------------------------------------------------------------------- #
# Authority panel
# --------------------------------------------------------------------------- #
def test_the_authority_page_mounts_the_panel(reader, acts):
    assert f"/authority/{ORG}/competition" in reader.get(f"/authority/{ORG}").text


def test_the_panel_states_median_share_and_period(reader, acts):
    body = reader.get(f"/authority/{ORG}/competition").text
    assert "Ανταγωνισμός" in body
    assert "50%" in body                                 # single-bid share
    assert "Με βάση 12 συμβάσεις" in body
    assert "Ιουν 2025" in body                           # the measured period
    assert "περιορισμένο δείγμα" in body                 # 12 < MIN_CONFIDENT
    assert "Απευθείας αναθέσεις: 10" in body


def test_the_panel_merges_an_entity_group(reader, merged):
    body = reader.get(f"/authority/{ORG}/competition").text
    assert "Με βάση 20 συμβάσεις" in body                # 12 + TWIN's 8
    assert reader.get(f"/authority/{TWIN}/competition").text.count("Με βάση 20") == 1


def test_a_small_authority_says_there_is_not_enough_data(reader, acts):
    body = reader.get(f"/authority/{SMALL}/competition").text
    assert "Δεν υπάρχουν αρκετά στοιχεία" in body
    assert "Με βάση" not in body


def test_the_panel_is_closed_to_a_gated_reader(unpaid, acts):
    assert unpaid.get(f"/authority/{ORG}/competition").text == ""


def test_the_panel_is_closed_to_anonymous(client, acts):
    assert client.get(f"/authority/{ORG}/competition").text == ""


def test_the_panel_speaks_english(reader, acts):
    reader.cookies.set("lang", "en")
    body = reader.get(f"/authority/{ORG}/competition").text
    assert "Based on 12 contracts" in body and "Jun 2025" in body


# --------------------------------------------------------------------------- #
# Notice line
# --------------------------------------------------------------------------- #
def test_the_notice_line_compares_an_open_notice_with_competitive_contracts(reader, acts):
    body = reader.get(f"/act/{NOTICE}/competition").text
    assert "ανταγωνιστικές διαδικασίες" in body
    assert "50% με μία προσφορά" in body and "12 συμβάσεις" in body


def test_the_notice_line_compares_a_direct_award_with_direct_awards(reader, acts):
    body = reader.get(f"/act/{NOTICE_DIRECT}/competition").text
    assert "απευθείας αναθέσεις" in body
    assert "100% με μία προσφορά" in body and "10 συμβάσεις" in body


def test_the_notice_line_disappears_under_the_minimum(reader, acts):
    assert reader.get(f"/act/{NOTICE_SMALL}/competition").text == ""


def test_the_notice_line_is_only_for_notices(reader, acts):
    assert reader.get("/act/CMPX-O1-0/competition").text == ""
    assert reader.get("/act/CMPX-NO-SUCH/competition").text == ""


def test_the_notice_line_is_closed_to_a_gated_reader(unpaid, acts):
    assert unpaid.get(f"/act/{NOTICE}/competition").text == ""


# --------------------------------------------------------------------------- #
# /analytics and the admin monitor
# --------------------------------------------------------------------------- #
_ANALYTICS_VIEWS = ("mv_analytics_totals", "mv_analytics_authorities",
                    "mv_analytics_contractors", "mv_analytics_monthly",
                    "mv_analytics_cpv")


@pytest.fixture()
def analytics(acts, db):
    """/analytics only renders once its own views are populated. They are put
    back UNPOPULATED afterwards (their state in the test schema), or this
    file's rows would leak into every later test that reads them."""
    cur = db.cursor()
    for v in _ANALYTICS_VIEWS:
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v}")
    yield
    for v in _ANALYTICS_VIEWS:
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v} WITH NO DATA")


def test_analytics_lists_competition_by_division_for_subscribers(reader, analytics):
    body = reader.get("/analytics").text
    assert 'id="analytics-competition"' in body
    assert "Δοκιμαστικός τομέας" in body


def test_analytics_hides_competition_from_a_gated_reader(unpaid, analytics):
    body = unpaid.get("/analytics").text
    assert 'id="analytics-competition"' not in body


def test_the_admin_collection_tab_shows_the_source_monitor(client, acts):
    make_user("cmpx_admin", "goodpassword1", role="admin")
    login(client, "cmpx_admin", "goodpassword1")
    body = client.get("/admin/collection").text
    assert 'id="bid-fill-monitor"' in body
    assert "Εκτός μέτρησης" in body


def test_the_manual_describes_it(client):
    make_user("cmpx_helpadmin", "goodpassword1", role="admin")
    login(client, "cmpx_helpadmin", "goodpassword1")
    body = client.get("/help").text
    assert "Ανταγωνισμός: πόσες προσφορές παίρνει μια αρχή" in body
    assert "Απρίλιο 2025 ως τον Ιανουάριο 2026" in body
