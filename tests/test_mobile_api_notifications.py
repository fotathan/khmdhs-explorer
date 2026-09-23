"""Mobile notification settings, encrypted devices, subscriptions and inbox."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from psycopg.types.json import Json

from tests.helpers import expire_sub, grant, make_user
from tests.test_mobile_api_auth import DEVICE

ROOT = Path(__file__).resolve().parent.parent


def _login(client, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": "pw-123456", "device": DEVICE,
    })
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token: str, **extra) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _profile(cur, user_id: int, name: str = "Mobile alerts") -> int:
    from app import auth
    return auth.create_search_profile(
        cur, name=name, scope="customer", owner_id=user_id,
        params={"q": "school", "type": ["notice"]},
        based_on_id=None, created_by=user_id)


def _event(cur, user_id: int, suffix: str, *, age: str = "0 seconds",
           expires: str = "90 days", read=False, deleted: str | None = None) -> int:
    cur.execute(
        """INSERT INTO proc.notification_event
             (user_id,event_type,dedupe_key,target_kind,target_id,adam,title,body,
              context,created_at,read_at,deleted_at,expires_at)
           VALUES (%s,'new_match',%s,'act',%s,%s,%s,%s,%s,
                   now()-(%s)::interval,
                   CASE WHEN %s THEN now() ELSE NULL END,
                   CASE WHEN %s::text IS NULL THEN NULL ELSE now()-(%s)::interval END,
                   now()+(%s)::interval)
           RETURNING id""",
        (user_id, hashlib.sha256(suffix.encode()).digest(), f"ADAM-{suffix}",
         f"ADAM-{suffix}", f"Title {suffix}", f"Body {suffix}",
         Json({"saved_search_names": ["My search"], "mobile_context": "ctx"}),
         age, read, deleted, deleted, expires),
    )
    return int(cur.fetchone()["id"])


def test_notification_migration_is_idempotent(db):
    path = ROOT / "migrations/20260914150000_mobile_notification_foundation.sql"
    for _ in range(2):
        result = subprocess.run(
            ["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1",
             "-q", "-f", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_notification_defaults_and_validated_update_work_when_lapsed(client):
    uid = make_user("mobile_notification_settings")
    expire_sub(uid)
    token = _login(client, "mobile_notification_settings")
    headers = _headers(token)

    response = client.get("/api/v1/notification-settings", headers=headers)
    assert response.status_code == 200
    assert response.json() | {"updated_at": "ignored"} == {
        "paused": False, "timezone": "Europe/Athens",
        "quiet_start": "22:00", "quiet_end": "08:00",
        "summary_time": "08:30", "daily_cap": 6, "language": "el",
        "updated_at": "ignored",
    }
    updated = client.put("/api/v1/notification-settings", headers=headers, json={
        "paused": True, "timezone": "Europe/London", "quiet_start": "23:15",
        "quiet_end": "07:30", "summary_time": "09:00", "daily_cap": 4,
        "language": "en",
    })
    assert updated.status_code == 200
    assert updated.json()["daily_cap"] == 4
    assert updated.json()["paused"] is True
    invalid = client.put("/api/v1/notification-settings", headers=headers, json={
        "paused": False, "timezone": "Not/AZone", "quiet_start": "22:00",
        "quiet_end": "08:00", "summary_time": "08:30", "daily_cap": 11,
        "language": "el",
    })
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


def test_device_registration_encrypts_token_and_revocation_is_owner_safe(
    client, db,
):
    owner = make_user("mobile_device_owner")
    other = make_user("mobile_device_other")
    owner_token = _login(client, "mobile_device_owner")
    other_token = _login(client, "mobile_device_other")
    payload = {**DEVICE, "permission_status": "granted",
               "push_provider": "expo",
               "push_token": "ExponentPushToken[secret-device-token]"}
    headers = _headers(owner_token,
                       **{"Idempotency-Key": "register-device-000001"})
    first = client.post("/api/v1/devices", headers=headers, json=payload)
    replay = client.post("/api/v1/devices", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert "push_token" not in first.text and "secret-device-token" not in first.text
    assert first.json()["current"] is True

    cur = db.cursor()
    cur.execute("""SELECT push_token_ciphertext,push_token_hash
                   FROM proc.mobile_device WHERE id=%s""", (int(first.json()["id"]),))
    stored = cur.fetchone()
    assert b"secret-device-token" not in bytes(stored["push_token_ciphertext"])
    assert b"secret-device-token" not in bytes(stored["push_token_hash"])

    denied = client.delete(
        f"/api/v1/devices/{first.json()['id']}", headers=_headers(other_token))
    assert denied.status_code == 404
    revoked = client.delete(
        f"/api/v1/devices/{first.json()['id']}", headers=_headers(owner_token))
    assert revoked.status_code == 204
    assert client.get("/api/v1/devices", headers=_headers(owner_token)).status_code == 401
    cur.execute("SELECT push_token_ciphertext FROM proc.mobile_device WHERE id=%s",
                (int(first.json()["id"]),))
    assert cur.fetchone()["push_token_ciphertext"] is None
    assert owner != other


def test_push_alert_visibility_suspension_cursor_and_saved_search_flag(client, db):
    owner = make_user("mobile_push_owner")
    other = make_user("mobile_push_other")
    grant(owner)
    token = _login(client, "mobile_push_owner")
    cur = db.cursor()
    own_profile = _profile(cur, owner)
    other_profile = _profile(cur, other, "Other private")
    payload = {"active": True, "delivery_mode": "immediate",
               "new_matches": True, "deadlines": True, "lead_days": [7, 1]}

    missing_device = client.put(
        f"/api/v1/saved-searches/{own_profile}/push-alert",
        headers=_headers(token), json=payload)
    assert missing_device.status_code == 200
    assert missing_device.json()["delivery_suspended_reason"] == "no_active_device"
    cur.execute("""SELECT last_cursor FROM proc.push_subscription
                   WHERE user_id=%s AND search_profile_id=%s""", (owner, own_profile))
    assert cur.fetchone()["last_cursor"] is not None

    inaccessible = client.get(
        f"/api/v1/saved-searches/{other_profile}/push-alert",
        headers=_headers(token))
    assert inaccessible.status_code == 404
    listing = client.get("/api/v1/saved-searches", headers=_headers(token))
    own = next(item for item in listing.json()["items"]
               if item["id"] == str(own_profile))
    assert own["push_alert_enabled"] is True

    expire_sub(owner)
    lapsed = client.get(
        f"/api/v1/saved-searches/{own_profile}/push-alert",
        headers=_headers(token))
    assert lapsed.status_code == 200
    assert lapsed.json()["delivery_suspended_reason"] == "not_entitled"

    deleted = client.delete(
        f"/api/v1/saved-searches/{own_profile}/push-alert",
        headers=_headers(token))
    assert deleted.status_code == 204
    defaults = client.get(
        f"/api/v1/saved-searches/{own_profile}/push-alert",
        headers=_headers(token)).json()
    assert defaults["id"] is None and defaults["active"] is False


def test_notification_inbox_keyset_pagination_read_and_delete_are_user_scoped(
    client, db,
):
    owner = make_user("mobile_inbox_owner")
    other = make_user("mobile_inbox_other")
    token = _login(client, "mobile_inbox_owner")
    cur = db.cursor()
    oldest = _event(cur, owner, "old", age="3 minutes")
    middle = _event(cur, owner, "middle", age="2 minutes")
    newest = _event(cur, owner, "new", age="1 minute")
    foreign = _event(cur, other, "foreign")

    first = client.get("/api/v1/notifications?limit=2", headers=_headers(token))
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == [str(newest), str(middle)]
    assert first.json()["unread_count"] == 3
    second = client.get(
        "/api/v1/notifications?limit=2&cursor=" + first.json()["next_cursor"],
        headers=_headers(token))
    assert [item["id"] for item in second.json()["items"]] == [str(oldest)]
    assert second.json()["next_cursor"] is None

    marked = client.patch(f"/api/v1/notifications/{newest}",
                          headers=_headers(token), json={"read": True})
    assert marked.status_code == 200 and marked.json()["read"] is True
    unread = client.get("/api/v1/notifications?state=unread",
                        headers=_headers(token)).json()
    assert unread["unread_count"] == 2
    assert str(newest) not in {item["id"] for item in unread["items"]}
    assert client.patch(f"/api/v1/notifications/{foreign}",
                        headers=_headers(token), json={"read": True}).status_code == 404

    all_read = client.post("/api/v1/notifications/read-all", headers=_headers(token))
    assert all_read.json() == {"updated": 2}
    assert client.delete(f"/api/v1/notifications/{middle}",
                         headers=_headers(token)).status_code == 204
    remaining = client.get("/api/v1/notifications", headers=_headers(token)).json()
    assert str(middle) not in {item["id"] for item in remaining["items"]}
    tampered = client.get(
        "/api/v1/notifications?limit=2&cursor=" + first.json()["next_cursor"] + "x",
        headers=_headers(token))
    assert tampered.status_code == 422
    assert tampered.json()["error"]["code"] == "invalid_cursor"


def test_notification_cleanup_applies_90_day_inbox_and_30_day_delivery_rules(db):
    from app.mobile_notifications import cleanup_expired

    uid = make_user("mobile_cleanup")
    cur = db.cursor()
    soft = _event(cur, uid, "soft-expired", age="100 days", expires="-1 day")
    hard = _event(cur, uid, "hard-deleted", age="100 days", expires="-10 days",
                  deleted="8 days")
    counts = cleanup_expired(cur)
    assert counts["notifications_expired"] == 1
    assert counts["notifications_deleted"] == 1
    cur.execute("SELECT deleted_at FROM proc.notification_event WHERE id=%s", (soft,))
    assert cur.fetchone()["deleted_at"] is not None
    cur.execute("SELECT 1 FROM proc.notification_event WHERE id=%s", (hard,))
    assert cur.fetchone() is None
