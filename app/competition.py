"""
competition.py — how many bids does a buyer usually get?

Spec: docs/specs/competition-indicator.md (slice 1).

KHMDHS reports `bidsSubmitted` on a contract (procurement_act.bids_submitted).
Nothing else in the product aggregated it, so the one place it appeared was the
page of a contract already awarded. This module turns it into two figures a
bidder can use BEFORE bidding: the median number of bids and the share of
single-bid awards, for an authority or a CPV division.

Where the numbers come from
---------------------------
Three materialised views (migration 20261001082952_competition_indicator.sql),
refreshed by proc.refresh_analytics():

  mv_competition_authority  (authority_id, competitive, bids, month, n)
  mv_competition_cpv        (division,     competitive, bids, month, n)
  mv_competition_fill       (month, n_contracts, n_filled, n_excluded)

They hold HISTOGRAMS, not finished statistics: medians do not add up, and the
authority page merges entity groups. `summarise()` takes histogram rows and
computes exact figures; it is pure arithmetic and is where the tests live.

Rules that are easy to break
----------------------------
* **The period is always stated, and it is measured.** KHMDHS only filled the
  field reliably from April 2025 to January 2026. The period printed is the
  span of months holding the central 90% of the counted contracts, never a
  min..max (one mistyped date stretches that to 1919), and never a constant
  (if KHMDHS fills the field again, the wording follows by itself).
* **Median and single-bid share, never a mean.** Values up to 29,970 exist;
  the views drop 0 and >100, but a mean would still read as "about this many".
* **Competitive and direct awards are never mixed.** A direct award reports one
  bid by construction most of the time. The headline is competitive; direct
  awards are a secondary line.
* **Below MIN_SHOW counted contracts nothing is stated** (the panel says there
  is not enough data; the notice line disappears). Below MIN_CONFIDENT the
  figures carry «περιορισμένο δείγμα».
* **Isolation.** This module reads acts only: never a company profile, never
  proc.act_ai_summary. Fit shows nothing from here yet (spec §7d, slice 3).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import psycopg

MIN_SHOW = 10           # below this, no figures at all
MIN_CONFIDENT = 30      # below this, «περιορισμένο δείγμα»
PERIOD_TRIM = 0.05      # the period spans the central 90% of counted contracts
ANALYTICS_ROWS = 15     # /analytics: the divisions with the thinnest competition
MONITOR_MONTHS = 6      # /admin/collection: months of fill rate shown

# (label, lowest, highest) — highest None = open-ended.
BUCKETS = (("1", 1, 1), ("2–3", 2, 3), ("4–6", 4, 6), ("7+", 7, None))

DIRECT_FAMILY = "Απευθείας ανάθεση"

_MONTHS = {
    "el": ("Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαΐ", "Ιουν",
           "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #
def _median(hist: dict[int, int], n: int) -> float:
    """Exact median of a {value: count} histogram holding n observations."""
    def nth(k: int) -> int:                      # k is 1-based
        seen = 0
        for value in sorted(hist):
            seen += hist[value]
            if seen >= k:
                return value
        raise ValueError("k beyond the histogram")
    if n % 2:
        return float(nth(n // 2 + 1))
    return (nth(n // 2) + nth(n // 2 + 1)) / 2


def _period(months: dict[dt.date, int], n: int) -> tuple[dt.date, dt.date]:
    """The first and last month of the central (1 - 2*PERIOD_TRIM) share of
    the contracts, by month of signature."""
    order = sorted(months)
    lo_k = max(1, int(n * PERIOD_TRIM) + 1)      # 1-based rank of the low edge
    hi_k = max(lo_k, n - int(n * PERIOD_TRIM))   # and of the high edge
    seen, first, last = 0, None, None
    for m in order:
        seen += months[m]
        if first is None and seen >= lo_k:
            first = m
        if seen >= hi_k:
            last = m
            break
    return first, last


def summarise(rows: Iterable) -> dict | None:
    """Figures from histogram rows, each with `bids`, `month` and `n` (a dict
    or a mapping-like DB row). None when there is nothing to summarise.

    Returns {n, median, single_share, buckets:[(label, share)], first, last,
    trimmed, confidence: 'none' | 'limited' | 'ok'}. Shares are 0..1.
    """
    hist: dict[int, int] = {}
    months: dict[dt.date, int] = {}
    for r in rows:
        k = int(r["n"] or 0)
        if k <= 0:
            continue
        b = int(r["bids"])
        hist[b] = hist.get(b, 0) + k
        m = r["month"]
        months[m] = months.get(m, 0) + k
    n = sum(hist.values())
    if not n:
        return None
    buckets = []
    for label, lo, hi in BUCKETS:
        k = sum(c for v, c in hist.items() if v >= lo and (hi is None or v <= hi))
        buckets.append((label, k / n))
    first, last = _period(months, n)
    return {
        "n": n,
        "median": _median(hist, n),
        "single_share": hist.get(1, 0) / n,
        "buckets": buckets,
        "first": first,
        "last": last,
        # False when n is too small to trim anything: the period then covers
        # every contract, and the wording must not say "9 in 10".
        "trimmed": int(n * PERIOD_TRIM) > 0,
        "confidence": ("none" if n < MIN_SHOW
                       else "limited" if n < MIN_CONFIDENT else "ok"),
    }


def split(rows: Iterable) -> dict:
    """{'competitive': summary|None, 'direct': summary|None} from rows that
    carry a `competitive` flag."""
    comp, direct = [], []
    for r in rows:
        (comp if r["competitive"] else direct).append(r)
    return {"competitive": summarise(comp), "direct": summarise(direct)}


# --------------------------------------------------------------------------- #
# Wording
# --------------------------------------------------------------------------- #
def month_label(m: dt.date | None, lang: str = "el") -> str:
    if m is None:
        return "—"
    return f"{_MONTHS.get(lang, _MONTHS['el'])[m.month - 1]} {m.year}"


def period_label(s: dict | None, lang: str = "el") -> str:
    """«Απρ 2025 – Ιαν 2026», or one month alone when they coincide."""
    if not s:
        return ""
    a, b = month_label(s["first"], lang), month_label(s["last"], lang)
    return a if a == b else f"{a} – {b}"


def median_label(x: float | None, lang: str = "el") -> str:
    """2 → «2»; 2.5 → «2,5» (Greek decimal comma) or "2.5"."""
    if x is None:
        return "—"
    if float(x).is_integer():
        return str(int(x))
    s = f"{x:.1f}"
    return s.replace(".", ",") if lang != "en" else s


def pct(share: float | None) -> str:
    return "—" if share is None else f"{round(share * 100)}%"


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def _rows(c, sql: str, args: tuple) -> list:
    """Rows from a competition view, or [] while the view does not exist yet
    or was never populated (the test schema creates it WITH NO DATA)."""
    try:
        c.execute(sql, args)
        return c.fetchall()
    except (psycopg.errors.UndefinedTable,
            psycopg.errors.ObjectNotInPrerequisiteState):
        if not c.connection.autocommit:   # the app's pool is autocommit
            c.connection.rollback()
        return []


def for_authority(c, member_ids: list[str]) -> dict:
    """The authority's figures, summed across every member of its entity group."""
    rows = _rows(c, """
        /* competition_for_authority */
        SELECT competitive, bids, month, n
        FROM proc.mv_competition_authority
        WHERE authority_id = ANY(%s)
    """, (list(member_ids),))
    return split(rows)


