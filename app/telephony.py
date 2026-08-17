"""
telephony.py — CTI (computer-telephony integration) for the CRM.

An open-source stand-in for the NFON *Cloudya CRM Connect / NCTI Premium* flow
(desktop app + browser plugin + DIAL: protocol handler + phonebook-sync
middleware). Here the same three capabilities are delivered with far less
moving parts:

  * **Click-to-call** — CRM pages call ``window.khmdhsPhone.call(number)`` and a
    WebRTC softphone (JsSIP) embedded in the page places the call through
    Asterisk. No desktop client, no protocol handler, any OS.
  * **Screen-pop (caller ID)** — a background AMI listener watches Asterisk call
    events; when a call rings an agent's extension it looks the caller's number
    up in the CRM and pushes the match to that agent's browser over a WebSocket.
  * **Live lookup instead of phonebook sync** — numbers are resolved against the
    live CRM tables on each call, so there is nothing to pre-sync into the PBX.

Everything is gated on ``TELEPHONY_ENABLED``. When unset (prod default) the
router still mounts but reports disabled, no AMI task starts, and the softphone
widget stays hidden — a complete no-op until configured. See
``TELEPHONY_RUNBOOK.md`` for the Asterisk side and env.

Config (env):
  TELEPHONY_ENABLED=1
  AMI_HOST=127.0.0.1  AMI_PORT=5038  AMI_USER=...  AMI_PASSWORD=...
  SIP_WS_URL=ws://localhost:8088/ws     — browser → Asterisk WebSocket transport
  SIP_DOMAIN=localhost                   — SIP realm/domain for URIs
  TELEPHONY_REGION=GR                    — default region for number parsing
  TELEPHONY_MATCH_ACTS=0                 — also scan proc.act_contractor (slow; off)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature

try:  # phone parsing is best-effort; a digit fallback keeps lookups working
    import phonenumbers
except ImportError:  # pragma: no cover - exercised only where the dep is absent
    phonenumbers = None

try:
    from app import auth as _auth
except ImportError:  # run with --app-dir=app
    import auth as _auth  # type: ignore

log = logging.getLogger("telephony")


# --------------------------------------------------------------------------- #
# Configuration (read once at import — the app reads env at start, per the
# local-secrets convention).
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


TELEPHONY_ENABLED = _env_bool("TELEPHONY_ENABLED")
AMI_HOST = os.environ.get("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.environ.get("AMI_PORT", "5038"))
AMI_USER = os.environ.get("AMI_USER", "")
AMI_PASSWORD = os.environ.get("AMI_PASSWORD", "")
SIP_WS_URL = os.environ.get("SIP_WS_URL", "ws://localhost:8088/ws")
SIP_DOMAIN = os.environ.get("SIP_DOMAIN", "localhost")
DEFAULT_REGION = os.environ.get("TELEPHONY_REGION", "GR")
MATCH_ACTS = _env_bool("TELEPHONY_MATCH_ACTS")

# A match key shorter than this is treated as "not enough to identify anyone"
# (avoids popping a record for a 3-digit internal code).
LOOKUP_MIN_DIGITS = 7
_KEY_LEN = 9  # compare the trailing 9 significant digits (Greek numbers are 10)

# WebSocket auth token. The screen-pop socket can't always rely on the session
# cookie being sent on the WS handshake (proxies/iframes withhold SameSite
# cookies from the upgrade request), so /telephony/config — which IS
# cookie-authenticated over HTTP — mints a short-lived signed token that the
# browser passes as ?token=… on the WS URL. Signed with SECRET_KEY when present,
# else a per-process key (sign + verify happen in the same process).
# The dev fallback mirrors main._SECRET_KEY's so tokens stay valid across
# restarts in local dev; main passes the real secret via init_service() so the
# two always match. A per-process random key would invalidate every previously
# issued token on restart (stale cached tokens -> 403).
_WS_SECRET = os.environ.get("SECRET_KEY") or "dev-insecure-key-change-in-prod"
_ws_signer = URLSafeTimedSerializer(_WS_SECRET, salt="telephony-ws")
WS_TOKEN_MAXAGE = 12 * 3600  # seconds


def configure_secret(secret: str) -> None:
    """Rebind the token signer to the app's session secret (called by main)."""
    global _ws_signer
    if secret:
        _ws_signer = URLSafeTimedSerializer(secret, salt="telephony-ws")


