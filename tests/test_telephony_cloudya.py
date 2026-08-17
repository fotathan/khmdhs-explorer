"""Tests for the NFON Cloudya CTI backend (app/telephony_cloudya.py).

Two layers:
  * pure-unit — token check, response shaping, pop target (no DB, always run);
  * endpoint  — the token gate + lookup/pop over the TestClient (needs a test DB,
    skips otherwise like the rest of the DB suite).
"""
from app import telephony_cloudya as tc


# --------------------------------------------------------------------------- #
# Auth token (constant-time, fails closed)
# --------------------------------------------------------------------------- #
def test_token_ok_matches_and_rejects():
    assert tc.token_ok("s3cret", "s3cret") is True
    assert tc.token_ok("nope", "s3cret") is False
    assert tc.token_ok("", "s3cret") is False       # no token presented
    assert tc.token_ok("s3cret", "") is False       # expected unset -> never opens
    assert tc.token_ok("", "") is False


class _FakeReq:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


def test_present_token_prefers_header_then_query():
    assert tc._present_token(_FakeReq(headers={"X-Cloudya-Token": "h"},
                                      query={"token": "q"})) == "h"
    assert tc._present_token(_FakeReq(query={"token": "q"})) == "q"
    assert tc._present_token(_FakeReq()) == ""


# --------------------------------------------------------------------------- #
# Response shaping
# --------------------------------------------------------------------------- #
def test_caller_payload_for_match():
    match = {"source": "customer", "name": "Maria", "subtitle": "Acme",
             "url": "/admin/crm/7", "customer_user_id": 7}
    p = tc.caller_payload(match, "2101234567")
    assert p["matched"] is True
    assert p["name"] == "Maria" and p["company"] == "Acme"
    assert p["url"] == "/admin/crm/7" and p["source"] == "customer"
    assert p["customer_user_id"] == 7
    assert p["raw_number"] == "2101234567"


def test_caller_payload_for_no_match():
    p = tc.caller_payload(None, "2101234567")
    assert p["matched"] is False
    assert p["name"] is None and p["company"] is None and p["url"] is None
    assert p["number"]  # normalised display number is still populated


def test_pop_target_prefers_record_then_fallback():
    assert tc.pop_target({"url": "/admin/crm/7"}) == "/admin/crm/7"
    assert tc.pop_target({"url": None}) == tc.CLOUDYA_NOMATCH_URL
    assert tc.pop_target(None) == tc.CLOUDYA_NOMATCH_URL


# --------------------------------------------------------------------------- #
# Endpoints (DB-backed; skip without TEST_DATABASE_URL via the client fixture)
# --------------------------------------------------------------------------- #
def _seed_customer(phone="2101234567", name="Maria", company="Acme"):
    from tests.helpers import make_user, connect
    uid = make_user("cloudya-caller")
    with connect() as c:
        c.execute("INSERT INTO proc.customer_profile (user_id, full_name, company, phone) "
                  "VALUES (%s,%s,%s,%s)", (uid, name, company, phone))
    return uid


def _enable(monkeypatch, token="sekret-token"):
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_ENABLED", True)
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_LOOKUP_TOKEN", token)


def test_lookup_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_ENABLED", False)
    assert client.get("/telephony/cloudya/lookup?number=2101234567").status_code == 404


def test_lookup_503_when_enabled_but_no_token_configured(client, monkeypatch):
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_ENABLED", True)
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_LOOKUP_TOKEN", "")
    r = client.get("/telephony/cloudya/lookup?number=2101234567&token=anything")
    assert r.status_code == 503  # fail closed — never serve PII without a secret


def test_lookup_401_without_valid_token(client, monkeypatch):
    _enable(monkeypatch)
    assert client.get("/telephony/cloudya/lookup?number=2101234567").status_code == 401
    assert client.get("/telephony/cloudya/lookup?number=2101234567&token=wrong"
                      ).status_code == 401


def test_lookup_returns_match_with_token(client, monkeypatch):
    _enable(monkeypatch, token="sekret-token")
    _seed_customer()
    r = client.get("/telephony/cloudya/lookup?number=2101234567",
                   headers={"X-Cloudya-Token": "sekret-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["name"] == "Maria" and body["company"] == "Acme"
    assert body["url"].startswith("/admin/crm/")


def test_pop_redirects_to_record(client, monkeypatch):
    _enable(monkeypatch, token="sekret-token")
    uid = _seed_customer(phone="2109999999")
    r = client.get("/telephony/cloudya/pop?number=2109999999&token=sekret-token",
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/admin/crm/{uid}"


def test_pop_unmatched_lands_on_fallback(client, monkeypatch):
    _enable(monkeypatch, token="sekret-token")
    monkeypatch.setattr("app.telephony_cloudya.CLOUDYA_NOMATCH_URL", "/admin/crm")
    r = client.get("/telephony/cloudya/pop?number=2100000000&token=sekret-token",
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/crm"
