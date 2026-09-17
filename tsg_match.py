#!/usr/bin/env python3
"""tsg_match.py — is this Tender Service notice a tender we already show?

docs/specs/tender-service-duplicates.md is the design; this is the build.

One decision per Tender Service record, stored on the record
(proc.tsg_record.match_*) and applied to its act:

  hidden   — an EXACT number says it is the same tender as an act we show:
             the record's own ID (ext_id), the ΕΣΗΔΗΣ system number (esidis), a
             quoted ΑΔΑΜ / request / ΑΔΑ (quoted_*), another Tender Service
             record with the same number (tsg_twin), or an admin's confirm
             (admin). A hidden act is kept, never deleted: procurement_act.
             duplicate_of points at the act we show.
  flagged  — only LIKELY: same authority code, deadline within a day, and a
             title / budget / reference-number match (tiers 1–3). Shown, labelled
             in alerts, queued in proc.duplicate_candidate for review.
  new      — neither.

What never happens, and why (measured on one week, spec §2):
  * A fuzzy match never hides. Hospitals publish dozens of tenders a day with
    the same generic title and a two-day deadline; "same authority, same
    deadline" alone was right 0 times in 12.
  * An exact number hides only behind a notice whose deadline is within
    tsg_exact_deadline_days: a re-tender quotes the ΑΔΑΜ of the notice that
    failed. Otherwise it becomes a tier 1 flag. Several notices inside that
    window are one procedure published twice (ΠΕΡΙΛΗΨΗ + ΔΙΑΚΗΡΥΞΗ, measured:
    55 promitheus records a week) and hide behind the closest.
  * An admin's confirm or reject is never undone by a re-check, a re-import or
    a re-projection.

The candidate block is "same authority code": Tender Service's
authorityIdentifier IS our proc.authority.org_id (the ΚΗΜΔΗΣ organisation code)
for ~95% of authorities. It misses some real twins (promitheus often names a
different authority than the ΚΗΜΔΗΣ notice), which is why exact rules never
depend on it.

Database functions take a dict-row cursor: the app's, or Database.dict_cursor()
inside an ingestion transaction. Nothing here commits.
"""
from __future__ import annotations

import collections
import datetime as dt
import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

NOTICE_LABEL = "Προκήρυξη"
PREFIX = "TSG:"

DEFAULTS = {
    "max_shared": 30,
    "tsg_title_min": 50,
    "tsg_deadline_days": 1,
    "tsg_exact_deadline_days": 3,
    "tsg_budget_tolerance_cents": 50,
    "tsg_round_budget_candidates": 5,
    "tsg_recheck_days": 45,
}

# Which copy stays when two Tender Service records are the same tender. The
# portal the tender actually runs on first; hospital and school sites last.
SOURCE_RANK = {
    "promitheus-gov-gr": 1,
    "http://opendata.diavgeia.gov.gr": 2,
    "manual_input": 3,
    "isupplies.gr": 4,
}
OTHER_RANK = 5

VAT_RATES = (0, 6, 13, 17, 24)
MAX_CANDIDATES = 3
EXACT_RULES = ("ext_id", "esidis", "quoted_adam", "quoted_req", "quoted_ada")
TEXT_FIELDS = ("title", "contentDescription", "tenderText", "referenceNumber",
               "authorityReference")

# Stripped from a title before it is compared: what a portal appends, not what
# the authority wrote.
BOILERPLATE = [
    re.compile(r"\s*-\s*αριθμός διαγωνισμού\s*:\s*\d+\s*$", re.I),   # isupplies
    re.compile(r"\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*EUR\s*$"),              # dypethessaly
    re.compile(r"^\s*\d{1,2}\.\s+"),                                    # promitheus lots
]

_ESIDIS = re.compile(r"^eproc-(\d{3,9})$")
_ADAM_PROC = re.compile(r"(?<![0-9A-Z])(\d{2}PROC\d{9})(?!\d)")
_ADAM_REQ = re.compile(r"(?<![0-9A-Z])(\d{2}REQ\d{9})(?!\d)")
# An ΑΔΑ only counts next to its label: bare 'XXXX-YYY' shapes are everywhere.
_ADA = re.compile(r"(?:ΑΔΑ|Α\.Δ\.Α\.?)(?!Μ)[^0-9Α-ΩA-Z]{0,5}([0-9Α-ΩA-Z]{6,10}-[0-9Α-ΩA-Z]{3})(?![0-9Α-ΩA-Z])")
_GREEK_CAP = re.compile(r"[Α-Ω]")
_TAGS = re.compile(r"<[^>]+>")
_TITLE_NUM = re.compile(r"(?<![\d.,])(\d{4,7})(?![\d.,]\d|\d)")
_WORD = re.compile(r"\w+", re.U)


# --------------------------------------------------------------------------- #
# pure functions (tests/test_tsg_match.py)
# --------------------------------------------------------------------------- #
def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").replace("ς", "σ")


def strip_boilerplate(title: str | None) -> str:
    s = html.unescape(title or "").strip()
    for rx in BOILERPLATE:
        s = rx.sub("", s)
    return s.strip()


def _trigrams(s: str) -> set[str]:
    """pg_trgm's trigram set: words of letters/digits, padded '  w '."""
    out: set[str] = set()
    for w in _WORD.findall(fold(s)):
        w = w.replace("_", "")
        if not w:
            continue
        p = f"  {w} "
        out.update(p[i:i + 3] for i in range(len(p) - 2))
    return out


def similarity(a: str | None, b: str | None) -> float:
    """pg_trgm similarity() over folded text — computed here so the result does
    not depend on which schema holds the extension."""
    ta, tb = _trigrams(a or ""), _trigrams(b or "")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def source_rank(data_source: str | None) -> int:
    return SOURCE_RANK.get(data_source or "", OTHER_RANK)


def _text(r: dict) -> str:
    parts = []
    for f in TEXT_FIELDS:
        v = r.get(f)
        if v:
            parts.append(html.unescape(_TAGS.sub(" ", str(v))))
    return "\n".join(parts)


