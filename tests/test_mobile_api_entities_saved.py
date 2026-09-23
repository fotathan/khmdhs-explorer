"""Mobile entity summaries and saved-search ownership/idempotency rules."""
from __future__ import annotations

import pytest

from tests.helpers import expire_sub, grant, make_user
from tests.test_mobile_api_auth import DEVICE


ENTITY_PREFIX = "MOB-ENTITY-"


def _login(client, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": "pw-123456", "device": DEVICE,
    })
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token: str, **extra) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _profile(cur, *, name: str, owner=None, published=False, params=None) -> int:
    from app import auth

    pid = auth.create_search_profile(
        cur, name=name, scope="customer" if owner else "portal",
        owner_id=owner, params=params or {"q": "school", "type": ["notice"]},
        based_on_id=None, created_by=owner)
    if published:
        auth.set_profile_published(cur, pid, True)
    return pid


@pytest.fixture()
def entity_data(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.entity_member WHERE member_key LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.entity_group WHERE canonical_key LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.act_operator WHERE adam LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("""INSERT INTO proc.authority
                     (org_id,name,contact_email,contact_phone,contact_url,city)
                   VALUES
                     (%s,'Canonical Mobile Authority','office@example.test',
                      '2100000000','https://authority.gov.gr','Athens'),
                     (%s,'Duplicate Mobile Authority',NULL,NULL,NULL,NULL)""",
                (ENTITY_PREFIX + "AUTH-A", ENTITY_PREFIX + "AUTH-B"))
    cur.execute("""INSERT INTO proc.entity_group
                     (kind,canonical_key,display_name,created_by)
                   VALUES ('authority',%s,'Merged Mobile Authority','test')
                   RETURNING id""", (ENTITY_PREFIX + "AUTH-A",))
    group_id = cur.fetchone()["id"]
    cur.execute("""INSERT INTO proc.entity_member (group_id,kind,member_key)
                   VALUES (%s,'authority',%s),(%s,'authority',%s)""",
                (group_id, ENTITY_PREFIX + "AUTH-A",
                 group_id, ENTITY_PREFIX + "AUTH-B"))
    cur.execute("""INSERT INTO proc.economic_operator
                     (vat_number,name,is_greek_vat,country,contact_url)
                   VALUES (%s,'Mobile Supplier',true,'GR','http://unsafe.example')
                   RETURNING operator_id""", (ENTITY_PREFIX + "VAT",))
    operator_id = cur.fetchone()["operator_id"]
    for adam, act_type, authority, value in (
        (ENTITY_PREFIX + "NOTICE", "notice", ENTITY_PREFIX + "AUTH-B", 1000),
        (ENTITY_PREFIX + "CONTRACT", "contract", ENTITY_PREFIX + "AUTH-A", 5000),
    ):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam,type,title,origin,data_source,authority_id,
                          total_cost_with_vat,submission_date)
                       VALUES (%s,%s,%s,'import','khmdhs',%s,%s,now())""",
                    (adam, act_type, "Mobile entity act", authority, value))
    cur.execute("""INSERT INTO proc.act_operator
                     (adam,operator_id,role,awarded_value_with_vat)
                   VALUES (%s,%s,'winner',4500)""",
                (ENTITY_PREFIX + "CONTRACT", operator_id))
    from app import main
    main._lookup_cache.clear()
    yield
    cur.execute("DELETE FROM proc.entity_member WHERE group_id=%s", (group_id,))
    cur.execute("DELETE FROM proc.entity_group WHERE id=%s", (group_id,))
    cur.execute("DELETE FROM proc.act_operator WHERE adam LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE %s",
                (ENTITY_PREFIX + "%",))
    cur.execute("DELETE FROM proc.economic_operator WHERE operator_id=%s",
                (operator_id,))
    cur.execute("DELETE FROM proc.authority WHERE org_id LIKE %s",
                (ENTITY_PREFIX + "%",))
    main._lookup_cache.clear()


def test_merge_aware_authority_and_contractor_summaries_are_customer_safe(
    client, entity_data,
):
    uid = make_user("mobile_entity_reader")
    grant(uid)
    token = _login(client, "mobile_entity_reader")
    headers = _headers(token, **{"Accept-Language": "en"})

    authority = client.get(
        f"/api/v1/authorities/{ENTITY_PREFIX}AUTH-B", headers=headers)
    assert authority.status_code == 200
    body = authority.json()
    assert body["kind"] == "authority"
    assert body["id"] == ENTITY_PREFIX + "AUTH-A"
    assert body["name"] == body["canonical_name"] == "Merged Mobile Authority"
    assert body["merged"] is True
    assert body["headline"] == {
        "act_count": 2,
        "contract_value": {"amount": "5000.00", "currency": "EUR"},
    }
    assert len(body["recent_acts"]) == 2
    assert body["contact"] is None  # requested duplicate has no contact fields
    assert "raw_json" not in authority.text

    contractor = client.get(
        f"/api/v1/contractors/{ENTITY_PREFIX}VAT", headers=headers)
    assert contractor.status_code == 200
    body = contractor.json()
    assert body["kind"] == "contractor"
    assert body["headline"] == {
        "act_count": 1,
        "contract_value": {"amount": "4500.00", "currency": "EUR"},
    }
    assert body["top_buyers"] == [{
        "id": ENTITY_PREFIX + "AUTH-A", "name": "Canonical Mobile Authority"}]
    assert body["contact"] is not None
    assert body["contact"]["website"] is None
    assert len(body["recent_acts"]) == 1


def test_authority_filter_search_covers_merged_directory(client, entity_data):
    uid = make_user("mobile_authority_options")
    grant(uid)
    token = _login(client, "mobile_authority_options")

    response = client.get(
        "/api/v1/authority-options?q=Duplicate%20Mobile",
        headers=_headers(token),
    )
    assert response.status_code == 200
    # A LookupItem has one shape on every endpoint: parent_code is in the
    # contract (optional, nullable) and a flat authority list has no parent.
    assert response.json() == [{
        "code": ENTITY_PREFIX + "AUTH-A",
        "label": "Canonical Mobile Authority",
        "parent_code": None,
    }]


def test_entity_detail_requires_entitlement_and_unknown_ids_are_404(
    client, entity_data,
):
    uid = make_user("mobile_entity_lapsed")
    expire_sub(uid)
    token = _login(client, "mobile_entity_lapsed")
    denied = client.get(
        f"/api/v1/authorities/{ENTITY_PREFIX}AUTH-A",
        headers=_headers(token))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_expired"

    grant(uid)
    # Existing access tokens re-read entitlement on every request.
    missing = client.get("/api/v1/contractors/UNKNOWN-MOBILE-VAT",
                         headers=_headers(token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_saved_search_create_is_idempotent_and_list_is_visibility_safe(client, db):
    uid = make_user("mobile_saved_owner")
    other = make_user("mobile_saved_other")
    token = _login(client, "mobile_saved_owner")
    cur = db.cursor()
    published = _profile(cur, name="Published portal", published=True)
    _profile(cur, name="Hidden portal", published=False)
    _profile(cur, name="Other customer", owner=other)

    payload = {
        "name": "My mobile notices",
        "filters": {"q": "school", "types": ["notice"]},
    }
    headers = _headers(token, **{"Idempotency-Key": "create-mobile-search-0001"})
    first = client.post("/api/v1/saved-searches", json=payload, headers=headers)
    assert first.status_code == 201
    replay = client.post("/api/v1/saved-searches", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    cur.execute("SELECT count(*) AS n FROM proc.search_profile WHERE owner_user_id=%s",
                (uid,))
    assert cur.fetchone()["n"] == 1

    changed = client.post(
        "/api/v1/saved-searches",
        json={**payload, "name": "Different"}, headers=headers)
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_conflict"

    listing = client.get("/api/v1/saved-searches", headers=_headers(token))
    assert listing.status_code == 200
    body = listing.json()
    assert body["owned_count"] == 1 and body["owned_limit"] == 25
    names = {item["name"] for item in body["items"]}
    assert names == {"My mobile notices", "Published portal"}
    portal = next(item for item in body["items"] if item["id"] == str(published))
    assert portal["owned"] is False and portal["editable"] is False
    assert portal["filters"]["types"] == ["notice"]


def test_saved_search_rename_delete_and_cascade_are_owner_only(client, db):
    owner = make_user("mobile_saved_victim")
    attacker = make_user("mobile_saved_attacker")
    cur = db.cursor()
    victim_id = _profile(cur, name="Victim search", owner=owner)
    own_id = _profile(cur, name="Attacker search", owner=attacker)
    portal_id = _profile(cur, name="Portal search", published=True)
    from app import digests
    digests.upsert_subscription(
        cur, user_id=attacker, search_profile_id=own_id, is_active=True)
    token = _login(client, "mobile_saved_attacker")
    headers = _headers(token)

    assert client.patch(
        f"/api/v1/saved-searches/{victim_id}", json={"name": "Stolen"},
        headers=headers).status_code == 404
    assert client.delete(
        f"/api/v1/saved-searches/{portal_id}", headers=headers).status_code == 404

    renamed = client.patch(
        f"/api/v1/saved-searches/{own_id}", json={"name": "Renamed"},
        headers=headers)
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["email_alert_enabled"] is True
    deleted = client.delete(f"/api/v1/saved-searches/{own_id}", headers=headers)
    assert deleted.status_code == 204
    cur.execute("SELECT count(*) AS n FROM proc.digest_subscription WHERE user_id=%s",
                (attacker,))
    assert cur.fetchone()["n"] == 0
    assert client.delete(
        f"/api/v1/saved-searches/{own_id}", headers=headers).status_code == 404


def test_lapsed_customer_keeps_saved_search_access_and_empty_filter_is_rejected(
    client,
):
    uid = make_user("mobile_saved_lapsed")
    expire_sub(uid)
    token = _login(client, "mobile_saved_lapsed")
    headers = _headers(token, **{"Idempotency-Key": "lapsed-mobile-search-01"})
    created = client.post("/api/v1/saved-searches", headers=headers, json={
        "name": "Kept while lapsed", "filters": {"sources": ["khmdhs"]},
    })
    assert created.status_code == 201
    assert client.get("/api/v1/saved-searches",
                      headers=_headers(token)).json()["owned_count"] == 1

    empty = client.post(
        "/api/v1/saved-searches",
        headers=_headers(token, **{"Idempotency-Key": "empty-mobile-search-001"}),
        json={"name": "Everything", "filters": {}},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "validation_error"


def test_saved_search_cap_returns_a_stable_conflict(client, db, monkeypatch):
    from app import account_searches

    monkeypatch.setattr(account_searches, "MAX_SAVED_SEARCHES", 2)
    uid = make_user("mobile_saved_cap")
    cur = db.cursor()
    _profile(cur, name="One", owner=uid)
    _profile(cur, name="Two", owner=uid)
    token = _login(client, "mobile_saved_cap")
    response = client.post(
        "/api/v1/saved-searches",
        headers=_headers(token, **{"Idempotency-Key": "cap-mobile-search-00001"}),
        json={"name": "Three", "filters": {"q": "third"}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "saved_search_limit"
