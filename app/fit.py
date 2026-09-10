"""fit.py — "is this tender worth bidding for", for one customer.

The first thing in the app that is a property of a CUSTOMER rather than of an
act, and the reason the two must not touch. `proc.act_ai_summary` is generated
once per act and served to every reader (ai_summary.py §3), so anything
customer-shaped near that cache leaks between customers. Fit is therefore
computed per (customer, act) at read time and stored nowhere.

Two design commitments, both from how the rest of this app already works.

**Deterministic, no model.** Every component below is arithmetic over the
corpus. That makes it explainable, free, instant, and identical on every run —
and it keeps the promise on /ai that customer data never reaches a model
provider. A ranking that needed an LLM would quietly break that page.

**Components, never just a total.** The app shows match chips on results and a
verbatim quote under every AI claim; a bare "87% match" would be the one number
a reader is asked to take on faith. `explain()` returns the parts, and the
templates show them, so a wrong score can be argued with.

The profile itself is DERIVED (seed_from_ledger): what a firm has actually won,
by ΑΦΜ, rather than what a form claims it can do. Cato and every notice-feed
competitor structurally cannot compute this — it needs an award ledger keyed by
tax number, which is what this corpus is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Weights
#
# Tuned by argument, not by fitting: there is no labelled "did they bid" data
# yet, and inventing one would dress a guess as a measurement. The ordering is
# what matters and it is defensible — what you supply dominates, where and for
# how much are real constraints, and a familiar buyer is a genuine advantage
# but never the difference between bidding and not.
#
# Revisit when there IS outcome data (win/loss), which is Tier 3 item 14.
# --------------------------------------------------------------------------- #
W_CPV = 0.45
W_VALUE = 0.20
W_GEO = 0.20
W_BUYER = 0.15

# A tender whose CPVs the firm has never touched is not a near miss. Below this
# the other components cannot rescue it, because "we do not sell that" is not
# outweighed by being nearby and the right size.
CPV_FLOOR = 0.15


@dataclass
class Profile:
    """A firm's capability, as the scorer needs it."""
    user_id: int
    cpv: dict[str, float] = field(default_factory=dict)   # prefix -> weight 0..1
    nuts: set[str] = field(default_factory=set)           # 4-char NUTS prefixes
    buyers: set[str] = field(default_factory=set)         # authority_id
    value_min: float | None = None
    value_max: float | None = None
    n_awards: int = 0

    @property
    def is_usable(self) -> bool:
        """No CPVs means nothing to match on, and every score would be the
        floor. Better to say "no profile" than to rank by noise."""
        return bool(self.cpv)


# --------------------------------------------------------------------------- #
# Components — each returns 0..1 with a reason a person can read
# --------------------------------------------------------------------------- #
def score_cpv(profile: Profile, act_cpvs: list[str]) -> tuple[float, str, str | None]:
    """How close is what is being bought to what this firm sells?

    Graded by prefix depth, because CPV is a hierarchy and a near miss in it
    is a real near miss: the same 8 digits is the same thing, the same 4 is
    the same family, the same 2 is the same industry. The firm's own weight
    for that prefix scales it — one contract in a division is not a
    speciality.
    """
    if not act_cpvs or not profile.cpv:
        return 0.0, "δεν υπάρχουν κωδικοί CPV για σύγκριση", None
    best, why, detail = 0.0, "", None
    for code in act_cpvs:
        code = (code or "").strip()
        if not code:
            continue
        for depth, credit, label in ((8, 1.0, "ακριβής κωδικός CPV"),
                                     (4, 0.7, "ίδια ομάδα CPV"),
                                     (2, 0.4, "ίδιος τομέας CPV")):
            prefix = code[:depth]
            weight = profile.cpv.get(prefix)
            if weight is None:
                continue
            # The floor is deliberately low. For a broad supplier — 76 buyers,
            # 18 regions — value, geography and buyer history all saturate at
            # 1.0 and stop discriminating, so CPV carries the whole signal and
            # a group touched once in 11,000 awards must not read as half a
            # match. Centrality, not mere presence.
            value = credit * (0.25 + 0.75 * weight)
            if value > best:
                best, why, detail = value, label, prefix
            break                                    # deepest match wins
    return ((best, why, detail) if best
            else (0.0, "δεν προμηθεύει τίποτα σε αυτούς τους CPV", None))


