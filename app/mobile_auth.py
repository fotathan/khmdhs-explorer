"""Native mobile session primitives.

Browser authentication remains cookie based in :mod:`app.auth`.  This module
adds an independent native-client contract: short-lived opaque access tokens,
rotating refresh tokens, one-use MFA challenges, and device-scoped revocation.
Only keyed token hashes are stored in PostgreSQL; raw tokens exist only in the
response that issues them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from psycopg.types.json import Json

from app import auth as account_auth

ACCESS_TTL_SECONDS = max(60, int(os.environ.get("MOBILE_ACCESS_TTL_SECONDS", "900")))
REFRESH_IDLE_DAYS = max(1, int(os.environ.get("MOBILE_REFRESH_IDLE_DAYS", "30")))
REFRESH_ABSOLUTE_DAYS = max(
    REFRESH_IDLE_DAYS, int(os.environ.get("MOBILE_REFRESH_ABSOLUTE_DAYS", "90")))
MFA_CHALLENGE_TTL_SECONDS = max(
    60, int(os.environ.get("MOBILE_MFA_CHALLENGE_TTL_SECONDS", "300")))
MFA_MAX_ATTEMPTS = max(1, min(10, int(os.environ.get("MOBILE_MFA_MAX_ATTEMPTS", "5"))))
TOKEN_HASH_VERSION = 1


class MobileAuthError(Exception):
    """Expected authentication failure with a stable public API code."""

    def __init__(self, code: str, *, status: int = 401):
        super().__init__(code)
        self.code = code
        self.status = status


class MobileAuthConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_at: dt.datetime
    refresh_token: str
    refresh_expires_at: dt.datetime
    session_id: uuid.UUID


@dataclass(frozen=True)
class AccessIdentity:
    user: dict
    session_id: uuid.UUID
    access_token_id: int
    device_id: int


def configured() -> bool:
    return bool((os.environ.get("MOBILE_TOKEN_HASH_KEY") or "").strip())


def _hash_key() -> bytes:
    value = (os.environ.get("MOBILE_TOKEN_HASH_KEY") or "").strip()
    if not value:
        if os.environ.get("RENDER") or os.environ.get("APP_ENV", "").lower() == "production":
            raise MobileAuthConfigurationError(
                "MOBILE_TOKEN_HASH_KEY is required when the mobile API is used in production")
        # A deterministic local-only fallback keeps manual development possible.
        # Tests set an explicit key; production is rejected above.
        value = "dev-only-mobile-token-hash-key-change-me"
    return value.encode("utf-8")


def token_hash(raw: str) -> bytes:
    if not isinstance(raw, str) or not raw:
        return b""
    return hmac.new(_hash_key(), raw.encode("utf-8"), hashlib.sha256).digest()


def _new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@contextmanager
def transaction(c):
    """Explicit transaction for cursors backed by the app's autocommit pool."""
    c.execute("BEGIN")
    try:
        yield
    except Exception:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")


def _upsert_device(c, *, user_id: int, device: dict) -> int:
    c.execute(
        """INSERT INTO proc.mobile_device
             (user_id, installation_id, platform, display_name, app_version,
              os_version, locale, timezone, enabled, last_seen_at, revoked_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,now(),NULL)
           ON CONFLICT (user_id, installation_id) DO UPDATE SET
             platform     = EXCLUDED.platform,
             display_name = EXCLUDED.display_name,
             app_version  = EXCLUDED.app_version,
             os_version   = EXCLUDED.os_version,
             locale       = EXCLUDED.locale,
             timezone     = EXCLUDED.timezone,
             enabled      = true,
             revoked_at   = NULL,
             last_seen_at = now(),
             updated_at   = now()
           RETURNING id""",
        (user_id, device["installation_id"], device["platform"],
         device.get("display_name"), device["app_version"],
         device.get("os_version"), device.get("locale") or "el",
         device.get("timezone") or "Europe/Athens"),
    )
    return int(c.fetchone()["id"])


