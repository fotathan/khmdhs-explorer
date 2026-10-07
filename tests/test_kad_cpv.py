"""ΚΑΔ → CPV estimate and the CRM sales brief (docs/specs/crm-brief-kad.md).

What is worth protecting:

**The estimate reads the ΚΑΔ description.** Filler words ("χονδρικό
εμπόριο") never match; a trader or manufacturer only gets GOODS codes, a
builder WORKS — a wholesaler of surgical instruments does not sell the
"repair of medical equipment" service its words happen to share.

**The learned mapping (measured, not used) stays honest**: counted in firms,
gated by MIN_FIRMS and lift, 8 → 6 → 4 digit fallback. It still feeds the
"same ΚΑΔ" peer list through operator_kad.

**The estimate is labelled and separate.** It never merges into the history
list, it is never read from the customer's own CLAIM (onboarding's
declared_afm), and like fit.py it never touches the shared AI summary cache.

**Region scores, never filters**, and comes from the registered postal code.
"""
from __future__ import annotations

import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

from app import crm_brief, fit, kad_cpv
from tests.helpers import make_user

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Pure units
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("postal,region", [
    ("546 25", "EL52"),      # Thessaloniki
    ("10431", "EL30"),       # Athens
    ("80100", "EL30"),       # Kythira is Attica, not the Peloponnese
    ("49100", "EL62"),       # Corfu
    ("31100", "EL62"),       # Lefkada
    ("37002", "EL61"),       # Skiathos — the Sporades are Thessaly
    ("34007", "EL64"),       # Skyros — Evia, Central Greece
    ("71202", "EL43"),       # Heraklion
    ("85100", "EL42"),       # Rhodes
    ("81100", "EL41"),       # Mytilene
    ("1043", None), ("", None), (None, None), ("99999", None), ("ΤΚ 12", None),
])
def test_postal_code_names_the_region(postal, region):
    assert kad_cpv.nuts2_from_postal(postal) == region


def test_every_postal_region_is_one_of_the_thirteen():
    assert set(kad_cpv.POSTAL_NUTS2.values()) == set(kad_cpv.REGION_LABELS)


def test_region_labels_agree_with_the_search_filter():
    """kad_cpv cannot import main; this is what keeps the two lists one list."""
    from app.main import NUTS_REGIONS
    assert kad_cpv.REGION_LABELS == {r["code"]: r["label"] for r in NUTS_REGIONS}


def test_company_kads_puts_the_primary_first_and_dedupes():
    gemi = {"activities_active": [
        {"id": "47110000", "descr": "λιανικό", "type": "Δευτερεύουσα"},
        {"id": "46.46.02.12", "descr": "χονδρικό", "type": "Κύρια"},
        {"id": "47110000", "descr": "διπλό", "type": "Δευτερεύουσα"},
        {"id": "123", "descr": "σκουπίδι", "type": "Δευτερεύουσα"}],
        "primary_kad": "46460212"}
    kads = kad_cpv.company_kads(gemi)
    assert [(k["kad"], k["primary"]) for k in kads] == [
        ("46460212", True), ("47110000", False)]


def test_company_kads_falls_back_to_the_primary_column():
    kads = kad_cpv.company_kads({"activities_active": [], "primary_kad": "41200000",
                                 "primary_kad_descr": "κτίρια"})
    assert kads == [{"kad": "41200000", "descr": "κτίρια", "primary": True}]
    assert kad_cpv.company_kads(None) == []


def test_headline_counts_only_good_fits():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    rows = [
        {"score": 80, "resolved_value": 1000, "final_submission_date": now + timedelta(days=3)},
        {"score": 50, "total_cost_with_vat": 500, "final_submission_date": now + timedelta(days=30)},
        {"score": 20, "resolved_value": 99999, "final_submission_date": now + timedelta(days=1)},
    ]
    h = crm_brief.headline(rows, now=now)
    assert h == {"n_open": 3, "n_good": 2, "value_good": 1500.0, "n_soon": 1}


def _code_strings(path: str) -> str:
    """Source with comments and docstrings dropped — the rule is about code."""
    import ast
    import io
    import tokenize
    src = (ROOT / path).read_text()
    doc_lines = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                doc_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


@pytest.mark.parametrize("path", ["app/kad_cpv.py", "app/crm_brief.py"])
def test_never_touches_the_shared_summary_cache(path):
    """Same isolation rule as fit.py: customer-shaped code stays away from the
    one-row-per-act summary every reader is served."""
    assert "act_ai_summary" not in _code_strings(path)


