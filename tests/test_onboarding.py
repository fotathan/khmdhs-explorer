"""First-login wizard (/welcome) and the registration question it branches on.

Spec: docs/specs/onboarding-wizard.md. What is worth protecting:

**The claim rule.** An ΑΦΜ the customer typed drives suggestions and nothing
else. It must never reach customer_profile.vat_number / tax_number /
operator_id or build a company_profile — those decide the fit score, the ledger
link and eventually an invoice, and only an admin writes them.

**What gets saved.** The wizard's output is ordinary saved searches. Their
params must be exactly what saving the equivalent URL by hand would store, and
keywords must never be ANDed onto the CPV search.

**Sign-up never waits on a lookup**, and never says whether an ΑΦΜ is taken.
"""
from __future__ import annotations

import re

import pytest

from app import onboarding as ob
from tests.helpers import connect, get_csrf, login, make_user


def _afm(prefix8: str) -> str:
    """A check-digit-valid ΑΦΜ from 8 digits."""
    total = sum(int(d) * (2 ** (8 - i)) for i, d in enumerate(prefix8))
    return prefix8 + str((total % 11) % 10)


FIRM_AFM = _afm("99901234")
OTHER_AFM = _afm("99905678")


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """~/.khmdhs.env puts a real GEMI_API_KEY in a developer's shell. No test
    here may reach the registry; the ones that need it stub enrich_one."""
    monkeypatch.delenv("GEMI_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #
def test_afm_check_digit():
    assert ob.afm_valid(FIRM_AFM) == FIRM_AFM
    assert ob.afm_valid("EL " + FIRM_AFM) == FIRM_AFM
    wrong = FIRM_AFM[:8] + str((int(FIRM_AFM[8]) + 1) % 10)
    assert ob.afm_valid(wrong) is None
    assert ob.afm_valid("000000000") is None
    assert ob.afm_valid("12345") is None
    assert ob.afm_valid("") is None


@pytest.mark.parametrize("raw,on", [
    (None, True), ("", True), ("1", True), ("yes", True), ("whatever", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False),
    ("off", False), ("n", False), ("f", False), ("Disabled", False),
])
def test_the_switch_fails_towards_off(monkeypatch, raw, on):
    if raw is None:
        monkeypatch.delenv("ONBOARDING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ONBOARDING_ENABLED", raw)
    assert ob.enabled() is on


def test_cpv_only_gives_the_subject_search_and_the_awards_search():
    out = ob.build_profiles({"cpv": ["3314"], "nuts": ["EL52"]})
    assert [p["key"] for p in out] == ["notices", "awards"]
    assert out[0]["params"] == {"type": ["notice"], "status": "active",
                                "cpv": ["3314"], "nuts": ["EL52"]}
    assert out[1]["params"] == {"type": ["contract"], "cpv": ["3314"],
                                "nuts": ["EL52"]}


def test_keywords_get_their_own_search_and_never_join_the_cpv_one():
    """CPV ORs within itself but ANDs with the keyword box. A combined search
    would match only acts with a chosen CPV AND a keyword — and miss exactly
    the wrongly-coded tenders keywords are there for."""
    out = ob.build_profiles({"cpv": ["3314"], "cat": ["c:26"],
                             "keywords": ["γάντια νιτριλίου", "μάσκες"],
                             "value_min": 1000, "value_max": 50000.5})
    by = {p["key"]: p["params"] for p in out}
    assert set(by) == {"notices", "keywords", "awards"}
    assert "q" not in by["notices"]
    assert "cpv" not in by["keywords"] and "cat" not in by["keywords"]
    assert by["keywords"]["q"] == '"γάντια νιτριλίου" or "μάσκες"'
    assert by["notices"]["value_min"] == "1000"
    assert by["notices"]["value_max"] == "50000.5"
    # Awards are about the market, not a budget band.
    assert "value_min" not in by["awards"]


def test_awards_search_is_optional():
    out = ob.build_profiles({"cpv": ["3314"], "awards": False})
    assert [p["key"] for p in out] == ["notices"]


def test_every_created_search_round_trips_through_the_query_string():
    """A wizard search must be indistinguishable from one saved by hand, or it
    will not open, edit or alert like one."""
    from app import search_profiles as sp
    answers = {"cpv": ["3314", "33184100"], "cat": ["c:26"], "nuts": ["EL52", "EL30"],
               "keywords": ["γάντια", 'κακό "απόσπασμα" *'], "value_min": 5,
               "value_max": 9}
    for p in ob.build_profiles(answers):
        assert sp.params_from_qs(sp.params_to_qs(p["params"])) == p["params"]


def test_keyword_syntax_is_neutralised():
    """Quotes, a leading minus and a trailing star all mean something to the
    q box. A keyword is a phrase, nothing more."""
    assert ob.clean_keyword('  -"γάντια"*  ') == "γάντια"
    q = ob.keywords_query(['a "b" c', "-εξαίρεση", "πρόθεμα*"])
    assert q == '"a b c" or "εξαίρεση" or "πρόθεμα"'
    assert not q.rstrip('"').endswith("*")


def test_generic_and_short_keywords_are_refused_with_a_reason():
    kept, refused = ob.parse_keywords(
        "γάντια νιτριλίου\nΠρομήθεια\nπαροχή υπηρεσιών\nab\nΓάντια Νιτριλίου\n\n",
        ["μάσκες"])
    assert kept == ["γάντια νιτριλίου", "μάσκες"]          # folded duplicate dropped
    reasons = dict(refused)
    assert "γενική" in reasons["Προμήθεια"]
    assert "γενική" in reasons["παροχή υπηρεσιών"]
    assert "σύντομη" in reasons["ab"]


def test_cpv_chips_stop_at_the_coverage_share_and_the_cap():
    rows = [{"prefix": "3314", "n_acts": 50}, {"prefix": "3318", "n_acts": 35},
            {"prefix": "3319", "n_acts": 10}, {"prefix": "4521", "n_acts": 5}]
    assert ob.pick_cpv(rows, 100) == ["3314", "3318"]            # 85% >= 80%
    many = [{"prefix": f"{3300 + i}", "n_acts": 1} for i in range(40)]
    assert len(ob.pick_cpv(many, 40)) == ob.MAX_CPV_CHIPS


def test_regions_follow_the_share_rule_and_a_national_firm_gets_all_greece():
    valid = {"EL30", "EL52", "EL43", "EL51", "EL53", "EL54", "EL61"}
    rows = [{"prefix": "EL52", "n_acts": 80}, {"prefix": "EL30", "n_acts": 15},
            {"prefix": "EL43", "n_acts": 5}, {"prefix": "XX99", "n_acts": 500}]
    assert ob.pick_regions(rows, valid) == ["EL52", "EL30"]
    national = [{"prefix": c, "n_acts": 10} for c in sorted(valid)[:6]]
    assert ob.pick_regions(national, valid) == []


def test_keyword_suggestions_skip_boilerplate_and_one_off_wording():
    titles = (["Προμήθεια γαντιών νιτριλίου για το νοσοκομείο"] * 4
              + ["Προμήθεια χειρουργικών μασκών"] * 3
              + ["Προμήθεια σπάνιου είδους"])
    out = [ob.fold(s) for s in ob.suggest_keywords(titles)]
    assert "γαντιων νιτριλιου" in out
    # The spelling a title used, accents and all — not a lowercased capital.
    assert "γαντιών νιτριλίου" in ob.suggest_keywords(titles)
    assert ob.suggest_keywords(["ΠΡΟΜΗΘΕΙΑ ΚΑΘΕΤΗΡΩΝ"] * 3) == ["ΚΑΘΕΤΗΡΩΝ"]
    assert "χειρουργικων μασκων" in out
    assert not any("προμηθεια" in s for s in out)
    assert not any("σπανιου" in s for s in out)                  # 1 title only
    # A word that only ever appears inside a suggested pair is not repeated.
    assert "γαντιων" not in out


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _cleanup(cur):
    cur.execute("DELETE FROM proc.customer_company_match WHERE afm IN (%s, %s)",
                (FIRM_AFM, OTHER_AFM))
    cur.execute("""DELETE FROM proc.act_operator WHERE adam LIKE 'OBT%%'
                    OR operator_id IN (SELECT operator_id FROM proc.economic_operator
                                        WHERE vat_number IN (%s, %s))""",
                (FIRM_AFM, OTHER_AFM))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'OBT%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number IN (%s, %s)",
                (FIRM_AFM, OTHER_AFM))
    cur.execute("DELETE FROM proc.authority WHERE org_id = 'OBTAUTH'")
    cur.execute("DELETE FROM proc.gemi_enrichment WHERE afm IN (%s, %s)",
                (FIRM_AFM, OTHER_AFM))


