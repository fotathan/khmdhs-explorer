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
              "migrations/20260917130324_tender_service_duplicates_tsg_record.sql",
              "migrations/20261002220000_analytics_sources_switch.sql"]
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


@pytest.mark.parametrize("label,act_type", [
    ("Προκήρυξη", "notice"),
    ("Αποτέλεσμα", "auction"),
    ("Σύμβαση", "contract"),
    ("Εντολή πληρωμής", "payment"),       # once folded past the 'πληρωμη' key: a notice
    ("Προηγούμενες Πληροφορίες", "prior_info"),
])
def test_every_label_we_have_a_type_for_maps_to_it(label, act_type):
    assert tg.type_of(label) == (act_type, True)
    cols, extras = tg.map_record(_rec(typeOfDocument=label), TODAY)
    assert cols["type"] == act_type
    assert not any("unknown typeOfDocument" in i for i in extras["issues"])


def test_a_label_without_a_type_is_a_counted_notice():
    cols, extras = tg.map_record(_rec(typeOfDocument="Διαβούλευση"), TODAY)
    assert cols["type"] == "notice"
    assert any("unknown typeOfDocument" in i for i in extras["issues"])


@pytest.mark.parametrize("raw,stored", [
    ("30.000,00 EUR", Decimal("30000.00")),   # Greek: '.' groups, ',' decimals
    ("2.311,60 EUR", Decimal("2311.60")),
    ("53,50", Decimal("53.50")),
    (None, None),
    ("", None),
    ("0,00 EUR", None),                         # a zero bond is no bond on record
    ("1.000,00 USD", None),                     # the filter is in euro: never mixed
])
def test_bid_bond_is_stored_in_euro_only(raw, stored):
    cols, extras = tg.map_record(_rec(bidBond=raw), TODAY)
    assert cols["bid_bond_amount"] == stored
    if raw and stored is None:
        assert any("bid bond not stored" in i for i in extras["issues"])


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
    proj = {k: v for k, v in s["projection"].items() if k not in ("matching", "bid_bonds")}
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


# --------------------------------------------------------------------------- #
# single-source trial (docs/specs/tender-service-single-source.md §5)
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _paced_client(responses, **kw):
    clock = FakeClock()
    sleeps: list = []

    def sleep(s):
        sleeps.append(s)
        clock.t += s
    c = tg.TsgClient(KEY, session=FakeSession(responses), sleep=sleep, clock=clock, **kw)
    c.sleeps = sleeps
    return c


def test_every_single_source_slice_asks_for_both_statuses():
    # A bare query means ACTIVE only: a day four weeks back loses ~94% of it.
    assert tg.SINGLE_SOURCE_SLICES
    assert all(p["status"] == "ACTIVE_AND_EXPIRED" for p in tg.SINGLE_SOURCE_SLICES.values())
    assert {p.get("typeOfDocument") for p in tg.TYPE_PUBLICATION.values()} == set(tg.DOCUMENT_TYPES)
    assert len(tg.DOCUMENT_TYPES) == 14 and "PAYMENT_ORDER" in tg.DOCUMENT_TYPES
    p = tg.window_params("pub:all", DAY)
    assert (p["publicationDate_from"], p["publicationDate_to"]) == ("04.08.26", "05.08.26")
    assert "lastUpdated_from" not in p


def test_a_date_filter_never_carries_a_time():
    # A time makes the API ignore the filter and answer with the whole archive.
    for kind in tg.SLICES:
        for k, v in tg.window_params(kind, DAY).items():
            if k.endswith(("_from", "_to")):
                assert len(v) == 8 and " " not in v and "T" not in v


def test_requests_are_spaced_by_the_minimum_interval():
    c = _paced_client([_page(range(10), 30), _page(range(10, 20), 30), _page(range(20, 30), 30)],
                      min_interval=4.0)
    c.walk({}, lambda page: None)
    assert c.sleeps == [4.0, 4.0]


