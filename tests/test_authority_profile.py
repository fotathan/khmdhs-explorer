# -*- coding: utf-8 -*-
"""The public authority profile (app/authority_profile.py,
docs/specs/public-detail-pages.md slice 2).

ARITHMETIC  summarise() on hand-built rows: sums, shares of the KNOWN total,
            undeclared fields kept apart, the TOP + "other" fold, bands.

WORDS       the sentence says only what the figures say: singular/plural, no
            value when nothing is valued, a CPV named only above its share,
            "all" only when it is all (of ALL contracts, not only those that
            state a region), an empty year said as such. Money and percentages
            never round a non-zero to 0% or a part to 100%.

COUNTING    which acts reach the views: notices + contracts published in the
            12 months, not cancelled, not hidden, analytics-allowlisted;
            value = eligible contracts only. An entity group adds up.

SURFACES    the gated page (the crawler's) carries the sentence, the figures,
            the breakdowns and the open tenders, its meta description is the
            sentence, and contact VALUES never reach its HTML. A subscriber
            gets the same section plus everything they had.

ISOLATION   no profile, no customer data, no AI summary.
"""
import datetime as dt
import pathlib
import re

import pytest

from app import authority_profile as ap
from tests.helpers import grant, login, make_user

ORG = "APRF-AUTH-1"
TWIN = "APRF-AUTH-2"
QUIET = "APRF-AUTH-3"          # only an act from two years ago
CPV_A = "98000000-3"
CPV_B = "97000000-0"
SECRET_EMAIL = "secret-desk@aprf.example"
SECRET_PHONE = "2109998877"
OPEN_TITLE = "Ανοιχτός διαγωνισμός προφίλ"
NUTS = ("EL301", "EL522")     # place-of-performance codes the acts carry

GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")


def _row(act_type="contract", ct="13", pf="Απευθείας ανάθεση", nuts2="EL30",
         band=0, n=1, n_valued=1, value=5000.0):
    return {"act_type": act_type, "contract_type": ct, "procedure_family": pf,
            "nuts2": nuts2, "value_band": band, "n": n, "n_valued": n_valued,
            "value": value, "period_start": dt.date(2025, 10, 9),
            "period_end": dt.date(2026, 10, 9)}


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #
def test_summarise_adds_rows_and_keeps_unknown_apart():
    p = ap.summarise([_row(n=3, n_valued=3, value=15000),
                      _row(ct="10", nuts2="", band=3, n=1, value=200000),
                      _row(ct="", pf="", nuts2="", band=-1, n=2, n_valued=0, value=0),
                      _row(act_type="notice", n=4, n_valued=0, value=0)], [], {})
    assert (p["notices"], p["contracts"], p["valued"], p["value"]) == (4, 6, 4, 215000)
    ct = p["contract_type"]
    assert ct["unknown"] == 2 and ct["known"] == 4
    assert [(r["key"], r["n"]) for r in ct["rows"]] == [("13", 3), ("10", 1)]
    assert ct["rows"][0]["share"] == pytest.approx(0.75)      # of the KNOWN four
    assert p["region"]["unknown"] == 3
    assert [b["n"] for b in p["bands"]] == [3, 0, 0, 1, 0, 0]
    assert p["band_total"] == 4
    assert p["period"] == (dt.date(2025, 10, 9), dt.date(2026, 10, 9))


def test_breakdowns_fold_after_the_top_rows():
    rows = [_row(pf=f"Διαδικασία {i}", n=10 - i) for i in range(7)]
    proc_ = ap.summarise(rows, [], {})["procedure"]
    assert len(proc_["rows"]) == ap.TOP
    assert proc_["other"] == (10 - 5) + (10 - 6)


def test_cpv_shares_are_of_contracts_and_ranked():
    p = ap.summarise([_row(n=10)],
                     [{"division": "98", "n": 8, "value": 1}, {"division": "97", "n": 2, "value": 1}],
                     {"98": "Α", "97": "Β"})
    assert [(c["division"], c["share"]) for c in p["cpv"]] == [("98", 0.8), ("97", 0.2)]


def test_nothing_in_the_window_is_empty_not_none():
    p = ap.summarise([], [], {})
    assert p["empty"] and p["contracts"] == 0


# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("v, el, en", [
    (933_520_000, "934 εκ. €", "€934M"),
    (65_300_000, "65,3 εκ. €", "€65.3M"),
    (1_200_000_000, "1,2 δισ. €", "€1.2B"),
    (12_000, "12 χιλ. €", "€12K"),
    (850, "850 €", "€850"),
])
def test_money_is_compact_in_both_languages(v, el, en):
    assert ap.fmt_money(v, "el") == el and ap.fmt_money(v, "en") == en


def test_a_share_never_rounds_to_nothing_or_everything():
    assert ap.fmt_pct(0.001) == "<1%"
    assert ap.fmt_pct(0.998) == ">99%"
    assert ap.fmt_pct(1) == "100%" and ap.fmt_pct(0) == "0%"
    assert ap.fmt_pct(0.5) == "50%"


def _profile(contracts=10, notices=3, region=None, cpv=None, valued=True):
    rows = [_row(act_type="notice", n=notices, n_valued=0, value=0)] if notices else []
    for nuts2, n in (region or {"EL30": contracts}).items():
        rows.append(_row(nuts2=nuts2, n=n, n_valued=n if valued else 0,
                         value=5000.0 * n if valued else 0))
    return ap.summarise(rows, cpv or [], {r["division"]: f"Κατηγορία {r['division']}"
                                          for r in (cpv or [])})


def test_the_sentence_counts_and_values():
    s = ap.sentence(_profile(), "ΔΗΜΟΣ Χ")
    assert "«ΔΗΜΟΣ Χ»" in s and "3 προκηρύξεις και 10 συμβάσεις συνολικής αξίας 50 χιλ. €" in s
    assert "9/10/2025 – 9/10/2026" in s


def test_the_sentence_uses_the_singular():
    s = ap.sentence(_profile(contracts=1, notices=1), "Χ")
    assert "1 προκήρυξη και 1 σύμβαση" in s
    s = ap.sentence(_profile(contracts=1, notices=1), "X", "en")
    assert "1 contract notice and 1 contract" in s


def test_no_value_is_stated_when_none_is_valued():
    s = ap.sentence(_profile(valued=False), "Χ")
    assert "αξίας" not in s and "10 συμβάσεις." in s


def test_a_cpv_is_named_only_above_its_share():
    cpv = [{"division": "98", "n": 9, "value": 0}, {"division": "97", "n": 1, "value": 0}]
    s = ap.sentence(_profile(cpv=cpv), "Χ")
    assert "«Κατηγορία 98»" in s and "Κατηγορία 97" not in s     # 10% is not "mostly"; 20% is
    cpv[1]["n"] = 2
    s = ap.sentence(_profile(cpv=cpv), "Χ")
    assert "«Κατηγορία 98» και «Κατηγορία 97»" in s


@pytest.mark.parametrize("region, expect", [
    ({"EL30": 10}, "Όλες εκτελούνται στην Αττική."),
    ({"EL30": 9, "": 1}, "Σχεδόν όλες (90%) εκτελούνται στην Αττική."),
    ({"EL54": 6, "EL30": 4}, "Οι περισσότερες (60%) εκτελούνται στην Ήπειρο."),
    ({"EL65": 4, "EL30": 3, "EL43": 3}, "Εκτελούνται σε περισσότερες περιφέρειες, συχνότερα στην Πελοπόννησο (40%)."),
])
def test_all_means_all(region, expect):
    """The share is of ALL contracts: one with no stated region stops 'all'."""
    assert expect in ap.sentence(_profile(region=region), "Χ")


def test_an_empty_year_is_said_plainly():
    s = ap.sentence(ap.summarise([], [], {}), "Χ")
    assert s == "Η αναθέτουσα αρχή «Χ» δεν δημοσίευσε προκηρύξεις ή συμβάσεις τους τελευταίους 12 μήνες."
    assert ap.sentence(None, "Χ") is None


def test_the_english_sentence_is_english_outside_the_quotes():
    cpv = [{"division": "98", "n": 9, "value": 0}]
    s = ap.sentence(_profile(region={"EL52": 10}, cpv=cpv), "ΔΗΜΟΣ Χ", "en")
    assert not GREEK.search(re.sub(r"«[^»]*»", "", s)), s
    assert "All of them are performed in Central Macedonia." in s


def test_regions_cover_exactly_the_filter_regions():
    from app.main import NUTS_REGIONS
    assert set(ap.REGIONS) == {r["code"] for r in NUTS_REGIONS}


