"""
digests.py — scheduled "new results" emails driven by search profiles.

The shape of the feature
------------------------
A DIGEST SUBSCRIPTION is (customer × search profile). On its schedule, the app
replays that profile's saved filters over everything ingested since the last
send and emails the customer what is new.

Which schedule applies is a two-step fallback, mirroring how a customer search
profile falls back to the portal profile it is based on:

    subscription.schedule_id   → the per-customer, per-profile override
    else the schedule flagged is_default → the portal default

so an admin sets the portal cadence once, and only overrides the customers who
want something else.

The window
----------
Always `procurement_act.ingested_at`, never the act's own dates. An act signed
last month but published to KHMDHS today must reach the customer today, and
ingested_at is the only column that records when it became visible to us. Each
subscription keeps its own high-water mark (last_cursor), so a missed run is
absorbed by the next one and no act is ever mailed twice.

Cadence maths (no cron parser)
------------------------------
A schedule is cadence + hour/minute (+ weekday | day_of_month) in a named tz.
`last_occurrence` walks days backwards from "today in tz" to the most recent
firing time at or before now; a subscription is due when it has not run since
that moment. This keeps the admin form a handful of selects and adds no
dependency, at the cost of not expressing "every 3 hours" — which no customer
has asked for.

Where the pieces live
---------------------
  app/mailer.py    — the actual sending (console/memory/file/smtp backends)
  proc.email_template slug 'digest' — subject + intro wording, editable at
                     /admin/email-templates so copy changes need no deploy
  app/templates/email_digest.html   — the results table around that intro
  cron_digests.py  — the entry point a scheduler (or you) invokes
  /admin/digests   — schedules, subscriptions, run history, preview, test send
"""
from __future__ import annotations

import datetime as dt
import html as _html
import os
import re
import zoneinfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from app import auth as _auth
    from app import email_builder as _email
    from app import i18n as _i18n
    from app import mailer as _mailer
    from app import search_profiles as _sp
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import email_builder as _email
    import i18n as _i18n
    import mailer as _mailer
    import search_profiles as _sp

APP_DIR = os.path.dirname(os.path.abspath(__file__))

CADENCES = ("daily", "weekdays", "weekly", "monthly")
DEFAULT_TZ = "Europe/Athens"

# Absolute links: an email has no origin to resolve "/act/25SYMV…" against.
def base_url() -> str:
    return (os.environ.get("APP_BASE_URL") or "http://localhost:8000").rstrip("/")


# --------------------------------------------------------------------------- #
# Cadence maths
# --------------------------------------------------------------------------- #
def _tz(name):
    try:
        return zoneinfo.ZoneInfo(name or DEFAULT_TZ)
    except Exception:                    # noqa: BLE001 — a bad tz must not stop the run
        return zoneinfo.ZoneInfo(DEFAULT_TZ)


def _fires_on(schedule, day: dt.date) -> bool:
    """Whether this schedule fires on the given local calendar day."""
    cadence = schedule.get("cadence")
    if cadence == "daily":
        return True
    if cadence == "weekdays":
        return day.weekday() < 5
    if cadence == "weekly":
        return day.weekday() == (schedule.get("weekday") or 0)
    if cadence == "monthly":
        return day.day == (schedule.get("day_of_month") or 1)
    return False


def last_occurrence(schedule, now: dt.datetime = None) -> dt.datetime | None:
    """The most recent firing time at or before `now`, as an aware UTC datetime.

    None when the schedule cannot fire (unknown cadence). Walks at most ~40 days
    back, which covers monthly; beyond that there is nothing to find."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    tz = _tz(schedule.get("tz"))
    local = now.astimezone(tz)
    hour, minute = int(schedule.get("hour") or 0), int(schedule.get("minute") or 0)
    for back in range(0, 40):
        day = (local - dt.timedelta(days=back)).date()
        if not _fires_on(schedule, day):
            continue
        # fold=0: on a DST fall-back the earlier of the two local times fires.
        cand = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz)
        if cand <= local:
            return cand.astimezone(dt.timezone.utc)
    return None


def next_occurrence(schedule, now: dt.datetime = None) -> dt.datetime | None:
    """The next firing time strictly after `now`, as aware UTC. For display."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    tz = _tz(schedule.get("tz"))
    local = now.astimezone(tz)
    hour, minute = int(schedule.get("hour") or 0), int(schedule.get("minute") or 0)
    for ahead in range(0, 40):
        day = (local + dt.timedelta(days=ahead)).date()
        if not _fires_on(schedule, day):
            continue
        cand = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz)
        if cand > local:
            return cand.astimezone(dt.timezone.utc)
    return None