def test_no_interval_means_no_pacing_sleep():
    c = _paced_client([_page(range(10), 20), _page(range(10, 20), 20)])
    c.walk({}, lambda page: None)
    assert c.sleeps == []


def test_a_429_waits_the_whole_reset_not_the_short_backoff():
    c = _paced_client([FakeResp(429, text="slow down",
                                headers={"Rate-Limit-Remaining": "All requests consumed, so it will take 90 seconds to reset"}),
                       _page([1], 1)])
    assert c.walk({}, lambda page: None) == (1, 1)
    assert c.sleeps == [90]


def test_a_429_without_a_reset_waits_at_least_a_minute():
    c = _paced_client([FakeResp(429, text="slow down"), _page([1], 1)])
    c.walk({}, lambda page: None)
    assert c.sleeps == [tg.TOO_MANY_WAIT]


def test_repeated_429s_stop_the_run_instead_of_knocking_again():
    c = _paced_client([FakeResp(429, text="slow down")] * tg.MAX_ATTEMPTS)
    with pytest.raises(tg.QuotaExhausted):
        c.walk({}, lambda page: None)
    assert len(c.session.calls) == tg.MAX_ATTEMPTS


def _ss_answer(per_day):
    """per_day: day-string → {None: answer for the all-types walk, 'TENDER': ..., ...}."""
    def answer(p):
        return per_day.get(p["publicationDate_from"], {}).get(p.get("typeOfDocument"), [])
    return answer


def test_single_source_walks_newest_day_first_once_per_day_and_stores_only(tdb):
    d1, d2 = DAY, DAY + dt.timedelta(days=1)
    client = FakeClient(_ss_answer({"04.08.26": {None: [_rec()]},
                                    "05.08.26": {None: [_award(publicationDate="05.08.26")]}}))
    s = tg.backfill_single_source(tdb, client, d1, d2, resume=False, today=TODAY)
    assert [p["publicationDate_from"] for p in client.params] == ["05.08.26", "04.08.26"]
    assert all(p["status"] == "ACTIVE_AND_EXPIRED" and "typeOfDocument" not in p for p in client.params)
    assert s["done"] == 2 and s["stored"] == {"new": 2} and s["split_days"] == 0
    # Stored, not projected: projection is the offline step.
    assert _one(tdb, "SELECT count(*) FROM proc.tsg_record") == (2,)
    assert _one(tdb, "SELECT count(*) FROM proc.procurement_act WHERE adam LIKE 'TSG:%%'") == (0,)
    assert tg.backfill_single_source(tdb, client, d1, d2, resume=True, today=TODAY)["skipped"] == 2
    assert len(client.params) == 2


