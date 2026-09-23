# Spec: KHMDHS mobile app — stage 2 technical design and delivery plan

**Status:** Approved on 2026-09-14. Stage 3 / Slices A–I are implemented at
the local build level; physical-device validation and later vertical slices
remain outstanding.
**Depends on:** `docs/specs/mobile-app-stage-1.md`
**Companion contract:** `docs/specs/mobile-api-v1.openapi.yaml`
**Scope:** the backend/mobile contracts and delivery plan needed to implement the approved customer-facing iOS and Android MVP.

---

## 1. Stage-2 outcome

Stage 2 converts the approved product concept into an executable design. It fixes the boundaries that implementation must follow:

- the existing FastAPI/PostgreSQL application remains the system of record;
- a new `/api/v1` JSON surface serves the native client;
- browser-cookie authentication remains unchanged and separate;
- the native client uses short-lived bearer access tokens and rotating device-bound refresh tokens;
- existing search semantics, ownership rules and entitlement checks are reused rather than reimplemented;
- email subscriptions and push subscriptions have independent schedules and cursors;
- one channel-neutral matcher feeds email, push and the notification inbox;
- a durable worker owns notification evaluation and delivery;
- the separate Expo/React Native repository consumes the committed OpenAPI contract.

Stage 2 does not add routes, tables, dependencies, services or mobile source code. The first code-producing phase is Stage 3 / Slice A.

## 2. Current-system map

| Concern | Existing source of truth | Reuse decision | Required change |
|---|---|---|---|
| Customer identity and roles | `proc.app_user`, `app/auth.py` | Reuse | Add native sessions without altering browser sessions |
| Entitlement | `app/auth.py` customer-status calculation and `has_access` | Reuse exactly | Central API dependency must enforce it |
| Password verification/throttling | `app/auth.py`, `proc.login_throttle` | Reuse | Return JSON errors and native MFA challenges |
| Passwordless login | `app/login_links.py` | Reuse token issuance and one-time semantics | Add app-link exchange flow; keep scanner-safe confirmation |
| 2FA | `app/auth.py` TOTP/recovery codes | Reuse | Add a one-use native challenge record |
| Session invalidation | `proc.app_user.session_version` | Reuse | Stamp every native session and compare on use/refresh |
| Search semantics | `app/main.py:build_where()` | Reuse | Put validated API input in front of it |
| Search execution | `app/main.py:run_search()` | Refactor/shared service | Add stable cursor pagination and plain-text snippets |
| Search filters | `app/main.py:_params_from()`, `app/search_profiles.py` | Reuse vocabulary | Publish a typed filter schema |
| Saved searches | `proc.search_profile`, `app/account_searches.py` | Reuse | Add JSON CRUD with identical ownership rules |
| Email alerts | `proc.digest_subscription`, `app/digests.py` | Preserve | Add JSON settings endpoints; do not overload for push |
| New-result matching | `app/digests.py` | Extract core | Make it channel-neutral and separately cursor-driven |
| Deadline reminder ledger | `proc.digest_deadline_notice` | Pattern reuse | Add a push-specific ledger |
| Act detail | `/act/{adam}` queries in `app/main.py` | Extract DTO-oriented service | Do not expose `raw_json` or admin-only data |
| Push provider/devices/inbox | None | New | Add tables, worker and provider adapter |
| Production scheduling | Sleeping free web service; optional inline workers | Do not reuse for a delivery promise | Run notification work on an always-on service |

## 3. Target architecture

```text
┌─────────────────────────────┐
│ Expo / React Native app     │
│ Search · Saved · Inbox      │
└──────────────┬──────────────┘
               │ HTTPS JSON + bearer token
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Existing FastAPI service                                    │
│ /api/v1                                                     │
│ auth · search · acts · saved searches · settings · devices │
└──────────┬──────────────────────────────┬───────────────────┘
           │ shared domain services       │ enqueue/read
           ▼                              ▼
┌───────────────────────┐      ┌──────────────────────────────┐
│ PostgreSQL / proc     │      │ Always-on notification worker│
│ users, acts, profiles │◀────▶│ evaluate · claim · send      │
│ sessions, events      │      └──────────────┬───────────────┘
└───────────────────────┘                     │ provider adapter
                                              ▼
                                  ┌───────────────────────────┐
                                  │ Expo Push Service         │
                                  │ → APNs / FCM              │
                                  └───────────────────────────┘
```

Email remains a sibling output:

```text
channel-neutral match result ──┬── email rendering / SMTP
                               └── notification event / push delivery
```

Email and push may describe the same act, but neither channel advances the other's cursor or suppresses its delivery.

## 4. Code and repository boundaries

### 4.1 Existing backend repository

Recommended additions in implementation:

