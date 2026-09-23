"""Mobile email-alert settings reuse the existing digest subscription model."""
from __future__ import annotations

import pytest

from tests.helpers import connect, expire_sub, grant, make_user
from tests.test_mobile_api_auth import DEVICE

HISTORY_ACT = "MOBILE-EMAIL-HISTORY-ACT"


@pytest.fixture()
def history_act(db):
    """The act a past run listed. _clean truncates the user side only, never
    proc.procurement_act — a leftover act changes every later search count."""
    cur = db.cursor()
    cur.execute(
        """INSERT INTO proc.procurement_act
             (adam,type,title,origin,data_source,submission_date)
           VALUES (%s,'notice','School supplies',
                   'import','khmdhs','2026-09-15T07:30:00+00:00')""",
        (HISTORY_ACT,),
    )
    yield HISTORY_ACT
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (HISTORY_ACT,))


def _login(client, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": "pw-123456", "device": DEVICE,
    })
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept-Language": "en"}


def _profile(cur, *, owner=None, published=False, name="Email profile") -> int:
    from app import auth
    profile_id = auth.create_search_profile(
        cur, name=name, scope="customer" if owner else "portal",
        owner_id=owner, params={"q": "school", "type": ["notice"]},
        based_on_id=None, created_by=owner)
    if published:
        auth.set_profile_published(cur, profile_id, True)
    return profile_id


def _schedules(cur) -> tuple[int, int]:
    cur.execute("DELETE FROM proc.digest_schedule")
    cur.execute(
        """INSERT INTO proc.digest_schedule
             (name,cadence,hour,minute,tz,is_default,is_active)
           VALUES ('Daily morning','daily',8,0,'Europe/Athens',true,true)
           RETURNING id"""
    )
    default_id = int(cur.fetchone()["id"])
    cur.execute(
        """INSERT INTO proc.digest_schedule
             (name,cadence,hour,minute,weekday,tz,is_default,is_active)
           VALUES ('Monday morning','weekly',8,30,0,'Europe/Athens',false,true)
           RETURNING id"""
    )
    weekly_id = int(cur.fetchone()["id"])
    return default_id, weekly_id


def _payload(schedule_id: int | None, **changes) -> dict:
    value = {
        "active": True,
        "layout": "summary",
        "schedule_id": str(schedule_id) if schedule_id is not None else None,
        "language": "en",
        "max_results": 40,
        "lead_days": [7, 1],
        "send_empty": False,
    }
    value.update(changes)
    return value


