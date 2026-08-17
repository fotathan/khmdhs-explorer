# Phase 4 Spec — SIP credential hardening

Companion to `TELEPHONY_PROD_DEPLOYMENT.md` §Phase 4. The one code change that
must land before telephony is enabled on a public prod deployment.

## The problem (confirmed in code)

`proc.sip_extension.sip_secret` is a **durable plaintext** SIP password. It is:
- stored in the clear in the DB (migration `20260817100404`, `sip_secret text NOT NULL`), and
- returned to the browser on every `/telephony/config` call
  ([app/telephony.py:552](app/telephony.py)), where JsSIP registers with it
  ([app/static/telephony.js:91](app/static/telephony.js), `password: c.sip_password`).

So a leaked config response, an XSS, or a DB dump yields a **permanent** SIP
credential. On an internal-only PBX that means an attacker can register as the
agent and place internal calls; the day a SIP trunk is added (§6) the same
credential becomes toll-fraud exposure.

## The hard constraint (why this needs Asterisk, not just app code)

A WebRTC softphone authenticates REGISTER with **SIP digest**, which requires
the browser to hold a secret Asterisk can verify. You cannot hide the secret
from the browser — it must present one. Therefore the only real mitigation is to
make that secret **ephemeral and rotatable**, and rotation only works if Asterisk
reads the current secret from somewhere the app can update. With our static
`pjsip.conf` (§1.3, ≤5 agents) Asterisk never re-reads a rotated password. So
true short-lived credentials require moving **just the PJSIP `auth` object** to
realtime (a DB the app writes). Endpoints and AORs stay static in `pjsip.conf`.

This is the fork below. **Option A** is the real fix; **Option B** is a lighter
interim if you want to pilot before wiring Asterisk to a DB.

---

## Option A — Ephemeral secrets via PJSIP auth-realtime (recommended)

Endpoints/AORs stay in `pjsip.conf`; the `auth` object for each agent is served
from a Postgres `ps_auths` row the app rotates. `/config` mints a short-lived
secret, writes it to `ps_auths`, and returns it with a TTL; the browser
refreshes before it expires. A leaked secret is useless after ~1h and is
revocable instantly.

### A.1 Schema (new migration via `migrate.py`)

```sql
-- App-owned bookkeeping: current ephemeral secret + when it expires, one per agent.
CREATE TABLE proc.sip_credential (
    user_id    bigint PRIMARY KEY REFERENCES proc.app_user(id) ON DELETE CASCADE,
    auth_id    text NOT NULL,          -- matches the pjsip.conf endpoint's auth= name
    secret     text NOT NULL,          -- current ephemeral password (rotated)
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Asterisk PJSIP realtime auth table (canonical column subset Asterisk reads).
-- Asterisk connects here read-only via res_config_pgsql/ODBC; the app writes it.
CREATE TABLE proc.ps_auths (
    id         text PRIMARY KEY,       -- = sip_credential.auth_id
    auth_type  text NOT NULL DEFAULT 'userpass',
    username   text NOT NULL,          -- = sip_extension.sip_user (stable)
    password   text NOT NULL,          -- = the ephemeral secret
    realm      text
);
```
- Drop the durable column: `ALTER TABLE proc.sip_extension DROP COLUMN sip_secret;`
  (`sip_user`, `extension`, `display_name` stay).
- Follow the repo migration convention (memory: *migration-tracking* —
  `migrate.py` + `migrations/manifest.txt`, not hand-applied psql).

### A.2 App code (`app/telephony.py`)

New helper:
```python
CRED_TTL_S = int(os.environ.get("SIP_CRED_TTL", "3600"))
_REFRESH_SKEW_S = 300   # re-use only if this much life remains

def mint_or_reuse_credential(cur, user_id, sip_user, auth_id):
    """Return (secret, expires_at). Reuse the current secret if it still has
    > _REFRESH_SKEW_S life; else rotate: new secret -> ps_auths + sip_credential."""
    cur.execute("SELECT secret, expires_at FROM proc.sip_credential WHERE user_id=%s",
                (user_id,))
    row = cur.fetchone()
    if row and (row["expires_at"] - _now()).total_seconds() > _REFRESH_SKEW_S:
        return row["secret"], row["expires_at"]
    secret = secrets.token_urlsafe(18)
    exp = _now() + timedelta(seconds=CRED_TTL_S)
    cur.execute("""INSERT INTO proc.ps_auths (id, auth_type, username, password, realm)
                   VALUES (%s,'userpass',%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET username=EXCLUDED.username,
                        password=EXCLUDED.password, realm=EXCLUDED.realm""",
                (auth_id, sip_user, secret, SIP_DOMAIN))
    cur.execute("""INSERT INTO proc.sip_credential (user_id, auth_id, secret, expires_at, updated_at)
                   VALUES (%s,%s,%s,%s, now())
                   ON CONFLICT (user_id) DO UPDATE SET auth_id=EXCLUDED.auth_id,
                        secret=EXCLUDED.secret, expires_at=EXCLUDED.expires_at, updated_at=now()""",
                (user_id, auth_id, secret, exp))
    return secret, exp

def revoke_credential(cur, user_id):
    """Immediately invalidate an agent's softphone (deactivation / logout-all)."""
    cur.execute("SELECT auth_id FROM proc.sip_credential WHERE user_id=%s", (user_id,))
    r = cur.fetchone()
    if r:
        cur.execute("DELETE FROM proc.ps_auths WHERE id=%s", (r["auth_id"],))
        cur.execute("DELETE FROM proc.sip_credential WHERE user_id=%s", (user_id,))
```