def is_due(subscription, schedule, now: dt.datetime = None) -> bool:
    """Due when the schedule has fired since we last evaluated this subscription.

    An inactive subscription or schedule is never due. A subscription that has
    never run is due at the first tick after its creation — its first digest is
    bounded by created_at (see window_start), not by the whole archive."""
    if not subscription.get("is_active") or not (schedule or {}).get("is_active"):
        return False
    occ = last_occurrence(schedule, now)
    if occ is None:
        return False
    last = subscription.get("last_run_at")
    return last is None or _aware(last) < occ


def _aware(value):
    """Postgres timestamptz comes back aware; be forgiving if it doesn't."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def window_start(subscription) -> dt.datetime:
    """Lower bound (exclusive) of the ingest window for the next digest."""
    return (_aware(subscription.get("last_cursor"))
            or _aware(subscription.get("created_at"))
            or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))


def describe_schedule(schedule, lang="el") -> str:
    """Human label for a schedule, e.g. 'Καθημερινά 08:00 (Europe/Athens)'."""
    if not schedule:
        return "—"
    t = (lambda s: _i18n.translate(s, lang))
    hhmm = f"{int(schedule.get('hour') or 0):02d}:{int(schedule.get('minute') or 0):02d}"
    cadence = schedule.get("cadence")
    if cadence == "weekly":
        days_el = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη",
                   "Παρασκευή", "Σάββατο", "Κυριακή")
        what = f"{t('Εβδομαδιαία')} — {t(days_el[int(schedule.get('weekday') or 0)])}"
    elif cadence == "monthly":
        what = f"{t('Μηνιαία')} — {t('ημέρα')} {int(schedule.get('day_of_month') or 1)}"
    elif cadence == "weekdays":
        what = t("Εργάσιμες")
    else:
        what = t("Καθημερινά")
    return f"{what} {hhmm} ({schedule.get('tz') or DEFAULT_TZ})"


# --------------------------------------------------------------------------- #
# Schedules — DB helpers
# --------------------------------------------------------------------------- #
def list_schedules(c):
    c.execute("""SELECT * FROM proc.digest_schedule
                 ORDER BY is_default DESC, lower(name)""")
    return c.fetchall()


def get_schedule(c, sid):
    c.execute("SELECT * FROM proc.digest_schedule WHERE id = %s", (sid,))
    return c.fetchone()


def default_schedule(c):
    c.execute("SELECT * FROM proc.digest_schedule WHERE is_default LIMIT 1")
    return c.fetchone()


def create_schedule(c, *, name, cadence, hour, minute, weekday=None,
                    day_of_month=None, tz=DEFAULT_TZ, is_default=False,
                    created_by=None):
    cadence = _check_cadence(cadence)
    weekday = int(weekday) if cadence == "weekly" and weekday not in (None, "") else None
    dom = int(day_of_month) if cadence == "monthly" and day_of_month not in (None, "") else None
    if is_default:
        c.execute("UPDATE proc.digest_schedule SET is_default = false WHERE is_default")
    c.execute("""INSERT INTO proc.digest_schedule
                   (name, cadence, hour, minute, weekday, day_of_month, tz,
                    is_default, created_by)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
              (name.strip(), cadence, int(hour), int(minute), weekday, dom,
               (tz or DEFAULT_TZ).strip(), bool(is_default), created_by))
    return c.fetchone()["id"]


def update_schedule(c, sid, *, name, cadence, hour, minute, weekday=None,
                    day_of_month=None, tz=DEFAULT_TZ, is_active=True,
                    is_default=False):
    cadence = _check_cadence(cadence)
    weekday = int(weekday) if cadence == "weekly" and weekday not in (None, "") else None
    dom = int(day_of_month) if cadence == "monthly" and day_of_month not in (None, "") else None
    if is_default:
        c.execute("UPDATE proc.digest_schedule SET is_default = false "
                  "WHERE is_default AND id <> %s", (sid,))
    c.execute("""UPDATE proc.digest_schedule
                 SET name=%s, cadence=%s, hour=%s, minute=%s, weekday=%s,
                     day_of_month=%s, tz=%s, is_active=%s, is_default=%s,
                     updated_at=now()
                 WHERE id=%s""",
              (name.strip(), cadence, int(hour), int(minute), weekday, dom,
               (tz or DEFAULT_TZ).strip(), bool(is_active), bool(is_default), sid))


