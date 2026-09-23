# Spec: KHMDHS mobile app — stage 1 product scope and architecture

**Status:** Product and architecture decisions approved by the product owner on 2026-09-14 — analysis only. Nothing in this document has been implemented.
**Repo path:** `docs/specs/mobile-app-stage-1.md`
**Scope:** customer-facing iOS and Android application, plus the backend capabilities needed to support it. Admin, CRM, ingestion and curation remain web-only.
**Approved baseline:** one cross-platform React Native/Expo client, Greek and English from the first public release, connected only to the existing FastAPI service.

---

## 1. Decision summary

Build a **separate mobile product**, not a mobile rendering of every web page.

The recommended shape is:

- a separate `KHMDHS-Mobile` repository for the React Native/Expo application;
- this repository continues to own PostgreSQL, ingestion, business rules, authentication authority, entitlements and notification matching;
- a versioned `/api/v1` interface becomes the only mobile route to server data;
- the legacy `frontend/` is not reused and the mobile client never queries Supabase directly;
- four primary mobile destinations: **Search**, **Saved**, **Notifications**, **Account**;
- act, authority and contractor details are secondary screens reached from search results, saved searches and notifications;
- push notifications are built on saved-search criteria, with a durable in-app notification history;
- email alerts remain available and independent. Push is another delivery channel, not a replacement for email.

This boundary allows the web product and mobile product to evolve independently while keeping one source of truth for procurement data and access control.

## 2. Product problem

The web app is capable but information-dense. A customer using a phone is usually trying to do one of three things:

1. Check whether anything relevant has appeared.
2. Screen a procurement act quickly enough to decide whether it deserves attention on a larger screen.
3. Act on an approaching deadline or a material change.

Reproducing every table and administrative function would make the mobile app slower to build and harder to use. Its primary value should be **relevance and timely action**, not full desktop parity.

## 3. Evidence from the current application

### 3.1 Reusable foundations

- `proc.procurement_act` and its related tables remain the canonical data model.
- `build_where()` in `app/main.py` already implements the search semantics.
- `proc.search_profile` already stores a customer's reusable filter set.
- `proc.digest_subscription` and `app/digests.py` already evaluate saved searches on a schedule and record runs.
- Customer entitlement is already derived server-side and must continue to control access and alert delivery.
- Existing account security includes passwords, passwordless links, TOTP 2FA, recovery codes and session invalidation through `session_version`.
- The current UI and catalog already support Greek and English.

### 3.2 Mobile gaps

- Only the main search has a deliberate JSON response. Act, authority, contractor, analytics, saved-search, alert and account flows are primarily HTML/form endpoints.
- Authentication is a signed browser cookie with CSRF protection. A native application needs a mobile session contract and secure token lifecycle.
- There is no mobile-device registry, push token lifecycle, notification outbox, delivery receipt processing or in-app notification inbox.
- The digest implementation is email-oriented. Matching should be extracted behind a channel-neutral boundary before push delivery is added.
- The privacy and terms pages are currently rendered as drafts. They are a release blocker for a public store launch.
- There is no mobile build, device-test, crash-reporting, release or store-signing pipeline.

## 4. Goals and non-goals

### Goals

- Let a customer find and screen relevant acts comfortably on a phone.
- Let a customer save a meaningful search and control how it notifies them.
- Notify the customer about new matches and deadlines without creating notification fatigue.
- Preserve current entitlement, account-security and data-provenance rules.
- Deep-link every notification to the exact act or saved-search result that explains it.
- Give the customer a durable in-app history because operating systems do not guarantee that every push remains visible.
- Support incremental delivery without requiring web/mobile feature parity.

### Non-goals for the first public release

- Admin, CRM, ingestion, curation, merge management or audit functions.
- Tender-table extraction, OCR, upload or editing.
- Bulk CSV/XLSX export.
- Bid preparation or bid submission.
- Full offline access to the procurement corpus or document archive.
- Telephony features.
- Replacing the web product for deep research, large tables or long-document review.
- Marketing campaigns or promotional push notifications.

## 5. Customer-function disposition