```text
app/
  api_v1/
    router.py
    dependencies.py
    errors.py
    schemas.py
    auth_routes.py
    search_routes.py
    act_routes.py
    saved_search_routes.py
    notification_routes.py
    device_routes.py
    account_routes.py
  mobile_auth.py
  search_service.py
  act_service.py
  alert_matching.py
  notifications.py
  push_provider.py
notification_worker.py
migrations/
tests/
  test_api_v1_*.py
  test_mobile_auth.py
  test_notification_*.py
```

Rules:

- Route modules validate/authorize and call domain services; they do not contain long SQL programs.
- HTML routes continue working. Shared logic moves into services only when tests prove behavior stayed identical.
- `app/extractors.py`, `app/exporter.py` and `app/ocr.py` remain untouched and byte-identical to their sibling tool.
- Mobile-specific logic never goes into the legacy `frontend/`.
- Provider-specific Expo behavior stays behind `push_provider.py`.
- The notification worker is not added to the existing subprocess job queue; it owns small, continuous database jobs and has different liveness requirements.

### 4.2 Separate mobile repository

Recommended starting shape:

```text
KHMDHS-Mobile/
  app/                         Expo Router screens/layouts
  src/
    api/                       generated client + auth retry
    auth/                      session state and secure storage
    features/
      search/
      acts/
      saved-searches/
      notifications/
      account/
    components/                product design system
    i18n/                      Greek and English resources
    telemetry/                 privacy-reviewed events and crash reporting
  e2e/
  assets/
  app.config.ts
  eas.json
```

The OpenAPI file in this repository is authoritative. The mobile repository generates API types from a pinned copy and CI fails when its copy drifts from the backend contract.

## 5. API-wide contract

### 5.1 Transport and representation

- HTTPS only outside local development.
- JSON request and response bodies with UTF-8.
- Base path `/api/v1`; additive compatible changes stay in v1, breaking changes require a new version.
- `Authorization: Bearer <access-token>` for protected routes.
- `Accept-Language: el|en`; unsupported/missing values fall back to Greek.
- Dates are `YYYY-MM-DD`; timestamps are ISO 8601 UTC ending in `Z`.
- Monetary values are decimal strings plus a currency, never binary floating-point values.
- Database bigint identifiers are JSON strings. ADAM remains a string.
- Enum codes are stable machine values; translated labels are separate fields.
- API responses never contain HTML snippets. Search highlights are plain text plus optional character ranges.
- Every response carries `X-Request-ID`; errors also include it in the body.
- Token/authentication responses use `Cache-Control: no-store`.

### 5.2 Error envelope

```json
{
  "error": {
    "code": "validation_error",
    "message": "One or more fields are invalid.",
    "fields": [
      {"field": "filters.value_min", "code": "invalid_decimal"}
    ],
    "request_id": "req_01..."
  }
}
```

Required stable codes include:

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `invalid_request` | Structurally readable but invalid input |
| 401 | `invalid_credentials` | Login failed without revealing account existence |
| 401 | `token_expired` | Access token expired; refresh may be attempted |
| 401 | `session_revoked` | Refresh/re-authentication required |
| 403 | `access_expired` | Valid account without current product entitlement |
| 403 | `password_change_required` | Account must change an administrator-issued password |
| 404 | `not_found` | Missing or inaccessible resource |
| 409 | `conflict` | State/version conflict or duplicate operation |
| 422 | `validation_error` | Field validation failure |
| 429 | `rate_limited` | Includes `Retry-After` |
| 500 | `internal_error` | Safe generic message; detail remains server-side |

### 5.3 Idempotency

- Device registration is idempotent on `(user_id, installation_id)`.
- Push-setting updates use `PUT` and replace the represented settings.
- Mark-read and mark-all-read are naturally idempotent.
- Notification event creation has a database uniqueness key; worker retries cannot duplicate events.
- Mutation endpoints that can be retried after a network timeout accept `Idempotency-Key`, retained for 24 hours per account and route.

### 5.4 Pagination

Search and inbox endpoints use opaque cursor pagination:

```json
{
  "items": [],
  "next_cursor": "opaque-signed-value-or-null",
  "snapshot_at": "2026-09-14T10:00:00Z"
}
```

The cursor binds:

- normalized filters and sort;
- the snapshot timestamp;
- the final sort value and ADAM/id tie-breaker;
- direction and expiry.

It is HMAC-signed and rejected if changed or reused with different filters. New ingestion after `snapshot_at` appears only after an explicit refresh, preventing duplicate or shifting results during continuous scrolling.

## 6. Native authentication design

### 6.1 Decision

Keep the existing account/password/TOTP authority and add a separate first-party native session mechanism. Do not put the browser session cookie in a WebView and do not ship a client secret inside the app.