def delete_schedule(c, sid):
    """Subscriptions pointing here fall back to the portal default (FK is
    ON DELETE SET NULL) rather than disappearing with the schedule."""
    c.execute("DELETE FROM proc.digest_schedule WHERE id = %s", (sid,))


def _check_cadence(cadence):
    cadence = (cadence or "").strip().lower()
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence: {cadence!r}")
    return cadence


# --------------------------------------------------------------------------- #
# Subscriptions — DB helpers
# --------------------------------------------------------------------------- #
_SUB_COLS = """
    ds.*,
    u.username, u.email, u.is_active AS user_active,
    sp.name AS profile_name, sp.scope AS profile_scope,
    sch.id  AS sched_id, sch.name AS sched_name
"""
_SUB_FROM = """
    FROM proc.digest_subscription ds
    JOIN proc.app_user u        ON u.id  = ds.user_id
    JOIN proc.search_profile sp ON sp.id = ds.search_profile_id
    LEFT JOIN proc.digest_schedule sch ON sch.id = ds.schedule_id
"""


def list_subscriptions(c, user_id=None):
    sql = f"SELECT {_SUB_COLS} {_SUB_FROM}"
    args = ()
    if user_id:
        sql += " WHERE ds.user_id = %s"
        args = (user_id,)
    sql += " ORDER BY lower(u.username), lower(sp.name)"
    c.execute(sql, args)
    return c.fetchall()


def get_subscription(c, sub_id):
    c.execute(f"SELECT {_SUB_COLS} {_SUB_FROM} WHERE ds.id = %s", (sub_id,))
    return c.fetchone()


def upsert_subscription(c, *, user_id, search_profile_id, schedule_id=None,
                        is_active=True, send_empty=False, max_results=25,
                        lang="el", created_by=None):
    """One subscription per (customer, profile) — a repeat save edits it."""
    lang = lang if lang in _i18n.SUPPORTED else "el"
    c.execute("""INSERT INTO proc.digest_subscription
                   (user_id, search_profile_id, schedule_id, is_active,
                    send_empty, max_results, lang, created_by)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (user_id, search_profile_id) DO UPDATE
                   SET schedule_id = EXCLUDED.schedule_id,
                       is_active   = EXCLUDED.is_active,
                       send_empty  = EXCLUDED.send_empty,
                       max_results = EXCLUDED.max_results,
                       lang        = EXCLUDED.lang,
                       updated_at  = now()
                 RETURNING id""",
              (user_id, search_profile_id, schedule_id, bool(is_active),
               bool(send_empty), int(max_results), lang, created_by))
    return c.fetchone()["id"]


def delete_subscription(c, sub_id):
    c.execute("DELETE FROM proc.digest_subscription WHERE id = %s", (sub_id,))


def resolve_schedule(c, subscription):
    """The schedule that governs this subscription: its own override, else the
    portal default. None when neither exists (nothing to fire on)."""
    sid = subscription.get("schedule_id")
    if sid:
        s = get_schedule(c, sid)
        if s:
            return s
    return default_schedule(c)


def active_subscriptions(c):
    """Every candidate for a scheduled run: active subscription, active account,
    an address to send to. Whether each is DUE is decided per schedule."""
    c.execute(f"""SELECT {_SUB_COLS} {_SUB_FROM}
                  WHERE ds.is_active AND u.is_active
                    AND coalesce(u.email, '') <> ''
                  ORDER BY ds.id""")
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Run history
# --------------------------------------------------------------------------- #
def record_run(c, *, subscription_id, trigger, status, n_results=0,
               cursor_from=None, cursor_to=None, recipient=None, subject=None,
               error=None):
    c.execute("""INSERT INTO proc.digest_run
                   (subscription_id, trigger, status, n_results, cursor_from,
                    cursor_to, recipient, subject, error, finished_at)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) RETURNING id""",
              (subscription_id, trigger, status, n_results, cursor_from,
               cursor_to, recipient, subject, (error or None)))
    return c.fetchone()["id"]


