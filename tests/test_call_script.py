"""The first-call sales script (docs/specs/call-script.md).

Most of it is pure: call_script.build() takes the brief and the signals as
dicts, so the truth rules are tested on hand-made briefs without a database.
The DB half covers the signals, the origin marker, the routes and the result
log.
"""
from __future__ import annotations

import itertools
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import call_script as cs
from app import call_script_text as cst
from app import kad_cpv
from tests.helpers import get_csrf, login, make_user
from tests.test_kad_cpv import _code_strings, market  # noqa: F401 — market is a fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Hand-made inputs
# --------------------------------------------------------------------------- #
def _row(score=80, days=5, title="Προμήθεια γαντιών", authority="ΓΝ ΘΕΣΣΑΛΟΝΙΚΗΣ"):
    return {"adam": "26PROC1", "score": score, "title": title,
            "authority_name": authority,
            "final_submission_date": NOW + timedelta(days=days)}


def _est(usable=True, label="Ιατρικά αναλώσιμα"):
    return kad_cpv.Estimate(
        afm="699999999", kads=[], areas=[{"prefix": "3314", "label": label}],
        profile=object() if usable else None)


KADS = [{"kad": "46460212", "descr": "ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ ΙΑΤΡΙΚΩΝ ΑΝΑΛΩΣΙΜΩΝ ΥΛΙΚΩΝ",
         "primary": True}]


def _history(n_good=12, value=840000.0, n_soon=3, top=None, buyers=True):
    return {"row": None, "n_awards": 14,
            "headline": {"n_open": 40, "n_good": n_good, "value_good": value,
                         "n_soon": n_soon},
            "top": [_row()] if top is None else top,
            "buyers": ([{"authority_id": "A1", "authority_name": "ΓΝ ΒΟΛΟΥ",
                         "n_acts": 9, "total_value": 1}] if buyers else []),
            "groups": [{"prefix": "3314", "label": "Χειρουργικά γάντια", "n": 9}]}


def _brief(basis, **kw):
    est = _est()
    b = {"company": "ΝΕΑ ΙΑΤΡΙΚΑ ΑΕ", "afm": "699999999", "kads": KADS,
         "gemi": {"status": "Ενεργή", "status_id": 3}, "est": est,
         "basis": basis, "history": None, "kad": None}
    if basis in ("history", "history+kad"):
        b["history"] = _history()
    if basis in ("history+kad", "kad"):
        b["kad"] = {"est": est, "headline": {"n_open": 9, "n_good": 4,
                                             "value_good": 1000.0, "n_soon": 1}}
    if basis == "none":
        b.update(est=_est(usable=False), kads=[], afm=None, gemi=None)
    b.update(kw)
    return b


def _sig(origin="self", **kw):
    s = {"origin": origin, "status": "tester", "contact": "Μαρία Παπαδοπούλου",
         "company": "ΝΕΑ ΙΑΤΡΙΚΑ ΑΕ", "registered_on": NOW - timedelta(days=4),
         "trial_ends": NOW + timedelta(days=3), "has_phone": True,
         "has_alert": False, "tender_experience": True, "lead_source": None,
         "dnc_at": None, "prior": None, "declared_afm": None,
         "generated_email": False}
    s.update(kw)
    return s


def _spoken(script):
    """Everything the salesperson reads ALOUD (notes are for them)."""
    return [cs.plain(b["segs"]) for b in cs.walk(script)
            if b["kind"] in ("say", "ask", "confirm")]


def _all_text(script):
    return [cs.plain(b["segs"]) for b in cs.walk(script)]


# --------------------------------------------------------------------------- #
# Every branch renders whole
# --------------------------------------------------------------------------- #
SIG_VARIANTS = [
    {}, {"contact": None}, {"company": None, "contact": None},
    {"trial_ends": None, "status": "none"}, {"status": "expired_tester"},
    {"tender_experience": False}, {"has_alert": True},
    {"declared_afm": "123456789"}, {"lead_source": "Έκθεση Food Expo"},
    {"has_phone": False, "prior": {"at": NOW - timedelta(days=30), "outcome": None}},
]


@pytest.mark.parametrize("origin,basis,lang", list(itertools.product(
    cs.ORIGINS, cs.BASES, ("el", "en"))))
