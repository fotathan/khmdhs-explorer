"""Fit scoring — "is this tender worth bidding for", per customer.

Three things are worth protecting here.

**The isolation rule.** proc.act_ai_summary is generated once per act and
served to every reader (ai_summary.py §3), and /ai tells customers the
summary code cannot see customer data at all. Fit is the first thing in the
app that IS customer-shaped, so the tests below check it stays on its own
side of that line.

**The scoring semantics**, component by component, because a blended number
is exactly the thing nobody can argue with once it is wrong.

**The derived/declared split.** Re-seeding from the ledger must never discard
a human's correction — that is the whole reason `source` is in the primary key.
"""
from __future__ import annotations

import pytest

from app import fit
from tests.helpers import make_user


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #
def _profile(**kw):
    return fit.Profile(user_id=1, **kw)


def test_cpv_grades_by_prefix_depth():
    """CPV is a hierarchy, so a near miss in it is a real near miss: the same
    8 digits is the same thing, the same 2 only the same industry."""
    p = _profile(cpv={"33": 1.0, "3318": 1.0, "33184100": 1.0})
    exact, *_ = fit.score_cpv(p, ["33184100"])
    group, *_ = fit.score_cpv(p, ["33189999"])
    div, *_ = fit.score_cpv(p, ["33990000"])
    assert exact > group > div > 0


def test_cpv_is_zero_for_something_they_do_not_supply():
    p = _profile(cpv={"33": 1.0})
    score, why, _ = fit.score_cpv(p, ["45000000"])
    assert score == 0.0 and "δεν προμηθεύει" in why


def test_cpv_weight_separates_a_speciality_from_a_one_off():
    """A group touched once in 11,000 awards must not read as half a match."""
    core = fit.score_cpv(_profile(cpv={"3318": 1.0}), ["33180000"])[0]
    rare = fit.score_cpv(_profile(cpv={"3318": 0.01}), ["33180000"])[0]
    assert core > rare * 2


def test_cpv_takes_the_best_of_several_codes():
    p = _profile(cpv={"33": 0.5, "44000000": 1.0})
    best, why, detail = fit.score_cpv(p, ["33990000", "44000000"])
    assert "ακριβής" in why and detail == "44000000" and best > 0.7


@pytest.mark.parametrize("value,expected_word", [
    (5_000, "εντός"), (60_000, "κοντά"), (5_000_000, "μεγαλύτερος"),
    (50, "μικρότερος"),
])
def test_value_band(value, expected_word):
    p = _profile(cpv={"33": 1.0}, value_min=3_000, value_max=30_000)
    _, why, _ = fit.score_value(p, value)
    assert expected_word in why


def test_missing_value_is_neutral_not_zero():
    """A great many acts carry no usable amount. Punishing the record for what
    the source omitted would rank on data quality instead of fit."""
    p = _profile(value_min=3_000, value_max=30_000)
    assert fit.score_value(p, None)[0] == 0.5
    assert fit.score_value(p, 0)[0] == 0.5


def test_no_value_history_is_neutral():
    assert fit.score_value(_profile(), 12_345)[0] == 0.5


def test_geography_prefix_matches_at_the_same_depth_as_the_filter():
    p = _profile(nuts={"EL30"})
    assert fit.score_geo(p, "EL303")[0] == 1.0        # inside the region
    assert 0 < fit.score_geo(p, "EL52")[0] < 1.0      # same country
    assert fit.score_geo(p, "BG41")[0] < 0.2          # elsewhere
    assert fit.score_geo(p, None)[0] == 0.5           # unstated


def test_buyer_history_is_recognised():
    p = _profile(buyers={"6003"})
    assert fit.score_buyer(p, "6003")[0] == 1.0
    assert fit.score_buyer(p, "9999")[0] == 0.0
    assert fit.score_buyer(_profile(), "6003")[0] == 0.0


# --------------------------------------------------------------------------- #
# The score
# --------------------------------------------------------------------------- #
def _act(**kw):
    base = {"resolved_value": 10_000, "nuts_code": "EL30", "authority_id": "6003"}
    return {**base, **kw}


def test_a_perfect_match_scores_near_100():
    p = _profile(cpv={"33184100": 1.0}, nuts={"EL30"}, buyers={"6003"},
                 value_min=3_000, value_max=30_000)
    assert fit.explain(p, _act(), ["33184100"])["score"] >= 95


