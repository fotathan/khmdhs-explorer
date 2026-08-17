# Telephony / CTI — Production Deployment Plan

Turns the local prototype (see `TELEPHONY_RUNBOOK.md`) into a running prod
feature. The app code is already deployed on Render but **inert**
(`TELEPHONY_ENABLED` unset, all endpoints default to `localhost`). This plan
covers what has to exist *outside* the app for it to work, and the two code
changes that shouldn't ship to prod without.

> Status: DRAFT. Scope decided: **internal-only, ≤5 agents, one VPS.** PSTN is
> deferred to a later phase (§6); with ≤5 agents, Asterisk endpoints are
> hand-maintained in `pjsip.conf` — no PJSIP realtime.

---

## Why this isn't just "flip a flag"

The web app runs on Render (a managed container platform). Asterisk cannot run
there: it needs a long-lived process with a **UDP RTP port range** (10000–10200)
and raw **AMI/SIP TCP ports**, none of which Render's web/worker/cron service
types expose. So prod telephony is really **two systems**:

```
  Browser (agent) ──wss:443──► Caddy/TLS ──ws──► Asterisk :8088   (SIP + WebRTC)
        │                                            │  UDP 10000-10200 (media, public)
        │                                            │
  Render web service ──AMI :5038 (private tunnel)────┘   screen-pop events
        │
        └── FastAPI app/telephony.py → Supabase (customer_profile, …)
```

- **Public, TLS:** the browser's `wss://` WebSocket to Asterisk, plus the RTP
  UDP range for audio.
- **Private, never public:** AMI (`:5038`). The runbook and `manager.conf` are
  emphatic about this — it's a powerful interface.

---

## Scope (decided)

- **Internal-only** for the first prod cut — agent-to-agent and click-to-call
  between extensions. This proves the screen-pop + call-logging mechanics in
  prod with no SIP trunk, no DID, no per-number cost. PSTN is deferred to §6.
- **≤5 agents** — so Asterisk endpoints are **hand-maintained in `pjsip.conf`**
  (one `[NNNN]` block per agent). No PJSIP realtime / ARA needed; skip that
  complexity until agent count grows.
- **Host: one small VPS** (Hetzner CX22 / DigitalOcean, ~€4–12/mo) running the
  *existing* `telephony/docker-compose.yml`, hardened. Least new tech; reuses
  everything already in `telephony/`.

---

## Phase 1 — Stand up the Asterisk host

1. Provision a VPS with a **static public IP** and a DNS name, e.g.
   `pbx.<yourdomain>`.
2. Install Docker + Compose; copy the `telephony/` directory up.
3. **Harden the sample configs** (they are prototype-grade):
   - `manager.conf`: change `secret=khmdhs-ami-secret` to a real secret; tighten
     `permit=` to only the tunnel subnet (see Phase 2) and drop the broad
     `172.16/192.168` LAN ranges.
   - `pjsip.conf`: the hardcoded `1001`/`1002` endpoints and `*pass` passwords
     are demo fixtures. Replace them with **one `[NNNN]` block per agent** (real
     extension + strong password), matching the `proc.sip_extension` rows from
     Phase 3. With ≤5 agents this is a short, hand-maintained file — keep the
     two demo blocks as a template and delete them once real ones exist.
   - Add the public-IP media settings to the `transport-ws` / a new UDP
     transport: `external_media_address` and `external_signaling_address` =
     the VPS public IP, so ICE candidates are routable (the `rtp.conf`
     `ice_host_candidates` macOS hack is *not* needed on a real public host).
4. **Firewall (host-level, e.g. ufw/cloud SG):**
   - `443/tcp` open to the world (Caddy → wss).
   - `10000-10200/udp` open to the world (RTP media).
   - `5038/tcp` (AMI) and `8088/tcp` (plain ws) **closed to the public** — only
     reachable over the tunnel / localhost.

## Phase 2 — TLS for the browser, private tunnel for AMI

**Browser side (public wss):** put **Caddy** in front of Asterisk's `:8088`.
Caddy auto-provisions Let's Encrypt for `pbx.<yourdomain>` and reverse-proxies
`wss://pbx.<yourdomain>/ws` → `ws://127.0.0.1:8088/ws`. This is the secure
context browsers require off `localhost` (`TELEPHONY_RUNBOOK.md` §Security).

**AMI side (private):** the Render web container must reach AMI `:5038` without
that port being public. **Recommendation: Tailscale.** Install it on the VPS,
add a Tailscale sidecar/entrypoint to the Render container (Render supports this
via the Dockerfile), bind AMI to the tailnet interface, and set
`AMI_HOST=<vps-tailscale-ip>`. Then `manager.conf permit=` is just the tailnet
CIDR.
- Simpler-but-weaker alternative: IP-allowlist Render's **static egress IPs**
  (stable on paid Render plans) in the host firewall + `manager.conf`. Avoids a
  tunnel but sends AMI creds/events over the public internet in cleartext —
  acceptable only if you also enable AMI TLS (`tlsenable` in `manager.conf`).
  Prefer Tailscale.