def test_the_summary_cannot_read_the_estimate():
    code = _code_strings("app/ai_summary.py")
    for name in ("kad_cpv", "crm_brief", "operator_kad", "gemi_enrichment"):
        assert name not in code, name


def test_never_reads_the_declared_afm():
    """The ΑΦΜ typed at sign-up is a claim; nothing is derived from a claim."""
    assert "declared_afm" not in _code_strings("app/kad_cpv.py")
    assert "onboarding" not in _code_strings("app/kad_cpv.py")


# --------------------------------------------------------------------------- #
# A small market
#   5 medical wholesalers (ΚΑΔ 46460212), each winning surgical gloves 3314
#   5 builders (ΚΑΔ 41200000), each winning building works 4521
#   2 IT firms (ΚΑΔ 62010000), each winning software 7220
#   EVERYONE also wins office supplies 3019 — the code lift must drop
#   one wholesaler also wins catering 5552 — one firm, below MIN_FIRMS
# --------------------------------------------------------------------------- #
MED, BUILD, IT = "46460212", "41200000", "62010000"
DESCR = {MED: "ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ ΙΑΤΡΙΚΩΝ ΑΝΑΛΩΣΙΜΩΝ ΥΛΙΚΩΝ",
         BUILD: "ΚΑΤΑΣΚΕΥΗ ΚΤΙΡΙΩΝ", IT: "ΕΚΔΟΣΗ ΠΑΙΧΝΙΔΙΩΝ ΗΛΕΚΤΡΟΝΙΚΟΥ ΥΠΟΛΟΓΙΣΤΗ",
         "47110000": "ΜΗ ΕΙΔΙΚΕΥΜΕΝΟ ΛΙΑΝΙΚΟ ΕΜΠΟΡΙΟ ΚΥΡΙΩΣ ΤΡΟΦΙΜΩΝ"}
FIRMS = ([(f"30000000{i}", MED, "54625") for i in range(5)]
         + [(f"40000000{i}", BUILD, "10431") for i in range(5)]
         + [(f"50000000{i}", IT, "10431") for i in range(2)])
CUSTOMER_AFM = "699999999"          # a new wholesaler, Thessaloniki, no awards
CODES = {"3314": "33141100-4", "4521": "45210000-2", "7220": "72200000-7",
         "3019": "30192700-8", "5552": "55520000-1"}
ROOTS = {"33140000-3": "Ιατρικά αναλώσιμα",
         "45210000-2": "Κατασκευαστικές εργασίες για κτίρια",
         "50421000-2": "Υπηρεσίες επισκευής και συντήρησης ιατρικού και χειρουργικού εξοπλισμού",
         "33169000-2": "Χειρουργικά όργανα",
         "72200000-7": "Λογισμικό", "30190000-7": "Είδη γραφείου",
         "33141100-4": "Γάντια", "30192700-8": "Γραφική ύλη",
         "55520000-1": "Τροφοδοσία"}


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_operator WHERE adam LIKE 'KADT%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'KADT%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number LIKE ANY(%s)",
                (["30000000_", "40000000_", "50000000_", "EL30000000_"],))
    cur.execute("DELETE FROM proc.gemi_enrichment WHERE afm LIKE ANY(%s)",
                (["30000000_", "40000000_", "50000000_", "6999999__"],))
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE 'KADT%'")
    cur.execute("DELETE FROM proc.kad_cpv_map")
    cur.execute("DELETE FROM proc.operator_kad")
    cur.execute("DELETE FROM proc.kad_cpv_build")


def _gemi(cur, afm, kad, postal, *, secondary=()):
    acts = [{"id": kad, "descr": DESCR.get(kad, f"ΚΑΔ {kad}"), "type": "Κύρια",
             "kadVersion": "kad_2026"}]
    acts += [{"id": k, "descr": DESCR.get(k, f"ΚΑΔ {k}"), "type": "Δευτερεύουσα",
              "kadVersion": "kad_2026"} for k in secondary]
    from psycopg.types.json import Json
    cur.execute("""INSERT INTO proc.gemi_enrichment
                     (afm, legal_name, status, status_id, zip_code, primary_kad,
                      primary_kad_descr, activities_active, fetch_status)
                   VALUES (%s, %s, 'Ενεργή', 3, %s, %s, %s, %s, 'ok')""",
                (afm, f"ΕΤΑΙΡΕΙΑ {afm}", postal, kad, DESCR.get(kad, f"ΚΑΔ {kad}"),
                 Json(acts)))