| Current capability | Mobile decision | Release | Mobile treatment |
|---|---|---:|---|
| Register, password login and logout | Include | MVP | Native forms and mobile session lifecycle |
| Passwordless login link | Include | MVP | Universal/app link returns to the app; 2FA still applies |
| TOTP and recovery-code challenge | Include | MVP | Native challenge screen |
| Search acts | Include | MVP | Search-first home screen |
| Search filters | Include selectively | MVP | Bottom sheet; common filters first, advanced filters grouped |
| Search result totals | Include | MVP | Count and value summary without desktop-density controls |
| Result list | Include | MVP | Touch-friendly cards and continuous pagination |
| Act detail | Include selectively | MVP | Screening summary first; expandable evidence sections |
| Official source document | Include | MVP | Open externally or in a system document viewer |
| Uploaded/published attachments | Include when available | MVP | View, download and share individual files; no ZIP bundle requirement |
| Match explanation / occurrences | Include | MVP | Explain why the act matched; jump to relevant text when available |
| Process lifecycle and related acts | Include | MVP | Compact timeline/list |
| Saved searches | Include | MVP | List, create from active filters, rename and delete owned searches |
| Email alert settings | Include | MVP | Preserve existing options in a mobile-friendly editor |
| Push alert settings | Add | MVP | Per saved search, explicit opt-in |
| Notification history | Add | MVP | Read/unread inbox with deep links |
| Authority detail | Include selectively | MVP | Identity, contact, headline totals and recent acts |
| Contractor detail | Include selectively | MVP | Identity, contact, headline totals, top buyers and recent acts |
| Full authority/contractor directories | Defer | Later | Add only if usage data supports mobile discovery |
| Explore summary | Defer | Later | Compact ranking cards, not desktop tables |
| Analytics | Defer | Later | Mobile-specific charts and date controls |
| Existing AI summary | Defer | Later | Display stored summary only; generation remains controlled server-side |
| Watch one act | Add | Later | Material-change and deadline notifications |
| Follow authority or contractor | Add | Later | New-act notifications scoped to that entity |
| Bookmarks / limited offline cache | Add | Later | Metadata and selected content only |
| Account profile and subscription status | Include | MVP | Read-only identity/subscription summary |
| Change password | Include | MVP | Re-authentication required |
| Manage 2FA | Include | MVP | Enrol, disable and regenerate recovery codes |
| Personal-data export | Link or share | MVP | Request server export and open/share the returned file |
| Account deletion | Include | MVP | Explicit confirmation and password/re-authentication |
| Glossary, data sources, AI policy, privacy and terms | Include lightly | MVP | Native list linking to responsive server-hosted canonical content |
| Bulk export, tables tool, admin and CRM | Exclude | Web only | “Continue on web” only where a customer genuinely needs it |

## 6. Mobile information architecture

### 6.1 Primary navigation

1. **Search**
   - Search entry and recent search state
   - Results
   - Filter sheet
   - Act detail
   - Authority and contractor detail

2. **Saved**
   - Saved searches
   - Create from current search
   - Rename/delete owned search
   - Email settings
   - Push settings

3. **Notifications**
   - Notification inbox
   - Unread/all filter
   - Notification detail/deep link
   - Global push and quiet-hour settings

4. **Account**
   - Profile and subscription status
   - Language
   - Password and 2FA
   - Registered devices
   - Personal-data export
   - Account deletion
   - Data sources, AI policy, privacy and terms

### 6.2 Secondary navigation

- Act detail is opened from results, saved-search results, notification history or a deep link.
- Authority and contractor details are opened from an act or result card.
- Advanced filters open as a modal/bottom sheet and return to the same search.
- Destructive account actions are never primary navigation items.

## 7. Screen specifications

### 7.1 Authentication

**Purpose:** establish a customer session without weakening existing password, throttling, entitlement or 2FA rules.

**Screens:** sign in, request login link, login-link confirmation, MFA challenge, registration, forced password change.

**Key states:** loading, invalid credentials, throttled, inactive account, expired challenge, forced password change, expired/lapsed product.

**Important behavior:** a passwordless link completes only the password step. Accounts with 2FA must still pass the TOTP/recovery-code challenge.