def esidis_of(r: dict) -> str | None:
    m = _ESIDIS.match(str(r.get("externalId") or "").strip())
    return m.group(1) if m else None


def quoted_numbers(r: dict) -> dict[str, list[str]]:
    """Official numbers the record QUOTES: {'adam': [...], 'req': [...], 'ada': [...]}."""
    text = _text(r)
    ada = [m for m in dict.fromkeys(_ADA.findall(text)) if _GREEK_CAP.search(m)]
    return {"adam": list(dict.fromkeys(_ADAM_PROC.findall(text))),
            "req": list(dict.fromkeys(_ADAM_REQ.findall(text))),
            "ada": ada}


def record_keys(r: dict, own_ada: str | None = None) -> list[str]:
    """The exact keys two Tender Service records can share ('esidis:521020',
    'adam:26PROC…', 'req:26REQ…', 'ada:ΨΚΕΘ…'). A Διαύγεια record's own ΑΔΑ is
    one: the isupplies copy of the same invitation quotes it."""
    q = quoted_numbers(r)
    keys = []
    e = esidis_of(r)
    if e:
        keys.append(f"esidis:{e}")
    keys += [f"adam:{x}" for x in q["adam"]]
    keys += [f"req:{x}" for x in q["req"]]
    adas = list(q["ada"])
    if own_ada and own_ada not in adas:
        adas.append(own_ada)
    keys += [f"ada:{x}" for x in adas]
    return keys


def title_numbers(title: str | None) -> list[str]:
    """4–7 digit reference numbers in a title ('26/0006050', '(24337-', 'α.π. 14240').
    Years are not references."""
    out = []
    for n in _TITLE_NUM.findall(strip_boilerplate(title)):
        if re.fullmatch(r"20[2-3]\d", n):
            continue
        out.append(n.lstrip("0") or n)
    return list(dict.fromkeys(out))


def budget_match(est, net, gross, tolerance_cents: int = 50) -> int | str | None:
    """The VAT rate at which the record's estimate equals our amount, 'gross'
    when it equals our with-VAT total, else None. isupplies publishes amounts
    with VAT; ΚΗΜΔΗΣ stores them without."""
    if est is None:
        return None
    est = Decimal(str(est))
    tol = Decimal(tolerance_cents) / 100
    if net is not None:
        net = Decimal(str(net))
        for rate in VAT_RATES:
            if abs(est - (net * (100 + rate) / 100).quantize(Decimal("0.01"))) <= tol:
                return rate
    if gross is not None and abs(est - Decimal(str(gross))) <= tol:
        return "gross"
    return None


def is_round(amount) -> bool:
    return amount is not None and Decimal(str(amount)) % 100 == 0


def tier_of(sig: dict, s: dict) -> int | None:
    """The tier a candidate earns (spec §3.4), or None for no flag."""
    if sig.get("guard"):
        return 1
    title_ok = (sig.get("title") or 0) * 100 >= s["tsg_title_min"]
    budget = sig.get("budget") is not None
    if budget and sig.get("round_budget") and sig.get("n_candidates", 0) > s["tsg_round_budget_candidates"]:
        budget = False
    ref = bool(sig.get("ref_no"))
    if title_ok and (budget or ref):
        return 1
    if budget and ref:
        return 1
    if title_ok:
        return 2
    if budget or ref:
        return 3
    return None


def _strength(sig: dict) -> tuple:
    return (bool(sig.get("guard")), sig.get("budget") is not None, bool(sig.get("ref_no")),
            round(sig.get("title") or 0, 4))


def deadline_of(r: dict, today: dt.date | None = None) -> dt.date | None:
    import tsg_ingest as tg
    d = tg.parse_date(r.get("deadlineDate"), today)
    return d.date() if d else None


# --------------------------------------------------------------------------- #
# settings / helpers
# --------------------------------------------------------------------------- #
def settings(c) -> dict:
    s = dict(DEFAULTS)
    try:
        c.execute("SELECT key, value FROM proc.match_setting WHERE key = ANY(%s)", (list(DEFAULTS),))
        for row in c.fetchall():
            s[row["key"]] = int(row["value"])
    except Exception:  # noqa: BLE001 — a missing table means defaults
        pass
    return s


def _near(a: dt.date | None, b, days: int) -> bool:
    if a is None or b is None:
        return False
    if isinstance(b, dt.datetime):
        b = b.date()
    return abs((a - b).days) <= days


@dataclass
class Decision:
    outcome: str                        # new | hidden | flagged | out_of_scope
    rule: str | None = None
    target: str | None = None           # hidden: the act shown instead
    tier: int | None = None
    candidates: list = field(default_factory=list)   # [{adam, tier, signals}]
    notes: list = field(default_factory=list)        # guard names, for warnings
    recheck: list = field(default_factory=list)      # twins to re-decide


# --------------------------------------------------------------------------- #
# exact rules
# --------------------------------------------------------------------------- #
def _notices(c, adams: list[str]) -> list[dict]:
    if not adams:
        return []
    c.execute("""SELECT a.adam, a.final_submission_date, a.submission_date
                   FROM proc.procurement_act a
                  WHERE a.adam = ANY(%s) AND a.type = 'notice'
                    AND a.data_source IS DISTINCT FROM 'tsg'
                    AND a.duplicate_of IS NULL""", (adams,))
    return list(c.fetchall())