def list_runs(c, subscription_id=None, limit=50):
    sql = """SELECT r.*, u.username, sp.name AS profile_name
             FROM proc.digest_run r
             LEFT JOIN proc.digest_subscription ds ON ds.id = r.subscription_id
             LEFT JOIN proc.app_user u        ON u.id  = ds.user_id
             LEFT JOIN proc.search_profile sp ON sp.id = ds.search_profile_id"""
    args = []
    if subscription_id:
        sql += " WHERE r.subscription_id = %s"
        args.append(subscription_id)
    sql += " ORDER BY r.started_at DESC LIMIT %s"
    args.append(int(limit))
    c.execute(sql, args)
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Finding the new acts
# --------------------------------------------------------------------------- #
def _main():
    """app.main imported lazily: it owns build_where and the label dicts, but it
    also opens the DB pool at import, and importing it at module scope would make
    this module unusable from anything that isn't the web app."""
    try:
        from app import main as m
    except ImportError:                  # pragma: no cover — run with --app-dir=app
        import main as m                 # type: ignore
    return m


DIGEST_COLS = """
    a.adam, a.type, a.title, a.submission_date, a.final_submission_date,
    a.total_cost_with_vat, a.ingested_at, a.cancelled,
    a.contract_type_code, a.nuts_code,
    auth.name AS authority_name
"""


def new_acts(c, params, since, until, limit=25):
    """Acts matching the profile's filters that were ingested in (since, until].

    Returns (rows, total) — total is the honest count of everything in the
    window, so an email showing the first 25 can say how many more there are.
    The window bound is exclusive at the bottom and inclusive at the top, so
    consecutive runs tile the timeline with no gap and no overlap."""
    where, args = _main().build_where(params or {})
    window = " AND a.ingested_at > %s AND a.ingested_at <= %s"
    c.execute(f"""SELECT count(*) AS n
                  FROM proc.procurement_act a
                  WHERE {where}{window}""", list(args) + [since, until])
    total = c.fetchone()["n"]
    if not total:
        return [], 0
    c.execute(f"""SELECT {DIGEST_COLS}
                  FROM proc.procurement_act a
                  LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                  WHERE {where}{window}
                  ORDER BY a.ingested_at DESC, a.adam
                  LIMIT %s""", list(args) + [since, until, int(limit)])
    return c.fetchall(), total


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
# A standalone Jinja environment: the digest renders from cron too, where there
# is no FastAPI Jinja2Templates instance and no request to build a context from.
_env = Environment(loader=FileSystemLoader(os.path.join(APP_DIR, "templates")),
                   autoescape=select_autoescape(["html", "xml"]))


def _fmt_money(value, lang="el"):
    if value in (None, ""):
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    whole = f"{n:,.2f}"
    if lang == "el":                     # 1.234.567,89
        whole = whole.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{whole} €"


def _fmt_date(value):
    if not value:
        return "—"
    try:
        return value.strftime("%d/%m/%Y")
    except AttributeError:
        return str(value)[:10]


def intro_html(c, subscription, profile, customer):
    """Subject + intro fragment from the 'digest' email template, with the
    [[field]] tokens resolved. Falls back to a built-in line if an admin has
    deleted the template — a missing row must not stop the send."""
    lang = subscription.get("lang") or "el"
    tpl = _auth.get_email_template(c, "digest", lang)
    # Same [[field]] vocabulary as the CRM builder (crm.merge_values), plus the
    # one field only a digest has: which saved search this is about.
    prof = _auth.get_profile(c, customer["id"]) or {}
    values = {k: (prof.get(k) or "") for k in _auth.PROFILE_FIELDS}
    values.update({"username": customer.get("username") or "",
                   "email": customer.get("email") or "",
                   "profile_name": profile.get("name") or ""})
    if not values.get("full_name"):
        values["full_name"] = customer.get("username") or ""
    if not tpl:
        fallback = ("<p>Νέα αποτελέσματα για το προφίλ <strong>{p}</strong>.</p>"
                    if lang == "el" else
                    "<p>New results for your saved search <strong>{p}</strong>.</p>")
        return (values["profile_name"], fallback.format(p=values["profile_name"]))
    # resolve_fields HTML-escapes what it substitutes, which is right for the
    # body but wrong for the subject — that is a plain-text header, so a profile
    # named "Καύσιμα & πετρελαιοειδή" would arrive as "... &amp; ...".
    subject = _html.unescape(
        _strip_markers(_email.resolve_fields(tpl.get("subject") or "", values)))
    body = _strip_markers(_email.resolve_fields(tpl.get("body_html") or "", values))
    return subject, body


# '@@token' plus the whitespace that follows it — removing the token alone
# would leave a stray leading space in front of the salutation.
_MARKER = re.compile(r"@@[A-Za-z0-9_]+[ \t]*")