`/telephony/config` ([telephony.py:534](app/telephony.py)) change — the write
means this route must use a **writable** cursor (today it reads only); mint
inside the `cursor()` block and return the TTL instead of the durable secret:
```python
secret, exp = mint_or_reuse_credential(c, u["id"], ext["sip_user"], _auth_id(ext))
...
"sip_password": secret,
"sip_password_ttl": int((exp - _now()).total_seconds()),   # NEW — drives client refresh
```
`_auth_id(ext)` = the `auth=` name in that agent's `pjsip.conf` block (convention:
`ext["sip_user"] + "-auth"`).

Revocation hook: call `revoke_credential` where `session_version` is bumped
(the existing "log out everywhere" / deactivate path in `app/auth.py`), so
disabling an agent also kills their softphone.

### A.3 Client (`app/static/telephony.js`)

- On successful config fetch, schedule a refresh at `sip_password_ttl * 0.8`:
  refetch `/config`; if `sip_password` changed, re-register with the new secret.
- Re-registration is **out-of-dialog** — an active call survives it. To be safe,
  if a call is in progress at refresh time, defer the UA restart until `endCall()`
  (add a `pendingCfg` flag; apply it in the existing `endCall`,
  [telephony.js:218](app/static/telephony.js)).
- On `registrationFailed` ([telephony.js:99](app/static/telephony.js)), refetch
  `/config` once and restart the UA when idle (covers a secret that expired while
  the tab slept). JsSIP credentials are set at construction, so "re-register with
  new secret" = `ua.stop()` then `startUA(newCfg)` when idle.
- Multi-tab: all tabs of one agent share the same current secret (mint_or_reuse
  reuses within TTL), so `max_contacts=5` multi-tab still works; at rotation each
  tab refreshes independently.

### A.4 Asterisk (on the VPS, §1–2)

- Add DB connectivity to the Asterisk image: `res_config_pgsql` (or unixODBC +
  `res_odbc` + `res_config_odbc`) pointed at the same Postgres, over the private
  tunnel / SSL.
- `extconfig.conf`: `ps_auths => pgsql,<db>,ps_auths` (or odbc).
- `sorcery.conf`: `[res_pjsip]` → `auth=realtime,ps_auths` (endpoints/aors stay
  the default `config` source = `pjsip.conf`).
- Disable auth object caching so a rotated password takes effect on the next
  REGISTER (realtime with no `qualify`/cache on the auth type).
- `pjsip.conf`: each static `[agentN]` endpoint keeps `auth=agentN-auth`,
  `aors=agentN`; the `[agentN-auth]` block is **removed** (now realtime).

### A.5 Cleanup / ops

- Rows are one-per-agent and reused, so they don't accumulate; no GC job needed.
- Optional belt-and-braces: extend the daily cron (`cron_catchup.py`) to
  `DELETE FROM proc.ps_auths a USING proc.sip_credential c WHERE a.id=c.auth_id
  AND c.expires_at < now() - interval '1 day'` — only matters if an agent is
  deactivated without the revoke hook firing.

### A.6 Tests (repo rule: ship pytest with the feature — memory *tests-for-every-feature*)

- `mint_or_reuse_credential`: mints when absent; reuses when >skew life; rotates
  when expired; writes the expected `ps_auths` row.
- `revoke_credential`: deletes both rows.
- `/telephony/config`: returns an ephemeral secret + `sip_password_ttl`, **never**
  a value stored durably; 401 unauth; `provisioned:false` when no extension.
- Run against the test-DB snapshot; regenerate the snapshot for the new tables
  (memory *test-suite-and-ci*).

### A.7 Effort

~1–1.5 days app + tests; ~0.5–1 day Asterisk realtime wiring + end-to-end verify.

---

## Option B — Encrypt-at-rest interim (pilot shortcut, NOT short-lived)

If you want to pilot before wiring Asterisk↔DB: keep the static `pjsip.conf`
secret, but stop storing it plaintext in *our* DB.
- Encrypt `sip_extension.sip_secret` at rest (`cryptography` Fernet, key from a
  new `SIP_SECRET_KEY` env var, following the *local-secrets* convention);
  decrypt in `get_extension` to serve.
- No Asterisk change, no realtime, no client change.
- **Honest limitation:** the secret is still durable and still handed to the
  browser — this only shrinks DB-dump exposure. It is a stopgap, not the Phase 4
  goal. Track Option A as the real follow-up.
- Effort: ~half a day + a test.

---

## The one decision for you

**A or B?** A is the genuine fix and the right thing before any public/trunked
deployment, at the cost of running Asterisk against Postgres. B lets you pilot
internal-only sooner with far less infra but leaves the durable-secret risk
mostly intact. My recommendation: **A**, unless you want an internal-only pilot
in front of trusted users first — then B now, A before §6 (the trunk).