@pytest.fixture()
def world(db):
    """Reference data + one contractor with a small award history:
    4 × 3314 (gloves) and 2 × 3318, five in Central Macedonia, one in Attica."""
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("""INSERT INTO proc.product (code, name, default_period_days)
                   VALUES ('test', 'Test', 7) ON CONFLICT (code) DO NOTHING""")
    for code, d in (("33140000-3", "Ιατρικά αναλώσιμα"),
                    ("33141100-1", "Επίδεσμοι"),
                    ("33180000-5", "Λειτουργική υποστήριξη"),
                    ("33184100-4", "Χειρουργικά εμφυτεύματα"),
                    ("45000000-7", "Κατασκευαστικές εργασίες")):
        cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description)
                       VALUES (%s, %s) ON CONFLICT (cpv_code) DO NOTHING""", (code, d))
    cur.execute("""INSERT INTO proc.tender_category (id, name, name_en)
                   VALUES (9901, 'Ιατρικός (δοκιμή)', 'Medical (test)')
                   ON CONFLICT (id) DO NOTHING""")
    for code in ("EL52", "EL522", "EL30", "EL303"):
        cur.execute("""INSERT INTO proc.nuts_code (nuts_code, label)
                       VALUES (%s, %s) ON CONFLICT (nuts_code) DO NOTHING""",
                    (code, code))
    cur.execute("""INSERT INTO proc.authority (org_id, name)
                   VALUES ('OBTAUTH', 'ΝΟΣΟΚΟΜΕΙΟ ΔΟΚΙΜΗΣ')
                   ON CONFLICT (org_id) DO NOTHING""")
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES (%s, 'ΓΑΝΤΙΑ ΔΟΚΙΜΗΣ ΑΕ', true) RETURNING operator_id""",
                (FIRM_AFM,))
    op = cur.fetchone()["operator_id"]
    plan = [("33141100-1", "EL522", "Προμήθεια γαντιών νιτριλίου")] * 4 + \
           [("33184100-4", "EL522", "Προμήθεια χειρουργικών εμφυτευμάτων"),
            ("33184100-4", "EL303", "Προμήθεια χειρουργικών εμφυτευμάτων")]
    for i, (cpv, nuts, title) in enumerate(plan):
        adam = f"OBT{i:03d}"
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, authority_id,
                          nuts_code, total_cost_with_vat, submission_date)
                       VALUES (%s,'contract',%s,'import','khmdhs','OBTAUTH',%s,%s,
                               now() - interval '3 days')""",
                    (adam, title, nuts, 10_000 + i * 1_000))
        cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                       VALUES (%s, %s, 'winner')""", (adam, op))
        cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                       VALUES (%s, 'είδος') RETURNING id""", (adam,))
        od = cur.fetchone()["id"]
        cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s, %s)""", (od, cpv))
    yield cur
    _cleanup(cur)


