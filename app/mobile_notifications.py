"""Customer-safe mobile notification settings, devices and inbox services.

This slice stores subscriptions and encrypted Expo tokens, but deliberately
does not contact a push provider. Notification generation and provider
delivery workers can be enabled independently in a later slice.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.errors import UniqueViolation

from app import mobile_auth, mobile_search
from app.api_v1.schemas import (
    Device,
    DeviceList,
    DeviceRegistration,
    NotificationItem,
    NotificationPage,
    NotificationSettings,
    NotificationSettingsInput,
    NotificationTarget,
    PushAlert,
    PushAlertInput,
)

PUSH_TOKEN_KEY_VERSION = 1
PUSH_TOKEN_AAD = b"khmdhs:expo-push-token:v1"
INBOX_CURSOR_TTL_SECONDS = 3600


class MobileNotificationError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


class MobilePushConfigurationError(RuntimeError):
    pass


def _push_key() -> bytes:
    encoded = (os.environ.get("MOBILE_PUSH_TOKEN_KEY") or "").strip()
    if not encoded:
        raise MobilePushConfigurationError(
            "MOBILE_PUSH_TOKEN_KEY is required to store push tokens")
    try:
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except Exception as exc:
        raise MobilePushConfigurationError(
            "MOBILE_PUSH_TOKEN_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise MobilePushConfigurationError(
            "MOBILE_PUSH_TOKEN_KEY must decode to exactly 32 bytes")
    return key


def protect_push_token(raw_token: str) -> tuple[bytes, bytes, int]:
    """Return AES-GCM ciphertext plus a keyed equality hash."""
    key = _push_key()
    nonce = secrets.token_bytes(12)
    ciphertext = nonce + AESGCM(key).encrypt(
        nonce, raw_token.encode("utf-8"), PUSH_TOKEN_AAD)
    digest = hmac.new(key, raw_token.encode("utf-8"), hashlib.sha256).digest()
    return ciphertext, digest, PUSH_TOKEN_KEY_VERSION


def reveal_push_token(ciphertext: bytes, key_version: int) -> str:
    """Decrypt a provider token for the delivery worker only."""
    if int(key_version) != PUSH_TOKEN_KEY_VERSION or len(ciphertext or b"") < 29:
        raise MobilePushConfigurationError("unsupported push-token key version")
    key = _push_key()
    raw = bytes(ciphertext)
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], PUSH_TOKEN_AAD).decode("utf-8")
    except Exception as exc:
        raise MobilePushConfigurationError("push token cannot be decrypted") from exc


def _format_time(value: dt.time) -> str:
    return value.strftime("%H:%M")


def _settings_dto(row: dict) -> NotificationSettings:
    return NotificationSettings(
        paused=bool(row["paused"]), timezone=row["timezone"],
        quiet_start=_format_time(row["quiet_start"]),
        quiet_end=_format_time(row["quiet_end"]),
        summary_time=_format_time(row["summary_time"]),
        daily_cap=int(row["daily_cap"]), language=row["lang"],
        updated_at=row["updated_at"],
    )


def get_settings(c, user_id: int) -> NotificationSettings:
    c.execute(
        """INSERT INTO proc.mobile_notification_preference (user_id)
           VALUES (%s) ON CONFLICT (user_id) DO NOTHING""", (user_id,))
    c.execute("SELECT * FROM proc.mobile_notification_preference WHERE user_id=%s",
              (user_id,))
    return _settings_dto(c.fetchone())


def put_settings(c, user_id: int,
                 payload: NotificationSettingsInput) -> NotificationSettings:
    # Pydantic validates this too; checking here protects direct service callers.
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise MobileNotificationError("validation_error", 422) from exc
    c.execute(
        """INSERT INTO proc.mobile_notification_preference
             (user_id,paused,timezone,quiet_start,quiet_end,summary_time,daily_cap,lang)
           VALUES (%s,%s,%s,%s::time,%s::time,%s::time,%s,%s)
           ON CONFLICT (user_id) DO UPDATE SET
             paused=excluded.paused, timezone=excluded.timezone,
             quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end,
             summary_time=excluded.summary_time, daily_cap=excluded.daily_cap,
             lang=excluded.lang, updated_at=now()
           RETURNING *""",
        (user_id, payload.paused, payload.timezone, payload.quiet_start,
         payload.quiet_end, payload.summary_time, payload.daily_cap,
         payload.language),
    )
    return _settings_dto(c.fetchone())


def _device_name(row: dict) -> str:
    return (row.get("display_name") or
            ("iOS device" if row["platform"] == "ios" else "Android device"))


def _device_dto(row: dict, current_device_id: int) -> Device:
    return Device(
        id=str(row["id"]), installation_id=row["installation_id"],
        platform=row["platform"], display_name=_device_name(row),
        app_version=row.get("app_version"), os_version=row.get("os_version"),
        permission_status=row.get("permission_status") or "unknown",
        enabled=bool(row["enabled"] and row["revoked_at"] is None),
        current=int(row["id"]) == int(current_device_id),
        last_seen_at=row["last_seen_at"], revoked_at=row["revoked_at"],
    )


def list_devices(c, user_id: int, current_device_id: int) -> DeviceList:
    c.execute("""SELECT * FROM proc.mobile_device WHERE user_id=%s
                 ORDER BY (id=%s) DESC, last_seen_at DESC, id DESC""",
              (user_id, current_device_id))
    return DeviceList(items=[_device_dto(row, current_device_id)
                             for row in c.fetchall()])


def register_current_device(c, *, user_id: int, current_device_id: int,
                            payload: DeviceRegistration) -> Device:
    with mobile_auth.transaction(c):
        c.execute("""SELECT * FROM proc.mobile_device
                     WHERE id=%s AND user_id=%s FOR UPDATE""",
                  (current_device_id, user_id))
        existing = c.fetchone()
        if not existing or str(existing["installation_id"]) != str(payload.installation_id):
            raise MobileNotificationError("not_found", 404)

        token_fields = payload.model_fields_set
        clear_token = payload.permission_status in {"unknown", "denied"}
        protected = None
        if payload.push_token:
            try:
                protected = protect_push_token(payload.push_token)
            except MobilePushConfigurationError as exc:
                raise MobileNotificationError("service_unavailable", 503) from exc

        assignments = """
          platform=%s,display_name=%s,app_version=%s,os_version=%s,locale=%s,
          timezone=%s,permission_status=%s,push_provider=%s,enabled=true,
          revoked_at=NULL,last_seen_at=now(),updated_at=now()
        """
        params: list = [
            payload.platform, payload.display_name, payload.app_version,
            payload.os_version, payload.locale, payload.timezone,
            payload.permission_status, payload.push_provider,
        ]
        if protected:
            assignments += ",push_token_ciphertext=%s,push_token_hash=%s,push_token_key_version=%s"
            params.extend(protected)
        elif clear_token or "push_token" in token_fields:
            assignments += ",push_token_ciphertext=NULL,push_token_hash=NULL,push_token_key_version=NULL"
        params.extend([current_device_id, user_id])
        try:
            c.execute(f"""UPDATE proc.mobile_device SET {assignments}
                          WHERE id=%s AND user_id=%s RETURNING *""", params)
        except UniqueViolation as exc:
            raise MobileNotificationError("conflict", 409) from exc
        return _device_dto(c.fetchone(), current_device_id)


def revoke_device(c, *, user_id: int, device_id: int) -> None:
    with mobile_auth.transaction(c):
        c.execute(
            """UPDATE proc.mobile_device SET enabled=false,revoked_at=coalesce(revoked_at,now()),
                    push_token_ciphertext=NULL,push_token_hash=NULL,push_token_key_version=NULL,
                    updated_at=now()
               WHERE id=%s AND user_id=%s AND revoked_at IS NULL RETURNING id""",
            (device_id, user_id),
        )
        if not c.fetchone():
            raise MobileNotificationError("not_found", 404)
        c.execute(
            """UPDATE proc.mobile_session SET revoked_at=coalesce(revoked_at,now()),
                    revoke_reason=coalesce(revoke_reason,'device_revoked'),updated_at=now()
               WHERE device_id=%s""", (device_id,))
        c.execute(
            """UPDATE proc.mobile_access_token SET revoked_at=coalesce(revoked_at,now())
               WHERE session_id IN (SELECT id FROM proc.mobile_session WHERE device_id=%s)""",
            (device_id,))


def _profile(c, user_id: int, profile_id: int) -> dict:
    c.execute("""SELECT * FROM proc.search_profile WHERE id=%s AND (
                   (scope='customer' AND owner_user_id=%s)
                   OR (scope='portal' AND is_published))""", (profile_id, user_id))
    row = c.fetchone()
    if not row:
        raise MobileNotificationError("not_found", 404)
    return row


def _suspended_reason(c, user_id: int, has_access: bool) -> str | None:
    c.execute("""SELECT paused FROM proc.mobile_notification_preference
                 WHERE user_id=%s""", (user_id,))
    pref = c.fetchone()
    if pref and pref["paused"]:
        return "globally_paused"
    if not has_access:
        return "not_entitled"
    c.execute("""SELECT 1 FROM proc.mobile_device WHERE user_id=%s
                 AND enabled AND revoked_at IS NULL
                 AND permission_status IN ('granted','provisional')
                 AND push_token_ciphertext IS NOT NULL LIMIT 1""", (user_id,))
    if not c.fetchone():
        return "no_active_device"
    return None


def _push_dto(c, row: dict | None, profile_id: int, user_id: int,
              has_access: bool) -> PushAlert:
    return PushAlert(
        id=str(row["id"]) if row else None,
        saved_search_id=str(profile_id),
        active=bool(row and row["is_active"]),
        delivery_mode=row["delivery_mode"] if row else "daily",
        new_matches=bool(row["new_matches"]) if row else True,
        deadlines=bool(row["deadlines"]) if row else True,
        lead_days=list(row["lead_days"]) if row else [7, 1],
        delivery_suspended_reason=(
            _suspended_reason(c, user_id, has_access)
            if row and row["is_active"] else None),
        updated_at=(row["updated_at"] if row else dt.datetime.now(dt.timezone.utc)),
    )


def get_push_alert(c, *, user_id: int, profile_id: int,
                   has_access: bool) -> PushAlert:
    _profile(c, user_id, profile_id)
    c.execute("""SELECT * FROM proc.push_subscription
                 WHERE user_id=%s AND search_profile_id=%s""",
              (user_id, profile_id))
    return _push_dto(c, c.fetchone(), profile_id, user_id, has_access)


def put_push_alert(c, *, user_id: int, profile_id: int, has_access: bool,
                   payload: PushAlertInput) -> PushAlert:
    _profile(c, user_id, profile_id)
    with mobile_auth.transaction(c):
        c.execute("""SELECT * FROM proc.push_subscription
                     WHERE user_id=%s AND search_profile_id=%s FOR UPDATE""",
                  (user_id, profile_id))
        previous = c.fetchone()
        newly_active = payload.active and not (previous and previous["is_active"])
        cursor = dt.datetime.now(dt.timezone.utc) if newly_active else (
            previous["last_cursor"] if previous else None)
        c.execute(
            """INSERT INTO proc.push_subscription
                 (user_id,search_profile_id,is_active,delivery_mode,new_matches,
                  deadlines,lead_days,last_cursor,reactivated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (user_id,search_profile_id) DO UPDATE SET
                 is_active=excluded.is_active,delivery_mode=excluded.delivery_mode,
                 new_matches=excluded.new_matches,deadlines=excluded.deadlines,
                 lead_days=excluded.lead_days,last_cursor=excluded.last_cursor,
                 reactivated_at=CASE WHEN excluded.is_active AND NOT proc.push_subscription.is_active
                                     THEN now() ELSE proc.push_subscription.reactivated_at END,
                 updated_at=now()
               RETURNING *""",
            (user_id, profile_id, payload.active, payload.delivery_mode,
             payload.new_matches, payload.deadlines, payload.lead_days, cursor,
             cursor if newly_active else None),
        )
        row = c.fetchone()
    return _push_dto(c, row, profile_id, user_id, has_access)


def delete_push_alert(c, *, user_id: int, profile_id: int) -> None:
    _profile(c, user_id, profile_id)
    c.execute("""DELETE FROM proc.push_subscription
                 WHERE user_id=%s AND search_profile_id=%s""", (user_id, profile_id))


def _notification_dto(row: dict) -> NotificationItem:
    context = row.get("context") or {}
    names = context.get("saved_search_names") or []
    names = [str(name)[:120] for name in names if isinstance(name, str)][:10]
    mobile_context = context.get("mobile_context")
    if not isinstance(mobile_context, str):
        mobile_context = None
    duplicate_of = context.get("possible_duplicate_of")
    if not isinstance(duplicate_of, str):
        duplicate_of = None
    return NotificationItem(
        id=str(row["id"]), type=row["event_type"], title=row["title"],
        body=row["body"], saved_search_names=names,
        possible_duplicate_of=duplicate_of,
        created_at=row["created_at"], read=row["read_at"] is not None,
        read_at=row["read_at"], expires_at=row["expires_at"],
        target=NotificationTarget(kind=row["target_kind"], id=row["target_id"],
                                  context=mobile_context),
    )


def _cursor_fingerprint(user_id: int, state: str, limit: int) -> str:
    raw = json.dumps([user_id, state, limit], separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def list_notifications(c, *, user_id: int, state: str, cursor_token: str | None,
                       limit: int) -> NotificationPage:
    fingerprint = _cursor_fingerprint(user_id, state, limit)
    before_at = before_id = None
    if cursor_token:
        payload = mobile_search.decode_signed(cursor_token, "notification")
        if payload.get("fingerprint") != fingerprint:
            raise MobileNotificationError("invalid_cursor", 422)
        try:
            snapshot = dt.datetime.fromisoformat(payload["snapshot"])
            before_at = dt.datetime.fromisoformat(payload["before_at"])
            before_id = int(payload["before_id"])
            if snapshot.tzinfo is None or before_at.tzinfo is None or before_id < 1:
                raise ValueError
        except Exception as exc:
            raise MobileNotificationError("invalid_cursor", 422) from exc
    else:
        snapshot = dt.datetime.now(dt.timezone.utc)

    where = "user_id=%s AND deleted_at IS NULL AND expires_at>now() AND created_at<=%s"
    params: list = [user_id, snapshot]
    if state == "unread":
        where += " AND read_at IS NULL"
    if before_at is not None:
        where += " AND (created_at,id)<(%s,%s)"
        params.extend([before_at, before_id])
    params.append(limit + 1)
    c.execute(f"""SELECT * FROM proc.notification_event WHERE {where}
                  ORDER BY created_at DESC,id DESC LIMIT %s""", params)
    rows = c.fetchall()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = mobile_search.encode_signed({
            "kind": "notification", "snapshot": snapshot.isoformat(),
            "before_at": last["created_at"].isoformat(),
            "before_id": int(last["id"]), "fingerprint": fingerprint,
            "expires_at": time.time() + INBOX_CURSOR_TTL_SECONDS,
        })
    c.execute("""SELECT count(*) AS n FROM proc.notification_event
                 WHERE user_id=%s AND deleted_at IS NULL AND read_at IS NULL
                   AND expires_at>now()""", (user_id,))
    unread = int(c.fetchone()["n"])
    return NotificationPage(items=[_notification_dto(row) for row in page],
                            next_cursor=next_cursor, unread_count=unread)


def update_notification(c, *, user_id: int, notification_id: int,
                        read: bool) -> NotificationItem:
    c.execute("""UPDATE proc.notification_event
                 SET read_at=CASE WHEN %s THEN coalesce(read_at,now()) ELSE NULL END
                 WHERE id=%s AND user_id=%s AND deleted_at IS NULL AND expires_at>now()
                 RETURNING *""", (read, notification_id, user_id))
    row = c.fetchone()
    if not row:
        raise MobileNotificationError("not_found", 404)
    return _notification_dto(row)


def mark_all_read(c, user_id: int) -> int:
    c.execute("""WITH changed AS (
                   UPDATE proc.notification_event SET read_at=now()
                   WHERE user_id=%s AND deleted_at IS NULL AND read_at IS NULL
                     AND expires_at>now() RETURNING id)
                 SELECT count(*) AS n FROM changed""", (user_id,))
    return int(c.fetchone()["n"])


def delete_notification(c, *, user_id: int, notification_id: int) -> None:
    c.execute("""UPDATE proc.notification_event SET deleted_at=now()
                 WHERE id=%s AND user_id=%s AND deleted_at IS NULL AND expires_at>now()
                 RETURNING id""", (notification_id, user_id))
    if not c.fetchone():
        raise MobileNotificationError("not_found", 404)


def cleanup_expired(c) -> dict[str, int]:
    statements = {
        "notifications_expired": """
          UPDATE proc.notification_event SET deleted_at=coalesce(deleted_at,now())
          WHERE expires_at<=now() AND deleted_at IS NULL RETURNING id""",
        "notifications_deleted": """
          DELETE FROM proc.notification_event
          WHERE deleted_at<now()-interval '7 days' RETURNING id""",
        "deliveries": """
          DELETE FROM proc.notification_delivery
          WHERE terminal_at<now()-interval '30 days' RETURNING id""",
    }
    counts = {}
    with mobile_auth.transaction(c):
        for name, sql in statements.items():
            c.execute(f"WITH changed AS ({sql}) SELECT count(*) AS n FROM changed")
            counts[name] = int(c.fetchone()["n"])
    return counts
