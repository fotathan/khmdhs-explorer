"""Pure unit checks for notification validation and token protection."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api_v1.schemas import NotificationSettingsInput, PushAlertInput
from app.mobile_notifications import protect_push_token


def test_push_token_protection_is_randomized_and_lookup_hash_is_stable(monkeypatch):
    monkeypatch.setenv(
        "MOBILE_PUSH_TOKEN_KEY",
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
    )
    first_cipher, first_hash, version = protect_push_token("ExpoPushToken[secret]")
    second_cipher, second_hash, second_version = protect_push_token("ExpoPushToken[secret]")
    assert first_cipher != second_cipher
    assert first_hash == second_hash
    assert b"secret" not in first_cipher
    assert version == second_version == 1


def test_notification_inputs_enforce_defaults_and_bounds():
    settings = NotificationSettingsInput()
    assert settings.model_dump() == {
        "paused": False, "timezone": "Europe/Athens",
        "quiet_start": "22:00", "quiet_end": "08:00",
        "summary_time": "08:30", "daily_cap": 6, "language": "el",
    }
    assert PushAlertInput().lead_days == [7, 1]
    with pytest.raises(ValidationError):
        NotificationSettingsInput(timezone="No/Such_Zone")
    with pytest.raises(ValidationError):
        PushAlertInput(lead_days=[7, 7])
