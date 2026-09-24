"""Attachments in production: object storage, the text cap, who may download,
and the AI summary reading attached documents.

The S3 backend is exercised against an in-memory fake with boto3's method
names — the real bucket is Supabase Storage, which no test should touch. What
must hold:
  * bytes go to the bucket, never Postgres; a failed insert takes them back out;
  * extracted text is capped per file and the cut is RECORDED, never silent;
  * files are served only to a caller with access (the act page's teaser rule);
  * the summary cites an attachment by id, and an act without attachments has
    exactly the sources (hence the cache key) it had before.
Needs TEST_DATABASE_URL for everything but the unit tests.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest

from app import ai_summary as ai
from app import attachments as att
from tests.helpers import get_csrf, grant, login, make_user

ADAM = "ATTP00000001"


class FakeS3:
    """The four calls attachments.py makes, over a dict."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[f"{Bucket}/{Key}"] = bytes(Body)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(f"{Bucket}/{Key}", None)

    def head_bucket(self, Bucket):
        return {}


@pytest.fixture()
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setenv("ATTACHMENTS_ENABLED", "1")
    monkeypatch.setattr(att, "BACKEND", "s3")
    monkeypatch.setattr(att, "_S3_BUCKET", "act-attachments")
    monkeypatch.setattr(att, "_S3_PREFIX", "")
    monkeypatch.setattr(att, "_s3_client_cache", fake)
    from app import main
    monkeypatch.setattr(main, "ATTACHMENTS_ENABLED", True)
    return fake


@pytest.fixture()
def act(db):
    c = db.cursor()
    c.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))
    c.execute("""INSERT INTO proc.procurement_act
                   (adam, type, title, origin, data_source, full_text,
                    final_submission_date, ingested_at)
                 VALUES (%s, 'notice', 'Περίληψη διακήρυξης', 'import', 'khmdhs',
                         'ΠΕΡΙΛΗΨΗ. Τα έγγραφα στο ΕΣΗΔΗΣ.', %s, now())""",
              (ADAM, dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)))
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))


def _admin(client):
    make_user("attadmin", "goodpassword1", role="admin")
    login(client, "attadmin", "goodpassword1")
    return get_csrf(client)


def _upload(client, token, name="spec.csv", body="α,β\nεγγύηση συμμετοχής,2%\n"):
    return client.post(f"/admin/act/{ADAM}/attachments",
                       headers={"X-CSRF-Token": token, "HX-Request": "true"},
                       files={"files": (name, body.encode("utf-8"), "text/csv")})


def _rows(db):
    c = db.cursor()
    c.execute("""SELECT id, storage_backend, storage_ref, extracted_text,
                        text_total_chars FROM proc.act_attachment
                  WHERE adam = %s ORDER BY id""", (ADAM,))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Storage (unit)
# --------------------------------------------------------------------------- #
def test_bytes_round_trip_through_the_bucket(s3):
    meta = att.store(ADAM, "Διακήρυξη.pdf", b"%PDF-1.7 data")
    assert meta["backend"] == "s3"
    assert meta["storage_ref"].startswith(f"{ADAM}/")
    assert att.load(meta["storage_ref"]) == b"%PDF-1.7 data"
    att.remove(meta["storage_ref"])
    assert s3.objects == {}


def test_a_file_over_the_limit_is_refused(s3, monkeypatch):
    monkeypatch.setattr(att, "MAX_BYTES", 10)
    with pytest.raises(att.AttachmentError):
        att.store(ADAM, "big.pdf", b"x" * 11)
    assert s3.objects == {}


def test_supabase_needs_path_style_addressing(monkeypatch):
    class Config:
        def __init__(self, **kw):
            self.kw = kw
    monkeypatch.delenv("ATTACH_S3_ADDRESSING", raising=False)
    monkeypatch.setattr(att, "_S3_ENDPOINT", "https://x.storage.supabase.co/storage/v1/s3")
    assert att.s3_config(Config).kw["s3"]["addressing_style"] == "path"
    monkeypatch.setattr(att, "_S3_ENDPOINT", None)          # real AWS
    assert att.s3_config(Config).kw["s3"]["addressing_style"] == "auto"


def test_the_text_cap_records_what_it_cut(monkeypatch):
    monkeypatch.setattr(att, "TEXT_MAX_CHARS", 5)
    assert att.cap_text("αβγ") == ("αβγ", None)
    assert att.cap_text("αβγδεζη") == ("αβγδε", 7)
    assert att.cap_text(None) == (None, None)


def test_storage_check_reports_a_reachable_bucket(s3):
    assert att.check_storage() == (True, "s3:act-attachments")


# --------------------------------------------------------------------------- #
# Upload (admin)
# --------------------------------------------------------------------------- #
def test_an_upload_stores_bytes_in_the_bucket_and_text_in_the_row(s3, act, client, db):
    r = _upload(client, _admin(client))
    assert r.status_code == 200
    (row,) = _rows(db)
    assert row["storage_backend"] == "s3"
    assert f"act-attachments/{row['storage_ref']}" in s3.objects
    assert "εγγύηση συμμετοχής" in row["extracted_text"]
    assert row["text_total_chars"] is None


