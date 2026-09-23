"""Customer mobile lookups, canonical search, cursor paging and safe detail."""
from __future__ import annotations

import datetime as dt

import pytest

from tests.helpers import expire_sub, grant, login as web_login, make_user
from tests.test_mobile_api_auth import DEVICE


PREFIX = "MOBILE-SLICE-B-"


def _login(client, username: str, password: str = "pw-123456") -> dict:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": password, "device": DEVICE,
    })
    assert response.status_code == 200
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def mobile_acts(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s", (PREFIX + "%",))
    cur.execute("""INSERT INTO proc.authority (org_id, name)
                   VALUES ('MOBILE-AUTH', 'Mobile Test Authority')
                   ON CONFLICT (org_id) DO UPDATE SET name=excluded.name""")
    base = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)
    for index, value in enumerate((1000, 2000, 3000), start=1):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, authority_id,
                          total_cost_with_vat, submission_date, ingested_at,
                          full_text, raw_json, contract_type_code)
                       VALUES (%s, 'notice', %s, 'import', 'khmdhs',
                               'MOBILE-AUTH', %s, %s, %s, %s, %s, '1')""",
                    (f"{PREFIX}{index}", f"mobilesliceword result {index}", value,
                     base + dt.timedelta(days=index),
                     base + dt.timedelta(hours=index),
                     "private mobile full text", '{"internal":"never expose"}'))
    from app import main
    main._lookup_cache.clear()
    yield
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s", (PREFIX + "%",))
    cur.execute("DELETE FROM proc.authority WHERE org_id='MOBILE-AUTH'")
    main._lookup_cache.clear()


@pytest.fixture()
def entitled_token(client):
    uid = make_user("mobile_reader")
    grant(uid)
    return _login(client, "mobile_reader")["access_token"]


def test_lookups_are_localized_etagged_and_available_before_entitlement(
    client, mobile_acts,
):
    make_user("lookup_reader")
    token = _login(client, "lookup_reader")["access_token"]
    first = client.get(
        "/api/v1/lookups",
        headers={**_bearer(token), "Accept-Language": "en"},
    )
    assert first.status_code == 200
    assert first.headers["etag"].startswith('"mobile-lookups-')
    body = first.json()
    assert body["language"] == "en"
    assert any(item["code"] == "MOBILE-AUTH" for item in body["authorities"])
    assert all(item["kind"] in {"category", "subcategory"}
               and item["id"].isdigit() for item in body["categories"])

    unchanged = client.get(
        "/api/v1/lookups",
        headers={**_bearer(token), "Accept-Language": "en",
                 "If-None-Match": first.headers["etag"]},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""


def test_mobile_and_browser_search_share_filter_semantics(
    client, mobile_acts, entitled_token,
):
    mobile = client.post(
        "/api/v1/acts/search",
        headers=_bearer(entitled_token),
        json={
            "filters": {"q": "mobilesliceword", "types": ["notice"]},
            "sort": "value_desc", "limit": 20,
        },
    )
    assert mobile.status_code == 200
    assert mobile.headers["cache-control"] == "no-store"
    mobile_adams = [item["adam"] for item in mobile.json()["items"]]

    browser = client.get(
        "/?q=mobilesliceword&type=notice&sort=value&per_page=20",
        headers={"Accept": "application/json"},
    )
    assert browser.status_code == 200
    browser_adams = [item["adam"] for item in browser.json()["results"]]
    assert mobile_adams == browser_adams
    assert mobile.json()["totals"] == {
        "count": 3, "value": {"amount": "6000.00", "currency": "EUR"},
    }


def test_search_rejects_unknown_vocabulary_and_invalid_limits(
    client, mobile_acts, entitled_token,
):
    unknown = client.post(
        "/api/v1/acts/search", headers=_bearer(entitled_token),
        json={"filters": {"types": ["unknown-act-type"]}},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"
    assert unknown.json()["error"]["fields"] == [
        {"field": "filters.types", "code": "unknown_value"}
    ]

    too_many = client.post(
        "/api/v1/acts/search", headers=_bearer(entitled_token),
        json={"filters": {}, "limit": 51},
    )
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "validation_error"


def test_lapsed_customer_can_read_me_and_lookups_but_not_customer_data(client):
    uid = make_user("lapsed_mobile_reader")
    expire_sub(uid)
    token = _login(client, "lapsed_mobile_reader")["access_token"]
    headers = _bearer(token)
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    assert client.get("/api/v1/lookups", headers=headers).status_code == 200
    denied = client.post("/api/v1/acts/search", headers=headers,
                         json={"filters": {}})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_expired"


def test_snapshot_cursor_has_no_duplicates_and_excludes_new_ingestion(
    client, db, mobile_acts, entitled_token,
):
    payload = {
        "filters": {"q": "mobilesliceword"},
        "sort": "submission_date", "limit": 2,
    }
    first = client.post("/api/v1/acts/search", headers=_bearer(entitled_token),
                        json=payload)
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"]

    db.cursor().execute("""INSERT INTO proc.procurement_act
        (adam,type,title,origin,data_source,submission_date,ingested_at)
        VALUES (%s,'notice','mobilesliceword inserted later','import','khmdhs',
                now() + interval '1 day', now())""", (PREFIX + "NEW",))

    second_payload = {**payload, "cursor": first_body["next_cursor"]}
    second = client.post("/api/v1/acts/search", headers=_bearer(entitled_token),
                         json=second_payload)
    assert second.status_code == 200
    all_adams = ([item["adam"] for item in first_body["items"]]
                 + [item["adam"] for item in second.json()["items"]])
    assert len(all_adams) == len(set(all_adams)) == 3
    assert PREFIX + "NEW" not in all_adams
    assert second.json()["totals"]["count"] == 3

    tampered = first_body["next_cursor"][:-1] + (
        "A" if first_body["next_cursor"][-1] != "A" else "B")
    bad = client.post(
        "/api/v1/acts/search", headers=_bearer(entitled_token),
        json={**payload, "cursor": tampered},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_cursor"


def test_mobile_detail_is_explicit_safe_and_browser_act_page_still_works(
    client, db, mobile_acts, entitled_token,
):
    search = client.post(
        "/api/v1/acts/search", headers=_bearer(entitled_token),
        json={"filters": {"q": "mobilesliceword"}, "limit": 1},
    ).json()
    item = search["items"][0]
    detail = client.get(
        f"/api/v1/acts/{item['adam']}",
        params={"context": item["context"]},
        headers=_bearer(entitled_token),
    )
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    body = detail.json()
    assert body["adam"] == item["adam"]
    assert body["match_reasons"]
    serialized = detail.text
    assert "never expose" not in serialized
    assert "private mobile full text" not in serialized
    assert "raw_json" not in serialized
    assert "last_edited_by" not in serialized
    assert body["links"][0]["url"].startswith("https://cerpp.eprocurement.gov.gr/")

    # This route renders Claude Code's current tab/accordion page on top of the
    # same shared core read. It guards against a mobile refactor regression.
    web_login(client, "mobile_reader", "pw-123456")
    browser = client.get(f"/act/{item['adam']}")
    assert browser.status_code == 200
    assert 'id="act-tabs"' in browser.text

    db.cursor().execute("""UPDATE proc.procurement_act
        SET data_source='diavgeia', source_url='https://evil.example/document'
        WHERE adam=%s""", (item["adam"],))
    unsafe = client.get(f"/api/v1/acts/{item['adam']}",
                        headers=_bearer(entitled_token))
    assert unsafe.status_code == 200
    assert unsafe.json()["links"] == []

    missing = client.get("/api/v1/acts/DOES-NOT-EXIST",
                         headers=_bearer(entitled_token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_favorites_are_account_scoped_idempotent_and_decorate_search_and_detail(
    client, mobile_acts, entitled_token,
):
    headers = _bearer(entitled_token)
    adam = PREFIX + "2"

    initial = client.post(
        "/api/v1/acts/search", headers=headers,
        json={"filters": {"q": "mobilesliceword"}, "limit": 20},
    )
    assert initial.status_code == 200
    assert all(item["favorited"] is False for item in initial.json()["items"])

    added = client.put(f"/api/v1/favorites/{adam}", headers=headers)
    assert added.status_code == 200
    assert added.json()["adam"] == adam
    assert added.json()["favorited"] is True
    assert added.json()["favorited_at"]

    replay = client.put(f"/api/v1/favorites/{adam}", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["favorited_at"] == added.json()["favorited_at"]

    listing = client.get("/api/v1/favorites", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["act"]["adam"] == adam
    assert listing.json()["items"][0]["act"]["favorited"] is True

    searched = client.post(
        "/api/v1/acts/search", headers=headers,
        json={"filters": {"q": "mobilesliceword"}, "limit": 20},
    ).json()
    favorite = next(item for item in searched["items"] if item["adam"] == adam)
    assert favorite["favorited"] is True
    detail = client.get(f"/api/v1/acts/{adam}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["favorited"] is True

    other_id = make_user("mobile_favorite_other")
    grant(other_id)
    other_token = _login(client, "mobile_favorite_other")["access_token"]
    other_listing = client.get(
        "/api/v1/favorites", headers=_bearer(other_token))
    assert other_listing.status_code == 200
    assert other_listing.json()["items"] == []
    assert other_listing.json()["total"] == 0

    removed = client.delete(f"/api/v1/favorites/{adam}", headers=headers)
    assert removed.status_code == 204
    assert client.delete(
        f"/api/v1/favorites/{adam}", headers=headers).status_code == 204
    assert client.get(
        f"/api/v1/acts/{adam}", headers=headers).json()["favorited"] is False
    missing = client.put("/api/v1/favorites/DOES-NOT-EXIST", headers=headers)
    assert missing.status_code == 404


def test_lapsed_customer_can_remove_but_not_read_favorites(
    client, mobile_acts,
):
    uid = make_user("mobile_favorite_lapsed")
    grant(uid)
    token = _login(client, "mobile_favorite_lapsed")["access_token"]
    headers = _bearer(token)
    adam = PREFIX + "1"
    assert client.put(f"/api/v1/favorites/{adam}", headers=headers).status_code == 200

    expire_sub(uid)
    denied = client.get("/api/v1/favorites", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_expired"
    assert client.delete(f"/api/v1/favorites/{adam}", headers=headers).status_code == 204