### 7.2 Search home and results

**Purpose:** move from an information need to a scannable result set with minimum typing.

**Primary elements:** keyword/ADAM field, saved-search shortcut, filter button with active-count indicator, total results/value, result cards and sort control.

**Common filters shown first:** act type, publication date, deadline, CPV/category, geography and value.

**Advanced filters:** authority, contract type, procedure type, source, status, full-text query and published-table query.

**Result card:** act type, source, title, authority, publication date, deadline, value, cancellation/modification state and match explanation when a query produced one.

**States:** first use, loading/skeleton, results, no results, network error, access-gated result and end of results.

### 7.3 Act detail

**Purpose:** support a quick screening decision while preserving access to the record and source evidence.

**Top section:** type/source, title, authority, value, publication date, deadline, cancellation/modification state, ADAM and actions to share/open the official record.

**Recommended order:**

1. Why it matched, when search context exists.
2. Key facts and deadlines.
3. Objects/items, CPVs and categories.
4. Authorities and contractors.
5. Lifecycle, related acts and lots.
6. Published tables and attachments.
7. Full text and provenance.
8. Stored AI summary when that later phase is enabled.

Long sections are collapsed by default. Source facts remain visually distinct from generated summaries or team annotations.

### 7.4 Saved searches

**Purpose:** make repeat discovery and alert management understandable in one place.

Each row shows the name, a human-readable filter summary, ownership (`own` or shared portal profile), email status and push status. Customers may rename/delete only their own searches; they may subscribe to published portal profiles without editing them.

Creating a search starts from the currently active non-empty filter set. An unfiltered “match everything” saved search is rejected, preserving the current behavior.

### 7.5 Alert settings

**Purpose:** allow the customer to configure notification value without needing to understand delivery infrastructure.

Recommended controls per saved search:

- Email: off/on, existing format, schedule, language, result cap and deadline lead days.
- Push: off/on, new matches, deadline reminders, immediate or daily summary, quiet hours and language.
- Material updates are shown as a later-phase option until change detection is specified and verified.

Default recommendation: push remains off until the customer enables it. When enabled, the safe default is a daily summary; immediate new-match delivery requires an explicit choice.

### 7.6 Notification inbox

**Purpose:** provide an auditable, recoverable history independent of the device notification tray.

Each item shows event type, saved-search name, tender title/ADAM, time and read state. Opening an item marks it read and navigates to the target act while preserving the saved-search context that explains the match.

The inbox supports unread/all views and “mark all read.” Deleting the inbox history is deferred until a retention policy is approved.

### 7.7 Authority and contractor detail

**Purpose:** answer “who is this entity and what has it recently done?” without reproducing every desktop table.

MVP content: identity/contact data, merge/canonical-name indication, total acts, contract value, top CPV areas, top buyers for contractors and a recent-act list. Full directories and dense comparative analytics are deferred.

### 7.8 Account and security

**Purpose:** let customers control access, language, privacy and device notification registrations.

MVP content: username/email, role, product and expiry, language, password change, 2FA management, registered-device list, personal-data export, account deletion and logout.

## 8. Push-notification product model

### 8.1 Notification types

| Event | MVP | Trigger | User control |
|---|---:|---|---|
| New matching act | Yes | An ingested act newly matches an enabled saved search | Per search; immediate or daily summary |
| Deadline reminder | Yes | A matching active notice reaches a selected lead-day mark | Per search; selectable lead days |
| Daily push summary | Yes | One or more eligible events accumulated during the customer's summary window | Per search or global default |
| Material update/cancellation | Later | A watched/matching act changes in a defined material field | Per search/watch; requires change taxonomy |
| Watched-act reminder | Later | A customer explicitly watches an act | Per act |
| Authority/contractor follow | Later | A new act is associated with a followed entity | Per entity |

### 8.2 Notification controls