def test_a_day_over_the_cap_is_walked_per_type_and_the_gap_recorded(tdb):
    day = {None: tg.OverCap(12_000), "TENDER": ([_rec()], 1),
           "RESULT": ([_award()], 1)}
    client = FakeClient(_ss_answer({"04.08.26": day}))
    s = tg.backfill_single_source(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert s["split_days"] == 1 and s["over_cap"] == 0 and s["done"] == 14
    assert {p.get("typeOfDocument") for p in client.params} == {None, *tg.DOCUMENT_TYPES}
    assert s["type_gap"] == {"2026-08-04": 12_000 - 2}
    assert "gap 11998" in _one(tdb, "SELECT last_error FROM proc.tsg_ingest_window "
                                    "WHERE kind = 'pub:all' AND day = %s", DAY)[0]
    # Resume: the split day is finished, so nothing is asked again — not even the all-types page.
    n = len(client.params)
    assert tg.backfill_single_source(tdb, client, DAY, DAY, resume=True, today=TODAY)["skipped"] == 14
    assert len(client.params) == n


def test_a_split_day_stopped_halfway_resumes_with_the_missing_types_only(tdb):
    calls = {"n": 0}

    def answer(p):
        if p.get("typeOfDocument") is None:
            return tg.OverCap(12_000)
        calls["n"] += 1
        if calls["n"] == 3:
            return tg.QuotaExhausted("budget")
        return []
    client = FakeClient(answer)
    s = tg.backfill_single_source(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert s["stopped"]
    before = len(client.params)
    calls["n"] = 100   # no more stops
    s2 = tg.backfill_single_source(tdb, client, DAY, DAY, resume=True, today=TODAY)
    asked = [p.get("typeOfDocument") for p in client.params[before:]]
    assert None not in asked                      # the over-cap answer is remembered
    assert len(asked) == 14 - 2                   # the two types already done are skipped
    assert s2["split_days"] == 1


def test_an_empty_day_is_not_believed_and_walked_again(tdb):
    client = FakeClient(lambda p: [])
    s = tg.backfill_single_source(tdb, client, DAY, DAY, resume=False, today=TODAY)
    assert s["errored"] == 1 and s["done"] == 0
    assert _one(tdb, "SELECT status FROM proc.tsg_ingest_window WHERE kind = 'pub:all' AND day = %s",
                DAY) == ("error",)
    tg.backfill_single_source(tdb, client, DAY, DAY, resume=True, today=TODAY)
    assert len(client.params) == 2


# --------------------------------------------------------------------------- #
# single-source projection (spec §3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ext,url,key,kind", [
    ("26PROC019000777", None, "26PROC019000777", "adam"),
    ("94ΩΕ46907Τ-ΚΜΗ-09-11143505", None, "94ΩΕ46907Τ-ΚΜΗ", "ada"),
    ("TED.00538898-2026", None, "TED:538898-2026", "ted"),
    ("attik-26-0000001", None, "TSG:900000101", "tsg"),
    (None, None, "TSG:900000101", "tsg"),
])
def test_the_single_source_key_is_the_id_our_other_ingesters_use(ext, url, key, kind):
    assert tg.single_source_key("900000101", {"externalId": ext, "sourceUrl": url}) == (key, kind)


@pytest.mark.parametrize("adam,typ", [("26REQ019700909", "request"), ("26PROC019739253", "notice"),
                                      ("26AWRD019000001", "auction"), ("26SYMV019000001", "contract"),
                                      ("26PAY019000001", "payment")])
def test_the_adam_kind_decides_the_type_before_the_label(adam, typ):
    assert tg.single_source_type(adam, "adam", {"typeOfDocument": "Προκήρυξη"}) == (typ, True)


@pytest.mark.parametrize("key,kind", [("26REQ019700909", "adam"), ("26PROC019739253", "adam"),
                                      ("ΨΨΨΨ46ΜΤΛΡ-ΑΒΓ", "ada"), ("TSG:1", "tsg")])
def test_prior_information_is_its_own_type_whatever_the_number(key, kind):
    r = {"typeOfDocument": "Προηγούμενες Πληροφορίες"}
    assert tg.single_source_type(key, kind, r) == ("prior_info", True)


def test_without_an_adam_the_label_decides_and_an_unknown_one_is_counted():
    assert tg.single_source_type("ΨΨΨΨ46ΜΤΛΡ-ΑΒΓ", "ada", {"typeOfDocument": "Αποτέλεσμα"}) == ("auction", True)
    assert tg.single_source_type("TSG:1", "tsg", {"typeOfDocument": "Κάτι νέο"}) == ("notice", False)


def _store(d, *recs):
    for r in recs:
        tg.upsert_record(d, r, TODAY)
    d.commit()