def test_a_long_text_is_capped_and_the_cut_is_shown(s3, act, client, db, monkeypatch):
    monkeypatch.setattr(att, "TEXT_MAX_CHARS", 10)
    r = _upload(client, _admin(client), body="x" * 50)
    (row,) = _rows(db)
    assert len(row["extracted_text"]) == 10
    assert row["text_total_chars"] >= 50
    assert "κείμενο περικομμένο" in r.text


def test_a_failed_insert_takes_the_bytes_back_out(s3, act, client, db, monkeypatch):
    """Stored first, recorded second: if the row cannot be written, nothing
    would ever point at the object again."""
    monkeypatch.setattr(att, "cap_text", lambda text: (object(), None))
    token = _admin(client)
    with pytest.raises(Exception):
        _upload(client, token)
    assert s3.objects == {}
    assert _rows(db) == []


# --------------------------------------------------------------------------- #
# Who may download
# --------------------------------------------------------------------------- #
def _stored(client):
    _upload(client, _admin(client))
    client.cookies.clear()


def test_an_anonymous_visitor_is_sent_to_the_act_page(s3, act, client, db):
    _stored(client)
    aid = _rows(db)[0]["id"]
    for url in (f"/act/{ADAM}/attachment/{aid}", f"/act/{ADAM}/attachments.zip"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == f"/act/{ADAM}", url


def test_a_lapsed_customer_is_sent_to_the_act_page(s3, act, client, db):
    _stored(client)
    aid = _rows(db)[0]["id"]
    make_user("attlapsed", "goodpassword1")
    login(client, "attlapsed", "goodpassword1")
    r = client.get(f"/act/{ADAM}/attachment/{aid}", follow_redirects=False)
    assert r.status_code == 303


def test_a_subscriber_downloads_the_file(s3, act, client, db):
    _stored(client)
    aid = _rows(db)[0]["id"]
    uid = make_user("attsub", "goodpassword1")
    grant(uid)
    login(client, "attsub", "goodpassword1")
    r = client.get(f"/act/{ADAM}/attachment/{aid}")
    assert r.status_code == 200 and "εγγύηση".encode() in r.content


def test_a_zip_too_big_to_build_in_memory_is_refused(s3, act, client, db, monkeypatch):
    from app import main
    _stored(client)
    uid = make_user("attsub2", "goodpassword1")
    grant(uid)
    login(client, "attsub2", "goodpassword1")
    monkeypatch.setattr(main, "ATTACH_ZIP_MAX_MB", 0)
    assert client.get(f"/act/{ADAM}/attachments.zip").status_code == 413


def test_switched_off_the_routes_do_not_exist(act, client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "ATTACHMENTS_ENABLED", False)
    assert client.get(f"/act/{ADAM}/attachment/1").status_code == 404


# --------------------------------------------------------------------------- #
# The AI summary reads them
# --------------------------------------------------------------------------- #
def test_attachments_become_citable_sources():
    sources = ai.build_sources(
        {"full_text": "ΠΕΡΙΛΗΨΗ"}, [],
        [{"id": 7, "filename": "Διακήρυξη.pdf", "extracted_text": "Εγγύηση 2%"},
         {"id": 8, "filename": "κενό.pdf", "extracted_text": "   "}])
    assert set(sources) == {"full_text", "attachment:7"}
    assert sources["attachment:7"].startswith("Διακήρυξη.pdf\n")


def test_an_act_without_attachments_keeps_its_cache_key():
    """Adding the attachments argument must not re-key every stored summary."""
    act_row = {"full_text": "ΔΙΑΚΗΡΥΞΗ"}
    tables = [{"id": 1, "locator": "σ.1", "rows": [["α", "β"]]}]
    before = ai.input_hash(ai.build_sources(act_row, tables))
    assert ai.input_hash(ai.build_sources(act_row, tables, [])) == before


def test_load_inputs_reads_attachments_only_while_enabled(s3, act, client, db, monkeypatch):
    _upload(client, _admin(client))
    aid = _rows(db)[0]["id"]
    c = db.cursor()
    _act, sources = ai.load_inputs(c, ADAM)
    assert f"attachment:{aid}" in sources
    monkeypatch.setenv("ATTACHMENTS_ENABLED", "0")
    _act, sources = ai.load_inputs(c, ADAM)
    assert not any(k.startswith("attachment:") for k in sources)


def test_a_short_full_text_leaves_the_budget_to_the_attachment():
    """The case this exists for: a περίληψη in full_text, the real
    διακήρυξη attached. Both reach the model inside the cap."""
    sources = ai.build_sources({"full_text": "ΠΕΡΙΛΗΨΗ"}, [],
                               [{"id": 3, "filename": "d.pdf",
                                 "extracted_text": "Όροι " * 1000}])
    text, truncated = ai._render_sources(sources)
    assert "--- SOURCE: full_text ---" in text
    assert "--- SOURCE: attachment:3 ---" in text
    assert truncated is None
