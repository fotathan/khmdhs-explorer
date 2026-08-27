"""
login_links.py — "email me a sign-in link", the passwordless path onto /login.

A SECOND way in, not a replacement for the password. Everything that guards a
password login still guards this one:

  * 2FA is not bypassed. A valid link completes the PASSWORD step only. If the
    account has TOTP on, the click lands in exactly the same `mfa_pending`
    state a correct password produces, and the second factor is still required.
    (A link that skipped it would silently downgrade every 2FA account to one
    factor, which is the whole reason 2FA was turned on.)
  * must_change_password still applies — AuthMiddleware walls the session off
    until the temporary password is replaced, however the session was created.
  * is_active is re-checked at consume time, not at issue time.

The token
---------
32 bytes of CSPRNG output, mailed once, stored only as sha256 (see the
migration for why sha256 and not scrypt). Single use, 15 minutes by default.
Consumption is one atomic UPDATE ... WHERE used_at IS NULL RETURNING, so two
concurrent clicks cannot both produce a session.

Why the link does not sign you in on GET
----------------------------------------
Corporate mail scanners and link prefetchers fetch every URL in a message. A
one-click GET would be spent by the scanner before the human clicks it, and the
customer would meet "this link has already been used" — the single most common
complaint about magic links. So the mailed URL opens an interstitial page whose
button POSTs the token back. GET stays safe (it only looks the token up);
POST spends it.

Enumeration
-----------
POST /login/link answers identically whether or not the address exists — same
page, same wording, same status. Nothing here may leak which addresses have
accounts. Throttled per (address, IP) through the same DB-backed limiter that
guards password login, so it cannot be used as a mail cannon either.

Mail goes out through app/mailer.py like everything else, which means
EMAIL_BACKEND=console (the default) prints the link instead of sending it —
that is what makes this testable on a dev box with no SMTP at all.
"""
from __future__ import annotations

import hashlib
import html as _html
import os
import secrets

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from app import auth as _auth
    from app import digests as _digests
    from app import email_builder as _email
    from app import i18n as _i18n
    from app import mailer as _mailer
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import digests as _digests
    import email_builder as _email
    import i18n as _i18n
    import mailer as _mailer

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# How long a mailed link stays usable. Short enough that a forwarded or
# shoulder-surfed message goes stale quickly, long enough to survive a slow
# corporate mail queue.
TTL_SECONDS = max(60, int(os.environ.get("LOGIN_LINK_TTL_SECONDS") or 900))

# The email_template slug this feature's wording lives under, per language.
TEMPLATE_SLUG = "login_link"
EMAIL_TEMPLATE = "email_login_link.html"

# Same limiter as password login (proc.login_throttle): 8 attempts, then a
# 5-minute lockout for that (address, IP) pair.
THROTTLE_PREFIX = "loginlink"

# Rendering an email from a request-free context (the same reason digests.py
# builds its own environment): no request, no base template, no context
# processor.
_env = Environment(loader=FileSystemLoader(os.path.join(APP_DIR, "templates")),
                   autoescape=select_autoescape(["html"]))


def base_url() -> str:
    """Absolute links: an email has no origin to resolve "/login/link/…" against."""
    return (os.environ.get("APP_BASE_URL") or "http://localhost:8000").rstrip("/")


def enabled() -> bool:
    """LOGIN_LINKS_ENABLED=0 hides the feature entirely (form, routes, link on
    /login). On by default: with EMAIL_BACKEND=console it is harmless, and the
    switch exists for the deployment that has not sorted out mail yet."""
    return (os.environ.get("LOGIN_LINKS_ENABLED", "1") or "1").strip() != "0"


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def _hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def issue(c, uid, *, ip=None, next_url=None, ttl=None) -> str:
    """Mint one link for a user and return the RAW token — the only time it
    exists outside the email. Any live link the user already has is burned
    first: asking for a new one is what people do when the first did not
    arrive, and two live credentials are worse than one."""
    raw = secrets.token_urlsafe(32)
    _auth.kill_login_links(c, uid)
    c.execute("""INSERT INTO proc.login_link
                   (user_id, token_hash, expires_at, requested_ip, next_url)
                 VALUES (%s, %s, now() + make_interval(secs => %s), %s, %s)""",
              (uid, _hash(raw), int(ttl or TTL_SECONDS), ip, next_url))
    # Opportunistic cleanup, the same trick proc.login_throttle uses, so a
    # request-a-link script cannot grow the table without bound.
    c.execute("DELETE FROM proc.login_link "
              "WHERE expires_at < now() - interval '1 day'")
    return raw


def peek(c, raw):
    """Look a token up WITHOUT spending it — what the interstitial GET does, so
    a mail scanner fetching the URL cannot burn the customer's link.

    Returns the row (with the user joined on) when the token is live, else None.
    """
    c.execute("""SELECT l.id, l.user_id, l.next_url, l.expires_at,
                        u.username, u.email
                   FROM proc.login_link l
                   JOIN proc.app_user u ON u.id = l.user_id
                  WHERE l.token_hash = %s
                    AND l.used_at IS NULL
                    AND l.expires_at > now()
                    AND u.is_active""", (_hash(raw),))
    return c.fetchone()