def test_every_branch_renders_without_a_hole(origin, basis, lang):
    for variant in SIG_VARIANTS:
        s = cs.build(_brief(basis), _sig(origin, **variant), lang=lang, now=NOW)
        assert not s["blocked"]
        assert cs.BRANCH_RE.match(s["branch"])
        texts = _all_text(s) + [cs.plain(w) for w in s["warnings"]]
        assert texts
        for text in texts:
            assert "[[" not in text and "]]" not in text, text
            assert "  " not in text, text
            assert not re.search(r"\s[.,;·]", text), text
        for blk in cs.walk(s):
            assert all(piece.strip() for piece, is_val in blk["segs"] if is_val)
        part_keys = [p["key"] for p in s["parts"] if p["blocks"]]
        assert part_keys == ["opener", "hook", "discovery", "close"]


def test_both_languages_have_the_same_words():
    assert set(cst.TEXT["el"]) == set(cst.TEXT["en"])
    for lang in ("el", "en"):
        for key, text in cst.TEXT[lang].items():
            assert set(re.findall(r"\[\[([a-z_]+)\]\]", text)) == set(
                re.findall(r"\[\[([a-z_]+)\]\]", cst.TEXT["el"][key])), key


def test_fill_refuses_a_missing_value_instead_of_leaving_a_hole():
    assert cs.fill("κερδίσατε [[n]] αναθέσεις", {"n": None}) is None
    assert cs.fill("κερδίσατε [[n]] αναθέσεις", {"n": " "}) is None
    assert cs.plain(cs.fill("κερδίσατε [[n]] αναθέσεις", {"n": "0"})) == "κερδίσατε 0 αναθέσεις"


# --------------------------------------------------------------------------- #
# The truth rules (§5)
# --------------------------------------------------------------------------- #
def _top_blocks(script):
    for part in script["parts"]:
        yield from part["blocks"]


@pytest.mark.parametrize("basis", ["kad", "history+kad"])
@pytest.mark.parametrize("origin", cs.ORIGINS)
def test_the_estimate_is_only_ever_asked(basis, origin):
    s = cs.build(_brief(basis), _sig(origin), now=NOW)
    kad_blocks = [b for b in _top_blocks(s) if b["src"] == "kad"]
    assert kad_blocks, "the estimate should be asked about"
    assert all(b["kind"] == "ask" for b in kad_blocks)
    # what the estimate guesses is never in a statement
    guess = "ιατρικά αναλώσιμα"
    for blk in cs.walk(s):
        if blk["kind"] == "say":
            assert guess not in cs.plain(blk["segs"]).lower()
            assert "χονδρικό εμπόριο" not in cs.plain(blk["segs"]).lower()


def test_the_history_is_said_as_a_fact():
    s = cs.build(_brief("history"), _sig("contractor_db"), now=NOW)
    hook = s["parts"][1]["blocks"][0]
    assert hook["kind"] == "say" and hook["src"] == "history"


def test_a_declared_afm_is_only_confirmed_and_never_read_out():
    s = cs.build(_brief("none"), _sig("self", declared_afm="123456789"), now=NOW)
    claims = [b for b in cs.walk(s) if b["src"] == "claim"]
    assert [b["kind"] for b in claims] == ["confirm"]
    assert not any("123456789" in t for t in _all_text(s))


def test_a_declared_afm_is_not_asked_once_a_company_is_linked():
    s = cs.build(_brief("kad"), _sig("self", declared_afm="123456789"), now=NOW)
    assert not [b for b in cs.walk(s) if b["src"] == "claim"]


def test_the_numbers_are_the_briefs_own():
    brief = _brief("history", history=_history(n_good=1234, value=2500000.4, n_soon=2,
                                               top=[_row(days=40)]))
    s = cs.build(brief, _sig("contractor_db"), now=NOW)
    assert s["hook"] == "H2"
    hook = cs.plain(s["parts"][1]["blocks"][0]["segs"])
    assert "1.234" in hook and "2.500.000 €" in hook and " 2 " in f" {hook} "
    en = cs.build(brief, _sig("contractor_db"), lang="en", now=NOW)
    hook_en = cs.plain(en["parts"][1]["blocks"][0]["segs"])
    assert "1,234" in hook_en and "€2,500,000" in hook_en