_n = 0


def _award(cur, op, cpv, *, nuts="EL52", authority="KADTA1", act_type="contract",
           deadline=None):
    global _n
    _n += 1
    adam = f"KADT{_n:05d}"
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id, nuts_code,
                      total_cost_with_vat, signed_date, final_submission_date)
                   VALUES (%s, %s, %s, 'import', 'khmdhs', %s, %s, 10000, %s, %s)""",
                (adam, act_type, f"Δοκιμή {cpv}", authority, nuts,
                 date.today() - timedelta(days=60), deadline))
    if op is not None:
        cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                       VALUES (%s, %s, 'winner')""", (adam, op))
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES (%s, 'είδος') RETURNING id""", (adam,))
    od = cur.fetchone()["id"]
    cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                   VALUES (%s, %s)""", (od, cpv))
    return adam


@pytest.fixture()
def market(db):
    cur = db.cursor()
    _cleanup(cur)
    for code, label in ROOTS.items():
        cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description) VALUES (%s, %s)
                       ON CONFLICT (cpv_code) DO NOTHING""", (code, label))
    for nuts in ("EL52", "EL30"):
        cur.execute("""INSERT INTO proc.nuts_code (nuts_code, label) VALUES (%s, %s)
                       ON CONFLICT (nuts_code) DO NOTHING""", (nuts, nuts))
    for org in ("KADTA1", "KADTA2"):
        cur.execute("""INSERT INTO proc.authority (org_id, name) VALUES (%s, %s)
                       ON CONFLICT (org_id) DO NOTHING""", (org, f"ΑΡΧΗ {org}"))
    ops = {}
    for i, (afm, kad, postal) in enumerate(FIRMS):
        _gemi(cur, afm, kad, postal)
        # the ledger spells some Greek ΑΦΜ with EL — the build must find both
        vat = ("EL" + afm) if i == 0 else afm
        cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                       VALUES (%s, %s, true) RETURNING operator_id""",
                    (vat, f"ΑΝΑΔΟΧΟΣ {afm}"))
        ops[afm] = cur.fetchone()["operator_id"]
        main = {MED: "3314", BUILD: "4521", IT: "7220"}[kad]
        nuts = "EL52" if kad == MED else "EL30"
        _award(cur, ops[afm], CODES[main], nuts=nuts)
        _award(cur, ops[afm], CODES["3019"], nuts=nuts)
    _award(cur, ops["300000001"], CODES["5552"])
    # the customer: a registry company, linked by an admin, never awarded
    uid = make_user("kadcust", "goodpassword1")
    _gemi(cur, CUSTOMER_AFM, MED, "54625", secondary=["47110000"])
    cur.execute("""INSERT INTO proc.customer_profile (user_id, company)
                   VALUES (%s, 'ΝΕΑ ΙΑΤΡΙΚΑ ΑΕ')
                   ON CONFLICT (user_id) DO NOTHING""", (uid,))
    cur.execute("""INSERT INTO proc.customer_company_match (user_id, afm, method)
                   VALUES (%s, %s, 'gemi_name')""", (uid, CUSTOMER_AFM))
    # open notices: one in their line, one far outside it
    future = datetime.now(timezone.utc) + timedelta(days=10)
    gloves = _award(cur, None, CODES["3314"], act_type="notice", deadline=future)
    works = _award(cur, None, CODES["4521"], act_type="notice", deadline=future)
    yield {"cur": cur, "uid": uid, "ops": ops, "gloves": gloves, "works": works,
           "conn": db}
    _cleanup(cur)


def _pairs(cur, kad):
    cur.execute("""SELECT cpv_prefix, n_firms, kad_firms, support FROM proc.kad_cpv_map
                    WHERE kad_prefix = %s""", (kad,))
    return {r["cpv_prefix"]: r for r in cur.fetchall()}


def test_build_learns_what_each_kad_wins(market):
    cur = market["cur"]
    out = kad_cpv.build(market["conn"])
    assert out["n_firms"] == 12 and out["operators"] == 12
    med = _pairs(cur, MED)
    assert med["3314"]["n_firms"] == 5 and med["3314"]["kad_firms"] == 5
    assert med["3314"]["support"] == pytest.approx(1.0)
    assert "33" in med and CODES["3314"] in med       # division and exact code too
    assert "4521" not in med and "4521" in _pairs(cur, BUILD)


def test_a_code_everyone_wins_is_dropped_by_lift(market):
    kad_cpv.build(market["conn"])
    assert "3019" not in _pairs(market["cur"], MED)