def make_ws_token(uid: int, ext: str) -> str:
    return _ws_signer.dumps({"uid": uid, "ext": ext})


def read_ws_token(token: str) -> dict | None:
    try:
        return _ws_signer.loads(token, max_age=WS_TOKEN_MAXAGE)
    except BadSignature:
        return None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Phone-number normalisation
# --------------------------------------------------------------------------- #
def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def normalize_number(raw: str | None, region: str = DEFAULT_REGION) -> dict:
    """Parse a raw phone string into a comparable shape.

    Returns ``{raw, e164, national, digits, key}`` where ``key`` is the trailing
    ``_KEY_LEN`` significant digits used for fuzzy matching against numbers that
    the DB stores in inconsistent formats (+30…, 0030…, national, spaced).
    ``phonenumbers`` is used when available; otherwise a pure-digit fallback.
    """
    raw = (raw or "").strip()
    d = _digits(raw)
    e164 = national = None
    if phonenumbers and raw:
        try:
            pn = phonenumbers.parse(raw, region)
            if phonenumbers.is_possible_number(pn):
                e164 = phonenumbers.format_number(
                    pn, phonenumbers.PhoneNumberFormat.E164)
                national = phonenumbers.format_number(
                    pn, phonenumbers.PhoneNumberFormat.NATIONAL)
                d = _digits(e164)
        except Exception:  # noqa: BLE001 — never let parsing break a lookup
            pass
    key_src = d
    # Drop a leading Greek country code so +30 210… and 210… share a key.
    if len(key_src) > 10 and key_src.startswith("30"):
        key_src = key_src[2:]
    key = key_src[-_KEY_LEN:] if len(key_src) >= _KEY_LEN else key_src
    return {"raw": raw, "e164": e164, "national": national, "digits": d, "key": key}


def match_key(raw: str | None, region: str = DEFAULT_REGION) -> str:
    """Just the fuzzy match key for a raw number (convenience for tests/SQL)."""
    return normalize_number(raw, region)["key"]


# --------------------------------------------------------------------------- #
# CRM lookup  (sync — call from a threadpool / run_in_executor in async paths)
# --------------------------------------------------------------------------- #
# Each entry: (label, table, name_expr, url_expr, [phone columns], has_user_id)
# The trailing-digits predicate is applied to each phone column.
_MATCH_PRED = ("right(regexp_replace(coalesce({col}, ''), '[^0-9]', '', 'g'), %(klen)s) = %(key)s "
               "AND length(regexp_replace(coalesce({col}, ''), '[^0-9]', '', 'g')) >= %(klen)s")


def _match_clause(columns: list[str]) -> str:
    return " OR ".join("(" + _MATCH_PRED.format(col=c) + ")" for c in columns)