def _strip_markers(html):
    """Remove any @@token left in the body.

    In the CRM builder that marker deliberately survives the merge — a human
    replaces it before sending. A digest has no human in the loop, so a stray
    '@@greeting' pasted in from a CRM body would go out to the customer as-is."""
    return _MARKER.sub("", html or "").strip()


def render_digest(*, subscription, profile, params, rows, total, since, until,
                  intro, subject):
    """The email's HTML + plain-text alternative. The text part is derived from
    the HTML (same source, so the two can never drift)."""
    lang = subscription.get("lang") or "el"
    t = (lambda s: _i18n.translate(s, lang))
    # The "see all results" link replays the EFFECTIVE filters, so a profile that
    # lives off a portal profile links to the same set it was mailed about.
    qs = _sp.params_to_qs(params or {})
    type_labels = _main().TYPE_LABELS
    items = []
    for r in rows:
        items.append({
            "adam": r["adam"],
            "title": r.get("title") or r["adam"],
            "url": f"{base_url()}/act/{r['adam']}",
            "type_label": _i18n.enum_label("type", r.get("type"), type_labels, lang),
            "authority": r.get("authority_name") or "—",
            "value": _fmt_money(r.get("total_cost_with_vat"), lang),
            "published": _fmt_date(r.get("submission_date")),
            "deadline": _fmt_date(r.get("final_submission_date")),
            "cancelled": bool(r.get("cancelled")),
        })
    search_url = f"{base_url()}/?{qs}" if qs else base_url()
    html = _env.get_template("email_digest.html").render(
        t=t, lang=lang, intro=intro, items=items, total=total,
        shown=len(items), profile_name=profile.get("name") or "",
        search_url=search_url,
        account_url=f"{base_url()}/account",
        since=_fmt_date(since), until=_fmt_date(until),
        base=base_url(), subject=subject)
    return html, _plain_digest(t=t, intro=intro, items=items, total=total,
                               since=since, until=until,
                               profile_name=profile.get("name") or "",
                               search_url=search_url)


def _plain_digest(*, t, intro, items, total, since, until, profile_name,
                  search_url):
    """The text/plain alternative, written out rather than derived from the HTML.

    email_builder.to_plain_text only walks <p>/<ul>/<ol>, which is right for the
    CRM bodies but would silently drop this email's entire results table — the
    part that matters. The intro fragment still goes through it, so the wording
    reads the same in both parts."""
    lines = [_email.to_plain_text(intro)]
    if total:
        head = f"{total} {t('νέες πράξεις')} · {_fmt_date(since)} – {_fmt_date(until)}"
        if len(items) < total:
            head += f"\n{t('Εμφανίζονται οι πρώτες')} {len(items)}."
        lines.append(head)
    else:
        lines.append(f"{t('Καμία νέα πράξη σε αυτό το διάστημα.')} "
                     f"({_fmt_date(since)} – {_fmt_date(until)})")
    for it in items:
        lines.append(
            f"{it['type_label']}{' · ' + t('Ακυρωμένη') if it['cancelled'] else ''}\n"
            f"{it['title']}\n"
            f"{it['authority']}\n"
            f"{t('Προϋπολογισμός')}: {it['value']} · {t('Δημοσίευση')}: {it['published']}"
            + (f" · {t('Προθεσμία')}: {it['deadline']}"
               if it['deadline'] and it['deadline'] != "—" else "")
            + f"\n{it['adam']}\n{it['url']}")
    if items:
        lines.append(f"{t('Δείτε όλα τα αποτελέσματα')}: {search_url}")
    why = t("Λαμβάνετε αυτό το μήνυμα επειδή έχει οριστεί ειδοποίηση για το "
            "προφίλ αναζήτησης")
    lines.append(f"{why} “{profile_name}”.")
    return "\n\n".join(x for x in lines if x)