def _register(client, username, *, experience="yes", afm="", **extra):
    data = {"username": username, "email": f"{username}@example.com",
            "password": "goodpassword1", "password2": "goodpassword1"}
    if experience is not None:
        data["tender_experience"] = experience
    if afm is not None:
        data["afm"] = afm
    data.update(extra)
    return client.post("/register", data=data, follow_redirects=False)


def _uid(cur, username):
    cur.execute("SELECT id FROM proc.app_user WHERE username = %s", (username,))
    return cur.fetchone()["id"]


def _post(client, path, data=None):
    return client.post(path, data=data or {},
                       headers={"X-CSRF-Token": get_csrf(client)},
                       follow_redirects=False)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_the_register_page_asks_the_question(client, world):
    body = client.get("/register").text
    assert 'name="tender_experience"' in body and 'name="afm"' in body


def test_the_question_is_required(client, world):
    r = _register(client, "ob_noanswer", experience=None)
    assert r.status_code == 400
    assert "ob_noanswer" in r.text                  # values kept
    world.execute("SELECT 1 FROM proc.app_user WHERE username = 'ob_noanswer'")
    assert world.fetchone() is None


def test_a_bad_afm_with_yes_is_refused_and_the_form_kept(client, world):
    bad = FIRM_AFM[:8] + str((int(FIRM_AFM[8]) + 1) % 10)
    r = _register(client, "ob_badafm", afm=bad)
    assert r.status_code == 400 and bad in r.text


