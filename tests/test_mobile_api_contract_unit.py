"""Contract checks for the Slice-A API that do not require PostgreSQL."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import jsonschema
import yaml
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.api_v1.errors import ApiError, api_error_handler, validation_error_handler
from app.api_v1.router import make_router
from app.api_v1.schemas import (
    ActDetail,
    ActSearchResponse,
    EntitySummary,
    EmailAlert,
    EmailAlertRunPage,
    FavoriteItem,
    FavoritePage,
    FavoriteState,
    LookupResponse,
    MeResponse,
    MfaChallengeResponse,
    MfaEnrollment,
    MfaStatus,
    SavedSearch,
    Device,
    NotificationItem,
    NotificationPage,
    NotificationSettings,
    PushAlert,
    TokenResponse,
)


ROOT = Path(__file__).resolve().parent.parent


def _contract():
    return yaml.safe_load((ROOT / "docs/specs/mobile-api-v1.openapi.yaml").read_text())


def _validate_component(name: str, instance: dict) -> None:
    contract = _contract()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{name}",
        "components": contract["components"],
    }
    jsonschema.Draft202012Validator(schema).validate(instance)


def _me() -> MeResponse:
    return MeResponse(
        id="42", username="mobile-user", email=None, role="customer",
        language="el",
        entitlement={
            "has_access": True, "status": "tester", "product_code": "pro",
            "expires_at": None,
        },
        security={"mfa_enabled": False, "password_change_required": False},
    )


def test_implemented_response_models_conform_to_committed_openapi():
    token = TokenResponse(
        access_token="mat_example", access_expires_in=900,
        refresh_token="mrt_example",
        refresh_expires_at=dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc),
        user=_me(),
    )
    challenge = MfaChallengeResponse(
        challenge_id="mch_example-challenge", expires_in=300,
        methods=["totp", "recovery_code"],
    )
    _validate_component("TokenResponse", token.model_dump(mode="json"))
    _validate_component("MfaChallengeResponse", challenge.model_dump(mode="json"))
    mfa_status = MfaStatus(enabled=True, recovery_codes_remaining=8)
    enrollment = MfaEnrollment(
        enrollment_id="men_example-enrollment", secret="JBSWY3DPEHPK3PXP",
        otpauth_uri="otpauth://totp/KHMDHS%3Amobile-user?secret=JBSWY3DPEHPK3PXP",
        expires_in=300,
    )
    _validate_component("MfaStatus", mfa_status.model_dump(mode="json"))
    _validate_component("MfaEnrollment", enrollment.model_dump(mode="json"))
    _validate_component("MeResponse", _me().model_dump(mode="json"))
    lookups = LookupResponse(
        version="abc123", language="en",
        act_types=[{"code": "notice", "label": "Notice"}],
        sources=[], authorities=[], contract_types=[], procedure_types=[],
        nuts_regions=[], categories=[],
    )
    search = ActSearchResponse(
        items=[], next_cursor=None,
        snapshot_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
        totals={"count": 0, "value": {"amount": "0"}},
    )
    detail = ActDetail(
        adam="26PROC000001", type={"code": "notice", "label": "Notice"},
        title="Example", source={"code": "khmdhs", "label": "KIMDIS"},
        cancelled=False, modified=False, sections=[], links=[],
    )
    _validate_component("LookupResponse", lookups.model_dump(mode="json"))
    _validate_component("ActSearchResponse", search.model_dump(mode="json"))
    _validate_component("ActDetail", detail.model_dump(mode="json"))
    favorite_state = FavoriteState(
        adam="26PROC000001", favorited=True,
        favorited_at=dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc),
    )
    favorite_item = FavoriteItem(
        act={
            "adam": "26PROC000001",
            "type": {"code": "notice", "label": "Notice"},
            "title": "Example",
            "source": {"code": "khmdhs", "label": "KIMDIS"},
            "cancelled": False,
            "modified": False,
            "favorited": True,
        },
        favorited_at=dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc),
    )
    favorite_page = FavoritePage(
        items=[favorite_item], next_cursor=None, total=1)
    _validate_component(
        "FavoriteState", favorite_state.model_dump(mode="json"))
    _validate_component(
        "FavoritePage", favorite_page.model_dump(mode="json"))
    entity = EntitySummary(
        kind="authority", id="AUTH-1", name="Authority",
        headline={"act_count": 0, "contract_value": {"amount": "0"}},
    )
    saved = SavedSearch(
        id="7", name="My search", scope="customer", owned=True,
        editable=True, filters={"types": ["notice"]},
        created_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
        updated_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
    )
    _validate_component("EntitySummary", entity.model_dump(mode="json"))
    email_alert = EmailAlert(
        saved_search_id="7", exists=False, active=False, layout="list",
        schedule_id=None, language="en", max_results=25,
        lead_days=[7, 1], send_empty=False,
        schedule_label="Daily 08:00 (Europe/Athens)",
        last_sent_at=None, additional_recipients=[], recent_runs=[],
        schedule_options=[{
            "id": "3", "label": "Daily 08:00 (Europe/Athens)",
            "is_default": True,
        }],
    )
    _validate_component("EmailAlert", email_alert.model_dump(mode="json"))
    email_run = EmailAlertRunPage(
        run={
            "id": "11", "status": "sent", "result_count": 1,
            "started_at": dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc),
        },
        saved_search_id="7", saved_search_name="My search",
        filters={"types": ["notice"]},
        items=[{"act": favorite_item.act, "included_in_email": True}],
        total=1, next_cursor=None,
    )
    _validate_component(
        "EmailAlertRunPage", email_run.model_dump(mode="json"))
    _validate_component(
        "SavedSearch", saved.model_dump(mode="json", exclude_none=True))
    settings = NotificationSettings(
        updated_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc))
    device = Device(
        id="8", installation_id="1d8f28ce-3d15-4b35-9ed1-563728586edf",
        platform="ios", display_name="iPhone", permission_status="granted",
        enabled=True, current=True,
        last_seen_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
    )
    push = PushAlert(
        id=None, saved_search_id="7",
        updated_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc))
    item = NotificationItem(
        id="9", type="new_match", title="New match", body="One new notice",
        created_at=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
        read=False,
        expires_at=dt.datetime(2026, 12, 13, tzinfo=dt.timezone.utc),
        target={"kind": "act", "id": "26PROC000001"},
    )
    page = NotificationPage(items=[item], unread_count=1)
    _validate_component("NotificationSettings", settings.model_dump(mode="json"))
    _validate_component("Device", device.model_dump(mode="json"))
    _validate_component("PushAlert", push.model_dump(mode="json"))
    _validate_component("NotificationPage", page.model_dump(mode="json"))


def test_timestamps_are_written_in_utc_whatever_zone_the_db_used():
    """The app pool sets every session to Europe/Athens, so a timestamptz comes
    back as 11:00+03:00. The contract's Timestamp is an instant in UTC — the
    same 08:00 must go out as 08:00Z, on every model."""
    from zoneinfo import ZoneInfo
    athens = dt.datetime(2026, 9, 15, 11, 0, tzinfo=ZoneInfo("Europe/Athens"))
    alert = EmailAlert(
        saved_search_id="7", exists=True, active=True, layout="list",
        schedule_id=None, language="en", max_results=25, lead_days=[7, 1],
        send_empty=False, last_sent_at=athens, schedule_options=[],
        recent_runs=[{"id": "1", "status": "sent", "result_count": 3,
                      "started_at": athens}],
    )
    body = alert.model_dump(mode="json")
    assert body["last_sent_at"] == "2026-09-15T08:00:00Z"
    assert body["recent_runs"][0]["started_at"] == "2026-09-15T08:00:00Z"
    naive = FavoriteState(adam="26PROC000001", favorited=True,
                          favorited_at=dt.datetime(2026, 9, 15, 8))
    assert naive.model_dump(mode="json")["favorited_at"] == "2026-09-15T08:00:00Z"


def test_no_model_declares_a_bare_datetime():
    """A new field typed dt.datetime would silently leak the Athens offset
    again. Every datetime field must carry the Timestamp validator."""
    import inspect
    import typing

    from pydantic import BaseModel

    from app.api_v1 import schemas
    def utc(metadata) -> bool:
        return any(getattr(m, "func", None) is schemas._as_utc for m in metadata)

    def has_bare(tp, wrapped: bool) -> bool:
        # `Timestamp` alone arrives unwrapped (validator in info.metadata);
        # `Timestamp | None` keeps the Annotated inside the union.
        if typing.get_origin(tp) is typing.Annotated:
            inner, *metadata = typing.get_args(tp)
            return has_bare(inner, wrapped or utc(metadata))
        if tp is dt.datetime:
            return not wrapped
        return any(has_bare(arg, wrapped) for arg in typing.get_args(tp))

    checked, bare = 0, []
    for name, model in inspect.getmembers(schemas, inspect.isclass):
        if not issubclass(model, BaseModel) or model.__module__ != schemas.__name__:
            continue
        for field, info in model.model_fields.items():
            if "datetime" in repr(info.annotation):
                checked += 1
            if has_bare(info.annotation, utc(info.metadata)):
                bare.append(f"{name}.{field}")
    assert bare == []
    assert checked >= 20        # the walk really reached the datetime fields


def test_runtime_openapi_keeps_committed_operation_ids_and_bearer_scheme():
    def unused_cursor():
        raise AssertionError("OpenAPI generation must not access the database")

    app = FastAPI()
    app.include_router(make_router(unused_cursor, lambda _request: "127.0.0.1"))
    schema = app.openapi()
    expected = {
        ("/api/v1/auth/login", "post"): "mobileLogin",
        ("/api/v1/auth/mfa/verify", "post"): "verifyMobileMfa",
        ("/api/v1/auth/refresh", "post"): "refreshMobileSession",
        ("/api/v1/auth/logout", "post"): "logoutMobileSession",
        ("/api/v1/me", "get"): "getCurrentMobileUser",
        ("/api/v1/account/password", "post"): "changeMobilePassword",
        ("/api/v1/account/mfa", "get"): "getMobileMfaStatus",
        ("/api/v1/account/mfa", "delete"): "disableMobileMfa",
        ("/api/v1/account/mfa/enrollment", "post"):
            "startMobileMfaEnrollment",
        ("/api/v1/account/mfa/enrollment/confirm", "post"):
            "confirmMobileMfaEnrollment",
        ("/api/v1/lookups", "get"): "getMobileLookups",
        ("/api/v1/authority-options", "get"):
            "searchMobileAuthorityOptions",
        ("/api/v1/acts/search", "post"): "searchMobileActs",
        ("/api/v1/acts/{adam}", "get"): "getMobileAct",
        ("/api/v1/authorities/{orgId}", "get"): "getMobileAuthority",
        ("/api/v1/contractors/{vat}", "get"): "getMobileContractor",
        ("/api/v1/saved-searches", "get"): "listMobileSavedSearches",
        ("/api/v1/saved-searches", "post"): "createMobileSavedSearch",
        ("/api/v1/saved-searches/{savedSearchId}", "patch"):
            "renameMobileSavedSearch",
        ("/api/v1/saved-searches/{savedSearchId}", "delete"):
            "deleteMobileSavedSearch",
        ("/api/v1/saved-searches/{savedSearchId}/email-alert", "get"):
            "getMobileEmailAlert",
        ("/api/v1/saved-searches/{savedSearchId}/email-alert", "put"):
            "putMobileEmailAlert",
        ("/api/v1/saved-searches/{savedSearchId}/email-alert", "delete"):
            "deleteMobileEmailAlert",
        ("/api/v1/saved-searches/{savedSearchId}/email-alert/runs/{runId}", "get"):
            "getMobileEmailAlertRun",
        ("/api/v1/notification-settings", "get"):
            "getMobileNotificationSettings",
        ("/api/v1/notification-settings", "put"):
            "putMobileNotificationSettings",
        ("/api/v1/devices", "get"): "listMobileDevices",
        ("/api/v1/devices", "post"): "registerMobileDevice",
        ("/api/v1/devices/{deviceId}", "delete"): "revokeMobileDevice",
        ("/api/v1/saved-searches/{savedSearchId}/push-alert", "get"):
            "getMobilePushAlert",
        ("/api/v1/saved-searches/{savedSearchId}/push-alert", "put"):
            "putMobilePushAlert",
        ("/api/v1/saved-searches/{savedSearchId}/push-alert", "delete"):
            "deleteMobilePushAlert",
        ("/api/v1/notifications", "get"): "listMobileNotifications",
        ("/api/v1/notifications/read-all", "post"):
            "markAllMobileNotificationsRead",
        ("/api/v1/notifications/{notificationId}", "patch"):
            "updateMobileNotification",
        ("/api/v1/notifications/{notificationId}", "delete"):
            "deleteMobileNotification",
    }
    for (path, method), operation_id in expected.items():
        assert schema["paths"][path][method]["operationId"] == operation_id
    assert schema["paths"]["/api/v1/me"]["get"]["security"] == [
        {"MobileBearer": []}
    ]


def test_production_flag_refuses_to_start_api_without_hash_key(monkeypatch):
    monkeypatch.setenv("MOBILE_API_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MOBILE_TOKEN_HASH_KEY", raising=False)

    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(make_router(
        lambda: (_ for _ in ()).throw(AssertionError("database accessed")),
        lambda _request: "127.0.0.1",
    ))
    response = TestClient(app).get("/api/v1/me")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