- Notifications require operating-system permission and a server-side enabled preference.
- Settings are per saved search, with a global “pause all push” control.
- Push is off by default. When the customer enables it, the suggested delivery mode is a daily summary rather than immediate alerts.
- The daily-summary default is **08:30 in the customer's timezone**. The customer can select another supported delivery time.
- Quiet hours default to **22:00–08:00 in the customer's timezone** and follow daylight-saving changes. `Europe/Athens` is used until the customer chooses another timezone.
- Events generated during quiet hours are deferred. New matches are consolidated into the next summary; deadline reminders are released after quiet hours. These product notifications do not bypass operating-system Focus or Do Not Disturb modes.
- The default daily cap is **six push deliveries per customer per local calendar day**: at most five individual opportunity/deadline alerts plus one grouped overflow summary. Deadline reminders take priority over new-match alerts. Additional events remain available in the inbox and roll into the next scheduled summary.
- The cap applies per customer, not per device, so delivery to two registered devices does not consume it twice. The customer can choose a lower or higher supported cap, or summary-only delivery; the MVP maximum is 10 pushes per day.
- Deadline reminders reuse the existing lead-day concept.
- Duplicate event delivery is prevented across worker retries and multiple devices.
- Lapsed customers keep preferences and history, but delivery follows the same entitlement rule as email.
- The customer can remove a registered device from the account screen.
- New-match and summary notifications use passive/low-priority delivery. Deadline reminders use normal/active delivery. None are marked critical, high-priority or time-sensitive by default.

### 8.3 Retention defaults

- User-visible notification events remain in the in-app inbox for **90 days** from creation, whether read or unread, then are deleted automatically.
- Customers can delete inbox items earlier. Deleting an inbox event does not delete the underlying public procurement act.
- Per-device delivery and provider-response records are retained for **30 days after reaching a terminal state**, which is sufficient for delivery troubleshooting without keeping identifiable operational history indefinitely.
- Longer-term reporting uses aggregated, non-user-identifying metrics rather than retained notification payloads.
- Account deletion removes notification events, device registrations and delivery records according to the account-deletion workflow rather than waiting for scheduled retention.

### 8.4 Lock-screen privacy

Tender data is public, but the fact that a specific customer follows a subject is account data. The default lock-screen payload should therefore contain a short tender title/event label and an opaque target identifier, not the customer's full saved-search criteria. Full context is fetched after authentication.

### 8.5 Reliability model

Push is best-effort. The product's source of truth is the server-side notification event and inbox.

Required behavior:

- create one idempotent notification event per user, trigger and target;
- fan it out to all active devices for that user;
- record provider acceptance, receipt/error and invalid-token responses;
- retry transient failures with bounded backoff;
- disable tokens reported as no longer registered;
- preserve the inbox item even when device delivery fails;
- do not claim that a push was seen merely because the provider accepted it.

### 8.6 Basis for the defaults

