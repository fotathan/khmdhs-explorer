"""Native /api/v1 authentication: bearer-only sessions and rotation safety."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pyotp

from tests.helpers import connect, enable_mfa, make_user


DEVICE = {
    "installation_id": "1d8f28ce-3d15-4b35-9ed1-563728586edf",
    "platform": "ios",
    "app_version": "0.1.0",
    "os_version": "20.0",
    "locale": "el",
    "timezone": "Europe/Athens",
    "display_name": "Test iPhone",
}


def _login(client, username="mobile_user", password="pw-123456", device=None):
    return client.post("/api/v1/auth/login", json={
        "username": username,
        "password": password,
        "device": device or DEVICE,
    })


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_mobile_api_can_be_disabled_without_minting_a_cookie(client, monkeypatch):
    monkeypatch.setenv("MOBILE_API_ENABLED", "0")
    response = client.get("/api/v1/me")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "khmdhs_session" not in response.headers.get("set-cookie", "")
    assert response.headers["x-request-id"]


def test_password_login_issues_hashed_tokens_and_me_uses_bearer_only(client):
    uid = make_user("mobile_user")
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_expires_in"] == 900
    assert body["access_token"].startswith("mat_")
    assert body["refresh_token"].startswith("mrt_")
    assert body["user"]["id"] == str(uid)
    assert response.headers["cache-control"] == "no-store"
    assert "khmdhs_session" not in response.headers.get("set-cookie", "")

    me = client.get("/api/v1/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "mobile_user"

    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT token_hash FROM proc.mobile_access_token")
        access_hash = bytes(cur.fetchone()["token_hash"])
        cur.execute("SELECT token_hash FROM proc.mobile_refresh_token")
        refresh_hash = bytes(cur.fetchone()["token_hash"])
    assert body["access_token"].encode() not in access_hash
    assert body["refresh_token"].encode() not in refresh_hash
    assert body["user"]["entitlement"]["has_access"] is False


def test_invalid_login_uses_stable_json_error(client):
    make_user("mobile_user")
    response = _login(client, password="wrong-password")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials"
    assert error["request_id"] == response.headers["x-request-id"]


def test_inactive_account_is_indistinguishable_from_bad_credentials(client):
    make_user("inactive_user", active=False)
    response = _login(client, username="inactive_user")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_forced_password_change_issues_no_mobile_session(client):
    uid = make_user("mobile_user")
    with connect() as c:
        from app import auth
        auth.set_password(c.cursor(), uid, "temporary-password", must_change=True)
    response = _login(client, password="temporary-password")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_change_required"
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) AS n FROM proc.mobile_session")
        assert cur.fetchone()["n"] == 0


def test_validation_errors_use_the_mobile_envelope(client):
    response = client.post("/api/v1/auth/login", json={"username": "x"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(field["field"] == "password" for field in error["fields"])
    assert any(field["field"] == "device" for field in error["fields"])


def test_mfa_login_issues_no_session_until_valid_second_factor(client):
    secret, _ = enable_mfa(make_user("second_user"))
    # Use a dedicated MFA user so the helper's session_version bump is explicit.
    response = _login(client, username="second_user")
    assert response.status_code == 202
    challenge = response.json()["challenge_id"]
    assert response.json()["status"] == "mfa_required"
    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) AS n FROM proc.mobile_session")
        assert cur.fetchone()["n"] == 0

    valid_code = pyotp.TOTP(secret).now()
    invalid_code = "000000" if valid_code != "000000" else "000001"
    bad = client.post("/api/v1/auth/mfa/verify", json={
        "challenge_id": challenge, "code": invalid_code})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_mfa"

    good = client.post("/api/v1/auth/mfa/verify", json={
        "challenge_id": challenge, "code": valid_code})
    assert good.status_code == 200
    assert client.get("/api/v1/me",
                      headers=_bearer(good.json()["access_token"])).status_code == 200

    replay = client.post("/api/v1/auth/mfa/verify", json={
        "challenge_id": challenge, "code": valid_code})
    assert replay.status_code == 410
    assert replay.json()["error"]["code"] == "challenge_expired"


def test_refresh_rotation_and_reuse_revokes_the_token_family(client):
    make_user("mobile_user")
    first = _login(client).json()
    refreshed = client.post("/api/v1/auth/refresh", json={
        "refresh_token": first["refresh_token"],
        "installation_id": DEVICE["installation_id"],
    })
    assert refreshed.status_code == 200
    second = refreshed.json()
    assert second["refresh_token"] != first["refresh_token"]

    reuse = client.post("/api/v1/auth/refresh", json={
        "refresh_token": first["refresh_token"],
        "installation_id": DEVICE["installation_id"],
    })
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "session_revoked"
    assert client.get("/api/v1/me",
                      headers=_bearer(second["access_token"])).status_code == 401


def test_concurrent_refresh_allows_one_rotation_then_revokes_on_reuse(client):
    make_user("mobile_user")
    first = _login(client).json()
    payload = {
        "refresh_token": first["refresh_token"],
        "installation_id": DEVICE["installation_id"],
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda _: client.post("/api/v1/auth/refresh", json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 401]
    success = next(response.json() for response in responses
                   if response.status_code == 200)
    assert client.get("/api/v1/me",
                      headers=_bearer(success["access_token"])).status_code == 401


def test_expired_access_token_cannot_call_me(client):
    make_user("mobile_user")
    token = _login(client).json()["access_token"]
    with connect() as c:
        c.execute("""UPDATE proc.mobile_access_token
                     SET created_at=now()-interval '2 seconds',
                         expires_at=now()-interval '1 second'""")
    response = client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_refresh_is_bound_to_the_installation(client):
    make_user("mobile_user")
    first = _login(client).json()
    response = client.post("/api/v1/auth/refresh", json={
        "refresh_token": first["refresh_token"],
        "installation_id": "a3ec57da-3cb3-459e-b392-5c065f6f00a8",
    })
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh"


def test_session_version_change_revokes_native_access(client):
    uid = make_user("mobile_user")
    token = _login(client).json()["access_token"]
    with connect() as c:
        from app import auth
        auth.set_password(c.cursor(), uid, "new-password-123")
    response = client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_revoked"


def test_logout_revokes_current_mobile_session(client):
    make_user("mobile_user")
    token = _login(client).json()["access_token"]
    response = client.post("/api/v1/auth/logout", headers=_bearer(token))
    assert response.status_code == 204
    assert client.get("/api/v1/me", headers=_bearer(token)).status_code == 401


def test_mobile_auth_cleanup_enforces_committed_retention_windows(client):
    uid = make_user("mobile_user")
    _login(client)
    with connect() as c:
        cur = c.cursor()
        from app import mobile_auth

        challenge, _expires = mobile_auth.create_mfa_challenge(cur, uid, DEVICE)
        assert challenge.startswith("mch_")
        cur.execute("""UPDATE proc.mobile_auth_challenge
                       SET created_at=now()-interval '32 days',
                           expires_at=now()-interval '31 days'""")
        cur.execute("""UPDATE proc.mobile_access_token
                       SET created_at=now()-interval '32 days',
                           expires_at=now()-interval '31 days'""")
        cur.execute("""UPDATE proc.mobile_refresh_token
                       SET created_at=now()-interval '32 days',
                           expires_at=now()-interval '31 days'""")
        cur.execute("""UPDATE proc.mobile_session
                       SET created_at=now()-interval '100 days',
                           absolute_expires_at=now()-interval '31 days'""")
        cur.execute("""INSERT INTO proc.api_idempotency_key
                         (user_id,method,path,key_hash,request_fingerprint,expires_at)
                       VALUES (%s,'POST','/api/v1/example',%s,%s,
                               now()-interval '1 second')""",
                    (uid, b"key", b"request"))
        counts = mobile_auth.cleanup_expired(cur)
        assert counts == {
            "idempotency_keys": 1,
            "mfa_challenges": 1,
            "mfa_enrollments": 0,
            "access_tokens": 1,
            "refresh_tokens": 1,
            "sessions": 1,
            "devices": 0,
        }