def test_yes_without_an_afm_creates_the_account_and_opens_the_wizard(client, world):
    r = _register(client, "ob_yes", afm="")
    assert r.status_code == 303 and r.headers["location"] == "/welcome"
    uid = _uid(world, "ob_yes")
    assert ob.get_experience(world, uid) is True
    assert ob.declared_afm(world, uid) is None


def test_no_with_an_afm_discards_the_afm(client, world):
    """Without JS the ΑΦΜ box is always visible, so "Όχι" + an ΑΦΜ is a normal
    submission, not an error — and the ΑΦΜ is not kept."""
    r = _register(client, "ob_no", experience="no", afm=FIRM_AFM)
    assert r.status_code == 303
    uid = _uid(world, "ob_no")
    assert ob.get_experience(world, uid) is False
    assert ob.declared_afm(world, uid) is None


def test_registration_never_looks_anything_up(client, world, monkeypatch):
    """A slow or failing registry must never stand between a person and their
    account."""
    from app import gemi_client

    def boom(*a, **k):
        raise AssertionError("no lookup during sign-up")
    for name in ("enrich_one", "fetch_company", "search_by_name_env"):
        monkeypatch.setattr(gemi_client, name, boom)
    monkeypatch.setattr(ob, "lookup", boom)
    monkeypatch.setattr(ob._fit, "ledger_summary", boom)
    r = _register(client, "ob_nolookup", afm=FIRM_AFM)
    assert r.status_code == 303


def test_registration_writes_the_answer_and_nothing_else_on_the_profile(client, world):
    _register(client, "ob_profile", afm=FIRM_AFM)
    uid = _uid(world, "ob_profile")
    world.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    row = world.fetchone()
    assert row["tender_experience"] is True
    for col in ("vat_number", "tax_number", "operator_id", "company"):
        assert row[col] is None, col
    assert ob.declared_afm(world, uid) == FIRM_AFM


def test_a_declared_afm_already_used_by_another_account_is_not_revealed(client, world):
    """"This ΑΦΜ is taken" would tell a stranger the firm has an account here."""
    a = _register(client, "ob_dup1", afm=FIRM_AFM)
    client.cookies.clear()
    b = _register(client, "ob_dup2", afm=FIRM_AFM)
    assert a.status_code == b.status_code == 303
    assert a.headers["location"] == b.headers["location"] == "/welcome"


def test_switched_off_there_is_no_question_no_wizard_and_no_redirect(
        client, world, monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "false")
    assert 'name="tender_experience"' not in client.get("/register").text
    r = _register(client, "ob_off", experience=None, afm=None)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/welcome", follow_redirects=False).status_code == 404
    world.execute("SELECT count(*) AS n FROM proc.onboarding")
    assert world.fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# The wizard end to end
# --------------------------------------------------------------------------- #
def _walk_to_overview(client, *, keywords="γάντια νιτριλίου\nμάσκες"):
    """Confirm the ledger firm, keep the suggested chips, add keywords, keep
    the suggested regions. Returns the step-4 response."""
    r = _post(client, "/welcome/step/1", {"action": "confirm"})
    assert r.headers["location"] == "/welcome/step/2"
    page = client.get("/welcome/step/2").text
    chips = re.findall(r'name="cpv" value="(\d+)" checked', page)
    r = _post(client, "/welcome/step/2", {"action": "next", "cpv": chips})
    assert r.headers["location"] == "/welcome/step/3"
    r = _post(client, "/welcome/step/3", {"action": "next", "keywords": keywords})
    assert r.headers["location"] == "/welcome/step/4"
    page = client.get("/welcome/step/4").text
    nuts = re.findall(r'name="nuts" value="(EL\d+)" checked', page)
    # Post the range the page pre-filled, as a browser would.
    vals = dict(re.findall(r'name="(value_min|value_max)"[^>]*value="([^"]*)"', page))
    r = _post(client, "/welcome/step/4", {"action": "next", "nuts": nuts,
                                          "awards": "1", **vals})
    assert r.headers["location"] == "/welcome/step/5"
    return chips, nuts