def test_email_alert_defaults_include_only_active_admin_schedules(client, db):
    uid = make_user("mobile_email_defaults")
    grant(uid)
    cur = db.cursor()
    cur.execute("UPDATE proc.app_user SET email='defaults@example.test' WHERE id=%s", (uid,))
    profile_id = _profile(cur, owner=uid)
    default_id, weekly_id = _schedules(cur)
    cur.execute("UPDATE proc.digest_schedule SET is_active=false WHERE id=%s", (weekly_id,))
    token = _login(client, "mobile_email_defaults")

    response = client.get(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=_headers(token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False and body["active"] is False
    assert body["layout"] == "list" and body["max_results"] == 25
    assert body["lead_days"] == [7, 1]
    assert body["schedule_id"] is None
    assert body["last_sent_at"] is None
    assert body["additional_recipients"] == []
    assert body["recent_runs"] == []
    assert body["schedule_options"] == [{
        "id": str(default_id), "label": "Daily 08:00 (Europe/Athens)",
        "is_default": True,
    }]


def test_email_alert_roundtrip_uses_digest_row_and_saved_search_badge(client, db):
    uid = make_user("mobile_email_owner")
    grant(uid)
    cur = db.cursor()
    cur.execute("UPDATE proc.app_user SET email='owner@example.test' WHERE id=%s", (uid,))
    profile_id = _profile(cur, owner=uid)
    default_id, weekly_id = _schedules(cur)
    token = _login(client, "mobile_email_owner")

    inherited = client.put(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=_headers(token), json=_payload(default_id),
    )
    assert inherited.status_code == 200
    assert inherited.json()["exists"] is True
    assert inherited.json()["schedule_id"] is None
    assert inherited.json()["delivery_suspended_reason"] is None

    explicit = client.put(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=_headers(token),
        json=_payload(weekly_id, layout="deadline", lead_days=[14, 3, 1]),
    )
    assert explicit.status_code == 200
    assert explicit.json()["schedule_id"] == str(weekly_id)
    assert explicit.json()["layout"] == "deadline"
    assert explicit.json()["lead_days"] == [14, 3, 1]
    assert explicit.json()["next_run_at"] is not None

    listing = client.get("/api/v1/saved-searches", headers=_headers(token))
    item = next(row for row in listing.json()["items"]
                if row["id"] == str(profile_id))
    assert item["email_alert_enabled"] is True

    removed = client.delete(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=_headers(token),
    )
    assert removed.status_code == 204
    with connect() as connection:
        row = connection.execute(
            "SELECT count(*) AS n FROM proc.digest_subscription WHERE user_id=%s",
            (uid,),
        ).fetchone()
        assert row["n"] == 0


def test_email_alert_returns_web_delivery_metadata(client, db, history_act):
    uid = make_user("mobile_email_history")
    grant(uid)
    cur = db.cursor()
    cur.execute(
        "UPDATE proc.app_user SET email='history@example.test' WHERE id=%s",
        (uid,),
    )
    profile_id = _profile(cur, owner=uid)
    default_id, _weekly_id = _schedules(cur)
    token = _login(client, "mobile_email_history")
    headers = _headers(token)
    created = client.put(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=headers, json=_payload(default_id),
    )
    assert created.status_code == 200

    cur.execute(
        """SELECT id FROM proc.digest_subscription
             WHERE user_id=%s AND search_profile_id=%s""",
        (uid, profile_id),
    )
    subscription_id = int(cur.fetchone()["id"])
    cur.execute(
        """INSERT INTO proc.digest_recipient
             (subscription_id,email,is_active,ord)
           VALUES (%s,'colleague@example.test',true,0)""",
        (subscription_id,),
    )
    cur.execute(
        """UPDATE proc.digest_subscription
              SET last_sent_at='2026-09-15T08:00:00+00:00'
            WHERE id=%s""",
        (subscription_id,),
    )
    cur.execute(
        """INSERT INTO proc.digest_run
             (subscription_id,status,n_results,started_at)
           VALUES (%s,'sent',12,'2026-09-15T08:00:00+00:00'),
                  (%s,'empty',0,'2026-09-14T08:00:00+00:00')""",
        (subscription_id, subscription_id),
    )
    cur.execute(
        """SELECT id FROM proc.digest_run
            WHERE subscription_id=%s ORDER BY started_at DESC""",
        (subscription_id,),
    )
    sent_run_id, empty_run_id = [int(row["id"]) for row in cur.fetchall()]
    cur.execute(
        """INSERT INTO proc.digest_run_item
             (run_id,adam,ord,in_email,ingested_at)
           VALUES (%s,%s,0,true,'2026-09-15T07:30:00+00:00')""",
        (sent_run_id, history_act),
    )

    response = client.get(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["last_sent_at"] == "2026-09-15T08:00:00Z"
    assert body["additional_recipients"] == ["colleague@example.test"]
    assert body["recent_runs"] == [
        {
            "id": str(sent_run_id),
            "status": "sent",
            "result_count": 12,
            "started_at": "2026-09-15T08:00:00Z",
        },
        {
            "id": str(empty_run_id),
            "status": "empty",
            "result_count": 0,
            "started_at": "2026-09-14T08:00:00Z",
        },
    ]

    run_page = client.get(
        f"/api/v1/saved-searches/{profile_id}/email-alert/runs/{sent_run_id}",
        headers=headers,
    )
    assert run_page.status_code == 200
    page = run_page.json()
    assert page["run"]["id"] == str(sent_run_id)
    assert page["saved_search_id"] == str(profile_id)
    assert page["saved_search_name"] == "Email profile"
    assert page["filters"]["q"] == "school"
    assert page["total"] == 1 and page["next_cursor"] is None
    assert page["items"][0]["included_in_email"] is True
    assert page["items"][0]["act"]["adam"] == history_act

    invalid_cursor = client.get(
        f"/api/v1/saved-searches/{profile_id}/email-alert/runs/{sent_run_id}?cursor=invalid",
        headers=headers,
    )
    assert invalid_cursor.status_code == 422

    other_uid = make_user("mobile_email_history_other")
    grant(other_uid)
    other_token = _login(client, "mobile_email_history_other")
    hidden = client.get(
        f"/api/v1/saved-searches/{profile_id}/email-alert/runs/{sent_run_id}",
        headers=_headers(other_token),
    )
    assert hidden.status_code == 404


def test_email_alert_profile_visibility_and_schedule_validation(client, db):
    uid = make_user("mobile_email_viewer")
    other = make_user("mobile_email_other")
    grant(uid)
    cur = db.cursor()
    own_id = _profile(cur, owner=uid, name="Own")
    other_id = _profile(cur, owner=other, name="Other")
    public_id = _profile(cur, published=True, name="Published")
    hidden_id = _profile(cur, published=False, name="Hidden")
    _default_id, weekly_id = _schedules(cur)
    cur.execute("UPDATE proc.digest_schedule SET is_active=false WHERE id=%s", (weekly_id,))
    token = _login(client, "mobile_email_viewer")
    headers = _headers(token)

    assert client.get(
        f"/api/v1/saved-searches/{other_id}/email-alert", headers=headers).status_code == 404
    assert client.get(
        f"/api/v1/saved-searches/{hidden_id}/email-alert", headers=headers).status_code == 404
    assert client.get(
        f"/api/v1/saved-searches/{public_id}/email-alert", headers=headers).status_code == 200

    invalid = client.put(
        f"/api/v1/saved-searches/{own_id}/email-alert",
        headers=headers, json=_payload(weekly_id),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["fields"] == [
        {"field": "schedule_id", "code": "unavailable"}]


def test_lapsed_email_alert_is_retained_but_marked_suspended(client, db):
    uid = make_user("mobile_email_lapsed")
    expire_sub(uid)
    cur = db.cursor()
    cur.execute("UPDATE proc.app_user SET email='lapsed@example.test' WHERE id=%s", (uid,))
    profile_id = _profile(cur, owner=uid)
    default_id, _weekly_id = _schedules(cur)
    token = _login(client, "mobile_email_lapsed")

    response = client.put(
        f"/api/v1/saved-searches/{profile_id}/email-alert",
        headers=_headers(token), json=_payload(default_id),
    )
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["delivery_suspended_reason"] == "not_entitled"
