"""Re-projecting a source must not move procurement_act.ingested_at.

ingested_at is the email-digest window (digests select
`ingested_at > last_cursor AND <= now`). ted_ingest.project_all and
DiavgeiaRepository.project_all run over EVERY source row after every catch-up,
so an unconditional `ingested_at=now()` re-entered every act into the next
digest and re-mailed it. It may move only for a new act or a real content
change."""
from __future__ import annotations

import os

import pytest

PAST = "2020-01-01 00:00:00+00"
TED_PUB = "999999-2099"
TED_ADAM = "TED:" + TED_PUB
DIAV_ADA = "ΤΕΣΤ-ΙΝΓΚ-ΑΤ1"


def _purge(d):
    for sql, arg in (
        ("DELETE FROM proc.procurement_act WHERE adam IN (%s, %s)", (TED_ADAM, DIAV_ADA)),
        ("DELETE FROM proc.ted_notice WHERE publication_number = %s", (TED_PUB,)),
        ("DELETE FROM proc.diavgeia_decision WHERE ada = %s", (DIAV_ADA,)),
    ):
        d.execute(sql, arg)
    d.commit()


@pytest.fixture()
def ingest_db(_schema):
    """procurement_act is not truncated between tests, and project_all projects
    EVERY source row — including ted_notice rows other tests left behind. So
    afterwards remove every act and authority that did not exist before (child
    rows cascade); left behind they inflate other tests' search totals."""
    from db import Database
    d = Database(os.environ["DATABASE_URL"])
    _purge(d)
    acts_before = [r[0] for r in d.query("SELECT adam FROM proc.procurement_act")]
    auths_before = [r[0] for r in d.query("SELECT org_id FROM proc.authority")]
    yield d
    d.rollback()
    d.execute("DELETE FROM proc.procurement_act WHERE NOT (adam = ANY(%s))", (acts_before,))
    d.execute("DELETE FROM proc.authority WHERE NOT (org_id = ANY(%s))", (auths_before,))
    d.commit()
    _purge(d)
    d.close()


def _ingested_at(d, adam):
    rows = d.query("SELECT ingested_at, ingested_at = %s::timestamptz "
                   "FROM proc.procurement_act WHERE adam = %s", (PAST, adam))
    assert rows, f"{adam} was not projected"
    return rows[0]


def _backdate(d, adam):
    d.execute("UPDATE proc.procurement_act SET ingested_at = %s WHERE adam = %s",
              (PAST, adam))
    d.commit()


# ---- TED ------------------------------------------------------------------ #

def _ted_notice(d, title="Προμήθεια υπολογιστών", full_text="Κείμενο"):
    d.execute("""INSERT INTO proc.ted_notice
                   (publication_number, notice_type, publication_date, title,
                    buyer_name, estimated_value, html_url, full_text)
                 VALUES (%s, 'cn-standard', '2026-09-01', %s, 'Δήμος Τεστ',
                         1000, 'https://ted.example/x', %s)""",
              (TED_PUB, title, full_text))
    d.commit()


def test_ted_rerun_without_change_keeps_ingested_at(ingest_db):
    import ted_ingest as ti
    _ted_notice(ingest_db)
    ti.project_all(ingest_db)
    _backdate(ingest_db, TED_ADAM)

    ti.project_all(ingest_db)                      # what every catch-up does
    assert _ingested_at(ingest_db, TED_ADAM)[1], "unchanged act re-entered the digest window"


def test_ted_missing_full_text_in_source_keeps_ingested_at(ingest_db):
    """The projection keeps the stored full_text when the source has none
    (COALESCE), so that is not a change either."""
    import ted_ingest as ti
    _ted_notice(ingest_db)
    ti.project_all(ingest_db)
    _backdate(ingest_db, TED_ADAM)

    ingest_db.execute("UPDATE proc.ted_notice SET full_text = NULL "
                      "WHERE publication_number = %s", (TED_PUB,))
    ingest_db.commit()
    ti.project_all(ingest_db)
    assert _ingested_at(ingest_db, TED_ADAM)[1]


def test_ted_changed_title_moves_ingested_at(ingest_db):
    import ted_ingest as ti
    _ted_notice(ingest_db)
    ti.project_all(ingest_db)
    _backdate(ingest_db, TED_ADAM)

    ingest_db.execute("UPDATE proc.ted_notice SET title = 'Νέος τίτλος' "
                      "WHERE publication_number = %s", (TED_PUB,))
    ingest_db.commit()
    ti.project_all(ingest_db)
    assert not _ingested_at(ingest_db, TED_ADAM)[1], "a real change must re-enter the window"
    assert ingest_db.query("SELECT title FROM proc.procurement_act WHERE adam = %s",
                           (TED_ADAM,))[0][0] == "Νέος τίτλος"


def test_ted_authored_act_is_untouched(ingest_db):
    import ted_ingest as ti
    _ted_notice(ingest_db)
    ti.project_all(ingest_db)
    ingest_db.execute("UPDATE proc.procurement_act SET origin = 'authored', "
                      "title = 'Επιμελημένο', ingested_at = %s WHERE adam = %s",
                      (PAST, TED_ADAM))
    ingest_db.commit()

    ti.project_all(ingest_db)
    row = ingest_db.query("SELECT title, ingested_at = %s::timestamptz "
                          "FROM proc.procurement_act WHERE adam = %s", (PAST, TED_ADAM))[0]
    assert row == ("Επιμελημένο", True)


# ---- Diavgeia ------------------------------------------------------------- #

def _diav_decision(d, subject="Ανάθεση προμήθειας"):
    d.execute("""INSERT INTO proc.diavgeia_decision
                   (ada, subject, decision_type, issue_date, document_url, status, amount)
                 VALUES (%s, %s, 'Δ.2.1', '2026-09-01', 'https://diavgeia.example/x',
                         'PUBLISHED', 500)""",
              (DIAV_ADA, subject))
    d.commit()


def test_diavgeia_rerun_without_change_keeps_ingested_at(ingest_db):
    import diavgeia_ingest as di
    _diav_decision(ingest_db)
    di.project_all(ingest_db)
    assert _ingested_at(ingest_db, DIAV_ADA)[0] is not None
    _backdate(ingest_db, DIAV_ADA)

    di.project_all(ingest_db)
    assert _ingested_at(ingest_db, DIAV_ADA)[1], "unchanged act re-entered the digest window"


def test_diavgeia_changed_title_moves_ingested_at(ingest_db):
    import diavgeia_ingest as di
    _diav_decision(ingest_db)
    di.project_all(ingest_db)
    _backdate(ingest_db, DIAV_ADA)

    ingest_db.execute("UPDATE proc.diavgeia_decision SET subject = 'Νέο θέμα' "
                      "WHERE ada = %s", (DIAV_ADA,))
    ingest_db.commit()
    di.project_all(ingest_db)
    assert not _ingested_at(ingest_db, DIAV_ADA)[1], "a real change must re-enter the window"