def resolve_exact(c, r: dict, s: dict) -> list[tuple[str, list[dict]]]:
    """[(rule, [our notices it names])] for every exact rule that names any.
    A number named by more than max_shared acts is a placeholder, not an identity."""
    import tsg_ingest as tg
    out = []
    cap = s["max_shared"]

    held = tg.held_keys(r)
    if held:
        c.execute("""SELECT adam, final_submission_date, submission_date FROM proc.procurement_act
                      WHERE adam = ANY(%s) AND data_source IS DISTINCT FROM 'tsg'""", (held,))
        rows = list(c.fetchall())
        if rows:
            out.append(("ext_id", rows[:1]))

    if r.get("typeOfDocument") != NOTICE_LABEL:
        return out

    e = esidis_of(r)
    if e:
        c.execute("""SELECT adam, final_submission_date, submission_date FROM proc.procurement_act
                      WHERE raw_json @> %s::jsonb AND type = 'notice'
                        AND data_source = 'khmdhs' AND duplicate_of IS NULL
                      LIMIT %s""",
                  (json.dumps({"systemicNumbers": [{"systemicNumber": e}]}), cap + 1))
        rows = list(c.fetchall())
        if rows and len(rows) <= cap:
            out.append(("esidis", rows))

    q = quoted_numbers(r)
    rows = _notices(c, q["adam"])
    if rows and len(rows) <= cap:
        out.append(("quoted_adam", rows))
    if q["req"]:
        c.execute("""SELECT DISTINCT p.adam, p.final_submission_date, p.submission_date
                       FROM proc.act_link l
                       JOIN proc.procurement_act p ON p.adam = l.target_adam
                      WHERE l.source_adam = ANY(%s) AND p.type = 'notice'
                        AND p.duplicate_of IS NULL
                      LIMIT %s""", (q["req"], cap + 1))
        rows = list(c.fetchall())
        if rows and len(rows) <= cap:
            out.append(("quoted_req", rows))
    rows = _notices(c, q["ada"])
    if rows and len(rows) <= cap:
        out.append(("quoted_ada", rows))
    return out


def _closest(rows: list[dict], deadline: dt.date) -> dict:
    """ΚΗΜΔΗΣ often publishes one procedure twice — a ΠΕΡΙΛΗΨΗ and the full
    ΔΙΑΚΗΡΥΞΗ, same ΕΣΗΔΗΣ number, same deadline. Any of them is the tender;
    take the nearest deadline, then the latest published, so the choice is
    stable."""
    def key(row):
        d = row["final_submission_date"]
        d = d.date() if isinstance(d, dt.datetime) else d
        pub = row.get("submission_date")
        return (abs((d - deadline).days), -(pub.timestamp() if pub else 0), row["adam"])
    return min(rows, key=key)


def decide_exact(c, r: dict, deadline: dt.date | None, s: dict) -> Decision | None:
    """Hidden when a rule names a notice whose deadline is within
    tsg_exact_deadline_days (several such notices are one procedure published
    twice); a tier 1 flag when every notice it names closes elsewhere — a
    re-tender quotes the ΑΔΑΜ, request or ΕΣΗΔΗΣ number of the one that failed."""
    found = resolve_exact(c, r, s)
    if not found:
        return None
    for rule, rows in found:
        if rule == "ext_id":
            # The record's own identity: no guard, it is the same document.
            return Decision("hidden", rule=rule, target=rows[0]["adam"])
        near = [row for row in rows if _near(deadline, row["final_submission_date"],
                                             s["tsg_exact_deadline_days"])]
        if near:
            notes = ["several_notices"] if len(near) > 1 else []
            return Decision("hidden", rule=rule, target=_closest(near, deadline)["adam"], notes=notes)
    # Every rule that named something closes elsewhere (or we cannot tell):
    # flag what they named.
    cands = {}
    for rule, rows in found:
        for row in rows:
            cands.setdefault(row["adam"], {"adam": row["adam"], "tier": 1,
                                           "signals": {"guard": "exact_far_deadline", "rule": rule}})
    ordered = list(cands.values())[:MAX_CANDIDATES]
    return Decision("flagged", tier=1, candidates=ordered, notes=["exact_far_deadline"])


# --------------------------------------------------------------------------- #
# Tender Service twins
# --------------------------------------------------------------------------- #
def decide_twin(c, internal: str, r: dict, keys: list[str], deadline, s: dict) -> Decision | None:
    """Another SHOWN Tender Service notice, from ANOTHER portal, shares an exact
    key. The better-ranked source stays; this one is hidden behind it, or the
    others are re-decided. Two records of one portal sharing a number are its
    lots or its amendments, not a copy — a portal does not republish itself."""
    if not keys:
        return None
    c.execute("""SELECT t.internal_id, t.projected_adam, t.data_source, t.deadline_date
                   FROM proc.tsg_record t
                   JOIN proc.procurement_act p ON p.adam = t.projected_adam
                  WHERE t.match_keys && %s::text[] AND t.internal_id <> %s
                    AND t.type_label = %s AND p.duplicate_of IS NULL
                    AND t.data_source IS DISTINCT FROM %s
                  LIMIT %s""", (keys, internal, NOTICE_LABEL, r.get("dataSource"),
                                s["max_shared"] + 1))
    twins = [t for t in c.fetchall()
             if _near(deadline, t["deadline_date"], s["tsg_exact_deadline_days"])]
    if not twins or len(twins) > s["max_shared"]:
        return None
    me = (source_rank(r.get("dataSource")), internal)
    best = min(twins, key=lambda t: (source_rank(t["data_source"]), t["internal_id"]))
    if (source_rank(best["data_source"]), best["internal_id"]) < me:
        return Decision("hidden", rule="tsg_twin", target=best["projected_adam"])
    return Decision("new", recheck=[t["internal_id"] for t in twins])