def lookup_caller(cur, number: str | None, region: str = DEFAULT_REGION) -> dict | None:
    """Resolve a phone number to a CRM record. Returns the best single match as
    ``{source, name, subtitle, url, customer_user_id}`` or ``None``.

    Priority: the customer account (customer_profile) → its contacts
    (customer_contact) → the contractor database (economic_operator). Only the
    first two carry an app_user id, which is what lets a call be logged against
    a customer. proc.act_contractor is scanned only when TELEPHONY_MATCH_ACTS is
    set (it can be large and has no functional index here).
    """
    norm = normalize_number(number, region)
    key = norm["key"]
    if len(key) < LOOKUP_MIN_DIGITS:
        return None
    params = {"key": key, "klen": _KEY_LEN}

    # 1) The customer account itself.
    cur.execute(
        f"""SELECT p.user_id, p.full_name, p.company
              FROM proc.customer_profile p
             WHERE {_match_clause(['p.phone', 'p.mobile'])}
             LIMIT 1""", params)
    row = cur.fetchone()
    if row:
        return {"source": "customer", "name": row["full_name"] or row["company"] or "—",
                "subtitle": row["company"], "url": f"/admin/crm/{row['user_id']}",
                "customer_user_id": row["user_id"]}

    # 2) A contact person under a customer account.
    cur.execute(
        f"""SELECT c.user_id, c.first_name, c.last_name, c.job_title
              FROM proc.customer_contact c
             WHERE c.is_active AND ({_match_clause(['c.phone', 'c.mobile'])})
             LIMIT 1""", params)
    row = cur.fetchone()
    if row:
        name = " ".join(x for x in (row["first_name"], row["last_name"]) if x) or "—"
        return {"source": "contact", "name": name, "subtitle": row["job_title"],
                "url": f"/admin/crm/{row['user_id']}", "customer_user_id": row["user_id"]}

    # 3) The contractor database (no app_user — screen-pop only, no call log).
    cur.execute(
        f"""SELECT o.vat_number, o.name
              FROM proc.economic_operator o
             WHERE {_match_clause(['o.contact_phone'])}
             LIMIT 1""", params)
    row = cur.fetchone()
    if row:
        url = f"/contractor/{row['vat_number']}" if row["vat_number"] else None
        return {"source": "contractor", "name": row["name"] or "—",
                "subtitle": row["vat_number"], "url": url, "customer_user_id": None}

    if MATCH_ACTS:
        cur.execute(
            f"""SELECT ac.name, ac.vat_number
                  FROM proc.act_contractor ac
                 WHERE {_match_clause(['ac.phone'])}
                 LIMIT 1""", params)
        row = cur.fetchone()
        if row:
            url = f"/contractor/{row['vat_number']}" if row.get("vat_number") else None
            return {"source": "act_contractor", "name": row["name"] or "—",
                    "subtitle": row.get("vat_number"), "url": url,
                    "customer_user_id": None}
    return None


def get_extension(cur, user_id: int) -> dict | None:
    cur.execute(
        """SELECT extension, sip_user, sip_secret, display_name
             FROM proc.sip_extension
            WHERE user_id = %s AND is_active""", (user_id,))
    return cur.fetchone()


