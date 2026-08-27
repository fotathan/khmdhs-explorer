# Running everything locally

Most features are **off by default** — the deployed app only turns on what it is
configured for. This is the recipe for a local run with *everything* switched on
at once, what each switch needs, and how to confirm it actually came up.

Nothing here sends email, calls a paid API without your key, or touches
production.

---

## 1. What has to be running first

```bash
open -a Docker                 # Docker Desktop, if it isn't already
docker ps                      # expect: khmdhs-pg, khmdhs-asterisk, khmdhs-whisper
```

| Container | Port | Needed for |
|---|---|---|
| `khmdhs-pg` | 5433 | everything — this is the database |
| `khmdhs-asterisk` | 5038, 8088 | the softphone / caller ID |
| `khmdhs-whisper` | 8000 | transcribing call recordings |

If a container is missing: `docker start khmdhs-pg` (same for the others).

Then make sure the database schema is current:

```bash
DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement" \
  python3 migrate.py status      # want: "0 pending · 0 drifted"
```

`up` instead of `status` applies anything pending.

---

## 2. Secrets

Keys live in `~/.khmdhs.env` (git-ignored, sourced automatically at launch).
It should contain:

```bash
ANTHROPIC_API_KEY=...      # OCR of scanned PDFs + call summaries
GEMI_API_KEY=...           # ΓΕΜΗ business-registry lookups
SECRET_KEY=...             # signs login cookies — see below
TELEPHONY_ENABLED=1        # softphone + caller ID
AMI_HOST=... AMI_PORT=... AMI_USER=... AMI_PASSWORD=...
SIP_WS_URL=... SIP_DOMAIN=...
```

`SECRET_KEY` is the one that is easy to miss: without it the app falls back to a
built-in key and logins are forgeable. Fine on a laptop, but generate a real one
once and forget about it:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" >> ~/.khmdhs.env
```

---

## 3. Launch with everything on

The preview launcher (`.claude/launch.json`, git-ignored because it holds the
local DB password) already sets all of this. To run it by hand instead:

```bash
set -a; . ~/.khmdhs.env; set +a
DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement" \
APP_BASE_URL="http://127.0.0.1:8012" \
REGISTRATION_MODE=open \
ATTACHMENTS_ENABLED=1 \
TABLES_ENABLED=1 \
ENABLE_DOCS=1 \
RATELIMIT_ENABLED=0 \
DIGEST_SCHEDULER=1 \
EMAIL_BACKEND=file EMAIL_FILE_DIR=./outbox \
EMAIL_FROM="KHMDHS Explorer <noreply@khmdhs.local>" \
TRANSCRIBE_BACKEND=openai \
TRANSCRIBE_BASE_URL=http://127.0.0.1:8000/v1 \
TRANSCRIBE_MODEL=Systran/faster-whisper-small \
TRANSCRIBE_LANGUAGE=el \
  ./khmdhs-env/bin/uvicorn app.main:app --port 8012
