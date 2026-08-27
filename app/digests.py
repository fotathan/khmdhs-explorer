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

Who may be mailed
-----------------
Only a customer with a CURRENT grant — status 'tester' or 'subscriber'. An
expired tester, a lapsed subscriber and a prospective lead are CRM records, not
customers, and a subscription someone set up months ago must not keep mailing
them. The gate is `is_entitled` (the same status expression the CRM segments
by); it is applied both when the sweep picks candidates and again inside
run_subscription, so the admin's "send now" button is not a way around it.

The window
----------
Always `procurement_act.ingested_at`, never the act's own dates. An act signed
last month but published to KHMDHS today must reach the customer today, and
ingested_at is the only column that records when it became visible to us.

last_cursor is the high-water mark, and it moves ONLY when an email actually
went out. So the window is literally "everything ingested since the last message
you received": an empty run, a refused run and a failed send all leave it where
it was, and nothing can be consumed by a run nobody was told about.

Who reads it
------------
A subscription mails the customer's own account address (unless
include_primary is turned off) plus every row in proc.digest_recipient — the
colleagues who want the same results. Each extra recipient carries its own
salutation and name, and the intro is re-resolved per person, so the message
greets whoever is reading it rather than the account holder. One send is
therefore N messages; it counts as sent when at least one of them left, and the
addresses that failed are recorded on the run.

Which body
----------
subscription.layout picks the shape:

    list     — prints the new acts (up to max_results), the original digest
    summary  — prints how many acts of each type, what they are worth and where
               they came from, and links out to the full set

Both take their subject and intro wording from proc.email_template — slug
'digest' and 'digest_summary' — so either can be reworded without a deploy.

What one send contained
-----------------------
Every run that mails writes its matched acts to proc.digest_run_item — the whole
window, not just the max_results the message lists — and carries an unguessable
token. The email's "see all results" button opens /digests/<token>, which
renders exactly those acts. Replaying the profile's filters instead would drift:
clicked two days later, the same search returns a different set.

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
  proc.email_template slugs 'digest' / 'digest_summary' — subject + intro
                     wording, editable at /admin/email-templates so copy
                     changes need no deploy
  app/templates/email_digest.html          — the results table around that intro
  app/templates/email_digest_summary.html  — the statistics version
  cron_digests.py  — the entry point a scheduler (or you) invokes
  /admin/digests   — schedules (the portal-wide settings), a read-only overview
                     of every subscription, and the run history
  /admin/crm/<id>  — the per-customer settings: which saved searches they are
                     mailed about, on what cadence, and the send/test buttons
  /digests/<token> — the result set one email contained (owner or admin only)
"""
from __future__ import annotations

import datetime as dt
import html as _html
import os
import re
import secrets
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

# The two shapes of digest body, and the email_template slug each takes its
# subject + intro from. Adding a third is a template, a slug and an entry here.
LAYOUTS = ("list", "summary")
LAYOUT_SLUGS = {"list": "digest", "summary": "digest_summary"}
LAYOUT_TEMPLATES = {"list": "email_digest.html",
                    "summary": "email_digest_summary.html"}

# How many authorities the summary body names before it stops. Beyond a handful
# the list stops being a summary.
SUMMARY_TOP_N = 5

# Who may be mailed. A subscription is not permission: an expired tester, a
# lapsed subscriber and a prospective lead are all CRM records, and mailing them
# results is either a leak of a paid product or a message to someone who never
# asked for one. The list mirrors auth.ENTITLED_STATUSES so the gate and the CRM
# segment tabs can never disagree about what "active customer" means.
ENTITLED_STATUSES = _auth.ENTITLED_STATUSES

# Upper bound on how many acts one run records. The email lists at most
# max_results; the rest exist so /digests/<token> can show the whole window
# ("see ALL results"). A pathological window (a brand-new subscription with a
# very wide profile) must still not write a million rows in one transaction.
ITEM_CAP = max(1, int(os.environ.get("DIGEST_ITEM_CAP") or 2000))

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
_SUB_COLS = f"""
    ds.*,
    u.username, u.email, u.is_active AS user_active, u.role AS user_role,
    sp.name AS profile_name, sp.scope AS profile_scope,
    sch.id  AS sched_id, sch.name AS sched_name,
    (SELECT count(*) FROM proc.digest_recipient dr
      WHERE dr.subscription_id = ds.id AND dr.is_active) AS n_extra_recipients,
    {_auth.SEGMENT_CASE_SQL} AS customer_status
"""
# The customer_profile / current-subscription joins are here only to feed the
# status expression above — the same one the CRM segments by. `p` and `s` are
# the alias names that expression hard-codes.
_SUB_FROM = f"""
    FROM proc.digest_subscription ds
    JOIN proc.app_user u        ON u.id  = ds.user_id
    JOIN proc.search_profile sp ON sp.id = ds.search_profile_id
    LEFT JOIN proc.digest_schedule sch ON sch.id = ds.schedule_id
    LEFT JOIN proc.customer_profile p ON p.user_id = u.id
    {_auth.CURRENT_SUB_JOIN_SQL}
