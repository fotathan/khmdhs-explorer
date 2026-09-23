"""Durable mobile-notification evaluation and Expo delivery worker."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import socket
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from app import auth, digests, mobile_auth, mobile_notifications
from app.push_provider import (
    ExpoPushProvider,
    ProviderPermanentError,
    ProviderTemporaryError,
)

EVALUATION_CAP = max(50, int(os.environ.get("PUSH_EVALUATION_CAP", "2000")))
CLAIM_LIMIT = max(1, min(100, int(os.environ.get("PUSH_CLAIM_LIMIT", "100"))))
LEASE_SECONDS = max(30, int(os.environ.get("PUSH_LEASE_SECONDS", "120")))
RECEIPT_DELAY_MINUTES = max(
    5, int(os.environ.get("PUSH_RECEIPT_DELAY_MINUTES", "15")))
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
# notification_event.expires_at's column default. The worker stamps it itself,
# from the run's `now`, together with created_at (see _event).
EVENT_TTL = dt.timedelta(days=90)


class EvaluationOverflow(RuntimeError):
    pass


@dataclass
class Candidate:
    event_id: int
    event_type: str
    immediate: bool
    newly_created: bool
    priority: int


def _aware(value) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _time(value, fallback: dt.time) -> dt.time:
    if isinstance(value, dt.time):
        return value.replace(tzinfo=None)
    try:
        return dt.time.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def quiet_release(now: dt.datetime, timezone: str, quiet_start,
                  quiet_end) -> dt.datetime:
    """Return now unless it is quiet; otherwise the next local quiet end."""
    tz = ZoneInfo(timezone or "Europe/Athens")
    local = _aware(now).astimezone(tz)
    start = _time(quiet_start, dt.time(22, 0))
    end = _time(quiet_end, dt.time(8, 0))
    current = local.time().replace(tzinfo=None)
    if start == end:
        return _aware(now)
    if start < end:
        inside = start <= current < end
        end_day = local.date()
    else:
        inside = current >= start or current < end
        end_day = local.date() + (dt.timedelta(days=1) if current >= start else dt.timedelta())
    if not inside:
        return _aware(now)
    return dt.datetime.combine(end_day, end, tzinfo=tz).astimezone(dt.timezone.utc)


def _dedupe(*parts) -> bytes:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).digest()


def _copy_context(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _event(c, *, now: dt.datetime, user_id: int, subscription_id: int | None,
           event_type: str, dedupe_key: bytes, target_kind: str, target_id: str,
           adam: str | None, title: str, body: str, profile_name: str | None,
           delivery_mode: str | None, duplicate_of: str | None = None) -> tuple[dict, bool]:
    context = {
        "saved_search_names": [profile_name] if profile_name else [],
        "delivery_modes": [delivery_mode] if delivery_mode else [],
    }
    if duplicate_of:
        # A POSSIBLE duplicate (tsg_match.py): sent, and labelled.
        context["possible_duplicate_of"] = duplicate_of
    # created_at/expires_at come from the run's clock, never the column
    # default: the summary window, the daily cap and the expiry check all
    # measure them against `now`, and the DB's now() is a second clock (another
    # host, and the transaction's start rather than the run's).
    now = _aware(now)
    c.execute(
        """INSERT INTO proc.notification_event
             (user_id,push_subscription_id,event_type,dedupe_key,target_kind,
              target_id,adam,title,body,context,created_at,expires_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING *""",
        (user_id, subscription_id, event_type, dedupe_key, target_kind,
         target_id, adam, title, body, Json(context), now, now + EVENT_TTL),
    )
    row = c.fetchone()
    if row:
        return dict(row), True
    c.execute("SELECT * FROM proc.notification_event WHERE dedupe_key=%s",
              (dedupe_key,))
    row = c.fetchone()
    if not row or int(row["user_id"]) != user_id:
        raise RuntimeError("notification dedupe collision")
    merged = _copy_context(row.get("context"))
    names = [str(v) for v in merged.get("saved_search_names", [])
             if isinstance(v, str)]
    modes = [str(v) for v in merged.get("delivery_modes", [])
             if isinstance(v, str)]
    if profile_name and profile_name not in names:
        names.append(profile_name)
    if delivery_mode and delivery_mode not in modes:
        modes.append(delivery_mode)
    merged["saved_search_names"] = names[:10]
    merged["delivery_modes"] = modes
    c.execute("""UPDATE proc.notification_event SET context=%s
                 WHERE id=%s RETURNING *""", (Json(merged), row["id"]))
    return dict(c.fetchone()), False


def _candidate(store: dict[int, Candidate], row: dict, *, inserted: bool,
               immediate: bool, priority: int) -> None:
    event_id = int(row["id"])
    current = store.get(event_id)
    if current:
        current.immediate = current.immediate or immediate
        current.newly_created = current.newly_created or inserted
        current.priority = min(current.priority, priority)
    else:
        store[event_id] = Candidate(
            event_id, row["event_type"], immediate, inserted, priority)


def _labels(lang: str, event_type: str, lead_days: int | None = None,
            count: int | None = None) -> tuple[str, str]:
    if lang == "en":
        if event_type == "deadline":
            return "Upcoming deadline", f"A matching procurement closes in {lead_days} day(s)."
        if event_type == "daily_summary":
            return "Procurement summary", f"{count or 0} new opportunities are waiting in your inbox."
        return "New opportunity", "A procurement matches one of your saved searches."
    if event_type == "deadline":
        return "Προσεχής προθεσμία", f"Μια σχετική προκήρυξη λήγει σε {lead_days} ημέρα/ημέρες."
    if event_type == "daily_summary":
        return "Σύνοψη διαγωνισμών", f"{count or 0} νέες ευκαιρίες σας περιμένουν στις ειδοποιήσεις."
    return "Νέα ευκαιρία", "Ένας διαγωνισμός ταιριάζει σε αποθηκευμένη αναζήτησή σας."


def _dup_suffix(lang: str, act: dict) -> str:
    """Appended to a push body when the act may repeat another one."""
    if not act.get("dup"):
        return ""
    return " · possible duplicate" if lang == "en" else " · πιθανή διπλοεγγραφή"


def _closing_rows(c, params: dict, now: dt.datetime, max_lead: int) -> list[dict]:
    from app import main

    where, args = main.build_where(params or {})
    c.execute(
        f"""SELECT {digests.DIGEST_COLS}
             FROM proc.procurement_act a
             LEFT JOIN proc.authority auth ON auth.org_id=a.authority_id
             WHERE {where} AND a.type='notice'
               AND coalesce(a.cancelled,false)=false
               AND a.final_submission_date>%s
               AND a.final_submission_date<=%s
             ORDER BY a.final_submission_date,a.adam LIMIT %s""",
        list(args) + [now, now + dt.timedelta(days=max_lead + 2), EVALUATION_CAP + 1],
    )
    rows = c.fetchall()
    if len(rows) > EVALUATION_CAP:
        raise EvaluationOverflow("deadline evaluation cap exceeded")
    rows = [dict(row) for row in rows]
    digests.annotate_duplicates(c, rows)
    return rows


def _due_marks(deadline: dt.datetime, now: dt.datetime, timezone: str,
               lead_days: list[int]) -> list[int]:
    tz = ZoneInfo(timezone or "Europe/Athens")
    days = (_aware(deadline).astimezone(tz).date()
            - _aware(now).astimezone(tz).date()).days
    return sorted((mark for mark in lead_days if days <= mark), reverse=True)


def _deadline_events(c, sub: dict, params: dict, now: dt.datetime,
                     candidates: dict[int, Candidate]) -> int:
    leads = [int(v) for v in sub.get("lead_days") or [7, 1]]
    rows = _closing_rows(c, params, now, max(leads))
    created = 0
    for act in rows:
        deadline = act.get("final_submission_date")
        due = _due_marks(deadline, now, sub["timezone"], leads)
        if not due:
            continue
        c.execute("""SELECT lead_days FROM proc.push_deadline_notice
                     WHERE push_subscription_id=%s AND adam=%s AND deadline_at=%s""",
                  (sub["id"], act["adam"], deadline))
        spent = {int(row["lead_days"]) for row in c.fetchall()}
        fresh = [mark for mark in due if mark not in spent]
        if not fresh:
            continue
        c.executemany(
            """INSERT INTO proc.push_deadline_notice
                 (push_subscription_id,adam,lead_days,deadline_at)
               VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            [(sub["id"], act["adam"], mark, deadline) for mark in fresh],
        )
        lead = min(fresh)
        title, body = _labels(sub["lang"], "deadline", lead_days=lead)
        row, inserted = _event(
            c, now=now, user_id=int(sub["user_id"]), subscription_id=int(sub["id"]),
            event_type="deadline",
            dedupe_key=_dedupe("deadline", sub["user_id"], act["adam"],
                                lead, _aware(deadline).isoformat()),
            target_kind="act", target_id=act["adam"], adam=act["adam"],
            title=title, body=body + _dup_suffix(sub["lang"], act),
            profile_name=sub["profile_name"],
            delivery_mode=sub["delivery_mode"],
            duplicate_of=(act.get("dup") or {}).get("candidate_adam"))
        _candidate(candidates, row, inserted=inserted,
                   immediate=sub["delivery_mode"] == "immediate", priority=0)
        created += int(inserted)
    return created


