"""Tender Service ingester (tsg_ingest.py).

What matters: a procurement we already hold is never shown twice (and a
projection is withdrawn when our own ingester catches up), a joint award never
gets a guessed ΑΦΜ, the API's traps — the 10,000 offset cap, the exclusive
`_to`, the daily cap, offset paging over a live set — never pass silently as a
finished day, a re-walk never re-enters a digest window, and a remote database
is refused.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
from decimal import Decimal

import pytest
import requests

import tsg_ingest as tg

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 9, 15)
DAY = dt.date(2026, 8, 4)
MIGRATIONS = ["migrations/20260915090000_tender_service_source_tables.sql",
              "migrations/20260915090100_tender_service_analytics_exclusion.sql",
              "migrations/20260915120000_tender_service_skip_reason.sql",
              "migrations/20260917130323_tender_service_duplicates.sql",
              "migrations/20260917130324_tender_service_duplicates_tsg_record.sql"]
NATIVE_ADAM = "26PROC019000001"
NATIVE_ADA = "ΨΨΨΨ46ΜΤΛΡ-ΑΒΓ"
FAKE_VAT = "999000111"


def _rec(**over):
    r = {"internalID": "900000101", "externalId": "attik-26-0000001-110926",
         "dataSource": "attikonhospital.gr", "typeOfDocument": "Προκήρυξη",
         "title": "Προμήθεια γαντιών", "contentDescription": "Προμήθεια γαντιών",
         "publicationDate": "04.08.26", "deadlineDate": "14.09.26 13:15",
         "estimatedPrices": "12.400,00 EUR", "cpvCodes": "18424300-0",
         "nutsCodes": "EL303", "authorities": "ΔΟΚΙΜΑΣΤΙΚΟ ΝΟΣΟΚΟΜΕΙΟ",
         "authorityOrgdbId": "990001.0", "tenderText": "<div>Γάντια</div>", "status": "ACTIVE"}
    r.update(over)
    return {k: v for k, v in r.items() if v is not None}


def _award(**over):
    base = {"internalID": "900000103", "typeOfDocument": "Αποτέλεσμα",
            "externalId": "9ΖΖΖΖ46-ΑΒΓ-08-04112116", "dataSource": "http://opendata.diavgeia.gov.gr",
            "contractors": "ΔΟΚΙΜΑΣΤΙΚΟΣ,,ΝΙΚΟΣ,ΠΕΤΡΟΣ", "contractorPrices": "430 EUR",
            "contractorStatisticalOrTaxNumber": FAKE_VAT, "contractValue": "430,00 EUR"}
    base.update(over)
    return _rec(**base)


# --------------------------------------------------------------------------- #
# pure mapping
# --------------------------------------------------------------------------- #
def test_held_keys_are_identity_fields_only():
    # referenceNumber on a notice is the REQUEST it came from, not the notice.
    assert tg.held_keys(_rec(externalId="26PROC019569114", referenceNumber="26REQ019406175")) == ["26PROC019569114"]
    assert tg.held_keys(_rec(externalId="Ψ6ΥΚ7Λ6-ΑΜΥ-08-04145604")) == ["Ψ6ΥΚ7Λ6-ΑΜΥ"]
    assert tg.held_keys(_rec(externalId="TED.00538898-2026")) == ["TED:538898-2026"]
    url = "https://ted.europa.eu/TED/notice/udl?uri=TED:NOTICE:538898-2026:TEXT:EN:HTML"
    assert tg.held_keys(_rec(externalId=None, sourceUrl=url)) == ["TED:538898-2026"]
    assert tg.held_keys(_rec(externalId="isup-475406", referenceNumber="ΨΤΖΒ469061-ΕΦΞ")) == []
    assert tg.held_keys(_rec(externalId=None)) == []


@pytest.mark.parametrize("nuts,outside", [
    ("CY000", True),
    ("EL303", False),
    ("EL", False),
    ("CY000 EL303", False),      # names a Greek place too: kept
    (None, False),               # no NUTS is not evidence of anything
])
def test_outside_greece_needs_every_nuts_code_to_be_foreign(nuts, outside):
    assert tg.outside_greece(_rec(nutsCodes=nuts)) is outside


def test_single_winner_carries_its_vat_and_value_falls_back_to_its_price():
    cols, extras = tg.map_record(_award(contractValue=None, contractorPrices="1.499,9 EUR"), TODAY)
    assert cols["type"] == "auction"
    assert extras["winners"] == [{"name": "ΔΟΚΙΜΑΣΤΙΚΟΣ ΝΙΚΟΣ ΠΕΤΡΟΣ", "vat": FAKE_VAT,
                                  "price": Decimal("1499.9")}]
    assert cols["contract_value"] == Decimal("1499.9") and cols["currency_code"] == "EUR"
    assert extras["issues"] == []


def test_explicit_contract_value_wins_over_the_winner_prices():
    cols, _ = tg.map_record(_award(contractValue="7.466,00 EUR", contractorPrices="7.466,04 EUR"), TODAY)
    assert cols["contract_value"] == Decimal("7466.00")


def test_joint_award_attaches_the_single_tax_number_to_nobody():
    cols, extras = tg.map_record(_award(contractors="ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ\nΒΗΤΑ ΔΟΚΙΜΗ ΕΠΕ",
                                        contractorPrices="100 EUR\n50,5 EUR", contractValue=None), TODAY)
    assert [w["vat"] for w in extras["winners"]] == [None, None]
    assert [w["price"] for w in extras["winners"]] == [Decimal("100"), Decimal("50.5")]
    assert cols["contract_value"] == Decimal("150.5")
    assert any("joint award" in i for i in extras["issues"])


def test_a_company_on_several_lines_is_one_winner_and_misaligned_prices_are_dropped():
    _, extras = tg.map_record(_award(contractors="ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ\nΑΛΦΑ ΔΟΚΙΜΗ ΑΕ\nΒΗΤΑ ΔΟΚΙΜΗ ΕΠΕ",
                                     contractorPrices="100 EUR\n50 EUR"), TODAY)
    assert [(w["name"], w["price"]) for w in extras["winners"]] == [("ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ", None),
                                                                     ("ΒΗΤΑ ΔΟΚΙΜΗ ΕΠΕ", None)]
    assert any("contractorPrices lines 2" in i for i in extras["issues"])


def test_repeated_lines_sum_their_prices_when_aligned():
    _, extras = tg.map_record(_award(contractors="ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ\nΑΛΦΑ ΔΟΚΙΜΗ ΑΕ",
                                     contractorPrices="100 EUR\n50 EUR"), TODAY)
    assert extras["winners"] == [{"name": "ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ", "vat": FAKE_VAT, "price": Decimal("150")}]


def test_a_tax_number_that_is_not_an_afm_is_not_an_identity():
    _, extras = tg.map_record(_award(contractorStatisticalOrTaxNumber="12345"), TODAY)
    assert extras["winners"][0]["vat"] is None
    assert any("not a 9-digit" in i for i in extras["issues"])


def test_titles_are_entity_decoded():
    cols, _ = tg.map_record(_rec(title="ΠΟΛΙΤΙΣΤΙΚΟΣ &amp; ΕΞΩΡΑΪΣΤΙΚΟΣ"), TODAY)
    assert cols["title"] == "ΠΟΛΙΤΙΣΤΙΚΟΣ & ΕΞΩΡΑΪΣΤΙΚΟΣ"


def test_content_hash_ignores_bookkeeping_and_key_order():
    a = {"internalID": "1", "title": "x", "_feed": "10-tender"}
    b = {"title": "x", "internalID": "1"}
    assert tg.content_hash(a) == tg.content_hash(b)
    assert tg.content_hash({**b, "status": "EXPIRED"}) != tg.content_hash(b)


def test_window_params_end_on_the_next_day_because_to_is_exclusive():
    assert tg.window_params("pub:result:expired", DAY) == {
        "typeOfDocument": "RESULT", "status": "EXPIRED",
        "publicationDate_from": "04.08.26", "publicationDate_to": "05.08.26"}
    assert tg.window_params("upd:active", dt.date(2026, 12, 31)) == {
        "lastUpdated_from": "31.12.26", "lastUpdated_to": "01.01.27"}


@pytest.mark.parametrize("dsn,env,ok", [
    ("postgresql://postgres:pw@127.0.0.1:5433/procurement", {}, True),
    ("postgresql://u@localhost/db", {}, True),
    ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", {}, False),
    ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", {"TSG_INGEST_REMOTE": "1"}, True),
    ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", {"TSG_INGEST_REMOTE": "false"}, False),
    ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", {"TSG_INGEST_REMOTE": "yes please"}, False),
    (None, {}, False),
])
def test_database_allowed_fails_towards_local_only(dsn, env, ok):
    assert tg.database_allowed(dsn, env) is ok


# --------------------------------------------------------------------------- #
# the client, against a fake transport
# --------------------------------------------------------------------------- #
KEY = "secret-key-not-in-urls"


class FakeResp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text = status, body, text
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "headers": headers, **(params or {})})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _page(ids, total, remaining="250 requests remaining for the next 500 seconds"):
    return FakeResp(body={"total": total, "tenders": [{"internalID": str(i)} for i in ids]},
                    headers={"Rate-Limit-Remaining": remaining})


def _client(responses, **kw):
    sleeps: list = []
    c = tg.TsgClient(KEY, session=FakeSession(responses), sleep=sleeps.append, **kw)
    c.sleeps = sleeps
    return c


def test_walk_pages_by_offset_and_keeps_the_key_in_the_header():
    c = _client([_page(range(10), 13), _page(range(10, 13), 13)])
    got: list = []
    assert c.walk({"typeOfDocument": "TENDER"}, got.extend) == (13, 13)
    assert len(got) == 13
    assert [call["offset"] for call in c.session.calls] == [0, 10]
    assert all(call["max"] == 10 for call in c.session.calls)
    assert all(KEY not in str({k: v for k, v in call.items() if k != "headers"}) for call in c.session.calls)
    assert c.session.calls[0]["headers"]["X-API-Key"] == KEY


def test_walk_stops_at_the_total_without_an_empty_extra_page():
    c = _client([_page(range(10), 10)])
    assert c.walk({}, lambda page: None) == (10, 10)
    assert len(c.session.calls) == 1


def test_page_size_is_learned_from_the_servers_400():
    c = _client([FakeResp(400, text="Requested tender amount is greater than limit: 5"), _page(range(5), 5)])
    assert c.walk({}, lambda page: None) == (5, 5)
    assert [call["max"] for call in c.session.calls] == [10, 5] and c.page_size == 5


def test_over_the_offset_cap_raises_before_anything_is_stored():
    c = _client([_page(range(10), 10_001)])
    got: list = []
    with pytest.raises(tg.OverCap):
        c.walk({}, got.extend)
    assert got == []


def test_the_daily_cap_stops_without_retrying():
    c = _client([FakeResp(400, text='{"code":"customer_api_limit_reached"}')])
    with pytest.raises(tg.QuotaExhausted):
        c.walk({}, lambda page: None)
    assert len(c.session.calls) == 1


def test_the_run_budget_stops_but_keeps_the_pages_already_fetched():
    c = _client([_page(range(10), 20), _page(range(10, 20), 20)], max_requests=1)
    got: list = []
    with pytest.raises(tg.QuotaExhausted):
        c.walk({}, got.extend)
    assert len(got) == 10


def test_server_errors_and_dropped_sockets_are_retried():
    c = _client([FakeResp(500, text="boom"), requests.ConnectionError(), _page([1], 1)])
    assert c.walk({}, lambda page: None) == (1, 1)
    assert len(c.session.calls) == 3


def test_other_client_errors_are_not_retried():
    c = _client([FakeResp(404, text="nope")])
    with pytest.raises(tg.ApiError) as e:
        c.walk({}, lambda page: None)
    assert e.value.status == 404 and len(c.session.calls) == 1


def test_a_low_rate_limit_waits_for_its_reset():
    c = _client([_page(range(10), 20, remaining="15 requests remaining for the next 42 seconds"),
                 _page(range(10, 20), 20)])
    c.walk({}, lambda page: None)
    assert 42 in c.sleeps


def test_a_different_date_pattern_is_refused():
    c = _client([FakeResp(body={"dateFormat": "MM/dd/yyyy"})])
    with pytest.raises(tg.ApiError):
        c.check_date_format()


def test_commands_refuse_a_remote_database(monkeypatch):
    import db as dbmod
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres")
    monkeypatch.delenv("TSG_INGEST_REMOTE", raising=False)
    with pytest.raises(SystemExit) as e:
        dbmod.cmd_tsg_project(argparse.Namespace(limit=None))
    assert "TSG_INGEST_REMOTE" in str(e.value)


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
class FakeClient:
    """Stands in for TsgClient.walk: answer(params) → records, (records, total), or an exception."""

    def __init__(self, answer):
        self.answer, self.spent, self.params = answer, 0, []

    def check_date_format(self):
        return tg.DATE_PATTERN

    def walk(self, params, on_page):
        self.params.append(params)
        self.spent += 1
        out = self.answer(params)
        if isinstance(out, Exception):
            raise out
        recs, total = out if isinstance(out, tuple) else (out, len(out))
        if recs:
            on_page(recs)
        return total, len({r["internalID"] for r in recs})


def _by_slice(mapping):
    return lambda p: mapping.get((p.get("typeOfDocument"), p.get("status")), [])


def _one(d, sql, *params):
    rows = d.query(sql, params)
    return tuple(rows[0]) if rows else None


def _wipe(d):
    d.rollback()
    d.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
    d.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s OR adam = ANY(%s)",
              ("TSG:%", [NATIVE_ADAM, NATIVE_ADA]))
    d.execute("DELETE FROM proc.tsg_match_run WHERE true")
    d.execute("DELETE FROM proc.tsg_record WHERE true")
    d.execute("DELETE FROM proc.tsg_ingest_window WHERE true")
    d.execute("DELETE FROM proc.economic_operator WHERE vat_number LIKE %s", ("999000%",))
    d.execute("""DELETE FROM proc.authority a WHERE a.org_id LIKE %s
                 AND NOT EXISTS (SELECT 1 FROM proc.procurement_act p WHERE p.authority_id = a.org_id)""",
              ("TSG:%",))
    d.commit()


@pytest.fixture(scope="module")
def _tsg_schema(_schema):
    for rel in MIGRATIONS:
        r = subprocess.run(["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1", "-q",
                            "-f", str(ROOT / rel)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{rel} failed:\n{r.stderr[-2000:]}")


@pytest.fixture()
def tdb(_clean, _tsg_schema):
    from db import Database
    d = Database(os.environ["DATABASE_URL"])
    _wipe(d)
    d.execute("INSERT INTO proc.cpv_code (cpv_code, description) VALUES ('18424300-0', 'Γάντια') "
              "ON CONFLICT DO NOTHING")
    d.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
              "VALUES (%s, 'notice', 'Κρατημένη', 'import', 'khmdhs')", (NATIVE_ADAM,))
    d.commit()
    yield d
    _wipe(d)
    d.close()


def test_backfill_stores_everything_and_projects_only_what_we_do_not_hold(tdb):
    held = _rec(internalID="900000102", externalId=NATIVE_ADAM, dataSource="eprocurement-gov-gr")
    client = FakeClient(_by_slice({("TENDER", None): [_rec(), held], ("RESULT", None): [_award()]}))
    s = tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert s["done"] == 4 and s["stored"] == {"new": 3}
    assert s["projection"]["inserted"] == 2 and s["projection"]["held"] == 1
    assert _one(tdb, "SELECT data_source, origin FROM proc.procurement_act WHERE adam = %s",
                "TSG:900000101") == ("tsg", "import")
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = %s", "TSG:900000102") is None
    assert _one(tdb, "SELECT held_adam, projected_adam FROM proc.tsg_record WHERE internal_id = %s",
                "900000102") == (NATIVE_ADAM, None)
    assert {(p.get("typeOfDocument"), p.get("status")) for p in client.params} == {
        ("TENDER", None), ("TENDER", "EXPIRED"), ("RESULT", None), ("RESULT", "EXPIRED")}
    assert all(p["publicationDate_to"] == "05.08.26" for p in client.params)


def test_a_single_winner_is_linked_by_vat_and_an_existing_name_is_kept(tdb):
    tdb.execute("INSERT INTO proc.economic_operator (vat_number, name) VALUES (%s, 'ΟΝΟΜΑ ΑΠΟ ΚΗΜΔΗΣ')",
                (FAKE_VAT,))
    tdb.commit()
    tg.backfill(tdb, FakeClient(_by_slice({("RESULT", None): [_award()]})), DAY, DAY,
                resume=False, today=TODAY)
    assert _one(tdb, """SELECT o.vat_number, o.name, a.type::text, a.contract_value
                        FROM proc.act_operator ao
                        JOIN proc.economic_operator o ON o.operator_id = ao.operator_id
                        JOIN proc.procurement_act a ON a.adam = ao.adam
                        WHERE ao.adam = %s AND ao.role = 'winner'""",
                "TSG:900000103") == (FAKE_VAT, "ΟΝΟΜΑ ΑΠΟ ΚΗΜΔΗΣ", "auction", Decimal("430.00"))


def test_a_joint_award_links_no_operator(tdb):
    joint = _award(contractors="ΑΛΦΑ ΔΟΚΙΜΗ ΑΕ\nΒΗΤΑ ΔΟΚΙΜΗ ΕΠΕ", contractorPrices="100 EUR\n50 EUR")
    tg.backfill(tdb, FakeClient(_by_slice({("RESULT", None): [joint]})), DAY, DAY,
                resume=False, today=TODAY)
    assert _one(tdb, "SELECT count(*) FROM proc.act_operator WHERE adam = %s", "TSG:900000103") == (0,)
    assert _one(tdb, "SELECT 1 FROM proc.economic_operator WHERE vat_number = %s", FAKE_VAT) is None


def test_a_rewalk_keeps_ingested_at_and_reprojects_only_changed_records(tdb):
    answer = {("TENDER", None): [_rec()]}
    client = FakeClient(_by_slice(answer))
    tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    (ingested,) = _one(tdb, "SELECT ingested_at FROM proc.procurement_act WHERE adam = %s", "TSG:900000101")

    again = tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert again["stored"] == {"same": 1}
    assert again["projection"].get("refreshed", 0) == 0

    answer[("TENDER", None)] = [_rec(title="Νέος τίτλος &amp; άλλα")]
    changed = tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert changed["stored"] == {"changed": 1} and changed["projection"]["refreshed"] == 1
    assert _one(tdb, "SELECT title, ingested_at FROM proc.procurement_act WHERE adam = %s",
                "TSG:900000101") == ("Νέος τίτλος & άλλα", ingested)


def test_resume_skips_days_that_are_done(tdb):
    client = FakeClient(_by_slice({}))
    tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    s = tg.backfill(tdb, client, DAY, DAY, resume=True, today=TODAY)
    assert s["skipped"] == 4 and len(client.params) == 4


def test_a_projection_is_hidden_not_deleted_when_we_come_to_hold_the_act(tdb):
    rec = _rec(externalId=NATIVE_ADA + "-08-04112116", dataSource="http://opendata.diavgeia.gov.gr")
    tg.backfill(tdb, FakeClient(_by_slice({("TENDER", None): [rec]})), DAY, DAY, resume=False, today=TODAY)
    assert _one(tdb, "SELECT duplicate_of FROM proc.procurement_act WHERE adam = %s", "TSG:900000101") == (None,)

    tdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES (%s, 'notice', 'Από τη Διαύγεια', 'import', 'diavgeia')", (NATIVE_ADA,))
    tdb.commit()
    out = tg.project_all(tdb, today=TODAY)
    assert out["matching"]["transitions"] == {"new→hidden": 1}
    # Kept — a delete would take its alert history and favourites with it.
    assert _one(tdb, "SELECT duplicate_of FROM proc.procurement_act WHERE adam = %s",
                "TSG:900000101") == (NATIVE_ADA,)
    assert _one(tdb, "SELECT held_adam, match_outcome, match_rule FROM proc.tsg_record WHERE internal_id = %s",
                "900000101") == (NATIVE_ADA, "hidden", "ext_id")


def test_cyprus_is_stored_but_never_shown(tdb):
    cy = _rec(internalID="900000104", externalId=None, dataSource="www.eprocurement.gov.cy",
              nutsCodes="CY000")
    s = tg.backfill(tdb, FakeClient(_by_slice({("TENDER", None): [cy]})), DAY, DAY,
                    resume=False, today=TODAY)
    proj = {k: v for k, v in s["projection"].items() if k != "matching"}
    assert s["stored"] == {"new": 1} and proj == {"skipped (outside Greece)": 1, "rechecked": 0}
    assert s["projection"]["matching"]["outcomes"] == {"out_of_scope": 1}
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = %s", "TSG:900000104") is None
    assert _one(tdb, "SELECT skip_reason, projected_adam FROM proc.tsg_record WHERE internal_id = %s",
                "900000104") == ("outside Greece", None)


def test_reproject_applies_a_new_rule_to_records_already_shown(tdb):
    cy = _rec(internalID="900000104", nutsCodes="CY000")
    tdb.execute("""INSERT INTO proc.tsg_record (internal_id, content_hash, raw_json, projected_adam, projected_hash)
                   VALUES ('900000104', %s, %s, 'TSG:900000104', %s)""",
                (tg.content_hash(cy), tg._as_jsonb(cy), tg.content_hash(cy)))
    tdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('TSG:900000104', 'notice', 'Κύπρος', 'import', 'tsg')")
    tdb.commit()
    assert tg.project_all(tdb, today=TODAY).get("skipped (outside Greece)") is None   # unchanged: not revisited
    assert tg.project_all(tdb, today=TODAY, reproject=True)["skipped (outside Greece)"] == 1
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = %s", "TSG:900000104") is None


def test_an_authored_act_is_never_overwritten(tdb):
    tdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('TSG:900000101', 'notice', 'Επιμελημένο', 'authored', 'tsg')")
    tdb.commit()
    s = tg.backfill(tdb, FakeClient(_by_slice({("TENDER", None): [_rec()]})), DAY, DAY,
                    resume=False, today=TODAY)
    assert s["projection"]["authored"] == 1
    assert _one(tdb, "SELECT title FROM proc.procurement_act WHERE adam = %s", "TSG:900000101") == ("Επιμελημένο",)


def test_a_short_walk_is_incomplete_and_walked_again(tdb):
    client = FakeClient(_by_slice({("TENDER", None): ([_rec()], 5)}))
    s = tg.backfill(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert s["incomplete"] == 1 and s["done"] == 3
    assert _one(tdb, """SELECT status, total, fetched FROM proc.tsg_ingest_window
                        WHERE kind = 'pub:tender:active' AND day = %s""", DAY) == ("incomplete", 5, 1)
    assert tg.backfill(tdb, client, DAY, DAY, resume=True, today=TODAY)["skipped"] == 3


def test_the_quota_stops_the_run_and_leaves_the_window_pending(tdb):
    def answer(p):
        if p.get("typeOfDocument") == "RESULT":
            return tg.QuotaExhausted("the key's daily API limit is reached")
        return []
    s = tg.backfill(tdb, FakeClient(answer), DAY, DAY, resume=False, today=TODAY)
    assert s["stopped"] and s["done"] == 2
    assert _one(tdb, "SELECT status FROM proc.tsg_ingest_window WHERE kind = 'pub:result:active' AND day = %s",
                DAY) == ("pending",)
    assert _one(tdb, "SELECT 1 FROM proc.tsg_ingest_window WHERE kind = 'pub:result:expired'") is None


def test_a_day_over_the_offset_cap_is_recorded_not_truncated(tdb):
    def answer(p):
        if p.get("typeOfDocument") == "RESULT" and p.get("status") is None:
            return tg.OverCap(12_345)
        return []
    s = tg.backfill(tdb, FakeClient(answer), DAY, DAY, resume=False, today=TODAY)
    assert s["over_cap"] == 1 and s["done"] == 3
    assert _one(tdb, "SELECT status, total FROM proc.tsg_ingest_window WHERE kind = 'pub:result:active' AND day = %s",
                DAY) == ("over_cap", 12_345)


def test_catchup_treats_today_as_partial_and_rewalks_the_last_completed_day(tdb):
    client = FakeClient(lambda p: [])
    s = tg.catchup(tdb, client, today=TODAY)
    assert (s["from"], s["to"]) == (TODAY - dt.timedelta(days=1), TODAY)
    assert s["done"] == 2 and s["partial"] == 2
    assert all("lastUpdated_from" in p and "typeOfDocument" not in p for p in client.params)
    assert tg.watermark(tdb) == TODAY - dt.timedelta(days=1)

    later = TODAY + dt.timedelta(days=3)
    s2 = tg.catchup(tdb, client, today=later)
    assert s2["from"] == TODAY - dt.timedelta(days=1)
    assert tg.watermark(tdb) == later - dt.timedelta(days=1)


def test_tender_service_acts_are_out_of_analytics(tdb):
    tdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('TSG:900000199', 'notice', 'x', 'import', 'tsg')")
    tdb.commit()
    rows = dict(tdb.query("""SELECT adam, proc.is_analytics_eligible(adam, NULL, false)
                             FROM proc.procurement_act WHERE adam = ANY(%s)""",
                          (["TSG:900000199", NATIVE_ADAM],)))
    assert rows == {"TSG:900000199": False, NATIVE_ADAM: True}