def test_single_source_projection_keys_acts_by_their_own_ids(tdb):
    adam = "26PROC019000777"
    _store(tdb, _rec(internalID="900000201", externalId=adam, dataSource="eprocurement-gov-gr"),
           _award(internalID="900000202"), _rec(internalID="900000203"))
    out = tg.project_single_source(tdb, today=TODAY)
    assert out["inserted:adam"] == 1 and out["inserted:ada"] == 1 and out["inserted:tsg"] == 1
    assert _one(tdb, "SELECT type, data_source FROM proc.procurement_act WHERE adam = %s", adam) == ("notice", "tsg")
    assert _one(tdb, "SELECT type FROM proc.procurement_act WHERE adam = %s", "9ΖΖΖΖ46-ΑΒΓ") == ("auction",)
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = %s", "TSG:900000203") == (1,)
    assert _one(tdb, "SELECT projected_adam FROM proc.tsg_record WHERE internal_id = %s", "900000201") == (adam,)
    assert tg.project_single_source(tdb, today=TODAY) == {"unknown_labels": {}}   # nothing changed
    tdb.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([adam, "9ΖΖΖΖ46-ΑΒΓ"],))
    tdb.commit()


def test_two_records_with_one_key_keep_the_first_and_record_the_second(tdb):
    adam = "26PROC019000778"
    _store(tdb, _rec(internalID="900000211", externalId=adam, publicationDate="05.08.26"),
           _rec(internalID="900000212", externalId=adam, publicationDate="04.08.26"))
    out = tg.project_single_source(tdb, today=TODAY)
    assert out["inserted:adam"] == 1 and out["same_key"] == 1
    # Newest publication first, so the same record wins every run.
    assert _one(tdb, "SELECT projected_adam FROM proc.tsg_record WHERE internal_id = '900000211'") == (adam,)
    assert _one(tdb, "SELECT skip_reason FROM proc.tsg_record WHERE internal_id = '900000212'") == (
        f"same key {adam} as 900000211",)
    tdb.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (adam,))
    tdb.commit()


def test_a_corrected_external_id_moves_the_act_to_its_new_key(tdb):
    _store(tdb, _rec(internalID="900000221"))
    tg.project_single_source(tdb, today=TODAY)
    _store(tdb, _rec(internalID="900000221", externalId="26PROC019000779", title="Διορθωμένο"))
    tg.project_single_source(tdb, today=TODAY)
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = 'TSG:900000221'") is None
    assert _one(tdb, "SELECT title FROM proc.procurement_act WHERE adam = '26PROC019000779'") == ("Διορθωμένο",)
    tdb.execute("DELETE FROM proc.procurement_act WHERE adam = '26PROC019000779'")
    tdb.commit()


def test_single_source_projection_keeps_cyprus_out(tdb):
    _store(tdb, _rec(internalID="900000231", nutsCodes="CY000"))
    assert tg.project_single_source(tdb, today=TODAY)["out_of_scope"] == 1
    assert _one(tdb, "SELECT 1 FROM proc.procurement_act WHERE adam = 'TSG:900000231'") is None


def test_tender_service_counts_for_analytics_only_where_the_database_switches_it_on(tdb):
    tdb.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('TSG:900000198', 'notice', 'x', 'import', 'tsg')")
    tdb.commit()
    eligible = """SELECT proc.is_analytics_eligible('TSG:900000198', NULL, false),
                         proc.is_analytics_eligible(%s, NULL, false)"""
    assert _one(tdb, "SELECT proc.analytics_sources()") == (["khmdhs", "manual"],)
    assert _one(tdb, eligible, NATIVE_ADAM) == (False, True)       # off by default: still an allowlist
    tdb.execute("SET khmdhs.single_source = 'on'")
    try:
        assert _one(tdb, "SELECT proc.analytics_sources()") == (["khmdhs", "manual", "tsg"],)
        assert _one(tdb, eligible, NATIVE_ADAM) == (True, True)
        tdb.execute("SET khmdhs.single_source = 'yes'")              # only the exact word turns it on
        assert _one(tdb, eligible, NATIVE_ADAM) == (False, True)
    finally:
        tdb.execute("RESET khmdhs.single_source")
        tdb.commit()


def test_the_analytics_switch_migration_has_no_do_blocks():
    sql = (ROOT / "migrations/20261002220000_analytics_sources_switch.sql").read_text(encoding="utf-8")
    code = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    assert "DO $$" not in code and "ALTER DATABASE" not in code