def _new_match_events(c, sub: dict, params: dict, since: dt.datetime,
                      now: dt.datetime, candidates: dict[int, Candidate]) -> int:
    _shown, total, rows = digests.new_acts(
        c, params, since, now, limit=EVALUATION_CAP, cap=EVALUATION_CAP)
    if int(total) > EVALUATION_CAP:
        raise EvaluationOverflow("new-match evaluation cap exceeded")
    digests.annotate_duplicates(c, rows)
    created = 0
    for act in rows:
        title, body = _labels(sub["lang"], "new_match")
        row, inserted = _event(
            c, now=now, user_id=int(sub["user_id"]), subscription_id=int(sub["id"]),
            event_type="new_match",
            dedupe_key=_dedupe("new_match", sub["user_id"], act["adam"],
                                _aware(act["ingested_at"]).isoformat()),
            target_kind="act", target_id=act["adam"], adam=act["adam"],
            title=title, body=body + _dup_suffix(sub["lang"], act),
            profile_name=sub["profile_name"],
            delivery_mode=sub["delivery_mode"],
            duplicate_of=(act.get("dup") or {}).get("candidate_adam"))
        _candidate(candidates, row, inserted=inserted,
                   immediate=sub["delivery_mode"] == "immediate", priority=1)
        created += int(inserted)
    return created


