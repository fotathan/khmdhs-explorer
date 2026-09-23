# KHMDHS mobile app — Stage 3 implementation record

**Status:** Slices A through K are implemented and verified locally through
2026-09-15 against the dedicated throwaway test database. Slices L through O are
implemented and pass contract, static and native-bundle checks; their database
integration tests still require an available throwaway PostgreSQL database.
Activation in any target environment still requires the migration, secret and
feature flag.

## Implemented boundary

- `/api/v1` JSON router with a stable bilingual error envelope, request IDs,
  explicit bearer security and no-store authentication responses.
- Password login using the existing account hash and shared database throttle.
- One-use, five-minute TOTP/recovery challenges that create no session before a
  valid second factor.
- Device-scoped native sessions with 15-minute opaque access tokens, rotating
  30-day-idle refresh tokens and a fixed 90-day session maximum.
- Keyed token hashes at rest, refresh reuse detection, installation binding,
  logout, account-state checks and `session_version` invalidation.
- `/api/v1/me` identity, entitlement and security state.
- Localized `/api/v1/lookups` with ETag revalidation; identity/account-state
  access remains available when a paid entitlement lapses.
- Entitlement-gated `/api/v1/acts/search` using the web app's canonical filter
  builder, strict typed input, bounded pages, explicit safe DTOs and signed,
  snapshot-bound continuation cursors.
- Entitlement-gated `/api/v1/acts/{adam}` with grouped customer-facing data,
  optional signed match context, explicit field allowlisting and HTTPS-only
  official links. Raw source JSON, full text, internal edits and admin controls
  are not serialized.
- Shared core act read for the native DTO and the current server-rendered act
  page. The page's new tab/accordion presentation remains unchanged.
- Merge-aware, entitlement-gated authority and contractor summaries with
  headline totals, recent acts, leading categories, contractor buyers and safe
  contact fields.
- Mobile saved-search list/create/rename/delete over the existing profile
  model: owned searches plus published portal searches, structured filters,
  the shared 25-search cap, lapsed-customer retention, owner-only writes and
  `404` anti-enumeration behavior.
- Idempotent saved-search creation backed by the API idempotency ledger; retries
  return the original resource and conflicting key reuse is rejected.
- Global notification preferences with the approved defaults: Europe/Athens,
  22:00–08:00 quiet hours, 08:30 daily summary and six notifications per day.
- Authenticated device listing/registration/revocation with push-permission
  state, Expo provider metadata, AES-256-GCM token encryption and keyed hashes.
  Raw push tokens are never returned, logged or stored.
- Per-search push settings for owned and published portal searches, independent
  evaluation cursors, safe activation from the current time, deadline lead
  days, lapsed-entitlement retention and explicit delivery-suspension reasons.
- Customer notification inbox with signed one-hour keyset cursors, unread
  counts, read/unread, read-all and soft deletion. Inbox retention is 90 days;
  terminal provider-delivery records are retained for 30 days.
- Durable notification evaluation for new saved-search matches and approaching
  deadlines, with per-customer deduplication across overlapping searches,
  moved-deadline re-arming and inbox-first event storage.
- Customer-local quiet hours and daily-summary windows, a six-push default cap,
  at most five individual alerts, deadline priority and one overflow summary.
  One event fans out to all eligible devices without consuming the cap twice.
- Leased delivery claims, bounded retry/backoff, separate Expo ticket and
  receipt states, stale-delivery expiry and automatic token removal when Expo
  reports `DeviceNotRegistered`.
- A standalone `notification_worker.py` process. It evaluates and queues events
  when run, but outbound provider calls require the separate
  `PUSH_DELIVERY_ENABLED=1` switch and are off by default.
- Isolated `KHMDHS-Mobile` Expo SDK 57 project with generated API models,
  Greek/English localization, KHMDHS mobile design tokens, password/MFA screens
  and authenticated Search, Saved, Notifications and Account tabs.
- Native access tokens remain memory-only. The rotating refresh token and
  installation identifier use device-only SecureStore storage; concurrent
  refreshes collapse into one rotation and token expiry gets one safe retry.
- Development, preview and production EAS profiles and mobile CI checks contain
  no production URL, signing credential or provider secret.
- Live native search with localized act-type and status filters, result sorting,
  snapshot-safe cursor paging, concise mobile cards and entitlement-aware error
  handling.
- Collapsible advanced search for publication and deadline ranges, value range,
  source, contract type, procedure type and region, with local range validation,
  removable applied-filter chips and one-tap clear-all.
- Searchable full-directory authority and category selectors, editable CPV and
  document/table-text criteria, and complete status selection. Reopening and
  rerunning a saved profile preserves every supported filter.
- Native authority and contractor profiles expose the already customer-safe
  merge-aware summaries: contact actions, activity/value totals, categories,
  contractor buyer context and recent acts. Results, favorites and act parties
  link into those profiles.
- Customer-safe native act detail with screening-first facts, collapsed reading
  groups, match reasons and HTTPS-only official-document links.
- Native act/document sharing through the operating-system share sheet, plus
  grouped previous/following lifecycle acts that open as native detail pages.
- Account-scoped act favorites with idempotent add/remove, signed cursor paging,
  direct result/detail controls and a Favorites view inside the Saved tab.
  Favorites do not implicitly enable email or push delivery.
- Locale-aware calendar-date and timestamp rendering throughout result cards,
  act detail and inbox surfaces, with explicit deadline urgency indicators.
- Saved-search creation with a unique idempotency key per user action, plus a
  Saved tab for owned and published profiles, channel-state visibility and
  one-tap search reruns.