def test_wrong_industry_is_capped_however_good_everything_else_is():
    """"We do not sell that" is not outweighed by being nearby, the right size
    and from a familiar buyer — which is exactly what an uncapped weighted sum
    would do."""
    p = _profile(cpv={"33": 1.0}, nuts={"EL30"}, buyers={"6003"},
                 value_min=3_000, value_max=30_000)
    out = fit.explain(p, _act(), ["45000000"])
    assert out["capped"] is True
    assert out["score"] <= 100 * fit.CPV_FLOOR


def test_every_component_is_explained():
    """A bare number is the one thing a reader cannot argue with."""
    p = _profile(cpv={"33": 1.0}, nuts={"EL30"}, buyers={"6003"})
    out = fit.explain(p, _act(), ["33180000"])
    assert [c["key"] for c in out["components"]] == ["cpv", "value", "geo", "buyer"]
    assert all(c["why"] and c["label"] for c in out["components"])


def test_the_weights_are_a_whole():
    assert fit.W_CPV + fit.W_VALUE + fit.W_GEO + fit.W_BUYER == pytest.approx(1.0)
    assert fit.W_CPV > max(fit.W_VALUE, fit.W_GEO, fit.W_BUYER)


def test_a_profile_with_no_cpvs_is_not_usable():
    """Every score would be the floor, so say "no profile" rather than rank
    by noise."""
    assert _profile().is_usable is False
    assert _profile(cpv={"33": 1.0}).is_usable is True


# --------------------------------------------------------------------------- #
# Seeding from the ledger
# --------------------------------------------------------------------------- #
def _cleanup(cur):
    """Ordered teardown. act_operator references both the act and the operator
    and does not cascade from either, so it goes first — and this runs on the
    way IN as well, because a run that failed mid-fixture leaves rows that
    would block the next one."""
    cur.execute("""DELETE FROM proc.act_operator WHERE adam LIKE 'FIT%'
                    OR operator_id IN (SELECT operator_id
                                         FROM proc.economic_operator
                                        WHERE vat_number = '123456789')""")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'FIT%'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = '123456789'")
    cur.execute("DELETE FROM proc.authority WHERE org_id = 'FITAUTH'")


@pytest.fixture()
def firm(db):
    """A customer linked by ΑΦΜ to an operator with a small award history."""
    cur = db.cursor()
    uid = make_user("fitcust", "goodpassword1")
    _cleanup(cur)                     # a previous failed run leaves rows behind
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES ('123456789','ΔΟΚΙΜΗ ΑΕ', true)
                   RETURNING operator_id""")
    op = cur.fetchone()["operator_id"]
    cur.execute("""INSERT INTO proc.customer_profile (user_id, company, vat_number)
                   VALUES (%s, 'ΔΟΚΙΜΗ ΑΕ', '123456789')
                   ON CONFLICT (user_id) DO UPDATE SET vat_number = '123456789'""",
                (uid,))
    # nuts_code and cpv_code are both FK-constrained reference tables; the
    # schema snapshot carries no rows, so the fixture seeds what it uses.
    cur.execute("""INSERT INTO proc.nuts_code (nuts_code, label)
                   VALUES ('EL303','Δοκιμαστική ενότητα')
                   ON CONFLICT (nuts_code) DO NOTHING""")
    cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description)
                   VALUES ('33184100','Χειρουργικά εμφυτεύματα')
                   ON CONFLICT (cpv_code) DO NOTHING""")
    cur.execute("""INSERT INTO proc.authority (org_id, name)
                   VALUES ('FITAUTH','ΝΟΣΟΚΟΜΕΙΟ ΔΟΚΙΜΗΣ')
                   ON CONFLICT (org_id) DO NOTHING""")
    for i in range(6):
        adam = f"FIT{i:04d}"
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, authority_id,
                          nuts_code, total_cost_with_vat)
                       VALUES (%s,'contract','Δοκιμή','import','khmdhs',
                               'FITAUTH','EL303', %s)""",
                    (adam, 10_000 + i * 1_000))
        cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                       VALUES (%s, %s, 'winner')""", (adam, op))
        cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                       VALUES (%s, 'είδος') RETURNING id""", (adam,))
        od = cur.fetchone()["id"]
        cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s, '33184100')""", (od,))
    yield uid, cur
    _cleanup(cur)


def test_seed_builds_a_profile_from_award_history_alone(firm):
    """No form filled in: the profile comes from what the firm has won."""
    uid, cur = firm
    out = fit.seed_from_ledger(cur, uid)
    assert out["ok"] and out["n_awards"] == 6 and out["n_buyers"] == 1

    p = fit.load_profile(cur, uid)
    assert p.cpv.get("33") == 1.0            # division
    assert "33184100" in p.cpv               # and the exact code
    assert p.nuts == {"EL30"}
    assert p.buyers == {"FITAUTH"}
    assert p.value_min is not None and p.value_min <= p.value_max