def test_a_single_firm_does_not_teach_the_mapping(market):
    kad_cpv.build(market["conn"])
    assert "5552" not in _pairs(market["cur"], MED)


def test_a_kad_below_min_firms_learns_nothing(market):
    """Two IT firms win software; two is not five."""
    kad_cpv.build(market["conn"])
    assert _pairs(market["cur"], IT) == {}


def test_build_records_itself_and_the_slim_peer_table(market):
    cur = market["cur"]
    kad_cpv.build(market["conn"])
    assert kad_cpv.last_build(cur)["n_firms"] == 12
    cur.execute("SELECT kad, nuts2 FROM proc.operator_kad WHERE afm = '300000000'")
    assert cur.fetchone() == {"kad": MED, "nuts2": "EL52"}


def test_a_rare_kad_falls_back_to_its_parent(market):
    kad_cpv.build(market["conn"])
    level, rows = kad_cpv.mapping_for(market["cur"], ["46460299"])["46460299"]
    assert level == "464602" and any(r["cpv_prefix"] == "3314" for r in rows)
    assert kad_cpv.mapping_for(market["cur"], ["99999999"])["99999999"] == (None, [])


def test_estimate_builds_a_labelled_profile_from_the_description(market):
    """No build, no enrichment run: the customer's own ΚΑΔ description is
    enough — that is the point of the default source."""
    cur, uid = market["cur"], market["uid"]
    est = kad_cpv.estimate(cur, uid)
    assert est.usable and est.reason is None
    assert est.region == "EL52" and est.region_label == "Κεντρική Μακεδονία"
    assert est.profile.cpv["3314"] == pytest.approx(1.0)
    assert "33140000-3" in est.profile.cpv and "33" in est.profile.cpv
    assert est.profile.nuts == {"EL52"} and not est.profile.buyers
    assert est.areas[0]["prefix"] == "3314" and est.areas[0]["label"] == "Ιατρικά αναλώσιμα"
    assert est.kads[0]["kad"] == MED and est.kads[0]["matched"]


def test_filler_words_never_match(market):
    assert kad_cpv.describe(market["cur"], MED, "ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ") == {}
    assert kad_cpv.describe(market["cur"], MED, "") == {}


def test_a_trader_gets_goods_not_the_services_its_words_share(market):
    cur = market["cur"]
    descr = "ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ ΧΕΙΡΟΥΡΓΙΚΟΥ ΕΞΟΠΛΙΣΜΟΥ ΚΑΙ ΟΡΓΑΝΩΝ"
    as_trader = kad_cpv.describe(cur, "46460200", descr)
    assert "33169000-2" in as_trader and "50421000-2" not in as_trader
    # the same words under a services ΚΑΔ are unrestricted
    assert "50421000-2" in kad_cpv.describe(cur, "95000000", descr)


def test_a_builder_gets_works(market):
    out = kad_cpv.describe(market["cur"], BUILD, "ΚΑΤΑΣΚΕΥΗ ΚΤΙΡΙΩΝ")
    assert list(out) == ["45210000-2"]


@pytest.mark.parametrize("kad,kind", [
    ("46460212", "goods"), ("47110000", "goods"), ("25992907", "goods"),
    ("41200000", "works"), ("43210000", "works"),
    ("62010000", None), ("86101000", None), ("", None)])
def test_the_nace_section_limits_the_cpv_type(kad, kind):
    out = kad_cpv.allowed_divisions(kad)
    assert out is {"goods": kad_cpv.GOODS_DIVISIONS, "works": kad_cpv.WORKS_DIVISIONS,
                   None: None}[kind]


def test_estimate_ranks_open_tenders_with_the_same_scorer(market):
    cur, uid = market["cur"], market["uid"]
    rows = fit.score_open(cur, kad_cpv.estimate(cur, uid).profile)
    adams = [r["adam"] for r in rows]
    assert market["gloves"] in adams and market["works"] not in adams
    top = rows[0]
    assert {c["key"] for c in top["components"]} == {"cpv", "value", "geo", "buyer"}
    assert top["score"] <= 75          # no buyer history, neutral value