# --------------------------------------------------------------------------- #
# Isolation + migration hygiene
# --------------------------------------------------------------------------- #
def test_the_profile_reads_acts_only():
    src = pathlib.Path(ap.__file__).read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    for forbidden in ("company_profile", "customer_profile", "act_ai_summary",
                      "app_user", "import fit"):
        assert forbidden not in code, forbidden


def test_the_migration_has_no_do_blocks():
    mig = pathlib.Path("migrations/20261009120000_authority_profile.sql")
    sql = "\n".join(line for line in mig.read_text(encoding="utf-8").splitlines()
                    if not line.lstrip().startswith("--"))
    assert "DO $$" not in sql


# --------------------------------------------------------------------------- #
# Fixtures: real rows, refreshed views
# --------------------------------------------------------------------------- #
def _refresh(cur):
    for v in ap.VIEWS:
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v}")


def _cleanup(cur):
    cur.execute("DELETE FROM proc.entity_member WHERE member_key LIKE 'APRF-%'")
    cur.execute("DELETE FROM proc.entity_group WHERE canonical_key LIKE 'APRF-%'")
    cur.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE adam LIKE 'APRF-%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'APRF-%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'APRF-%'")
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code IN (%s, %s)", (CPV_A, CPV_B))
    cur.execute("DELETE FROM proc.nuts_code WHERE nuts_code = ANY(%s)", (list(NUTS),))


def _act(cur, adam, org, atype="contract", *, ct="13", pf="Απευθείας ανάθεση",
         nuts="EL301", value=5000, cpv=CPV_A, source="khmdhs", cancelled=False,
         days_ago=30, deadline_days=None, title="Πράξη προφίλ"):
    cur.execute("""
        INSERT INTO proc.procurement_act
          (adam, type, title, origin, data_source, authority_id, total_cost_with_vat,
           contract_type_code, procedure_family, nuts_code, cancelled,
           submission_date, final_submission_date)
        VALUES (%s, %s::proc.act_type, %s, 'import', %s, %s, %s, %s, %s, %s, %s,
                now() - make_interval(days => %s),
                CASE WHEN %s::int IS NULL THEN NULL
                     ELSE now() + make_interval(days => %s::int) END)""",
        (adam, atype, title, source, org, value, ct, pf, nuts, cancelled,
         days_ago, deadline_days, deadline_days))
    if cpv:
        cur.execute("INSERT INTO proc.act_object_detail (adam) VALUES (%s) RETURNING id",
                    (adam,))
        od = cur.fetchone()["id"]
        cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) "
                    "VALUES (%s, %s)", (od, cpv))


@pytest.fixture()
def acts(db):
    """ORG, last 12 months: 3 notices (one still open), 10 contracts —
    6 small direct-award supplies in Attica, 2 open-procedure works in Central
    Macedonia, 1 with nothing declared, 1 over the value ceiling — plus rows
    that must NOT count. TWIN: 1 contract. QUIET: one contract, two years ago."""
    cur = db.cursor()
    _cleanup(cur)
    for code in NUTS:
        cur.execute("INSERT INTO proc.nuts_code (nuts_code, label) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (code, f"Δοκιμή {code}"))
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description, description_en) VALUES "
                "(%s, 'Δοκιμαστική κατηγορία Α', 'Test category A'), "
                "(%s, 'Δοκιμαστική κατηγορία Β', 'Test category B')", (CPV_A, CPV_B))
    cur.execute("""INSERT INTO proc.authority (org_id, name, contact_email, contact_phone)
                   VALUES (%s, 'ΔΗΜΟΣ ΠΡΟΦΙΛ', %s, %s), (%s, 'ΔΗΜΟΣ ΠΡΟΦΙΛ (2)', NULL, NULL),
                          (%s, 'ΗΣΥΧΟΣ ΦΟΡΕΑΣ', NULL, NULL)""",
                (ORG, SECRET_EMAIL, SECRET_PHONE, TWIN, QUIET))
    _act(cur, "APRF-N-1", ORG, "notice", title=OPEN_TITLE, deadline_days=10)
    _act(cur, "APRF-N-2", ORG, "notice", deadline_days=-5, title="Έκλεισε προφίλ")
    _act(cur, "APRF-N-3", ORG, "notice")
    for i in range(6):
        _act(cur, f"APRF-C-S{i}", ORG)
    for i in range(2):
        _act(cur, f"APRF-C-W{i}", ORG, ct="10", pf="Ανοιχτή διαδικασία",
             nuts="EL522", value=200000, cpv=CPV_B)
    _act(cur, "APRF-C-UNK", ORG, ct=None, pf=None, nuts=None, value=20000)
    _act(cur, "APRF-C-HUGE", ORG, value=10 ** 13)
    _act(cur, "APRF-T-1", TWIN)
    _act(cur, "APRF-Q-OLD", QUIET, days_ago=730)
    # --- must not count ---
    _act(cur, "APRF-X-CANCEL", ORG, cancelled=True)
    _act(cur, "APRF-X-TED", ORG, source="ted")
    _act(cur, "APRF-X-DIAV", ORG, source="diavgeia")
    _act(cur, "APRF-X-OLD", ORG, days_ago=400)
    _act(cur, "APRF-X-PAY", ORG, "payment")
    _act(cur, "APRF-X-HIDDEN", ORG, "notice", deadline_days=10, title="Κρυφή διπλοεγγραφή")
    cur.execute("UPDATE proc.procurement_act SET duplicate_of = 'APRF-N-1' "
                "WHERE adam = 'APRF-X-HIDDEN'")
    _refresh(cur)
    yield
    _cleanup(cur)
    for v in ap.VIEWS:        # back to the test schema's unpopulated state
        cur.execute(f"REFRESH MATERIALIZED VIEW proc.{v} WITH NO DATA")