The mobile contract uses opaque random bearer tokens:

- access token lifetime: **15 minutes**;
- refresh idle lifetime: **30 days**;
- refresh absolute lifetime: **90 days from initial login**;
- refresh token rotates on every successful use;
- reuse of an already-rotated token revokes the full token family;
- access and refresh tokens are hashed at rest with a keyed hash;
- refresh tokens are stored in Expo SecureStore; access tokens are kept in memory and may be re-created after restart;
- every session stores the account's `session_version`; a mismatch revokes it;
- password change, role change, MFA change, account deactivation or deletion invalidates native sessions as it does browser sessions.

This applies refresh-token rotation and bounded lifetime in line with [RFC 9700](https://www.rfc-editor.org/info/rfc9700/). Expo SecureStore uses platform-protected storage, but the implementation must account for its documented lifecycle differences between Android and iOS: [Expo SecureStore](https://docs.expo.dev/versions/latest/sdk/securestore/).

### 6.2 Password login

1. App generates a stable random installation identifier and sends credentials plus device metadata over TLS.
2. Server uses the existing password hash and DB-backed throttle.
3. Invalid account, deactivated account and wrong password return the same public error.
4. If MFA is disabled, the server creates a device/session and returns tokens.
5. If MFA is enabled, the server returns `202` with a one-use challenge identifier expiring in five minutes; no session exists yet.
6. TOTP/recovery verification consumes the challenge, creates the session and returns tokens.

The browser and mobile login attempts share the same account/IP throttle budget so the second endpoint cannot bypass protection on the first.

### 6.3 Passwordless login

- The request endpoint returns the same response for every syntactically valid address.
- The mail contains an HTTPS universal/app link on an owned domain.
- Opening the link shows an explicit confirmation screen in the app; opening the URL alone does not spend the token.
- Confirmation calls the one-time exchange endpoint.
- Accounts with MFA still receive an MFA challenge, not tokens.
- The link token never appears in analytics, crash events, request logs or referrer data.
- If the app is unavailable, the canonical HTTPS page falls back to the existing web confirmation.

### 6.4 Refresh and logout

- Refresh is serialized by the mobile client so one device does not race itself.
- A valid refresh rotates the token and issues a new 15-minute access token in one transaction.
- A stale refresh token triggers family revocation and `session_revoked`.
- Logout revokes the current mobile session and clears secure storage.
- “Log out all devices,” password/MFA changes and account deletion revoke all families.
- Revoking a device revokes its sessions and disables its push token.

## 7. Data model design

Names are implementation targets; migrations remain separate, timestamped and idempotent.

### 7.1 `proc.mobile_device`

One installation associated with one account.

| Field | Contract |
|---|---|
| `id` | bigint primary key |
| `user_id` | FK `app_user`, cascade on account deletion |
| `installation_id` | UUID generated by app; unique per user |
| `platform` | `ios` or `android` |
| `display_name` | optional user-visible device label |
| `app_version`, `os_version` | bounded text |
| `locale`, `timezone` | supported locale/IANA timezone |
| `permission_status` | `unknown`, `granted`, `denied`, `provisional` |
| `push_provider` | initially `expo` |
| `push_token_ciphertext` | encrypted provider token, nullable |
| `push_token_hash` | keyed lookup/deduplication hash, nullable |
| `enabled`, `last_seen_at`, `revoked_at` | lifecycle state |
| timestamps | creation/update |

Provider tokens are not authentication credentials, but they are sensitive routing identifiers. Encrypt them with a dedicated versioned server key and redact them from all logs.

### 7.2 `proc.mobile_session`

| Field | Contract |
|---|---|
| `id`, `family_id` | random UUIDs generated server-side |
| `user_id`, `device_id` | FKs with cascade |
| `refresh_token_hash` | unique keyed hash; raw token never stored |
| `session_version` | copied from `app_user` at authorization |
| `scope` | fixed MVP scope, not client-controlled |
| `refresh_expires_at` | sliding 30-day idle deadline |
| `absolute_expires_at` | fixed 90-day maximum |
| `rotated_at`, `replaced_by_id` | replay-detection chain |
| `revoked_at`, `revoke_reason` | explicit terminal state |
| `last_used_at`, timestamps | audit/lifecycle |

Short-lived access tokens use a separate hashed token table or equivalent indexed record so access-token lookup does not scan refresh history. Terminal token records are retained for 30 days for replay/security investigation and then purged.

### 7.3 `proc.mobile_auth_challenge`

One-use, five-minute challenges for MFA and passwordless exchange. Store a hash, user, purpose, expiry, attempt counter, consumed timestamp and originating installation/IP hash. Maximum verification attempts: five.

### 7.4 `proc.mobile_notification_preference`

One row per user:

- global pause;
- timezone;
- quiet start `22:00` and end `08:00`;
- default summary time `08:30`;
- daily cap `6`, constrained to `1..10`;
- language;
- update timestamp.

### 7.5 `proc.push_subscription`

One row per `(user_id, search_profile_id)`:

- active flag;
- delivery mode `immediate` or `daily`;
- new-match enabled;
- deadline enabled and lead-day array, default `{7,1}`;
- independent `last_cursor` and evaluation timestamps;
- created/updated metadata.

The FK to `search_profile` cascades on deletion. A row may reference an owned customer profile or an available published portal profile, enforced in the service before insert/update.

### 7.6 `proc.notification_event`

The durable user-visible inbox item:

- user and optional push subscription;
- event type `new_match`, `deadline`, or `daily_summary`;
- opaque unique `dedupe_key`;
- target kind/id and optional ADAM;
- localized title/body snapshot suitable for the inbox;
- minimal structured context JSON;
- created/read/deleted/expiry timestamps;
- `expires_at = created_at + 90 days`.

Do not store complete saved-search criteria in the event payload.

### 7.7 `proc.notification_delivery`

One attempt stream per `(event_id, device_id)`:

- state `queued`, `submitting`, `accepted`, `retry`, `rejected`, `expired`;
- provider ticket/receipt identifiers;
- attempt count and `next_attempt_at`;
- safe provider error code, not raw payload;
- claim/lease fields for worker recovery;
- terminal timestamp and 30-day retention.

“Accepted” means accepted by the provider, not displayed or seen by the user.

### 7.8 `proc.push_deadline_notice`

Unique on `(push_subscription_id, adam, lead_days, deadline)`. A moved deadline creates a new eligible key; worker retries for the same deadline do not.

### 7.9 `proc.api_idempotency_key`

Scoped by user, method, route and caller-supplied key. Stores a request fingerprint and completed response reference for 24 hours. A key reused with another body returns `409 conflict`.

## 8. API endpoint plan

The companion OpenAPI contract contains request/response shapes. Delivery slice is shown here.

| Method and path | Slice | Purpose |
|---|---:|---|
| `POST /api/v1/auth/login` | A | Password step; tokens or MFA challenge |
| `POST /api/v1/auth/mfa/verify` | A | Consume native MFA challenge |
| `POST /api/v1/auth/refresh` | A | Rotate refresh token |
| `POST /api/v1/auth/logout` | A | Revoke current session |
| `POST /api/v1/auth/magic-link/request` | B | Send enumeration-safe login link |
| `POST /api/v1/auth/magic-link/exchange` | B | Explicit one-time exchange |
| `POST /api/v1/auth/register` | D | Existing self-registration policy in JSON |
| `GET /api/v1/me` | A | Identity, role, entitlement, product/expiry, security flags |
| `GET /api/v1/lookups` | B | Localized filter vocabulary with cache validators |
| `POST /api/v1/acts/search` | B | Typed filters and cursor pagination |
| `GET /api/v1/acts/{adam}` | B | Mobile act detail DTO |
| `GET /api/v1/authorities/{org_id}` | D | Mobile authority identity, totals and recent acts |
| `GET /api/v1/contractors/{vat}` | D | Mobile contractor identity, buyers, totals and recent acts |
| `GET /api/v1/saved-searches` | B | Owned plus available published profiles |
| `POST /api/v1/saved-searches` | B | Save active typed filters |
| `PATCH /api/v1/saved-searches/{id}` | B | Rename owned profile |
| `DELETE /api/v1/saved-searches/{id}` | B | Delete owned profile and channel settings |
| `GET/PUT/DELETE /api/v1/saved-searches/{id}/email-alert` | D | Existing email setting in JSON |
| `GET/PUT/DELETE /api/v1/saved-searches/{id}/push-alert` | C | Push setting for this profile |
| `GET/PUT /api/v1/notification-settings` | C | Global pause, timezone, quiet hours, cap |
| `GET/POST /api/v1/devices` | C | List/register current installation |
| `DELETE /api/v1/devices/{id}` | C | Revoke owned device and sessions |
| `GET /api/v1/notifications` | C | Cursor-paginated inbox |
| `PATCH/DELETE /api/v1/notifications/{id}` | C | Mark read or delete own event |
| `POST /api/v1/notifications/read-all` | C | Mark current user's events read |
| `POST /api/v1/account/password` | D | Re-authenticated password change |
| `GET /api/v1/account/mfa` | D | Inspect 2FA state |
| `POST /api/v1/account/mfa/enrollment` | D | Start re-authenticated enrolment |
| `POST /api/v1/account/mfa/enrollment/confirm` | D | Verify TOTP and return recovery codes once |
| `DELETE /api/v1/account/mfa` | D | Re-authenticated 2FA disable |
| `POST /api/v1/account/export` | D | Request/open personal-data export |
| `DELETE /api/v1/account` | D | Confirmed, re-authenticated deletion |

All resource lookups use the current user in the SQL predicate. An identifier belonging to another customer returns the same `404` as a nonexistent identifier.

## 9. Search contract and extraction plan

### 9.1 Typed filters

The API publishes these existing filters:

- `q`, `fulltext`, `tables_q`;
- `type[]`, `authority[]`, `source[]`;
- `cpv[]`, `category[]`;
- `contract_type[]`, `procedure_type[]`, `nuts[]`;
- publication and deadline ranges;
- min/max value;
- status and sort.

Validation before SQL construction:

- reject unknown enum values instead of silently dropping them;
- range start must not exceed range end;
- numeric values are non-negative decimals and min must not exceed max;
- at most 20 selections per multi-value filter;
- text search fields are capped at 300 Unicode characters;
- page size defaults to 20 and is capped at 50;
- category values retain the current `c:<id>` / `s:<id>` representation internally but the API exposes structured category/subcategory identifiers.

### 9.2 Shared service

Extract a `SearchFilters` normalization layer and search service. Both HTML and API calls feed the same normalized dictionary to `build_where()`. Contract tests compare representative HTML-query and API-filter result ADAMs to prove parity.

Do not duplicate `build_where()` in the API router. Any behavior correction is made once and tested for both clients.

### 9.3 Result DTO

Each result includes:

- ADAM, act type code/label and data source;
- title and authority identifier/name;
- publication/signature/deadline dates;
- resolved value as decimal string and correction flag;
- cancellation/modification state;
- contract/procedure/NUTS codes and labels when present;
- plain-text search snippet;
- bounded match reasons derived through `app/search_match.py`.

The mobile API does not expose database row dictionaries wholesale.

### 9.4 Totals

The first page returns total count and value using the existing cache. Later cursor pages may omit recomputation and repeat the first-page totals from the signed cursor. The client treats them as a snapshot summary, not a live counter.

## 10. Act-detail contract

The service behind `/act/{adam}` is split into reusable fetch functions, then mapped into a mobile DTO with sections:

1. screening summary;
2. core facts and dates;
3. authority and parties/operators;
4. objects, CPVs, categories and lots;
5. deadlines and procedure;
6. lifecycle and related acts;
7. official source and customer-visible attachments;
8. published extracted tables/full text availability;
9. provenance;
10. stored AI summary only when the later feature flag is enabled.

Rules:

- keep server authorization and feature flags;
- do not expose `raw_json`, internal notes, unpublished tables, admin annotations, job state or edit controls;
- distinguish source facts, calculated/display values and AI-produced text in the DTO;
- official URLs are allowlisted `https` links; the app never executes document content;
- missing optional sections are omitted or marked unavailable, never synthesized.

## 11. Saved-search and alert rules

- Maximum 25 owned saved searches, matching the existing server limit.
- A saved search needs at least one effective filter.
- Customers can rename/delete only their own customer-scoped profiles.
- Customers can run and subscribe to published portal profiles but cannot edit them.
- Guessing another customer's or unpublished profile identifier returns `404`.
- Deleting an owned profile cascades email/push settings and their cursors.
- Email settings continue using `digest_subscription` and offered admin-defined schedules.
- Push settings use `push_subscription`; they are not represented as an email layout or recipient.
- Lapsed customers retain settings and history but receive no new delivery.
- A customer re-entitled after a lapse begins from a defined reactivation cursor, not an unbounded catch-up of every missed act. MVP rule: start at reactivation time and keep previously created inbox events.

## 12. Notification evaluation and delivery

### 12.1 New-match evaluation

For each due active push subscription:

1. Lock the subscription row briefly.
2. Define `(cursor_from, cursor_to]`; a new subscription starts at its creation/reactivation time.
3. Run the effective saved-search filters plus `ingested_at` bounds.
4. Create event rows with a deterministic dedupe key.
5. Advance the push cursor only after those events commit.
6. Apply quiet-hour, mode and cap rules to delivery creation.

Suggested dedupe key input:

```text
new_match | user_id | search_profile_id | adam | eligibility_window
```

Multiple saved searches may legitimately explain the same act. The inbox may either show separate reasons or collapse them into one event with multiple matching profile IDs. MVP decision: **collapse per user and ADAM within one evaluation cycle**, preserving every matching profile name in server-side context while showing a concise explanation.

### 12.2 Deadline evaluation

- Evaluate active notices against selected lead days in customer timezone.
- Use the push-specific deadline ledger.
- A changed deadline re-arms the lead-day events.
- Deadline reminders have priority in the daily individual-delivery budget.
- A reminder that becomes due during quiet hours is released after 08:00; it is not marked OS time-sensitive.

### 12.3 Daily and overflow summaries

- Daily-mode subscriptions accumulate inbox events without individual pushes.
- At 08:30 customer-local time, one summary event groups eligible counts and high-level categories/searches.
- Immediate mode allows up to five individual alerts; a sixth delivery slot is reserved for one overflow summary.
- After the six-push account cap, new events are inbox-only until the next local day/summary.
- Cap calculation is per user/local date; fan-out to multiple devices does not multiply usage.
- Global pause creates inbox events but no delivery rows.

### 12.4 Delivery worker

- Claim due delivery rows with `FOR UPDATE SKIP LOCKED` and a lease timeout.
- Send provider-sized batches through the adapter.
- Persist ticket identifiers and query receipts after the provider's recommended delay.
- Retry only transient failures with bounded exponential backoff and jitter.
- Mark permanent failures terminal; an invalid/unregistered token disables that device token.
- Never retry beyond the event's useful/retention window.
- Use one event row regardless of the number of devices.
- Provider acceptance and receipt status are operational facts, never represented as “seen.”

Expo documents the distinction between push tickets and later receipts here: [Sending notifications with the Expo Push Service](https://docs.expo.dev/push-notifications/sending-notifications/).

### 12.5 Cleanup

A daily idempotent cleanup job:

- soft-deletes/expires inbox events at 90 days, then hard-deletes after a short recovery buffer;
- deletes terminal delivery records after 30 days;
- deletes expired/revoked native token records after 30 days;
- removes revoked device registrations after 90 days when no retained security record needs them;
- purges expired idempotency keys after 24 hours;
- records only aggregate cleanup counts.

## 13. Deep-link contract

Canonical links use an owned HTTPS domain:

```text
https://<owned-domain>/m/act/<adam>
https://<owned-domain>/m/notifications/<event-id>
https://<owned-domain>/m/auth/link/<one-time-token>
```

- Configure iOS Universal Links and Android App Links for the exact host/path set.
- Notification payloads carry `event_id` and a schema version, not arbitrary URLs.
- The app fetches the event after authentication and lets the server decide the target.
- Signed-out taps authenticate and then resume the pending event.
- Unknown/expired events fall back to the inbox with a clear message.
- The web fallback opens the corresponding canonical web page where appropriate.
- Exact redirect matching is required; no user-controlled open redirects.

## 14. Mobile state and screen delivery

### 14.1 Navigation

- Root auth gate.
- Four authenticated tabs: Search, Saved, Notifications, Account.
- Act/entity screens are stack routes over their source tab.
- Deep links enter the same route graph rather than a parallel screen.

### 14.2 Client data

- Server state uses one query/cache layer with keys derived from normalized filters.
- Access token is memory-only; refresh token and installation ID use secure storage.
- Filter edits are local until Apply; the effective applied filter object is canonical.
- Search pages remain bound to their server snapshot cursor.
- Inbox unread badge comes from the server and is updated optimistically only for idempotent read actions.
- No procurement document body is retained offline in MVP.

### 14.3 Offline behavior

The MVP supports graceful unavailability, not offline mode:

- show cached list/detail content only as stale read-only UI where the query library already has it;
- do not present cached deadlines as current without a stale timestamp;
- queue only safe idempotent read-state changes briefly;
- require connectivity for login, search, saving settings and account/security changes.

## 15. Security and privacy control matrix

| Risk | Required control | Verification |
|---|---|---|
| Credential stuffing | Shared DB throttle, generic login errors, request/IP telemetry | Unit + integration abuse tests |
| MFA bypass | No tokens before challenge consumption; five-minute/one-use challenge | Auth state-machine tests |
| Refresh-token theft/replay | Secure storage, keyed hash, rotation, family revocation, absolute expiry | Reuse/concurrency tests |
| Lost device | Per-device revoke; password/MFA changes invalidate all sessions | Real-device and API tests |
| Cross-customer IDOR | User predicate in every query; inaccessible IDs return 404 | Two-customer matrix tests |
| Entitlement bypass | Central dependency plus service check for scheduled delivery | Lapsed/reactivated tests |
| Push privacy leak | Minimal lock-screen text; no full criteria; fetch after auth | Payload snapshot review |
| Push token leakage | Encryption at rest, keyed lookup hash, log redaction | Secret/log tests |
| Deep-link interception | Verified HTTPS app links, exact path matching, one-time exchange | iOS/Android link tests |
| Query abuse | Typed bounds, list/text caps, shared rate limiting, statement timeout | Fuzz/rate/load tests |
| Duplicate alerts | Unique dedupe key, transaction boundary, ledger, idempotent worker | Retry/concurrency tests |
| Worker crash | Leased claims, durable rows, receipt reconciliation | Kill/restart tests |
| Sensitive telemetry | Allowlisted event properties; no tokens, search text or document content | Telemetry schema review |

## 16. Test strategy

### 16.1 Backend

- Pure unit tests for filter validation, cursor signing, token hashing/rotation, quiet hours, cap allocation and dedupe keys.
- DB-backed API tests against `tests/proc_schema.sql` for every endpoint.
- Contract tests validate responses against the committed OpenAPI document.
- Parity tests compare HTML and API search results for the same filters.
- Ownership matrix: owner, other customer, published portal, unpublished portal, admin, anonymous.
- Entitlement matrix: admin, entitled, tester, expired/lapsed, inactive.
- Time tests cover Athens DST changes, midnight caps and quiet periods crossing midnight.
- Notification worker tests cover retry, lease expiry, duplicate evaluation, multiple devices, invalid token and provider outage.
- Migration tests apply every new migration twice and validate constraints/indexes.

Whenever a table is added, regenerate `tests/proc_schema.sql` from the resulting schema.

### 16.2 Mobile

- Unit tests for auth reducer, filter normalization, deep-link parser and notification-setting rules.
- Component tests for loading, error, empty, offline, lapsed and accessibility states.
- End-to-end tests on current supported iOS and Android device classes.
- Push matrix: foreground, background, terminated, permission denied, token changed, two devices, quiet hours and cap reached.
- Greek/English visual checks including large text and long translated labels.
- Real-device passwordless app-link and account-deletion tests.

### 16.3 Security verification before beta

- Dependency and secret scanning in both repositories.
- API authorization review and automated IDOR suite.
- Mobile binary/config review for embedded secrets and non-production endpoints.
- Token replay and refresh-race review.
- App-link/domain verification.
- Notification payload and retention review.
- External penetration test or independent security review before public launch.

## 17. Observability and initial beta objectives

Every log/event carries request ID and, where applicable, anonymized session/event identifiers. Never log credentials, bearer tokens, provider tokens, one-time links or full saved-search text.

Dashboards:

- API request count, p50/p95 latency and error rate by route;
- login/MFA/refresh success and throttling without usernames;
- active devices and invalid-token rate;
- evaluation lag from ingestion to notification event;
- queued/retry/terminal delivery counts and oldest queue age;
- provider acceptance/receipt errors;
- push volume per customer distribution and cap/quiet deferrals;
- notification opens, mutes and disables by event type;
- mobile crash-free sessions and app versions.

Provisional beta objectives, to be recalibrated after a measured baseline:

- p95 ordinary API response under 1.5 seconds, search under 2.5 seconds;
- p95 immediate-event creation within 10 minutes after the act is available in the database;
- no duplicate inbox event for the same dedupe key;
- 99% of eligible delivery rows submitted or terminally classified within 10 minutes while providers are available;
- at least 99.5% crash-free mobile sessions;
- zero cross-account data disclosures and zero secrets in logs.

## 18. Deployment and operating model

### 18.1 Required environments

| Environment | Backend/database | Mobile identity | Push project |
|---|---|---|---|
| Development | local/throwaway | dev bundle/package | development credentials |
| Staging | isolated service/test data | staging bundle/package | staging credentials |
| Production | current production data | production bundle/package | production credentials |

Never point development or preview builds at production by default.

### 18.2 Production worker decision

The current Render web service is on a sleeping free plan and runs some background work inline. That is not sufficient for timely push notifications: when the web service sleeps or deploys, evaluation and delivery stop.

Recommended production requirement:

- one paid, always-on notification worker in Frankfurt, separate from the web process;
- one owner-monitored cleanup schedule;
- database credentials limited to the tables/actions the worker needs;
- provider credentials and token-encryption keys set separately on web and worker as required;
- health/liveness based on database heartbeat and oldest queue age.

This is a paid infrastructure requirement, not an optional performance upgrade. A free-only pilot can test on demand, but it cannot make a reliable notification-timeliness promise.

### 18.3 Required secrets/configuration

- dedicated access/refresh token hashing keys with versions;
- push-token encryption key with rotation version;
- Expo push access credentials and Apple/Google credentials;
- exact API and app-link origins;
- allowed mobile app identifiers;
- retention/cap defaults as configuration bounded by DB constraints;
- crash-reporting key configured without user-content capture.

No secret is committed to either repository or embedded in the mobile bundle.

## 19. Migration and rollout sequence

### Phase 1 — additive foundation

1. Add native device/session/challenge/idempotency tables and constraints.
2. Add `/api/v1` error/dependency/auth foundation behind `MOBILE_API_ENABLED=0`.
3. Add auth tests, token cleanup and audit telemetry.
4. Enable only in development/staging.

### Phase 2 — vertical prototype

1. Extract shared search and act services under parity tests.
2. Add lookups, search, act and saved-search APIs.
3. Generate the mobile client and build sign-in → search → act → save flow.
4. Observe invited internal users; no push yet.

### Phase 3 — notification value

1. Add preferences, push subscription, event, delivery and deadline-ledger tables.
2. Extract channel-neutral matching without changing email output.
3. Add fake/memory provider and deterministic worker tests.
4. Add Expo adapter, staging devices, inbox and deep links.
5. Load/chaos test before any production token is accepted.

### Phase 4 — account and beta

1. Add account/device/security flows and entity summaries.
2. Close privacy, terms, support, retention and store-account gates.
3. Deploy always-on worker and dashboards.
4. Release to a named beta cohort with rollback and support coverage.

All database changes are additive until both clients have adopted them. Rollback disables the mobile API/worker first; schema removal is a later, separately approved operation.

## 20. Stage-3 implementation backlog

### A1 — API shell and conventions

- Add `/api/v1` router, error envelope, request IDs, language resolution and no-store auth headers.
- Add OpenAPI response conformance tests.
- Keep API disabled by default in production.

**Done when:** health-level contract tests run without affecting any HTML route.

### A2 — Native session schema and token primitives

- Add device, session, access-token, challenge and idempotency schema.
- Implement random token generation, keyed hashing, rotation and cleanup.
- Implement session-version invalidation.

**Done when:** token reuse, expiry, revoke and concurrent-refresh tests pass.

### A3 — Login, MFA, refresh, logout and `/me`

- Reuse existing credential, throttle, TOTP/recovery and entitlement logic.
- Return the committed JSON states.
- Add two-user/expired/inactive/password-change tests.

**Done when:** a test client completes and revokes a device session without creating a browser session.

### A4 — Search service extraction and API

- Introduce typed filter normalization.
- Add signed snapshot cursor and result DTO.
- Add parity and query-bound tests.

**Done when:** representative API/HTML filters return the same ADAMs and pagination does not duplicate items after new ingestion.

### A5 — Act-detail service and API

- Extract reusable query groups.
- Build safe customer DTO and feature-gated sections.
- Add missing/unauthorized/source-URL tests.

**Done when:** the mobile response contains every approved screening field and no internal/admin field.

### A6 — Saved-search API

- Add list/create/rename/delete routes with existing caps and ownership rules.
- Support published portal profiles as read-only/applicable.
- Add cascade and `404` anti-enumeration tests.

**Done when:** MOB-004 passes at API level.

### A7 — Mobile repository bootstrap

- Create the separate Expo repository and environments.
- Generate API types.
- Build design tokens, localization, auth shell and four-tab navigation.
- Configure secure storage and development app links.

**Done when:** development builds run on one iOS and one Android device class and CI has no production secrets.

### A8 — Vertical mobile path

- Implement login/MFA, search/filter, results, act detail and save search.
- Add offline/error/lapsed/accessibility states.
- Add end-to-end tests and instrumentation.

**Done when:** invited observers can complete the Stage-1 Slice-B journey against staging.

## 21. Stage-2 decisions and remaining prerequisites

Technical decisions fixed by this document:

- opaque 15-minute access tokens;
- rotating 30-day-idle/90-day-absolute refresh tokens;
- verified HTTPS app links;
- separate email and push subscriptions/cursors;
- durable notification events and per-device delivery rows;
- Expo behind a provider adapter;
- always-on production notification worker;
- cursor/snapshot pagination;
- Stage-1 retention, cap and quiet-hour defaults.

Inputs still needed before public beta, but not blockers to Stage-3 development:

- final app/product name and bundle/package identifiers;
- owned public domain for API and verified app links;
- initial store countries/regions;
- Apple and Google developer-account owners;
- named beta cohort and support owner;
- final privacy/terms text and incident owner;
- approval for the always-on worker cost before notification production rollout.

## 22. Stage-2 completion checklist

- [x] Existing code/data boundaries mapped.
- [x] Target component and repository boundaries defined.
- [x] API conventions and endpoint inventory defined.
- [x] Native authentication lifecycle defined.
- [x] Data responsibilities, constraints and retention defined.
- [x] Search/act/saved-search reuse plan defined.
- [x] Notification evaluation/delivery algorithm defined.
- [x] Deep-link, security, privacy and testing controls defined.
- [x] Deployment requirement and paid-worker dependency identified.
- [x] Stage-3 backlog sequenced with definitions of done.
- [x] Stage-2 technical review accepted for implementation.