def _queue_event(c, event_id: int, user_id: int, due_at: dt.datetime,
                 now: dt.datetime) -> bool:
    # created_at is the run's clock: _delivered_events_today counts it against
    # the customer's local day of `now` (the daily cap).
    c.execute(
        """WITH added AS (
             INSERT INTO proc.notification_delivery
                    (event_id,device_id,next_attempt_at,created_at)
             SELECT %s,d.id,%s,%s FROM proc.mobile_device d
              WHERE d.user_id=%s AND d.enabled AND d.revoked_at IS NULL
                AND d.permission_status IN ('granted','provisional')
                AND d.push_token_ciphertext IS NOT NULL
             ON CONFLICT (event_id,device_id) DO NOTHING RETURNING id)
           SELECT count(*) AS n FROM added""", (event_id, due_at, _aware(now), user_id))
    return int(c.fetchone()["n"]) > 0


def _delivered_events_today(c, user_id: int, timezone: str,
                            local_day: dt.date) -> tuple[int, int]:
    c.execute(
        """SELECT count(DISTINCT d.event_id) AS total,
                  count(DISTINCT d.event_id) FILTER (
                    WHERE e.event_type<>'daily_summary') AS individual
           FROM proc.notification_delivery d
           JOIN proc.notification_event e ON e.id=d.event_id
           WHERE e.user_id=%s
             AND (d.created_at AT TIME ZONE %s)::date=%s""",
        (user_id, timezone, local_day),
    )
    row = c.fetchone()
    return int(row["total"] or 0), int(row["individual"] or 0)