# bid bonds copied onto the act we show (sync_bid_bonds)
# --------------------------------------------------------------------------- #
def _held(internal="900000102", bond="2.400,00 EUR", **over):
    return _rec(internalID=internal, externalId=NATIVE_ADAM, dataSource="eprocurement-gov-gr",
                bidBond=bond, **over)


def _bond(d, adam=NATIVE_ADAM):
    return _one(d, "SELECT bid_bond_amount FROM proc.procurement_act WHERE adam = %s", adam)[0]


def _ledger(d, adam=NATIVE_ADAM):
    return _one(d, "SELECT amount, internal_ids FROM proc.act_bid_bond_fill WHERE adam = %s", adam)


def _project(d, *recs):
    tg.backfill(d, FakeClient(_by_slice({("TENDER", None): list(recs)})), DAY, DAY,
                resume=False, today=TODAY)


def _undo_match(d, internal="900000102"):
    d.execute("UPDATE proc.tsg_record SET match_outcome = 'flagged' WHERE internal_id = %s",
              (internal,))
    d.commit()


def test_an_exact_duplicates_bid_bond_fills_our_act_once(tdb):
    _project(tdb, _held())
    assert _bond(tdb) == Decimal("2400.00")
    assert _ledger(tdb) == (Decimal("2400.00"), ["900000102"])
    assert tg.sync_bid_bonds(tdb) == {"conflict": 0, "filled": 0, "reverted": 0,
                                      "released": 0, "kept": 1}


def test_a_value_already_on_our_act_is_never_overwritten(tdb):
    tdb.execute("UPDATE proc.procurement_act SET bid_bond_amount = 999 WHERE adam = %s", (NATIVE_ADAM,))
    tdb.commit()
    _project(tdb, _held())
    assert _bond(tdb) == Decimal("999")
    assert _ledger(tdb) is None


def test_copies_that_disagree_write_nothing(tdb):
    _project(tdb, _held(), _held(internal="900000105", bond="2.400,40 EUR"))
    assert _bond(tdb) is None
    assert tg.sync_bid_bonds(tdb)["conflict"] == 1


def test_copies_that_agree_fill_once_and_name_both(tdb):
    _project(tdb, _held(), _held(internal="900000105"))
    assert _ledger(tdb) == (Decimal("2400.00"), ["900000102", "900000105"])


def test_an_undone_match_takes_its_bond_back(tdb):
    _project(tdb, _held())
    _undo_match(tdb)
    assert tg.sync_bid_bonds(tdb)["reverted"] == 1
    assert _bond(tdb) is None and _ledger(tdb) is None


def test_an_admins_later_edit_survives_an_undone_match(tdb):
    _project(tdb, _held())
    tdb.execute("UPDATE proc.procurement_act SET bid_bond_amount = 5000 WHERE adam = %s", (NATIVE_ADAM,))
    tdb.commit()
    _undo_match(tdb)
    assert tg.sync_bid_bonds(tdb)["released"] == 1
    assert _bond(tdb) == Decimal("5000") and _ledger(tdb) is None


def test_a_changed_amount_replaces_the_one_we_wrote(tdb):
    _project(tdb, _held())
    tdb.execute("""UPDATE proc.tsg_record SET raw_json = jsonb_set(raw_json, '{bidBond}', '"3.000,00 EUR"')
                   WHERE internal_id = '900000102'""")
    tdb.commit()
    out = tg.sync_bid_bonds(tdb)
    assert out["reverted"] == 1 and out["filled"] == 1
    assert _ledger(tdb) == (Decimal("3000.00"), ["900000102"])


def test_a_fuzzy_flag_never_feeds_another_sources_act(tdb):
    # A notice of ours without the identity link: projected as its own act, never hidden.
    _project(tdb, _rec(bidBond="2.400,00 EUR"))
    assert _bond(tdb) is None
    assert _bond(tdb, "TSG:900000101") == Decimal("2400.00")