def test_account_activity_is_never_spoken():
    prior = {"at": datetime(2026, 9, 1, 10, tzinfo=timezone.utc),
             "outcome": "Ζήτησε τιμοκατάλογο"}
    s = cs.build(_brief("history"), _sig("self", prior=prior, has_alert=True), now=NOW)
    spoken = " ".join(_spoken(s))
    assert "01/09/2026" not in spoken and "τιμοκατάλογο" not in spoken
    assert any("01/09/2026" in cs.plain(w) for w in s["warnings"])


def test_no_title_is_guessed_from_a_name():
    for origin, basis in itertools.product(cs.ORIGINS, cs.BASES):
        for text in _spoken(cs.build(_brief(basis), _sig(origin), now=NOW)):
            assert not re.search(r"\b(κύριε|κυρία|κ\.|Mr|Ms|Mrs)\b", text), text


# --------------------------------------------------------------------------- #
# Hooks, openers, closes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("brief,hook", [
    (_brief("history"), "H1"),
    (_brief("history", history=_history(top=[_row(days=40)])), "H2"),
    (_brief("history", history=_history(top=[_row(score=30)])), "H2"),
    (_brief("history", history=_history(n_good=0, top=[])), "H3"),
    (_brief("history+kad", history=_history(n_good=0, top=[], buyers=False)), "H4"),
    (_brief("kad"), "H4"),
    (_brief("none"), "H5"),
])
def test_the_hook_is_the_first_one_available(brief, hook):
    assert cs.build(brief, _sig("contractor_db"), now=NOW)["hook"] == hook


def test_h1_names_the_tender_and_cuts_a_long_title():
    long_title = "Προμήθεια " + "υγειονομικού υλικού " * 20
    brief = _brief("history", history=_history(top=[_row(title=long_title)]))
    hook = cs.plain(cs.build(brief, _sig("self"), now=NOW)["parts"][1]["blocks"][0]["segs"])
    assert "ΓΝ ΘΕΣΣΑΛΟΝΙΚΗΣ" in hook and "13/10/2026" in hook
    assert "…" in hook and len(hook) < 400


def test_a_quoted_title_is_not_quoted_twice():
    brief = _brief("history", history=_history(top=[_row(title="«Προμήθεια γαντιών»")]))
    hook = cs.plain(cs.build(brief, _sig("self"), now=NOW)["parts"][1]["blocks"][0]["segs"])
    assert "«Προμήθεια γαντιών»" in hook and "««" not in hook and "»»" not in hook


def test_the_spoken_company_name_is_the_trade_title():
    brief = _brief("history", company="ΣΗΜΑ ΜΟΝΟΠΡΟΣΩΠΗ ΑΝΩΝΥΜΗ ΕΤΑΙΡΙΑ", trade_title="ΣΗΜΑ")
    intro = cs.plain(cs.build(brief, _sig("contractor_db"), now=NOW)
                     ["parts"][0]["blocks"][0]["segs"])
    assert "εταιρεία ΣΗΜΑ;" in intro


def test_one_tender_is_singular():
    brief = _brief("history", history=_history(n_good=1, n_soon=1, top=[_row(days=40)]))
    hook = cs.plain(cs.build(brief, _sig("self"), now=NOW)["parts"][1]["blocks"][0]["segs"])
    assert "έναν ανοιχτό διαγωνισμό" in hook and "Κλείνει μέσα σε 14 ημέρες" in hook


def test_the_kad_description_is_read_in_sentence_case():
    s = cs.build(_brief("kad"), _sig("external"), now=NOW)
    hook = cs.plain(s["parts"][1]["blocks"][0]["segs"])
    assert "«Χονδρικο εμποριο ιατρικων αναλωσιμων υλικων»" in hook
    assert cs._sentence("ΕΙΔΩΝ ΙΑΤΡΙΚΗΣ ΧΡΗΣΗΣ") == "Ειδων ιατρικης χρησης"
    assert cs._sentence("ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ ΓΙΑ ΙΑΤΡΟΥΣ") == "Χονδρικο εμποριο για ιατρους"


