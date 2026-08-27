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
| Passwordless sign-in links | `LOGIN_LINKS_ENABLED=1` | default on; needs a working `EMAIL_BACKEND` to be useful. `LOGIN_LINK_TTL_SECONDS=900` sets how long a link lives |

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
2. `/admin/crm/<uid>` → the customer's card → **Result email alerts** → pick the
   profile → Save. The customer needs an email address AND an active grant:
   only testers and subscribers are ever mailed, and the panel says so in a
   banner when they are not. `/admin/digests` no longer has this form — it holds
   the schedules, a read-only overview, and the history.
3. **Preview** renders the exact email without sending. A brand-new subscription
   has nothing new yet, so use `?days=30` on the preview URL to see real content.
4. **Test send** mails it to you and leaves the customer's position untouched.
   It works even for a lapsed customer, because the message goes to you.
5. The sent message's **See all results** button opens `/digests/<token>` — the
   acts that email contained, not a fresh search. The same link appears as
   "what was sent" in `/admin/digests?tab=runs` and on the customer's card.
   Signed out it bounces through the login; as another customer it is a 403.
6. Or from a terminal, without the app running:

```bash
DATABASE_URL="postgresql://postgres:pw@127.0.0.1:5433/procurement" \
DIGEST_DRY_RUN=1 python3 cron_digests.py     # what would be sent, sends nothing
```

Drop `DIGEST_DRY_RUN` to actually send. Run `DIGEST_SCHEDULER=1` on the web app
**or** `cron_digests.py` on a timer — never both, or the two race each other.

---

## 7b. Trying a passwordless sign-in link

The recipe's `EMAIL_BACKEND=file` is all you need — the link lands in `./outbox`
as an `.eml` you can open in any mail client, or read with `grep`.

1. Give an account an email address (`/admin/crm/<uid>` → Details, or
   `/register`). Any active account works; no grant or subscription is needed —
   signing in and having access are separate things.
2. `/login` → **Στείλτε μου σύνδεσμο σύνδεσης με email** → enter that address.
   The page says "check your email" for ANY address you type: it is the same
   answer for an address with no account, on purpose.
3. Read the link out of the newest message:

```bash
grep -ho "http[^ ]*/login/link/[A-Za-z0-9_-]*" outbox/*.eml | tail -1
```

4. Open it. You get a confirmation page with a **Σύνδεση** button — the link
   deliberately does not sign you in on load, because mail scanners fetch URLs
   and would spend the token before you clicked. Press the button to sign in.
5. Open the same URL again: "Ο σύνδεσμος δεν ισχύει". One use, 15 minutes.
6. Turn 2FA on for the account and repeat — the link now lands on `/login/mfa`
   and you still need the authenticator code. That is the property the feature
   is only safe with; `tests/test_login_link.py` pins it.

`APP_BASE_URL` is what the mailed link points at, so keep it matching the port
you launched on or the link will open the wrong server.

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