def test_yes_with_an_afm_shows_the_firm_on_the_first_screen(client, world):
    _register(client, "ob_show", afm=FIRM_AFM)
    r = client.get("/welcome", follow_redirects=False)
    assert r.headers["location"] == "/welcome/step/0"
    _post(client, "/welcome/step/0")
    body = client.get("/welcome/step/1").text
    # Worded as "the contractor with this ΑΦΜ", not "you": it is unverified.
    assert "ΓΑΝΤΙΑ ΔΟΚΙΜΗΣ ΑΕ" in body and "Ο ανάδοχος με αυτό το ΑΦΜ" in body
    assert "<b>6</b>" in body


def test_the_full_run_creates_the_searches_and_respects_the_claim_rule(client, world):
    from app import fit
    _register(client, "ob_full", afm=FIRM_AFM)
    uid = _uid(world, "ob_full")
    _post(client, "/welcome/step/0")
    client.get("/welcome/step/1")
    chips, nuts = _walk_to_overview(client)
    assert chips == ["3314", "3318"]
    assert sorted(nuts) == ["EL30", "EL52"]

    overview = client.get("/welcome/step/5").text
    assert "Νέοι διαγωνισμοί στο αντικείμενό μου" in overview
    assert "θα είχε βρει" in overview                    # the count ran

    r = _post(client, "/welcome/step/5", {"action": "finish",
                                          "name_notices": "Γάντια — διαγωνισμοί"})
    assert r.status_code == 303
    world.execute("""SELECT id, name, params FROM proc.search_profile
                      WHERE owner_user_id = %s ORDER BY id""", (uid,))
    rows = world.fetchall()
    assert [r_["name"] for r_ in rows] == [
        "Γάντια — διαγωνισμοί", "Διαγωνισμοί με τις λέξεις-κλειδιά μου",
        "Αναθέσεις στον κλάδο μου"]
    assert r.headers["location"] == f"/search-profiles/{rows[0]['id']}/apply"
    assert rows[0]["params"]["cpv"] == ["3314", "3318"]
    assert rows[1]["params"]["q"] == '"γάντια νιτριλίου" or "μάσκες"'
    assert rows[2]["params"]["type"] == ["contract"]
    # The ledger's value band is a hint, never a silent filter: p10–p90 would
    # hide a fifth of the sizes the firm wins and every unbudgeted tender.
    assert "value_min" not in rows[0]["params"] and "value_max" not in rows[0]["params"]

    # ---- the claim rule ----
    world.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    prof = world.fetchone()
    assert prof["vat_number"] is None and prof["tax_number"] is None
    assert prof["operator_id"] is None
    assert fit.operator_ids_for(world, uid) == []
    for table in ("company_profile", "company_profile_cpv",
                  "company_profile_nuts", "company_profile_buyer"):
        world.execute(f"SELECT count(*) AS n FROM proc.{table} WHERE user_id = %s",
                      (uid,))
        assert world.fetchone()["n"] == 0, table

    world.execute("SELECT completed_at, created_profile_ids FROM proc.onboarding "
                  "WHERE user_id = %s", (uid,))
    st = world.fetchone()
    assert st["completed_at"] is not None
    assert sorted(st["created_profile_ids"]) == [r_["id"] for r_ in rows]

    # The home page says so once, then stops.
    home = client.get(f"/search-profiles/{rows[0]['id']}/apply").text
    assert "Δημιουργήθηκαν" in home
    assert "Δημιουργήθηκαν" not in client.get("/").text


def test_no_opens_on_the_manual_path(client, world):
    _register(client, "ob_manual", experience="no")
    r = _post(client, "/welcome/step/0")
    assert r.headers["location"] == "/welcome/step/2"
    assert client.get("/welcome/step/1", follow_redirects=False).headers[
        "location"] == "/welcome/step/2"
    page = client.get("/welcome/step/2").text
    assert 'value="c:9901"' in page                       # categories offered
    r = _post(client, "/welcome/step/2", {"action": "next", "cat": ["c:9901"]})
    assert r.headers["location"] == "/welcome/step/3"


