# Telephony / CTI — doc index

The CRM's telephony feature (WebRTC softphone, click-to-call, caller-ID
screen-pop) — an open-source stand-in for NFON *Cloudya CRM Connect / NCTI
Premium*. Code lives in `app/telephony.py`, `app/static/telephony.js`,
`app/templates/_softphone.html`, and the `telephony/` Asterisk stack.

| Doc | What it covers |
|---|---|
| [TELEPHONY_RUNBOOK.md](TELEPHONY_RUNBOOK.md) | How the feature works + the **local dev / demo** setup (Asterisk in Docker, env, demo script). Start here. |
| [TELEPHONY_PROD_DEPLOYMENT.md](TELEPHONY_PROD_DEPLOYMENT.md) | Turning the prototype into a running **prod** feature — the 6-phase plan (VPS Asterisk, TLS/Tailscale, DB, Render env). Scope: internal-only, ≤5 agents, one VPS. |
| [TELEPHONY_PROD_PHASE4_CREDENTIALS.md](TELEPHONY_PROD_PHASE4_CREDENTIALS.md) | Deep-dive on the one **code change** prod needs first: short-lived SIP credentials (Option A: auth-realtime) vs the encrypt-at-rest interim (Option B). |
| [TELEPHONY_CLOUDYA_BRANCH_SKETCH.md](TELEPHONY_CLOUDYA_BRANCH_SKETCH.md) | The **alternative**: a keep/delete/add map for a branch that swaps the self-hosted Asterisk stack for the commercial NFON Cloudya path. |

**Status (2026-08-17):** the app code is merged to `main` and deployed on Render
but **inert** — `TELEPHONY_ENABLED` is unset in prod and there is no Asterisk
host wired up. It runs only in local dev today.