- A localhost-only static preview server that preserves Expo client routes and
  proxies `/api/v1` to the local FastAPI process. It is explicitly not a
  production server and changes no backend CORS policy.
- Live notification inbox with unread badge/filtering, cursor paging,
  reversible read state, read-all, soft removal and act-target navigation.
- Mobile global-preference UI for pause, quiet hours, summary time and daily
  cap, plus per-saved-search controls for delivery mode, new matches, deadlines
  and reminder lead days.
- Expo SDK 57 notification permission and push-token acquisition flow. It
  requires a signed native build and a real EAS project ID; browser preview and
  unconfigured builds state that boundary and do not fabricate a token.
- Saved-search rename and confirmed deletion, non-current device revocation,
  foreground inbox refresh and refresh-on-receipt behavior.
- Re-authenticated native password changes and TOTP management: five-minute,
  hashed one-use enrollment identifiers, authenticator handoff/manual secret,
  one-time recovery-code display, remaining-code status and protected disable.
  Security changes preserve the acting phone session and immediately revoke all
  other native sessions.
- Mobile email-alert settings over the existing `digest_subscription` model,
  independent from push: on/off, list/statistics/deadline formats,
  administrator-defined active schedules, Greek/English content, result cap,
  empty-result delivery and deadline lead days. Published portal searches are
  subscribable but remain read-only, while inaccessible profile IDs return
  `404`; lapsed entitlement keeps settings but marks delivery suspended.
- Owner-only native result-email history over the existing `digest_run` and
  `digest_run_item` records. Each run keeps its send-time filter snapshot,
  distinguishes rows included in the message from additional recorded matches,
  and exposes signed, scoped pagination without re-running the live search.
- Feature flag `MOBILE_API_ENABLED`, off by default; production additionally
  refuses service without `MOBILE_TOKEN_HASH_KEY`.
- Privacy-safe authentication outcome telemetry without usernames or tokens.
- Idempotent daily cleanup command with the approved 30/90-day retention rules.

## Deliberately not included

Passwordless login, personal-data export/account deletion and signed-device
push validation remain in later Stage 3 slices. Production provider activation,
always-on worker hosting and monitoring also remain separate approval gates.
Browser cookie authentication is unchanged. No Expo request is made unless both
the worker is running and `PUSH_DELIVERY_ENABLED=1` is explicitly set.

## Activation order

1. Apply `migrations/20260914120000_mobile_api_auth_foundation.sql`, then
   `migrations/20260914150000_mobile_notification_foundation.sql`, then
   `migrations/20260915143000_mobile_account_security.sql`, then
   `migrations/20260915170000_user_act_favorites.sql`, in an isolated environment.
2. Set new independent secrets `MOBILE_TOKEN_HASH_KEY` and
   `MOBILE_PUSH_TOKEN_KEY` (URL-safe base64 for exactly 32 random bytes), then enable
   `MOBILE_API_ENABLED=1` only there.
3. Run the database-backed authentication suite and a migration status check.
4. Exercise login, MFA, refresh race/replay, logout and forced revocation.
5. Verify lookup revalidation, web/mobile filter parity, snapshot pagination,
   lapsed-access behavior and the safe act-detail allowlist.
6. Verify merged entity totals, saved-search visibility/ownership, idempotent
   creation, cap enforcement and delete cascades.
7. Verify device token encryption/revocation, notification defaults, push
   suspension reasons, inbox ownership/pagination and retention cleanup.
8. Run `PUSH_WORKER_ONCE=1 PUSH_DELIVERY_ENABLED=0 python3 notification_worker.py`
   to evaluate safely without contacting Expo; inspect the inbox and durable
   delivery rows.
9. Schedule `python3 cleanup_mobile_auth.py` daily.

## Mobile bootstrap verification

- Pinned OpenAPI copy matches the backend contract and generated TypeScript
  models compile.
- TypeScript and ESLint checks pass.
- Expo export produces iOS, Android and static web bundles.
- The static web preview renders the responsive sign-in screen. It is a visual
  development aid, not a replacement for iOS/Android device testing.
- A local same-origin smoke test verifies demo login, paginated search, safe act
  detail and saved-search create/delete through the preview proxy.
- Notification API smoke testing verifies inbox reads, a reversible read-state
  update, preference roundtrip and per-search alert retrieval. Both native
  platform bundles include the notification module without compile errors.
- Expo Go on a physical iPhone has authenticated to the isolated demo database
  and exercised notification bootstrap, saved searches, search and act detail.
  A reversible saved-search create/rename/delete test also passes.
- The account-security API passed password/MFA lifecycle and cross-device
  revocation tests against a dedicated throwaway PostgreSQL database. Its
  screen and read-only MFA status endpoint are loaded in the physical-iPhone
  Expo session; destructive credential changes were deliberately not run on
  the shared demo user.
- Email-alert ownership, persistence, schedule validation, lapsed-entitlement
  and Saved-tab badge behavior passed against a dedicated throwaway database.
  The physical-iPhone demo reads one local-only default schedule and a
  non-deliverable `.invalid` account address; no email backend was activated.
- Result-email history passes API-contract, Python compilation, mobile
  type/lint and owner/cursor integration coverage. The database-backed cases
  are present but were skipped in this run because no throwaway PostgreSQL test
  database was configured.
- Favorites pass the committed API-contract checks, Python compilation/style,
  mobile type/lint checks and iOS/Android bundle export. The database-backed
  ownership/idempotency test is present but was skipped because this workstation
  did not have a dedicated PostgreSQL test database available for this run.
- Physical iOS and Android runtime validation remains required before Slice F
  meets its full device-level definition of done.

No production flag, migration, key, schedule, provider delivery, always-on
service or deployment has been changed by this implementation.