def test_the_subject_step_needs_something_and_drops_what_is_not_real(client, world):
    _register(client, "ob_tamper", experience="no")
    _post(client, "/welcome/step/0")
    r = _post(client, "/welcome/step/2", {"action": "next", "cpv": ["99999999"],
                                          "cat": ["c:987654", "x:1"],
                                          "cpv_add": "'; DROP TABLE"})
    assert r.status_code == 400
    r = _post(client, "/welcome/step/2", {"action": "next",
                                          "cpv": ["99999999", "3314"],
                                          "cpv_add": "45, 1234567890"})
    assert r.status_code == 303
    uid = _uid(world, "ob_tamper")
    world.execute("SELECT answers FROM proc.onboarding WHERE user_id = %s", (uid,))
    assert world.fetchone()["answers"]["cpv"] == ["3314", "45"]


def test_regions_outside_the_filter_list_are_dropped(client, world):
    _register(client, "ob_nuts", experience="no")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
    _post(client, "/welcome/step/3", {"action": "next", "keywords": ""})
    _post(client, "/welcome/step/4", {"action": "next", "nuts": ["EL52", "ZZ99"]})
    uid = _uid(world, "ob_nuts")
    world.execute("SELECT answers FROM proc.onboarding WHERE user_id = %s", (uid,))
    assert world.fetchone()["answers"]["nuts"] == ["EL52"]


def test_keywords_over_the_limit_or_generic_are_refused(client, world):
    _register(client, "ob_kw", experience="no")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
    too_many = "\n".join(f"λέξη{i:02d}" for i in range(ob.MAX_KEYWORDS + 1))
    r = _post(client, "/welcome/step/3", {"action": "next", "keywords": too_many})
    assert r.status_code == 400 and "λέξη15" in r.text
    r = _post(client, "/welcome/step/3", {"action": "next", "keywords": "προμήθεια"})
    assert r.status_code == 400 and "γενική" in r.text


def test_a_bad_value_range_is_refused(client, world):
    _register(client, "ob_range", experience="no")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
    _post(client, "/welcome/step/3", {"action": "next"})
    r = _post(client, "/welcome/step/4", {"action": "next", "value_min": "500",
                                          "value_max": "100"})
    assert r.status_code == 400


def test_closing_the_tab_resumes_where_it_stopped(client, world):
    _register(client, "ob_resume", experience="no")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
    _post(client, "/welcome/step/3", {"action": "next", "keywords": "γάντια"})
    r = client.get("/welcome", follow_redirects=False)
    assert r.headers["location"] == "/welcome/step/4"
    assert "γάντια" in client.get("/welcome/step/3").text


def test_rejecting_the_firm_drops_the_claim(client, world):
    _register(client, "ob_reject", afm=FIRM_AFM)
    _post(client, "/welcome/step/0")
    client.get("/welcome/step/1")
    _post(client, "/welcome/step/1", {"action": "reject"})
    uid = _uid(world, "ob_reject")
    assert ob.declared_afm(world, uid) is None
    world.execute("SELECT answers FROM proc.onboarding WHERE user_id = %s", (uid,))
    assert "lookup" not in world.fetchone()["answers"]


def test_an_afm_with_no_awards_says_that_is_normal(client, world):
    _register(client, "ob_noawards")
    _post(client, "/welcome/step/0")
    r = _post(client, "/welcome/step/1", {"action": "lookup", "afm": OTHER_AFM})
    assert r.headers["location"] == "/welcome/step/1"
    body = client.get("/welcome/step/1").text
    assert "δεν έχετε ακόμη κερδίσει" in body


def test_afm_lookups_are_throttled(client, world, monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "_MAX_FAILS", 2)
    _register(client, "ob_throttle")
    _post(client, "/welcome/step/0")
    for _ in range(2):
        r = _post(client, "/welcome/step/1", {"action": "lookup", "afm": OTHER_AFM})
        assert r.status_code == 303
    r = _post(client, "/welcome/step/1", {"action": "lookup", "afm": OTHER_AFM})
    assert r.status_code == 429


