"""Tender Service preview import (tsg_preview_import.py) + the tsg source badge.

The preview writes into procurement_act, so what matters is: it never shows a
procurement twice (records we already hold are skipped), it never reaches a
database that is not local, a re-run does not re-enter anyone's digest window
(ingested_at stays put), and --delete leaves nothing behind. The pure mapping
tests pin the Greek number and date conventions the real ingester will reuse.
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest

import tsg_preview_import as tp


# --------------------------------------------------------------------------- #
# pure mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("3.145 EUR", (Decimal("3145"), None, "EUR")),            # dot groups thousands
    ("2.756,5 EUR", (Decimal("2756.5"), None, "EUR")),
    ("41.109,00 EUR", (Decimal("41109.00"), None, "EUR")),
    ("345.200,00 EUR/1.113.100,00 EUR", (Decimal("345200.00"), Decimal("1113100.00"), "EUR")),
    ("1.160,00 EUR/1.160,00 EUR", (Decimal("1160.00"), None, "EUR")),   # a point estimate, twice
    ("", (None, None, None)),
    ("n/a", (None, None, None)),
])
def test_parse_amount(raw, expected):
    assert tp.parse_amount(raw) == expected


def test_parse_date_keeps_time_and_zone():
    d = tp.parse_date("13.08.27 16:00", today=dt.date(2026, 9, 15))
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2027, 8, 13, 16, 0)
    assert d.utcoffset() == dt.timedelta(hours=3)             # Athens summer time


@pytest.mark.parametrize("raw", ["12.11.55", "15.03.91", "31.02.26", "garbage", ""])
def test_parse_date_refuses_impossible_years_and_days(raw):
    # '12.11.55' is a hand-entered archive record that expired in 2020.
    assert tp.parse_date(raw, today=dt.date(2026, 9, 15)) is None


def test_parse_date_iso():
    assert tp.parse_date("2027-03-11T00:00:00", today=dt.date(2026, 9, 15)).date() == dt.date(2027, 3, 11)


def test_identifiers():
    assert tp.adam_of("26PROC019577171") == "26PROC019577171"
    assert tp.adam_of("eproc-516060") is None
    assert tp.ada_of("94ΩΕ46907Τ-ΚΜΗ-09-11143505") == "94ΩΕ46907Τ-ΚΜΗ"
    assert tp.ada_of("9ΥΠΓΩ6Ζ-ΓΝ8-09-11084123") == "9ΥΠΓΩ6Ζ-ΓΝ8"
    assert tp.ada_of("attik-26-0005959-110926") is None


def test_type_labels():
    assert tp.type_of("Προκήρυξη") == ("notice", True)
    assert tp.type_of("Αποτέλεσμα") == ("auction", True)
    assert tp.type_of("Κάτι άλλο") == ("notice", False)


def _record(**over):
    r = {
        "internalID": "900000001", "externalId": "attik-26-0005959-110926",
        "dataSource": "attikonhospital.gr", "typeOfDocument": "Προκήρυξη",
        "title": "Προμήθεια γαντιών", "contentDescription": "Προμήθεια γαντιών",
        "publicationDate": "04.08.26", "deadlineDate": "14.09.26 13:15",
        "estimatedPrices": "12.400,00 EUR", "cpvCodes": "18424300-0 33141420",
        "nutsCodes": "EL303", "authorities": "ΠΓΝ ΑΤΤΙΚΟΝ", "authorityOrgdbId": "5518.0",
        "tenderText": "<div><b>Αντικείμενο</b></div><div>Γάντια</div>"
                      "<table><tr><th>Είδος</th><th>Ποσότητα</th></tr><tr><td>Γάντια</td><td>100</td></tr></table>",
        "status": "ACTIVE",
    }
    r.update(over)
    return r


def test_map_record_columns():
    cols, extras = tp.map_record(_record(), today=dt.date(2026, 9, 15))
    assert cols["adam"] == "TSG:900000001" and cols["data_source"] == "tsg" and cols["origin"] == "import"
    assert cols["type"] == "notice"
    assert cols["budget"] == Decimal("12400.00") and cols["currency_code"] == "EUR"
    assert cols["short_description"] is None                  # same as the title
    assert cols["nuts_code"] == "EL303"
    assert "<table>" in cols["full_text_html"] and 'data-row="r1"' in cols["full_text_html"]
    assert "Είδος | Ποσότητα" in cols["full_text"]
    assert extras["cpvs"] == ["18424300", "33141420"]
    assert extras["authority"]["orgdb_id"] == "5518"          # the exporter's '.0' stripped
    assert extras["issues"] == []


def test_only_local_databases():
    assert tp.is_local("postgresql://postgres:pw@127.0.0.1:5433/procurement")
    assert tp.is_local("postgresql://u@localhost/db")
    assert not tp.is_local("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres")


def test_main_refuses_a_remote_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres")
    assert tp.main(["--dry-run"]) == 2


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
@pytest.fixture()
def conn(_clean):
    import psycopg
    c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)
    cur = c.cursor()
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description) VALUES ('18424300-0', 'Γάντια') "
                "ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('26PROC019571715', 'notice', 'Κρατημένη', 'import', 'khmdhs') ON CONFLICT DO NOTHING")
    yield c
    tp.run_delete(c)
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = '26PROC019571715'")
    c.close()


def test_import_skips_held_refreshes_quietly_and_deletes_cleanly(conn):
    held = _record(internalID="900000002", externalId="26PROC019571715", dataSource="eprocurement-gov-gr")
    first = tp.run_import(conn, [_record(), held], today=dt.date(2026, 9, 15))
    assert first["stats"]["inserted"] == 1
    assert first["skipped"] == {"already held (ΑΔΑΜ)": 1}

    cur = conn.cursor()
    cur.execute("""SELECT a.data_source, a.full_text_html, a.authority_id, a.ingested_at,
                          (SELECT count(*) FROM proc.act_object_detail od
                             JOIN proc.object_detail_cpv c ON c.object_detail_id = od.id
                            WHERE od.adam = a.adam)
                   FROM proc.procurement_act a WHERE a.adam = 'TSG:900000001'""")
    source, html, authority, ingested, cpv_links = cur.fetchone()
    assert source == "tsg" and "<table>" in html
    assert authority == "TSG:5518"
    assert cpv_links == 1                                     # 33141420 is not in the test catalogue

    again = tp.run_import(conn, [_record(title="Νέος τίτλος")], today=dt.date(2026, 9, 15))
    assert again["stats"]["refreshed"] == 1
    cur.execute("SELECT title, ingested_at FROM proc.procurement_act WHERE adam = 'TSG:900000001'")
    title, ingested_again = cur.fetchone()
    assert title == "Νέος τίτλος"
    assert ingested_again == ingested                         # not back in a digest window

    gone = tp.run_delete(conn)
    assert gone == {"acts deleted": 1, "authorities deleted": 1}
    cur.execute("SELECT count(*) FROM proc.procurement_act WHERE adam LIKE 'TSG:%'")
    assert cur.fetchone()[0] == 0


def test_import_never_overwrites_an_authored_act(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO proc.procurement_act (adam, type, title, origin, data_source) "
                "VALUES ('TSG:900000001', 'notice', 'Επιμελημένο', 'authored', 'tsg')")
    tp.run_import(conn, [_record()], today=dt.date(2026, 9, 15))
    cur.execute("SELECT title FROM proc.procurement_act WHERE adam = 'TSG:900000001'")
    assert cur.fetchone()[0] == "Επιμελημένο"
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'TSG:900000001'")


# --------------------------------------------------------------------------- #
# the app: badge, rich text on the act page, and no public facet
# --------------------------------------------------------------------------- #
def test_tsg_act_page_shows_badge_and_styled_rich_text(client, db):
    from tests.helpers import login, make_user
    cur = db.cursor()
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text, full_text_html)
                   VALUES ('TSG:BADGE-1', 'notice', 'Δοκιμή', 'import', 'tsg', 'x | y',
                           '<table><tbody><tr><td data-row="r1">x</td><td data-row="r1">y</td></tr></tbody></table>')""")
    make_user("tsgadmin", "goodpassword1", role="admin")
    login(client, "tsgadmin", "goodpassword1")
    r = client.get("/act/TSG:BADGE-1", follow_redirects=False)
    assert r.status_code == 200
    assert "src-badge src-tsg" in r.text
    # Provenance and the identifier label must not claim KHMDHS / a ΑΔΑΜ.
    assert "<dd>Tender Service</dd>" in r.text
    assert "<dt>Tender Service</dt>" in r.text
    assert 'class="full-text-html rich-text"' in r.text
    assert "/static/css/rich_text.css" in r.text
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'TSG:BADGE-1'")


def test_tsg_is_displayed_but_not_a_public_facet():
    from app import main
    assert "tsg" not in main._SOURCE_LABELS                   # sitemap + indexable facets
    assert main._SOURCE_DISPLAY["tsg"] == "Tender Service"
