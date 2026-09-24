"""
cert_reminders.py — email a customer before a declared certificate expires.

docs/specs/evaluation-layer.md §10 (slice 2). The certificates themselves are
app/eligibility_eval.py; this module only decides WHEN to write about them.

Rules
-----
* OPT-IN. proc.certificate_alert.enabled, set by the customer on
  /account/certificates. No row = off. Deliverability (SPF/DKIM/DMARC,
  unsubscribe) is not done, so nobody is mailed who did not ask.
* Entitled, active accounts with an email address only (auth.load_user's
  has_access: testers, subscribers, admins) — the same people who see the
  certificate notes on the checklist.
* Marks: REMIND_DAYS before valid_until (60 and 14). Each mark fires at most
  once per certificate AND per valid_until: the ledger
  (proc.certificate_expiry_notice) carries the date it was sent for, so a
  renewal re-arms every mark. A certificate already inside several unsent
  marks (declared 10 days before it lapses) gets ONE line, and every mark it
  has passed is spent with it.
* One message per customer per run, listing every certificate that is due.
* The ledger is written ONLY after the message left — a failed send retries
  on the next tick, never loses the reminder.
* An expired certificate (or one without a date) is never chased.
* app/mailer.py is the only place mail leaves; EMAIL_BACKEND defaults to
  console.

Fired by digests.run_loop (DIGEST_SCHEDULER=1) and cron_digests.py — the same
runner as the digests, never both.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import os
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from app import auth as _auth
    from app import digests as _digests
    from app import eligibility_eval as _eval
    from app import email_builder as _email
    from app import i18n as _i18n
    from app import mailer as _mailer
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import digests as _digests
    import eligibility_eval as _eval
    import email_builder as _email
    import i18n as _i18n
    import mailer as _mailer

ATHENS = ZoneInfo("Europe/Athens")
REMIND_DAYS = (60, 14)
TEMPLATE_SLUG = "cert_expiry"
EMAIL_TEMPLATE = "email_cert_expiry.html"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
_env = Environment(loader=FileSystemLoader(os.path.join(APP_DIR, "templates")),
                   autoescape=select_autoescape(["html"]))


def base_url() -> str:
    return (os.environ.get("APP_BASE_URL") or "http://localhost:8000").rstrip("/")


def athens_today() -> dt.date:
    return dt.datetime.now(ATHENS).date()


# --------------------------------------------------------------------------- #
# The opt-in
# --------------------------------------------------------------------------- #
def is_enabled(c, user_id) -> bool:
    c.execute("SELECT enabled FROM proc.certificate_alert WHERE user_id = %s",
              (user_id,))
    row = c.fetchone()
    return bool(row and row["enabled"])


def set_enabled(c, user_id, on: bool, lang: str = "el") -> None:
    """Switch the reminders on or off. `lang` is the language the customer
    was using — the one the reminder is written in (there is no per-user
    language otherwise; the calendar feed keeps its own the same way)."""
    lang = "en" if lang == "en" else "el"
    c.execute("""INSERT INTO proc.certificate_alert (user_id, enabled, lang)
                 VALUES (%s, %s, %s)
                 ON CONFLICT (user_id) DO UPDATE
                   SET enabled = EXCLUDED.enabled, lang = EXCLUDED.lang,
                       updated_at = now()""",
              (user_id, bool(on), lang))


# --------------------------------------------------------------------------- #
# What is due — pure
# --------------------------------------------------------------------------- #
def due_marks(valid_until: dt.date | None, today: dt.date,
              spent: set[int]) -> list[int]:
    """The unsent marks this certificate has reached. [] when it has none
    (not near yet, already expired, no date, or every reached mark spent)."""
    if valid_until is None:
        return []
    left = (valid_until - today).days
    if left < 0:
        return []
    return [m for m in REMIND_DAYS if left <= m and m not in spent]


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def _candidates(c) -> list[dict]:
    """[{user_id, lang}] — opted in, holding at least one dated certificate."""
    c.execute("""SELECT a.user_id, a.lang
                   FROM proc.certificate_alert a
                  WHERE a.enabled
                    AND EXISTS (SELECT 1 FROM proc.company_certificate cc
                                 WHERE cc.user_id = a.user_id
                                   AND cc.valid_until IS NOT NULL)
                  ORDER BY a.user_id""")
    return c.fetchall()


def _spent(c, cert_ids) -> dict[tuple[int, dt.date], set[int]]:
    if not cert_ids:
        return {}
    c.execute("""SELECT certificate_id, valid_until, mark_days
                   FROM proc.certificate_expiry_notice
                  WHERE certificate_id = ANY(%s)""", (list(cert_ids),))
    out: dict = {}
    for r in c.fetchall():
        out.setdefault((r["certificate_id"], r["valid_until"]), set()).add(r["mark_days"])
    return out


def due_for_user(c, user_id, today: dt.date) -> list[dict]:
    """[{cert…, "days_left", "marks"}] for this user's certificates that are due."""
    certs = [x for x in _eval.certificates(c, user_id) if x["valid_until"]]
    spent = _spent(c, [x["id"] for x in certs])
    out = []
    for cert in certs:
        marks = due_marks(cert["valid_until"], today,
                          spent.get((cert["id"], cert["valid_until"]), set()))
        if marks:
            out.append({**cert, "marks": marks,
                        "days_left": (cert["valid_until"] - today).days})
    out.sort(key=lambda x: x["valid_until"])
    return out


