"""Ownership-safe mobile saved-search reads and mutations."""
from __future__ import annotations

import hashlib
import json

from psycopg.types.json import Json

from app import auth
from app import mobile_auth
from app import mobile_search
from app.api_v1.schemas import (
    ActSearchFilters,
    ActSearchRequest,
    CategorySelection,
    SavedSearch,
    SavedSearchCreate,
    SavedSearchList,
)


class MobileSavedSearchError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    seen = set()
    result = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def stored_filters(params: dict | None) -> ActSearchFilters:
    params = params or {}
    categories = []
    for value in _as_list(params.get("cat")):
        prefix, _, identifier = value.partition(":")
        if prefix in {"c", "s"} and identifier.isdigit() and int(identifier) > 0:
            categories.append(CategorySelection(
                kind="category" if prefix == "c" else "subcategory",
                id=identifier,
            ))
    status = params.get("status")
    if status not in {"active", "cancelled", "modified"}:
        status = None
    return ActSearchFilters(
        q=params.get("q") or None,
        fulltext=params.get("fulltext") or None,
        tables_q=params.get("tables_q") or None,
        types=_as_list(params.get("type")),
        authority_ids=_as_list(params.get("authority")),
        sources=_as_list(params.get("source")),
        cpv_prefixes=_as_list(params.get("cpv")),
        categories=categories,
        contract_types=_as_list(params.get("contract_type")),
        procedure_types=_as_list(params.get("procedure_type")),
        nuts=_as_list(params.get("nuts")),
        publication_from=params.get("date_from") or None,
        publication_to=params.get("date_to") or None,
        deadline_from=params.get("deadline_from") or None,
        deadline_to=params.get("deadline_to") or None,
        value_min=(str(params["value_min"])
                   if params.get("value_min") not in (None, "") else None),
        value_max=(str(params["value_max"])
                   if params.get("value_max") not in (None, "") else None),
        status=status,
    )


def _params_for_create(payload: SavedSearchCreate) -> dict:
    request = ActSearchRequest(filters=payload.filters)
    params = mobile_search.api_filters(request)
    params.pop("sort", None)
    params = {key: value for key, value in params.items()
              if value not in (None, "", [], {})}
    mobile_search.validate_vocabulary(params)
    if not params:
        raise MobileSavedSearchError("validation_error", 422)
    return params


def _dto(c, profile: dict, user_id: int,
         email_profile_ids: set[int] | None = None,
         push_profile_ids: set[int] | None = None) -> SavedSearch:
    email_profile_ids = email_profile_ids or set()
    push_profile_ids = push_profile_ids or set()
    effective = auth.effective_params(c, profile)
    owned = (profile["scope"] == "customer"
             and int(profile["owner_user_id"]) == int(user_id))
    return SavedSearch(
        id=str(profile["id"]), name=str(profile["name"]),
        scope=profile["scope"], owned=owned, editable=owned,
        filters=stored_filters(effective),
        email_alert_enabled=int(profile["id"]) in email_profile_ids,
        push_alert_enabled=int(profile["id"]) in push_profile_ids,
        created_at=profile["created_at"], updated_at=profile["updated_at"],
    )


def list_saved(c, user_id: int, owned_limit: int) -> SavedSearchList:
    c.execute("""SELECT * FROM proc.search_profile
                 WHERE (scope='customer' AND owner_user_id=%s)
                    OR (scope='portal' AND is_published)
                 ORDER BY (scope='customer') DESC, lower(name), id""",
              (user_id,))
    profiles = c.fetchall()
    c.execute("""SELECT search_profile_id FROM proc.digest_subscription
                 WHERE user_id=%s AND is_active""", (user_id,))
    email_ids = {int(row["search_profile_id"]) for row in c.fetchall()}
    c.execute("""SELECT search_profile_id FROM proc.push_subscription
                 WHERE user_id=%s AND is_active""", (user_id,))
    push_ids = {int(row["search_profile_id"]) for row in c.fetchall()}
    items = [_dto(c, profile, user_id, email_ids, push_ids)
             for profile in profiles]
    return SavedSearchList(
        items=items, owned_count=sum(1 for item in items if item.owned),
        owned_limit=owned_limit,
    )