# --------------------------------------------------------------------------- #
# values: total_cost_with_vat / _without_vat (what every page sums)
# --------------------------------------------------------------------------- #
def _vals(r):
    cols, _ = tg.map_record(r, TODAY)
    return cols["total_cost_without_vat"], cols["total_cost_with_vat"]


def test_an_estimate_without_vat_gets_its_rate_added():
    assert _vals(_rec(estimatedPricesBelow="1.000,00 EUR", estimatedPricesBelowVatIncluded="Όχι",
                      estimatedPricesBelowVatRate="24.0")) == (Decimal("1000.00"), Decimal("1240.00"))


def test_an_estimate_with_vat_included_is_split_back():
    assert _vals(_rec(estimatedPricesBelow="1.240,00 EUR", estimatedPricesBelowVatIncluded="Ναι",
                      estimatedPricesBelowVatRate="24.0")) == (Decimal("1000.00"), Decimal("1240.00"))


def test_lots_are_summed_each_with_its_own_rate():
    # A multi-lot notice used to get no value at all (the parser read one line).
    r = _rec(estimatedPrices=None, estimatedPricesBelow="100,00 EUR\n200,00 EUR",
             estimatedPricesBelowVatIncluded="Όχι\nΌχι", estimatedPricesBelowVatRate="24.0\n13.0")
    assert _vals(r) == (Decimal("300.00"), Decimal("350.00"))      # 124 + 226


def test_a_lot_whose_vat_is_unknown_empties_the_total_rather_than_understate_it():
    # Three lots, two rates: which lot has which is unknowable.
    r = _rec(estimatedPrices=None, estimatedPricesBelow="100,00 EUR\n200,00 EUR\n50,00 EUR",
             estimatedPricesBelowVatIncluded="Όχι", estimatedPricesBelowVatRate="24.0\n13.0")
    assert _vals(r) == (Decimal("350.00"), None)


def test_one_stated_rate_applies_to_every_lot():
    r = _rec(estimatedPrices=None, estimatedPricesBelow="100,00 EUR\n200,00 EUR",
             estimatedPricesBelowVatIncluded="Όχι", estimatedPricesBelowVatRate="24.0")
    assert _vals(r) == (Decimal("300.00"), Decimal("372.00"))


def test_an_unflagged_amount_is_net_except_from_diavgeia_which_never_says():
    assert _vals(_rec(estimatedPrices="500,00 EUR")) == (Decimal("500.00"), None)
    assert _vals(_rec(estimatedPrices="500,00 EUR", dataSource="http://opendata.diavgeia.gov.gr")) == (
        Decimal("500.00"), Decimal("500.00"))


def test_an_award_is_worth_its_awarded_value_not_its_estimate():
    r = _award(contractValue="430,00 EUR", contractVatIncluded="NO", contractVatRate="24.0",
               estimatedPricesBelow="9.999,00 EUR", estimatedPricesBelowVatIncluded="Όχι",
               estimatedPricesBelowVatRate="24.0")
    cols, _ = tg.map_record(r, TODAY)
    assert (cols["total_cost_without_vat"], cols["total_cost_with_vat"]) == (Decimal("430.00"), Decimal("533.20"))
    assert cols["budget"] == Decimal("9999.00")                  # the estimate stays the budget
    assert cols["contract_value"] == Decimal("430.00")


def test_payments_and_contracts_take_the_awarded_value_too():
    for label, t in (("Εντολή πληρωμής", "payment"), ("Σύμβαση", "contract")):
        cols, _ = tg.map_record(_rec(typeOfDocument=label, contractValue="1.000,00 EUR",
                                     contractVatIncluded="NO", contractVatRate="13.0"), TODAY)
        assert cols["type"] == t
        assert cols["total_cost_with_vat"] == Decimal("1130.00")