def _summary_window(now: dt.datetime, timezone: str, summary_time) -> tuple[
    dt.date, dt.datetime, dt.datetime
] | None:
    """Return the most recent completed customer-local summary window."""
    tz = ZoneInfo(timezone)
    local = _aware(now).astimezone(tz)
    at = _time(summary_time, dt.time(8, 30))
    if local.time().replace(tzinfo=None) < at:
        return None
    end_local = dt.datetime.combine(local.date(), at, tzinfo=tz)
    start_local = dt.datetime.combine(
        local.date() - dt.timedelta(days=1), at, tzinfo=tz)
    return (
        local.date(),
        start_local.astimezone(dt.timezone.utc),
        end_local.astimezone(dt.timezone.utc),
    )


def _summary_count(c, user_id: int, start: dt.datetime,
                   end: dt.datetime) -> int:
    c.execute(
        """SELECT count(*) AS n FROM proc.notification_event
           WHERE user_id=%s AND event_type IN ('new_match','deadline')
             AND created_at>%s AND created_at<=%s
             AND context->'delivery_modes' ? 'daily'""",
        (user_id, start, end),
    )
    return int(c.fetchone()["n"])


def _allocate(c, *, user_id: int, pref: dict, candidates: dict[int, Candidate],
              now: dt.datetime) -> tuple[int, int]:
    if pref["paused"]:
        return 0, 0
    timezone = pref["timezone"]
    local_day = _aware(now).astimezone(ZoneInfo(timezone)).date()
    used, individual_used = _delivered_events_today(c, user_id, timezone, local_day)
    cap = int(pref["daily_cap"])
    release = quiet_release(now, timezone, pref["quiet_start"], pref["quiet_end"])
    queued = overflow = 0
    for candidate in sorted(candidates.values(), key=lambda item: (item.priority, item.event_id)):
        if not candidate.newly_created or not candidate.immediate:
            continue
        if used >= cap or individual_used >= 5:
            overflow += 1
            continue
        if _queue_event(c, candidate.event_id, user_id, release, now):
            queued += 1
            used += 1
            individual_used += 1

    window = _summary_window(now, timezone, pref["summary_time"])
    daily_count = _summary_count(c, user_id, window[1], window[2]) if window else 0
    summary_day = window[0] if daily_count else local_day
    if (overflow or daily_count) and used < cap:
        count = overflow + daily_count
        title, body = _labels(pref["lang"], "daily_summary", count=count)
        row, inserted = _event(
            c, now=now, user_id=user_id, subscription_id=None, event_type="daily_summary",
            dedupe_key=_dedupe("daily_summary", user_id, summary_day),
            target_kind="search_summary", target_id=summary_day.isoformat(), adam=None,
            title=title, body=body, profile_name=None, delivery_mode=None)
        if inserted and _queue_event(c, int(row["id"]), user_id, release, now):
            queued += 1
    return queued, overflow


def _user_subscriptions(c, user_id: int) -> list[dict]:
    c.execute(
        """SELECT ps.*,sp.name AS profile_name,sp.scope AS profile_scope,
                  sp.owner_user_id,sp.is_published,
                  p.paused,p.timezone,p.quiet_start,p.quiet_end,p.summary_time,
                  p.daily_cap,p.lang
           FROM proc.push_subscription ps
           JOIN proc.search_profile sp ON sp.id=ps.search_profile_id
           JOIN proc.mobile_notification_preference p ON p.user_id=ps.user_id
           WHERE ps.user_id=%s AND ps.is_active
           ORDER BY ps.id FOR UPDATE OF ps,p""", (user_id,))
    return [dict(row) for row in c.fetchall()]