# --------------------------------------------------------------------------- #
# fuzzy candidates
# --------------------------------------------------------------------------- #
def fuzzy_candidates(c, internal: str, adam: str, r: dict, deadline, s: dict) -> list[dict]:
    aid = str(r.get("authorityIdentifier") or "").strip()
    if not aid or deadline is None or r.get("typeOfDocument") != NOTICE_LABEL:
        return []
    import tsg_ingest as tg
    days = s["tsg_deadline_days"]
    lo, hi = deadline - dt.timedelta(days=days), deadline + dt.timedelta(days=days + 1)
    nums = title_numbers(r.get("title"))
    title = strip_boilerplate(r.get("title"))
    est = tg.parse_amount(r.get("estimatedPrices") or r.get("estimatedPricesBelow"))[0]
    cpvs = {x.split("-")[0][:8] for x in str(r.get("cpvCodes") or "").split() if x[:8].isdigit()}
    num_sql = """EXISTS (SELECT 1 FROM unnest(%s::text[]) n
                          WHERE {t} ~ ('(^|[^0-9])0*' || n || '([^0-9]|$)')
                             OR {f} ~ ('(^|[^0-9])0*' || n || '([^0-9]|$)'))"""

    c.execute("""SELECT candidate_adam FROM proc.duplicate_candidate
                  WHERE adam = %s AND status = 'rejected'""", (adam,))
    rejected = {row["candidate_adam"] for row in c.fetchall()}

    c.execute(f"""
        SELECT a.adam, a.title, a.total_cost_without_vat AS net, a.total_cost_with_vat AS gross,
               {num_sql.format(t='a.title', f='coalesce(a.full_text, %s)')} AS ref_hit,
               (SELECT array_agg(DISTINCT left(oc.cpv_code, 8))
                  FROM proc.act_object_detail od
                  JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                 WHERE od.adam = a.adam) AS cpvs,
               NULL::text AS data_source, NULL::text AS internal_id
          FROM proc.procurement_act a
         WHERE a.authority_id = %s AND a.type = 'notice'
           AND a.data_source IN ('khmdhs', 'diavgeia')
           AND a.final_submission_date >= %s AND a.final_submission_date < %s
           AND a.duplicate_of IS NULL
        UNION ALL
        SELECT p.adam, t.raw_json->>'title', p.total_cost_without_vat, NULL,
               {num_sql.format(t="coalesce(t.raw_json->>'title', '')", f='coalesce(p.full_text, %s)')},
               NULL, t.data_source, t.internal_id
          FROM proc.tsg_record t
          JOIN proc.procurement_act p ON p.adam = t.projected_adam
         WHERE t.authority_code = %s AND t.deadline_date BETWEEN %s AND %s
           AND t.internal_id <> %s AND t.type_label = %s
           AND t.data_source IS DISTINCT FROM %s
           AND p.duplicate_of IS NULL""",
              (nums, "", aid, lo, hi, nums, "", aid, lo, hi - dt.timedelta(days=1), internal, NOTICE_LABEL,
               r.get("dataSource")))
    rows = [row for row in c.fetchall() if row["adam"] not in rejected and row["adam"] != adam]
    me = (source_rank(r.get("dataSource")), internal)
    # Two shown Tender Service copies (from different portals — a hospital's own
    # records share its generic titles): only the worse-ranked one is flagged.
    rows = [row for row in rows if row["internal_id"] is None
            or (source_rank(row["data_source"]), row["internal_id"]) < me]
    out = []
    for row in rows:
        other_title = strip_boilerplate(row["title"])
        sig = {
            "title": round(similarity(title, other_title), 3),
            "budget": budget_match(est, row["net"], row["gross"], s["tsg_budget_tolerance_cents"]),
            "round_budget": is_round(est),
            "ref_no": bool(nums) and bool(row["ref_hit"]),
            "cpv": bool(cpvs and row["cpvs"] and cpvs & set(row["cpvs"])),
            "n_candidates": len(rows),
        }
        tier = tier_of(sig, s)
        if tier:
            out.append({"adam": row["adam"], "tier": tier, "signals": sig})
    out.sort(key=lambda x: (x["tier"], tuple(not v for v in _strength(x["signals"]))))
    return out[:MAX_CANDIDATES]


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #
def confirmed_target(c, adam: str) -> str | None:
    c.execute("""SELECT d.candidate_adam, p.duplicate_of
                   FROM proc.duplicate_candidate d
                   JOIN proc.procurement_act p ON p.adam = d.candidate_adam
                  WHERE d.adam = %s AND d.status = 'confirmed'
                  ORDER BY d.decided_at DESC NULLS LAST LIMIT 1""", (adam,))
    row = c.fetchone()
    if not row:
        return None
    return row["duplicate_of"] or row["candidate_adam"]


def decide(c, internal: str, r: dict, *, act_exists: bool, s: dict,
           today: dt.date | None = None, phase: str = "all") -> Decision:
    """phase 'identity' stops before the fuzzy search (it needs the act row)."""
    adam = PREFIX + internal
    deadline = deadline_of(r, today)
    if act_exists:
        target = confirmed_target(c, adam)
        if target:
            return Decision("hidden", rule="admin", target=target)
    d = decide_exact(c, r, deadline, s)
    if d and d.outcome == "hidden":
        return d
    guard_flag = d
    twin = None
    if r.get("typeOfDocument") == NOTICE_LABEL:
        import tsg_ingest as tg
        keys = record_keys(r, tg.ada_of(r.get("externalId")))
        twin = decide_twin(c, internal, r, keys, deadline, s)
        if twin and twin.outcome == "hidden":
            return twin
    if phase == "identity":
        return guard_flag or Decision("new", recheck=twin.recheck if twin else [])
    cands = list(guard_flag.candidates) if guard_flag else []
    seen = {x["adam"] for x in cands}
    if act_exists:
        for x in fuzzy_candidates(c, internal, adam, r, deadline, s):
            if x["adam"] not in seen:
                cands.append(x)
                seen.add(x["adam"])
    cands = cands[:MAX_CANDIDATES]
    recheck = twin.recheck if twin else []
    if cands:
        return Decision("flagged", tier=min(x["tier"] for x in cands), target=cands[0]["adam"],
                        candidates=cands, notes=guard_flag.notes if guard_flag else [],
                        recheck=recheck)
    return Decision("new", recheck=recheck)


def _copy_reminders(c, hidden: str, target: str) -> None:
    """A reminder mark already spent on the hidden copy must not fire again for
    the act that replaces it. The ledger matches on the deadline, so the copy is
    written with the TARGET's deadline."""
    c.execute("""INSERT INTO proc.digest_deadline_notice (subscription_id, adam, lead_days, deadline, run_id)
                 SELECT n.subscription_id, p.adam, n.lead_days, p.final_submission_date, n.run_id
                   FROM proc.digest_deadline_notice n
                   JOIN proc.procurement_act p ON p.adam = %s
                  WHERE n.adam = %s
                 ON CONFLICT (subscription_id, adam, lead_days) DO NOTHING""", (target, hidden))
    # The push ledger only exists where the mobile API is installed.
    c.execute("SELECT to_regclass('proc.push_deadline_notice') IS NOT NULL AS ok")
    if c.fetchone()["ok"]:
        c.execute("""INSERT INTO proc.push_deadline_notice (push_subscription_id, adam, lead_days, deadline_at)
                     SELECT n.push_subscription_id, p.adam, n.lead_days, p.final_submission_date
                       FROM proc.push_deadline_notice n
                       JOIN proc.procurement_act p ON p.adam = %s
                      WHERE n.adam = %s AND p.final_submission_date IS NOT NULL
                     ON CONFLICT DO NOTHING""", (target, hidden))