def _issue_token_rows(c, *, session_id: uuid.UUID,
                      absolute_expires_at: dt.datetime) -> IssuedTokens:
    now = _utcnow()
    access_raw = _new_token("mat_")
    refresh_raw = _new_token("mrt_")
    access_expires = now + dt.timedelta(seconds=ACCESS_TTL_SECONDS)
    refresh_expires = min(
        now + dt.timedelta(days=REFRESH_IDLE_DAYS), absolute_expires_at)
    c.execute(
        """INSERT INTO proc.mobile_access_token
             (session_id, token_hash, token_hash_version, expires_at)
           VALUES (%s,%s,%s,%s)""",
        (session_id, token_hash(access_raw), TOKEN_HASH_VERSION, access_expires),
    )
    c.execute(
        """INSERT INTO proc.mobile_refresh_token
             (session_id, token_hash, token_hash_version, expires_at)
           VALUES (%s,%s,%s,%s)""",
        (session_id, token_hash(refresh_raw), TOKEN_HASH_VERSION, refresh_expires),
    )
    return IssuedTokens(access_raw, access_expires, refresh_raw,
                        refresh_expires, session_id)


def issue_session(c, user: dict, device: dict) -> IssuedTokens:
    now = _utcnow()
    absolute_expires = now + dt.timedelta(days=REFRESH_ABSOLUTE_DAYS)
    session_id = uuid.uuid4()
    with transaction(c):
        device_id = _upsert_device(c, user_id=int(user["id"]), device=device)
        c.execute(
            """INSERT INTO proc.mobile_session
                 (id, user_id, device_id, session_version, absolute_expires_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (session_id, user["id"], device_id,
             int(user.get("session_version") or 0), absolute_expires),
        )
        issued = _issue_token_rows(
            c, session_id=session_id, absolute_expires_at=absolute_expires)
    return issued


def create_mfa_challenge(c, user_id: int, device: dict) -> tuple[str, dt.datetime]:
    raw = _new_token("mch_")
    expires = _utcnow() + dt.timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS)
    c.execute(
        """INSERT INTO proc.mobile_auth_challenge
             (user_id, challenge_hash, token_hash_version, purpose, device,
              max_attempts, expires_at)
           VALUES (%s,%s,%s,'login_mfa',%s,%s,%s)""",
        (user_id, token_hash(raw), TOKEN_HASH_VERSION, Json(device),
         MFA_MAX_ATTEMPTS, expires),
    )
    return raw, expires


def complete_mfa_challenge(c, raw_challenge: str, code: str
                           ) -> tuple[dict, IssuedTokens]:
    outcome = "invalid_challenge"
    user = None
    issued = None
    now = _utcnow()
    with transaction(c):
        c.execute(
            """SELECT * FROM proc.mobile_auth_challenge
               WHERE challenge_hash = %s
               FOR UPDATE""",
            (token_hash(raw_challenge),),
        )
        row = c.fetchone()
        if not row:
            outcome = "invalid_challenge"
        elif row["consumed_at"] is not None or row["expires_at"] <= now:
            outcome = "challenge_expired"
            if row["consumed_at"] is None:
                c.execute("UPDATE proc.mobile_auth_challenge SET consumed_at=now() WHERE id=%s",
                          (row["id"],))
        elif int(row["attempts"]) >= int(row["max_attempts"]):
            outcome = "challenge_expired"
        else:
            mfa = account_auth.get_mfa(c, row["user_id"])
            good = bool(mfa and mfa["mfa_enabled"] and (
                account_auth.verify_totp(mfa["mfa_secret"], code)
                or account_auth.consume_recovery_code(c, row["user_id"], code)))
            if not good:
                attempts = int(row["attempts"]) + 1
                c.execute(
                    """UPDATE proc.mobile_auth_challenge
                       SET attempts=%s,
                           consumed_at=CASE WHEN %s >= max_attempts THEN now()
                                            ELSE consumed_at END
                       WHERE id=%s""",
                    (attempts, attempts, row["id"]),
                )
                outcome = "invalid_mfa"
            else:
                user = account_auth.load_user(c, row["user_id"])
                if not user:
                    outcome = "invalid_challenge"
                elif user.get("must_change_password"):
                    outcome = "password_change_required"
                else:
                    c.execute("UPDATE proc.mobile_auth_challenge SET consumed_at=now() WHERE id=%s",
                              (row["id"],))
                    account_auth.touch_last_login(c, row["user_id"])
                    device_id = _upsert_device(c, user_id=row["user_id"],
                                               device=dict(row["device"]))
                    session_id = uuid.uuid4()
                    absolute_expires = now + dt.timedelta(days=REFRESH_ABSOLUTE_DAYS)
                    c.execute(
                        """INSERT INTO proc.mobile_session
                             (id,user_id,device_id,session_version,absolute_expires_at)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (session_id, row["user_id"], device_id,
                         int(user.get("session_version") or 0), absolute_expires),
                    )
                    issued = _issue_token_rows(
                        c, session_id=session_id,
                        absolute_expires_at=absolute_expires)
                    outcome = "ok"
    if outcome == "ok":
        return dict(user), issued
    if outcome == "challenge_expired":
        raise MobileAuthError(outcome, status=410)
    if outcome == "password_change_required":
        raise MobileAuthError(outcome, status=403)
    raise MobileAuthError(outcome)


