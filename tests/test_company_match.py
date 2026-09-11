# -*- coding: utf-8 -*-
"""ΓΕΜΗ company match: the registry guard, the re-ranking, and what a link is
allowed to write.

No network and no API key. The registry transport is stubbed with recorded
response shapes, and the link path is driven through the real
gemi_client.flatten/upsert so the storage this feature depends on is exercised
rather than mocked away. DB-backed tests skip without TEST_DATABASE_URL.
"""
import pytest

from app import company_match as CM
from app import gemi_client as GC
from app import leads as L
from tests.helpers import connect, get_csrf, login, make_user


# --------------------------------------------------------------------------- #
# recorded registry shapes
# --------------------------------------------------------------------------- #
def _rec(afm, name, **kw):
    """One searchResults entry, in the shape the registry actually returns."""
    rec = {
        "afm": afm,
        "arGemi": kw.get("ar_gemi", "000" + afm[-4:]),
        "coNameEl": name,
        "coTitlesEl": kw.get("titles", []),
        "legalType": {"id": 8, "descr": kw.get("legal_type", "ΑΕ")},
        "status": {"id": kw.get("status_id", 3),
                   "descr": kw.get("status", "Ενεργή")},
        "isBranch": kw.get("is_branch", False),
        "street": kw.get("street", "Λεωφόρος Δοκιμής"),
        "streetNumber": kw.get("street_number", "12"),
        "zipCode": kw.get("zip", "11111"),
        "city": kw.get("city", "ΑΘΗΝΑ"),
        "municipality": {"id": 1, "descr": "ΑΘΗΝΑΙΩΝ"},
        "prefecture": {"id": 1, "descr": "ΑΤΤΙΚΗΣ"},
        "phone": kw.get("phone", "2101234567"),
        "fax": None,
        "email": kw.get("email"),
        "url": kw.get("url"),
        "incorporationDate": "2001-05-04",
        "activities": kw.get("activities", [
            {"type": "Κύρια", "dtTo": None,
             "activity": {"id": "41201000", "descr": "Κατασκευαστικές εργασίες",
                          "kadVersion": "2008"}},
        ]),
        "persons": [{"name": "ΔΕΝ ΕΙΣΑΓΕΤΑΙ ΠΟΤΕ"}],
        "objective": "…",
    }
    return rec


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Client:
    """Stands in for httpx.Client: hands back one queued response per call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params or {})
        return self._responses.pop(0)


def _payload(total, records):
    return {"searchMetadata": {"totalCount": total, "resultsOffset": 0,
                               "resultsSize": len(records)},
            "searchResults": records}


# --------------------------------------------------------------------------- #
# the guard — the one test that must never be deleted
# --------------------------------------------------------------------------- #
def test_dropped_parameter_is_not_a_result_set():
    """The registry ignores a filter it does not recognise and answers with the
    WHOLE register, which looks exactly like a successful search. Anything that
    size is a bug on our side, never a list of candidates."""
    whole_register = _payload(1_686_179, [_rec("094049864", "ΤΥΧΑΙΑ ΕΤΑΙΡΕΙΑ ΑΕ")])
    client = _Client(_Resp(200, whole_register))
    status, records = GC.search_by_name(client, "key", "ΟΤΙΔΗΠΟΤΕ")
    assert status == "param_ignored"
    assert records == []


def test_a_real_result_is_far_below_the_guard():
    payload = _payload(17, [_rec("094049864", "ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΙΑ")])
    status, records = GC.search_by_name(_Client(_Resp(200, payload)), "key", "ΕΛΠΕ")
    assert status == "ok" and len(records) == 1


def test_registry_404_means_no_match_not_an_error():
    status, records = GC.search_by_name(_Client(_Resp(404)), "key", "ΖΞΘΨΩΩΩ")
    assert (status, records) == ("not_found", [])


def test_blank_query_never_reaches_the_registry():
    client = _Client()
    assert GC.search_by_name(client, "key", "   ") == ("not_found", [])
    assert client.calls == []


def test_search_sends_the_name_parameter():
    client = _Client(_Resp(200, _payload(3, [_rec("094049864", "ΑΛΦΑ ΑΕ")])))
    GC.search_by_name(client, "key", "ΑΛΦΑ", max_results=7)
    assert client.calls[0]["name"] == "ΑΛΦΑ"
    assert client.calls[0]["resultsSize"] == 7


# --------------------------------------------------------------------------- #
# name normalisation + re-ranking
# --------------------------------------------------------------------------- #
def test_normalize_name_strips_legal_forms_and_status_prefix():
    assert CM.normalize_name("Π.ΠΑΠΑΔΟΠΟΥΛΟΣ ΚΑΙ ΣΙΑ Ο.Ε.") == "παπαδοπουλοσ"
    assert CM.normalize_name("(ΔΙΑΓΡΑΦΗΚΕ) ΑΛΦΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ") == "αλφα"
    # accents and final sigma fold together
    assert CM.normalize_name("Άλφα ΑΕ") == CM.normalize_name("ΑΛΦΑ Α.Ε.")


def _score(cand, profile=None, cust=None, freemail=()):
    return CM.score_candidate(cand, profile or {}, cust or {}, set(freemail))


def test_exact_name_outranks_the_registry_order():
    """The live registry returns ΕΛΙΝΟΙΛ *first* for 'ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ' and the
    exact match second. Our order must not be theirs."""
    profile = {"company": "ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ"}
    registry_order = [
        CM._from_record(_rec("094004190",
                             "ΕΛΙΝΟΙΛ ΕΛΛΗΝΙΚΗ ΕΤΑΙΡΙΑ ΠΕΤΡΕΛΑΙΩΝ ΑΝΩΝΥΜΗ ΕΤΑΙΡΙΑ")),
        CM._from_record(_rec("094049864", "ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΙΑ")),
    ]
    for cand in registry_order:
        cand["score"], cand["signals"] = _score(cand, profile)
    ranked = sorted(registry_order, key=lambda x: -x["score"])
    assert ranked[0]["afm"] == "094049864"


def test_email_domain_confirms_a_candidate():
    profile = {"company": "ΑΛΦΑ"}
    cust = {"email": "kostas@alfa-tech.gr"}
    plain = CM._from_record(_rec("111111111", "ΑΛΦΑ ΑΕ"))
    with_domain = CM._from_record(_rec("222222222", "ΑΛΦΑ ΑΕ",
                                       email="info@alfa-tech.gr"))
    s_plain, _ = _score(plain, profile, cust)
    s_domain, sig = _score(with_domain, profile, cust)
    assert s_domain > s_plain
    assert sig["email_domain"]["value"] == 1.0


def test_freemail_domain_confirms_nothing():
    """Every gmail.com customer would otherwise match every gmail.com company."""
    profile = {"company": "ΑΛΦΑ"}
    cust = {"email": "kostas@gmail.com"}
    cand = CM._from_record(_rec("222222222", "ΑΛΦΑ ΑΕ", email="info@gmail.com"))
    score, sig = _score(cand, profile, cust, freemail={"gmail.com"})
    assert sig["email_domain"]["value"] == 0.0
    assert "freemail" in sig["email_domain"]["detail"]


def test_struck_off_company_is_demoted_but_still_returned():
    profile = {"company": "ΑΛΦΑ"}
    active = CM._from_record(_rec("111111111", "ΑΛΦΑ ΑΕ"))
    gone = CM._from_record(_rec("222222222", "ΑΛΦΑ ΑΕ",
                                status="ΔΙΑΓΡΑΦΗΚΕ", status_id=5))
    s_active, _ = _score(active, profile)
    s_gone, sig = _score(gone, profile)
    assert 0 < s_gone < s_active
    assert "ΔΙΑΓΡΑΦΗΚΕ" in sig["penalties"]


def test_explanation_lists_the_components_not_just_a_total():
    profile = {"company": "ΑΛΦΑ", "city": "ΑΘΗΝΑ"}
    cust = {"email": "k@alfa.gr"}
    cand = CM._from_record(_rec("111111111", "ΑΛΦΑ ΑΕ", email="info@alfa.gr"))
    _, signals = _score(cand, profile, cust)
    whys = {w["why"] for w in CM.explain(signals)}
    assert "ομοιότητα επωνυμίας" in whys
    assert "ίδιο domain email" in whys
    assert "ίδια έδρα" in whys


def test_the_raw_record_never_reaches_a_template(monkeypatch, db):
    """A registry record carries the company's officers and up to 22KB of legal
    text. It has no business being rendered or round-tripped."""
    c = db.cursor()
    uid = make_user("cust-raw")
    _profile(c, uid, company="ΑΛΦΑ")
    monkeypatch.setattr(GC, "search_by_name_env",
                        lambda name, max_results=25: ("ok", [_rec("111111111", "ΑΛΦΑ ΑΕ")]))
    out = CM.search(c, uid)
    assert out["candidates"]
    for cand in out["candidates"]:
        assert "record" not in cand
        assert "persons" not in cand


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _profile(cur, uid, **cols):
    keys = list(cols)
    cur.execute(
        f"INSERT INTO proc.customer_profile (user_id, {', '.join(keys)}) "
        f"VALUES (%s, {', '.join(['%s'] * len(keys))}) "
        f"ON CONFLICT (user_id) DO UPDATE SET "
        + ", ".join(f"{k} = EXCLUDED.{k}" for k in keys),
        [uid] + [cols[k] for k in keys])


def _op(cur, vat, name, **extra):
    cols = {"vat_number": vat, "name": name, "country": "GR"}
    cols.update(extra)
    cur.execute(
        f"INSERT INTO proc.economic_operator ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))}) RETURNING operator_id",
        list(cols.values()))
    return cur.fetchone()["operator_id"]


def _read(cur, uid):
    cur.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    return dict(cur.fetchone() or {})


def _stub_registry(monkeypatch, record):
    """enrich_one without a network: store the recorded record through the REAL
    flatten/upsert, so the row apply_match reads is the row production writes."""
    def fake(cur, afm_raw):
        afm = GC.normalize_afm(afm_raw)
        GC.upsert(cur, afm, "ok", record, 1)
        return "ok", afm
    monkeypatch.setattr(GC, "enrich_one", fake)


@pytest.fixture()
def match_clean(_clean):
    with connect() as c:
        c.execute("DELETE FROM proc.economic_operator WHERE vat_number LIKE 'CM%'")
        c.execute("DELETE FROM proc.gemi_enrichment WHERE afm LIKE '9990%'")
        c.execute("INSERT INTO proc.crm_freemail_domain(domain) VALUES ('gmail.com') "
                  "ON CONFLICT DO NOTHING")
    yield


# --------------------------------------------------------------------------- #
# candidate sources
# --------------------------------------------------------------------------- #
def test_ledger_candidates_find_a_contractor_by_part_of_its_name(db, match_clean):
    c = db.cursor()
    _op(c, "CM0001", "ΙΝΤΡΑΚΑΤ ΑΝΩΝΥΜΗ ΤΕΧΝΙΚΗ ΕΤΑΙΡΕΙΑ")
    _op(c, "CM0002", "ΑΣΧΕΤΗ ΕΜΠΟΡΙΚΗ ΑΕ")
    names = [x["name"] for x in CM.ledger_candidates(c, "Ιντρακάτ")]
    assert any("ΙΝΤΡΑΚΑΤ" in n for n in names)
    assert not any("ΑΣΧΕΤΗ" in n for n in names)


def test_search_merges_the_ledger_and_the_registry_on_one_afm(monkeypatch, db,
                                                              match_clean):
    c = db.cursor()
    uid = make_user("cust-merge")
    _profile(c, uid, company="ΑΛΦΑ ΤΕΧΝΙΚΗ")
    _op(c, "999012345", "ΑΛΦΑ ΤΕΧΝΙΚΗ ΑΕ")
    monkeypatch.setattr(
        GC, "search_by_name_env",
        lambda name, max_results=25: ("ok", [_rec("999012345", "ΑΛΦΑ ΤΕΧΝΙΚΗ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ")]))
    out = CM.search(c, uid)
    hits = [x for x in out["candidates"] if x["afm"] == "999012345"]
    assert len(hits) == 1, "the same company must not appear twice"
    assert hits[0]["source"] == "both" and hits[0]["operator_id"]


def test_search_still_works_without_an_api_key(monkeypatch, db, match_clean):
    c = db.cursor()
    uid = make_user("cust-nokey")
    _profile(c, uid, company="ΑΛΦΑ ΤΕΧΝΙΚΗ")
    _op(c, "CM0003", "ΑΛΦΑ ΤΕΧΝΙΚΗ ΑΕ")

    def boom(name, max_results=25):
        raise RuntimeError("GEMI_API_KEY not set")
    monkeypatch.setattr(GC, "search_by_name_env", boom)
    out = CM.search(c, uid)
    assert out["registry_status"] == "no_key"
    assert any("ΑΛΦΑ" in (x["name"] or "") for x in out["candidates"])


# --------------------------------------------------------------------------- #
# linking
# --------------------------------------------------------------------------- #
def test_apply_fills_only_empty_fields(monkeypatch, db, match_clean):
    c = db.cursor()
    uid = make_user("cust-fill")
    _profile(c, uid, company="Η ΔΙΚΗ ΜΟΥ ΓΡΑΦΗ ΑΕ", city="ΠΑΤΡΑ")
    _stub_registry(monkeypatch, _rec("999011111", "ΑΛΦΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ",
                                     city="ΑΘΗΝΑ", zip="15125"))
    out = CM.apply_match(c, uid, "999011111")
    row = _read(c, uid)
    # typed values survive
    assert row["company"] == "Η ΔΙΚΗ ΜΟΥ ΓΡΑΦΗ ΑΕ"
    assert row["city"] == "ΠΑΤΡΑ"
    assert "company" not in out["filled"] and "city" not in out["filled"]
    # blanks are filled
    assert row["postal_code"] == "15125"
    assert row["address"] == "Λεωφόρος Δοκιμής 12"
    assert row["industry"] == "Κατασκευαστικές εργασίες"
    assert "postal_code" in out["filled"]


def test_apply_writes_the_identifiers_and_records_the_match(monkeypatch, db,
                                                            match_clean):
    c = db.cursor()
    uid = make_user("cust-ids")
    _profile(c, uid, company="ΑΛΦΑ")
    oid = _op(c, "999022222", "ΑΛΦΑ ΑΕ")
    _stub_registry(monkeypatch, _rec("999022222", "ΑΛΦΑ ΑΝΩΝΥΜΗ ΕΤΑΙΡΕΙΑ",
                                     ar_gemi="123456789"))
    CM.apply_match(c, uid, "999022222", by=None)
    row = _read(c, uid)
    assert row["vat_number"] == "999022222"
    assert row["reg_number"] == "123456789"
    assert row["operator_id"] == oid
    match = CM.current_match(c, uid)
    assert match["afm"] == "999022222" and match["method"] == "gemi_name"
    assert match["score"] is not None and match["signals"]


def test_apply_never_touches_protected_fields(monkeypatch, db, match_clean):
    c = db.cursor()
    uid = make_user("cust-protect")
    _profile(c, uid, full_name="Κώστας Παπαδόπουλος", crm_stage="prospective",
             service="TAS", lead_source="συνέδριο", about="μην το πειράξεις")
    c.execute("SELECT email FROM proc.app_user WHERE id = %s", (uid,))
    email_before = c.fetchone()["email"]
    _stub_registry(monkeypatch, _rec("999033333", "ΑΛΦΑ ΑΕ", email="info@alfa.gr"))
    CM.apply_match(c, uid, "999033333")
    row = _read(c, uid)
    assert row["full_name"] == "Κώστας Παπαδόπουλος"
    assert row["crm_stage"] == "prospective"
    assert row["service"] == "TAS"
    assert row["lead_source"] == "συνέδριο"
    assert row["about"] == "μην το πειράξεις"
    c.execute("SELECT email FROM proc.app_user WHERE id = %s", (uid,))
    assert c.fetchone()["email"] == email_before


def test_a_different_vat_needs_confirmation(monkeypatch, db, match_clean):
    c = db.cursor()
    uid = make_user("cust-vat")
    _profile(c, uid, vat_number="111111111")
    _stub_registry(monkeypatch, _rec("999044444", "ΑΛΦΑ ΑΕ"))
    with pytest.raises(CM.VatConflict) as exc:
        CM.apply_match(c, uid, "999044444")
    assert exc.value.existing == "111111111"
    assert _read(c, uid)["vat_number"] == "111111111"     # nothing written
    CM.apply_match(c, uid, "999044444", confirm=True)
    assert _read(c, uid)["vat_number"] == "999044444"
    # a confirmed replacement is the admin's, not ours to undo later
    assert "vat_number" not in (CM.current_match(c, uid)["filled"] or {})


def test_unlink_reverts_only_what_the_import_wrote(monkeypatch, db, match_clean):
    c = db.cursor()
    uid = make_user("cust-unlink")
    _profile(c, uid, company="ΤΟ ΔΙΚΟ ΜΟΥ ΟΝΟΜΑ")
    _stub_registry(monkeypatch, _rec("999055555", "ΑΛΦΑ ΑΕ", city="ΑΘΗΝΑ",
                                     zip="15125", phone="2109999999"))
    CM.apply_match(c, uid, "999055555")
    # an admin corrects one of the imported values afterwards
    _profile(c, uid, city="ΘΕΣΣΑΛΟΝΙΚΗ")
    reverted = CM.unlink(c, uid)
    row = _read(c, uid)
    assert row["company"] == "ΤΟ ΔΙΚΟ ΜΟΥ ΟΝΟΜΑ"   # never ours
    assert row["city"] == "ΘΕΣΣΑΛΟΝΙΚΗ"            # edited since — left alone
    assert "city" not in reverted
    assert row["postal_code"] is None              # ours, untouched — reverted
    assert row["vat_number"] is None
    assert CM.current_match(c, uid) is None


def test_link_survives_a_registry_outage_using_the_ledger(monkeypatch, db,
                                                          match_clean):
    c = db.cursor()
    uid = make_user("cust-outage")
    oid = _op(c, "999066666", "ΑΛΦΑ ΑΕ", city="ΛΑΡΙΣΑ", postal_code="41222")

    def boom(cur, afm_raw):
        raise RuntimeError("GEMI_API_KEY not set")
    monkeypatch.setattr(GC, "enrich_one", boom)
    out = CM.apply_match(c, uid, "999066666")
    row = _read(c, uid)
    assert out["gemi_status"] == "no_key" and out["method"] == "ledger"
    assert row["vat_number"] == "999066666" and row["operator_id"] == oid
    assert row["city"] == "ΛΑΡΙΣΑ"


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
def _admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")


def test_link_route_ignores_company_data_posted_with_it(monkeypatch, client,
                                                        match_clean):
    """Only the ΑΦΜ is trusted. Anything else in the form is ignored — the
    company data is rebuilt server-side."""
    with connect() as conn:
        c = conn.cursor()
        uid = make_user("cust-route")
        _profile(c, uid, company=None)
    _stub_registry(monkeypatch, _rec("999077777", "ΠΡΑΓΜΑΤΙΚΗ ΕΠΩΝΥΜΙΑ ΑΕ"))
    _admin(client)
    tok = get_csrf(client)
    r = client.post(f"/admin/crm/{uid}/company-match/link",
                    data={"afm": "999077777", "company": "ΠΛΑΣΤΗ ΕΠΩΝΥΜΙΑ",
                          "city": "ΠΛΑΣΤΗ ΠΟΛΗ", "vat_number": "000000000"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    with connect() as conn:
        row = _read(conn.cursor(), uid)
    assert row["company"] == "ΠΡΑΓΜΑΤΙΚΗ ΕΠΩΝΥΜΙΑ ΑΕ"
    assert row["city"] != "ΠΛΑΣΤΗ ΠΟΛΗ"
    assert row["vat_number"] == "999077777"


def test_search_route_reports_a_dropped_filter_as_an_error(monkeypatch, client,
                                                           match_clean):
    with connect() as conn:
        uid = make_user("cust-guard")
        _profile(conn.cursor(), uid, company="ΑΛΦΑ")
    monkeypatch.setattr(GC, "search_by_name_env",
                        lambda name, max_results=25: ("param_ignored", []))
    _admin(client)
    r = client.post(f"/admin/crm/{uid}/company-match/search",
                    data={"q": "ΑΛΦΑ"}, headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 200
    assert "ολόκληρο το μητρώο" in r.text


def test_match_routes_require_admin(client, match_clean):
    with connect() as conn:
        uid = make_user("cust-anon")
        _profile(conn.cursor(), uid, company="ΑΛΦΑ")
    for path in ("search", "link", "unlink"):
        r = client.post(f"/admin/crm/{uid}/company-match/{path}",
                        data={"afm": "999088888"}, follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403), path


def test_panel_renders_on_the_customer_card(client, match_clean):
    with connect() as conn:
        uid = make_user("cust-card")
        _profile(conn.cursor(), uid, company="ΑΛΦΑ")
    _admin(client)
    html = client.get(f"/admin/crm/{uid}").text
    assert 'id="company-match"' in html
    # The uid has to be IN the posted URL. The panel is an include, so a
    # context that forgets cust_id still renders a form — one that posts to
    # /admin/crm//company-match/search and 404s. That shipped once.
    assert f"/admin/crm/{uid}/company-match/search" in html
    assert "/admin/crm//company-match/" not in html


# --------------------------------------------------------------------------- #
# the shared fill rule
# --------------------------------------------------------------------------- #
def test_fill_if_empty_is_one_implementation_for_both_importers():
    merged, filled = L.fill_if_empty(
        {"company": "ΥΠΑΡΧΕΙ", "city": None, "phone": "  "},
        [("company", "ΝΕΟ"), ("city", "ΑΘΗΝΑ"), ("phone", "210"),
         ("address", None)])
    assert merged["company"] == "ΥΠΑΡΧΕΙ"          # never overwritten
    assert merged["city"] == "ΑΘΗΝΑ"
    assert merged["phone"] == "210"                # whitespace counts as empty
    assert set(filled) == {"city", "phone"}