# --------------------------------------------------------------------------- #
# Running one subscription
# --------------------------------------------------------------------------- #
def build(c, subscription, *, now=None, since=None):
    """Everything needed to send (or preview) one digest, without sending.

    Returns a dict: subject, html, text, rows, total, since, until, recipient.
    `since` overrides the stored cursor — that is how the preview shows a useful
    sample ("last 7 days") for a subscription that has just been created."""
    now = now or dt.datetime.now(dt.timezone.utc)
    profile = _auth.get_search_profile(c, subscription["search_profile_id"])
    if not profile:
        raise ValueError("the subscription's search profile no longer exists")
    params = _auth.effective_params(c, profile)
    # get_customer is scoped to role='customer'; fall back for an admin account
    # subscribed to their own profile (which is how you test the feature).
    customer = _auth.get_customer(c, subscription["user_id"])
    if not customer:
        c.execute("SELECT id, username, email FROM proc.app_user WHERE id = %s",
                  (subscription["user_id"],))
        customer = c.fetchone()
    start = since or window_start(subscription)
    rows, total = new_acts(c, params, start, now,
                           limit=subscription.get("max_results") or 25)
    subject, intro = intro_html(c, subscription, profile, customer)
    html, text = render_digest(subscription=subscription, profile=profile,
                               params=params, rows=rows, total=total,
                               since=start, until=now, intro=intro,
                               subject=subject)
    return {"subject": subject, "html": html, "text": text, "rows": rows,
            "total": total, "since": start, "until": now,
            "recipient": _mailer.address_for(customer),
            "profile": profile, "customer": customer, "params": params}


def run_subscription(c, subscription, *, trigger="schedule", now=None,
                     since=None, advance=True, to=None):
    """Build and send one digest, recording the attempt either way.

    `advance` moves the subscription's cursor forward; a test send leaves it
    alone so it cannot swallow results the customer has not been mailed yet.
    Never raises for an ordinary failure — a broken subscription records an
    'error' run and the sweep carries on to the next one."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sub_id = subscription["id"]
    try:
        built = build(c, subscription, now=now, since=since)
    except Exception as exc:             # noqa: BLE001 — recorded, not raised
        record_run(c, subscription_id=sub_id, trigger=trigger, status="error",
                   error=f"{type(exc).__name__}: {exc}")
        _touch(c, sub_id, now, advance=False)
        return {"status": "error", "error": str(exc), "n": 0}

    recipient = (to or built["recipient"] or "").strip()
    empty = built["total"] == 0

    if empty and not subscription.get("send_empty") and trigger != "test":
        record_run(c, subscription_id=sub_id, trigger=trigger, status="empty",
                   n_results=0, cursor_from=built["since"], cursor_to=now,
                   recipient=recipient, subject=built["subject"])
        # The cursor still advances: nothing matched in this window, and
        # re-scanning it next time would only find the same nothing.
        _touch(c, sub_id, now, advance=advance, sent=False)
        return {"status": "empty", "n": 0}

    try:
        sent = _mailer.send(to=recipient, subject=built["subject"],
                            html=built["html"], text=built["text"],
                            headers={"X-KHMDHS-Digest": str(sub_id)})
    except _mailer.MailError as exc:
        record_run(c, subscription_id=sub_id, trigger=trigger, status="error",
                   n_results=built["total"], cursor_from=built["since"],
                   cursor_to=now, recipient=recipient, subject=built["subject"],
                   error=str(exc))
        # Do NOT advance on a send failure — the next run must retry this window.
        _touch(c, sub_id, now, advance=False)
        return {"status": "error", "error": str(exc), "n": built["total"]}

    record_run(c, subscription_id=sub_id, trigger=trigger, status="sent",
               n_results=built["total"], cursor_from=built["since"],
               cursor_to=now, recipient=sent["to"], subject=built["subject"])
    _touch(c, sub_id, now, advance=advance, sent=True)
    return {"status": "sent", "n": built["total"], "to": sent["to"],
            "backend": sent["backend"], "detail": sent["detail"]}


def _touch(c, sub_id, now, *, advance=True, sent=False):
    """Record that we evaluated this subscription. last_run_at gates dueness;
    last_cursor is the ingest high-water mark and only moves when the window was
    actually consumed."""
    sets = ["last_run_at = %s", "updated_at = now()"]
    args = [now]
    if advance:
        sets.append("last_cursor = %s")
        args.append(now)
    if sent:
        sets.append("last_sent_at = %s")
        args.append(now)
    args.append(sub_id)
    c.execute(f"UPDATE proc.digest_subscription SET {', '.join(sets)} WHERE id = %s",
              args)


def run_due(c, *, now=None, force=False, limit=None):
    """One sweep: send every subscription whose schedule has fired since we last
    looked. `force` ignores dueness (the admin's "run now"). Returns a summary."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"checked": 0, "sent": 0, "empty": 0, "errors": 0, "skipped": 0,
           "results": []}
    for sub in active_subscriptions(c):
        if limit is not None and out["checked"] >= limit:
            break
        out["checked"] += 1
        schedule = resolve_schedule(c, sub)
        if not force and not is_due(sub, schedule, now):
            out["skipped"] += 1
            continue
        res = run_subscription(c, sub, trigger=("manual" if force else "schedule"),
                               now=now)
        out[{"sent": "sent", "empty": "empty", "error": "errors"}[res["status"]]] += 1
        out["results"].append({"subscription_id": sub["id"],
                               "username": sub.get("username"),
                               "profile": sub.get("profile_name"), **res})
    return out


