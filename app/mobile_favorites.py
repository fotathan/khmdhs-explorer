"""Ownership-safe account favorites for customer procurement acts."""
from __future__ import annotations

import datetime as dt
import time

from app import mobile_search
from app.api_v1.schemas import (
    ActDetail,
    ActSearchItem,
    FavoriteItem,
    FavoritePage,
    FavoriteState,
)


class MobileFavoriteError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def favorite_adams(c, user_id: int, adams: list[str]) -> set[str]:
    if not adams:
        return set()
    c.execute(
        """SELECT adam FROM proc.user_favorite_act
           WHERE user_id=%s AND adam=ANY(%s)""",
        (user_id, adams),
    )
    return {str(row["adam"]) for row in c.fetchall()}


def mark_items(c, user_id: int, items: list[ActSearchItem]) -> None:
    favorites = favorite_adams(c, user_id, [item.adam for item in items])
    for item in items:
        item.favorited = item.adam in favorites


def mark_detail(c, user_id: int, detail: ActDetail) -> None:
    detail.favorited = detail.adam in favorite_adams(c, user_id, [detail.adam])


def put_favorite(c, *, user_id: int, adam: str) -> FavoriteState:
    c.execute(
        """INSERT INTO proc.user_favorite_act (user_id,adam)
           SELECT %s,a.adam FROM proc.procurement_act a WHERE a.adam=%s
           ON CONFLICT (user_id,adam) DO NOTHING
           RETURNING created_at""",
        (user_id, adam),
    )
    row = c.fetchone()
    if row is None:
        c.execute(
            """SELECT created_at FROM proc.user_favorite_act
               WHERE user_id=%s AND adam=%s""",
            (user_id, adam),
        )
        row = c.fetchone()
    if row is None:
        raise MobileFavoriteError("not_found", 404)
    return FavoriteState(adam=adam, favorited=True, favorited_at=row["created_at"])


def delete_favorite(c, *, user_id: int, adam: str) -> None:
    # Idempotent removal keeps retrying a mobile tap safe after a network loss.
    c.execute(
        "DELETE FROM proc.user_favorite_act WHERE user_id=%s AND adam=%s",
        (user_id, adam),
    )


def _cursor(token: str, user_id: int) -> tuple[dt.datetime, dt.datetime, str]:
    try:
        payload = mobile_search.decode_signed(token, "favorite_list")
        if int(payload["user_id"]) != user_id:
            raise ValueError("owner")
        snapshot = dt.datetime.fromisoformat(str(payload["snapshot_at"]))
        created = dt.datetime.fromisoformat(str(payload["created_at"]))
        adam = str(payload["adam"])
        if snapshot.tzinfo is None or created.tzinfo is None or not adam:
            raise ValueError("cursor values")
        return snapshot, created, adam
    except Exception as exc:
        raise MobileFavoriteError("invalid_cursor", 422) from exc


def list_favorites(
    c,
    *,
    user_id: int,
    lang: str,
    limit: int,
    cursor: str | None,
) -> FavoritePage:
    from app import main as web

    if cursor:
        snapshot, before_created, before_adam = _cursor(cursor, user_id)
    else:
        snapshot = dt.datetime.now(dt.timezone.utc)
        before_created = before_adam = None

    where = ["f.user_id=%s", "f.created_at<=%s"]
    args: list = [user_id, snapshot]
    if before_created is not None:
        where.append("(f.created_at,f.adam)<(%s,%s)")
        args.extend([before_created, before_adam])
    c.execute(
        f"""SELECT f.created_at AS favorited_at,
                   {web.SELECT_COLS}, NULL::text AS snippet
            FROM proc.user_favorite_act f
            JOIN proc.procurement_act a ON a.adam=f.adam
            LEFT JOIN proc.authority auth ON auth.org_id=a.authority_id
            WHERE {' AND '.join(where)}
            ORDER BY f.created_at DESC,f.adam DESC
            LIMIT %s""",
        [*args, limit + 1],
    )
    rows = c.fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [FavoriteItem(
        act=mobile_search.act_item(row, lang=lang, favorited=True),
        favorited_at=row["favorited_at"],
    ) for row in rows]
    c.execute("SELECT count(*) AS n FROM proc.user_favorite_act WHERE user_id=%s",
              (user_id,))
    total = int(c.fetchone()["n"])
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = mobile_search.encode_signed({
            "kind": "favorite_list",
            "user_id": user_id,
            "snapshot_at": snapshot.isoformat(),
            "created_at": last["favorited_at"].isoformat(),
            "adam": str(last["adam"]),
            "expires_at": time.time() + mobile_search.CURSOR_TTL_SECONDS,
        })
    return FavoritePage(items=items, next_cursor=next_cursor, total=total)
