# Production telephony config templates

Fill-in-the-blank configs for standing up the prod PBX described in
`TELEPHONY_PROD_DEPLOYMENT.md` (internal-only, ≤5 agents, one VPS) with the
short-lived SIP credentials from `TELEPHONY_PROD_PHASE4_CREDENTIALS.md`. These
are **templates** — every `<PLACEHOLDER>` must be replaced. Nothing here is wired
into the app or the local-dev demo (`telephony/`), which stay untouched.

## Files

| File | Role | Deployment phase |
|---|---|---|
| `docker-compose.yml` | Asterisk + Caddy on the VPS (host networking) | 1–2 |
| `Caddyfile` | TLS termination → `wss://<domain>/ws` → `ws://127.0.0.1:8088/ws` | 2 |
| `asterisk/pjsip.conf` | WebRTC endpoints, public-IP media, per-agent blocks, **realtime auth** | 1, 4 |
| `asterisk/manager.conf` | AMI, real secret, tailnet-only `permit=` | 1–2 |
| `asterisk/http.conf` | SIP-over-ws bound to 127.0.0.1 (Caddy fronts it) | 2 |
| `asterisk/rtp.conf` | RTP range 10000–10200, ICE via public IP | 1 |
| `asterisk/extensions.conf` | internal dialplan + MixMonitor recording | 1 |
| `asterisk/sorcery.conf` | PJSIP `auth=realtime,ps_auths` (endpoints/AORs stay static) | 4 |
| `asterisk/extconfig.conf` | realtime family `ps_auths => pgsql,<db>,ps_auths` | 4 |
| `asterisk/res_pgsql.conf` | Asterisk's read-only Postgres connection | 4 |
| `.env.example` | every placeholder + the Render env, in one checklist | all |

## Order of operations

1. **VPS + DNS** — static IP, `pbx.<domain>` A-record, Docker installed, Tailscale
   installed **on the host**.
2. **Fill placeholders** using `.env.example` as the checklist (public IP, tailnet
   IP/CIDR, domain, AMI secret, DB creds). Add one `[agentNNNN]`/`[NNNN]` pair in
   `pjsip.conf` and one `exten` in `extensions.conf` per agent.
3. **DB (Phase 3–4)** — apply the migrations to prod
   (`sip_extension`+CTI `20260817100404`, ephemeral creds `20260818140000`),
   provision one `proc.sip_extension` row per agent, and create the read-only
   Asterisk role (see `.env.example`).
4. **Bring it up** — `docker compose -f telephony/prod/docker-compose.yml up -d`.
   Check `docker exec khmdhs-asterisk asterisk -rx "pjsip show endpoints"` and
   `... "pjsip show auths"` (auths should resolve from realtime).
5. **Render (Phase 5)** — set the web-service env from `.env.example` (incl.
   `SIP_AUTH_MODE=realtime`), redeploy. The AMI listener connects over Tailscale
   and the softphone appears for provisioned agents.

## Realtime DB driver (the one image gotcha)

`sorcery.conf` + `extconfig.conf` need Asterisk built **with Postgres realtime**.
The demo `andrius/asterisk:20-alpine` image may not include `res_config_pgsql` —
verify with `asterisk -rx "module show like res_config_pgsql"`. Two options:

- **pgsql:** use an Asterisk image that ships `res_config_pgsql`, and set
  `res_pgsql.conf`.
- **ODBC (portable fallback):** most images include `res_config_odbc`; install
  unixODBC + the Postgres ODBC driver in a small custom image, configure
  `odbc.ini`/`res_odbc.conf`, and change `extconfig.conf` to
  `ps_auths => odbc,<res_odbc-context>,ps_auths`.

**Schema note:** the tables live in `proc`, but Asterisk queries the unqualified
name. Set the Asterisk role's `search_path` to include `proc` (see
`extconfig.conf`), or expose a `public.ps_auths` view.

## Security checklist (mirrors the deployment doc)

- [ ] AMI secret changed; `:5038` bound to the tailnet, `permit=` = tailnet CIDR only.
- [ ] `:8088` (plain ws) and `:5038` (AMI) never reachable on the public NIC.
- [ ] `wss://` via Caddy/Let's Encrypt; RTP UDP range firewalled; public IP in ICE.
- [ ] Realtime auth active (`SIP_AUTH_MODE=realtime`) — no durable secret to the browser.
- [ ] Static `1001pass`/`1002pass` demo passwords gone; per-agent endpoints only.
- [ ] Asterisk DB role is read-only (SELECT on `proc.ps_auths`).