"""


def is_entitled(subscription) -> bool:
    """Whether this subscription's customer may be mailed at all right now.

    Independent of the subscription's own is_active flag and of dueness: this
    is about the ACCOUNT. A digest set up while someone was a tester must stop
    the day their grant lapses, without an admin having to remember.

    Admins are always entitled, exactly as auth.load_user grants them access
    without a subscription: an admin subscribed to their own profile is how the
    feature gets exercised on a real cadence, and it is not a leak of anything
    they cannot already read."""
    sub = subscription or {}
    if sub.get("user_role") == "admin":
        return True
    return sub.get("customer_status") in ENTITLED_STATUSES


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
                        lang="el", layout="list", include_primary=True,
                        created_by=None):
    """One subscription per (customer, profile) — a repeat save edits it.

    The extra recipients are NOT touched here: they are edited one row at a
    time (add_recipient / delete_recipient), and a save of the subscription's
    cadence must not silently drop the colleagues someone added."""
    lang = lang if lang in _i18n.SUPPORTED else "el"
    layout = check_layout(layout)
    c.execute("""INSERT INTO proc.digest_subscription
                   (user_id, search_profile_id, schedule_id, is_active,
                    send_empty, max_results, lang, layout, include_primary,
                    created_by)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (user_id, search_profile_id) DO UPDATE
                   SET schedule_id     = EXCLUDED.schedule_id,
                       is_active       = EXCLUDED.is_active,
                       send_empty      = EXCLUDED.send_empty,
                       max_results     = EXCLUDED.max_results,
                       lang            = EXCLUDED.lang,
                       layout          = EXCLUDED.layout,
                       include_primary = EXCLUDED.include_primary,
                       updated_at      = now()
                 RETURNING id""",
              (user_id, search_profile_id, schedule_id, bool(is_active),
               bool(send_empty), int(max_results), lang, layout,
               bool(include_primary), created_by))
    return c.fetchone()["id"]


def check_layout(layout):
    layout = (layout or "").strip().lower() or "list"
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout!r}")
    return layout


# --------------------------------------------------------------------------- #
# Recipients — who one subscription is mailed to
# --------------------------------------------------------------------------- #
# The account address is implicit (subscription.include_primary); these rows are
# the people ADDED to it. They are not app_user rows on purpose: a colleague who
# should receive the results does not thereby get a login, and giving them one
# would be a different, chargeable thing.
def list_recipients(c, subscription_id, *, active_only=False):
    sql = """SELECT id, subscription_id, email, salutation, first_name,
                    last_name, ord, is_active, created_at
             FROM proc.digest_recipient WHERE subscription_id = %s"""
    if active_only:
        sql += " AND is_active"
    sql += " ORDER BY ord, id"
    c.execute(sql, (subscription_id,))
    return c.fetchall()


def add_recipient(c, subscription_id, *, email, salutation=None, first_name=None,
                  last_name=None, is_active=True, created_by=None):
    """Add (or, for an address already on the list, update) one reader.

    The address is validated here rather than at send time: a typo discovered
    three days later, in a run history nobody is reading, is a digest silently
    not delivered."""
    email = (email or "").strip()
    if not _mailer.valid_address(email):
        raise ValueError(f"invalid email address: {email!r}")
    c.execute("""INSERT INTO proc.digest_recipient
                   (subscription_id, email, salutation, first_name, last_name,
                    is_active, ord, created_by)
                 VALUES (%s,%s,%s,%s,%s,%s,
                         coalesce((SELECT max(ord) + 1 FROM proc.digest_recipient
                                    WHERE subscription_id = %s), 0),
                         %s)
                 ON CONFLICT (subscription_id, lower(btrim(email))) DO UPDATE
                   SET salutation = EXCLUDED.salutation,
                       first_name = EXCLUDED.first_name,
                       last_name  = EXCLUDED.last_name,
                       is_active  = EXCLUDED.is_active
                 RETURNING id""",
              (subscription_id, email, _clean(salutation), _clean(first_name),
               _clean(last_name), bool(is_active), subscription_id, created_by))
    return c.fetchone()["id"]


def delete_recipient(c, recipient_id, subscription_id=None):
    """Remove one reader. `subscription_id` scopes the delete to the
    subscription the request came from, so a guessed id cannot reach another
    customer's list."""
    sql = "DELETE FROM proc.digest_recipient WHERE id = %s"
    args = [recipient_id]
    if subscription_id is not None:
        sql += " AND subscription_id = %s"
        args.append(subscription_id)
    c.execute(sql, args)


def _clean(value):
    value = (value or "").strip()
    return value or None


def _primary_recipient(customer):
    """The account holder as a recipient record: the address plus whatever name
    the CRM profile has, filled in by the merge from the profile itself."""
    return {"email": _mailer.address_for(customer), "salutation": None,
            "first_name": None, "last_name": None, "is_primary": True}


def recipients_for(c, subscription, customer, *, to=None):
    """Everyone this send goes to, in order, each with the name to greet.

    `to` is the test-send override: one explicit address, and none of the
    customer's real readers. Addresses are de-duplicated case-insensitively —
    the same mailbox listed as both the account address and an extra recipient
    is one person, and must not receive two copies."""
    if (to or "").strip():
        return [{"email": to.strip(), "salutation": None, "first_name": None,
                 "last_name": None, "is_primary": True}]
    out, seen = [], set()
    if subscription.get("include_primary", True):
        primary = _primary_recipient(customer)
        if primary["email"]:
            out.append(primary)
            seen.add(primary["email"].lower())
    for row in list_recipients(c, subscription["id"], active_only=True):
        addr = (row.get("email") or "").strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append({"email": addr, "salutation": row.get("salutation"),
                    "first_name": row.get("first_name"),
                    "last_name": row.get("last_name"), "is_primary": False})
    return out


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
    """Every candidate for a scheduled run: an active subscription on an active
    account that is ENTITLED (a live tester or subscriber) and has SOMEONE to
    mail. Whether each is DUE is then decided per schedule.

    "Someone to mail" is the account address or a named recipient — an agency
    account with no mailbox of its own but three named readers is a perfectly
    ordinary arrangement, and used to be filtered out here.

    The entitlement test is in the WHERE clause rather than in Python so the
    sweep never even loads a lapsed customer — an ineligible subscription is not
    a skipped run, it is not a candidate."""
    c.execute(f"""SELECT * FROM (
                    SELECT {_SUB_COLS} {_SUB_FROM}
                    WHERE ds.is_active AND u.is_active
                      AND ((ds.include_primary AND coalesce(u.email, '') <> '')
                           OR EXISTS (SELECT 1 FROM proc.digest_recipient dr
                                       WHERE dr.subscription_id = ds.id
                                         AND dr.is_active))
                  ) q
                  WHERE q.user_role = 'admin' OR q.customer_status = ANY(%s)
                  ORDER BY q.id""", (list(ENTITLED_STATUSES),))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Run history