def evaluate_user(c, user_id: int, now: dt.datetime) -> dict:
    result = {"subscriptions": 0, "events": 0, "deliveries": 0, "overflow": 0}
    with mobile_auth.transaction(c):
        c.execute("""INSERT INTO proc.mobile_notification_preference (user_id)
                     VALUES (%s) ON CONFLICT (user_id) DO NOTHING""", (user_id,))
        subscriptions = _user_subscriptions(c, user_id)
        result["subscriptions"] = len(subscriptions)
        if not subscriptions:
            return result
        user = auth.load_user(c, user_id)
        if not user or not user.get("has_access"):
            c.execute("""UPDATE proc.push_subscription
                         SET last_cursor=%s,last_evaluated_at=%s,updated_at=now()
                         WHERE user_id=%s AND is_active""", (now, now, user_id))
            return result

        candidates: dict[int, Candidate] = {}
        valid_subscriptions = []
        for sub in subscriptions:
            accessible = (
                sub["profile_scope"] == "customer"
                and int(sub["owner_user_id"] or 0) == user_id
            ) or (sub["profile_scope"] == "portal" and sub["is_published"])
            if not accessible:
                c.execute("""UPDATE proc.push_subscription
                             SET last_cursor=%s,last_evaluated_at=%s,updated_at=now()
                             WHERE id=%s""", (now, now, sub["id"]))
                continue
            valid_subscriptions.append(sub)
            profile = auth.get_search_profile(c, sub["search_profile_id"])
            params = auth.effective_params(c, profile) or {}
            if sub["deadlines"]:
                result["events"] += _deadline_events(c, sub, params, now, candidates)
            if sub["new_matches"]:
                since = _aware(sub.get("last_cursor") or sub.get("reactivated_at")
                               or sub.get("created_at"))
                result["events"] += _new_match_events(
                    c, sub, params, since, now, candidates)
            c.execute("""UPDATE proc.push_subscription
                         SET last_cursor=%s,last_evaluated_at=%s,updated_at=now()
                         WHERE id=%s""", (now, now, sub["id"]))
        if valid_subscriptions:
            pref = valid_subscriptions[0]
            queued, overflow = _allocate(
                c, user_id=user_id, pref=pref, candidates=candidates, now=now)
            result["deliveries"] = queued
            result["overflow"] = overflow
    return result


def evaluate_all(c, now: dt.datetime | None = None, limit: int | None = None) -> dict:
    now = _aware(now or dt.datetime.now(dt.timezone.utc))
    c.execute("""SELECT DISTINCT user_id FROM proc.push_subscription
                 WHERE is_active ORDER BY user_id""")
    user_ids = [int(row["user_id"]) for row in c.fetchall()]
    if limit:
        user_ids = user_ids[:int(limit)]
    totals = {"users": 0, "subscriptions": 0, "events": 0,
              "deliveries": 0, "overflow": 0, "errors": 0}
    for user_id in user_ids:
        try:
            result = evaluate_user(c, user_id, now)
        except Exception:  # caller logs aggregate; never leak profile criteria
            totals["errors"] += 1
            continue
        totals["users"] += 1
        for key in ("subscriptions", "events", "deliveries", "overflow"):
            totals[key] += result[key]
    return totals


def delivery_enabled() -> bool:
    return (os.environ.get("PUSH_DELIVERY_ENABLED") or "0").lower() in {
        "1", "true", "yes", "on"}