def score_value(profile: Profile, value) -> tuple[float, str, str | None]:
    """Is this the size of job the firm actually wins?

    Missing values score neutral rather than zero. A great many acts carry no
    usable amount, and punishing the record for what the source omitted would
    rank on data quality instead of fit.
    """
    if profile.value_min is None or profile.value_max is None:
        return 0.5, "χωρίς ιστορικό αξιών για σύγκριση", None
    if value is None:
        return 0.5, "δεν αναφέρεται αξία", None
    v = float(value)
    if v <= 0:
        return 0.5, "δεν αναφέρεται αξία", None
    lo, hi = float(profile.value_min), float(profile.value_max)
    if lo <= v <= hi:
        return 1.0, "εντός του συνήθους εύρους τους", None
    if 0.5 * lo <= v <= 2.0 * hi:
        return 0.5, "κοντά στο σύνηθες εύρος τους", None
    return 0.1, ("μεγαλύτερος από ό,τι έχουν αναλάβει" if v > hi
                 else "μικρότερος από ό,τι διεκδικούν συνήθως"), None


def score_geo(profile: Profile, nuts_code: str | None) -> tuple[float, str, str | None]:
    """Do they work there? Prefix-matched at NUTS-2, the same depth the
    region filter uses, so a match here means the same thing it does there."""
    if not profile.nuts:
        return 0.5, "χωρίς ιστορικό περιοχών", None
    if not nuts_code:
        return 0.5, "δεν αναφέρεται περιοχή", None
    region = nuts_code[:4]
    if region in profile.nuts:
        return 1.0, "δραστηριοποιούνται εκεί", region
    if any(n[:2] == region[:2] for n in profile.nuts):
        return 0.3, "ίδια χώρα, άλλη περιφέρεια", None
    return 0.1, "εκτός των περιοχών τους", None


def score_buyer(profile: Profile, authority_id: str | None) -> tuple[float, str, str | None]:
    """Have they won from this buyer before?

    The component a notice feed cannot compute, and the one an experienced
    bidder checks first — knowing the authority is worth real money in bid
    preparation. Weighted lowest all the same: a new buyer is not a reason
    to skip a tender that otherwise fits.
    """
    if not profile.buyers:
        return 0.0, "χωρίς ιστορικό αναθετουσών", None
    if authority_id and authority_id in profile.buyers:
        return 1.0, "έχουν αναλάβει ξανά από αυτήν", None
    return 0.0, "νέα αναθέτουσα γι' αυτούς", None


# --------------------------------------------------------------------------- #
# The score
# --------------------------------------------------------------------------- #
def explain(profile: Profile, act: dict, act_cpvs: list[str]) -> dict:
    """Score one act for one firm, with the reasoning attached.

    Returns {score 0..100, components:[{key,label,weight,score,why,detail}],
    capped}. `why` is a FIXED Greek phrase — a translation key — and the
    variable part (a CPV prefix, a NUTS code) rides separately in `detail`,
    because a phrase with a code interpolated into it can never be looked up
    in the i18n catalogue.
    """
    cpv_s, cpv_why, cpv_d = score_cpv(profile, act_cpvs)
    val_s, val_why, val_d = score_value(profile, act.get("resolved_value")
                                        or act.get("total_cost_with_vat"))
    geo_s, geo_why, geo_d = score_geo(profile, act.get("nuts_code"))
    buy_s, buy_why, buy_d = score_buyer(profile, act.get("authority_id"))

    total = (W_CPV * cpv_s + W_VALUE * val_s + W_GEO * geo_s + W_BUYER * buy_s)
    # "We do not sell that" is not outweighed by being nearby and the right
    # size, so a tender with no CPV footing is capped rather than scored.
    capped = cpv_s == 0.0
    if capped:
        total = min(total, CPV_FLOOR)

    return {
        "score": round(100 * total),
        "capped": capped,
        "components": [
            {"key": "cpv", "label": "Αντικείμενο", "weight": W_CPV,
             "score": cpv_s, "why": cpv_why, "detail": cpv_d},
            {"key": "value", "label": "Μέγεθος", "weight": W_VALUE,
             "score": val_s, "why": val_why, "detail": val_d},
            {"key": "geo", "label": "Περιοχή", "weight": W_GEO,
             "score": geo_s, "why": geo_why, "detail": geo_d},
            {"key": "buyer", "label": "Αναθέτουσα", "weight": W_BUYER,
             "score": buy_s, "why": buy_why, "detail": buy_d},
        ],
    }


