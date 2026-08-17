# Telephony / CTI Runbook

An open-source stand-in for the NFON *Cloudya CRM Connect / NCTI Premium* flow.
It gives the CRM three capabilities without any desktop app, browser plugin, or
OS protocol-handler registration:

| Capability | How it works here |
|---|---|
| **Click-to-call** | A WebRTC softphone (JsSIP) is embedded in every page. `tel:` links and `[data-call]` elements dial through it; `window.khmdhsPhone.call(number)` is the programmatic hook. |
| **Screen-pop (caller ID)** | A background AMI listener watches Asterisk call events; on an inbound call it looks the number up in the CRM and pushes the match to the agent's browser over a WebSocket. |
| **Caller lookup** | Numbers are resolved **live** against the CRM tables on each call — no phonebook to pre-sync into the PBX. |

Everything is gated on `TELEPHONY_ENABLED`. Unset (the prod default) it is a
complete no-op: the router reports disabled, the AMI listener never starts, and
the softphone widget stays hidden.

---

## Architecture

```
 Browser (agent, logged in)
   ├── JsSIP softphone ──ws──► Asterisk :8088/ws   (SIP signaling + WebRTC media)
   └── /telephony/ws  ◄──────  FastAPI              (screen-pop push)
                                   ▲
 Asterisk ──AMI :5038 events──────►│  app/telephony.py
                                   └── lookup_caller() → proc.customer_profile,
                                       customer_contact, economic_operator
```

- **`app/telephony.py`** — router (`/telephony/config|lookup|log-call|ws`),
  the phone-number normaliser, `lookup_caller()`, the WebSocket hub, and the
  reconnecting AMI listener (`TelephonyService`).
- **`app/static/telephony.js` + `templates/_softphone.html`** — the widget.
- **`telephony/`** — Asterisk `docker-compose.yml` + configs + `seed_demo.sql`.
- **Migration `20260817100404`** — `proc.sip_extension` (agent → SIP endpoint)
  and CTI columns on `proc.customer_call`.

---

## One-time setup

### 1. Apply the migration and install the dep

```bash
DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement" python3 migrate.py up
./khmdhs-env/bin/pip install -r requirements.txt   # adds phonenumbers
```

### 2. Start Asterisk

```bash
docker compose -f telephony/docker-compose.yml up
```

This brings up one Asterisk with two WebRTC endpoints (`1001`, `1002`), the SIP
WebSocket on `:8088`, and AMI on `:5038`. No SIP trunk — the demo is
extension-to-extension, which is enough to exercise click-to-call and the
screen-pop.

### 3. Map two accounts to the extensions

Edit the two usernames at the top of `telephony/seed_demo.sql`, then:

```bash
psql "$DATABASE_URL" -f telephony/seed_demo.sql
```

This maps `caller_username → ext 1001` and `agent_username → ext 1002`, and sets
the caller's `customer_profile.phone` to `2101234567` — the number `pjsip.conf`
uses as ext 1001's caller ID — so the pop resolves to a named record.

### 4. Run the app with telephony on

```bash
export TELEPHONY_ENABLED=1
export AMI_HOST=127.0.0.1 AMI_PORT=5038 AMI_USER=khmdhs AMI_PASSWORD=khmdhs-ami-secret
export SIP_WS_URL=ws://localhost:8088/ws SIP_DOMAIN=localhost
./khmdhs-env/bin/uvicorn app.main:app --port 8012
```

(Or add these to `~/.khmdhs.env`, which `launch.json` already sources.)

---

## Demo script

1. **Browser A** — log in as the **agent** (ext 1002). The softphone dot goes
   green (*Online*) when it registers.
2. **Browser B** (or a private window) — log in as the **caller** (ext 1001).
3. In Browser B, open the softphone, dial **1002**, Call.
4. **Browser A rings** and pops **“Demo Customer · Demo Co Ltd”** with an *Open
   record* link to `/admin/crm/<id>`. Answer to connect; hang up to end.
5. A held incoming call is logged to `proc.customer_call` for that customer
   (direction `incoming`, with duration).
6. **Click-to-call**: anywhere a number renders as `tel:` or carries
   `data-call`, clicking it opens the softphone and dials. To make a customer's
   phone clickable in a template:
   ```html
   <a href="tel:{{ number }}" data-name="{{ full_name }}"
      data-customer-id="{{ user_id }}">{{ number }}</a>
   ```
   `data-customer-id` lets the outbound call be logged against that customer.

**Echo test** (single tab, no second party): dial **600** to hear yourself —
confirms mic/speakers.

> **Browser note:** placing a call needs microphone permission. **Chrome**
> prompts and works out of the box. **Arc** often suppresses the mic prompt —
> the call then dies silently before any SIP INVITE is sent (Asterisk shows
> "0 calls processed"). In Arc, enable the mic manually via the site-settings
> shield/lock in the address bar, or just use Chrome for the demo.

---

## Two-way audio on macOS

Signaling — registration, ringing, **screen-pop**, and call logging (the whole
CTI point) — works over Docker on any OS. Two-way **audio** through Docker on
macOS needs one extra step, because Asterisk advertises its container IP as the
RTP (ICE) candidate and the host browser can't route to it:

1. `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' khmdhs-asterisk`
2. Uncomment `[ice_host_candidates]` in `telephony/asterisk/rtp.conf`, set
   `<container-ip> => 127.0.0.1`, and restart the container.

On Linux, giving the container host networking avoids this entirely. If you just
want to demo the CRM integration, you can skip audio — the screen-pop fires on
call setup regardless.

---

## Configuration reference

| Env | Default | Meaning |
|---|---|---|
| `TELEPHONY_ENABLED` | *(off)* | Master switch. Off = full no-op. |
| `AMI_HOST` / `AMI_PORT` | `127.0.0.1` / `5038` | Asterisk Manager Interface. |
| `AMI_USER` / `AMI_PASSWORD` | — | AMI credentials (see `manager.conf`). |
| `SIP_WS_URL` | `ws://localhost:8088/ws` | Browser → Asterisk WebSocket. |
| `SIP_DOMAIN` | `localhost` | SIP realm/domain for URIs. |
| `TELEPHONY_REGION` | `GR` | Default region for number parsing. |
| `TELEPHONY_MATCH_ACTS` | `0` | Also scan `proc.act_contractor` on lookup (can be slow; no functional index). |

---

## Security notes (prototype → production)

- **SIP secrets** are stored in plaintext in `proc.sip_extension` and returned to
  the authenticated owner's browser (a WebRTC softphone must present them to
  REGISTER). Fine for a prototype; for production issue short-lived credentials
  instead of persisting the secret.
- **AMI** (`:5038`) is a powerful interface — keep it on the internal network,
  change the sample secret, and never expose it publicly.
- For a public deployment, terminate **TLS** in front of Asterisk (`wss://`) and
  point `SIP_WS_URL` at it; browsers require a secure context off `localhost`.
- Adding a real **SIP trunk** (to place/receive PSTN calls) is Asterisk config
  only — no application change. Inbound DIDs land in the `internal` context;
  route them to an agent's endpoint and the same screen-pop fires.