def _revoke_session_rows(c, session_id, reason: str) -> None:
    c.execute(
        """WITH revoked_session AS (
             UPDATE proc.mobile_session
             SET revoked_at=coalesce(revoked_at,now()),
                 revoke_reason=coalesce(revoke_reason,%s), updated_at=now()
             WHERE id=%s
             RETURNING id
           )
           UPDATE proc.mobile_access_token
           SET revoked_at=coalesce(revoked_at,now())
           WHERE session_id IN (SELECT id FROM revoked_session)""",
        (reason, session_id),
    )


def revoke_session(c, session_id, reason: str = "logout") -> None:
    with transaction(c):
        _revoke_session_rows(c, session_id, reason)


def preserve_current_session_after_security_change(
    c, *, user_id: int, current_session_id: uuid.UUID,
) -> None:
    """Keep the acting mobile session while revoking every other native session.

    Password and MFA helpers bump ``app_user.session_version``. Browser account
    settings preserve the acting browser session after the same operations; the
    native API mirrors that behaviour while explicitly revoking all other token
    families immediately.
    """
    new_version = account_auth.session_version(c, user_id)
    c.execute(
        """SELECT id FROM proc.mobile_session
           WHERE user_id=%s AND id<>%s AND revoked_at IS NULL
           FOR UPDATE""",
        (user_id, current_session_id),
    )
    for row in c.fetchall():
        _revoke_session_rows(c, row["id"], "session_version")
    c.execute(
        """UPDATE proc.mobile_session
           SET session_version=%s, updated_at=now()
           WHERE id=%s AND user_id=%s AND revoked_at IS NULL""",
        (new_version, current_session_id, user_id),
    )