def _claim_submissions(c, now: dt.datetime) -> list[dict]:
    with mobile_auth.transaction(c):
        c.execute(
            """WITH picked AS (
                 SELECT d.id FROM proc.notification_delivery d
                 JOIN proc.notification_event e ON e.id=d.event_id
                 JOIN proc.mobile_device md ON md.id=d.device_id
                 WHERE e.expires_at>%s AND md.enabled AND md.revoked_at IS NULL
                   AND md.push_token_ciphertext IS NOT NULL
                   AND ((d.state IN ('queued','retry') AND d.next_attempt_at<=%s)
                     OR (d.state='submitting' AND d.lease_expires_at<=%s))
                 ORDER BY d.next_attempt_at,d.id FOR UPDATE OF d SKIP LOCKED
                 LIMIT %s)
               UPDATE proc.notification_delivery d SET
                 state='submitting',attempt_count=attempt_count+1,
                 claimed_at=%s,claimed_by=%s,
                 lease_expires_at=%s+make_interval(secs=>%s),updated_at=%s
               FROM picked WHERE d.id=picked.id
               RETURNING d.id""",
            (now, now, now, CLAIM_LIMIT, now, WORKER_ID, now, LEASE_SECONDS, now),
        )
        ids = [int(row["id"]) for row in c.fetchall()]
        if not ids:
            return []
        c.execute(
            """SELECT d.*,e.title,e.body,e.id AS notification_event_id,
                      md.push_token_ciphertext,md.push_token_key_version
               FROM proc.notification_delivery d
               JOIN proc.notification_event e ON e.id=d.event_id
               JOIN proc.mobile_device md ON md.id=d.device_id
               WHERE d.id=ANY(%s) ORDER BY d.id""", (ids,))
        return [dict(row) for row in c.fetchall()]