def extensions_map(cur) -> dict[str, int]:
    """{extension -> user_id} for every active SIP endpoint."""
    cur.execute("SELECT extension, user_id FROM proc.sip_extension WHERE is_active")
    return {r["extension"]: r["user_id"] for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# AMI event parsing  (pure — unit-tested)
# --------------------------------------------------------------------------- #
def channel_extension(channel: str | None) -> str | None:
    """'PJSIP/1002-00000003' -> '1002'. Returns None for anything unexpected."""
    if not channel:
        return None
    m = re.match(r"^[A-Za-z0-9]+/([^-/]+)-", channel)
    return m.group(1) if m else None


def screenpop_from_event(ev: dict) -> tuple[str, str] | None:
    """From a ringing event, extract (dest_extension, caller_number) or None.

    Uses DialBegin, which fires once per bridged leg with DestChannel = the leg
    being rung and CallerIDNum = the calling party's caller id.
    """
    if ev.get("Event") != "DialBegin":
        return None
    dest_ext = channel_extension(ev.get("DestChannel"))
    caller = (ev.get("CallerIDNum") or "").strip()
    if not dest_ext or not caller:
        return None
    return dest_ext, caller


# --------------------------------------------------------------------------- #
# WebSocket hub + AMI listener service
# --------------------------------------------------------------------------- #
class _Hub:
    """Routes screen-pops to the browser(s) registered for an extension."""

    def __init__(self) -> None:
        self._by_ext: dict[str, set[WebSocket]] = {}

    def add(self, ext: str, ws: WebSocket) -> None:
        self._by_ext.setdefault(ext, set()).add(ws)

    def remove(self, ext: str, ws: WebSocket) -> None:
        conns = self._by_ext.get(ext)
        if conns:
            conns.discard(ws)
            if not conns:
                self._by_ext.pop(ext, None)

    async def push(self, ext: str, payload: dict) -> None:
        for ws in list(self._by_ext.get(ext, ())):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — drop dead sockets silently
                self.remove(ext, ws)


class TelephonyService:
    """Owns the AMI connection (reconnecting) and the screen-pop hub. One
    instance per process, started/stopped from the app lifespan."""

    def __init__(self, cursor_factory) -> None:
        self._cursor = cursor_factory
        self.hub = _Hub()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        if not TELEPHONY_ENABLED:
            log.info("telephony disabled (TELEPHONY_ENABLED unset) — AMI listener not started")
            return
        if not (AMI_USER and AMI_PASSWORD):
            log.warning("telephony enabled but AMI_USER/AMI_PASSWORD unset — screen-pop off")
            return
        self._stop.clear()
        self._task = asyncio.ensure_future(self._run())
        log.info("telephony AMI listener starting for %s:%s", AMI_HOST, AMI_PORT)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # -- DB helpers (run off the event loop) -------------------------------- #
    def _lookup_sync(self, number: str) -> dict | None:
        """Resolve a caller number to a CRM match (or None)."""
        with self._cursor() as c:
            return lookup_caller(c, number)

    def _known_ext_sync(self, ext: str) -> bool:
        with self._cursor() as c:
            return ext in extensions_map(c)

    # -- AMI loop ----------------------------------------------------------- #
    async def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                await self._connect_and_read()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reconnect on any failure
                log.warning("AMI connection lost (%s); retrying in %ss", e, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def _connect_and_read(self) -> None:
        reader, writer = await asyncio.open_connection(AMI_HOST, AMI_PORT)
        try:
            writer.write(
                (f"Action: Login\r\nUsername: {AMI_USER}\r\n"
                 f"Secret: {AMI_PASSWORD}\r\nEvents: on\r\n\r\n").encode())
            await writer.drain()
            log.info("AMI connected and authenticated")
            while not self._stop.is_set():
                ev = await self._read_message(reader)
                if ev is None:
                    raise ConnectionError("AMI stream closed")
                await self._handle(ev)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _read_message(reader: asyncio.StreamReader) -> dict | None:
        """Read one AMI message (key/value lines terminated by a blank line)."""
        fields: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                return None
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text == "":
                if fields:
                    return fields
                continue  # skip leading blank lines / banner spacing
            if ":" in text:
                k, v = text.split(":", 1)
                fields[k.strip()] = v.strip()
            # non key:value lines (e.g. the login banner) are ignored

    async def _handle(self, ev: dict) -> None:
        popped = screenpop_from_event(ev)
        if not popped:
            return
        dest_ext, caller = popped
        loop = asyncio.get_event_loop()
        try:
            if not await loop.run_in_executor(None, self._known_ext_sync, dest_ext):
                return
            match = await loop.run_in_executor(None, self._lookup_sync, caller)
        except Exception as e:  # noqa: BLE001 — a bad lookup must not kill the loop
            log.warning("screen-pop lookup failed for %s: %s", caller, e)
            return
        norm = normalize_number(caller)
        await self.hub.push(dest_ext, {
            "type": "incoming",
            "number": norm["national"] or norm["e164"] or caller,
            "raw_number": caller,
            "match": match,
        })
        log.info("screen-pop -> ext %s: caller %s matched=%s",
                 dest_ext, caller, bool(match))


# Module-level singleton, wired up in main.py.
SERVICE: TelephonyService | None = None


def init_service(cursor_factory) -> TelephonyService:
    global SERVICE
    SERVICE = TelephonyService(cursor_factory)
    return SERVICE


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def _user_for_extension(cursor, uid, expected_ext: str | None = None) -> dict | None:
    """Load an active user and attach their provisioned extension. If
    expected_ext is given, it must still match (guards a stale token)."""
    with cursor() as c:
        u = _auth.load_user(c, uid)
        if not u:
            return None
        ext = get_extension(c, uid)
        if not ext:
            return None
        if expected_ext is not None and ext["extension"] != expected_ext:
            return None
        u = dict(u)
        u["_extension"] = ext["extension"]
        return u


def _resolve_ws_user(websocket: WebSocket, cursor) -> dict | None:
    """Authenticate the screen-pop WebSocket. Preferred path: the signed token
    minted by /telephony/config (survives cookie-less handshakes). Fallback:
    the session cookie + session_version, when it is present."""
    token = websocket.query_params.get("token")
    if token:
        data = read_ws_token(token)
        if data and data.get("uid"):
            return _user_for_extension(cursor, data["uid"], data.get("ext"))
        return None
    # Fallback: session cookie (works for same-origin, non-proxied pages).
    try:
        uid = websocket.session.get("uid")
        sv = websocket.session.get("sv")
    except Exception:  # noqa: BLE001
        return None
    if not uid:
        return None
    with cursor() as c:
        u = _auth.load_user(c, uid)
        if u and int(sv or -1) == int(u.get("session_version") or 0):
            ext = get_extension(c, uid)
            if ext:
                u = dict(u)
                u["_extension"] = ext["extension"]
            return u
    return None


def make_router(templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/telephony", tags=["telephony"])

    @router.get("/config")
    def config(request: Request):  # sync -> runs in threadpool
        u = getattr(request.state, "user", None)
        if not u:
            return JSONResponse({"enabled": False}, status_code=401)
        if not TELEPHONY_ENABLED:
            return {"enabled": False}
        with cursor() as c:
            ext = get_extension(c, u["id"])
        if not ext:
            # Enabled globally, but this agent has no SIP endpoint provisioned.
            return {"enabled": True, "provisioned": False}
        return {
            "enabled": True, "provisioned": True,
            "ws_url": SIP_WS_URL, "domain": SIP_DOMAIN,
            "extension": ext["extension"], "sip_user": ext["sip_user"],
            # The browser must present this to REGISTER (inherent to WebRTC
            # softphones). Only ever returned to the authenticated owner.
            "sip_password": ext["sip_secret"],
            "display_name": ext["display_name"] or u.get("username"),
            # Bearer token for the screen-pop WebSocket (see make_ws_token).
            "ws_token": make_ws_token(u["id"], ext["extension"]),
        }

    @router.get("/lookup")
    def lookup(request: Request, number: str = ""):
        if not getattr(request.state, "user", None):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        with cursor() as c:
            match = lookup_caller(c, number)
        return {"number": number, "match": match}

    @router.post("/log-call")
    async def log_call(request: Request):
        u = getattr(request.state, "user", None)
        if not u:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        customer_user_id = body.get("customer_user_id")
        if not customer_user_id:
            # customer_call.user_id is NOT NULL -> we can only log against a
            # known customer account. Unmatched calls are intentionally dropped.
            return {"logged": False, "reason": "no_customer"}
        direction = "incoming" if body.get("direction") == "incoming" else "outgoing"
        status = body.get("status") or "held"
        with cursor() as c:
            c.execute(
                """INSERT INTO proc.customer_call
                       (user_id, subject, direction, status, external_number,
                        started_at, ended_at, duration_s, assigned_to, created_by)
                   VALUES (%s, %s, %s, %s, %s, now(), now(), %s, %s, %s)
                   RETURNING id""",
                (customer_user_id, body.get("subject"), direction, status,
                 body.get("external_number"), body.get("duration_s"),
                 u["id"], u["id"]))
            call_id = c.fetchone()["id"]
        return {"logged": True, "id": call_id}

    @router.websocket("/ws")
    async def ws(websocket: WebSocket):
        u = _resolve_ws_user(websocket, cursor)
        ext = (u or {}).get("_extension")
        if not u or not ext or SERVICE is None:
            # Most common cause is a stale/expired ws_token (e.g. page left open
            # across a long gap) — the browser refetches /config on reload.
            log.info("screen-pop WS rejected (user=%s ext=%s service=%s)",
                     bool(u), bool(ext), SERVICE is not None)
            await websocket.close(code=1008)
            return
        await websocket.accept()
        SERVICE.hub.add(ext, websocket)
        try:
            await websocket.send_json({"type": "ready", "extension": ext})
            while True:
                # We don't need inbound messages; receive to detect disconnect
                # and to keep the connection from idling out.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            SERVICE.hub.remove(ext, websocket)

    return router