def test_the_opener_follows_the_origin():
    self_open = cs.plain(cs.build(_brief("none"), _sig("self"), now=NOW)
                         ["parts"][0]["blocks"][1]["segs"])
    assert "κάνατε εγγραφή" in self_open and "04/10/2026" in self_open
    db_open = cs.plain(cs.build(_brief("history"), _sig("contractor_db"), now=NOW)
                       ["parts"][0]["blocks"][1]["segs"])
    assert "χειρουργικά γάντια" in db_open
    plain_open = cs.plain(cs.build(_brief("none"), _sig("contractor_db"), now=NOW)
                          ["parts"][0]["blocks"][1]["segs"])
    assert "εμφανίζεται σε δημόσιες αναθέσεις" in plain_open
    ext = cs.build(_brief("none"), _sig("external"), now=NOW)["parts"][0]["blocks"][1]
    assert ext["kind"] == "ask" and len(ext["answers"]) == 3


def test_the_trial_end_is_mentioned_only_when_close():
    near = cs.build(_brief("none"), _sig("self"), now=NOW)
    far = cs.build(_brief("none"), _sig("self", trial_ends=NOW + timedelta(days=20)), now=NOW)
    assert any("λήγει" in t for t in _spoken(near))
    assert not any("λήγει" in t for t in _spoken(far))


def test_the_brand_and_language_are_chosen_on_the_script():
    s = cs.build(_brief("none"), _sig("self"), brand=1, lang="en", now=NOW)
    intro = cs.plain(s["parts"][0]["blocks"][0]["segs"])
    assert "Tender Service" in intro and intro.startswith("Good morning")
    s = cs.build(_brief("none"), _sig("self"), brand=99, lang="xx", now=NOW)
    assert "Promitheies.gr" in cs.plain(s["parts"][0]["blocks"][0]["segs"])
    assert s["lang"] == "el"


def test_closes_fit_the_account():
    def closes(**kw):
        s = cs.build(_brief("none"), _sig(**kw), now=NOW)
        return {k for b in s["parts"][3]["blocks"] for k in b["keys"]}
    assert {"c1", "c3", "c4"} <= closes(origin="self")
    assert "c4" not in closes(origin="self", has_alert=True)
    assert "c2" in closes(origin="contractor_db", status="prospective")
    assert "c2" not in closes(origin="contractor_db", status="tester")


def test_price_is_never_quoted():
    for origin, basis in itertools.product(cs.ORIGINS, cs.BASES):
        s = cs.build(_brief(basis), _sig(origin), now=NOW)
        assert not any("€" in t and "τιμή" in t.lower() for t in _spoken(s))
        o4 = next(o for o in s["objections"] if "κοστίζει" in o["q"])
        assert "γραπτώς" in cs.plain(o4["blocks"][0]["segs"])


# --------------------------------------------------------------------------- #
# Banners
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("brief,sig,word", [
    (_brief("history"), _sig("self", status="subscriber"), "συνδρομητής"),
    (_brief("history"), _sig("self", dnc_at=NOW - timedelta(days=2)), "06/10/2026"),
    (_brief("kad", gemi={"status": "Λύση - Εκκαθάριση", "status_id": 7}),
     _sig("contractor_db"), "Λύση - Εκκαθάριση"),
])
def test_a_stop_shows_no_sales_script(brief, sig, word):
    s = cs.build(brief, sig, now=NOW)
    assert s["blocked"] and not s["parts"] and not s["objections"]
    assert any(word in cs.plain(x) for x in s["stops"])


def test_cold_calls_carry_the_register_reminder():
    for origin in cs.ORIGINS:
        s = cs.build(_brief("none"), _sig(origin), now=NOW)
        reminded = any("3471/2006" in cs.plain(w) for w in s["warnings"])
        assert reminded == (origin != "self")


def test_a_generated_address_is_flagged():
    s = cs.build(_brief("none"), _sig("contractor_db", generated_email=True), now=NOW)
    assert any("prospective.com" in cs.plain(w) for w in s["warnings"])


def test_where_did_you_get_my_number_has_a_true_answer_per_origin():
    def o5(origin, **kw):
        s = cs.build(_brief("none"), _sig(origin, **kw), now=NOW)
        obj = next(o for o in s["objections"] if "τηλέφωνό" in o["q"])
        return [cs.plain(b["segs"]) for b in obj["blocks"]]
    assert "εγγραφή" in o5("self")[0]
    assert "ΚΗΜΔΗΣ" in o5("contractor_db")[0]
    assert o5("external", lead_source="Έκθεση Food Expo")[0] == "Από Έκθεση Food Expo."
    unknown = o5("external")
    assert "ελέγξω" in unknown[0] and any("Μην μαντέψετε" in t for t in unknown)
    for origin in cs.ORIGINS:                      # always offers the opt-out
        assert "λίστα κλήσεων" in o5(origin)[-1]


