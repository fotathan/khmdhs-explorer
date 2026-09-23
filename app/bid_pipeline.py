"""
bid_pipeline.py — where a customer stands on each favourite, and what the
award ledger says happened. Spec: docs/specs/bid-pipeline.md.

What it is
----------
A stage on a favourite: bidding → submitted → won / lost, or no_bid. No stage
(NULL) means just a bookmark. It lives in columns on proc.user_favorite_act,
so the pipeline IS the favourites list — removing the star forgets the stage.

Why it exists
-------------
Two reasons, the second the important one:
  1. a customer's own "what am I bidding on" list, on the page they already use;
  2. win/loss data. fit.py's weights are argued, not fitted, because nobody has
     told us which tenders a customer went for and how it ended. This is how
     they tell us.

What the ledger adds (detect_outcomes)
--------------------------------------
Competitors ask the customer how it ended. We can LOOK: follow the act's
lifecycle chain to its award acts (auction / contract) and read the winners
from proc.act_operator. If a winner is one of the customer's own ledger
identities (fit.operator_ids_for — the ΑΦΜ an ADMIN linked, never the
onboarding claim), it is a detected win; if the award names only others, it
went elsewhere.

**Detection never writes.** It is shown next to the stage with a one-click
confirm, and confirm_from_ledger RE-RUNS detection server-side rather than
trusting anything the form sends — the same rule as company_match: a form field
is not a source for something written into a customer record. Reasons it must
stay a suggestion:
  * lots — a multi-lot tender can name the customer on one lot and others on
    the rest; "awarded to others" on the lot they bid for is not "we lost";
  * joint ventures bid under their own ΑΦΜ, not the members';
  * the ledger only records WINNERS. It cannot tell "lost" from "never bid".

Only the customer's own record is read or written. Nothing here touches
proc.act_ai_summary (see fit.py's isolation rule).
"""
from __future__ import annotations

try:
    from app import fit as _fit
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import fit as _fit

# (code, Greek label). Greek is the i18n key; the templates run it through t().
STAGES = (
    ("bidding",   "Ετοιμάζουμε προσφορά"),
    ("submitted", "Υποβλήθηκε προσφορά"),
    ("won",       "Κερδήθηκε"),
    ("lost",      "Δεν κερδήθηκε"),
    ("no_bid",    "Δεν συμμετέχουμε"),
)
STAGE_CODES = tuple(code for code, _ in STAGES)
STAGE_LABELS = dict(STAGES)
NO_STAGE_LABEL = "Χωρίς στάδιο"

# Stages where "what happened?" is still an open question, so the ledger is
# worth asking. A bookmark with no stage is not nagged; a closed stage is done.
OPEN_STAGES = ("bidding", "submitted")

NOTE_MAX = 300

# The lifecycle edges from a tender towards its award. The same relation names
# main.py's act-page chain walks, minus payments (not an award) and
# contract_next (an extension, not an outcome). Walked forward only.
_AWARD_RELATIONS = [
    "request_to_notice", "request_to_auction", "request_to_contract",
    "notice_to_auction", "auction_to_contract",
]
_AWARD_TYPES = ["auction", "contract"]
_MAX_DEPTH = 4

_DETECT_SQL = """
    WITH RECURSIVE chain AS (
        SELECT r.adam AS root, r.adam AS adam, 0 AS depth, ARRAY[r.adam] AS path
          FROM unnest(%s::text[]) AS r(adam)
        UNION ALL
        SELECT c.root, l.target_adam, c.depth + 1, c.path || l.target_adam
          FROM chain c
          JOIN proc.act_link l ON l.source_adam = c.adam
         WHERE c.depth < %s
           AND NOT (l.target_adam = ANY (c.path))
           AND l.relation = ANY (%s::proc.link_relation[])
    )
    SELECT DISTINCT ch.root, a.adam AS award_adam, a.signed_date,
           o.operator_id, eo.name AS winner_name
      FROM chain ch
      JOIN proc.procurement_act a ON a.adam = ch.adam
      JOIN proc.act_operator o ON o.adam = a.adam AND o.role = 'winner'
      LEFT JOIN proc.economic_operator eo ON eo.operator_id = o.operator_id
     WHERE a.type = ANY (%s::proc.act_type[])
       AND NOT coalesce(a.cancelled, false)
     ORDER BY ch.root, a.signed_date NULLS LAST, a.adam
"""


def normalize_stage(value) -> str | None:
    """'' / None → None (no stage). Anything not a known code → ValueError."""
    v = (value or "").strip()
    if not v:
        return None
    if v not in STAGE_CODES:
        raise ValueError(f"unknown stage {v!r}")
    return v