def apply(c, internal: str, r: dict, d: Decision, *, act_exists: bool,
          run_id: int | None = None) -> str:
    """Write the decision; returns the transition, e.g. 'new→flagged'."""
    adam = PREFIX + internal
    c.execute("""SELECT t.match_outcome, p.duplicate_of, (p.adam IS NOT NULL) AS has_act
                   FROM proc.tsg_record t
                   LEFT JOIN proc.procurement_act p ON p.adam = t.projected_adam
                  WHERE t.internal_id = %s""", (internal,))
    prev = c.fetchone() or {}
    before = prev.get("match_outcome") or "none"
    if before == "hidden" and prev.get("has_act") and prev.get("duplicate_of") is None:
        before = "unhidden"          # the act it pointed at was deleted

    if d.outcome == "hidden":
        if act_exists:
            # Nothing may point at an act that is itself hidden.
            c.execute("""UPDATE proc.procurement_act SET duplicate_of = %s
                          WHERE duplicate_of = %s""", (d.target, adam))
            c.execute("""UPDATE proc.procurement_act SET duplicate_of = %s
                          WHERE adam = %s AND duplicate_of IS DISTINCT FROM %s""",
                      (d.target, adam, d.target))
            if c.rowcount:
                _copy_reminders(c, adam, d.target)
            c.execute("""UPDATE proc.duplicate_candidate SET status = 'superseded', last_seen_at = now()
                          WHERE adam = %s AND status = 'pending'""", (adam,))
        c.execute("""UPDATE proc.tsg_record
                        SET match_outcome = 'hidden', match_rule = %s, match_tier = NULL,
                            matched_adam = %s, held_adam = %s,
                            projected_adam = CASE WHEN %s THEN %s END,
                            matched_at = CASE WHEN match_outcome IS DISTINCT FROM 'hidden'
                                               OR matched_adam IS DISTINCT FROM %s
                                              THEN now() ELSE matched_at END
                      WHERE internal_id = %s""",
                  (d.rule, d.target, d.target, act_exists, adam, d.target, internal))
        return f"{before}→hidden"

    if act_exists:
        c.execute("""UPDATE proc.procurement_act SET duplicate_of = NULL
                      WHERE adam = %s AND duplicate_of IS NOT NULL""", (adam,))
    keep = [x["adam"] for x in d.candidates]
    for rank, x in enumerate(d.candidates, 1):
        c.execute("""INSERT INTO proc.duplicate_candidate
                       (adam, candidate_adam, tier, rank, signals, found_by_run)
                     VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                     ON CONFLICT (adam, candidate_adam) DO UPDATE
                       SET tier = EXCLUDED.tier, rank = EXCLUDED.rank,
                           signals = EXCLUDED.signals, last_seen_at = now(),
                           status = CASE WHEN proc.duplicate_candidate.status = 'superseded'
                                         THEN 'pending' ELSE proc.duplicate_candidate.status END""",
                  (adam, x["adam"], x["tier"], rank, json.dumps(x["signals"], default=str), run_id))
    c.execute("""UPDATE proc.duplicate_candidate SET status = 'superseded', last_seen_at = now()
                  WHERE adam = %s AND status = 'pending' AND NOT (candidate_adam = ANY(%s))""",
              (adam, keep))
    c.execute("""UPDATE proc.tsg_record
                    SET match_outcome = %s, match_rule = NULL, match_tier = %s,
                        matched_adam = %s, held_adam = NULL,
                        matched_at = CASE WHEN match_outcome IS DISTINCT FROM %s
                                           OR matched_adam IS DISTINCT FROM %s
                                          THEN now() ELSE matched_at END
                  WHERE internal_id = %s""",
              (d.outcome, d.tier, d.target, d.outcome, d.target, internal))
    return f"{before}→{d.outcome}"


def record_scope(c, internal: str, r: dict, today: dt.date | None = None) -> None:
    """The lookup columns matching reads, refreshed from the payload."""
    import tsg_ingest as tg
    keys = record_keys(r, tg.ada_of(r.get("externalId"))) if r.get("typeOfDocument") == NOTICE_LABEL else []
    c.execute("""UPDATE proc.tsg_record
                    SET authority_code = %s, deadline_date = %s, match_keys = %s::text[]
                  WHERE internal_id = %s""",
              (str(r.get("authorityIdentifier") or "").strip() or None,
               deadline_of(r, today), keys, internal))


