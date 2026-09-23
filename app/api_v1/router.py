from __future__ import annotations

import logging
import os

from fastapi import (APIRouter, Depends, Header, HTTPException, Path, Query,
                     Request)
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import auth as account_auth
from app import mobile_auth
from app import mobile_search
from app import mobile_saved_searches
from app import mobile_email_alerts
from app import mobile_notifications
from app import mobile_favorites
from app import obs as _obs
from app.api_v1.errors import ApiError, language
from app.api_v1.schemas import (
    LoginRequest,
    LookupItem,
    LookupResponse,
    MeResponse,
    MfaChallengeResponse,
    MfaDisableRequest,
    MfaEnabledResponse,
    MfaEnrollment,
    MfaEnrollmentConfirmRequest,
    MfaEnrollmentStartRequest,
    MfaStatus,
    MfaVerifyRequest,
    PasswordChangeRequest,
    RefreshRequest,
    ActSearchRequest,
    ActSearchResponse,
    ActDetail,
    EntitySummary,
    EmailAlert,
    EmailAlertInput,
    EmailAlertRunPage,
    SavedSearch,
    SavedSearchCreate,
    SavedSearchList,
    SavedSearchRename,
    Device,
    DeviceList,
    DeviceRegistration,
    FavoritePage,
    FavoriteState,
    NotificationItem,
    NotificationPage,
    NotificationSettings,
    NotificationSettingsInput,
    NotificationUpdate,
    PushAlert,
    PushAlertInput,
    UpdatedCount,
    TokenResponse,
)


