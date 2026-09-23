"""Native account-security endpoints: password changes and TOTP lifecycle."""
from __future__ import annotations

import pyotp

from tests.helpers import connect, make_user


DEVICE_ONE = {
    "installation_id": "1d8f28ce-3d15-4b35-9ed1-563728586edf",
    "platform": "ios",
    "app_version": "0.1.0",
    "locale": "en",
    "timezone": "Europe/Athens",
    "display_name": "Current iPhone",
}
DEVICE_TWO = {**DEVICE_ONE,
              "installation_id": "a3ec57da-3cb3-459e-b392-5c065f6f00a8",
              "display_name": "Other iPhone"}


def _login(client, password="pw-123456", device=DEVICE_ONE):
    return client.post("/api/v1/auth/login", json={
        "username": "security_user", "password": password, "device": device,
    })


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_password_change_preserves_actor_and_revokes_other_devices(client):
    make_user("security_user")
    actor = _login(client).json()
    other = _login(client, device=DEVICE_TWO).json()

    changed = client.post(
        "/api/v1/account/password",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "pw-123456", "new_password": "new-password-123"},
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/me", headers=_bearer(actor["access_token"])).status_code == 200
    assert client.get("/api/v1/me", headers=_bearer(other["access_token"])).status_code == 401
    assert _login(client).status_code == 401
    assert _login(client, password="new-password-123").status_code == 200


def test_password_change_requires_current_password(client):
    make_user("security_user")
    actor = _login(client).json()
    response = client.post(
        "/api/v1/account/password",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "wrong-password", "new_password": "new-password-123"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_mfa_enrollment_is_one_use_and_returns_recovery_codes_once(client):
    uid = make_user("security_user")
    actor = _login(client).json()
    other = _login(client, device=DEVICE_TWO).json()

    refused = client.post(
        "/api/v1/account/mfa/enrollment",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "wrong-password"},
    )
    assert refused.status_code == 401

    started = client.post(
        "/api/v1/account/mfa/enrollment",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "pw-123456"},
    )
    assert started.status_code == 200
    enrollment = started.json()
    assert enrollment["enrollment_id"].startswith("men_")
    assert enrollment["otpauth_uri"].startswith("otpauth://totp/")

    confirmed = client.post(
        "/api/v1/account/mfa/enrollment/confirm",
        headers=_bearer(actor["access_token"]),
        json={
            "enrollment_id": enrollment["enrollment_id"],
            "code": pyotp.TOTP(enrollment["secret"]).now(),
        },
    )
    assert confirmed.status_code == 200
    recovery_codes = confirmed.json()["recovery_codes"]
    assert confirmed.json()["enabled"] is True
    assert len(recovery_codes) == 10
    assert client.get("/api/v1/me", headers=_bearer(actor["access_token"])).status_code == 200
    assert client.get("/api/v1/me", headers=_bearer(other["access_token"])).status_code == 401

    status = client.get("/api/v1/account/mfa", headers=_bearer(actor["access_token"])).json()
    assert status == {"enabled": True, "recovery_codes_remaining": 10}
    replay = client.post(
        "/api/v1/account/mfa/enrollment/confirm",
        headers=_bearer(actor["access_token"]),
        json={"enrollment_id": enrollment["enrollment_id"], "code": "000000"},
    )
    assert replay.status_code == 410
    assert replay.json()["error"]["code"] == "challenge_expired"

    with connect() as c:
        cur = c.cursor()
        cur.execute("SELECT mfa_secret, mfa_recovery_codes FROM proc.app_user WHERE id=%s", (uid,))
        row = cur.fetchone()
    assert row["mfa_secret"] == enrollment["secret"]
    assert all(code not in row["mfa_recovery_codes"] for code in recovery_codes)


def test_mfa_disable_requires_password_and_second_factor(client):
    make_user("security_user")
    actor = _login(client).json()
    started = client.post(
        "/api/v1/account/mfa/enrollment",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "pw-123456"},
    ).json()
    client.post(
        "/api/v1/account/mfa/enrollment/confirm",
        headers=_bearer(actor["access_token"]),
        json={
            "enrollment_id": started["enrollment_id"],
            "code": pyotp.TOTP(started["secret"]).now(),
        },
    )

    valid_code = pyotp.TOTP(started["secret"]).now()
    invalid_code = "000000" if valid_code != "000000" else "000001"
    bad = client.request(
        "DELETE", "/api/v1/account/mfa",
        headers=_bearer(actor["access_token"]),
        json={"current_password": "pw-123456", "code": invalid_code},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_mfa"

    disabled = client.request(
        "DELETE", "/api/v1/account/mfa",
        headers=_bearer(actor["access_token"]),
        json={
            "current_password": "pw-123456",
            "code": valid_code,
        },
    )
    assert disabled.status_code == 204
    assert client.get("/api/v1/account/mfa", headers=_bearer(actor["access_token"])).json() == {
        "enabled": False, "recovery_codes_remaining": 0,
    }