# --------------------------------------------------------------------------- #
# Isolation and the origin marker in code
# --------------------------------------------------------------------------- #
def _code(path):
    return _code_strings(path)


@pytest.mark.parametrize("path", ["app/call_script.py", "app/call_script_text.py"])
def test_no_model_and_no_shared_summary(path):
    code = _code(path)
    for word in ("act_ai_summary", "ai_summary", "call_summary", "anthropic",
                 "deepseek", "urllib", "requests"):
        assert word not in code, word


def test_the_summary_cannot_read_the_script():
    code = _code("app/ai_summary.py")
    assert "call_script" not in code and "script_result" not in code


@pytest.mark.parametrize("source,origin", [
    ("OrgDB", "contractor_db"), ("register", "self"), ("admin", "external"),
    (None, "external"), ("", "external"), ("LinkedIn", "external")])
def test_origin_of(source, origin):
    assert cs.origin_of(source) == origin


# --------------------------------------------------------------------------- #
# Database: signals, origin, routes, results
# --------------------------------------------------------------------------- #
@pytest.fixture()
def trial(db):
    """The self-service test product (the snapshot carries no data)."""
    db.execute("""INSERT INTO proc.product (code, name, default_period_days)
                  VALUES ('test', 'Test', 7) ON CONFLICT (code) DO NOTHING""")
    return db


def _admin(client):
    uid = make_user("csadmin", "goodpassword1", role="admin")
    login(client, "csadmin", "goodpassword1")
    return uid, get_csrf(client)


def _customer(cur, username="cscust", *, source="register", **profile):
    uid = make_user(username, "goodpassword1")
    cols = {"creation_source": source, "company": "ΔΟΚΙΜΗ ΑΕ", **profile}
    names = ", ".join(cols)
    cur.execute(f"""INSERT INTO proc.customer_profile (user_id, {names})
                    VALUES (%s, {", ".join(["%s"] * len(cols))})""",
                (uid, *cols.values()))
    return uid


def test_signals_read_the_account(trial):
    db = trial
    from app import auth
    cur = db.cursor()
    uid = _customer(cur, source="OrgDB", phone="2310000000", lead_source="OrgDB")
    auth.grant_product(cur, uid, "test", granted_by=None)
    cur.execute("""INSERT INTO proc.customer_contact (user_id, ord, first_name, last_name,
                                                      is_main, is_active)
                   VALUES (%s, 0, 'Νίκος', 'Ιωάννου', true, true)""", (uid,))
    cur.execute("UPDATE proc.app_user SET email = %s WHERE id = %s",
                (f"{uid}@prospective.com", uid))
    sig = cs.signals(cur, uid)
    assert sig["origin"] == "contractor_db" and sig["contact"] == "Νίκος Ιωάννου"
    assert sig["has_phone"] and sig["trial_ends"] and sig["generated_email"]
    assert sig["status"] == "tester" and sig["prior"] is None


def test_registration_marks_the_account_as_self(client, trial):
    db = trial
    r = client.post("/register", data={
        "username": "cs_newbie", "email": "cs_newbie@example.com",
        "password": "goodpassword1", "password2": "goodpassword1",
        "tender_experience": "no"}, follow_redirects=False)
    assert r.status_code == 303
    cur = db.cursor()
    cur.execute("""SELECT p.creation_source FROM proc.app_user u
                   JOIN proc.customer_profile p ON p.user_id = u.id
                  WHERE u.username = 'cs_newbie'""")
    assert cur.fetchone()["creation_source"] == "register"


def test_admin_creation_marks_the_account_as_admin(client, db):
    _, csrf = _admin(client)
    r = client.post("/admin/users", data={
        "username": "cs_byadmin", "email": "cs_byadmin@example.com",
        "password": "goodpassword1", "role": "customer", "csrf_token": csrf},
        follow_redirects=False)
    assert r.status_code == 303
    cur = db.cursor()
    cur.execute("""SELECT p.creation_source FROM proc.app_user u
                   JOIN proc.customer_profile p ON p.user_id = u.id
                  WHERE u.username = 'cs_byadmin'""")
    assert cur.fetchone()["creation_source"] == "admin"


