# Cloudya branch — keep / delete / add sketch

A file-level map for a `feat/telephony-nfon-cloudya` branch that swaps the
self-hosted Asterisk/WebRTC stack (currently on `main`) for the commercial NFON
**Cloudya** path in `Cloudya CRM Integration.pdf`. **No code written** — this is
the plan only.

Key idea from that analysis: the two solutions share only the **CRM-side lookup
+ call-logging layer**. Everything that makes calls happen moves out of the web
app — into the Cloudya Desktop App, the CRM Connect browser plugin, and NFON
SaaS. So this branch *deletes more than it adds*.

Which files to ADD depends on which caller-ID option from the spec you pick:
- **Path B3** — Cloudya **CTI API** (programmatic, live lookup — closest to what
  you have today).
- **Path B4/B5** — NFON **PBX phonebook sync** (push CRM contacts to the PBX;
  the APIAPP-middleware model). Contradicts your current live-lookup design.

The sketch below marks items **[B3]**, **[B4/B5]**, or **[both]**.

---

## KEEP (reusable, backend-neutral)

| File | Keep what | Notes |
|---|---|---|
| `app/telephony.py` | `_digits`, `normalize_number`, `match_key`, `_MATCH_PRED`/`_match_clause`, `lookup_caller`, and the `/telephony/lookup` + `/telephony/log-call` routes | **Slim, don't keep whole** — see DELETE for the rest of this file. This is the only genuinely reusable code. |
| `migrations/20260817100404_*.sql` | The `customer_call` CTI columns (`external_number`, `started_at`, `ended_at`, `duration_s`) | Already applied; call-logging still uses them. `proc.sip_extension` in the same migration becomes **dormant** (SIP-cred table, Asterisk-only) — you don't un-apply it, it just goes unused. |
| `tests/test_telephony.py` | The `normalize_number` / `match_key` / `lookup_caller` tests | Delete the AMI/screen-pop/route tests (see DELETE). |
| `tests/proc_schema.sql` | As-is | Test-DB snapshot; regenerate only if you add tables. |
| `requirements.txt` | `phonenumbers==8.13.55` | Still used by the lookup layer. |

---

## DELETE (the whole self-hosted PBX + in-browser softphone)

| Path | What it is |
|---|---|
| `telephony/` (entire dir) | Asterisk `docker-compose.yml`, `asterisk/*.conf`, `seed_demo.sql` — the self-hosted PBX. Cloudya replaces it. |
| `app/static/telephony.js` | JsSIP softphone client. Cloudya dials via the desktop app, not the browser. |
| `app/static/telephony.css` | Softphone widget styles. |
| `app/static/vendor/jssip-3.11.1.min.js` | The WebRTC SIP library. Not needed. |
| `app/templates/_softphone.html` | The in-page softphone widget. |
| **Part of** `app/telephony.py` | `configure_secret`, `make_ws_token`, `read_ws_token`, `channel_extension`, `screenpop_from_event`, `route_recipients`, `_Hub`, `TelephonyService`, `init_service`, `_user_for_extension`, `_resolve_ws_user`, `get_extension`, `extensions_map`, `manager_of`, and the `/telephony/config` + `/telephony/ws` routes. All are AMI/WebRTC/SIP-credential machinery. |
| `TELEPHONY_RUNBOOK.md`, `TELEPHONY_PROD_DEPLOYMENT.md`, `TELEPHONY_PROD_PHASE4_CREDENTIALS.md` | Asterisk-specific docs. Replace with a Cloudya runbook (below). *(These stay on `main`; just not carried onto this branch.)* |

> `get_extension`/`manager_of`/`route_recipients` handle "which agent's browser
> gets the pop." Under Cloudya the pop is native to whoever's phone rings, so
> server-side routing is unnecessary — **unless** you use the CTI API to drive a
> *custom* in-app pop, in which case keep a slimmed agent→user map. **[B3 only, optional]**

---

## MODIFY