def _record(c, items) -> None:
    for it in items:
        for m in it["marks"]:
            c.execute("""INSERT INTO proc.certificate_expiry_notice
                           (certificate_id, mark_days, valid_until)
                         VALUES (%s, %s, %s)
                         ON CONFLICT DO NOTHING""",
                      (it["id"], m, it["valid_until"]))


# --------------------------------------------------------------------------- #
# The message
# --------------------------------------------------------------------------- #
def _merge_values(c, user) -> dict:
    profile = _auth.get_profile(c, user["id"]) or {}
    full = (profile.get("full_name") or "").strip()
    return {"full_name": full, "first_name": full.split()[0] if full else "",
            "recipient_name": full, "username": user.get("username") or "",
            "email": user.get("email") or "",
            "company": (profile.get("company") or "").strip()}


def line(it: dict, t) -> str:
    """One certificate as a sentence — the email's list item and text line."""
    who = (f"{t('Κατασκευαστής')} {it['manufacturer']}: " if it.get("manufacturer") else "")
    name = it["name"] + (f":{it['edition']}" if it.get("edition") else "")
    when = it["valid_until"].strftime("%d/%m/%Y")
    if it["days_left"] == 0:
        left = t("λήγει σήμερα")
    elif it["days_left"] == 1:
        left = t("αύριο")
    else:
        left = t("σε %d ημέρες") % it["days_left"]
    return f"{who}{name} — {t('λήγει')} {when} ({left})"


def render_email(c, user, items, lang="el") -> tuple[str, str, str]:
    lang = _i18n.normalize_lang(lang)
    t = (lambda s: _i18n.translate(s, lang))
    tpl = _auth.get_email_template(c, TEMPLATE_SLUG, lang)
    values = _merge_values(c, user)
    if tpl:
        # _soft_resolve, not email_builder.resolve_fields: no human in the
        # loop, so an empty optional token drops out instead of failing.
        subject = _html.unescape(
            _digests._strip_markers(_digests._soft_resolve(tpl.get("subject") or "", values)))
        intro = _digests._strip_markers(
            _digests._soft_resolve(tpl.get("body_html") or "", values))
    else:
        subject = t("Πιστοποιητικό σας λήγει σύντομα")
        intro = "<p>{}</p>".format(
            t("Ένα ή περισσότερα πιστοποιητικά στο προφίλ σας λήγουν σύντομα."))
    lines = [line(it, t) for it in items]
    url = f"{base_url()}/account/certificates"
    html = _env.get_template(EMAIL_TEMPLATE).render(
        t=t, lang=lang, intro=intro, lines=lines, url=url,
        username=user.get("username") or "", base=base_url())
    text = "\n\n".join([_email.to_plain_text(intro),
                        "\n".join(f"- {x}" for x in lines),
                        f"{t('Τα πιστοποιητικά μου')}: {url}",
                        t("Λαμβάνετε αυτό το μήνυμα επειδή ζητήσατε υπενθυμίσεις λήξης πιστοποιητικών. Τις απενεργοποιείτε στη σελίδα των πιστοποιητικών σας.")])
    return subject or t("Πιστοποιητικό σας λήγει σύντομα"), html, text


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def run_due(c, *, today: dt.date | None = None) -> dict:
    """One sweep over every opted-in customer. Returns a summary."""
    today = today or athens_today()
    out = {"checked": 0, "sent": 0, "errors": 0, "skipped": 0}
    for cand in _candidates(c):
        uid = cand["user_id"]
        out["checked"] += 1
        user = _auth.load_user(c, uid)
        if (not user or not user.get("is_active") or not user.get("has_access")
                or not _mailer.valid_address(_mailer.address_for(user))):
            out["skipped"] += 1
            continue
        items = due_for_user(c, uid, today)
        if not items:
            continue
        try:
            subject, html, text = render_email(c, user, items,
                                               lang=cand["lang"])
            _mailer.send(to=_mailer.address_for(user), subject=subject,
                         html=html, text=text,
                         headers={"X-KHMDHS-Mail": "cert-expiry"})
        except Exception as e:           # noqa: BLE001 — one customer never stops the sweep
            out["errors"] += 1
            print(f"[cert_reminders] user {uid}: {e!r}", flush=True)
            continue
        _record(c, items)
        out["sent"] += 1
    return out