## Phase 3 — Prod database

1. Apply the migration to the **prod** Supabase DB (creates `proc.sip_extension`
   + CTI columns on `proc.customer_call`):
   ```
   DATABASE_URL="<prod-owner-url>" python3 migrate.py up
   # migration: 20260817100404_sip_extension_and_call_cti_columns.sql
   ```
   (`phonenumbers==8.13.55` is already in `requirements.txt`, so the Render image
   ships it — no dep change.)
2. **Provision one `sip_extension` row per agent** (extension, sip_user,
   sip_secret, display_name, user_id). Keep these in sync with the per-agent
   `[NNNN]` blocks in `pjsip.conf` (Phase 1.3) — the app reads the row, Asterisk
   reads the conf, and the two must agree on extension + password.

## Phase 4 — Code hardening before prod (do NOT skip)

> Full spec: **`TELEPHONY_PROD_PHASE4_CREDENTIALS.md`** (schema, app/JS/Asterisk
> changes, tests, effort, and the A-vs-B decision).

The runbook flags this and I confirmed it in `app/telephony.py`:

- **Short-lived SIP credentials.** Today `sip_secret` is stored plaintext in
  `proc.sip_extension` and returned to the browser on every `/telephony/config`
  call ([app/telephony.py:552](app/telephony.py)). For prod, issue a
  short-lived credential instead of persisting/returning a durable secret —
  either rotating per-agent secrets or a time-boxed auth. This is a real code
  change (app + Asterisk auth), not config. **Scope it before enabling prod.**
- Everything else (CSP/mic Permissions-Policy widening, WS-token auth) is
  already conditional on `TELEPHONY_ENABLED` and needs no change.

## Phase 5 — Wire up Render and enable

Add the telephony env to the **web service** in the Render dashboard (not
`render.yaml` — keep the AMI secret out of git, like `DATABASE_URL`/`SECRET_KEY`):

| Env | Value |
|---|---|
| `TELEPHONY_ENABLED` | `1` |
| `AMI_HOST` | `<vps tailscale IP>` |
| `AMI_PORT` | `5038` |
| `AMI_USER` | `khmdhs` (or your chosen user) |
| `AMI_PASSWORD` | *the real AMI secret from Phase 1.3* |
| `SIP_WS_URL` | `wss://pbx.<yourdomain>/ws` |
| `SIP_DOMAIN` | `pbx.<yourdomain>` |
| `TELEPHONY_REGION` | `GR` |

Redeploy the web service (env is read once at startup). On boot, the AMI
listener connects over the tunnel; `/telephony/config` starts returning
`enabled: true, provisioned: true` for agents with a `sip_extension` row, and
the softphone widget appears.

**Note — always-on:** the web service is on Render's `free` plan (sleeps after
inactivity). A sleeping web dyno drops the AMI listener and the screen-pop
WebSocket, so **screen-pop won't fire while asleep**. Upgrade the web service to
`starter` ($7/mo) for telephony to be reliable.

## Phase 6 — SIP trunk for real PSTN (deferred — later phase)

Asterisk-config-only per the runbook: add the trunk provider's endpoint/auth to
`pjsip.conf`, route inbound DIDs into the `internal` context to the right
agent's endpoint (the same `DialBegin` → screen-pop fires), and add an outbound
route for agent-dialed PSTN numbers. Buy a DID from the trunk provider. No app
change.

---

## Security checklist (prototype → prod)

- [ ] AMI secret changed from `khmdhs-ami-secret`; AMI never publicly reachable.
- [ ] AMI reached only over Tailscale (or TLS + egress-IP allowlist).
- [ ] `wss://` via Caddy/Let's Encrypt; no plain `ws://` exposed publicly.
- [ ] Short-lived SIP credentials (Phase 4) — no durable plaintext secret to the
      browser.
- [ ] Per-agent endpoints replace the demo `1001/1002` fixtures.
- [ ] `pjsip.conf` passwords are not the demo `1001pass`/`1002pass`.
- [ ] RTP UDP range firewalled to only what's needed; public IP in ICE config.

## Rough cost (internal-only)

- VPS: ~€4–12/mo. • Render web `starter`: $7/mo. • Domain/DNS: existing.
- Tailscale: free tier fine. • PSTN trunk + DID (Phase 6 only): provider-dependent.

## Open questions for you

1. Do you have a spare domain/subdomain for `pbx.<...>`, and a preferred VPS
   provider (Hetzner / DigitalOcean / other)?
2. Who owns the short-lived-credential change (Phase 4) — want me to spec it as
   a proper task with the app + Asterisk auth changes broken out?
