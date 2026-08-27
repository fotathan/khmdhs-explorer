"""
mailer.py — the one place this app sends email from.

Backends (EMAIL_BACKEND, default "console"):

  console  — log the message to stdout. The zero-setup default; nothing leaves
             the machine, which is what you want on a dev box and in CI.
  memory   — keep messages in a list (mailer.outbox()) instead of sending.
             What the tests assert against.
  file     — write one .eml per message under EMAIL_FILE_DIR. Open them in any
             mail client to check the real MIME/HTML rendering.
  smtp     — a real SMTP conversation. Point it at Mailpit/MailHog locally
             (SMTP_HOST=127.0.0.1 SMTP_PORT=1025) or at a provider later.

Deliverability (SPF/DKIM/DMARC, bounce handling, an unsubscribe endpoint) is
deliberately NOT handled here — this is a testing-grade sender. Anything going
to real recipients needs that work first.

EMAIL_REDIRECT_TO is the safety catch while testing: set it and every message
goes there instead of the customer, with the intended address preserved in an
X-Original-To header and in the file/console dump.

Every message is multipart/alternative: the caller supplies both the HTML body
and the plain-text alternative, and both are always sent. Nothing here knows
what a digest is — see app/digests.py.
"""
from __future__ import annotations

import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

DEFAULT_FROM = "KHMDHS Explorer <noreply@localhost>"
BACKENDS = ("console", "memory", "file", "smtp")

# Filled by the "memory" backend; drained by tests via outbox()/clear_outbox().
_OUTBOX: list[dict] = []

# A liberal address check — enough to catch an empty/garbled recipient before we
# open an SMTP connection, not an RFC 5322 parser.
_ADDR_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class MailError(RuntimeError):
    """Sending failed. The caller records it on the run and moves on."""


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _flag(name, default=False):
    v = _env(name)
    return v.lower() in ("1", "true", "yes", "on") if v else default


def backend() -> str:
    b = (_env("EMAIL_BACKEND", "console") or "console").lower()
    return b if b in BACKENDS else "console"


def sender() -> str:
    return _env("EMAIL_FROM", DEFAULT_FROM)


def valid_address(addr: str) -> bool:
    _, email = parseaddr(addr or "")
    return bool(_ADDR_RE.match(email or ""))


def describe() -> dict:
    """What the admin page shows about the current mail setup."""
    b = backend()
    where = {
        "console": "stdout",
        "memory":  "in-process list",
        "file":    _env("EMAIL_FILE_DIR", "outbox"),
        "smtp":    f"{_env('SMTP_HOST', '(unset)')}:{_env('SMTP_PORT', '25')}",
    }[b]
    return {"backend": b, "target": where, "sender": sender(),
            "redirect_to": _env("EMAIL_REDIRECT_TO") or None,
            "configured": b != "smtp" or bool(_env("SMTP_HOST"))}


# --------------------------------------------------------------------------- #
# Outbox (memory backend)
# --------------------------------------------------------------------------- #
def outbox() -> list[dict]:
    return list(_OUTBOX)


def clear_outbox() -> None:
    _OUTBOX.clear()


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def build_message(*, to: str, subject: str, html: str, text: str,
                  from_addr: str = None, reply_to: str = None,
                  headers: dict = None) -> EmailMessage:
    """A multipart/alternative message. `to` is the INTENDED recipient even when
    EMAIL_REDIRECT_TO rewrites the envelope — the original is kept in a header
    so a redirected test mail still says who it was for."""
    msg = EmailMessage()
    msg["From"] = from_addr or sender()
    redirect = _env("EMAIL_REDIRECT_TO")
    msg["To"] = redirect or to
    if redirect and redirect != to:
        msg["X-Original-To"] = to
    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="khmdhs.local")
    rt = reply_to or _env("EMAIL_REPLY_TO")
    if rt:
        msg["Reply-To"] = rt
    # Bulk mail markers: keep auto-responders quiet, and mark the class of mail
    # so a receiving MTA can file it. Not a substitute for a real List-Unsubscribe
    # endpoint, which belongs with the deliverability work.
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    for k, v in (headers or {}).items():
        if v:
            msg[k] = v
    msg.set_content(text or "")
    msg.add_alternative(html or "", subtype="html")
    return msg


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def send(*, to: str, subject: str, html: str, text: str,
         from_addr: str = None, reply_to: str = None,
         headers: dict = None) -> dict:
    """Send one message through the configured backend.

    Returns {backend, to, message_id, detail}. Raises MailError on failure —
    including an unusable recipient, which is by far the most common one and
    should never reach the SMTP layer."""
    to = (to or "").strip()
    if not valid_address(to):
        raise MailError(f"invalid recipient address: {to!r}")

    msg = build_message(to=to, subject=subject, html=html, text=text,
                        from_addr=from_addr, reply_to=reply_to, headers=headers)
    envelope_to = msg["To"]
    b = backend()
    result = {"backend": b, "to": envelope_to, "intended": to,
              "message_id": msg["Message-ID"], "detail": ""}

    if b == "memory":
        _OUTBOX.append({**result, "subject": subject, "html": html,
                        "text": text, "raw": msg.as_string()})
        result["detail"] = "queued in memory"
        return result

    if b == "console":
        print(f"\n=== EMAIL ({envelope_to}) ===\nSubject: {subject}\n\n"
              f"{text}\n=== end email ===\n", file=sys.stdout, flush=True)
        result["detail"] = "written to stdout"
        return result

    if b == "file":
        directory = _env("EMAIL_FILE_DIR", "outbox")
        try:
            os.makedirs(directory, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._@-]", "_", envelope_to)[:60]
            path = os.path.join(directory, f"{int(time.time() * 1000)}-{safe}.eml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(msg.as_string())
        except OSError as exc:
            raise MailError(f"could not write message: {exc}") from exc
        result["detail"] = path
        return result

    # ---- smtp ------------------------------------------------------------- #
    host = _env("SMTP_HOST")
    if not host:
        raise MailError("EMAIL_BACKEND=smtp but SMTP_HOST is not set")
    port = int(_env("SMTP_PORT", "25") or 25)
    timeout = float(_env("SMTP_TIMEOUT", "20") or 20)
    user, password = _env("SMTP_USER"), os.environ.get("SMTP_PASSWORD") or ""
    try:
        opener = smtplib.SMTP_SSL if _flag("SMTP_SSL") else smtplib.SMTP
        with opener(host, port, timeout=timeout) as srv:
            if _flag("SMTP_STARTTLS") and not _flag("SMTP_SSL"):
                srv.starttls()
            if user:
                srv.login(user, password)
            srv.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP send failed ({type(exc).__name__}): {exc}") from exc
    result["detail"] = f"sent via {host}:{port}"
    return result


def address_for(user: dict) -> str:
    """The address a customer row is mailed at: their account email. Username is
    NOT a fallback — usernames are not addresses here, and guessing one would
    mail a stranger."""
    return ((user or {}).get("email") or "").strip()