# --------------------------------------------------------------------------- #
def new_token() -> str:
    """The handle in the email's 'see all results' link. Unguessable, and the
    only thing the URL carries — /digests/<token> still requires the owner to be
    logged in, so a leaked link is not a leaked result set."""
    return secrets.token_urlsafe(24)


def record_run(c, *, subscription_id, trigger, status, n_results=0,
               cursor_from=None, cursor_to=None, recipient=None, subject=None,
               error=None, token=None, n_recipients=0):
    """`recipient` is every address the run reached, joined for display;
    n_recipients is how many they were. The count is stored rather than derived
    by splitting the string, which would break on a display name containing a
    comma."""
    c.execute("""INSERT INTO proc.digest_run
                   (subscription_id, trigger, status, n_results, cursor_from,
                    cursor_to, recipient, subject, error, token, n_recipients,
                    finished_at)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) RETURNING id""",
              (subscription_id, trigger, status, n_results, cursor_from,
               cursor_to, recipient, subject, (error or None), token,
               int(n_recipients or 0)))
    return c.fetchone()["id"]


def record_run_items(c, run_id, matched, shown=0):
    """Write down exactly which acts this run covered.

    `matched` is every act in the window (already capped at ITEM_CAP); `shown`
    is how many of them the email itself listed, in the same order. Storing both
    is what lets the results page honour "see ALL results" when max_results
    truncated the message — and what makes the set replayable later, after the
    profile has been edited or the act re-ingested."""
    rows = [(run_id, r["adam"], i, i < shown, r.get("ingested_at"))
            for i, r in enumerate(matched or [])]
    if not rows:
        return 0
    c.executemany("""INSERT INTO proc.digest_run_item
                       (run_id, adam, ord, in_email, ingested_at)
                     VALUES (%s,%s,%s,%s,%s)
                     ON CONFLICT (run_id, adam) DO NOTHING""", rows)
    return len(rows)


def get_run_by_token(c, token):
    """One run addressed by its link token, with everything the results page
    needs to authorise the viewer and title the page."""
    c.execute("""SELECT r.*, ds.user_id, ds.lang AS sub_lang,
                        ds.search_profile_id, sp.name AS profile_name,
                        u.username
                 FROM proc.digest_run r
                 JOIN proc.digest_subscription ds ON ds.id = r.subscription_id
                 LEFT JOIN proc.search_profile sp ON sp.id = ds.search_profile_id
                 LEFT JOIN proc.app_user u        ON u.id  = ds.user_id
                 WHERE r.token = %s""", (token,))
    return c.fetchone()


def run_item_count(c, run_id):
    """How many of the run's recorded acts still exist."""
    c.execute("""SELECT count(*) AS n
                 FROM proc.digest_run_item ri
                 JOIN proc.procurement_act a ON a.adam = ri.adam
                 WHERE ri.run_id = %s""", (run_id,))
    return c.fetchone()["n"]


def run_item_acts(c, run_id, limit=None, offset=0):
    """The run's recorded acts, joined back to the live act rows and in the
    order the email listed them.

    An act deleted since the send simply drops out (the FK cascades), which is
    the honest answer — the page shows what still exists of what was sent.
    `limit` pages the results view: one run may hold up to ITEM_CAP acts, and
    rendering two thousand cards in one response is a several-megabyte page."""
    sql = f"""SELECT {_main().SELECT_COLS}, ri.in_email, ri.ord
              FROM proc.digest_run_item ri
              JOIN proc.procurement_act a ON a.adam = ri.adam
              LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
              WHERE ri.run_id = %s
              ORDER BY ri.ord, ri.id"""
    args = [run_id]
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        args += [int(limit), int(offset)]
    c.execute(sql, args)
    return c.fetchall()