def test_an_origin_is_never_overwritten(db):
    from app import auth
    cur = db.cursor()
    uid = _customer(cur, source="OrgDB")
    auth.set_creation_source(cur, uid, "register")
    assert cs.signals(cur, uid)["origin"] == "contractor_db"
    with pytest.raises(ValueError):
        auth.set_creation_source(cur, uid, "whatever")


def test_the_backfill_finds_self_registered_accounts(trial):
    db = trial
    from app import auth
    cur = db.cursor()
    self_uid = make_user("cs_old_self", "goodpassword1")
    auth.grant_product(cur, self_uid, "test", granted_by=None)
    admin_uid = make_user("cs_old_admin", "goodpassword1", role="admin")
    granted = make_user("cs_old_granted", "goodpassword1")
    auth.grant_product(cur, granted, "test", granted_by=admin_uid)
    lead = _customer(cur, "cs_old_lead", source="OrgDB")
    auth.grant_product(cur, lead, "test", granted_by=None)
    sql = (ROOT / "migrations/20261008145739_call_script.sql").read_text()
    backfill = sql[sql.index("INSERT INTO proc.customer_profile"):sql.rindex(";")]
    cur.execute(backfill)
    cur.execute("""SELECT u.username, p.creation_source FROM proc.app_user u
                   LEFT JOIN proc.customer_profile p ON p.user_id = u.id
                  WHERE u.username LIKE 'cs_old_%%'""")
    got = {r["username"]: r["creation_source"] for r in cur.fetchall()}
    assert got == {"cs_old_self": "register", "cs_old_admin": None,
                   "cs_old_granted": None, "cs_old_lead": "OrgDB"}


def test_the_script_is_admin_only(client, db):
    uid = _customer(db.cursor())
    for path in (f"/admin/crm/{uid}/script", f"/admin/crm/{uid}/script?lang=en"):
        assert client.get(path, follow_redirects=False).status_code in (303, 403)
    r = client.post(f"/admin/crm/{uid}/script/result",
                    data={"result": "no_answer"}, follow_redirects=False)
    assert r.status_code in (303, 403)
    login(client, "cscust", "goodpassword1")
    assert client.get(f"/admin/crm/{uid}/script",
                      follow_redirects=False).status_code in (303, 403)


def test_the_dialog_and_the_print_page(client, db):
    uid = _customer(db.cursor())
    _admin(client)
    dlg = client.get(f"/admin/crm/{uid}/script", headers={"HX-Request": "true"})
    assert dlg.status_code == 200
    assert 'action="/admin/crm/%d/script/result"' % uid in dlg.text
    assert 'name="branch" value="self|none|H5"' in dlg.text
    assert "<details open" not in dlg.text and "<!DOCTYPE" not in dlg.text
    assert 'hx-get="/admin/crm/%d/script?lang=en&amp;brand=0"' % uid in dlg.text
    page = client.get(f"/admin/crm/{uid}/script?lang=en&brand=1")
    assert page.status_code == 200 and page.text.lstrip().startswith("<!DOCTYPE")
    assert "<details open" in page.text and "Tender Service" in page.text
    assert "Good morning" in page.text and 'name="result"' not in page.text
    assert client.get("/admin/crm/999999/script").status_code == 404


def test_the_card_offers_the_script_outside_the_tab_panels(client, db):
    uid = _customer(db.cursor())
    _admin(client)
    html = client.get(f"/admin/crm/{uid}").text
    assert f'href="/admin/crm/{uid}/script"' in html
    assert html.index('id="script-dlg"') < html.index('class="cpanel')


def _calls(cur, uid):
    cur.execute("""SELECT status, outcome, script_version, script_branch, script_result,
                          created_by, direction
                     FROM proc.customer_call WHERE user_id = %s ORDER BY id""", (uid,))
    return cur.fetchall()