def _retry(c, row: dict, code: str, now: dt.datetime) -> None:
    if int(row["attempt_count"]) >= 8:
        c.execute("""UPDATE proc.notification_delivery SET state='expired',
                     error_code=%s,terminal_at=%s,claimed_at=NULL,claimed_by=NULL,
                     lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                  (code, now, now, row["id"]))
        return
    seconds = min(3600, 15 * (2 ** min(int(row["attempt_count"]), 8)))
    seconds += int(row["id"]) % 17
    c.execute("""UPDATE proc.notification_delivery SET state='retry',
                 error_code=%s,next_attempt_at=%s+make_interval(secs=>%s),
                 claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL,updated_at=%s
                 WHERE id=%s""", (code, now, seconds, now, row["id"]))


def _disable_unregistered(c, device_id: int) -> None:
    c.execute("""UPDATE proc.mobile_device SET permission_status='denied',
                 push_token_ciphertext=NULL,push_token_hash=NULL,
                 push_token_key_version=NULL,updated_at=now() WHERE id=%s""",
              (device_id,))


def submit_due(c, provider=None, now: dt.datetime | None = None) -> dict:
    if not delivery_enabled():
        return {"claimed": 0, "accepted": 0, "retry": 0, "rejected": 0,
                "disabled": True}
    now = _aware(now or dt.datetime.now(dt.timezone.utc))
    rows = _claim_submissions(c, now)
    out = {"claimed": len(rows), "accepted": 0, "retry": 0, "rejected": 0,
           "disabled": False}
    if not rows:
        return out
    messages = []
    valid_rows = []
    for row in rows:
        try:
            token = mobile_notifications.reveal_push_token(
                row["push_token_ciphertext"], row["push_token_key_version"])
        except mobile_notifications.MobilePushConfigurationError:
            c.execute("""UPDATE proc.notification_delivery SET state='rejected',
                         error_code='token_decrypt',terminal_at=%s,updated_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL
                         WHERE id=%s""", (now, now, row["id"]))
            out["rejected"] += 1
            continue
        messages.append({
            "to": token, "title": row["title"], "body": row["body"],
            "data": {"schema_version": 1,
                     "event_id": str(row["notification_event_id"])},
        })
        valid_rows.append(row)
    if not valid_rows:
        return out
    provider = provider or ExpoPushProvider()
    try:
        tickets = provider.send(messages)
    except ProviderTemporaryError as exc:
        code = str(exc)[:64] or "provider_temporary"
        for row in valid_rows:
            _retry(c, row, code, now)
            out["retry"] += 1
        return out
    except ProviderPermanentError as exc:
        code = str(exc)[:64] or "provider_permanent"
        for row in valid_rows:
            c.execute("""UPDATE proc.notification_delivery SET state='rejected',
                         error_code=%s,terminal_at=%s,updated_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL
                         WHERE id=%s""", (code, now, now, row["id"]))
            out["rejected"] += 1
        return out
    for row, ticket in zip(valid_rows, tickets):
        if ticket.accepted:
            c.execute("""UPDATE proc.notification_delivery SET state='accepted',
                         provider_ticket_id=%s,error_code=NULL,submitted_at=%s,
                         next_attempt_at=%s+make_interval(mins=>%s),updated_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL
                         WHERE id=%s""",
                      (ticket.ticket_id, now, now, RECEIPT_DELAY_MINUTES, now,
                       row["id"]))
            out["accepted"] += 1
        elif ticket.retryable:
            _retry(c, row, ticket.error_code or "provider_retry", now)
            out["retry"] += 1
        else:
            code = ticket.error_code or "provider_rejected"
            c.execute("""UPDATE proc.notification_delivery SET state='rejected',
                         error_code=%s,terminal_at=%s,updated_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL
                         WHERE id=%s""", (code, now, now, row["id"]))
            if code == "DeviceNotRegistered":
                _disable_unregistered(c, int(row["device_id"]))
            out["rejected"] += 1
    return out


def _claim_receipts(c, now: dt.datetime) -> list[dict]:
    with mobile_auth.transaction(c):
        c.execute(
            """WITH picked AS (
                 SELECT id FROM proc.notification_delivery
                 WHERE state='accepted' AND terminal_at IS NULL
                   AND provider_ticket_id IS NOT NULL AND next_attempt_at<=%s
                   AND (lease_expires_at IS NULL OR lease_expires_at<=%s)
                 ORDER BY next_attempt_at,id FOR UPDATE SKIP LOCKED LIMIT %s)
               UPDATE proc.notification_delivery d SET claimed_at=%s,claimed_by=%s,
                 lease_expires_at=%s+make_interval(secs=>%s),updated_at=%s
               FROM picked WHERE d.id=picked.id RETURNING d.*""",
            (now, now, CLAIM_LIMIT, now, WORKER_ID, now, LEASE_SECONDS, now),
        )
        return [dict(row) for row in c.fetchall()]


def check_receipts(c, provider=None, now: dt.datetime | None = None) -> dict:
    if not delivery_enabled():
        return {"claimed": 0, "accepted": 0, "retry": 0, "rejected": 0,
                "pending": 0, "disabled": True}
    now = _aware(now or dt.datetime.now(dt.timezone.utc))
    rows = _claim_receipts(c, now)
    out = {"claimed": len(rows), "accepted": 0, "retry": 0, "rejected": 0,
           "pending": 0, "disabled": False}
    if not rows:
        return out
    provider = provider or ExpoPushProvider()
    ids = [row["provider_ticket_id"] for row in rows]
    try:
        results = provider.receipts(ids)
    except ProviderTemporaryError as exc:
        code = str(exc)[:64] or "receipt_provider_error"
        for row in rows:
            c.execute("""UPDATE proc.notification_delivery SET error_code=%s,
                         next_attempt_at=%s+interval '15 minutes',claimed_at=NULL,
                         claimed_by=NULL,lease_expires_at=NULL,updated_at=%s
                         WHERE id=%s""", (code, now, now, row["id"]))
            out["pending"] += 1
        return out
    except ProviderPermanentError as exc:
        code = str(exc)[:64] or "receipt_provider_rejected"
        for row in rows:
            c.execute("""UPDATE proc.notification_delivery SET state='rejected',
                         error_code=%s,receipt_checked_at=%s,terminal_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL,
                         updated_at=%s WHERE id=%s""",
                      (code, now, now, now, row["id"]))
            out["rejected"] += 1
        return out
    for row in rows:
        receipt = results[row["provider_ticket_id"]]
        if receipt.status == "accepted":
            c.execute("""UPDATE proc.notification_delivery SET terminal_at=%s,
                         provider_receipt_id=provider_ticket_id,receipt_checked_at=%s,
                         error_code=NULL,claimed_at=NULL,claimed_by=NULL,
                         lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                      (now, now, now, row["id"]))
            out["accepted"] += 1
        elif receipt.status == "retry":
            c.execute("""UPDATE proc.notification_delivery SET state='retry',
                         provider_ticket_id=NULL,error_code=%s,next_attempt_at=%s,
                         receipt_checked_at=%s,claimed_at=NULL,claimed_by=NULL,
                         lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                      (receipt.error_code, now, now, now, row["id"]))
            out["retry"] += 1
        elif receipt.status == "rejected":
            c.execute("""UPDATE proc.notification_delivery SET state='rejected',
                         error_code=%s,receipt_checked_at=%s,terminal_at=%s,
                         claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL,
                         updated_at=%s WHERE id=%s""",
                      (receipt.error_code, now, now, now, row["id"]))
            if receipt.error_code == "DeviceNotRegistered":
                _disable_unregistered(c, int(row["device_id"]))
            out["rejected"] += 1
        else:
            submitted = _aware(row.get("submitted_at"))
            if now - submitted >= dt.timedelta(hours=23):
                c.execute("""UPDATE proc.notification_delivery SET state='expired',
                             error_code='receipt_missing',receipt_checked_at=%s,
                             terminal_at=%s,claimed_at=NULL,claimed_by=NULL,
                             lease_expires_at=NULL,updated_at=%s WHERE id=%s""",
                          (now, now, now, row["id"]))
                out["rejected"] += 1
            else:
                c.execute("""UPDATE proc.notification_delivery SET
                             receipt_checked_at=%s,next_attempt_at=%s+interval '15 minutes',
                             claimed_at=NULL,claimed_by=NULL,lease_expires_at=NULL,
                             updated_at=%s WHERE id=%s""",
                          (now, now, now, row["id"]))
                out["pending"] += 1
    return out


def expire_stale_deliveries(c, now: dt.datetime | None = None) -> int:
    now = _aware(now or dt.datetime.now(dt.timezone.utc))
    c.execute(
        """WITH expired AS (
             UPDATE proc.notification_delivery d SET state='expired',
               error_code=CASE WHEN e.expires_at<=%s THEN 'event_expired'
                               ELSE 'device_unavailable' END,
               terminal_at=%s,claimed_at=NULL,claimed_by=NULL,
               lease_expires_at=NULL,updated_at=%s
             FROM proc.notification_event e,proc.mobile_device md
             WHERE d.event_id=e.id AND d.device_id=md.id
               AND d.terminal_at IS NULL AND d.state<>'accepted'
               AND (e.expires_at<=%s OR NOT md.enabled OR md.revoked_at IS NOT NULL
                    OR md.permission_status NOT IN ('granted','provisional')
                    OR md.push_token_ciphertext IS NULL)
             RETURNING d.id)
           SELECT count(*) AS n FROM expired""", (now, now, now, now))
    return int(c.fetchone()["n"])


def run_once(c, now: dt.datetime | None = None, provider=None) -> dict:
    now = _aware(now or dt.datetime.now(dt.timezone.utc))
    return {
        "evaluation": evaluate_all(c, now),
        "expired": expire_stale_deliveries(c, now),
        "submission": submit_due(c, provider=provider, now=now),
        "receipts": check_receipts(c, provider=provider, now=now),
    }