| File | Change |
|---|---|
| `app/main.py` | Remove the AMI lifespan start/stop ([main.py:1034-1046](app/main.py:1034)); drop the SIP-WebSocket `connect-src` + `microphone` CSP/Permissions-Policy widening ([main.py:1285-1324](app/main.py:1285)) — no in-browser media means the stricter default policy is fine again; slim the router mount + globals ([main.py:1642-1645](app/main.py:1642)). |
| `app/templates/base.html` / `beta_base.html` | Remove the `{% include "_softphone.html" %}` line (231 / 372). No in-page widget. |
| `app/templates/admin_crm_customer.html` | Swap the `data-call` / `data-customer-id` widget hooks (lines 30-32, 104-106) for plain `tel:` links (or bare numbers) — the CRM Connect plugin auto-detects/handles those. |
| `app/i18n_catalog.py` | Replace the `_TELEPHONY` string block ([:1987-2017](app/i18n_catalog.py:1987)) — softphone/"green dot"/"no app needed" copy no longer applies. |
| `app/templates/beta_help.html` | Update the telephony help section (memory rule: help page tracks user-facing features). |

---

## ADD

### Path B3 — Cloudya CTI API (recommended; live lookup, no phonebook sync)

| New file | Purpose |
|---|---|
| `app/telephony_cloudya.py` | A slim router: an endpoint the Cloudya **CTI API** calls (or a client that subscribes to CTI events) to resolve an incoming caller → reuses `lookup_caller`. Optionally pushes a custom in-app pop; otherwise the desktop app shows the native pop and this just logs. |
| env / config | `CLOUDYA_CTI_*` credentials + endpoint (per the CTI API manual linked in the spec). Follow the `~/.khmdhs.env` local-secrets convention. |
| `tests/test_telephony_cloudya.py` | Tests for the new endpoint + that it reuses the shared lookup. |
| `CLOUDYA_RUNBOOK.md` | Setup: desktop-app/plugin install, DIAL: handler, CTI API keys. |

### Path B4/B5 — NFON PBX phonebook sync (the APIAPP model)

| New file | Purpose |
|---|---|
| `nfon_phonebook_sync.py` (repo root) | Standalone CLI that exports CRM contacts (`customer_profile`, `customer_contact`, `economic_operator`) to the NFON PBX phonebook via the Service Portal API (or CSV/XLS). Shaped like `gemi_enrich.py`. |
| `app/nfon_client.py` | Shared Service Portal API client (parse/upsert), mirroring the `gemi_client.py` split so on-demand and offline paths stay identical. |
| `render.yaml` cron entry | A daily `type: cron` running the sync (matches APIAPP's "daily synchronization"). |
| migration | A small sync-state/watermark table if you track what was pushed. Via `migrate.py` + `migrations/manifest.txt`. |
| env / config | `NFON_PORTAL_API_*` keys + endpoints. |
| `tests/test_nfon_sync.py` | Sync/export tests. |
| `CLOUDYA_RUNBOOK.md` | Setup + the ⚠ from the spec: NFON provides keys but **does not support/maintain** the integration. |

---

## NOT in git (can't live on any branch — flag for planning)

- NFON **subscriptions/licenses**: Cloudya + CRM Connect / NCTI Premium; API credentials.
- **Per-user, per-OS installs**: Cloudya Desktop App + CRM Connect browser plugin, and registering the `DIAL:` protocol handler (Windows/macOS). IT rollout, not code.
- **APIAPP middleware** (B4/B5) if you go that route — a third-party (Biznes Innovation) product/contract.
- Platform limit: this path is **Windows/macOS desktop-bound**, versus today's "any OS, any browser, no install."

---

## Net shape

Delete the `telephony/` dir + 4 static/template files + ~60% of `telephony.py`;
keep the ~lookup/normalize/log core; add one small router **(B3)** *or* a
sync CLI + client + cron **(B4/B5)**. The branch is mostly subtraction — which is
the honest signal that Cloudya and the Asterisk build are **alternative
backends behind the same CRM lookup layer**, not two versions of one feature.
If you ever want both to coexist, the cleaner move is a `TELEPHONY_BACKEND`
switch on `main` rather than a long-lived divergent branch.