# --------------------------------------------------------------------------- #
# one pass: counts, warnings, run row
# --------------------------------------------------------------------------- #
class Run:
    """One matching pass. A record may be decided twice in one pass (re-checked,
    then re-projected); counts use each record's LAST decision, so the summary
    adds up to the records, not to the work done. Transitions count every change."""

    def __init__(self, c, trigger: str, job_id: int | None = None):
        self.c, self.trigger = c, trigger
        self.final: dict[str, tuple] = {}
        self.transitions = collections.Counter()
        self.decided: set[str] = set()
        self.pending = 0
        c.execute("INSERT INTO proc.tsg_match_run (trigger, job_id) VALUES (%s, %s) RETURNING id",
                  (trigger, job_id))
        self.id = c.fetchone()["id"]

    def note(self, internal: str, r: dict, d: Decision, transition: str, *, recheck: bool) -> None:
        self.decided.add(internal)
        notice = r.get("typeOfDocument") == NOTICE_LABEL
        self.final[internal] = (r.get("dataSource") or "?", d.outcome, d.rule,
                                d.tier if d.outcome == "flagged" else None, tuple(d.notes),
                                notice, bool(notice and esidis_of(r)),
                                bool(r.get("authorityIdentifier")))
        before, after = transition.split("→")
        if (recheck and before != after) or before == "unhidden":
            self.transitions[transition] += 1

    def _tally(self):
        outcomes, rules, tiers, notes = (collections.Counter() for _ in range(4))
        by_source = collections.defaultdict(collections.Counter)
        esidis, authority = collections.Counter(), collections.Counter()
        for src, outcome, rule, tier, nts, notice, has_esidis, has_code in self.final.values():
            outcomes[outcome] += 1
            by_source[src][outcome] += 1
            if rule:
                rules[rule] += 1
            if tier:
                tiers[str(tier)] += 1
            for n in nts:
                notes[n] += 1
            if has_esidis:
                esidis["eligible"] += 1
                esidis["matched"] += int(rule == "esidis")
            if notice:
                authority["notices"] += 1
                authority["with_code"] += int(has_code)
        return outcomes, rules, tiers, notes, by_source, esidis, authority

    def counts(self) -> dict:
        outcomes, rules, tiers, notes, by_source, esidis, authority = self._tally()
        return {"outcomes": dict(outcomes), "rules": dict(rules),
                "tiers": dict(tiers), "transitions": dict(self.transitions),
                "guards": dict(notes),
                "by_source": {k: dict(v) for k, v in sorted(by_source.items())},
                "esidis": dict(esidis), "authority": dict(authority)}

    def warnings(self) -> list[dict]:
        c, out = self.c, []
        _o, _r, _t, notes, _b, esidis, authority = self._tally()
        el, hit = esidis["eligible"], esidis["matched"]
        if el >= 10 and hit / el < 0.10:
            c.execute("""SELECT counts->'esidis' AS e FROM proc.tsg_match_run
                          WHERE id <> %s AND finished_at IS NOT NULL
                            AND (counts->'esidis'->>'eligible')::int > 0
                          ORDER BY id DESC LIMIT 7""", (self.id,))
            hist = [row["e"] for row in c.fetchall()]
            h_el = sum(int(e.get("eligible", 0)) for e in hist)
            h_hit = sum(int(e.get("matched", 0)) for e in hist)
            if not hist or (h_el and h_hit / h_el >= 0.5):
                out.append({"code": "rule_went_quiet", "rule": "esidis",
                            "message": f"esidis matched {hit} of {el} promitheus notices "
                                       f"(usual ≥ 50%) — feed format changed?"})
        notices = authority["notices"]
        if notices >= 20:
            with_code = authority["with_code"]
            unknown = notices - with_code
            if unknown / notices > 0.10:
                out.append({"code": "unknown_authority_rate",
                            "message": f"{unknown} of {notices} notices carry no authority code"})
        for code in ("exact_far_deadline",):
            if notes.get(code):
                out.append({"code": code, "message": f"{notes[code]} records hit the {code} guard"})
        c.execute("""SELECT count(*) AS n, min(first_found_at) AS oldest
                       FROM proc.duplicate_candidate WHERE status = 'pending'""")
        row = c.fetchone()
        self.pending = int(row["n"])
        oldest = row["oldest"]
        if self.pending > 500 or (oldest and oldest < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)):
            out.append({"code": "review_backlog",
                        "message": f"{self.pending} possible duplicates await review"
                                   + (f", oldest since {oldest:%Y-%m-%d}" if oldest else "")})
        unhidden = sum(v for k, v in self.transitions.items() if k.startswith("unhidden"))
        if unhidden:
            out.append({"code": "unhidden",
                        "message": f"{unhidden} hidden acts came back: the act they pointed at was deleted"})
        return out

    def finish(self) -> dict:
        warnings = self.warnings()
        counts = self.counts()
        counts["pending_review"] = self.pending
        self.c.execute("""UPDATE proc.tsg_match_run SET finished_at = now(), counts = %s::jsonb,
                                 warnings = %s::jsonb WHERE id = %s""",
                       (json.dumps(counts), json.dumps(warnings, ensure_ascii=False), self.id))
        return {"run_id": self.id, **counts, "warnings": warnings}


def match_one(c, run: Run, internal: str, r: dict, *, s: dict, today=None, recheck=False) -> Decision:
    """Re-decide a record whose act may or may not exist (no projection)."""
    adam = PREFIX + internal
    c.execute("SELECT 1 FROM proc.procurement_act WHERE adam = %s", (adam,))
    exists = c.fetchone() is not None
    d = decide(c, internal, r, act_exists=exists, s=s, today=today)
    if d.outcome != "hidden" and not exists:
        # Shown now but never projected (it was hidden at first sight): the
        # projection pass picks it up.
        c.execute("UPDATE proc.tsg_record SET projected_hash = NULL WHERE internal_id = %s", (internal,))
        return d
    t = apply(c, internal, r, d, act_exists=exists, run_id=run.id)
    run.note(internal, r, d, t, recheck=recheck)
    return d


def match_open(c, run: Run, *, today: dt.date | None = None, limit: int | None = None) -> int:
    """Re-check every Tender Service record whose answer can still change:
    shown notices that have not closed, hidden ones whose target is gone, and
    any record hidden by its own ID (all document types)."""
    today = today or dt.date.today()
    s = settings(c)
    c.execute("""
        SELECT t.internal_id, t.raw_json
          FROM proc.tsg_record t
          LEFT JOIN proc.procurement_act p ON p.adam = t.projected_adam
         WHERE t.skip_reason IS NULL AND t.projection_error IS NULL
           AND t.match_outcome IS NOT NULL
           AND (
                 (t.type_label = %(notice)s
                  AND t.projected_adam IS NOT NULL
                  AND coalesce(t.deadline_date, %(today)s) >= %(today)s - 1
                  AND t.first_seen_at >= %(since)s)
              OR (t.match_outcome = 'hidden' AND t.held_adam IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM proc.procurement_act h WHERE h.adam = t.held_adam))
              OR (t.match_outcome = 'hidden' AND p.adam IS NOT NULL AND p.duplicate_of IS NULL)
              OR (t.match_outcome <> 'hidden' AND cardinality(t.held_keys) > 0
                  AND EXISTS (SELECT 1 FROM proc.procurement_act h
                               WHERE h.adam = ANY(t.held_keys)
                                 AND h.data_source IS DISTINCT FROM 'tsg'))
           )
         ORDER BY t.internal_id
         LIMIT %(limit)s""",
              {"notice": NOTICE_LABEL, "today": today,
               "since": dt.datetime.combine(today - dt.timedelta(days=s["tsg_recheck_days"]),
                                            dt.time(), dt.timezone.utc),
               "limit": limit})
    rows = [row for row in c.fetchall() if row["internal_id"] not in run.decided]
    for row in rows:
        raw = row["raw_json"] if isinstance(row["raw_json"], dict) else json.loads(row["raw_json"])
        match_one(c, run, row["internal_id"], raw, s=s, today=today, recheck=True)
    return len(rows)


