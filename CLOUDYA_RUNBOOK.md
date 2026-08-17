# Cloudya (NFON) CTI Runbook

The **alternative** telephony backend to the self-hosted Asterisk/WebRTC build
(`TELEPHONY_RUNBOOK.md`). Instead of an in-browser softphone + Asterisk, calls
run through NFON's own **Cloudya Desktop App + CRM Connect** plugin, and this app
only serves caller-ID lookups. Implements **path B3** from the Cloudya spec
(`TELEPHONY_CLOUDYA_BRANCH_SKETCH.md`).

Branch: `feat/telephony-nfon-cloudya`. Coexists with the Asterisk path — both are
dormant no-ops unless their own env flag is set.

## What this app provides

Two **read-only, token-gated** HTTP endpoints (`app/telephony_cloudya.py`), both
called by CRM Connect on the agent's machine:

| Endpoint | Purpose |
|---|---|
| `GET /telephony/cloudya/lookup?number=…` | Resolve a caller number → JSON `{name, company, url, source, …}`. CRM Connect's "web/database lookup" data source displays the name. |
| `GET /telephony/cloudya/pop?number=…` | 302-redirect the agent's browser to the matched CRM record (`/admin/crm/<id>`), or to `CLOUDYA_NOMATCH_URL`. CRM Connect's "open URL on ring". |

Both reuse `telephony.lookup_caller()` — the **same** live CRM lookup the Asterisk
path uses (`customer_profile → customer_contact → economic_operator`). No
phonebook sync, no softphone, no AMI.

**Click-to-call is not served here.** It is entirely CRM Connect + the OS `DIAL:`
handler (see setup). Numbers on the page just need to be `tel:` links or bare
numbers the plugin can detect.

## Config (env)

| Env | Default | Meaning |
|---|---|---|
| `CLOUDYA_ENABLED` | *(off)* | Master switch. Off = endpoints 404, full no-op. |
| `CLOUDYA_LOOKUP_TOKEN` | — | Shared secret CRM Connect presents on every call. **Required** when enabled — the endpoints fail closed (503) without it, and never serve caller identity to an unauthenticated request. |
| `CLOUDYA_NOMATCH_URL` | `/admin/crm` | Where `pop` sends an unrecognised number. |

Set these in the Render dashboard (like `DATABASE_URL`), not in git. Generate the
token with `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`.

## Auth

The desktop client carries **no app session cookie**, so the shared token is the
only gate. Present it as the **`X-Cloudya-Token` header** (preferred — URLs get
logged) or, if CRM Connect can only template a URL, as `?token=…`. The check is
constant-time and fails closed when the secret is unset.

## One-time setup

### 1. NFON side (per agent, IT rollout — not code)
- Install the **Cloudya Desktop App** (Windows/macOS) and the **CRM Connect**
  browser plugin.
- Register **CRM Connect** as the OS handler for the `DIAL:` protocol (enables
  click-to-call from any page).
- NFON **Cloudya / CRM Connect (NCTI Premium)** subscription + login.

### 2. CRM Connect configuration (points it at this app)
- Add a **custom web/database lookup** data source with URL:
  `https://<app-host>/telephony/cloudya/lookup?number=%NUMBER%`
  and the token header/param. Map its response fields (`name`, `company`, `url`)
  to CRM Connect's display slots.
- Set **"on incoming call, open URL"** to:
  `https://<app-host>/telephony/cloudya/pop?number=%NUMBER%`
  (opens in the agent's browser, where they are logged in as admin — the pop then
  lands on the CRM record).
- `%NUMBER%` is CRM Connect's caller-number placeholder; confirm the exact token
  name in your CRM Connect build.

### 3. App side
- Set `CLOUDYA_ENABLED=1` and `CLOUDYA_LOOKUP_TOKEN=…` in the environment and
  redeploy (env is read once at startup).
- No migration and no schema change: the lookup reuses existing CRM tables, and
  call logging (if added later) reuses the `customer_call` CTI columns already on
  prod.

## Verify

```bash
# 404 until enabled; 401 without the token; 200 with it.
curl -i "https://<app-host>/telephony/cloudya/lookup?number=2101234567" \
     -H "X-Cloudya-Token: <token>"
```
A known customer number returns `{"matched": true, "name": "...", "url": "/admin/crm/<id>"}`.

## Security notes

- Endpoints are **read-only** and reveal only caller identity (name/company +
  record URL) to a token holder. No write surface.
- Keep the token secret; rotate by changing the env var and the CRM Connect
  config. It is revocable instantly (unset the env → endpoints stop matching).
- Prefer the **header** over the query token so the secret isn't written to
  access logs.

## Not yet built (later increment)

NFON's deeper **programmatic Cloudya CTI API** — subscribing to CTI *events*
(ringing/answered/ended) server-side rather than being polled by CRM Connect —
needs the NFON API contract from the manual:
🔗 https://www.nfon.com/en/service/documentation/manuals/web-applications/cti-api/
That contract is **not reproduced here**; the URL-lookup/pop mechanism above is
the standard CRM Connect custom-integration path and stands on its own. Adding
event-driven call logging (writing `proc.customer_call`) is the natural next step.