def set_stage(c, user_id: int, adam: str, stage: str | None,
              note: str | None) -> dict | None:
    """Set the stage and note on one of THIS user's favourites, by hand.

    Returns the updated row, or None when the act is not their favourite (a
    stage has nowhere to live without the star). A changed stage is stamped
    with the time and becomes source 'user' — which also drops any ledger
    outcome, since the customer has overruled it. Editing only the note keeps
    the stage, its time and its source as they were."""
    note = (note or "").strip()[:NOTE_MAX] or None
    c.execute("""
        UPDATE proc.user_favorite_act SET
            bid_stage_at = CASE WHEN bid_stage IS DISTINCT FROM %(s)s
                                THEN now() ELSE bid_stage_at END,
            bid_stage_source = CASE
                WHEN %(s)s IS NULL THEN NULL
                WHEN bid_stage IS DISTINCT FROM %(s)s THEN 'user'
                ELSE bid_stage_source END,
            bid_outcome_adam = CASE WHEN bid_stage IS DISTINCT FROM %(s)s
                                    THEN NULL ELSE bid_outcome_adam END,
            bid_stage = %(s)s,
            bid_note = %(n)s
        WHERE user_id = %(u)s AND adam = %(a)s
        RETURNING adam, bid_stage, bid_note, bid_stage_at, bid_stage_source,
                  bid_outcome_adam""",
              {"s": stage, "n": note, "u": user_id, "a": adam})
    return c.fetchone()


def get_stage(c, user_id: int, adam: str) -> dict | None:
    c.execute("""SELECT adam, bid_stage, bid_note, bid_stage_at,
                        bid_stage_source, bid_outcome_adam
                   FROM proc.user_favorite_act
                  WHERE user_id = %s AND adam = %s""", (user_id, adam))
    return c.fetchone()


def stage_counts(c, user_id: int) -> dict:
    """Favourites per stage, over the WHOLE list (not the page's cap).
    Key None = no stage."""
    c.execute("""SELECT bid_stage, count(*) AS n FROM proc.user_favorite_act
                  WHERE user_id = %s GROUP BY bid_stage""", (user_id,))
    return {r["bid_stage"]: int(r["n"]) for r in c.fetchall()}


def summary(counts: dict) -> dict:
    """The pipeline headline. 'submitted' counts every favourite that got at
    least that far (submitted + won + lost); the win rate is over DECIDED
    tenders only, and is None until one is decided."""
    won, lost = counts.get("won", 0), counts.get("lost", 0)
    decided = won + lost
    return {
        "submitted": counts.get("submitted", 0) + decided,
        "won": won, "lost": lost,
        "win_rate": round(100 * won / decided) if decided else None,
    }


def detect_outcomes(c, user_id: int, adams) -> dict:
    """What the award ledger says about each of these acts. READ-ONLY.

    Returns {adam: outcome} only for acts that reached an award with a named
    winner; an act with no award yet is simply absent. outcome:
        kind         'won'   — a winner is one of this customer's identities
                     'other' — the award names only others
        award_adam   the award act to link to (for 'won': one naming them)
        winners      [names], the customer's own first
        can_confirm  whether confirm_from_ledger would record it: a win always;
                     'other' only when we KNOW who the customer is (without a
                     linked ΑΦΜ, "not them" is not something we can tell)
    """
    adams = sorted({str(a) for a in (adams or []) if a})
    if not adams:
        return {}
    mine = set(_fit.operator_ids_for(c, user_id))
    c.execute(_DETECT_SQL, (adams, _MAX_DEPTH, _AWARD_RELATIONS, _AWARD_TYPES))
    by_root: dict[str, list] = {}
    for r in c.fetchall():
        by_root.setdefault(r["root"], []).append(r)

    out = {}
    for root, rows in by_root.items():
        own = [r for r in rows if r["operator_id"] in mine]
        pick = own[0] if own else rows[0]
        names, seen = [], set()
        for r in own + [r for r in rows if r["operator_id"] not in mine]:
            if r["operator_id"] in seen:
                continue
            seen.add(r["operator_id"])
            names.append(r["winner_name"] or "—")
        out[root] = {
            "kind": "won" if own else "other",
            "award_adam": pick["award_adam"],
            "winners": names,
            "can_confirm": bool(own) or bool(mine),
        }
    return out


def confirm_from_ledger(c, user_id: int, adam: str) -> dict | None:
    """Record the stage the ledger shows, with the award it came from.

    Re-detects here instead of taking a stage or an award from the request.
    Returns the updated row, or None when there is nothing to confirm (not a
    favourite, no award, or 'other' for a customer we cannot identify)."""
    if not get_stage(c, user_id, adam):
        return None
    found = detect_outcomes(c, user_id, [adam]).get(adam)
    if not found or not found["can_confirm"]:
        return None
    stage = "won" if found["kind"] == "won" else "lost"
    c.execute("""
        UPDATE proc.user_favorite_act SET
            bid_stage_at = CASE WHEN bid_stage IS DISTINCT FROM %(s)s
                                THEN now() ELSE bid_stage_at END,
            bid_stage = %(s)s,
            bid_stage_source = 'ledger',
            bid_outcome_adam = %(o)s
        WHERE user_id = %(u)s AND adam = %(a)s
        RETURNING adam, bid_stage, bid_note, bid_stage_at, bid_stage_source,
                  bid_outcome_adam""",
              {"s": stage, "o": found["award_adam"], "u": user_id, "a": adam})
    return c.fetchone()