# --------------------------------------------------------------------------- #
# Seeding the profile from the award ledger
# --------------------------------------------------------------------------- #
# Awards only. A notice a firm did not win says nothing about what it can do,
# and 'auction' (the award decision) plus 'contract' is where a winner's name
# and a real figure first appear.
_AWARD_TYPES = ("contract", "auction")

# Enough of a track record for the band to mean anything. Below this the
# percentiles are describing two or three contracts and would discriminate on
# noise, so the value component is left neutral instead.
MIN_AWARDS_FOR_BAND = 5


def operator_ids_for(c, user_id: int) -> list[int]:
    """Every ledger identity behind this customer.

    Both routes matter: customer_profile.operator_id is set when a lead was
    created FROM a contractor, and the ΑΦΜ is what a human typed. A firm can
    also appear under several operator rows, so this returns a list — using
    one would understate the history.
    """
    c.execute("""SELECT operator_id, vat_number, tax_number
                   FROM proc.customer_profile WHERE user_id = %s""", (user_id,))
    row = c.fetchone()
    if not row:
        return []
    ids: list[int] = []
    if row.get("operator_id"):
        ids.append(int(row["operator_id"]))
    vat = (row.get("vat_number") or row.get("tax_number") or "").strip()
    if vat:
        c.execute("""SELECT operator_id FROM proc.economic_operator
                      WHERE vat_number = %s""", (vat,))
        ids += [int(r["operator_id"]) for r in c.fetchall()]
    return sorted(set(ids))