def test_seed_needs_something_to_seed_from(db):
    """"No profile" has to be reported, not left looking like a failed run."""
    cur = db.cursor()
    uid = make_user("fitnolink", "goodpassword1")
    out = fit.seed_from_ledger(cur, uid)
    assert out["ok"] is False and "ΑΦΜ" in out["reason"]


def test_a_thin_history_gets_no_value_band(firm):
    """Percentiles over three contracts describe noise. Better no band — the
    value component then scores neutral — than a confident wrong one."""
    uid, cur = firm
    cur.execute("DELETE FROM proc.procurement_act WHERE adam IN ('FIT0003','FIT0004','FIT0005')")
    out = fit.seed_from_ledger(cur, uid)
    assert out["ok"] and out["banded"] is False
    assert fit.load_profile(cur, uid).value_min is None


def test_reseeding_keeps_a_human_correction(firm):
    """The whole reason `source` is in the primary key."""
    uid, cur = firm
    fit.seed_from_ledger(cur, uid)
    cur.execute("""INSERT INTO proc.company_profile_cpv
                     (user_id, cpv_prefix, n_acts, source)
                   VALUES (%s, '45', 1, 'declared')""", (uid,))
    fit.seed_from_ledger(cur, uid)              # re-derive
    cur.execute("""SELECT cpv_prefix FROM proc.company_profile_cpv
                    WHERE user_id = %s AND source = 'declared'""", (uid,))
    assert [r["cpv_prefix"] for r in cur.fetchall()] == ["45"]


def test_weights_are_normalised_within_each_depth(firm):
    """Normalising globally measures an 8-digit code against the busiest
    DIVISION — necessarily a fraction of it — so the deepest, most specific
    match scores lowest. That is backwards, and it showed up on real data as a
    supplier's own speciality scoring 0.36."""
    uid, cur = firm
    fit.seed_from_ledger(cur, uid)
    p = fit.load_profile(cur, uid)
    assert p.cpv["33184100"] == 1.0        # the only 8-digit code: top of its depth
    assert p.cpv["3318"] == 1.0
    assert p.cpv["33"] == 1.0


def test_rank_orders_by_score_and_explains_each_row(firm):
    uid, cur = firm
    fit.seed_from_ledger(cur, uid)
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id,
                      nuts_code, total_cost_with_vat, final_submission_date)
                   VALUES ('FITOPEN1','notice','Ανοικτή','import','khmdhs',
                           'FITAUTH','EL303', 12000, now() + interval '10 days')""")
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES ('FITOPEN1','είδος') RETURNING id""")
    od = cur.fetchone()["id"]
    cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                   VALUES (%s,'33184100')""", (od,))
    out = fit.rank(cur, uid)
    assert out["reason"] is None
    adams = [r["adam"] for r in out["rows"]]
    assert "FITOPEN1" in adams
    row = next(r for r in out["rows"] if r["adam"] == "FITOPEN1")
    assert row["score"] >= 95 and len(row["components"]) == 4


def test_rank_skips_closed_and_cancelled_tenders(firm):
    """Nothing that cannot be bid for should reach a "worth bidding" list."""
    uid, cur = firm
    fit.seed_from_ledger(cur, uid)
    for adam, extra in (("FITSHUT", "final_submission_date = now() - interval '1 day'"),
                        ("FITCANC", "final_submission_date = now() + interval '5 days', "
                                    "cancelled = true")):
        cur.execute(f"""INSERT INTO proc.procurement_act
                          (adam, type, title, origin, data_source, authority_id,
                           nuts_code, total_cost_with_vat)
                        VALUES (%s,'notice','x','import','khmdhs','FITAUTH',
                                'EL303', 12000)""", (adam,))
        cur.execute(f"UPDATE proc.procurement_act SET {extra} WHERE adam = %s", (adam,))
        cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                       VALUES (%s,'x') RETURNING id""", (adam,))
        od = cur.fetchone()["id"]
        cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                       VALUES (%s,'33184100')""", (od,))
    adams = [r["adam"] for r in fit.rank(cur, uid)["rows"]]
    assert "FITSHUT" not in adams and "FITCANC" not in adams


def test_rank_says_why_when_there_is_nothing_to_rank(db):
    cur = db.cursor()
    uid = make_user("fitempty", "goodpassword1")
    out = fit.rank(cur, uid)
    assert out["rows"] == [] and out["reason"]


# --------------------------------------------------------------------------- #
# The isolation rule
# --------------------------------------------------------------------------- #
def _code_strings(path: str) -> str:
    """Every string literal and imported name in a module, docstrings and
    comments excluded — so a rule can be asserted against what the code DOES
    without tripping over the comment that explains the rule."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    doc_nodes = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            first = (n.body or [None])[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_nodes.add(id(first.value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in doc_nodes:
                out.append(node.value)
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
            out += [a.name for a in node.names]
    return "\n".join(out)