def for_act(c, adam: str) -> dict | None:
    """The figures of a notice's authority FOR THE SAME KIND OF PROCEDURE as
    the notice: a direct-award invitation is compared with the authority's
    direct awards, everything else with its competitive procedures. None when
    the act is not a notice, has no authority, or that half of the authority's
    history is under MIN_SHOW. The result carries `competitive` so the line
    can say which it is.

    The authority's entity group is NOT merged here: one indexed lookup is the
    whole cost of a line shown on every notice page. The authority page, where
    the panel lives, merges."""
    # procedure_family is filled by refresh_analytics(), so a notice imported
    # since the last refresh has none yet: compute it the same way.
    c.execute("""SELECT type::text AS type, authority_id,
                        coalesce(procedure_family,
                                 proc.compute_procedure_family(procedure_type_code))
                            AS procedure_family
                 FROM proc.procurement_act WHERE adam = %s""", (adam,))
    act = c.fetchone()
    if not act or act["type"] != "notice" or not act["authority_id"]:
        return None
    competitive = act["procedure_family"] != DIRECT_FAMILY
    figs = for_authority(c, [act["authority_id"]])
    s = figs["competitive" if competitive else "direct"]
    if not s or s["confidence"] == "none":
        return None
    return {"authority_id": act["authority_id"], "competitive": competitive, **s}


def by_division(c, lang: str = "el", limit: int = ANALYTICS_ROWS) -> list[dict]:
    """CPV divisions with the thinnest competition, competitive procedures
    only, divisions under MIN_SHOW left out. Highest single-bid share first."""
    rows = _rows(c, """
        /* competition_by_division */
        SELECT division, bids, month, n
        FROM proc.mv_competition_cpv
        WHERE competitive
    """, ())
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r["division"], []).append(r)
    out = []
    for division, rs in per.items():
        s = summarise(rs)
        if s and s["confidence"] != "none":
            out.append({"division": division, **s})
    out.sort(key=lambda d: (-d["single_share"], -d["n"], d["division"]))
    out = out[:limit]
    if out:
        col = "description_en" if lang == "en" else "description"
        c.execute(f"""
            SELECT substr(cpv_code, 1, 2) AS division,
                   coalesce({col}, description) AS label
            FROM proc.cpv_code
            WHERE substr(cpv_code, 3, 6) = '000000'
              AND substr(cpv_code, 1, 2) = ANY(%s)
        """, ([d["division"] for d in out],))
        labels = {r["division"]: r["label"] for r in c.fetchall()}
        for d in out:
            d["label"] = labels.get(d["division"])
    return out


def fill_monitor(c, months: int = MONITOR_MONTHS) -> dict:
    """The admin's view of the source: per recent month, how many KHMDHS
    contracts state a bid count. {rows:[{month, n_contracts, n_filled,
    share}], n_excluded}. Empty rows while the view is missing."""
    rows = _rows(c, """
        SELECT month, n_contracts, n_filled
        FROM proc.mv_competition_fill
        ORDER BY month DESC
        LIMIT %s
    """, (months,))
    out = [{"month": r["month"], "n_contracts": r["n_contracts"],
            "n_filled": r["n_filled"],
            "share": (r["n_filled"] / r["n_contracts"]) if r["n_contracts"] else None}
           for r in rows]
    total = _rows(c, "SELECT coalesce(sum(n_excluded), 0) AS n FROM proc.mv_competition_fill", ())
    return {"rows": out, "n_excluded": int(total[0]["n"]) if total else 0}