- Apple recommends explicit permission, avoiding duplicate notifications and reserving time-sensitive interruption for information requiring immediate attention: [Apple Human Interface Guidelines — Managing notifications](https://developer.apple.com/design/human-interface-guidelines/managing-notifications) and [Notifications](https://developer.apple.com/design/human-interface-guidelines/notifications/).
- Android recommends opt-in, grouping multiple notifications and low importance for new subscribed content, while normal/default importance suits reminders: [Android Developers — Notifications](https://developer.android.com/design/ui/mobile/guides/home-screen/notifications).
- The 90-day inbox and 30-day delivery-log periods are product defaults, not statutory periods. They apply the GDPR storage-limitation principle that identifiable data should be kept no longer than necessary for its purpose: [Regulation (EU) 2016/679, Article 5](https://eur-lex.europa.eu/eli/reg/2016/679/).

## 9. Proposed backend boundaries

### 9.1 Channel-neutral matching

Extract the reusable parts of digest evaluation so they answer:

> For this user and saved-search profile, which acts or deadlines became eligible in this evaluation window?

Email rendering, push rendering and inbox persistence consume that result independently. This avoids duplicating search semantics and prevents email-specific fields from becoming the push domain model.

### 9.2 Conceptual data additions

Names are provisional; the responsibilities are the decision.

| Responsibility | Suggested table |
|---|---|
| One installed/logged-in app instance | `proc.mobile_device` |
| Hashed rotating refresh credential per device | `proc.mobile_refresh_token` |
| Push preferences attached to a search profile | `proc.push_subscription` |
| Durable user-visible event/inbox item | `proc.notification_event` |
| Attempt/receipt per event and device | `proc.notification_delivery` |
| One explicitly watched act | `proc.watched_act` (later) |

`mobile_device` should hold user, installation identifier, platform, provider token, app version, locale, enabled state, last-seen time and revocation/invalid-token state. Raw refresh credentials are never stored.

### 9.3 Worker flow

1. Ingestion commits acts and their `ingested_at`/update metadata.
2. A scheduled/durable worker evaluates due push subscriptions.
3. Matching results create idempotent `notification_event` rows.
4. Delivery rows are created for each active device.
5. The provider adapter sends accepted rows.
6. Receipts update delivery state and invalidate dead tokens.
7. The mobile inbox reads events, not provider logs.

The web process must not be the sole owner of this work. Deployments, restarts or multiple web instances must not lose or duplicate notifications.

## 10. Proposed `/api/v1` surface

This is a resource contract, not a commitment to exact URL spelling.

### Authentication and account

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/verify`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/login-link/request`
- `POST /api/v1/auth/login-link/consume`
- `GET /api/v1/me`
- `POST /api/v1/me/password`
- `GET|POST|DELETE /api/v1/me/mfa`
- `POST /api/v1/me/export`
- `DELETE /api/v1/me`

### Discovery

- `GET /api/v1/acts`
- `GET /api/v1/acts/{adam}`
- `GET /api/v1/acts/{adam}/matches`
- `GET /api/v1/acts/{adam}/attachments/{id}`
- `GET /api/v1/authorities/{org_id}`
- `GET /api/v1/contractors/{vat}`
- `GET /api/v1/lookups`
- `GET /api/v1/cpv/suggest`
- `GET /api/v1/nuts/suggest`

### Saved searches and delivery preferences

- `GET|POST /api/v1/search-profiles`
- `GET|PATCH|DELETE /api/v1/search-profiles/{id}`
- `GET|PUT|DELETE /api/v1/search-profiles/{id}/email-subscription`
- `GET|PUT|DELETE /api/v1/search-profiles/{id}/push-subscription`

### Devices and notifications

- `GET|POST /api/v1/devices`
- `PATCH|DELETE /api/v1/devices/{id}`
- `GET /api/v1/notifications`
- `PATCH /api/v1/notifications/{id}`
- `POST /api/v1/notifications/mark-all-read`

### Contract rules

- All responses use stable codes plus localized labels where the label already exists server-side.
- The server re-checks ownership, role, entitlement and `session_version`; the client never supplies or infers authorization.
- List endpoints use deterministic cursor pagination for mobile scrolling. Page-number compatibility may remain on the web routes.
- Mutation endpoints accept JSON and idempotency keys where retry could duplicate state.
- Errors use one documented shape with a stable error code and localized/display-safe message.
- API schemas are published as OpenAPI and used to generate or validate the mobile client types.

## 11. Mobile authentication recommendation

Do not reproduce the browser cookie in the native client.

Recommended model:

- short-lived signed access token;
- opaque, rotating refresh token stored hashed server-side;
- one refresh-token family per registered mobile device;
- refresh token stored only in iOS Keychain / Android Keystore-backed secure storage;
- refresh rotation on every use and replay detection;
- password, role, status, product and 2FA changes invalidate mobile sessions through the existing `session_version` concept;
- logout revokes the current device session; “logout all devices” revokes every refresh-token family;
- login throttling and non-enumerating passwordless-link behavior remain server-owned;
- universal/app links are allowlisted and never accept an arbitrary post-login destination.

Exact access-token lifetime and refresh-token retention are security decisions for the implementation design review.

## 12. Mobile design principles

- **Screening first:** show the few facts needed to decide whether to continue reading.
- **Progressive disclosure:** dense record sections expand only when requested.
- **One primary action per screen:** search, save, enable, open or confirm.
- **Source clarity:** distinguish feed facts, published document text, stored AI summaries and internal annotations.
- **Greek-first fit:** validate long Greek titles and labels at 320–430 px widths; do not design from short English placeholders.
- **Accessible touch:** approximately 44 px targets, scalable text, screen-reader labels, contrast and no color-only status.
- **Interruption tolerant:** preserve filters, scroll position and partially edited preferences across ordinary navigation.
- **No deceptive delivery claims:** “sent to provider” is not “seen by customer.”
- **No forced permission prompt on launch:** ask for push permission after the customer enables a useful alert.

## 13. Operational and release requirements

### Required before internal beta

- API contract tests and mobile-generated client validation.
- Automated unit/component tests for search, auth and preferences.
- Device-level happy-path tests on at least one supported iOS and Android device class.
- Push tests for foreground, background, terminated app, denied permission, expired token and multiple devices.
- Crash reporting, API latency/error monitoring and notification delivery dashboards.
- Separate development, staging and production app identifiers and push credentials.
- A staging backend and non-production test accounts.

### Required before public release

- Final privacy policy and terms; the existing draft state is not acceptable as a release artifact.
- Approved retention policy for devices, refresh credentials, inbox events and delivery logs.
- Store listing, support contact, screenshots and release notes.
- Apple/Google signing ownership and recovery procedures.
- Account deletion and personal-data export verified on real devices.
- Security review of authentication, deep links, notification payloads and API authorization.
- Backup/restore and rollback plan for schema changes.
- Accessibility review in both languages.
- Beta feedback and a documented go/no-go decision.

## 14. Success measures

Targets should be set after an internal beta baseline. Measure at least:

- percentage of active mobile customers who complete a search;
- percentage who save at least one filtered search;
- push-permission opt-in after enabling an alert;
- notification open rate by event type and delivery mode;
- mute/disable rate by saved search;
- time from new act ingestion to notification-event creation;
- provider acceptance, receipt failure and invalid-token rates;
- time from notification tap to act content visible;
- API error/latency and crash-free session rate;
- customer-reported false-positive, duplicate and late-notification incidents;
- seven- and thirty-day retained use among beta customers.

No engagement metric should reward sending more notifications. Relevance, timeliness and low disable rates are the quality signals.

## 15. Recommended delivery slices

### Slice A — contract and security foundation

- Approve the mobile scope and information architecture.
- Define API response/error conventions and versioning.
- Define mobile authentication and revocation.
- Add contract tests before mobile screens depend on the API.

### Slice B — usable vertical path

- Sign in, including MFA.
- Search and filter.
- Results.
- Act detail and official-document link.
- Save the active filtered search.

This is the first end-to-end prototype suitable for customer observation.

### Slice C — notification value

- Device registration.
- Per-search push settings.
- New-match and deadline events.
- Durable outbox, provider delivery and receipt processing.
- Notification inbox and deep links.

### Slice D — account, entity context and beta hardening

- Authority/contractor summary screens.
- Account/security/privacy flows.
- Accessibility, performance and device matrix.
- Store-ready builds and controlled beta.

Analytics, watchlists, follows, offline caching and AI-summary display remain later slices unless beta evidence changes the priority.

## 16. Proposed MVP user stories

These stories become committed requirements only after the scope review.

### MOB-001 — Customer authentication

As a **Customer**, I want to sign in securely on my mobile device so that I can access the customer features associated with my account.

**Acceptance Criteria**

- A customer can sign in with their existing username and password.
- An account with 2FA enabled is not authenticated until a valid TOTP or recovery code is supplied.
- Invalid, inactive, throttled and forced-password-change states are shown without exposing whether another account exists.
- The authenticated session survives an ordinary app restart until it expires or is revoked.
- Logging out revokes the current mobile session and returns the customer to the sign-in screen.

**Gherkin**

```gherkin
Feature: Mobile customer authentication

  Scenario: Sign in to an account protected by 2FA
    Given a customer has a valid account with 2FA enabled
    When the customer submits valid credentials and a valid second factor
    Then the app opens the authenticated customer experience
```

### MOB-002 — Search and filter acts

As a **Customer**, I want to search and filter procurement acts on my phone so that I can find opportunities relevant to me.

**Acceptance Criteria**

- The mobile search supports the existing keyword/ADAM semantics.
- The customer can filter by act type, publication date, deadline, CPV/category, geography and value.
- The customer can reach the remaining supported filters from an advanced section.
- Applied filters remain visible and can be removed individually or cleared together.
- Results show a loading state, an actionable error state and a no-results state.
- Loading additional results does not duplicate or reorder items already shown without an explicit refresh.

**Gherkin**

```gherkin
Feature: Mobile procurement search

  Scenario: Find notices in a selected category and deadline range
    Given the customer is signed in
    When the customer applies a category and deadline range to a search
    Then the app lists only acts returned for those filters
```

### MOB-003 — Screen an act

As a **Customer**, I want to review the essential facts and source material for an act so that I can decide whether to investigate it further.

**Acceptance Criteria**

- The detail screen shows the act type, title, authority, value, dates, deadline, ADAM and status when available.
- The customer can see why the act matched when the act was opened from a search context.
- Detailed sections are available without forcing all long text and tables into the initial view.
- Feed facts, published source content and AI-generated content are visibly distinguishable.
- The customer can open the official source document and available individual attachments.
- Related acts and lifecycle information remain navigable.

**Gherkin**

```gherkin
Feature: Mobile act screening

  Scenario: Open a matching search result
    Given a customer has search results with match context
    When the customer opens one result
    Then the act detail shows its essential facts and the reason it matched
```

### MOB-004 — Save a filtered search

As a **Customer**, I want to save my active search so that I can run the same criteria again and configure alerts for it.

**Acceptance Criteria**

- A signed-in customer can save a search only when at least one supported filter is active.
- The saved search stores the server-recognized filter set and the name supplied by the customer.
- The customer can open any saved search available to them.
- The customer can rename or delete only a search they own.
- Deleting an owned search removes its associated customer-controlled delivery preferences after confirmation.

**Gherkin**

```gherkin
Feature: Mobile saved searches

  Scenario: Save the current filtered search
    Given a customer has an active search containing at least one filter
    When the customer gives the search a name and saves it
    Then the search appears in Saved with the same effective filters
```

### MOB-005 — Configure push alerts

As a **Customer**, I want to control push alerts for each saved search so that I receive useful updates without unnecessary interruptions.

**Acceptance Criteria**

- Push remains disabled until the customer explicitly enables it for a saved search.
- The customer can choose new-match notifications and deadline reminders.
- The customer can choose immediate new-match delivery or a daily summary.
- The customer can select supported deadline lead days and quiet hours.
- The operating-system permission prompt is requested only after the customer chooses to enable push.
- A lapsed customer retains the saved settings but does not receive delivery while not entitled.

**Gherkin**

```gherkin
Feature: Per-search push preferences

  Scenario: Enable a daily summary for a saved search
    Given a customer has a saved search and push is disabled for it
    When the customer enables push and selects daily summary
    Then the server stores an active daily push preference for that search
```

### MOB-006 — Receive and open a relevant notification

As a **Customer**, I want a notification to open the exact relevant act so that I can understand and act on it quickly.

**Acceptance Criteria**

- An eligible event creates one inbox item even if the customer has multiple devices.
- Each active device may receive the event without creating duplicate inbox items.
- The lock-screen payload does not expose the customer's full search criteria.
- Tapping an authenticated notification opens the target act with its match context.
- Tapping while signed out returns to the target after successful authentication.
- A provider failure does not remove the corresponding inbox item.

**Gherkin**

```gherkin
Feature: Notification deep link

  Scenario: Open a new-match notification
    Given an authenticated customer receives a notification for a matching act
    When the customer taps the notification
    Then the app opens that act and shows why it matched
```

### MOB-007 — Review notification history

As a **Customer**, I want to review my notification history so that I can recover alerts I dismissed or did not receive on the device.

**Acceptance Criteria**

- The inbox shows notification type, target, saved-search context, creation time and read state.
- The customer can switch between all and unread notifications.
- Opening an item marks it read and navigates to its available target.
- The customer can mark all visible notifications as read.
- One customer cannot read or mutate another customer's notification events.

**Gherkin**

```gherkin
Feature: Notification inbox

  Scenario: Recover a dismissed alert
    Given a customer has an unread notification event in the inbox
    When the customer opens the event
    Then the event is marked read and its target content is displayed
```

### MOB-008 — Manage account and devices

As a **Customer**, I want to manage my account security and registered mobile devices so that I remain in control of access and notifications.

**Acceptance Criteria**

- The account screen shows the current account identity, product and expiry information available from the server.
- The customer can change their password and manage 2FA through authenticated flows.
- The customer can see and revoke registered mobile devices.
- The customer can request their personal-data export.
- The customer can delete their account only after explicit confirmation and re-authentication.
- Revoking a device prevents its refresh credential and push token from being used again.

**Gherkin**

```gherkin
Feature: Mobile account and device control

  Scenario: Revoke an old mobile device
    Given a customer is viewing two registered devices
    When the customer revokes the old device
    Then that device can no longer refresh its session or receive push notifications
```

## 17. Technical enablers

These are necessary implementation work but are not customer stories:

- API module and OpenAPI contract for `/api/v1`.
- Mobile access/refresh session service with revocation and replay detection.
- Channel-neutral saved-search matcher.
- Notification event, outbox, delivery and receipt-processing service.
- Expo push adapter behind a provider-neutral interface.
- Mobile repository, environment configuration and generated API types.
- Mobile unit, component and end-to-end test harness.
- Development/staging/production build profiles and signing process.
- Mobile/backend observability and alerting.
- Privacy, retention and operational runbooks.

## 18. Approved product decisions

These decisions were approved by the product owner on 2026-09-14.

| Decision | Approved baseline | Why it matters |
|---|---|---|
| Platforms | iOS and Android together through Expo | Avoid two separate product/code tracks |
| App name | Keep “KHMDHS Explorer” as a working name only | Store identity and trademark check remain open |
| Languages | Greek and English in MVP | Matches the current customer experience |
| Repository | Separate `KHMDHS-Mobile` repo | Independent app-store lifecycle and cleaner CI |
| Registration | Keep existing self-service registration | Avoid web-only account creation unless intentionally changed |
| Push default | Off; daily summary suggested when enabled | Explicit consent and lower notification fatigue |
| Daily summary time | 08:30 customer-local time | Delivers a workday briefing after quiet hours |
| Immediate push | Explicit per-search choice | Broad searches can generate high volume |
| Daily push cap | Six total: up to five individual alerts plus one overflow summary; configurable up to 10 | Protects attention while preserving an inbox record |
| Quiet hours | 22:00–08:00 customer-local time | Fits a professional rather than emergency use case |
| Deadline defaults | Reuse current 7-day and 1-day concept | Maintains cross-channel consistency |
| Material-update alerts | Defer until a field-change taxonomy exists | “Changed” must be meaningful and testable |
| Analytics | Later | Dense desktop presentation is not core mobile value |
| AI summaries | Later, display only | Requires an API contract and careful source distinction |
| Full offline mode | Exclude from MVP | Corpus/documents are too large and freshness matters |
| Notification retention | Inbox 90 days; terminal delivery records 30 days | Balances recovery/troubleshooting with storage limitation |
| Beta group and success targets | Decide before Slice B completes | Needed for evidence-based go/no-go |

## 19. Stage-1 approval record and release gates

The product owner approved the following Stage-1 gates on 2026-09-14:

- **Scope gate:** MVP/later/web-only disposition in §5.
- **Navigation gate:** four-tab information architecture in §6.
- **Screen gate:** screening-first layouts and content priority in §7.
- **Notification gate:** event types, consent model, retention, daily cap, quiet hours and entitlement behavior in §8.
- **Architecture gate:** separate mobile repo, `/api/v1`, mobile token model and durable worker boundary.

The following operational release gate remains open and must close before a public beta: assign owners for privacy/terms, store accounts, signing, support, security review and the beta go/no-go decision.

## 20. Missing information

- Confirm the public product/app name and whether the existing visual identity should be carried over unchanged.
- Confirm which countries/store regions are in scope for the first release.
- Define the beta customer group and who will conduct customer-observation sessions.
- Assign ownership for Apple/Google developer accounts, legal text, support contact and production incident response.