def start_mfa_enrollment(c, *, user_id: int, username: str) -> tuple[str, str, str]:
    raw = _new_token("men_")
    secret = account_auth.new_totp_secret()
    expires = _utcnow() + dt.timedelta(seconds=MFA_CHALLENGE_TTL_SECONDS)
    with transaction(c):
        c.execute(
            """UPDATE proc.mobile_mfa_enrollment SET consumed_at=now()
               WHERE user_id=%s AND consumed_at IS NULL""",
            (user_id,),
        )
        c.execute(
            """INSERT INTO proc.mobile_mfa_enrollment
                 (user_id, enrollment_hash, token_hash_version, secret,
                  max_attempts, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, token_hash(raw), TOKEN_HASH_VERSION, secret,
             MFA_MAX_ATTEMPTS, expires),
        )
    return raw, secret, account_auth.totp_uri(secret, username)


def confirm_mfa_enrollment(
    c, *, user_id: int, current_session_id: uuid.UUID,
    raw_enrollment: str, code: str,
) -> list[str]:
    outcome = "invalid_challenge"
    recovery_codes: list[str] | None = None
    now = _utcnow()
    with transaction(c):
        c.execute(
            """SELECT * FROM proc.mobile_mfa_enrollment
               WHERE enrollment_hash=%s AND user_id=%s
               FOR UPDATE""",
            (token_hash(raw_enrollment), user_id),
        )
        row = c.fetchone()
        if not row:
            outcome = "invalid_challenge"
        elif row["consumed_at"] is not None or row["expires_at"] <= now:
            outcome = "challenge_expired"
            if row["consumed_at"] is None:
                c.execute(
                    "UPDATE proc.mobile_mfa_enrollment SET consumed_at=now() WHERE id=%s",
                    (row["id"],),
                )
        elif int(row["attempts"]) >= int(row["max_attempts"]):
            outcome = "challenge_expired"
        elif not account_auth.verify_totp(row["secret"], code):
            attempts = int(row["attempts"]) + 1
            c.execute(
                """UPDATE proc.mobile_mfa_enrollment
                   SET attempts=%s,
                       consumed_at=CASE WHEN %s >= max_attempts THEN now()
                                        ELSE consumed_at END
                   WHERE id=%s""",
                (attempts, attempts, row["id"]),
            )
            outcome = "invalid_mfa"
        else:
            plain, hashed = account_auth.gen_recovery_codes()
            account_auth.enable_mfa(c, user_id, row["secret"], hashed)
            c.execute(
                "UPDATE proc.mobile_mfa_enrollment SET consumed_at=now() WHERE id=%s",
                (row["id"],),
            )
            preserve_current_session_after_security_change(
                c, user_id=user_id, current_session_id=current_session_id)
            recovery_codes = plain
            outcome = "ok"
    if outcome == "ok":
        return recovery_codes or []
    if outcome == "challenge_expired":
        raise MobileAuthError(outcome, status=410)
    raise MobileAuthError(outcome)


def authenticate_access(c, raw_access: str) -> AccessIdentity:
    digest = token_hash(raw_access)
    c.execute(
        """SELECT at.id AS access_token_id, at.expires_at AS access_expires_at,
                  at.revoked_at AS access_revoked_at,
                  s.id AS session_id, s.user_id, s.device_id,
                  s.session_version, s.absolute_expires_at, s.revoked_at,
                  d.enabled AS device_enabled, d.revoked_at AS device_revoked_at
           FROM proc.mobile_access_token at
           JOIN proc.mobile_session s ON s.id=at.session_id
           JOIN proc.mobile_device d ON d.id=s.device_id
           WHERE at.token_hash=%s""",
        (digest,),
    )
    row = c.fetchone()
    now = _utcnow()
    if not row or row["access_revoked_at"] is not None or row["access_expires_at"] <= now:
        raise MobileAuthError("token_expired")
    if (row["revoked_at"] is not None or row["absolute_expires_at"] <= now
            or not row["device_enabled"] or row["device_revoked_at"] is not None):
        raise MobileAuthError("session_revoked")
    user = account_auth.load_user(c, row["user_id"])
    if not user:
        _revoke_session_rows(c, row["session_id"], "account_inactive")
        raise MobileAuthError("session_revoked")
    if int(user.get("session_version") or 0) != int(row["session_version"]):
        _revoke_session_rows(c, row["session_id"], "session_version")
        raise MobileAuthError("session_revoked")
    # Avoid one write per API call while keeping the device/session list useful.
    c.execute(
        """UPDATE proc.mobile_session SET last_used_at=now(), updated_at=now()
           WHERE id=%s AND last_used_at < now() - interval '15 minutes'""",
        (row["session_id"],),
    )
    c.execute(
        """UPDATE proc.mobile_device SET last_seen_at=now(), updated_at=now()
           WHERE id=%s AND last_seen_at < now() - interval '15 minutes'""",
        (row["device_id"],),
    )
    return AccessIdentity(dict(user), row["session_id"],
                          int(row["access_token_id"]), int(row["device_id"]))


def rotate_refresh(c, raw_refresh: str, installation_id: str
                   ) -> tuple[dict, IssuedTokens]:
    result = "invalid_refresh"
    issued = None
    user = None
    now = _utcnow()
    with transaction(c):
        c.execute(
            """SELECT rt.id AS refresh_id, rt.session_id, rt.expires_at,
                      rt.used_at, s.user_id, s.device_id, s.session_version,
                      s.absolute_expires_at, s.revoked_at,
                      d.installation_id, d.enabled AS device_enabled,
                      d.revoked_at AS device_revoked_at
               FROM proc.mobile_refresh_token rt
               JOIN proc.mobile_session s ON s.id=rt.session_id
               JOIN proc.mobile_device d ON d.id=s.device_id
               WHERE rt.token_hash=%s
               FOR UPDATE OF rt, s""",
            (token_hash(raw_refresh),),
        )
        row = c.fetchone()
        if not row or str(row["installation_id"]) != str(installation_id):
            result = "invalid_refresh"
        elif row["used_at"] is not None:
            _revoke_session_rows(c, row["session_id"], "refresh_reuse")
            result = "session_revoked"
        elif (row["revoked_at"] is not None or not row["device_enabled"]
              or row["device_revoked_at"] is not None):
            result = "session_revoked"
        elif row["expires_at"] <= now or row["absolute_expires_at"] <= now:
            _revoke_session_rows(c, row["session_id"], "expired")
            result = "session_revoked"
        else:
            user = account_auth.load_user(c, row["user_id"])
            if not user:
                _revoke_session_rows(c, row["session_id"], "account_inactive")
                result = "session_revoked"
            elif int(user.get("session_version") or 0) != int(row["session_version"]):
                _revoke_session_rows(c, row["session_id"], "session_version")
                result = "session_revoked"
            else:
                issued = _issue_token_rows(
                    c, session_id=row["session_id"],
                    absolute_expires_at=row["absolute_expires_at"])
                c.execute(
                    """UPDATE proc.mobile_refresh_token
                       SET used_at=now(), replaced_by_id=(
                         SELECT id FROM proc.mobile_refresh_token
                         WHERE session_id=%s AND token_hash=%s)
                       WHERE id=%s""",
                    (row["session_id"], token_hash(issued.refresh_token),
                     row["refresh_id"]),
                )
                c.execute(
                    """UPDATE proc.mobile_session
                       SET last_used_at=now(), updated_at=now() WHERE id=%s""",
                    (row["session_id"],),
                )
                result = "ok"
    if result == "ok":
        return dict(user), issued
    raise MobileAuthError(result)


def cleanup_expired(c) -> dict[str, int]:
    """Purge native-auth records after their committed recovery windows.

    The operation is idempotent and returns aggregate counts only.  It never
    exposes token, account, device, or search data in the cron output.
    """
    statements = {
        "idempotency_keys": """
            DELETE FROM proc.api_idempotency_key
            WHERE expires_at < now()
            RETURNING id
        """,
        "mfa_challenges": """
            DELETE FROM proc.mobile_auth_challenge
            WHERE coalesce(consumed_at, expires_at) < now() - interval '30 days'
            RETURNING id
        """,
        "mfa_enrollments": """
            DELETE FROM proc.mobile_mfa_enrollment
            WHERE coalesce(consumed_at, expires_at) < now() - interval '30 days'
            RETURNING id
        """,
        "access_tokens": """
            DELETE FROM proc.mobile_access_token
            WHERE expires_at < now() - interval '30 days'
               OR revoked_at < now() - interval '30 days'
            RETURNING id
        """,
        "refresh_tokens": """
            DELETE FROM proc.mobile_refresh_token
            WHERE expires_at < now() - interval '30 days'
               OR used_at < now() - interval '30 days'
            RETURNING id
        """,
        "sessions": """
            DELETE FROM proc.mobile_session
            WHERE revoked_at < now() - interval '30 days'
               OR absolute_expires_at < now() - interval '30 days'
            RETURNING id
        """,
        "devices": """
            DELETE FROM proc.mobile_device d
            WHERE d.revoked_at < now() - interval '90 days'
              AND NOT EXISTS (
                SELECT 1 FROM proc.mobile_session s WHERE s.device_id=d.id
              )
            RETURNING id
        """,
    }
    counts: dict[str, int] = {}
    with transaction(c):
        for name, sql in statements.items():
            c.execute(f"WITH deleted AS ({sql}) SELECT count(*) AS n FROM deleted")
            counts[name] = int(c.fetchone()["n"])
    return counts
