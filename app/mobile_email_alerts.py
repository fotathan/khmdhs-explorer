"""Mobile JSON adapter for the existing scheduled email-digest model.

Email alerts remain rows in ``proc.digest_subscription`` and continue to run
through :mod:`app.digests`. This module only applies mobile ownership rules and
maps the existing schedules/settings to the committed API DTO.
"""
from __future__ import annotations

import time

from app import (
    auth,
    digests,
    mobile_favorites,
    mobile_saved_searches,
    mobile_search,
    search_match,
)
from app.api_v1.schemas import (
    EmailAlert,
    EmailAlertInput,
    EmailAlertRun,
    EmailAlertRunItem,
    EmailAlertRunPage,
    EmailSchedule,
)

RUN_PAGE_SIZE = 20


class MobileEmailAlertError(Exception):
    def __init__(self, code: str, status: int, *, fields: list[dict] | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.fields = fields


def _available_profile(c, *, user: dict, profile_id: int) -> dict:
    profile = auth.get_search_profile(c, profile_id)
    if not profile or not auth.can_apply_profile(user, profile):
        raise MobileEmailAlertError("not_found", 404)
    return profile


def _schedule_options(c, language: str) -> tuple[list[dict], list[EmailSchedule]]:
    rows = [dict(row) for row in digests.list_schedules(c) if row["is_active"]]
    options = [EmailSchedule(
        id=str(row["id"]),
        label=digests.describe_schedule(row, language),
        is_default=bool(row["is_default"]),
    ) for row in rows]
    return rows, options


def _subscription(c, *, user_id: int, profile_id: int) -> dict | None:
    c.execute(
        """SELECT * FROM proc.digest_subscription
           WHERE user_id=%s AND search_profile_id=%s""",
        (user_id, profile_id),
    )
    row = c.fetchone()
    return dict(row) if row else None


def _has_recipient(c, *, user: dict, subscription: dict | None) -> bool:
    if (user.get("email") or "").strip():
        return True
    if not subscription:
        return False
    c.execute(
        """SELECT EXISTS(
             SELECT 1 FROM proc.digest_recipient
             WHERE subscription_id=%s AND is_active
           ) AS yes""",
        (subscription["id"],),
    )
    return bool(c.fetchone()["yes"])


def _dto(c, *, user: dict, profile_id: int, language: str) -> EmailAlert:
    schedules, options = _schedule_options(c, language)
    subscription = _subscription(
        c, user_id=int(user["id"]), profile_id=profile_id)
    default = next((row for row in schedules if row["is_default"]), None)
    selected = None
    if subscription and subscription.get("schedule_id") is not None:
        selected = next((row for row in schedules
                         if int(row["id"]) == int(subscription["schedule_id"])), None)
    else:
        selected = default

    active = bool(subscription and subscription.get("is_active"))
    suspended = None
    if active and not user.get("has_access"):
        suspended = "not_entitled"
    elif active and not _has_recipient(c, user=user, subscription=subscription):
        suspended = "no_email"
    elif active and selected is None:
        suspended = "no_schedule"

    recipients = []
    runs = []
    if subscription:
        recipients = [
            row["email"]
            for row in digests.list_recipients(
                c, subscription["id"], active_only=True)
        ]
        runs = [
            EmailAlertRun(
                id=str(row["id"]),
                status=row["status"],
                result_count=int(row.get("n_results") or 0),
                started_at=row["started_at"],
            )
            for row in digests.list_runs(
                c, subscription_id=subscription["id"], limit=3)
        ]

    return EmailAlert(
        saved_search_id=str(profile_id),
        exists=subscription is not None,
        active=active,
        layout=(subscription.get("layout") if subscription else "list"),
        schedule_id=(str(subscription["schedule_id"])
                     if subscription and subscription.get("schedule_id") is not None
                     else None),
        language=(subscription.get("lang") if subscription else language),
        max_results=int(subscription.get("max_results") if subscription else 25),
        lead_days=list(subscription.get("lead_days") if subscription else
                       digests.DEFAULT_LEAD_DAYS),
        send_empty=bool(subscription and subscription.get("send_empty")),
        schedule_label=(digests.describe_schedule(selected, language)
                        if selected else None),
        next_run_at=(digests.next_occurrence(selected) if selected and active else None),
        last_sent_at=(subscription.get("last_sent_at") if subscription else None),
        additional_recipients=recipients,
        recent_runs=runs,
        schedule_options=options,
        delivery_suspended_reason=suspended,
    )


def get_email_alert(
    c, *, user: dict, profile_id: int, language: str,
) -> EmailAlert:
    _available_profile(c, user=user, profile_id=profile_id)
    return _dto(c, user=user, profile_id=profile_id, language=language)


def put_email_alert(
    c, *, user: dict, profile_id: int, payload: EmailAlertInput,
    language: str,
) -> EmailAlert:
    _available_profile(c, user=user, profile_id=profile_id)
    schedule_id = None
    if payload.schedule_id is not None:
        requested = digests.get_schedule(c, int(payload.schedule_id))
        if not requested or not requested["is_active"]:
            raise MobileEmailAlertError(
                "validation_error", 422,
                fields=[{"field": "schedule_id", "code": "unavailable"}],
            )
        # Selecting the current default means "follow the portal default" so a
        # future admin schedule change reaches this customer automatically.
        schedule_id = None if requested["is_default"] else int(requested["id"])
    digests.upsert_subscription(
        c,
        user_id=int(user["id"]),
        search_profile_id=profile_id,
        schedule_id=schedule_id,
        is_active=payload.active,
        send_empty=payload.send_empty,
        max_results=payload.max_results,
        lang=payload.language,
        layout=payload.layout,
        include_primary=True,
        lead_days=payload.lead_days,
        created_by=int(user["id"]),
    )
    return _dto(c, user=user, profile_id=profile_id, language=language)


def delete_email_alert(c, *, user: dict, profile_id: int) -> None:
    _available_profile(c, user=user, profile_id=profile_id)
    c.execute(
        """DELETE FROM proc.digest_subscription
           WHERE user_id=%s AND search_profile_id=%s""",
        (int(user["id"]), profile_id),
    )


def _owned_run(c, *, user_id: int, profile_id: int, run_id: int) -> dict:
    c.execute(
        """SELECT run.*, subscription.search_profile_id,
                  profile.name AS profile_name
             FROM proc.digest_run run
             JOIN proc.digest_subscription subscription
               ON subscription.id=run.subscription_id
             JOIN proc.search_profile profile
               ON profile.id=subscription.search_profile_id
            WHERE run.id=%s AND subscription.user_id=%s
              AND subscription.search_profile_id=%s""",
        (run_id, user_id, profile_id),
    )
    row = c.fetchone()
    if not row:
        raise MobileEmailAlertError("not_found", 404)
    return dict(row)


def _run_offset(token: str, *, user_id: int, profile_id: int, run_id: int) -> int:
    try:
        payload = mobile_search.decode_signed(token, "email_alert_run")
        if (int(payload["user_id"]) != user_id
                or int(payload["profile_id"]) != profile_id
                or int(payload["run_id"]) != run_id):
            raise ValueError("cursor scope")
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError("cursor offset")
        return offset
    except Exception as exc:
        raise MobileEmailAlertError("invalid_cursor", 422) from exc


def get_email_alert_run(
    c,
    *,
    user: dict,
    profile_id: int,
    run_id: int,
    language: str,
    cursor: str | None = None,
) -> EmailAlertRunPage:
    user_id = int(user["id"])
    run = _owned_run(
        c, user_id=user_id, profile_id=profile_id, run_id=run_id)
    offset = (_run_offset(
        cursor, user_id=user_id, profile_id=profile_id, run_id=run_id)
        if cursor else 0)
    rows = digests.run_item_acts(
        c, run_id, limit=RUN_PAGE_SIZE + 1, offset=offset)
    has_more = len(rows) > RUN_PAGE_SIZE
    rows = rows[:RUN_PAGE_SIZE]
    params = digests.run_params(c, run)
    from app import main as web
    q_text, cpv_codes = web.match_terms_from_params(params)
    chips = {}
    if rows and (q_text or cpv_codes):
        chips = search_match.list_chips(
            c, [row["adam"] for row in rows], q_text, cpv_codes, language)
    context = mobile_search.make_context(params)
    favorites = mobile_favorites.favorite_adams(
        c, user_id, [str(row["adam"]) for row in rows])
    items = [EmailAlertRunItem(
        act=mobile_search.act_item(
            row,
            lang=language,
            chips=chips.get(row["adam"]),
            context=context,
            favorited=str(row["adam"]) in favorites,
        ),
        included_in_email=bool(row["in_email"]),
    ) for row in rows]
    next_cursor = None
    if has_more:
        next_cursor = mobile_search.encode_signed({
            "kind": "email_alert_run",
            "user_id": user_id,
            "profile_id": profile_id,
            "run_id": run_id,
            "offset": offset + RUN_PAGE_SIZE,
            "expires_at": time.time() + mobile_search.CURSOR_TTL_SECONDS,
        })
    return EmailAlertRunPage(
        run=EmailAlertRun(
            id=str(run["id"]), status=run["status"],
            result_count=int(run.get("n_results") or 0),
            started_at=run["started_at"],
        ),
        saved_search_id=str(profile_id),
        saved_search_name=str(run.get("profile_name") or ""),
        filters=mobile_saved_searches.stored_filters(params),
        items=items,
        total=int(digests.run_item_count(c, run_id)),
        next_cursor=next_cursor,
    )