@pytest.mark.parametrize("setup,reason", [
    ("no_afm", "δεν υπάρχει ΑΦΜ"),
    ("no_gemi", "δεν υπάρχουν στοιχεία ΓΕΜΗ"),
    ("no_match", "δεν ταιριάζει"),
])
def test_estimate_says_why_when_it_cannot(market, setup, reason):
    cur, uid = market["cur"], market["uid"]
    if setup == "no_match":
        from psycopg.types.json import Json
        cur.execute("""UPDATE proc.gemi_enrichment SET activities_active = %s,
                              primary_kad_descr = 'ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ'
                        WHERE afm = %s""",
                    (Json([{"id": MED, "descr": "ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ", "type": "Κύρια"}]),
                     CUSTOMER_AFM))
    if setup == "no_afm":
        cur.execute("DELETE FROM proc.customer_company_match WHERE user_id = %s", (uid,))
    if setup == "no_gemi":
        cur.execute("DELETE FROM proc.gemi_enrichment WHERE afm = %s", (CUSTOMER_AFM,))
    est = kad_cpv.estimate(cur, uid)
    assert not est.usable and reason in est.reason


def test_a_declared_afm_alone_gives_no_estimate(market):
    """The customer's own sign-up claim is never a source."""
    cur, uid = market["cur"], market["uid"]
    kad_cpv.build(market["conn"])
    cur.execute("DELETE FROM proc.customer_company_match WHERE user_id = %s", (uid,))
    cur.execute("""INSERT INTO proc.onboarding (user_id, declared_afm) VALUES (%s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET declared_afm = EXCLUDED.declared_afm""",
                (uid, CUSTOMER_AFM))
    est = kad_cpv.estimate(cur, uid)
    assert est.afm is None and not est.usable


def test_peers_share_the_kad_and_never_include_the_customer(market):
    cur, uid = market["cur"], market["uid"]
    kad_cpv.build(market["conn"])
    est = kad_cpv.estimate(cur, uid)
    out = kad_cpv.peers(cur, est)
    afms = {r["afm"] for r in out["rows"]}
    assert out["level"] == MED and afms == {f"30000000{i}" for i in range(5)}
    assert all(r["same_region"] for r in out["rows"])


def test_leaders_win_in_the_estimated_codes(market):
    cur, uid = market["cur"], market["uid"]
    kad_cpv.build(market["conn"])
    out = kad_cpv.leaders(cur, kad_cpv.estimate(cur, uid))
    # The medical firms lead: they win in the estimate's own group (3314).
    # Others may trail on a shared generic code — the test's CPV vocabulary is
    # a handful of rows, so a word like "ύλη" weighs far more here than in the
    # real 9,465-code list.
    top5 = {r["name"] for r in out["rows"][:5]}
    assert top5 == {f"ΑΝΑΔΟΧΟΣ 30000000{i}" for i in range(5)}
    assert all(r["n_region"] == r["n_awards"] for r in out["rows"][:5])
    assert out["n_codes"] >= 2          # the whole 3314 group, not one code


def test_push_replaces_the_target_whole(market):
    from tests.helpers import connect
    cur = market["cur"]
    kad_cpv.build(market["conn"])
    cur.execute("SELECT count(*) AS n FROM proc.kad_cpv_map")
    before = cur.fetchone()["n"]
    with connect() as dst:
        out = kad_cpv.push(market["conn"], dst)
    cur.execute("SELECT count(*) AS n FROM proc.kad_cpv_map")
    assert out["pairs"] == before == cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM proc.kad_cpv_build")
    assert cur.fetchone()["n"] == 2


# --------------------------------------------------------------------------- #
# The brief
# --------------------------------------------------------------------------- #
def test_brief_for_a_firm_with_no_history_is_the_estimate(market):
    cur, uid = market["cur"], market["uid"]
    kad_cpv.build(market["conn"])
    b = crm_brief.build(cur, uid)
    assert b["basis"] == "kad" and b["history"] is None
    assert b["kad"]["headline"]["n_open"] >= 1
    assert b["sections"] == ["peers", "leaders"]
    assert b["company"] == f"ΕΤΑΙΡΕΙΑ {CUSTOMER_AFM}"


def test_brief_for_an_established_contractor_is_history_only(market):
    cur = market["cur"]
    kad_cpv.build(market["conn"])
    afm = "300000002"
    for _ in range(5):                 # 6 awards ≥ MIN_AWARDS_FOR_BAND
        _award(cur, market["ops"][afm], CODES["3314"])
    uid = make_user("kadvet", "goodpassword1")
    cur.execute("""INSERT INTO proc.customer_profile (user_id, company, vat_number)
                   VALUES (%s, 'ΠΑΛΙΟΣ', %s)""", (uid, afm))
    fit.seed_from_ledger(cur, uid)
    b = crm_brief.build(cur, uid)
    assert b["basis"] == "history" and b["kad"] is None
    assert b["sections"] == ["competitors"]