def test_a_result_is_logged_with_its_frozen_branch(client, db):
    cur = db.cursor()
    uid = _customer(cur)
    admin, csrf = _admin(client)
    r = client.post(f"/admin/crm/{uid}/script/result", data={
        "result": "callback", "branch": "contractor_db|history|H1",
        "version": cst.SCRIPT_VERSION, "note": "  μετά τις   15:00 ",
        "due_at": "2026-10-12T15:00", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and "tab=activity" in r.headers["location"]
    [call] = _calls(cur, uid)
    assert call == {"status": "held", "outcome": "Ξανακαλέστε — μετά τις 15:00",
                    "script_version": cst.SCRIPT_VERSION,
                    "script_branch": "contractor_db|history|H1",
                    "script_result": "callback", "created_by": admin,
                    "direction": "outgoing"}
    cur.execute("SELECT subject, due_at, assigned_to FROM proc.customer_task WHERE user_id = %s",
                (uid,))
    task = cur.fetchone()
    assert task["subject"].startswith("Επανάκληση") and task["assigned_to"] == admin
    assert task["due_at"].astimezone(cs.ATHENS).hour == 15


def test_no_answer_is_logged_as_not_answered(client, db):
    cur = db.cursor()
    uid = _customer(cur)
    _, csrf = _admin(client)
    client.post(f"/admin/crm/{uid}/script/result", data={
        "result": "no_answer", "branch": "self|none|H5",
        "version": cst.SCRIPT_VERSION, "csrf_token": csrf})
    assert _calls(cur, uid)[0]["status"] == "not_answered"


@pytest.mark.parametrize("field,value", [
    ("result", "sold_everything"), ("branch", "self|none|H9"),
    ("branch", "admin|none|H1"), ("version", "0"), ("due_at", "next tuesday")])
def test_a_bad_result_writes_nothing(client, db, field, value):
    cur = db.cursor()
    uid = _customer(cur)
    _, csrf = _admin(client)
    data = {"result": "callback", "branch": "self|none|H5",
            "version": cst.SCRIPT_VERSION, "csrf_token": csrf, field: value}
    r = client.post(f"/admin/crm/{uid}/script/result", data=data, follow_redirects=False)
    assert r.status_code == 303 and "flash=" in r.headers["location"]
    assert _calls(cur, uid) == []
    cur.execute("SELECT 1 FROM proc.customer_task WHERE user_id = %s", (uid,))
    assert cur.fetchone() is None


def test_do_not_call_blocks_the_script_until_an_admin_lifts_it(client, db):
    cur = db.cursor()
    uid = _customer(cur)
    admin, csrf = _admin(client)
    client.post(f"/admin/crm/{uid}/script/result", data={
        "result": "do_not_call", "branch": "self|none|H5",
        "version": cst.SCRIPT_VERSION, "csrf_token": csrf})
    cur.execute("SELECT do_not_call_at, do_not_call_by FROM proc.customer_profile "
                "WHERE user_id = %s", (uid,))
    row = cur.fetchone()
    assert row["do_not_call_at"] is not None and row["do_not_call_by"] == admin
    dlg = client.get(f"/admin/crm/{uid}/script", headers={"HX-Request": "true"}).text
    assert "cs-stop" in dlg and 'name="result"' not in dlg
    card = client.get(f"/admin/crm/{uid}").text
    assert f'action="/admin/crm/{uid}/do-not-call/clear"' in card
    client.post(f"/admin/crm/{uid}/do-not-call/clear", data={"csrf_token": csrf})
    assert cs.signals(cur, uid)["dnc_at"] is None


def test_a_full_script_from_a_real_brief(client, request):
    """The kad market of test_kad_cpv: a linked wholesaler with no awards."""
    world = request.getfixturevalue("market")
    cur, uid = world["cur"], world["uid"]
    from app import crm_brief
    kad_cpv.build(world["conn"])
    brief = crm_brief.build(cur, uid)
    s = cs.build(brief, cs.signals(cur, uid))
    assert s["basis"] == "kad" and s["hook"] == "H4"
    hook = s["parts"][1]["blocks"][0]
    assert hook["kind"] == "ask" and "Χονδρικο εμποριο" in cs.plain(hook["segs"])
    n = brief["kad"]["headline"]["n_good"]
    yes = cs.plain(hook["answers"][0]["then"][0]["segs"])
    assert (str(n) in yes) if n > 1 else True
    _admin(client)
    assert client.get(f"/admin/crm/{uid}/script").status_code == 200