def format_counts(m: dict) -> str:
    o = m.get("outcomes", {})
    lines = ["matching: " + " ".join(f"{k}={o.get(k, 0)}" for k in ("new", "hidden", "flagged", "out_of_scope"))]
    if m.get("rules"):
        lines.append("  hidden by rule:  " + " ".join(f"{k}={v}" for k, v in sorted(m["rules"].items())))
    if m.get("tiers") or "pending_review" in m:
        lines.append("  flagged by tier: " + " ".join(f"{k}={v}" for k, v in sorted(m.get("tiers", {}).items()))
                     + f"   (pending review now: {m.get('pending_review', 0)})")
    if m.get("transitions"):
        lines.append("  re-check:        " + " ".join(f"{k}={v}" for k, v in sorted(m["transitions"].items())))
    if m.get("by_source"):
        lines.append("  by source:       " + " | ".join(
            f"{src} " + " ".join(f"{k}={v}" for k, v in sorted(c.items()))
            for src, c in m["by_source"].items()))
    for w in m.get("warnings", []):
        lines.append(f"WARNING {w['message']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# after another source's catch-up / by hand
# --------------------------------------------------------------------------- #
def enabled(db) -> bool:
    """Tender Service is in use on this database: the ingester would run here and
    it has stored records."""
    import os
    import tsg_ingest as tg
    if not tg.database_allowed(getattr(db, "dsn", None) or os.environ.get("DATABASE_URL")):
        return False
    try:
        rows = db.query("SELECT EXISTS (SELECT 1 FROM proc.tsg_record)")
    except Exception:  # noqa: BLE001 — no table: not in use
        db.rollback()
        return False
    return bool(rows and rows[0][0])


def recheck(db, trigger: str, *, today: dt.date | None = None, dry_run: bool = False,
            job_id: int | None = None) -> dict:
    """A matching pass with no projection: after ΚΗΜΔΗΣ / Διαύγεια catch-up (a
    twin may just have arrived), or `db.py tsg-match`. Records hidden at first
    sight that are now shown are left for the next projection."""
    import tsg_ingest as tg
    c = db.dict_cursor()
    run = Run(c, trigger, job_id)
    n = match_open(c, run, today=today)
    out = run.finish()
    out["rechecked"] = n
    if dry_run:
        db.rollback()
    else:
        db.commit()
        waiting = db.query("""SELECT count(*) FROM proc.tsg_record
                              WHERE projected_hash IS DISTINCT FROM content_hash""")[0][0]
        if waiting:
            out["projection"] = tg.project_all(db, today=today, trigger=trigger + "+project")
    return out


# --------------------------------------------------------------------------- #
# admin decisions (app/interconnect.py)
# --------------------------------------------------------------------------- #
_TSG_TABLES: bool | None = None


def tsg_tables(c) -> bool:
    """Whether this database has the Tender Service tables. Production does
    not until Tender Service goes live; the web app must work without them
    (the core migration is deliberately independent of them). A positive
    answer is cached; a negative one is re-asked, so installing the tables does
    not need a restart."""
    global _TSG_TABLES
    if _TSG_TABLES:
        return True
    c.execute("SELECT to_regclass('proc.tsg_record') IS NOT NULL AS ok")
    row = c.fetchone()
    _TSG_TABLES = bool(row["ok"] if isinstance(row, dict) else row[0])
    return _TSG_TABLES

def labels_for(c, adams: list[str]) -> dict[str, dict]:
    """{adam: best pending candidate} for alert labels: title, source, date, tier
    and how many more there are. Only candidates still shown."""
    if not adams:
        return {}
    c.execute("""SELECT d.adam, d.candidate_adam, d.tier, a.title, a.data_source,
                        a.submission_date, a.ingested_at,
                        count(*) OVER (PARTITION BY d.adam) AS n
                   FROM proc.duplicate_candidate d
                   JOIN proc.procurement_act a ON a.adam = d.candidate_adam
                   JOIN proc.procurement_act me ON me.adam = d.adam
                  WHERE d.adam = ANY(%s) AND d.status = 'pending'
                    AND a.duplicate_of IS NULL AND me.duplicate_of IS NULL
                  ORDER BY d.adam, d.rank, d.tier""", (list(adams),))
    out: dict[str, dict] = {}
    for row in c.fetchall():
        if row["adam"] not in out:
            out[row["adam"]] = dict(row, more=int(row["n"]) - 1)
    return out


def _record(c, adam: str):
    if not tsg_tables(c):
        return None, None
    c.execute("SELECT internal_id, raw_json FROM proc.tsg_record WHERE projected_adam = %s", (adam,))
    row = c.fetchone()
    if not row:
        return None, None
    raw = row["raw_json"] if isinstance(row["raw_json"], dict) else json.loads(row["raw_json"])
    return row["internal_id"], raw


def confirm(c, candidate_id: int, user_id: int | None) -> str:
    """The pair is the same tender: hide the Tender Service act behind it."""
    c.execute("SELECT adam, candidate_adam FROM proc.duplicate_candidate WHERE id = %s", (candidate_id,))
    pair = c.fetchone()
    if not pair:
        raise LookupError("candidate not found")
    c.execute("""UPDATE proc.duplicate_candidate
                    SET status = 'confirmed', decided_by = %s, decided_at = now()
                  WHERE id = %s""", (user_id, candidate_id))
    internal, raw = _record(c, pair["adam"])
    target = confirmed_target(c, pair["adam"])
    d = Decision("hidden", rule="admin", target=target)
    if internal:
        apply(c, internal, raw, d, act_exists=True)
    else:
        c.execute("UPDATE proc.procurement_act SET duplicate_of = %s WHERE adam = %s", (target, pair["adam"]))
        _copy_reminders(c, pair["adam"], target)
    c.execute("""UPDATE proc.duplicate_candidate SET status = 'superseded'
                  WHERE adam = %s AND status = 'pending'""", (pair["adam"],))
    try:
        from app import interconnect
        interconnect.set_duplicate(c, pair["adam"], target, by=str(user_id or "tsg"))
    except Exception:  # noqa: BLE001 — the overlay is a courtesy, the hide is the decision
        pass
    return pair["adam"]


def reject(c, candidate_id: int, user_id: int | None) -> str:
    """Not the same tender: never flag this pair again; re-decide the act."""
    c.execute("""UPDATE proc.duplicate_candidate
                    SET status = 'rejected', decided_by = %s, decided_at = now()
                  WHERE id = %s RETURNING adam""", (user_id, candidate_id))
    row = c.fetchone()
    if not row:
        raise LookupError("candidate not found")
    _redecide(c, row["adam"])
    return row["adam"]


def unhide(c, adam: str, user_id: int | None) -> None:
    """Undo a hide: the pair behind it becomes 'rejected' so nothing re-hides it
    automatically — except an exact number, which is not a judgement."""
    c.execute("SELECT duplicate_of FROM proc.procurement_act WHERE adam = %s", (adam,))
    row = c.fetchone()
    if not row or not row["duplicate_of"]:
        return
    target = row["duplicate_of"]
    c.execute("""INSERT INTO proc.duplicate_candidate (adam, candidate_adam, tier, status, decided_by, decided_at)
                 VALUES (%s, %s, 1, 'rejected', %s, now())
                 ON CONFLICT (adam, candidate_adam) DO UPDATE
                   SET status = 'rejected', decided_by = EXCLUDED.decided_by, decided_at = now()""",
              (adam, target, user_id))
    c.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE adam = %s", (adam,))
    try:
        from app import interconnect
        interconnect.clear_duplicate(c, adam)
    except Exception:  # noqa: BLE001
        pass
    _redecide(c, adam, skip_exact_target=target)


def _redecide(c, adam: str, skip_exact_target: str | None = None) -> None:
    internal, raw = _record(c, adam)
    if not internal:
        return
    s = settings(c)
    d = decide(c, internal, raw, act_exists=True, s=s)
    if d.outcome == "hidden" and d.target == skip_exact_target and d.rule != "ext_id":
        d = Decision("new")
    apply(c, internal, raw, d, act_exists=True)


def queue(c, *, status: str = "pending", source: str | None = None, tier: int | None = None,
          alerted: bool = False, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """The review list, soonest deadline first. `alerted` keeps the pairs whose
    Tender Service act already reached a customer (an email or a push) — the
    flags someone has actually seen, reviewed first when the queue is long."""
    if not tsg_tables(c):
        return [], 0
    where = ["d.status = %s"]
    args: list = [status]
    if alerted:
        where.append("""(EXISTS (SELECT 1 FROM proc.digest_run_item ri WHERE ri.adam = d.adam AND ri.in_email)
                         OR (to_regclass('proc.notification_event') IS NOT NULL
                             AND EXISTS (SELECT 1 FROM proc.notification_event ne WHERE ne.adam = d.adam)))""")
    if source:
        where.append("t.data_source = %s")
        args.append(source)
    if tier:
        where.append("d.tier = %s")
        args.append(int(tier))
    sql_where = " AND ".join(where)
    c.execute(f"""SELECT count(*) AS n FROM proc.duplicate_candidate d
                  JOIN proc.tsg_record t ON t.projected_adam = d.adam
                  WHERE {sql_where}""", args)
    total = int(c.fetchone()["n"])
    c.execute(f"""
        SELECT d.id, d.adam, d.candidate_adam, d.tier, d.rank, d.signals, d.status,
               d.first_found_at, d.decided_at, u.username AS decided_by_name,
               t.data_source AS tsg_source,
               me.title AS tsg_title, me.final_submission_date AS tsg_deadline,
               me.total_cost_without_vat AS tsg_value, me.duplicate_of AS tsg_hidden_behind,
               o.title AS other_title, o.final_submission_date AS other_deadline,
               o.total_cost_without_vat AS other_net, o.total_cost_with_vat AS other_gross,
               o.data_source AS other_source,
               auth.name AS authority_name
          FROM proc.duplicate_candidate d
          JOIN proc.tsg_record t ON t.projected_adam = d.adam
          JOIN proc.procurement_act me ON me.adam = d.adam
          JOIN proc.procurement_act o ON o.adam = d.candidate_adam
          LEFT JOIN proc.authority auth ON auth.org_id = o.authority_id
          LEFT JOIN proc.app_user u ON u.id = d.decided_by
         WHERE {sql_where}
         ORDER BY me.final_submission_date NULLS LAST, d.tier, d.adam, d.rank
         LIMIT %s OFFSET %s""", args + [limit, offset])
    return [dict(r) for r in c.fetchall()], total


def queue_sources(c) -> list[str]:
    if not tsg_tables(c):
        return []
    c.execute("""SELECT DISTINCT t.data_source FROM proc.duplicate_candidate d
                 JOIN proc.tsg_record t ON t.projected_adam = d.adam ORDER BY 1""")
    return [r["data_source"] for r in c.fetchall() if r["data_source"]]


def recent_runs(c, limit: int = 20) -> list[dict]:
    c.execute("""SELECT id, trigger, started_at, finished_at, counts, warnings
                   FROM proc.tsg_match_run ORDER BY id DESC LIMIT %s""", (limit,))
    return [dict(r) for r in c.fetchall()]