def list_runs(c, subscription_id=None, limit=50):
    sql = """SELECT r.*, u.username, sp.name AS profile_name,
                    ds.user_id AS customer_id
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


def new_acts(c, params, since, until, limit=25, cap=None):
    """Acts matching the profile's filters that were ingested in (since, until].

    Returns (shown, total, matched):
      matched — every match in the window, up to `cap` rows. This is what the
                run records, and what /digests/<token> lists.
      shown   — the first `limit` of those: what the email itself prints.
      total   — the honest count of the whole window, so a truncated email can
                say how many more there are.

    Fetching `matched` once and slicing it means the email and the results page
    are guaranteed to agree on order and content — they are the same query.

    The window bound is exclusive at the bottom and inclusive at the top, so
    consecutive runs tile the timeline with no gap and no overlap."""
    cap = max(int(limit), int(cap or ITEM_CAP))
    where, args = _main().build_where(params or {})
    window = " AND a.ingested_at > %s AND a.ingested_at <= %s"
    c.execute(f"""SELECT count(*) AS n
                  FROM proc.procurement_act a
                  WHERE {where}{window}""", list(args) + [since, until])
    total = c.fetchone()["n"]
    if not total:
        return [], 0, []
    c.execute(f"""SELECT {DIGEST_COLS}
                  FROM proc.procurement_act a
                  LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                  WHERE {where}{window}
                  ORDER BY a.ingested_at DESC, a.adam
                  LIMIT %s""", list(args) + [since, until, cap])
    matched = c.fetchall()
    return matched[:int(limit)], total, matched


def window_stats(c, params, since, until):
    """The numbers the summary body prints, over the WHOLE ingest window.

    Computed in SQL, not from the rows the run recorded: `matched` stops at
    ITEM_CAP, and a summary that reports 2000 acts when 5000 matched is worse
    than no summary at all. Three aggregates over the same window the list
    digest already counts, on the same index.

    Returns {total, value, authorities, cancelled, open_deadlines,
             next_deadline, by_type[], top_authorities[]}. by_type is in count
    order, so the biggest thing that happened is the first line the reader sees.
    `until` doubles as "now" for the deadline questions — the message is about
    that instant, and a deadline that passed before it was sent is not open.
    """
    where, args = _main().build_where(params or {})
    window = " AND a.ingested_at > %s AND a.ingested_at <= %s"

    c.execute(f"""SELECT count(*) AS total,
                         coalesce(sum(a.total_cost_with_vat), 0) AS value,
                         count(DISTINCT a.authority_id) AS authorities,
                         count(*) FILTER (WHERE a.cancelled) AS cancelled,
                         count(*) FILTER (
                             WHERE a.final_submission_date > %s) AS open_deadlines,
                         min(a.final_submission_date) FILTER (
                             WHERE a.final_submission_date > %s) AS next_deadline
                  FROM proc.procurement_act a
                  WHERE {where}{window}""",
              [until, until] + list(args) + [since, until])
    stats = dict(c.fetchone() or {})

    c.execute(f"""SELECT a.type, count(*) AS n,
                         coalesce(sum(a.total_cost_with_vat), 0) AS value,
                         count(*) FILTER (WHERE a.cancelled) AS cancelled
                  FROM proc.procurement_act a
                  WHERE {where}{window}
                  GROUP BY a.type
                  ORDER BY count(*) DESC, a.type""",
              list(args) + [since, until])
    stats["by_type"] = [dict(r) for r in c.fetchall()]

    c.execute(f"""SELECT auth.name AS name, count(*) AS n,
                         coalesce(sum(a.total_cost_with_vat), 0) AS value
                  FROM proc.procurement_act a
                  LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
                  WHERE {where}{window}
                  GROUP BY auth.name
                  ORDER BY count(*) DESC, auth.name
                  LIMIT %s""",
              list(args) + [since, until, SUMMARY_TOP_N])
    stats["top_authorities"] = [dict(r) for r in c.fetchall()]
    return stats


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


def merge_values(c, subscription, profile, customer, recipient=None):
    """The [[field]] vocabulary one digest resolves against.

    Same fields as the CRM builder (crm.merge_values), plus the two only a
    digest has: which saved search this is about, and WHO is reading it. An
    extra recipient is a different person from the account holder, so their name
    overrides full_name — otherwise a colleague's copy opens by greeting the
    customer."""
    prof = _auth.get_profile(c, customer["id"]) or {}
    values = {k: (prof.get(k) or "") for k in _auth.PROFILE_FIELDS}
    values.update({"username": customer.get("username") or "",
                   "email": customer.get("email") or "",
                   "profile_name": profile.get("name") or ""})
    if not values.get("full_name"):
        values["full_name"] = customer.get("username") or ""
    rcp = recipient or {}
    first = (rcp.get("first_name") or "").strip()
    last = (rcp.get("last_name") or "").strip()
    named = " ".join(part for part in (first, last) if part)
    values["salutation"] = (rcp.get("salutation") or "").strip()
    values["first_name"] = first
    values["last_name"] = last
    values["recipient_name"] = named or values["full_name"]
    if rcp.get("email"):
        values["email"] = rcp["email"]
    if named:
        values["full_name"] = named
    return values


def intro_html(c, subscription, profile, customer, recipient=None):
    """Subject + intro fragment from this layout's email template, with the
    [[field]] tokens resolved for `recipient`. Falls back to a built-in line if
    an admin has deleted the template — a missing row must not stop the send."""
    lang = subscription.get("lang") or "el"
    slug = LAYOUT_SLUGS.get(subscription.get("layout") or "list", "digest")
    tpl = _auth.get_email_template(c, slug, lang)
    values = merge_values(c, subscription, profile, customer, recipient)
    if not tpl:
        fallback = ("<p>Νέα αποτελέσματα για το προφίλ <strong>{p}</strong>.</p>"
                    if lang == "el" else
                    "<p>New results for your saved search <strong>{p}</strong>.</p>")
        return (values["profile_name"], fallback.format(p=values["profile_name"]))
    # _soft_resolve HTML-escapes what it substitutes, which is right for the
    # body but wrong for the subject — that is a plain-text header, so a profile
    # named "Καύσιμα & πετρελαιοειδή" would arrive as "... &amp; ...".
    subject = _html.unescape(
        _strip_markers(_soft_resolve(tpl.get("subject") or "", values)))
    body = _strip_markers(_soft_resolve(tpl.get("body_html") or "", values))
    return subject, body


# Whitespace left behind by a token that resolved to nothing: a doubled space,
# a space hard against the tag that opened the line, or one in front of the
# comma that followed the name.
_GAP = re.compile(r"[ \t]{2,}")
_AFTER_TAG = re.compile(r">[ \t]+")
_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?·])")
# ... and the comma a vanished name left stranded at the start of its line.
_ORPHAN_PUNCT = re.compile(r">[ \t]*[,.;:·]+[ \t]*")


def _soft_resolve(html, values):
    """Resolve [[field]] tokens, dropping the ones with no value.

    email_builder.resolve_fields REFUSES to render a body with an empty field,
    which is right for the CRM builder: a human is about to send that message
    and must fill the hole. A digest has no human in the loop, and its greeting
    fields are optional per recipient — a colleague listed with an address and
    no name is an ordinary row, and it must not stop a scheduled send for
    everyone else on the list. So an empty token drops out here, and the gap it
    leaves is closed: "[[salutation]] [[first_name]]," with neither set has to
    arrive as "," removed, not as "  ,".

    An admin who mistypes a token sees the result in Προεπισκόπηση, which
    renders exactly what would be sent."""
    def substitute(match):
        value = (values or {}).get(match.group(1))
        if value is None or not str(value).strip():
            return ""
        return _html.escape(str(value), quote=True)

    out = _email.FIELD_TOKEN.sub(substitute, html or "")
    out = _BEFORE_PUNCT.sub(r"\1", _AFTER_TAG.sub(">", _GAP.sub(" ", out)))
    return _ORPHAN_PUNCT.sub(">", out)


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
                  intro, subject, token=None, stats=None):
    """One recipient's email: HTML plus the plain-text alternative.

    Which body depends on subscription.layout — 'list' prints the acts,
    'summary' prints the statistics and links out. Both take the same `intro`
    (already resolved for this reader) and the same "see all results" target, so
    switching a customer between them changes the shape of the message and
    nothing else."""
    lang = subscription.get("lang") or "el"
    t = (lambda s: _i18n.translate(s, lang))
    qs = _sp.params_to_qs(params or {})
    type_labels = _main().TYPE_LABELS
    layout = subscription.get("layout") or "list"
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
    # "See all results" opens THIS email's own result set (/digests/<token>),
    # not a live re-run of the filters: by the time it is clicked, replaying the
    # search would return a different set — including acts the customer has not
    # been told about, and missing ones they have. The live search stays the
    # fallback for a preview, which has no run to point at.
    search_url = f"{base_url()}/?{qs}" if qs else base_url()
    results_url = f"{base_url()}/digests/{token}" if token else search_url
    common = dict(t=t, lang=lang, intro=intro, total=total,
                  profile_name=profile.get("name") or "",
                  search_url=search_url, results_url=results_url,
                  account_url=f"{base_url()}/account",
                  since=_fmt_date(since), until=_fmt_date(until),
                  base=base_url(), subject=subject)

    if layout == "summary":
        summary = _summary_view(stats or {}, type_labels, lang, t)
        html = _env.get_template(LAYOUT_TEMPLATES["summary"]).render(
            **common, stats=summary)
        return html, _plain_summary(t=t, intro=intro, stats=summary, total=total,
                                    since=since, until=until,
                                    profile_name=profile.get("name") or "",
                                    results_url=results_url)

    html = _env.get_template(LAYOUT_TEMPLATES["list"]).render(
        **common, items=items, shown=len(items))
    return html, _plain_digest(t=t, intro=intro, items=items, total=total,
                               since=since, until=until,
                               profile_name=profile.get("name") or "",
                               search_url=results_url)


def _summary_view(stats, type_labels, lang, t):
    """window_stats() turned into display strings.

    Formatting here rather than in the template so the HTML body and the
    text/plain alternative print the same numbers the same way — the two are
    rendered separately, and a €-format that lives in only one of them is a bug
    nobody sees until a customer forwards the plain part."""
    total = int(stats.get("total") or 0)
    rows = []
    for row in stats.get("by_type") or []:
        n = int(row.get("n") or 0)
        rows.append({
            "label": _i18n.enum_label("type", row.get("type"), type_labels, lang),
            "n": n,
            "value": _fmt_money(row.get("value"), lang),
            "cancelled": int(row.get("cancelled") or 0),
            # Bar width in the HTML body: this type's share of the window,
            # so the bars read as "what this digest is mostly made of".
            "share": (round(100.0 * n / total) if total else 0),
        })
    top = [{"name": (r.get("name") or "—"), "n": int(r.get("n") or 0),
            "value": _fmt_money(r.get("value"), lang)}
           for r in (stats.get("top_authorities") or [])]
    return {
        "total": total,
        "value": _fmt_money(stats.get("value"), lang),
        "authorities": int(stats.get("authorities") or 0),
        "cancelled": int(stats.get("cancelled") or 0),
        "open_deadlines": int(stats.get("open_deadlines") or 0),
        "next_deadline": _fmt_date(stats.get("next_deadline")),
        "by_type": rows, "top_authorities": top,
    }


def _plain_summary(*, t, intro, stats, total, since, until, profile_name,
                   results_url):
    """The text/plain alternative of the summary body — the same figures, in the
    same order, without the table."""
    lines = [_email.to_plain_text(intro)]
    if total:
        lines.append(f"{total} {t('νέες πράξεις')} · {_fmt_date(since)} – {_fmt_date(until)}")
        block = [f"{t('Συνολικός προϋπολογισμός')}: {stats['value']}",
                 f"{t('Αναθέτουσες αρχές')}: {stats['authorities']}"]
        if stats["open_deadlines"]:
            block.append(f"{t('Ανοιχτές προθεσμίες')}: {stats['open_deadlines']} "
                         f"({t('επόμενη')} {stats['next_deadline']})")
        if stats["cancelled"]:
            block.append(f"{t('Ακυρωμένες')}: {stats['cancelled']}")
        lines.append("\n".join(block))
        lines.append("\n".join(
            f"{row['label']}: {row['n']} · {row['value']}"
            for row in stats["by_type"]))
        if stats["top_authorities"]:
            lines.append(f"{t('Κυριότερες αναθέτουσες')}:\n" + "\n".join(
                f"{a['name']}: {a['n']} · {a['value']}"
                for a in stats["top_authorities"]))
        lines.append(f"{t('Δείτε όλα τα αποτελέσματα')}: {results_url}")
    else:
        lines.append(f"{t('Καμία νέα πράξη σε αυτό το διάστημα.')} "
                     f"({_fmt_date(since)} – {_fmt_date(until)})")
    why = t("Λαμβάνετε αυτό το μήνυμα επειδή έχει οριστεί ειδοποίηση για το "
            "προφίλ αναζήτησης")
    lines.append(f"{why} “{profile_name}”.")
    return "\n\n".join(x for x in lines if x)


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
def build(c, subscription, *, now=None, since=None, token=None, to=None):
    """Everything needed to send (or preview) one digest, without sending.

    The window is queried ONCE and every recipient's message is rendered from
    it: the acts, the counts and the "see all results" link are identical for
    everyone, and only the greeting differs. Returns a dict with

      messages    — one {to, subject, html, text, recipient} per reader, in
                    send order. Never empty: a subscription with nobody on it
                    still renders (the preview has to show something), which is
                    why the caller checks `recipients` and not this.
      recipients  — who this send is really for. EMPTY means nobody: no account
                    address and no active extra, which is an error, not a send.
      rows/matched/total — what the email lists, the whole window (recorded as
                    the run's items), and the honest count.

    `since` overrides the stored cursor — that is how the preview shows a useful
    sample ("last 7 days") for a subscription that has just been created.
    `to` is the test-send override: one address instead of the real list.
    `token` is the run handle the "see all results" button points at; a preview
    passes none and falls back to a live search link."""
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
    rows, total, matched = new_acts(
        c, params, start, now, limit=subscription.get("max_results") or 25)
    # The summary body needs figures the recorded rows cannot give (they stop at
    # ITEM_CAP), so it costs three extra aggregates — only when it is the body
    # actually being sent.
    stats = (window_stats(c, params, start, now)
             if (subscription.get("layout") or "list") == "summary" else None)

    people = recipients_for(c, subscription, customer, to=to)
    messages = []
    for person in (people or [_primary_recipient(customer)]):
        subject, intro = intro_html(c, subscription, profile, customer, person)
        html, text = render_digest(subscription=subscription, profile=profile,
                                   params=params, rows=rows, total=total,
                                   since=start, until=now, intro=intro,
                                   subject=subject, token=token, stats=stats)
        messages.append({"to": person["email"], "recipient": person,
                         "subject": subject, "html": html, "text": text})
    first = messages[0]
    return {"subject": first["subject"], "html": first["html"],
            "text": first["text"], "messages": messages, "recipients": people,
            "rows": rows, "matched": matched, "total": total, "stats": stats,
            "since": start, "until": now, "recipient": first["to"],
            "profile": profile, "customer": customer, "params": params}


def run_subscription(c, subscription, *, trigger="schedule", now=None,
                     since=None, advance=True, to=None):
    """Build and send one digest, recording the attempt either way.

    `advance` allows the subscription's cursor to move; a test send passes False
    so it cannot swallow results the customer has not been mailed yet. Even with
    advance=True the cursor only moves when a message actually LEFT — see
    _touch. Never raises for an ordinary failure: a broken subscription records
    an 'error' run and the sweep carries on to the next one.

    A real send to a customer who is no longer entitled is refused here, not
    only in active_subscriptions: the sweep filters them out, but the admin's
    "send now" button reaches this function directly and must not become a way
    around the gate. A test send is exempt — it goes to the admin.

    One run is one message PER RECIPIENT. It counts as sent as soon as one of
    them left: the window has then been mailed, and re-sending it later so a
    bounced colleague could get it would put the whole set in front of everyone
    else a second time. The addresses that failed are recorded on the run
    instead, which is where an admin looks for them."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sub_id = subscription["id"]

    if trigger != "test" and not is_entitled(subscription):
        status = subscription.get("customer_status") or "unknown"
        record_run(c, subscription_id=sub_id, trigger=trigger, status="skipped",
                   cursor_from=window_start(subscription), cursor_to=now,
                   recipient=(subscription.get("email") or None),
                   error=f"customer is not an active tester or subscriber ({status})")
        # last_run_at moves so the sweep does not re-evaluate this every tick;
        # the cursor does NOT, so whatever accumulates while they are lapsed is
        # still there to mail the day they are re-granted.
        _touch(c, sub_id, now, advance=False)
        return {"status": "skipped", "n": 0, "customer_status": status,
                "error": f"not entitled ({status})"}

    # Minted before the build so the message can link to its own results page.
    # A run that ends up mailing nothing simply never stores it.
    token = new_token()
    try:
        built = build(c, subscription, now=now, since=since, token=token, to=to)
    except Exception as exc:             # noqa: BLE001 — recorded, not raised
        record_run(c, subscription_id=sub_id, trigger=trigger, status="error",
                   error=f"{type(exc).__name__}: {exc}")
        _touch(c, sub_id, now, advance=False)
        return {"status": "error", "error": str(exc), "n": 0}

    people = built["recipients"]
    addresses = ", ".join(p["email"] for p in people)
    empty = built["total"] == 0

    if empty and not subscription.get("send_empty") and trigger != "test":
        record_run(c, subscription_id=sub_id, trigger=trigger, status="empty",
                   n_results=0, cursor_from=built["since"], cursor_to=now,
                   recipient=addresses or None, subject=built["subject"])
        # No mail left the building, so the cursor stays put: the window is
        # defined as "since the last email we actually sent you". Re-scanning an
        # empty window costs one indexed count and keeps that promise literal.
        _touch(c, sub_id, now, advance=False, sent=False)
        return {"status": "empty", "n": 0}

    # Nobody to mail. Turning include_primary off and then removing the last
    # named recipient leaves a subscription that would run for ever, sending to
    # no one; say so on the run rather than reporting a successful send of
    # nothing.
    if not people:
        error = "no recipient: the account has no address and no reader is listed"
        record_run(c, subscription_id=sub_id, trigger=trigger, status="error",
                   n_results=built["total"], cursor_from=built["since"],
                   cursor_to=now, subject=built["subject"], error=error)
        _touch(c, sub_id, now, advance=False)
        return {"status": "error", "error": error, "n": built["total"]}

    delivered, failures, last = [], [], None
    for message in built["messages"]:
        try:
            last = _mailer.send(to=message["to"], subject=message["subject"],
                                html=message["html"], text=message["text"],
                                headers={"X-KHMDHS-Digest": str(sub_id)})
            delivered.append(last["to"])
        except _mailer.MailError as exc:
            failures.append(f"{message['to']}: {exc}")

    if not delivered:
        record_run(c, subscription_id=sub_id, trigger=trigger, status="error",
                   n_results=built["total"], cursor_from=built["since"],
                   cursor_to=now, recipient=addresses, subject=built["subject"],
                   error="; ".join(failures))
        # Do NOT advance on a send failure — the next run must retry this window.
        _touch(c, sub_id, now, advance=False)
        return {"status": "error", "error": "; ".join(failures),
                "n": built["total"]}

    run_id = record_run(c, subscription_id=sub_id, trigger=trigger, status="sent",
                        n_results=built["total"], cursor_from=built["since"],
                        cursor_to=now, recipient=", ".join(delivered),
                        n_recipients=len(delivered), subject=built["subject"],
                        error=("; ".join(failures) or None), token=token)
    record_run_items(c, run_id, built["matched"], shown=len(built["rows"]))
    _touch(c, sub_id, now, advance=advance, sent=True)
    return {"status": "sent", "n": built["total"], "to": delivered[0],
            "recipients": delivered, "failed": failures,
            "backend": last["backend"], "detail": last["detail"],
            "run_id": run_id, "token": token}


def _touch(c, sub_id, now, *, advance=True, sent=False):
    """Record that we evaluated this subscription.

    last_run_at gates dueness and moves on every evaluation. last_cursor is the
    ingest high-water mark and moves ONLY when an email actually went out
    (`advance and sent`) — that is what makes "everything since your last email"
    literally true, and what stops an empty run, a refused run or a failed send
    from quietly consuming a window nobody was told about."""
    sets = ["last_run_at = %s", "updated_at = now()"]
    args = [now]
    if advance and sent:
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
    # "skipped" = not due yet. "blocked" = the customer is no longer entitled;
    # active_subscriptions already filters those out, so it should stay 0 — it
    # is here so a future caller passing its own list cannot lose the count.
    out = {"checked": 0, "sent": 0, "empty": 0, "errors": 0, "skipped": 0,
           "blocked": 0, "results": []}
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
        out[{"sent": "sent", "empty": "empty", "error": "errors",
             "skipped": "blocked"}[res["status"]]] += 1
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
            # Entitlement first: a lapsed customer is never "due", whatever the
            # cadence says, and the overview must show that rather than a
            # pending badge for mail that will never be sent.
            s["entitled"] = is_entitled(s)
            s["due"] = bool(s["entitled"] and sched and is_due(s, sched, now))
        for s in schedules:
            s["label"] = describe_schedule(s, lang)
            s["next_run_at"] = next_occurrence(s, now)
        return templates.TemplateResponse(request, "admin_digests.html", {
            "admin_tab": "digests", "tab": tab, "subs": subs,
            "schedules": schedules, "cadences": CADENCES,
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
    # These endpoints are shared with the CRM customer page, which is where the
    # FORMS live (one customer's alerts belong on that customer's card; a single
    # portal-wide list stops scaling the moment there are more than a handful).
    # `back` is the page to return to, so the same endpoint serves both.
    @router.post("/subscriptions")
    async def save_subscription(request: Request,
                                user_id: str = Form(...),
                                search_profile_id: str = Form(...),
                                schedule_id: str = Form(""),
                                lang: str = Form("el"),
                                max_results: str = Form("25"),
                                send_empty: str = Form(""),
                                # Checkbox semantics: an unchecked box posts
                                # NOTHING, so the default has to be the "off"
                                # value. With Form("on") here, unticking
                                # "Ενεργή" on the customer card silently did
                                # nothing — the endpoint could never be told to
                                # deactivate a subscription. Every form that
                                # reaches this endpoint renders all three boxes.
                                is_active: str = Form(""),
                                layout: str = Form("list"),
                                include_primary: str = Form(""),
                                back: str = Form("")):
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
            try:
                upsert_subscription(
                    c, user_id=int(user_id),
                    search_profile_id=int(search_profile_id),
                    schedule_id=(int(schedule_id)
                                 if (schedule_id or "").strip() else None),
                    is_active=bool(is_active), send_empty=bool(send_empty),
                    max_results=max(1, min(200, int(max_results or 25))),
                    lang=lang, layout=layout,
                    include_primary=bool(include_primary),
                    created_by=admin["id"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return _back("subscriptions", back=back)

    # ---- recipients -------------------------------------------------------- #
    # Edited one row at a time, from the customer card. Separate endpoints (not
    # a repeated field on the subscription form) so adding a colleague cannot
    # accidentally rewrite the cadence, and removing one is a single click with
    # nothing else in flight.
    @router.post("/subscriptions/{sub_id}/recipients")
    async def add_sub_recipient(sub_id: int, request: Request,
                                email: str = Form(...),
                                salutation: str = Form(""),
                                first_name: str = Form(""),
                                last_name: str = Form(""),
                                back: str = Form("")):
        admin = _admin(request)
        with cursor() as c:
            if not get_subscription(c, sub_id):
                raise HTTPException(404, "subscription not found")
            try:
                add_recipient(c, sub_id, email=email, salutation=salutation,
                              first_name=first_name, last_name=last_name,
                              created_by=admin["id"])
            except ValueError as exc:
                return _back("subscriptions", flash=str(exc), back=back)
        return _back("subscriptions", back=back)

    @router.post("/subscriptions/{sub_id}/recipients/{rid}/delete")
    async def remove_sub_recipient(sub_id: int, rid: int, request: Request,
                                   back: str = Form("")):
        _admin(request)
        with cursor() as c:
            delete_recipient(c, rid, subscription_id=sub_id)
        return _back("subscriptions", back=back)

    @router.post("/subscriptions/{sub_id}/delete")
    async def remove_subscription(sub_id: int, request: Request,
                                  back: str = Form("")):
        _admin(request)
        with cursor() as c:
            delete_subscription(c, sub_id)
        return _back("subscriptions", back=back)

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
                       to: str = Form(""), back: str = Form("")):
        """mode=test  — send the last 7 days to `to` (default: the admin), cursor
                        untouched, and allowed whatever the customer's status is
                        (the message goes to the admin, not to them).
           mode=real  — the real thing: the customer's window, and refused with a
                        'skipped' run if they are no longer entitled."""
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
        return _back("runs", flash=f"{res['status']}: {res.get('n', 0)} {detail}",
                     back=back)

    # ---- sweep ------------------------------------------------------------- #
    @router.post("/run")
    async def run_now(request: Request, force: str = Form("")):
        _admin(request)
        with cursor() as c:
            out = run_due(c, force=bool(force))
        return _back("runs", flash=(f"checked {out['checked']} · sent {out['sent']} "
                                    f"· empty {out['empty']} · errors {out['errors']}"))

    def _back(tab, flash=None, back=""):
        """Back to the digests page, or to whatever `back` names — which is how
        the CRM customer page reuses these endpoints. Only a same-site path is
        honoured, so a crafted form cannot turn this into an open redirect."""
        from urllib.parse import quote
        back = (back or "").strip()
        if back.startswith("/") and not back.startswith("//"):
            url = back
            if flash:
                url += ("&" if "?" in url else "?") + "flash=" + quote(flash)
            return RedirectResponse(url=url, status_code=303)
        url = f"/admin/digests?tab={tab}"
        if flash:
            url += "&flash=" + quote(flash)
        return RedirectResponse(url=url, status_code=303)

    return router


# --------------------------------------------------------------------------- #
# The result set one email contained — /digests/<token>
# --------------------------------------------------------------------------- #
def make_results_router(templates: Jinja2Templates, cursor) -> APIRouter:
    """The page the email's "see all results" button opens.

    Not an admin surface and not public either: the token addresses the run, and
    the route then insists the viewer IS the customer that run was for (or an
    admin). A forwarded link therefore shows a stranger a login page, not a
    customer's result set."""
    router = APIRouter(prefix="/digests", tags=["digests"])

    # token_urlsafe alphabet. Anything else cannot be a token, so reject it
    # before touching the database.
    _TOKEN_OK = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

    PER_PAGE = 25

    @router.get("/{token}", response_class=HTMLResponse)
    def digest_results(token: str, request: Request, page: int = 1):
        if not _TOKEN_OK.match(token or ""):
            raise HTTPException(404, "not found")
        user = getattr(request.state, "user", None)
        with cursor() as c:
            run = get_run_by_token(c, token)
            if not run:
                raise HTTPException(404, "not found")
            if not user:
                # Clicked straight out of the mail client: sign in, come back.
                return RedirectResponse(url=f"/login?next=/digests/{token}",
                                        status_code=303)
            if user.get("role") != "admin" and user.get("id") != run["user_id"]:
                raise HTTPException(403, "not your results")
            total = run_item_count(c, run["id"])
            total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
            page = max(1, min(int(page or 1), total_pages))
            rows = run_item_acts(c, run["id"], limit=PER_PAGE,
                                 offset=(page - 1) * PER_PAGE)
        return templates.TemplateResponse(request, "digest_results.html", {
            "run": run, "rows": rows, "total": total,
            "page": page, "total_pages": total_pages, "token": token,
            "profile_name": run.get("profile_name") or "",
            "nav_active": "search",
            # The email's own window, so the page can say what period this was.
            "since": run.get("cursor_from"), "until": run.get("cursor_to"),
            "is_admin_view": (user.get("role") == "admin"
                              and user.get("id") != run["user_id"]),
        })

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