def test_brief_for_a_thin_history_shows_both(market):
    cur = market["cur"]
    kad_cpv.build(market["conn"])
    afm = "300000003"                  # 2 awards — thin
    uid = make_user("kadthin", "goodpassword1")
    cur.execute("""INSERT INTO proc.customer_profile (user_id, company, vat_number)
                   VALUES (%s, 'ΛΙΓΑ', %s)""", (uid, afm))
    fit.seed_from_ledger(cur, uid)
    b = crm_brief.build(cur, uid)
    assert b["basis"] == "history+kad"
    assert b["sections"] == ["competitors", "peers", "leaders"]
    # the thin firm itself is never its own peer
    est = b["est"]
    assert afm not in {r["afm"] for r in kad_cpv.peers(cur, est)["rows"]}


def _admin(client):
    from tests.helpers import get_csrf, login
    make_user("kadadmin", "goodpassword1", role="admin")
    login(client, "kadadmin", "goodpassword1")
    return get_csrf(client)


def test_brief_is_admin_only(client, market):
    uid = market["uid"]
    for path in (f"/admin/crm/{uid}/brief", f"/admin/crm/{uid}/brief/peers"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (303, 403), path


def test_brief_dialog_loads_competitors_lazily(client, market):
    uid = market["uid"]
    kad_cpv.build(market["conn"])
    _admin(client)
    html = client.get(f"/admin/crm/{uid}/brief", headers={"HX-Request": "true"}).text
    assert "<!DOCTYPE" not in html
    assert f'hx-get="/admin/crm/{uid}/brief/peers"' in html
    assert f'hx-get="/admin/crm/{uid}/brief/leaders"' in html
    assert "Εκτίμηση από ΚΑΔ" in html and "Ιατρικά αναλώσιμα" in html
    assert "46.46.02.12" in html
    peers = client.get(f"/admin/crm/{uid}/brief/peers").text
    assert "ΑΝΑΔΟΧΟΣ 300000001" in peers


def test_brief_page_has_everything_inline_for_print(client, market):
    uid = market["uid"]
    kad_cpv.build(market["conn"])
    _admin(client)
    html = client.get(f"/admin/crm/{uid}/brief").text
    assert "<!DOCTYPE html>" in html and "window.print()" in html
    assert "hx-get=" not in html                       # paper loads nothing later
    assert "ΑΝΑΔΟΧΟΣ 300000001" in html


def test_brief_unknown_section_or_customer_is_404(client, market):
    uid = market["uid"]
    _admin(client)
    assert client.get(f"/admin/crm/{uid}/brief/everything").status_code == 404
    assert client.get("/admin/crm/999999/brief").status_code == 404
    assert client.get("/admin/crm/999999/brief/peers").status_code == 404


def test_card_offers_the_brief_and_the_kad_block(client, market):
    uid = market["uid"]
    kad_cpv.build(market["conn"])
    _admin(client)
    html = client.get(f"/admin/crm/{uid}").text
    assert f'href="/admin/crm/{uid}/brief"' in html and 'id="brief-dlg"' in html
    assert 'id="fit-kad"' in html and "Εκτίμηση από ΚΑΔ" in html


def test_card_still_renders_with_nothing_to_estimate(client, db):
    uid = make_user("kadnone", "goodpassword1")
    _admin(client)
    r = client.get(f"/admin/crm/{uid}")
    assert r.status_code == 200 and 'id="brief-dlg"' in r.text
    brief = client.get(f"/admin/crm/{uid}/brief", headers={"HX-Request": "true"})
    assert brief.status_code == 200 and "Δεν υπάρχουν αρκετά στοιχεία" in brief.text


def test_a_list_that_times_out_says_so_instead_of_failing(client, market, monkeypatch):
    """A lazy placeholder left on a 500 spins forever. The pool's statement
    timeout is real on a large supplier; the brief must answer 200 and say it."""
    import psycopg
    uid = market["uid"]
    kad_cpv.build(market["conn"])

    def slow(*_a, **_k):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    monkeypatch.setattr(kad_cpv, "leaders", slow)
    _admin(client)
    r = client.get(f"/admin/crm/{uid}/brief/leaders")
    assert r.status_code == 200 and "δεν ολοκληρώθηκε εγκαίρως" in r.text
    page = client.get(f"/admin/crm/{uid}/brief")
    assert page.status_code == 200 and "δεν ολοκληρώθηκε εγκαίρως" in page.text