def _fingerprint(payload: SavedSearchCreate) -> bytes:
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(body).digest()


def create_saved(c, *, user_id: int, payload: SavedSearchCreate,
                 idempotency_key: str, owned_limit: int) -> SavedSearch:
    params = _params_for_create(payload)
    path = "/api/v1/saved-searches"
    key_hash = mobile_auth.token_hash("idempotency:" + idempotency_key)
    fingerprint = _fingerprint(payload)
    with mobile_auth.transaction(c):
        c.execute("""DELETE FROM proc.api_idempotency_key
                     WHERE user_id=%s AND method='POST' AND path=%s
                       AND key_hash=%s AND expires_at<=now()""",
                  (user_id, path, key_hash))
        c.execute("""INSERT INTO proc.api_idempotency_key
                       (user_id,method,path,key_hash,request_fingerprint)
                     VALUES (%s,'POST',%s,%s,%s)
                     ON CONFLICT (user_id,method,path,key_hash) DO NOTHING
                     RETURNING id""",
                  (user_id, path, key_hash, fingerprint))
        claimed = c.fetchone()
        if not claimed:
            c.execute("""SELECT request_fingerprint,response_body
                         FROM proc.api_idempotency_key
                         WHERE user_id=%s AND method='POST' AND path=%s
                           AND key_hash=%s""", (user_id, path, key_hash))
            prior = c.fetchone()
            if not prior or bytes(prior["request_fingerprint"]) != fingerprint:
                raise MobileSavedSearchError("idempotency_conflict", 409)
            if prior["response_body"] is None:
                raise MobileSavedSearchError("service_unavailable", 503)
            return SavedSearch.model_validate(prior["response_body"])

        # Serialize the cap check with every creation for this account.
        c.execute("SELECT id FROM proc.app_user WHERE id=%s FOR UPDATE", (user_id,))
        c.execute("""SELECT count(*) AS n FROM proc.search_profile
                     WHERE scope='customer' AND owner_user_id=%s""", (user_id,))
        if int(c.fetchone()["n"]) >= owned_limit:
            raise MobileSavedSearchError("saved_search_limit", 409)
        profile_id = auth.create_search_profile(
            c, name=payload.name, scope="customer", owner_id=user_id,
            params=params, based_on_id=None, created_by=user_id)
        profile = auth.get_search_profile(c, profile_id)
        result = _dto(c, profile, user_id)
        response_body = result.model_dump(mode="json")
        c.execute("""UPDATE proc.api_idempotency_key
                     SET response_status=201,response_body=%s
                     WHERE id=%s""", (Json(response_body), claimed["id"]))
        return result


def rename_saved(c, *, user_id: int, profile_id: int,
                 name: str) -> SavedSearch:
    c.execute("""UPDATE proc.search_profile
                 SET name=%s,updated_at=now()
                 WHERE id=%s AND scope='customer' AND owner_user_id=%s
                 RETURNING *""", (name, profile_id, user_id))
    profile = c.fetchone()
    if not profile:
        raise MobileSavedSearchError("not_found", 404)
    c.execute("""SELECT search_profile_id FROM proc.digest_subscription
                 WHERE user_id=%s AND search_profile_id=%s AND is_active""",
              (user_id, profile_id))
    email_ids = {profile_id} if c.fetchone() else set()
    c.execute("""SELECT search_profile_id FROM proc.push_subscription
                 WHERE user_id=%s AND search_profile_id=%s AND is_active""",
              (user_id, profile_id))
    push_ids = {profile_id} if c.fetchone() else set()
    return _dto(c, profile, user_id, email_ids, push_ids)


def delete_saved(c, *, user_id: int, profile_id: int) -> None:
    c.execute("""DELETE FROM proc.search_profile
                 WHERE id=%s AND scope='customer' AND owner_user_id=%s
                 RETURNING id""", (profile_id, user_id))
    if not c.fetchone():
        raise MobileSavedSearchError("not_found", 404)