def consume(c, raw):
    """Spend a token and return its row, or None if it was already spent,
    expired, unknown, or belongs to a deactivated account.

    The read and the write are ONE statement on purpose: `SELECT ... then
    UPDATE` would let two concurrent clicks both pass the check and both create
    a session. `used_at IS NULL` in the WHERE clause makes the database the
    arbiter of single use."""
    c.execute("""UPDATE proc.login_link l
                    SET used_at = now()
                   FROM proc.app_user u
                  WHERE l.token_hash = %s
                    AND l.used_at IS NULL
                    AND l.expires_at > now()
                    AND u.id = l.user_id
                    AND u.is_active
              RETURNING l.id, l.user_id, l.next_url""", (_hash(raw),))
    return c.fetchone()


def mark_email_verified(c, uid):
    """Stamp the first successful link login as proof the address is real.

    Registration never confirmed it (email is optional and unverified), so this
    is the only place in the app that learns an address actually reaches its
    owner. Only ever set once — a later link login must not move the date."""
    c.execute("UPDATE proc.app_user SET email_verified_at = now() "
              "WHERE id = %s AND email_verified_at IS NULL", (uid,))


# --------------------------------------------------------------------------- #
# The email
# --------------------------------------------------------------------------- #
def _merge_values(c, user):
    """[[field]] values for the wording. The customer profile supplies the name
    when there is one; the account is the fallback. Every field is optional —
    _soft_resolve drops the empty ones rather than failing the send."""
    profile = _auth.get_profile(c, user["id"]) or {}
    full = (profile.get("full_name") or "").strip()
    first = full.split()[0] if full else ""
    return {"full_name": full,
            "first_name": first,
            "recipient_name": full,
            "username": user.get("username") or "",
            "email": user.get("email") or "",
            "company": (profile.get("company") or "").strip()}


def render_email(c, user, url, lang="el"):
    """(subject, html, text) for one sign-in link.

    Wording comes from proc.email_template slug 'login_link' so an admin can
    change a sentence without a deploy; the URL is placed by the Jinja template,
    never by the admin-authored fragment, so no edit to that wording can break,
    truncate or leak the credential.

    A deleted template row falls back to a built-in line: a customer locked out
    of their account is not the moment to discover an admin emptied a table."""
    lang = _i18n.normalize_lang(lang)
    t = (lambda s: _i18n.translate(s, lang))
    tpl = _auth.get_email_template(c, TEMPLATE_SLUG, lang)
    values = _merge_values(c, user)
    if tpl:
        # digests._soft_resolve, NOT email_builder.resolve_fields — the same rule
        # the digests carry (see CLAUDE.md): an automated send has no human in
        # the loop, so an empty optional token must drop out instead of failing.
        # Nowhere is that more true than here: the alternative is a customer who
        # cannot get in because their profile has no company name.
        # unescape() on the subject only — that is a plain-text header, so an
        # escaped "&" would arrive literally as "&amp;".
        subject = _html.unescape(
            _digests._strip_markers(_digests._soft_resolve(tpl.get("subject") or "", values)))
        intro = _digests._strip_markers(
            _digests._soft_resolve(tpl.get("body_html") or "", values))
    else:
        subject = t("Ο σύνδεσμος σύνδεσής σας")
        intro = ("<p>{}</p>".format(
            t("Ζητήσατε σύνδεση χωρίς κωδικό στον λογαριασμό σας.")))
    minutes = max(1, TTL_SECONDS // 60)
    html = _env.get_template(EMAIL_TEMPLATE).render(
        t=t, lang=lang, intro=intro, url=url, minutes=minutes,
        username=user.get("username") or "", base=base_url())
    text = "\n\n".join([
        _email.to_plain_text(intro),
        f"{t('Σύνδεση')}: {url}",
        t("Ο σύνδεσμος ισχύει για %d λεπτά και μπορεί να χρησιμοποιηθεί μία φορά.")
        % minutes,
        t("Αν δεν ζητήσατε εσείς αυτό το μήνυμα, αγνοήστε το — ο λογαριασμός σας "
          "δεν έχει αλλάξει."),
    ])
    return subject or t("Ο σύνδεσμος σύνδεσής σας"), html, text


def send_link(c, user, *, ip=None, next_url=None, lang="el") -> dict:
    """Issue a token, render the message and hand it to the mailer.

    Raises mailer.MailError if the address is unusable or the backend fails —
    the caller swallows it, because the response must look identical whether or
    not an address exists (see the module docstring)."""
    to = _mailer.address_for(user)
    raw = issue(c, user["id"], ip=ip, next_url=next_url)
    url = f"{base_url()}/login/link/{raw}"
    subject, html, text = render_email(c, user, url, lang=lang)
    return _mailer.send(to=to, subject=subject, html=html, text=text,
                        headers={"X-KHMDHS-Mail": "login-link"})
