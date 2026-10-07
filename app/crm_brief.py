"""crm_brief.py — one page a salesperson reads before calling a customer.

docs/specs/crm-brief-kad.md. Everything here already exists somewhere on the
CRM card (Ταίριασμα tab, its dialogs, the ΓΕΜΗ match); the brief puts the
handful of facts that matter on one screen, and adds the ΚΑΔ estimate for a
company with no award history (kad_cpv.py).

Two bases, never blended into one number:

  * **history** — fit.py's stored profile, derived from what the firm WON.
  * **kad**     — an estimate from the registry ΚΑΔ, for a firm with no
                  history or a thin one (< fit.MIN_AWARDS_FOR_BAND awards).
                  Always shown as its own labelled block.

Read-only and computed per request, like the fit score: nothing about a
customer is cached, and nothing here reads act_ai_summary (test-enforced).
Competitor lists take seconds on a large supplier, so the dialog loads them
lazily (section()); the print page computes them inline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from app import fit as _fit
    from app import kad_cpv as _kad
except ImportError:                       # pragma: no cover - run with --app-dir=app
    import fit as _fit
    import kad_cpv as _kad

GOOD_SCORE = 45      # the Fit tab's "mid" colour and up: worth a look
SOON_DAYS = 14       # "closing soon" — enough time to still prepare a bid
TOP_N = 5            # rows per list on the brief
COMPETITORS_N = 8

SECTIONS = ("competitors", "peers", "leaders")


def _aware(ts):
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def headline(rows: list[dict], now: datetime | None = None) -> dict:
    """The numbers a salesperson opens with: how many open tenders fit
    (score ≥ GOOD_SCORE), what they are worth, how many close soon."""
    now = now or datetime.now(timezone.utc)
    soon = now + timedelta(days=SOON_DAYS)
    good = [r for r in rows if r["score"] >= GOOD_SCORE]
    value = sum(float(r.get("resolved_value") or r.get("total_cost_with_vat") or 0)
                for r in good)
    n_soon = sum(1 for r in good
                 if r.get("final_submission_date")
                 and _aware(r["final_submission_date"]) <= soon)
    return {"n_open": len(rows), "n_good": len(good), "value_good": value,
            "n_soon": n_soon}


def _history_groups(c, uid: int, lang: str) -> list[dict]:
    c.execute("""SELECT cpv_prefix, sum(n_acts) AS n
                   FROM proc.company_profile_cpv
                  WHERE user_id = %s AND length(cpv_prefix) = 4
                  GROUP BY cpv_prefix ORDER BY n DESC, cpv_prefix LIMIT %s""",
              (uid, TOP_N))
    rows = c.fetchall()
    labels = _kad.cpv_labels(c, [r["cpv_prefix"] for r in rows], lang)
    return [{"prefix": r["cpv_prefix"], "label": labels.get(r["cpv_prefix"]),
             "n": int(r["n"])} for r in rows]


def build(c, uid: int, *, lang: str = "el") -> dict:
    """Everything the brief shows except the competitor lists."""
    c.execute("""SELECT company, vat_number, city, postal_code
                   FROM proc.customer_profile WHERE user_id = %s""", (uid,))
    cprof = c.fetchone() or {}
    est = _kad.estimate(c, uid, lang=lang)

    profile = _fit.load_profile(c, uid)
    c.execute("SELECT * FROM proc.company_profile WHERE user_id = %s", (uid,))
    prow = c.fetchone()
    history = None
    if profile is not None and profile.is_usable:
        rows = _fit.score_open(c, profile)
        history = {"row": prow, "n_awards": profile.n_awards,
                   "headline": headline(rows), "top": rows[:TOP_N],
                   "buyers": _fit.buyers(c, uid)[:TOP_N],
                   "groups": _history_groups(c, uid, lang)}
    # A linked contractor whose profile was never derived: one click away.
    needs_derive = history is None and bool(_fit.operator_ids_for(c, uid))

    thin = history is None or history["n_awards"] < _fit.MIN_AWARDS_FOR_BAND
    kad = None
    if thin:
        kad = {"est": est}
        if est.usable:
            rows = _fit.score_open(c, est.profile)
            kad.update(headline=headline(rows), top=rows[:TOP_N])

    if history and kad and est.usable:
        basis = "history+kad"
    elif history:
        basis = "history"
    elif kad and est.usable:
        basis = "kad"
    else:
        basis = "none"

    sections = []
    if history:
        sections.append("competitors")
    if kad and est.usable:
        sections += ["peers", "leaders"]
    gemi = est.gemi or {}
    return {
        "company": (gemi.get("legal_name") or cprof.get("company")
                    or gemi.get("trade_title")),
        "trade_title": gemi.get("trade_title"),
        "afm": est.afm, "gemi": est.gemi, "kads": est.kads or [],
        "region": est.region, "region_label": est.region_label,
        "est": est, "history": history, "kad": kad, "thin": thin,
        "needs_derive": needs_derive, "basis": basis, "sections": sections,
        "generated_at": datetime.now(ZoneInfo("Europe/Athens")),
    }


def section(c, uid: int, kind: str, *, est=None, lang: str = "el") -> dict | None:
    """One competitor list. `kind` in SECTIONS; `est` saves re-reading the
    estimate when the caller already has it (the print page).

    None when the query did not finish (the pool's statement timeout on a
    very large supplier): the brief says so instead of failing, and a lazy
    placeholder must never be left spinning on a 500."""
    try:
        return _section(c, uid, kind, est=est, lang=lang)
    except ValueError:
        raise
    except Exception:                    # noqa: BLE001 — a panel, not the page
        return None


def _section(c, uid: int, kind: str, *, est=None, lang: str = "el") -> dict:
    if kind == "competitors":
        return _fit.competitors(c, uid, limit=COMPETITORS_N)
    est = est or _kad.estimate(c, uid, lang=lang)
    if kind == "peers":
        return _kad.peers(c, est, limit=COMPETITORS_N)
    if kind == "leaders":
        return _kad.leaders(c, est, limit=COMPETITORS_N)
    raise ValueError(kind)