def seed_from_ledger(c, user_id: int, *, by: int | None = None) -> dict:
    """(Re)build the derived half of a profile from what the firm has won.

    Declared rows are untouched: re-deriving must never silently discard a
    human's correction, which is the whole reason source is part of the
    primary key.

    Returns a summary of what was found, so the caller can say "nothing to
    derive" rather than leaving an empty profile looking like a failure.
    """
    op_ids = operator_ids_for(c, user_id)
    if not op_ids:
        return {"ok": False,
                "reason": "δεν υπάρχει ΑΦΜ ή συνδεδεμένος ανάδοχος για αυτόν τον πελάτη"}

    c.execute(f"""
        SELECT count(*) AS n_awards,
               count(DISTINCT a.authority_id) AS n_buyers,
               percentile_disc(0.10) WITHIN GROUP (ORDER BY v.val) AS p10,
               percentile_disc(0.50) WITHIN GROUP (ORDER BY v.val) AS p50,
               percentile_disc(0.90) WITHIN GROUP (ORDER BY v.val) AS p90
          FROM proc.act_operator ao
          JOIN proc.procurement_act a ON a.adam = ao.adam
          CROSS JOIN LATERAL (SELECT coalesce(
                     ao.awarded_value_with_vat,
                     proc.resolved_value(a.adam, a.total_cost_with_vat)) AS val) v
         WHERE ao.operator_id = ANY(%s)
           AND a.type = ANY(%s)
           AND NOT coalesce(a.cancelled, false)
    """, (op_ids, list(_AWARD_TYPES)))
    agg = c.fetchone() or {}
    n_awards = int(agg.get("n_awards") or 0)
    if not n_awards:
        return {"ok": False, "reason": "δεν βρέθηκαν αναθέσεις για αυτό το ΑΦΜ"}

    banded = n_awards >= MIN_AWARDS_FOR_BAND
    c.execute("""
        INSERT INTO proc.company_profile
              (user_id, operator_ids, derived_at, n_awards, n_buyers,
               value_p10, value_median, value_p90, updated_at, updated_by)
        VALUES (%s, %s, now(), %s, %s, %s, %s, %s, now(), %s)
        ON CONFLICT (user_id) DO UPDATE SET
              operator_ids = EXCLUDED.operator_ids,
              derived_at   = EXCLUDED.derived_at,
              n_awards     = EXCLUDED.n_awards,
              n_buyers     = EXCLUDED.n_buyers,
              value_p10    = EXCLUDED.value_p10,
              value_median = EXCLUDED.value_median,
              value_p90    = EXCLUDED.value_p90,
              updated_at   = now(),
              updated_by   = EXCLUDED.updated_by
    """, (user_id, op_ids, n_awards, int(agg.get("n_buyers") or 0),
          agg.get("p10") if banded else None,
          agg.get("p50") if banded else None,
          agg.get("p90") if banded else None, by))

    # Derived rows are replaced wholesale; declared rows are left alone.
    for table in ("company_profile_cpv", "company_profile_nuts"):
        c.execute(f"DELETE FROM proc.{table} "
                  f"WHERE user_id = %s AND source = 'derived'", (user_id,))
    c.execute("DELETE FROM proc.company_profile_buyer WHERE user_id = %s", (user_id,))

    # CPV at both depths: the division says what industry they are in, the
    # full code says what they actually supply. Scoring reads whichever is
    # deeper, so storing both is what makes the graded match possible.
    c.execute("""
        INSERT INTO proc.company_profile_cpv
              (user_id, cpv_prefix, n_acts, total_value, source)
        SELECT %s, prefix, count(DISTINCT adam), coalesce(sum(val), 0), 'derived'
          FROM (
            SELECT a.adam, p.prefix,
                   coalesce(ao.awarded_value_with_vat,
                            proc.resolved_value(a.adam, a.total_cost_with_vat)) AS val
              FROM proc.act_operator ao
              JOIN proc.procurement_act a ON a.adam = ao.adam
              JOIN proc.act_object_detail od ON od.adam = a.adam
              JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
              CROSS JOIN LATERAL (VALUES (substr(oc.cpv_code, 1, 2)),
                                         (substr(oc.cpv_code, 1, 4)),
                                         (oc.cpv_code)) AS p(prefix)
             WHERE ao.operator_id = ANY(%s) AND a.type = ANY(%s)
               AND NOT coalesce(a.cancelled, false)
               AND oc.cpv_code IS NOT NULL AND oc.cpv_code <> ''
          ) s
         GROUP BY prefix
    """, (user_id, op_ids, list(_AWARD_TYPES)))

    c.execute("""
        INSERT INTO proc.company_profile_nuts (user_id, nuts_prefix, n_acts, source)
        SELECT %s, substr(a.nuts_code, 1, 4), count(DISTINCT a.adam), 'derived'
          FROM proc.act_operator ao
          JOIN proc.procurement_act a ON a.adam = ao.adam
         WHERE ao.operator_id = ANY(%s) AND a.type = ANY(%s)
           AND NOT coalesce(a.cancelled, false)
           AND a.nuts_code IS NOT NULL AND a.nuts_code <> ''
         GROUP BY substr(a.nuts_code, 1, 4)
    """, (user_id, op_ids, list(_AWARD_TYPES)))

    c.execute("""
        INSERT INTO proc.company_profile_buyer
              (user_id, authority_id, n_acts, total_value)
        SELECT %s, a.authority_id, count(DISTINCT a.adam),
               coalesce(sum(coalesce(ao.awarded_value_with_vat,
                        proc.resolved_value(a.adam, a.total_cost_with_vat))), 0)
          FROM proc.act_operator ao
          JOIN proc.procurement_act a ON a.adam = ao.adam
         WHERE ao.operator_id = ANY(%s) AND a.type = ANY(%s)
           AND NOT coalesce(a.cancelled, false)
           AND a.authority_id IS NOT NULL
         GROUP BY a.authority_id
    """, (user_id, op_ids, list(_AWARD_TYPES)))

    return {"ok": True, "n_awards": n_awards,
            "n_buyers": int(agg.get("n_buyers") or 0),
            "operator_ids": op_ids, "banded": banded}