@pytest.fixture()
def merged(db, acts):
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.entity_group (kind, canonical_key, display_name)
                   VALUES ('authority', %s, 'ΔΗΜΟΣ ΠΡΟΦΙΛ') RETURNING id""", (ORG,))
    gid = cur.fetchone()["id"]
    for key in (ORG, TWIN):
        cur.execute("INSERT INTO proc.entity_member (group_id, kind, member_key) "
                    "VALUES (%s, 'authority', %s)", (gid, key))


@pytest.fixture()
def reader(client):
    uid = make_user("aprf_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "aprf_cust", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #
def test_which_acts_count(db, acts):
    p = ap.load(db.cursor(), [ORG])
    assert p["notices"] == 3
    assert p["contracts"] == 10
    # 6 × 5k + 2 × 200k + 20k; the one over the ceiling counts, but has no value
    assert p["valued"] == 9 and p["value"] == pytest.approx(450_000)
    assert p["contract_type"]["unknown"] == 1
    assert {r["key"]: r["n"] for r in p["region"]["rows"]} == {"EL30": 7, "EL52": 2}
    assert [(c["division"], c["n"]) for c in p["cpv"]] == [("98", 8), ("97", 2)]
    assert p["cpv"][0]["label"] == "Δοκιμαστική κατηγορία Α"


def test_an_entity_group_adds_up(db, merged):
    p = ap.load(db.cursor(), [ORG, TWIN])
    assert p["contracts"] == 11 and p["value"] == pytest.approx(455_000)


def test_open_tenders_are_open_visible_notices(db, acts):
    rows = ap.open_tenders(db.cursor(), [ORG])
    assert [r["adam"] for r in rows] == ["APRF-N-1"]


def test_a_quiet_authority_says_so(db, acts):
    p = ap.load(db.cursor(), [QUIET])
    assert p["empty"] and p["period"]
    assert "δεν δημοσίευσε" in ap.sentence(p, "ΗΣΥΧΟΣ ΦΟΡΕΑΣ")


def test_no_profile_until_the_views_are_populated(db):
    assert ap.load(db.cursor(), [ORG]) is None      # test schema: WITH NO DATA


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def test_the_gated_page_carries_the_profile(client, acts):
    body = client.get(f"/authority/{ORG}").text
    assert "reg-cta" in body
    assert "η αναθέτουσα αρχή «ΔΗΜΟΣ ΠΡΟΦΙΛ» δημοσίευσε 3 προκηρύξεις και 10 συμβάσεις" in body
    assert "Τι αγοράζει" in body and "Είδος σύμβασης" in body and "Μέγεθος συμβάσεων" in body
    assert OPEN_TITLE in body and "Κρυφή διπλοεγγραφή" not in body
    # still the teaser: no act list, no suppliers, no all-years block
    assert "Όλο το ιστορικό" not in body
    assert "/top-contractors" not in body


def test_contact_values_never_reach_the_gated_html(client, reader, acts):
    assert SECRET_EMAIL in reader.get(f"/authority/{ORG}").text, "subscriber lost the email"
    client.cookies.clear()
    body = client.get(f"/authority/{ORG}").text
    assert SECRET_EMAIL not in body and SECRET_PHONE not in body
    assert 'class="ap-blur"' in body and "Ορατό με εγγραφή" in body


def test_the_meta_description_is_the_sentence(client, acts):
    body = client.get(f"/authority/{ORG}").text
    meta = re.search(r'<meta name="description" content="([^"]*)"', body).group(1)
    assert meta.startswith("Τους τελευταίους 12 μήνες")


def test_a_subscriber_gets_the_profile_and_everything_else(reader, acts):
    body = reader.get(f"/authority/{ORG}").text
    assert "Τι αγοράζει" in body and "Όλο το ιστορικό" in body
    assert "/top-contractors" in body


def test_the_page_follows_the_language(client, acts):
    client.cookies.set("lang", "en")
    body = client.get(f"/authority/{ORG}").text
    assert "the contracting authority «ΔΗΜΟΣ ΠΡΟΦΙΛ» published 3 contract notices" in body
    assert "What it buys" in body and "Test category A" in body


def test_a_page_without_the_views_still_renders(client, db):
    cur = db.cursor()
    cur.execute("INSERT INTO proc.authority (org_id, name) VALUES (%s, 'ΧΩΡΙΣ ΠΡΟΦΙΛ') "
                "ON CONFLICT DO NOTHING", (QUIET,))
    try:
        r = client.get(f"/authority/{QUIET}")
        assert r.status_code == 200 and 'class="ap"' not in r.text
    finally:
        cur.execute("DELETE FROM proc.authority WHERE org_id = %s", (QUIET,))


# --------------------------------------------------------------------------- #
# The redesign's extra blocks (spec slice 5)
# --------------------------------------------------------------------------- #
def test_the_faq_answers_from_the_figures(acts, db):
    p = ap.load(db.cursor(), [ORG])
    qa = ap.faq(p, "ΔΗΜΟΣ ΠΡΟΦΙΛ", ORG, label_ct=lambda k: {"13": "Προμήθειες"}.get(k, k))
    assert qa[0]["a"] == "Τους τελευταίους 12 μήνες δημοσίευσε 3 προκηρύξεις."   # < 12: no "per month"
    assert "«Προμήθειες»" in qa[1]["a"] and "«Απευθείας ανάθεση»" in qa[1]["a"]
    assert qa[-1]["href"] == f"/?authority={ORG}"
    many = ap.summarise([{**r, "n": 30} for r in [
        {"act_type": "notice", "contract_type": "", "procedure_family": "", "nuts2": "",
         "value_band": -1, "n": 30, "n_valued": 0, "value": 0,
         "period_start": None, "period_end": None}]], [], {})
    assert "περίπου 3 τον μήνα" in ap.faq(many, "Χ", ORG)[0]["a"]


def test_latest_acts_skip_requests_and_payments(acts, db):
    rows = ap.latest_acts(db.cursor(), [ORG], limit=50)
    types = {r["type"] for r in rows}
    assert "payment" not in types and types <= {"notice", "auction", "contract"}
    assert "APRF-X-HIDDEN" not in {r["adam"] for r in rows}


def test_related_authorities_share_the_main_region(acts, db):
    cur = db.cursor()
    p = ap.load(cur, [ORG])
    rel = ap.related(cur, [ORG], p, "ΔΗΜΟΣ ΠΡΟΦΙΛ")
    assert [r["org_id"] for r in rel] == [TWIN]          # same region, same kind
    assert ap.related(cur, [ORG, TWIN], p, "ΔΗΜΟΣ ΠΡΟΦΙΛ") == []


def test_the_page_carries_the_faq_and_the_hero_figures(client, acts):
    body = client.get(f"/authority/{ORG}").text
    assert "Συχνές ερωτήσεις" in body and "Πόσο συχνά προκηρύσσει" in body
    assert 'class="dv-stats"' in body and "ανοιχτοί διαγωνισμοί τώρα" in body
    assert f'href="/authority/{TWIN}"' in body           # related authorities