def test_a_full_saved_search_list_creates_what_fits_and_says_so(
        client, world, monkeypatch):
    from app import account_searches
    monkeypatch.setattr(account_searches, "MAX_SAVED_SEARCHES", 1)
    _register(client, "ob_cap", experience="no")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
    _post(client, "/welcome/step/3", {"action": "next", "keywords": "γάντια"})
    _post(client, "/welcome/step/4", {"action": "next", "awards": "1"})
    r = _post(client, "/welcome/step/5", {"action": "finish"})
    assert r.status_code == 303 and r.headers["location"].startswith("/account/searches?flash=")
    uid = _uid(world, "ob_cap")
    world.execute("SELECT count(*) AS n FROM proc.search_profile WHERE owner_user_id = %s",
                  (uid,))
    assert world.fetchone()["n"] == 1


def test_rerunning_never_touches_the_earlier_searches(client, world):
    _register(client, "ob_rerun", experience="no")
    uid = _uid(world, "ob_rerun")
    for _ in range(2):
        _post(client, "/welcome/step/0")
        _post(client, "/welcome/step/2", {"action": "next", "cpv": ["3314"]})
        _post(client, "/welcome/step/3", {"action": "next"})
        _post(client, "/welcome/step/4", {"action": "next", "awards": "1"})
        _post(client, "/welcome/step/5", {"action": "finish"})
    world.execute("SELECT count(*) AS n FROM proc.search_profile WHERE owner_user_id = %s",
                  (uid,))
    assert world.fetchone()["n"] == 4                     # 2 per run, none replaced


def test_signed_out_visitors_are_sent_to_login_and_posts_refused(client, world):
    r = client.get("/welcome", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]
    r = client.post("/welcome/skip", follow_redirects=False)
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# The band on /
# --------------------------------------------------------------------------- #
def test_the_band_shows_until_skipped(client, world):
    _register(client, "ob_band", experience="no")
    assert "Ρυθμίστε τις αναζητήσεις σας σε 2 λεπτά" in client.get("/").text
    _post(client, "/welcome/skip")
    assert "Ρυθμίστε τις αναζητήσεις σας σε 2 λεπτά" not in client.get("/").text


def test_no_band_for_a_customer_who_already_keeps_searches(client, world):
    from app import auth
    uid = make_user("ob_old", "goodpassword1")
    auth.create_search_profile(world, name="Παλιά", scope="customer", owner_id=uid,
                               params={"q": "x"}, based_on_id=None, created_by=uid)
    login(client, "ob_old", "goodpassword1")
    assert "Ρυθμίστε τις αναζητήσεις σας σε 2 λεπτά" not in client.get("/").text


# --------------------------------------------------------------------------- #
# The ledger summary agrees with the profile an admin would derive
# --------------------------------------------------------------------------- #
def test_ledger_summary_agrees_with_seed_from_ledger(world):
    from app import fit
    uid = make_user("ob_seed")
    world.execute("""INSERT INTO proc.customer_profile (user_id, vat_number)
                     VALUES (%s, %s)""", (uid, FIRM_AFM))
    ops = fit.operator_ids_for_afm(world, FIRM_AFM)
    assert ops == fit.operator_ids_for(world, uid)
    summary = fit.ledger_summary(world, ops)
    seeded = fit.seed_from_ledger(world, uid)
    assert summary["n_awards"] == seeded["n_awards"] == 6
    assert summary["n_buyers"] == seeded["n_buyers"]
    world.execute("""SELECT cpv_prefix, n_acts FROM proc.company_profile_cpv
                      WHERE user_id = %s AND length(cpv_prefix) = 4""", (uid,))
    assert {r["cpv_prefix"]: r["n_acts"] for r in world.fetchall()} == \
        {r["prefix"]: r["n_acts"] for r in summary["cpv4"]}
    world.execute("""SELECT nuts_prefix, n_acts FROM proc.company_profile_nuts
                      WHERE user_id = %s""", (uid,))
    assert {r["nuts_prefix"]: r["n_acts"] for r in world.fetchall()} == \
        {r["prefix"]: r["n_acts"] for r in summary["nuts"]}
    world.execute("SELECT value_p10, value_p90 FROM proc.company_profile WHERE user_id = %s",
                  (uid,))
    row = world.fetchone()
    assert (row["value_p10"], row["value_p90"]) == (summary["p10"], summary["p90"])


def test_the_ledger_summary_writes_nothing(world):
    from app import fit
    world.execute("SELECT count(*) AS n FROM proc.company_profile")
    before = world.fetchone()["n"]
    fit.ledger_summary(world, fit.operator_ids_for_afm(world, FIRM_AFM))
    world.execute("SELECT count(*) AS n FROM proc.company_profile")
    assert world.fetchone()["n"] == before


