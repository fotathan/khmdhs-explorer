from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field,
                      field_validator, model_validator)


def _as_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:           # naive = UTC, as digests._aware reads it
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


# The contract's one Timestamp type: an instant, always written in UTC ("…Z").
# The app pool sets every session to Europe/Athens for the web pages, so a
# timestamptz read from the DB arrives as "+03:00" unless it is converted here.
# Every datetime in a model is therefore a Timestamp, never a bare dt.datetime
# (test-enforced in test_mobile_api_contract_unit).
Timestamp = Annotated[dt.datetime, AfterValidator(_as_utc)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeviceMetadata(StrictModel):
    installation_id: uuid.UUID
    platform: Literal["ios", "android"]
    app_version: str = Field(min_length=1, max_length=32)
    os_version: str | None = Field(default=None, max_length=32)
    locale: Literal["el", "en"] = "el"
    timezone: str = Field(default="Europe/Athens", min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    device: DeviceMetadata


class MfaVerifyRequest(StrictModel):
    challenge_id: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=6, max_length=64)


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=8, max_length=200)


class MfaEnrollmentStartRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)


class MfaEnrollmentConfirmRequest(StrictModel):
    enrollment_id: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=6, max_length=12)


class MfaDisableRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=64)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=16, max_length=512)
    installation_id: uuid.UUID


class Entitlement(BaseModel):
    has_access: bool
    status: str
    product_code: str | None = None
    expires_at: Timestamp | None = None


class SecurityState(BaseModel):
    mfa_enabled: bool
    password_change_required: bool


class MeResponse(BaseModel):
    id: str
    username: str
    email: str | None = None
    role: Literal["customer", "admin"]
    language: Literal["el", "en"]
    entitlement: Entitlement
    security: SecurityState


class TokenResponse(BaseModel):
    token_type: Literal["Bearer"] = "Bearer"
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: Timestamp
    user: MeResponse


class MfaChallengeResponse(BaseModel):
    status: Literal["mfa_required"] = "mfa_required"
    challenge_id: str
    expires_in: int
    methods: list[Literal["totp", "recovery_code"]]


class MfaStatus(BaseModel):
    enabled: bool
    recovery_codes_remaining: int = Field(ge=0)


class MfaEnrollment(BaseModel):
    enrollment_id: str
    secret: str
    otpauth_uri: str
    expires_in: int


class MfaEnabledResponse(BaseModel):
    enabled: Literal[True] = True
    recovery_codes: list[str] = Field(min_length=1)


class LookupItem(BaseModel):
    code: str
    label: str
    parent_code: str | None = None


class CategoryLookup(BaseModel):
    kind: Literal["category", "subcategory"]
    id: str
    label: str
    parent_id: str | None = None


class LookupResponse(BaseModel):
    version: str
    language: Literal["el", "en"]
    act_types: list[LookupItem]
    sources: list[LookupItem]
    authorities: list[LookupItem]
    contract_types: list[LookupItem]
    procedure_types: list[LookupItem]
    nuts_regions: list[LookupItem]
    categories: list[CategoryLookup]


class CategorySelection(StrictModel):
    kind: Literal["category", "subcategory"]
    id: str = Field(pattern=r"^[1-9][0-9]*$", max_length=20)


FilterCode = Annotated[str, Field(min_length=1, max_length=128)]


class ActSearchFilters(StrictModel):
    q: str | None = Field(default=None, max_length=300)
    fulltext: str | None = Field(default=None, max_length=300)
    tables_q: str | None = Field(default=None, max_length=300)
    types: list[FilterCode] = Field(default_factory=list, max_length=20)
    authority_ids: list[FilterCode] = Field(default_factory=list, max_length=20)
    sources: list[FilterCode] = Field(default_factory=list, max_length=20)
    cpv_prefixes: list[FilterCode] = Field(default_factory=list, max_length=20)
    categories: list[CategorySelection] = Field(default_factory=list, max_length=20)
    contract_types: list[FilterCode] = Field(default_factory=list, max_length=20)
    procedure_types: list[FilterCode] = Field(default_factory=list, max_length=20)
    nuts: list[FilterCode] = Field(default_factory=list, max_length=20)
    publication_from: dt.date | None = None
    publication_to: dt.date | None = None
    deadline_from: dt.date | None = None
    deadline_to: dt.date | None = None
    value_min: str | None = Field(
        default=None, max_length=32, pattern=r"^\d+(\.\d{1,2})?$")
    value_max: str | None = Field(
        default=None, max_length=32, pattern=r"^\d+(\.\d{1,2})?$")
    status: Literal["active", "cancelled", "modified"] | None = None

    @field_validator("types", "authority_ids", "sources", "cpv_prefixes",
                     "contract_types", "procedure_types", "nuts")
    @classmethod
    def unique_nonempty_values(cls, values: list[str]) -> list[str]:
        clean = [value.strip() for value in values]
        if any(not value for value in clean):
            raise ValueError("empty selection")
        if len(clean) != len(set(clean)):
            raise ValueError("duplicate selection")
        return clean

    @field_validator("cpv_prefixes")
    @classmethod
    def valid_cpv_prefixes(cls, values: list[str]) -> list[str]:
        if any(not value.isdigit() or len(value) > 8 for value in values):
            raise ValueError("invalid CPV prefix")
        return values

    @model_validator(mode="after")
    def ordered_ranges(self):
        category_keys = [(item.kind, item.id) for item in self.categories]
        if len(category_keys) != len(set(category_keys)):
            raise ValueError("duplicate category selection")
        if (self.publication_from and self.publication_to
                and self.publication_from > self.publication_to):
            raise ValueError("publication_from must not exceed publication_to")
        if (self.deadline_from and self.deadline_to
                and self.deadline_from > self.deadline_to):
            raise ValueError("deadline_from must not exceed deadline_to")
        if (self.value_min is not None and self.value_max is not None
                and Decimal(self.value_min) > Decimal(self.value_max)):
            raise ValueError("value_min must not exceed value_max")
        return self