```

No `--reload` — it double-starts the background threads.

---

## 4. What each switch does

**On by default — you do not need to set these**

| Feature | Switch | Note |
|---|---|---|
| Table extraction (`/tables`) | `TABLES_ENABLED=1` | default on |
| Background jobs (admin backfills) | `RUN_INLINE_WORKER=1` | default on locally, off on Render (a separate worker container runs there) |
| Local OCR (Tesseract) | `LOCAL_OCR=1` | needs `tesseract` + the `ell` language pack; both are installed |
| Table relevance classifier | `TABLE_RELEVANCE=1` | default on |
| Rate limiting | `RATELIMIT_ENABLED=1` | the recipe turns it **off** so you don't throttle yourself while clicking around |

**Off by default — the recipe turns these on**

| Feature | Switch | Also needs |
|---|---|---|
| Act attachments (upload + search inside) | `ATTACHMENTS_ENABLED=1` | `proc.act_attachment` (already applied locally) |
| Self-service signup at `/register` | `REGISTRATION_MODE=open` | `invite` instead, with `REGISTRATION_INVITE_CODE`, to gate it |
| API docs at `/docs` | `ENABLE_DOCS=1` | — |
| Scheduled result emails | `DIGEST_SCHEDULER=1` | sweeps every 60s (`DIGEST_POLL_SECONDS`) |
| Email delivery | `EMAIL_BACKEND=file` | writes `.eml` files to `./outbox`; see below |
| Call transcription | `TRANSCRIBE_BASE_URL` | the `khmdhs-whisper` container |

**Key-gated — on as soon as the key is present**

| Feature | Key |
|---|---|
| OCR of scanned PDFs, Claude table extraction, call summaries | `ANTHROPIC_API_KEY` |
| ΓΕΜΗ enrichment (admin button on contractor/authority pages) | `GEMI_API_KEY` |
| Softphone + caller-ID screen-pop | `TELEPHONY_ENABLED=1` + `AMI_*` / `SIP_*` |

---

## 5. Email: the four modes

`EMAIL_BACKEND` decides where a digest actually goes. **Nothing can escape by
accident** — the default logs and discards.

| Value | Where the mail goes |
|---|---|
| `console` | printed to the terminal (the default) |
| `file` | one `.eml` per message in `EMAIL_FILE_DIR` — open them in Mail |
| `memory` | held in a list; used by the tests |
| `smtp` | a real mail server (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`) |

For a realistic local test without any real sending, run a fake inbox:

```bash
docker run -d --name khmdhs-mail -p 1025:1025 -p 8025:8025 axllent/mailpit
```

then launch with `EMAIL_BACKEND=smtp SMTP_HOST=127.0.0.1 SMTP_PORT=1025` and read
the mail at <http://localhost:8025>.

`EMAIL_REDIRECT_TO=you@example.com` is the safety catch: every message goes to
that address instead of the customer, with the intended recipient kept in an
`X-Original-To` header. Use it the first time you point anything at a real
server.

Deliverability (SPF/DKIM/DMARC, bounces, unsubscribe) is **not implemented** —
that work must land before any of this points at real customers.

---

## 6. Confirming it all came up

With the app running on 8012, signed in as an admin:

| Check | Where | Looks right when |
|---|---|---|
| Core | <http://127.0.0.1:8012/healthz> | `{"ok":1}` |
| Attachments | any act page | an "Attachments" panel is present |
| Tables tool | <http://127.0.0.1:8012/tables> | the page loads |
| OCR | `/tables`, upload a scanned PDF | an OCR button appears per file |
| ΓΕΜΗ | a contractor page | a "ΓΕΜΗ" enrichment button |
| Softphone | any page, bottom-right | a green dot on the softphone widget |
| Email alerts | <http://127.0.0.1:8012/admin/digests> | the top bar reads `file → ./outbox` |
| Background jobs | <http://127.0.0.1:8012/admin/collection> | a launched job leaves "queued" |
| API docs | <http://127.0.0.1:8012/docs> | Swagger loads |

Startup problems print to the terminal rather than crashing the app — a failed
telephony listener or digest scheduler logs one line and the rest still boots,
so read the first few lines of output if a feature seems missing.

---

## 7. Trying the digest emails end to end

1. `/search-profiles/manage` → create a portal profile with some filters.
2. `/admin/digests` → **Subscriptions** → pick a customer (they need an email
   address on their account) and that profile → Save.
3. **Preview** renders the exact email without sending. A brand-new subscription
   has nothing new yet, so use `?days=30` on the preview URL to see real content.
4. **Test send** mails it to you and leaves the customer's position untouched.
5. Or from a terminal, without the app running:

```bash
DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement" \
DIGEST_DRY_RUN=1 python3 cron_digests.py     # what would be sent, sends nothing
```

Drop `DIGEST_DRY_RUN` to actually send. Run `DIGEST_SCHEDULER=1` on the web app
**or** `cron_digests.py` on a timer — never both, or the two race each other.

---

## 8. Tests

```bash
TEST_DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/khmdhs_test" \
  ./khmdhs-env/bin/python -m pytest
```

`khmdhs_test` is a throwaway database on the same container; it is dropped and
rebuilt from `tests/proc_schema.sql` on every run, so never point this at
`procurement`. Without the variable the DB-backed tests skip and only the
pure-unit ones run.