# --------------------------------------------------------------------------- #
# Admin UI — /admin/digests
# --------------------------------------------------------------------------- #
def make_router(templates: Jinja2Templates, cursor) -> APIRouter:
    router = APIRouter(prefix="/admin/digests", tags=["digests"])

    def _admin(request):
        u = getattr(request.state, "user", None)
        if not (u and u.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="admins only")
        return u

    def _page(request, c, tab="subscriptions", flash=None):
        # The cadence labels are built in Python, so they need the page's
        # language explicitly — a template's t() cannot reach inside them.
        lang = _i18n.lang_from_request(request)
        subs = list_subscriptions(c)
        schedules = list_schedules(c)
        by_id = {s["id"]: s for s in schedules}
        default = next((s for s in schedules if s["is_default"]), None)
        now = dt.datetime.now(dt.timezone.utc)
        for s in subs:
            sched = by_id.get(s["schedule_id"]) or default
            s["schedule_label"] = describe_schedule(sched, lang)
            s["inherited"] = s["schedule_id"] is None
            s["next_run_at"] = next_occurrence(sched, now) if sched else None
            s["due"] = is_due(s, sched, now) if sched else False
        for s in schedules:
            s["label"] = describe_schedule(s, lang)
            s["next_run_at"] = next_occurrence(s, now)
        c.execute("""SELECT u.id, u.username, u.email
                     FROM proc.app_user u
                     WHERE u.role = 'customer' AND u.is_active
                     ORDER BY lower(u.username)""")
        customers = c.fetchall()
        c.execute("""SELECT sp.id, sp.name, sp.scope, sp.owner_user_id,
                            u.username AS owner_username
                     FROM proc.search_profile sp
                     LEFT JOIN proc.app_user u ON u.id = sp.owner_user_id
                     ORDER BY sp.scope, lower(sp.name)""")
        profiles = c.fetchall()
        return templates.TemplateResponse(request, "admin_digests.html", {
            "admin_tab": "digests", "tab": tab, "subs": subs,
            "schedules": schedules, "customers": customers,
            "profiles": profiles, "cadences": CADENCES,
            "runs": list_runs(c, limit=50), "mail": _mailer.describe(),
            "default_schedule": default, "flash": flash,
            "base_url": base_url()})

    @router.get("", response_class=HTMLResponse)
    def index(request: Request, tab: str = "subscriptions", flash: str = ""):
        _admin(request)
        with cursor() as c:
            return _page(request, c, tab=tab, flash=flash or None)

    # ---- schedules --------------------------------------------------------- #
    @router.post("/schedules")
    async def save_schedule(request: Request,
                            id: str = Form(""), name: str = Form(...),
                            cadence: str = Form("daily"),
                            hour: str = Form("8"), minute: str = Form("0"),
                            weekday: str = Form(""), day_of_month: str = Form(""),
                            tz: str = Form(DEFAULT_TZ),
                            is_default: str = Form(""),
                            is_active: str = Form("on")):
        admin = _admin(request)
        if not (name or "").strip():
            raise HTTPException(400, "name is required")
        try:
            with cursor() as c:
                if (id or "").strip():
                    update_schedule(c, int(id), name=name, cadence=cadence,
                                    hour=hour or 0, minute=minute or 0,
                                    weekday=weekday, day_of_month=day_of_month,
                                    tz=tz, is_active=bool(is_active),
                                    is_default=bool(is_default))
                else:
                    create_schedule(c, name=name, cadence=cadence,
                                    hour=hour or 0, minute=minute or 0,
                                    weekday=weekday, day_of_month=day_of_month,
                                    tz=tz, is_default=bool(is_default),
                                    created_by=admin["id"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _back("schedules")

    @router.post("/schedules/{sid}/delete")
    async def remove_schedule(sid: int, request: Request):
        _admin(request)
        with cursor() as c:
            delete_schedule(c, sid)
        return _back("schedules")

    # ---- subscriptions ----------------------------------------------------- #
    @router.post("/subscriptions")
    async def save_subscription(request: Request,
                                user_id: str = Form(...),
                                search_profile_id: str = Form(...),
                                schedule_id: str = Form(""),
                                lang: str = Form("el"),
                                max_results: str = Form("25"),
                                send_empty: str = Form(""),
                                is_active: str = Form("on")):
        admin = _admin(request)
        with cursor() as c:
            profile = _auth.get_search_profile(c, int(search_profile_id))
            if not profile:
                raise HTTPException(404, "search profile not found")
            # A customer-scoped profile belongs to exactly one customer;
            # subscribing anyone else to it would mail them someone else's saved
            # search. Portal profiles are shared and may go to anybody.
            if (profile["scope"] == "customer"
                    and profile["owner_user_id"] != int(user_id)):
                raise HTTPException(400, "that profile belongs to another customer")
            upsert_subscription(
                c, user_id=int(user_id), search_profile_id=int(search_profile_id),
                schedule_id=int(schedule_id) if (schedule_id or "").strip() else None,
                is_active=bool(is_active), send_empty=bool(send_empty),
                max_results=max(1, min(200, int(max_results or 25))),
                lang=lang, created_by=admin["id"])
        return _back("subscriptions")

    @router.post("/subscriptions/{sub_id}/delete")
    async def remove_subscription(sub_id: int, request: Request):
        _admin(request)
        with cursor() as c:
            delete_subscription(c, sub_id)
        return _back("subscriptions")

    @router.get("/subscriptions/{sub_id}/preview", response_class=HTMLResponse)
    def preview(sub_id: int, request: Request, days: int = 7):
        """The exact HTML the customer would receive, over the last `days` of
        ingestion — so a brand-new subscription still shows something."""
        _admin(request)
        with cursor() as c:
            sub = get_subscription(c, sub_id)
            if not sub:
                raise HTTPException(404, "subscription not found")
            now = dt.datetime.now(dt.timezone.utc)
            since = now - dt.timedelta(days=max(1, min(365, days)))
            built = build(c, sub, now=now, since=since)
        return HTMLResponse(built["html"])

    @router.post("/subscriptions/{sub_id}/send")
    async def send_now(sub_id: int, request: Request, mode: str = Form("test"),
                       to: str = Form("")):
        """mode=test  — send the last 7 days to `to` (default: the admin), cursor
                        untouched.
           mode=real  — the real thing: the customer's window, cursor advances."""
        admin = _admin(request)
        with cursor() as c:
            sub = get_subscription(c, sub_id)
            if not sub:
                raise HTTPException(404, "subscription not found")
            if mode == "real":
                res = run_subscription(c, sub, trigger="manual")
            else:
                now = dt.datetime.now(dt.timezone.utc)
                res = run_subscription(
                    c, sub, trigger="test", now=now,
                    since=now - dt.timedelta(days=7), advance=False,
                    to=(to or "").strip() or admin.get("email") or sub.get("email"))
        detail = res.get("detail") or res.get("error") or ""
        return _back("runs", flash=f"{res['status']}: {res.get('n', 0)} {detail}")

    # ---- sweep ------------------------------------------------------------- #
    @router.post("/run")
    async def run_now(request: Request, force: str = Form("")):
        _admin(request)
        with cursor() as c:
            out = run_due(c, force=bool(force))
        return _back("runs", flash=(f"checked {out['checked']} · sent {out['sent']} "
                                    f"· empty {out['empty']} · errors {out['errors']}"))

    def _back(tab, flash=None):
        url = f"/admin/digests?tab={tab}"
        if flash:
            from urllib.parse import quote
            url += f"&flash={quote(flash)}"
        return RedirectResponse(url=url, status_code=303)

    return router


# --------------------------------------------------------------------------- #
# Background scheduler (local dev / single-container deploys)
# --------------------------------------------------------------------------- #
def run_loop(stop_event, cursor_factory, poll_seconds=60.0):
    """Tick every `poll_seconds` and send whatever has come due.

    The web process runs this in a daemon thread when DIGEST_SCHEDULER=1, which
    is what makes the whole feature testable with no cron service and no paid
    plan. In a multi-container deployment run cron_digests.py instead and leave
    this off, or two containers will race for the same subscriptions."""
    import traceback
    while not stop_event.is_set():
        try:
            with cursor_factory() as c:
                out = run_due(c)
            if out["sent"] or out["errors"]:
                print(f"[digests] sent={out['sent']} empty={out['empty']} "
                      f"errors={out['errors']}", flush=True)
        except Exception:                # noqa: BLE001 — a tick must never kill the thread
            traceback.print_exc()
        stop_event.wait(poll_seconds)