class ActSearchRequest(StrictModel):
    filters: ActSearchFilters
    sort: Literal[
        "submission_date", "deadline", "value_asc", "value_desc", "relevance"
    ] = "submission_date"
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=20, ge=1, le=50)


class Money(BaseModel):
    amount: str
    currency: Literal["EUR"] = "EUR"


class EntityRef(BaseModel):
    id: str
    name: str


class MatchReason(BaseModel):
    kind: Literal["text", "cpv", "category", "authority", "geography",
                  "deadline", "value"]
    label: str


class ActSearchItem(BaseModel):
    adam: str
    type: LookupItem
    title: str
    source: LookupItem
    authority: EntityRef | None = None
    signed_date: dt.date | None = None
    publication_date: dt.date | None = None
    deadline_at: Timestamp | None = None
    value: Money | None = None
    value_corrected: bool = False
    cancelled: bool
    modified: bool
    contract_type: LookupItem | None = None
    procedure_type: LookupItem | None = None
    nuts_region: LookupItem | None = None
    snippet: str | None = None
    match_reasons: list[MatchReason] = Field(default_factory=list, max_length=5)
    context: str | None = None
    favorited: bool = False


class SearchTotals(BaseModel):
    count: int = Field(ge=0)
    value: Money


class ActSearchResponse(BaseModel):
    items: list[ActSearchItem]
    next_cursor: str | None = None
    snapshot_at: Timestamp
    totals: SearchTotals


class DetailSection(BaseModel):
    kind: str
    title: str
    source_class: Literal["source", "calculated", "ai"] = "source"
    data: dict


class Link(BaseModel):
    label: str
    url: str


class ActDetail(BaseModel):
    adam: str
    type: LookupItem
    title: str
    source: LookupItem
    authority: EntityRef | None = None
    publication_date: dt.date | None = None
    deadline_at: Timestamp | None = None
    value: Money | None = None
    cancelled: bool
    modified: bool
    match_reasons: list[MatchReason] = Field(default_factory=list)
    sections: list[DetailSection]
    links: list[Link]
    updated_at: Timestamp | None = None
    favorited: bool = False


class FavoriteState(BaseModel):
    adam: str
    favorited: bool
    favorited_at: Timestamp | None = None


class FavoriteItem(BaseModel):
    act: ActSearchItem
    favorited_at: Timestamp


class FavoritePage(BaseModel):
    items: list[FavoriteItem]
    next_cursor: str | None = None
    total: int = Field(ge=0)


class EntityContact(BaseModel):
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None


class EntityHeadline(BaseModel):
    act_count: int = Field(ge=0)
    contract_value: Money | None = None


class EntitySummary(BaseModel):
    kind: Literal["authority", "contractor"]
    id: str
    name: str
    canonical_name: str | None = None
    merged: bool = False
    contact: EntityContact | None = None
    headline: EntityHeadline
    top_categories: list[LookupItem] = Field(default_factory=list, max_length=8)
    top_buyers: list[EntityRef] = Field(default_factory=list, max_length=10)
    recent_acts: list[ActSearchItem] = Field(default_factory=list, max_length=20)


class SavedSearch(BaseModel):
    id: str
    name: str
    scope: Literal["customer", "portal"]
    owned: bool
    editable: bool
    filters: ActSearchFilters
    email_alert_enabled: bool = False
    push_alert_enabled: bool = False
    created_at: Timestamp
    updated_at: Timestamp


class SavedSearchList(BaseModel):
    items: list[SavedSearch]
    owned_count: int = Field(ge=0)
    owned_limit: int = Field(ge=1)


class SavedSearchCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    filters: ActSearchFilters

    @model_validator(mode="after")
    def has_effective_filter(self):
        values = self.filters.model_dump(exclude_none=True)
        if not any(value not in ("", [], {}) for value in values.values()):
            raise ValueError("at least one filter is required")
        return self


class SavedSearchRename(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class EmailAlertInput(StrictModel):
    active: bool
    layout: Literal["list", "summary", "deadline"]
    schedule_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    language: Literal["el", "en"]
    max_results: int = Field(ge=1, le=200)
    lead_days: list[int] = Field(min_length=1, max_length=6)
    send_empty: bool = False

    @field_validator("lead_days")
    @classmethod
    def valid_email_lead_days(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)) or any(day < 0 or day > 90 for day in values):
            raise ValueError("lead_days must be unique values from 0 to 90")
        return values


class EmailSchedule(BaseModel):
    id: str
    label: str
    is_default: bool


class EmailAlertRun(BaseModel):
    id: str
    status: Literal["sent", "empty", "error", "skipped"]
    result_count: int = Field(ge=0)
    started_at: Timestamp


class EmailAlertRunItem(BaseModel):
    act: ActSearchItem
    included_in_email: bool


class EmailAlertRunPage(BaseModel):
    run: EmailAlertRun
    saved_search_id: str
    saved_search_name: str
    filters: ActSearchFilters
    items: list[EmailAlertRunItem]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class EmailAlert(EmailAlertInput):
    saved_search_id: str
    exists: bool
    schedule_label: str | None = None
    next_run_at: Timestamp | None = None
    last_sent_at: Timestamp | None = None
    additional_recipients: list[str] = Field(default_factory=list)
    recent_runs: list[EmailAlertRun] = Field(default_factory=list, max_length=3)
    schedule_options: list[EmailSchedule]
    delivery_suspended_reason: Literal[
        "not_entitled", "no_email", "no_schedule"
    ] | None = None


TimeOfDay = Annotated[
    str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", min_length=5, max_length=5)
]


class NotificationSettingsInput(StrictModel):
    paused: bool = False
    timezone: str = Field(default="Europe/Athens", min_length=1, max_length=64)
    quiet_start: TimeOfDay = "22:00"
    quiet_end: TimeOfDay = "08:00"
    summary_time: TimeOfDay = "08:30"
    daily_cap: int = Field(default=6, ge=1, le=10)
    language: Literal["el", "en"] = "el"

    @field_validator("timezone")
    @classmethod
    def valid_notification_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class NotificationSettings(NotificationSettingsInput):
    updated_at: Timestamp


class PushAlertInput(StrictModel):
    active: bool = False
    delivery_mode: Literal["immediate", "daily"] = "daily"
    new_matches: bool = True
    deadlines: bool = True
    lead_days: list[int] = Field(default_factory=lambda: [7, 1],
                                 min_length=1, max_length=6)

    @field_validator("lead_days")
    @classmethod
    def valid_lead_days(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)) or any(day < 0 or day > 90 for day in values):
            raise ValueError("lead_days must be unique values from 0 to 90")
        return values


class PushAlert(PushAlertInput):
    id: str | None = None
    saved_search_id: str
    delivery_suspended_reason: Literal[
        "globally_paused", "not_entitled", "no_active_device"
    ] | None = None
    updated_at: Timestamp


class DeviceRegistration(DeviceMetadata):
    permission_status: Literal["unknown", "granted", "denied", "provisional"]
    push_provider: Literal["expo"] = "expo"
    push_token: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def token_requires_permission(self):
        if self.push_token and self.permission_status not in {"granted", "provisional"}:
            raise ValueError("push_token requires granted or provisional permission")
        return self


class Device(BaseModel):
    id: str
    installation_id: uuid.UUID
    platform: Literal["ios", "android"]
    display_name: str
    app_version: str | None = None
    os_version: str | None = None
    permission_status: Literal["unknown", "granted", "denied", "provisional"]
    enabled: bool
    current: bool
    last_seen_at: Timestamp
    revoked_at: Timestamp | None = None


class DeviceList(BaseModel):
    items: list[Device]


class NotificationTarget(BaseModel):
    kind: Literal["act", "search_summary"]
    id: str
    context: str | None = None


class NotificationItem(BaseModel):
    id: str
    type: Literal["new_match", "deadline", "daily_summary"]
    title: str
    body: str
    saved_search_names: list[str] = Field(default_factory=list)
    # The act this one may repeat (a Tender Service possible duplicate).
    possible_duplicate_of: str | None = None
    created_at: Timestamp
    read: bool
    read_at: Timestamp | None = None
    expires_at: Timestamp
    target: NotificationTarget


class NotificationPage(BaseModel):
    items: list[NotificationItem]
    next_cursor: str | None = None
    unread_count: int = Field(ge=0)


class NotificationUpdate(StrictModel):
    read: bool


class UpdatedCount(BaseModel):
    updated: int = Field(ge=0)