def test_fit_never_touches_the_shared_summary_cache():
    """act_ai_summary is one row per act, served to everyone. A customer-shaped
    input anywhere near it leaks between customers — so the module that knows
    about customers must not read or write it."""
    code = _code_strings("app/fit.py")
    for forbidden in ("act_ai_summary", "ai_summary_job", "ai_summary"):
        assert forbidden not in code, forbidden


def test_the_summary_still_cannot_read_a_company_profile():
    """The other direction, and the claim /ai makes to customers in writing."""
    code = _code_strings("app/ai_summary.py")
    assert "company_profile" not in code
    assert "customer_profile" not in code


def test_reasons_are_stable_phrases_with_the_variable_part_split_out():
    """A reason with a CPV code interpolated into it could never be looked up
    in the i18n catalogue, so the phrase and the code travel separately."""
    p = _profile(cpv={"3318": 1.0}, nuts={"EL30"})
    _, why, detail = fit.score_cpv(p, ["33180000"])
    assert detail == "3318" and detail not in why
    _, geo_why, geo_detail = fit.score_geo(p, "EL303")
    assert geo_detail == "EL30" and geo_detail not in geo_why


# --------------------------------------------------------------------------- #
# The admin surface
#
# Admin-only on purpose, and that is a product decision as much as a security
# one: a wrong score shown to a paying customer teaches them to ignore the
# feature permanently, where shown to an admin it costs an afternoon.
# --------------------------------------------------------------------------- #
def test_the_fit_panel_is_admin_only(client, firm):
    uid, _cur = firm
    r = client.get(f"/admin/crm/{uid}", follow_redirects=False)
    assert r.status_code in (303, 403)          # anonymous: never rendered


def test_deriving_is_admin_only(client, firm):
    uid, _cur = firm
    r = client.post(f"/admin/crm/{uid}/fit/derive", follow_redirects=False)
    assert r.status_code in (303, 403)
    assert "/login" in r.headers.get("location", "") or r.status_code == 403


def _admin(client):
    from tests.helpers import get_csrf, login
    make_user("fitadmin", "goodpassword1", role="admin")
    login(client, "fitadmin", "goodpassword1")
    return get_csrf(client)


def test_the_panel_shows_the_profile_and_its_matches(client, firm):
    uid, cur = firm
    _admin(client)
    fit.seed_from_ledger(cur, uid)
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, authority_id,
                      nuts_code, total_cost_with_vat, final_submission_date)
                   VALUES ('FITUI1','notice','Ανοικτός','import','khmdhs',
                           'FITAUTH','EL303', 12000, now() + interval '9 days')""")
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES ('FITUI1','x') RETURNING id""")
    od = cur.fetchone()["id"]
    cur.execute("""INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code)
                   VALUES (%s,'33184100')""", (od,))
    html = client.get(f"/admin/crm/{uid}").text
    assert "ctab-fit" in html
    assert "FITUI1" in html
    # the components, not just a number — the whole point of the panel
    assert "fit-comp" in html
    assert "εντός του συνήθους εύρους τους" in html


def test_derive_rebuilds_the_profile_from_the_card(client, firm):
    uid, cur = firm
    csrf = _admin(client)
    r = client.post(f"/admin/crm/{uid}/fit/derive",
                    data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and "tab=fit" in r.headers["location"]
    cur.execute("SELECT n_awards FROM proc.company_profile WHERE user_id = %s", (uid,))
    assert cur.fetchone()["n_awards"] == 6


def test_derive_on_an_unknown_customer_is_404(client, firm):
    csrf = _admin(client)
    r = client.post("/admin/crm/999999/fit/derive",
                    data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 404


def test_a_customer_with_no_profile_still_renders_the_card(client, db):
    """The panel is one tab on a page that has to work regardless."""
    cur = db.cursor()
    uid = make_user("fitbare", "goodpassword1")
    _admin(client)
    r = client.get(f"/admin/crm/{uid}")
    assert r.status_code == 200 and "ctab-fit" in r.text