def load_profile(c, user_id: int) -> Profile | None:
    """The stored profile, shaped for the scorer. None when there is none."""
    c.execute("""SELECT * FROM proc.company_profile WHERE user_id = %s""",
              (user_id,))
    row = c.fetchone()
    if not row:
        return None

    c.execute("""SELECT cpv_prefix, sum(n_acts) AS n
                   FROM proc.company_profile_cpv WHERE user_id = %s
                  GROUP BY cpv_prefix""", (user_id,))
    cpv_rows = c.fetchall()
    # Weight WITHIN each depth, not across all of them. Normalising globally
    # measures every prefix against the busiest division, so an 8-digit code —
    # necessarily a fraction of its own division — always scores near zero and
    # the deepest, most specific match is punished hardest. That is backwards,
    # and it showed up immediately on a real firm: a medical supplier's core
    # CPV group scored 0.36 on its own speciality.
    peak: dict[int, int] = {}
    for r in cpv_rows:
        d = len(r["cpv_prefix"])
        peak[d] = max(peak.get(d, 0), int(r["n"]))
    cpv = {r["cpv_prefix"]: min(1.0, int(r["n"]) / (peak[len(r["cpv_prefix"])] or 1))
           for r in cpv_rows}

    c.execute("""SELECT DISTINCT nuts_prefix FROM proc.company_profile_nuts
                  WHERE user_id = %s""", (user_id,))
    nuts = {r["nuts_prefix"] for r in c.fetchall()}

    c.execute("""SELECT authority_id FROM proc.company_profile_buyer
                  WHERE user_id = %s""", (user_id,))
    buyers = {r["authority_id"] for r in c.fetchall()}

    lo = row.get("value_min_override")
    if lo is None:
        lo = row.get("value_p10")
    hi = row.get("value_max_override")
    if hi is None:
        hi = row.get("value_p90")
    return Profile(user_id=user_id, cpv=cpv, nuts=nuts, buyers=buyers,
                   value_min=lo, value_max=hi,
                   n_awards=int(row.get("n_awards") or 0))


# --------------------------------------------------------------------------- #
# Ranking open tenders
# --------------------------------------------------------------------------- #
def open_tenders(c, profile: Profile, *, limit: int = 200) -> list[dict]:
    """Candidate open notices, pre-filtered to the firm's CPV divisions.

    The filter is the point: scoring every open notice would rank a few
    hundred rows the firm has no business bidding for, and the divisions are
    the cheapest honest cut. Ranking happens in Python because the components
    have to be explainable, and a CASE expression that produced the same
    number would not be.
    """
    divisions = sorted({p for p in profile.cpv if len(p) == 2})
    if not divisions:
        return []
    c.execute("""
        SELECT a.adam, a.title, a.nuts_code, a.authority_id, a.signed_date,
               a.final_submission_date, a.total_cost_with_vat,
               proc.resolved_value(a.adam, a.total_cost_with_vat) AS resolved_value,
               auth.name AS authority_name,
               array_remove(array_agg(DISTINCT oc.cpv_code), NULL) AS cpvs
          FROM proc.procurement_act a
          LEFT JOIN proc.authority auth ON auth.org_id = a.authority_id
          JOIN proc.act_object_detail od ON od.adam = a.adam
          JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
         WHERE a.type = 'notice'
           AND NOT coalesce(a.cancelled, false)
           AND a.final_submission_date > now()
           AND substr(oc.cpv_code, 1, 2) = ANY(%s)
         GROUP BY a.adam, a.title, a.nuts_code, a.authority_id, a.signed_date,
                  a.final_submission_date, a.total_cost_with_vat, auth.name
         ORDER BY a.final_submission_date
         LIMIT %s
    """, (divisions, limit))
    return c.fetchall()


def rank(c, user_id: int, *, limit: int = 25) -> dict:
    """The whole thing: profile -> candidates -> scored, best first."""
    profile = load_profile(c, user_id)
    if profile is None:
        return {"profile": None, "rows": [],
                "reason": "δεν έχει δημιουργηθεί προφίλ ακόμη"}
    if not profile.is_usable:
        return {"profile": profile, "rows": [],
                "reason": "το προφίλ δεν έχει ιστορικό CPV για ταίριασμα"}
    scored = []
    for act in open_tenders(c, profile):
        detail = explain(profile, act, list(act.get("cpvs") or []))
        scored.append({**act, **detail})
    # Deadline breaks ties: between two equally good fits, the one closing
    # sooner is the one worth looking at today.
    scored.sort(key=lambda r: (-r["score"], r["final_submission_date"]))
    return {"profile": profile, "rows": scored[:limit],
            "n_candidates": len(scored), "reason": None}
