"""Shared mobile search normalization, signed cursors, and safe DTO mapping.

The SQL engine remains ``app.main.build_where`` / ``run_search``.  This module
adapts typed native requests to that canonical internal filter dictionary and
maps the bounded result rows to an explicit public contract.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import time
import zlib
from decimal import Decimal

from app import search_match
from app.api_v1.schemas import (
    ActSearchItem,
    ActSearchRequest,
    ActSearchResponse,
    CategoryLookup,
    LookupItem,
    LookupResponse,
    MatchReason,
    Money,
    SearchTotals,
)

CURSOR_TTL_SECONDS = max(
    300, min(86400, int(os.environ.get("MOBILE_CURSOR_TTL_SECONDS", "3600"))))
_SOURCE_LABELS = {
    "el": {"khmdhs": "ΚΗΜΔΗΣ", "diavgeia": "Διαύγεια", "ted": "TED", "tsg": "Tender Service"},
    "en": {"khmdhs": "KIMDIS", "diavgeia": "Diavgeia", "ted": "TED", "tsg": "Tender Service"},
}
_SORT_TO_WEB = {
    "submission_date": "submission_date",
    "deadline": "deadline",
    "value_asc": "value_asc",
    "value_desc": "value",
    "relevance": "relevance",
}
_WEB_SINGLE = (
    "q", "fulltext", "tables_q", "date_from", "date_to",
    "deadline_from", "deadline_to", "value_min", "value_max", "status", "sort",
)
_WEB_MULTI = (
    "type", "authority", "contract_type", "procedure_type", "nuts", "cpv",
    "cat", "source",
)


class MobileSearchError(Exception):
    def __init__(self, code: str, *, fields: list[dict] | None = None):
        super().__init__(code)
        self.code = code
        self.fields = fields


def web_filters(query_params) -> dict:
    """Normalize the browser query string to the canonical filter dictionary."""
    out = {key: query_params.get(key, "") for key in _WEB_SINGLE}
    for key in _WEB_MULTI:
        seen = set()
        values = []
        for value in query_params.getlist(key):
            value = value.strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        out[key] = values
    return out


def api_filters(payload: ActSearchRequest) -> dict:
    """Map typed public names to the same keys consumed by ``build_where``."""
    filters = payload.filters
    return {
        "q": filters.q or "",
        "fulltext": filters.fulltext or "",
        "tables_q": filters.tables_q or "",
        "type": list(filters.types),
        "authority": list(filters.authority_ids),
        "source": list(filters.sources),
        "cpv": list(filters.cpv_prefixes),
        "cat": [f"{'c' if item.kind == 'category' else 's'}:{item.id}"
                for item in filters.categories],
        "contract_type": list(filters.contract_types),
        "procedure_type": list(filters.procedure_types),
        "nuts": list(filters.nuts),
        "date_from": filters.publication_from,
        "date_to": filters.publication_to,
        "deadline_from": filters.deadline_from,
        "deadline_to": filters.deadline_to,
        "value_min": filters.value_min or "",
        "value_max": filters.value_max or "",
        "status": filters.status or "",
        "sort": _SORT_TO_WEB[payload.sort],
    }


def validate_vocabulary(params: dict) -> None:
    """Reject unknown controlled values instead of silently dropping them."""
    from app import main as web

    lookup = web.lookups()
    supported = {
        "type": set(web.TYPE_LABELS),
        "source": set(_SOURCE_LABELS["el"]),
        "contract_type": set(web.CONTRACT_TYPES),
        "procedure_type": (
            set(web.PROCEDURE_TYPES)
            | {str(item["code"]) for item in lookup.get("procedure_types", [])}
        ),
        "nuts": (
            {str(item["code"]) for item in web.NUTS_REGIONS}
            | {str(item["code"]) for item in lookup.get("nuts", [])}
        ),
        "cat": {
            f"c:{category['id']}"
            for category in lookup.get("categories", [])
        } | {
            f"s:{sub['id']}"
            for category in lookup.get("categories", [])
            for sub in category.get("subs", [])
        },
    }
    fields = []
    public_names = {
        "type": "filters.types",
        "source": "filters.sources",
        "contract_type": "filters.contract_types",
        "procedure_type": "filters.procedure_types",
        "nuts": "filters.nuts",
        "cat": "filters.categories",
    }
    for key, allowed in supported.items():
        if any(str(value) not in allowed for value in params.get(key, [])):
            fields.append({"field": public_names[key], "code": "unknown_value"})
    if fields:
        raise MobileSearchError("validation_error", fields=fields)


def _signing_key() -> bytes:
    value = ((os.environ.get("MOBILE_CURSOR_SIGNING_KEY") or "").strip()
             or (os.environ.get("MOBILE_TOKEN_HASH_KEY") or "").strip())
    if not value:
        if os.environ.get("RENDER") or os.environ.get("APP_ENV", "").lower() == "production":
            raise MobileSearchError("service_unavailable")
        value = "dev-only-mobile-cursor-key-change-me"
    return value.encode()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode()


def _encode(payload: dict) -> str:
    packed = zlib.compress(_canonical(payload), level=9)
    body = base64.urlsafe_b64encode(packed).rstrip(b"=").decode()
    signature = hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()[:16]
    sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"v1.{body}.{sig}"


def _decode_b64(value: str) -> bytes:
    """Decode only the one canonical URL-safe spelling of a token segment."""
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("base64 alphabet")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode()
    if not hmac.compare_digest(value, canonical):
        raise ValueError("non-canonical base64")
    return decoded


def _decode(token: str, expected_kind: str) -> dict:
    try:
        version, body, supplied = token.split(".", 2)
        if version != "v1":
            raise ValueError("version")
        expected = hmac.new(
            _signing_key(), body.encode(), hashlib.sha256).digest()[:16]
        supplied_bytes = _decode_b64(supplied)
        if not hmac.compare_digest(expected, supplied_bytes):
            raise ValueError("signature")
        packed = _decode_b64(body)
        payload = json.loads(zlib.decompress(packed))
        if payload.get("kind") != expected_kind or float(payload["expires_at"]) < time.time():
            raise ValueError("expired")
        return payload
    except MobileSearchError:
        raise
    except Exception as exc:
        raise MobileSearchError("invalid_cursor") from exc


def encode_signed(payload: dict) -> str:
    """Encode a bounded mobile API cursor/context payload."""
    return _encode(payload)


def decode_signed(token: str, expected_kind: str) -> dict:
    """Decode and authenticate a mobile API cursor/context payload."""
    return _decode(token, expected_kind)


def _fingerprint(params: dict, limit: int) -> str:
    return hashlib.sha256(_canonical({"filters": params, "limit": limit})).hexdigest()


def _next_cursor(params: dict, *, limit: int, snapshot: dt.datetime,
                 offset: int) -> str:
    return _encode({
        "kind": "search", "snapshot": snapshot.isoformat(), "offset": offset,
        "fingerprint": _fingerprint(params, limit),
        "expires_at": time.time() + CURSOR_TTL_SECONDS,
    })


def _read_cursor(token: str, params: dict, limit: int) -> tuple[dt.datetime, int]:
    payload = _decode(token, "search")
    if payload.get("fingerprint") != _fingerprint(params, limit):
        raise MobileSearchError("invalid_cursor")
    try:
        snapshot = dt.datetime.fromisoformat(str(payload["snapshot"]))
        offset = int(payload["offset"])
        if snapshot.tzinfo is None or offset < 0:
            raise ValueError("cursor values")
    except Exception as exc:
        raise MobileSearchError("invalid_cursor") from exc
    return snapshot.astimezone(dt.timezone.utc), offset


def make_context(params: dict) -> str | None:
    q = " ".join(str(value) for value in (
        params.get("q"), params.get("fulltext")) if value).strip()
    cpv = list(params.get("cpv") or [])
    if not q and not cpv:
        return None
    return _encode({
        "kind": "match", "q": q, "cpv": cpv,
        "expires_at": time.time() + CURSOR_TTL_SECONDS,
    })


def read_context(token: str | None) -> tuple[str, list[str]]:
    if not token:
        return "", []
    try:
        payload = _decode(token, "match")
        return str(payload.get("q") or ""), [str(v) for v in payload.get("cpv", [])]
    except MobileSearchError:
        # Match context affects explanation only, never authorization or the act.
        return "", []


def _lookup(code, label) -> LookupItem | None:
    if code is None or label is None:
        return None
    return LookupItem(code=str(code), label=str(label))


def _date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    return value


def _money(value) -> Money | None:
    if value is None:
        return None
    return Money(amount=format(Decimal(value), "f"))


def _plain_snippet(value) -> str | None:
    if not value:
        return None
    return html.unescape(re.sub(r"<[^>]+>", "", str(value))).strip() or None


def _match_reasons(chips) -> list[MatchReason]:
    reasons = []
    for chip in (chips or [])[:5]:
        reasons.append(MatchReason(
            kind="cpv" if chip.kind == "cpv" else "text",
            label=str(chip.label or chip.code or chip.term),
        ))
    return reasons


def act_item(row: dict, *, lang: str, chips=None,
             context: str | None = None,
             favorited: bool = False) -> ActSearchItem:
    from app import main as web

    type_code = str(row.get("type") or "")
    source_code = str(row.get("data_source") or "")
    type_label = web._i18n.enum_label("type", type_code, web.TYPE_LABELS, lang)
    contract_code = row.get("contract_type_code")
    procedure_code = row.get("procedure_type_code")
    nuts_code = row.get("nuts_code")
    nuts_labels = {
        str(item["code"]): item["label"]
        for item in web.lookups().get("nuts", [])
    }
    authority = None
    if row.get("authority_id") and row.get("authority_name"):
        authority = {"id": str(row["authority_id"]), "name": row["authority_name"]}
    return ActSearchItem(
        adam=str(row["adam"]),
        type={"code": type_code, "label": type_label},
        title=str(row.get("title") or ""),
        source={
            "code": source_code,
            "label": _SOURCE_LABELS.get(lang, _SOURCE_LABELS["el"]).get(
                source_code, source_code),
        },
        authority=authority,
        signed_date=_date(row.get("signed_date")),
        publication_date=_date(row.get("submission_date")),
        deadline_at=row.get("final_submission_date"),
        value=_money(row.get("resolved_value")),
        value_corrected=bool(row.get("is_corrected")),
        cancelled=bool(row.get("cancelled")),
        modified=bool(row.get("is_modified")),
        contract_type=_lookup(
            contract_code,
            web._i18n.enum_label(
                "contract_type", contract_code, web.CONTRACT_TYPES, lang)
            if contract_code is not None else None,
        ),
        procedure_type=_lookup(
            procedure_code,
            web._i18n.enum_label(
                "procedure_type", procedure_code, web.PROCEDURE_TYPES, lang)
            if procedure_code is not None else None,
        ),
        nuts_region=_lookup(nuts_code, nuts_labels.get(str(nuts_code), nuts_code)),
        snippet=_plain_snippet(row.get("snippet")),
        match_reasons=_match_reasons(chips),
        context=context,
        favorited=favorited,
    )


def search(cursor_factory, payload: ActSearchRequest, lang: str) -> ActSearchResponse:
    from app import main as web

    params = api_filters(payload)
    validate_vocabulary(params)
    if payload.cursor:
        snapshot, offset = _read_cursor(payload.cursor, params, payload.limit)
    else:
        snapshot = dt.datetime.now(dt.timezone.utc)
        offset = 0

    rows, aggregate = web.run_search(
        params, payload.limit + 1, offset, snapshot_at=snapshot)
    has_more = len(rows) > payload.limit
    rows = rows[:payload.limit]
    q_text, cpv_codes = web.match_terms_from_params(params)
    chips = {}
    if q_text or cpv_codes:
        with cursor_factory() as c:
            chips = search_match.list_chips(
                c, [row["adam"] for row in rows], q_text, cpv_codes, lang)
    context = make_context(params)
    items = [act_item(
        row, lang=lang, chips=chips.get(row["adam"]), context=context)
        for row in rows]
    next_cursor = (_next_cursor(
        params, limit=payload.limit, snapshot=snapshot,
        offset=offset + payload.limit) if has_more else None)
    return ActSearchResponse(
        items=items, next_cursor=next_cursor, snapshot_at=snapshot,
        totals=SearchTotals(
            count=max(0, int(aggregate["n"] or 0)),
            value=_money(aggregate.get("total_value")) or Money(amount="0"),
        ),
    )


def lookups(lang: str) -> tuple[LookupResponse, str]:
    from app import main as web

    raw = web.lookups()
    act_types = [LookupItem(
        code=code,
        label=web._i18n.enum_label("type", code, web.TYPE_LABELS, lang),
    ) for code in web.TYPE_FILTER_ORDER]
    sources = [LookupItem(code=code, label=label) for code, label in
               _SOURCE_LABELS.get(lang, _SOURCE_LABELS["el"]).items()]
    authorities = [LookupItem(code=str(item["id"]), label=str(item["name"]))
                   for item in raw.get("authorities", []) if item.get("name")]
    contract_types = [LookupItem(
        code=str(item["code"]),
        label=web._i18n.enum_label(
            "contract_type", item["code"], web.CONTRACT_TYPES, lang),
    ) for item in raw.get("contract_types", [])]
    procedure_types = [LookupItem(
        code=str(item["code"]),
        label=web._i18n.enum_label(
            "procedure_type", item["code"], web.PROCEDURE_TYPES, lang),
    ) for item in raw.get("procedure_types", [])]
    nuts = [LookupItem(code=str(item["code"]), label=str(item["label"]))
            for item in raw.get("nuts", [])]
    categories = []
    for category in raw.get("categories", []):
        category_code = str(category["id"])
        category_label = (category.get("name_en") if lang == "en" else None)
        category_label = category_label or category.get("name") or category_code
        categories.append(CategoryLookup(
            kind="category", id=category_code, label=category_label))
        for sub in category.get("subs", []):
            sub_label = (sub.get("name_en") if lang == "en" else None)
            categories.append(CategoryLookup(
                kind="subcategory", id=str(sub["id"]),
                label=sub_label or sub.get("name") or str(sub["id"]),
                parent_id=category_code,
            ))
    body = {
        "language": lang, "act_types": act_types, "sources": sources,
        "authorities": authorities, "contract_types": contract_types,
        "procedure_types": procedure_types, "nuts_regions": nuts,
        "categories": categories,
    }
    version = hashlib.sha256(_canonical({
        key: [item.model_dump(mode="json") for item in value]
        if isinstance(value, list) else value for key, value in body.items()
    })).hexdigest()[:16]
    response = LookupResponse(version=version, **body)
    return response, f'"mobile-lookups-{version}"'