# --------------------------------------------------------------------------- #
# CRM
# --------------------------------------------------------------------------- #
def _admin(client):
    make_user("ob_admin", "goodpassword1", role="admin")
    login(client, "ob_admin", "goodpassword1")


def test_crm_filters_by_tender_experience_and_shows_the_funnel(client, world):
    _register(client, "ob_crm_yes")
    client.cookies.clear()
    _register(client, "ob_crm_no", experience="no")
    client.cookies.clear()
    make_user("ob_crm_unknown", "goodpassword1")
    _admin(client)
    yes = client.get("/admin/crm?exp=yes").text
    assert "ob_crm_yes" in yes and "ob_crm_no" not in yes and "ob_crm_unknown" not in yes
    unknown = client.get("/admin/crm?exp=unknown").text
    assert "ob_crm_unknown" in unknown and "ob_crm_yes" not in unknown
    assert "ξεκίνησαν" in client.get("/admin/crm").text


def test_the_card_shows_the_declared_afm_and_edits_the_answer(client, world):
    _register(client, "ob_card", afm=FIRM_AFM)
    uid = _uid(world, "ob_card")
    client.cookies.clear()
    _admin(client)
    card = client.get(f"/admin/crm/{uid}").text
    assert "Δηλωμένο ΑΦΜ στην εγγραφή" in card and FIRM_AFM in card
    assert "Εμπειρία διαγωνισμών" in card
    r = _post(client, f"/admin/crm/{uid}/profile",
              {"email": "ob_card@example.com", "tender_experience": "no"})
    assert r.status_code == 303
    assert ob.get_experience(world, uid) is False
    # A profile save WITHOUT the field (an older card) leaves it alone.
    _post(client, f"/admin/crm/{uid}/profile", {"email": "ob_card@example.com"})
    assert ob.get_experience(world, uid) is False


def _stub_registry(monkeypatch, record):
    """enrich_one without a network, through the REAL upsert."""
    from app import gemi_client as GC

    def fake(cur, afm_raw):
        afm = GC.normalize_afm(afm_raw)
        GC.upsert(cur, afm, "ok", record, 1)
        return "ok", afm
    monkeypatch.setattr(GC, "enrich_one", fake)


def test_an_afm_only_in_the_registry_confirms_the_name(client, world, monkeypatch):
    from tests.test_company_match import _rec
    monkeypatch.setenv("GEMI_API_KEY", "stub")
    _stub_registry(monkeypatch, _rec(OTHER_AFM, "ΝΕΑ ΕΤΑΙΡΕΙΑ ΙΚΕ"))
    _register(client, "ob_gemi")
    _post(client, "/welcome/step/0")
    _post(client, "/welcome/step/1", {"action": "lookup", "afm": OTHER_AFM})
    body = client.get("/welcome/step/1").text
    assert "ΝΕΑ ΕΤΑΙΡΕΙΑ ΙΚΕ" in body and "δεν έχετε ακόμη κερδίσει" in body


def test_company_match_never_touches_the_answer(world, monkeypatch):
    from app import company_match as CM
    from tests.test_company_match import _rec
    uid = make_user("ob_cm")
    ob.set_experience(world, uid, True)
    monkeypatch.setenv("GEMI_API_KEY", "stub")
    _stub_registry(monkeypatch, _rec(FIRM_AFM, "ΓΑΝΤΙΑ ΔΟΚΙΜΗΣ ΑΕ"))
    CM.apply_match(world, uid, FIRM_AFM)
    assert ob.get_experience(world, uid) is True
    assert "tender_experience" not in (CM.FIELD_LABELS or {})


def test_the_claim_never_reaches_the_customer_record_in_code():
    """The wizard module must not write the identifier columns at all — the
    one door to those is company_match.apply_match."""
    src = open("app/onboarding.py", encoding="utf-8").read()
    writes = re.findall(r"(?:INSERT INTO|UPDATE)\s+proc\.customer_profile[^;]*?\"\"\"",
                        src, re.S)
    assert writes, "expected the tender_experience writes"
    for w in writes:
        for col in ("vat_number", "tax_number", "operator_id"):
            assert col not in w, col