def _enabled() -> bool:
    return (os.environ.get("MOBILE_API_ENABLED") or "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def require_mobile_api():
    if not _enabled():
        # A disabled API is intentionally indistinguishable from an absent one.
        raise ApiError("not_found", 404)
    if ((os.environ.get("RENDER") or
         os.environ.get("APP_ENV", "").lower() == "production")
            and not mobile_auth.configured()):
        raise ApiError("service_unavailable", 503)


def make_router(cursor, client_ip, rate_limit=None) -> APIRouter:
    bearer = HTTPBearer(auto_error=False, scheme_name="MobileBearer")
    router = APIRouter(
        prefix="/api/v1", tags=["mobile-v1"],
        dependencies=[Depends(require_mobile_api)],
    )

    def _me(c, user: dict, lang: str) -> MeResponse:
        mfa = account_auth.get_mfa(c, user["id"])
        return MeResponse(
            id=str(user["id"]), username=user["username"], email=user.get("email"),
            role=user["role"], language=lang,
            entitlement={
                "has_access": bool(user.get("has_access")),
                "status": user.get("status") or "none",
                "product_code": user.get("sub_product"),
                "expires_at": user.get("sub_expires_at"),
            },
            security={
                "mfa_enabled": bool(mfa and mfa.get("mfa_enabled")),
                "password_change_required": bool(user.get("must_change_password")),
            },
        )

    def _token_response(c, user: dict, issued, lang: str) -> TokenResponse:
        return TokenResponse(
            access_token=issued.access_token,
            access_expires_in=mobile_auth.ACCESS_TTL_SECONDS,
            refresh_token=issued.refresh_token,
            refresh_expires_at=issued.refresh_expires_at,
            user=_me(c, user, lang),
        )

    def _audit(request: Request, event: str, outcome: str) -> None:
        level = (logging.INFO if outcome in {"success", "mfa_required"}
                 else logging.WARNING)
        _obs.log_event(level, "mobile_auth", request_id=getattr(
            request.state, "request_id", "unknown"), auth_event=event,
            outcome=outcome)

    def _translate_auth_error(exc: mobile_auth.MobileAuthError):
        raise ApiError(exc.code, exc.status) from exc

    def _limit(request: Request, bucket: str) -> None:
        if rate_limit is None:
            return
        try:
            rate_limit(request, bucket)
        except HTTPException as exc:
            if exc.status_code == 429:
                raise ApiError(
                    "rate_limited", 429, headers=dict(exc.headers or {})) from exc
            raise

    def current_identity(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ):
        if not credentials or credentials.scheme.lower() != "bearer":
            raise ApiError("token_expired", 401)
        raw = credentials.credentials.strip()
        if not raw:
            raise ApiError("token_expired", 401)
        try:
            with cursor() as c:
                identity = mobile_auth.authenticate_access(c, raw)
        except mobile_auth.MobileAuthConfigurationError as exc:
            raise ApiError("service_unavailable", 503) from exc
        except mobile_auth.MobileAuthError as exc:
            _translate_auth_error(exc)
        request.state.mobile_identity = identity
        return identity

    def current_entitled_identity(identity=Depends(current_identity)):
        if not identity.user.get("has_access"):
            raise ApiError("access_expired", 403)
        return identity

    @router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def mobile_demo():
        if os.environ.get("ENABLE_DOCS", "0") != "1":
            raise ApiError("not_found", 404)
        from app.api_v1.demo import html
        return HTMLResponse(html())

    @router.post(
        "/auth/login", response_model=TokenResponse,
        operation_id="mobileLogin",
        responses={202: {"model": MfaChallengeResponse}},
    )
    def login(payload: LoginRequest, request: Request):
        lang = language(request)
        username = payload.username.strip()
        key = f"{username.lower()}|{client_ip(request)}"
        device = payload.device.model_dump(mode="json")
        try:
            with cursor() as c:
                retry = account_auth.throttle_blocked(c, key)
                if retry:
                    _audit(request, "login", "rate_limited")
                    raise ApiError("rate_limited", 429,
                                   headers={"Retry-After": str(retry)})
                user = account_auth.get_by_username(c, username)
                if not (user and user.get("is_active") and
                        account_auth.verify_password(payload.password,
                                                     user["password_hash"])):
                    account_auth.throttle_fail(c, key)
                    _audit(request, "login", "invalid_credentials")
                    raise ApiError("invalid_credentials", 401)
                account_auth.throttle_reset(c, key)
                user = dict(user)
                if user.get("must_change_password"):
                    _audit(request, "login", "password_change_required")
                    raise ApiError("password_change_required", 403)
                mfa = account_auth.get_mfa(c, user["id"])
                if mfa and mfa.get("mfa_enabled") and mfa.get("mfa_secret"):
                    challenge, _expires = mobile_auth.create_mfa_challenge(
                        c, user["id"], device)
                    body = MfaChallengeResponse(
                        challenge_id=challenge,
                        expires_in=mobile_auth.MFA_CHALLENGE_TTL_SECONDS,
                        methods=["totp", "recovery_code"],
                    )
                    _audit(request, "login", "mfa_required")
                    return JSONResponse(
                        body.model_dump(mode="json"), status_code=202,
                        headers={"Cache-Control": "no-store"})
                account_auth.touch_last_login(c, user["id"])
                live_user = account_auth.load_user(c, user["id"])
                issued = mobile_auth.issue_session(c, dict(live_user), device)
                _audit(request, "login", "success")
                return _token_response(c, dict(live_user), issued, lang)
        except mobile_auth.MobileAuthConfigurationError as exc:
            raise ApiError("service_unavailable", 503) from exc

    @router.post("/auth/mfa/verify", response_model=TokenResponse,
                 operation_id="verifyMobileMfa")
    def verify_mfa(payload: MfaVerifyRequest, request: Request):
        try:
            with cursor() as c:
                user, issued = mobile_auth.complete_mfa_challenge(
                    c, payload.challenge_id, payload.code)
                _audit(request, "mfa_verify", "success")
                return _token_response(c, user, issued, language(request))
        except mobile_auth.MobileAuthConfigurationError as exc:
            raise ApiError("service_unavailable", 503) from exc
        except mobile_auth.MobileAuthError as exc:
            _audit(request, "mfa_verify", exc.code)
            _translate_auth_error(exc)

    @router.post("/auth/refresh", response_model=TokenResponse,
                 operation_id="refreshMobileSession")
    def refresh(payload: RefreshRequest, request: Request):
        try:
            with cursor() as c:
                user, issued = mobile_auth.rotate_refresh(
                    c, payload.refresh_token, str(payload.installation_id))
                _audit(request, "refresh", "success")
                return _token_response(c, user, issued, language(request))
        except mobile_auth.MobileAuthConfigurationError as exc:
            raise ApiError("service_unavailable", 503) from exc
        except mobile_auth.MobileAuthError as exc:
            _audit(request, "refresh", exc.code)
            _translate_auth_error(exc)

    @router.post("/auth/logout", status_code=204,
                 operation_id="logoutMobileSession",
                 dependencies=[Depends(current_identity)])
    def logout(request: Request):
        identity = request.state.mobile_identity
        with cursor() as c:
            mobile_auth.revoke_session(c, identity.session_id, "logout")
        _audit(request, "logout", "success")
        return Response(status_code=204)

    @router.get("/me", response_model=MeResponse,
                operation_id="getCurrentMobileUser")
    def me(request: Request, identity=Depends(current_identity)):
        with cursor() as c:
            return _me(c, identity.user, language(request))

    @router.post("/account/password", status_code=204,
                 operation_id="changeMobilePassword")
    def change_mobile_password(
        payload: PasswordChangeRequest,
        request: Request,
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_account_security")
        user_id = int(identity.user["id"])
        with cursor() as c:
            full = account_auth.get_by_username(c, identity.user["username"])
            if not (full and account_auth.verify_password(
                    payload.current_password, full["password_hash"])):
                raise ApiError("invalid_credentials", 401)
            if not account_auth.password_ok(payload.new_password):
                raise ApiError(
                    "validation_error", 422,
                    fields=[{"field": "new_password", "code": "password_policy"}],
                )
            with mobile_auth.transaction(c):
                account_auth.set_password(c, user_id, payload.new_password)
                mobile_auth.preserve_current_session_after_security_change(
                    c, user_id=user_id,
                    current_session_id=identity.session_id)
        _audit(request, "password_change", "success")
        return Response(status_code=204)

    @router.get("/account/mfa", response_model=MfaStatus,
                operation_id="getMobileMfaStatus")
    def get_mobile_mfa_status(identity=Depends(current_identity)):
        with cursor() as c:
            mfa = account_auth.get_mfa(c, int(identity.user["id"]))
        codes = list((mfa or {}).get("mfa_recovery_codes") or [])
        return MfaStatus(
            enabled=bool(mfa and mfa.get("mfa_enabled")),
            recovery_codes_remaining=len(codes),
        )

    @router.post("/account/mfa/enrollment", response_model=MfaEnrollment,
                 operation_id="startMobileMfaEnrollment")
    def start_mobile_mfa_enrollment(
        payload: MfaEnrollmentStartRequest,
        request: Request,
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_account_security")
        user_id = int(identity.user["id"])
        with cursor() as c:
            full = account_auth.get_by_username(c, identity.user["username"])
            if not (full and account_auth.verify_password(
                    payload.current_password, full["password_hash"])):
                raise ApiError("invalid_credentials", 401)
            mfa = account_auth.get_mfa(c, user_id)
            if mfa and mfa.get("mfa_enabled"):
                raise ApiError("conflict", 409)
            enrollment_id, secret, uri = mobile_auth.start_mfa_enrollment(
                c, user_id=user_id, username=identity.user["username"])
        return MfaEnrollment(
            enrollment_id=enrollment_id,
            secret=secret,
            otpauth_uri=uri,
            expires_in=mobile_auth.MFA_CHALLENGE_TTL_SECONDS,
        )

    @router.post("/account/mfa/enrollment/confirm",
                 response_model=MfaEnabledResponse,
                 operation_id="confirmMobileMfaEnrollment")
    def confirm_mobile_mfa_enrollment(
        payload: MfaEnrollmentConfirmRequest,
        request: Request,
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_account_security")
        try:
            with cursor() as c:
                codes = mobile_auth.confirm_mfa_enrollment(
                    c, user_id=int(identity.user["id"]),
                    current_session_id=identity.session_id,
                    raw_enrollment=payload.enrollment_id,
                    code=payload.code,
                )
        except mobile_auth.MobileAuthError as exc:
            _translate_auth_error(exc)
        _audit(request, "mfa_enable", "success")
        return MfaEnabledResponse(recovery_codes=codes)

    @router.delete("/account/mfa", status_code=204,
                   operation_id="disableMobileMfa")
    def disable_mobile_mfa(
        payload: MfaDisableRequest,
        request: Request,
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_account_security")
        user_id = int(identity.user["id"])
        with cursor() as c:
            full = account_auth.get_by_username(c, identity.user["username"])
            if not (full and account_auth.verify_password(
                    payload.current_password, full["password_hash"])):
                raise ApiError("invalid_credentials", 401)
            with mobile_auth.transaction(c):
                mfa = account_auth.get_mfa(c, user_id)
                valid_code = bool(mfa and mfa.get("mfa_enabled") and (
                    account_auth.verify_totp(mfa.get("mfa_secret"), payload.code)
                    or account_auth.consume_recovery_code(c, user_id, payload.code)
                ))
                if not valid_code:
                    raise ApiError("invalid_mfa", 401)
                account_auth.disable_mfa(c, user_id)
                mobile_auth.preserve_current_session_after_security_change(
                    c, user_id=user_id,
                    current_session_id=identity.session_id)
        _audit(request, "mfa_disable", "success")
        return Response(status_code=204)

    @router.get("/lookups", response_model=LookupResponse,
                operation_id="getMobileLookups")
    def get_lookups(request: Request, _identity=Depends(current_identity)):
        try:
            body, etag = mobile_search.lookups(language(request))
        except mobile_search.MobileSearchError as exc:
            raise ApiError(exc.code, 503) from exc
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(body.model_dump(mode="json"), headers={"ETag": etag})

    @router.post("/acts/search", response_model=ActSearchResponse,
                 operation_id="searchMobileActs")
    def search_acts(payload: ActSearchRequest, request: Request,
                    identity=Depends(current_entitled_identity)):
        _limit(request, "mobile_search")
        try:
            result = mobile_search.search(cursor, payload, language(request))
            with cursor() as c:
                mobile_favorites.mark_items(
                    c, int(identity.user["id"]), result.items)
            return result
        except mobile_search.MobileSearchError as exc:
            status = 503 if exc.code == "service_unavailable" else 422
            raise ApiError(exc.code, status, fields=exc.fields) from exc

    @router.get("/acts/{adam}", response_model=ActDetail,
                operation_id="getMobileAct")
    def get_act(
        request: Request,
        adam: str = Path(min_length=3, max_length=64),
        context: str | None = Query(default=None, max_length=1024),
        identity=Depends(current_entitled_identity),
    ):
        from app import act_service

        _limit(request, "mobile_act")
        q, cpv = mobile_search.read_context(context)
        with cursor() as c:
            detail = act_service.mobile_detail(
                c, adam, language(request), q=q, cpv=cpv)
            if detail is not None:
                mobile_favorites.mark_detail(
                    c, int(identity.user["id"]), detail)
        if detail is None:
            raise ApiError("not_found", 404)
        return detail

    @router.get("/favorites", response_model=FavoritePage,
                operation_id="listMobileFavorites")
    def list_mobile_favorites(
        request: Request,
        limit: int = Query(default=20, ge=1, le=50),
        page_cursor: str | None = Query(
            default=None, alias="cursor", max_length=2048),
        identity=Depends(current_entitled_identity),
    ):
        _limit(request, "mobile_favorites")
        try:
            with cursor() as c:
                return mobile_favorites.list_favorites(
                    c, user_id=int(identity.user["id"]),
                    lang=language(request), limit=limit, cursor=page_cursor)
        except mobile_favorites.MobileFavoriteError as exc:
            raise ApiError(exc.code, exc.status) from exc

    @router.put("/favorites/{adam}", response_model=FavoriteState,
                operation_id="putMobileFavorite")
    def put_mobile_favorite(
        request: Request,
        adam: str = Path(min_length=3, max_length=64),
        identity=Depends(current_entitled_identity),
    ):
        _limit(request, "mobile_favorite_write")
        try:
            with cursor() as c:
                return mobile_favorites.put_favorite(
                    c, user_id=int(identity.user["id"]), adam=adam)
        except mobile_favorites.MobileFavoriteError as exc:
            raise ApiError(exc.code, exc.status) from exc

    @router.delete("/favorites/{adam}", status_code=204,
                   operation_id="deleteMobileFavorite")
    def delete_mobile_favorite(
        request: Request,
        adam: str = Path(min_length=3, max_length=64),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_favorite_write")
        with cursor() as c:
            mobile_favorites.delete_favorite(
                c, user_id=int(identity.user["id"]), adam=adam)
        return Response(status_code=204)

    @router.get("/authority-options", response_model=list[LookupItem],
                operation_id="searchMobileAuthorityOptions")
    def search_authority_options(
        request: Request,
        q: str = Query(min_length=2, max_length=120),
        identity=Depends(current_entitled_identity),
    ):
        from app import entity_service

        _limit(request, "mobile_entity")
        if len(q.strip()) < 2:
            raise ApiError(
                "validation_error", 422,
                fields=[{"field": "q", "code": "too_short"}],
            )
        with cursor() as c:
            return entity_service.authority_options(c, q, limit=20)

    @router.get("/authorities/{orgId}", response_model=EntitySummary,
                operation_id="getMobileAuthority")
    def get_authority(
        request: Request,
        orgId: str = Path(min_length=1, max_length=128),
        identity=Depends(current_entitled_identity),
    ):
        from app import entity_service

        _limit(request, "mobile_entity")
        with cursor() as c:
            summary = entity_service.authority_summary(
                c, orgId, language(request))
            if summary is not None:
                mobile_favorites.mark_items(
                    c, int(identity.user["id"]), summary.recent_acts)
        if summary is None:
            raise ApiError("not_found", 404)
        return summary

    @router.get("/contractors/{vat}", response_model=EntitySummary,
                operation_id="getMobileContractor")
    def get_contractor(
        request: Request,
        vat: str = Path(min_length=1, max_length=32),
        identity=Depends(current_entitled_identity),
    ):
        from app import entity_service

        _limit(request, "mobile_entity")
        with cursor() as c:
            summary = entity_service.contractor_summary(
                c, vat, language(request))
            if summary is not None:
                mobile_favorites.mark_items(
                    c, int(identity.user["id"]), summary.recent_acts)
        if summary is None:
            raise ApiError("not_found", 404)
        return summary

    def _saved_error(exc: mobile_saved_searches.MobileSavedSearchError):
        raise ApiError(exc.code, exc.status) from exc

    def _notification_error(exc: mobile_notifications.MobileNotificationError):
        raise ApiError(exc.code, exc.status) from exc

    @router.get("/saved-searches", response_model=SavedSearchList,
                response_model_exclude_none=True,
                operation_id="listMobileSavedSearches")
    def list_saved_searches(request: Request,
                            identity=Depends(current_identity)):
        from app.account_searches import MAX_SAVED_SEARCHES

        _limit(request, "mobile_saved_search")
        with cursor() as c:
            return mobile_saved_searches.list_saved(
                c, int(identity.user["id"]), MAX_SAVED_SEARCHES)

    @router.post("/saved-searches", response_model=SavedSearch,
                 response_model_exclude_none=True,
                 status_code=201, operation_id="createMobileSavedSearch")
    def create_saved_search(
        payload: SavedSearchCreate,
        request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=16, max_length=128),
        identity=Depends(current_identity),
    ):
        from app.account_searches import MAX_SAVED_SEARCHES

        _limit(request, "mobile_saved_search_write")
        try:
            with cursor() as c:
                return mobile_saved_searches.create_saved(
                    c, user_id=int(identity.user["id"]), payload=payload,
                    idempotency_key=idempotency_key,
                    owned_limit=MAX_SAVED_SEARCHES)
        except mobile_search.MobileSearchError as exc:
            status = 503 if exc.code == "service_unavailable" else 422
            raise ApiError(exc.code, status, fields=exc.fields) from exc
        except mobile_saved_searches.MobileSavedSearchError as exc:
            _saved_error(exc)

    @router.patch("/saved-searches/{savedSearchId}",
                  response_model=SavedSearch,
                  response_model_exclude_none=True,
                  operation_id="renameMobileSavedSearch")
    def rename_saved_search(
        payload: SavedSearchRename,
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_saved_search_write")
        try:
            with cursor() as c:
                return mobile_saved_searches.rename_saved(
                    c, user_id=int(identity.user["id"]),
                    profile_id=savedSearchId, name=payload.name)
        except mobile_saved_searches.MobileSavedSearchError as exc:
            _saved_error(exc)

    @router.delete("/saved-searches/{savedSearchId}", status_code=204,
                   operation_id="deleteMobileSavedSearch")
    def delete_saved_search(
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_saved_search_write")
        try:
            with cursor() as c:
                mobile_saved_searches.delete_saved(
                    c, user_id=int(identity.user["id"]),
                    profile_id=savedSearchId)
        except mobile_saved_searches.MobileSavedSearchError as exc:
            _saved_error(exc)
        return Response(status_code=204)

    def _email_alert_error(exc: mobile_email_alerts.MobileEmailAlertError):
        raise ApiError(exc.code, exc.status, fields=exc.fields) from exc

    @router.get("/saved-searches/{savedSearchId}/email-alert",
                response_model=EmailAlert,
                operation_id="getMobileEmailAlert")
    def get_mobile_email_alert(
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        try:
            with cursor() as c:
                return mobile_email_alerts.get_email_alert(
                    c, user=identity.user, profile_id=savedSearchId,
                    language=language(request))
        except mobile_email_alerts.MobileEmailAlertError as exc:
            _email_alert_error(exc)

    @router.put("/saved-searches/{savedSearchId}/email-alert",
                response_model=EmailAlert,
                operation_id="putMobileEmailAlert")
    def put_mobile_email_alert(
        payload: EmailAlertInput,
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_saved_search_write")
        try:
            with cursor() as c:
                return mobile_email_alerts.put_email_alert(
                    c, user=identity.user, profile_id=savedSearchId,
                    payload=payload, language=language(request))
        except mobile_email_alerts.MobileEmailAlertError as exc:
            _email_alert_error(exc)

    @router.delete("/saved-searches/{savedSearchId}/email-alert",
                   status_code=204, operation_id="deleteMobileEmailAlert")
    def delete_mobile_email_alert(
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_saved_search_write")
        try:
            with cursor() as c:
                mobile_email_alerts.delete_email_alert(
                    c, user=identity.user, profile_id=savedSearchId)
        except mobile_email_alerts.MobileEmailAlertError as exc:
            _email_alert_error(exc)
        return Response(status_code=204)

    @router.get(
        "/saved-searches/{savedSearchId}/email-alert/runs/{runId}",
        response_model=EmailAlertRunPage,
        operation_id="getMobileEmailAlertRun",
    )
    def get_mobile_email_alert_run(
        request: Request,
        savedSearchId: int = Path(ge=1),
        runId: int = Path(ge=1),
        page_cursor: str | None = Query(default=None, alias="cursor", max_length=2048),
        identity=Depends(current_identity),
    ):
        try:
            with cursor() as c:
                return mobile_email_alerts.get_email_alert_run(
                    c,
                    user=identity.user,
                    profile_id=savedSearchId,
                    run_id=runId,
                    language=language(request),
                    cursor=page_cursor,
                )
        except mobile_email_alerts.MobileEmailAlertError as exc:
            _email_alert_error(exc)

    @router.get("/notification-settings", response_model=NotificationSettings,
                operation_id="getMobileNotificationSettings")
    def get_notification_settings(identity=Depends(current_identity)):
        with cursor() as c:
            return mobile_notifications.get_settings(c, int(identity.user["id"]))

    @router.put("/notification-settings", response_model=NotificationSettings,
                operation_id="putMobileNotificationSettings")
    def put_notification_settings(
        payload: NotificationSettingsInput,
        request: Request,
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        try:
            with cursor() as c:
                return mobile_notifications.put_settings(
                    c, int(identity.user["id"]), payload)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.get("/devices", response_model=DeviceList,
                operation_id="listMobileDevices")
    def list_mobile_devices(identity=Depends(current_identity)):
        with cursor() as c:
            return mobile_notifications.list_devices(
                c, int(identity.user["id"]), identity.device_id)

    @router.post("/devices", response_model=Device,
                 operation_id="registerMobileDevice")
    def register_mobile_device(
        payload: DeviceRegistration,
        request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=16, max_length=128),
        identity=Depends(current_identity),
    ):
        # Device registration is naturally idempotent on the authenticated
        # installation. Repeating the same request converges to the same row.
        del idempotency_key
        _limit(request, "mobile_device_write")
        try:
            with cursor() as c:
                return mobile_notifications.register_current_device(
                    c, user_id=int(identity.user["id"]),
                    current_device_id=identity.device_id, payload=payload)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.delete("/devices/{deviceId}", status_code=204,
                   operation_id="revokeMobileDevice")
    def revoke_mobile_device(
        request: Request,
        deviceId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_device_write")
        try:
            with cursor() as c:
                mobile_notifications.revoke_device(
                    c, user_id=int(identity.user["id"]), device_id=deviceId)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)
        return Response(status_code=204)

    @router.get("/saved-searches/{savedSearchId}/push-alert",
                response_model=PushAlert, operation_id="getMobilePushAlert")
    def get_mobile_push_alert(
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        try:
            with cursor() as c:
                return mobile_notifications.get_push_alert(
                    c, user_id=int(identity.user["id"]),
                    profile_id=savedSearchId,
                    has_access=bool(identity.user.get("has_access")))
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.put("/saved-searches/{savedSearchId}/push-alert",
                response_model=PushAlert, operation_id="putMobilePushAlert")
    def put_mobile_push_alert(
        payload: PushAlertInput,
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        try:
            with cursor() as c:
                return mobile_notifications.put_push_alert(
                    c, user_id=int(identity.user["id"]),
                    profile_id=savedSearchId,
                    has_access=bool(identity.user.get("has_access")),
                    payload=payload)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.delete("/saved-searches/{savedSearchId}/push-alert",
                   status_code=204, operation_id="deleteMobilePushAlert")
    def delete_mobile_push_alert(
        request: Request,
        savedSearchId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        try:
            with cursor() as c:
                mobile_notifications.delete_push_alert(
                    c, user_id=int(identity.user["id"]),
                    profile_id=savedSearchId)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)
        return Response(status_code=204)

    @router.get("/notifications", response_model=NotificationPage,
                operation_id="listMobileNotifications")
    def list_mobile_notifications(
        state: str = Query(default="all", pattern="^(all|unread)$"),
        cursor_token: str | None = Query(
            default=None, alias="cursor", max_length=2048),
        limit: int = Query(default=20, ge=1, le=50),
        identity=Depends(current_identity),
    ):
        try:
            with cursor() as c:
                return mobile_notifications.list_notifications(
                    c, user_id=int(identity.user["id"]), state=state,
                    cursor_token=cursor_token, limit=limit)
        except mobile_search.MobileSearchError as exc:
            raise ApiError(exc.code, 422) from exc
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.post("/notifications/read-all", response_model=UpdatedCount,
                 operation_id="markAllMobileNotificationsRead")
    def mark_all_mobile_notifications_read(
        request: Request, identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        with cursor() as c:
            return UpdatedCount(updated=mobile_notifications.mark_all_read(
                c, int(identity.user["id"])))

    @router.patch("/notifications/{notificationId}",
                  response_model=NotificationItem,
                  operation_id="updateMobileNotification")
    def update_mobile_notification(
        payload: NotificationUpdate,
        request: Request,
        notificationId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        try:
            with cursor() as c:
                return mobile_notifications.update_notification(
                    c, user_id=int(identity.user["id"]),
                    notification_id=notificationId, read=payload.read)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)

    @router.delete("/notifications/{notificationId}", status_code=204,
                   operation_id="deleteMobileNotification")
    def delete_mobile_notification(
        request: Request,
        notificationId: int = Path(ge=1),
        identity=Depends(current_identity),
    ):
        _limit(request, "mobile_notification_write")
        try:
            with cursor() as c:
                mobile_notifications.delete_notification(
                    c, user_id=int(identity.user["id"]),
                    notification_id=notificationId)
        except mobile_notifications.MobileNotificationError as exc:
            _notification_error(exc)
        return Response(status_code=204)

    return router
