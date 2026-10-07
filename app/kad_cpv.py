"""kad_cpv.py — what a company with no award history probably sells.

fit.py ranks open tenders for a firm from what it has WON. A customer who has
never won anything (or never bid) has no such history, but the registry still
says what they do: their ΚΑΔ. This module turns a ΚΑΔ into CPV codes and builds
an ordinary fit.Profile from them, so the same scorer ranks the same tenders.
docs/specs/crm-brief-kad.md.

**There is no official ΚΑΔ/NACE→CPV table to use.** The only CPV↔CPA mapping
was an annex to Regulation 2195/2002, on CPA 96 / NACE Rev. 1, deleted since;
ΚΑΔ 2025 is NACE Rev. 2.1 / CPA 2.2 (checked 2026-10-07). Two sources instead:

  1. **The ΚΑΔ's own description (the default).** An 8-digit ΚΑΔ names the
     product or service ("ΧΟΝΔΡΙΚΟ ΕΜΠΟΡΙΟ ΙΑΤΡΙΚΩΝ ΑΝΑΛΩΣΙΜΩΝ ΥΛΙΚΩΝ"). It is
     matched against the Greek CPV descriptions word by word (Postgres's Greek
     stemmer, rarer words weigh more), after dropping the trade/manufacturing
     filler (DESC_STOP) and limiting the CPV type by the ΚΑΔ's NACE section: a
     trader or manufacturer sells GOODS, a builder does WORKS. Needs nothing
     but the CPV list. Measured on 731 enriched contractors (2026-10-07):
     at least one right group in the top 5 for 88% of firms vs 66% for the
     five most popular groups; `kad_cpv_map.py evaluate` repeats it.

  2. **Learned from the ledger — measured, NOT used.** `build` counts which
     CPVs the contractors under each ΚΑΔ win (in firms, gated by MIN_FIRMS /
     MIN_SUPPORT / MIN_LIFT, 8 → 6 → 4 digits). On 747 enriched contractors
     it did worse than the naive guess even in-sample (hit 39% vs 66%): the
     contractors we could enrich are the largest, and they win a little of
     everything. It needs every contractor's ΚΑΔ from ΓΕΜΗ, which the registry
     rate-limits to one call every 6-8 s. `build` stays for two things: the
     slim operator_kad table behind the "same ΚΑΔ" peer list, and `evaluate`
     comparing it again if the enrichment ever grows.

**An estimate, labelled as one, stored nowhere.** Built on request like the
fit score itself. Value and buyer have no history to compare against, so they
score as fit.py already scores a missing history (neutral / none).

Region: the registered postal code's first two digits name the NUTS-2 region
(POSTAL_NUTS2). Our authorities carry no NUTS code and our contractors almost
no postal code, so the registry's address is the only place to read it from.
It enters the score (fit.W_GEO), never filters.

Isolation, as fit.py: never reads act_ai_summary; reads no customer data
beyond the ΑΦΜ an admin linked and that company's own registry record.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass

from psycopg.types.json import Json

try:
    from app import fit as _fit
    from app import gemi_client
except ImportError:                       # pragma: no cover - run with --app-dir=app
    import fit as _fit
    import gemi_client

# --------------------------------------------------------------------------- #
# Learning thresholds. Argued, not fitted (there is no win/loss data). Tuned
# once against the first full build — see the spec's measurement section.
# --------------------------------------------------------------------------- #
LEARN_YEARS = 5          # awards signed within this window teach the mapping
MIN_FIRMS = 5            # firms behind a (ΚΑΔ, CPV) pair before it counts
MIN_SUPPORT = 0.10       # ... and at least this share of the ΚΑΔ's firms
MIN_LIFT = 2.0           # ... and this many times the share of ALL firms
KAD_LEVELS = (8, 6, 4)   # deepest first; 2 digits ('46' = all wholesale) says nothing

SECONDARY_FACTOR = 0.5   # a secondary ΚΑΔ counts half the primary
MAX_SECONDARY = 10       # firms list dozens; past this they only add noise

RECENT_YEARS = 3         # window for "who wins there now" (peers, leaders)
PEERS_MAX = 15
LEADERS_MAX = 15
LEADER_GROUPS = 3        # CPV groups the market-leader query searches
LEADER_MIN_WEIGHT = 0.3  # ... each at least this central to the estimate
AREAS_MAX = 6            # CPV groups shown as "what they probably sell"

_AWARD_TYPES = _fit._AWARD_TYPES

# Greek postal code -> NUTS-2 (2021) region, by the first two digits. Ranges
# from ΕΛΤΑ's numbering, which follows the regional units; checked against
# the island cases that cross a boundary: Kythira (801) is Attica, Lefkada (311)
# and Corfu (49x) the Ionian Islands, the Sporades (370) Thessaly, Skyros (340)
# Central Greece, Thasos (640) and Samothrace (680) East Macedonia-Thrace.
POSTAL_NUTS2: dict[str, str] = {}
for _codes, _region in (
        (range(10, 20), "EL30"), ((80,), "EL30"),
        ((20, 21, 22, 23, 24), "EL65"),
        ((25, 26, 27, 30), "EL63"),
        ((28, 29, 31, 49), "EL62"),
        ((32, 33, 34, 35, 36), "EL64"),
        ((37, 38, 40, 41, 42, 43), "EL61"),
        ((44, 45, 46, 47, 48), "EL54"),
        ((50, 51, 52, 53), "EL53"),
        (range(54, 64), "EL52"),
        (range(64, 70), "EL51"),
        (range(70, 75), "EL43"),
        ((81, 82, 83), "EL41"),
        ((84, 85), "EL42")):
    for _n in _codes:
        POSTAL_NUTS2[f"{_n:02d}"] = _region

# The 13 regions as the search filter names them (main.NUTS_REGIONS — main
# cannot be imported from here; a test pins that the two agree).
REGION_LABELS = {
    "EL30": "Αττική", "EL51": "Αν. Μακεδονία & Θράκη",
    "EL52": "Κεντρική Μακεδονία", "EL53": "Δυτική Μακεδονία",
    "EL54": "Ήπειρος", "EL61": "Θεσσαλία", "EL62": "Ιόνια Νησιά",
    "EL63": "Δυτική Ελλάδα", "EL64": "Στερεά Ελλάδα",
    "EL65": "Πελοπόννησος", "EL41": "Βόρειο Αιγαίο",
    "EL42": "Νότιο Αιγαίο", "EL43": "Κρήτη",
}

_DIGITS = re.compile(r"\D+")


def nuts2_from_postal(postal: str | None) -> str | None:
    """'546 25' -> 'EL52'. None for anything that is not a 5-digit Greek code."""
    digits = _DIGITS.sub("", postal or "")
    if len(digits) != 5:
        return None
    return POSTAL_NUTS2.get(digits[:2])


def kad_digits(kad: str | None) -> str:
    """'46.46.02.12' or '46460212' -> '46460212'."""
    return _DIGITS.sub("", kad or "")


# --------------------------------------------------------------------------- #
# Build — learn the mapping from the ledger (kad_cpv_map.py build)
# --------------------------------------------------------------------------- #
def build(conn, *, years: int = LEARN_YEARS, min_firms: int = MIN_FIRMS,
          min_support: float = MIN_SUPPORT, min_lift: float = MIN_LIFT) -> dict:
    """Replace kad_cpv_map / operator_kad from gemi_enrichment + the ledger.

    One transaction: a failed build leaves the previous mapping in place.
    `conn` is a psycopg connection with dict rows. Returns the build stats.
    """
    with conn.transaction():
        c = conn.cursor()
        c.execute("""SELECT afm, primary_kad, zip_code FROM proc.gemi_enrichment
                      WHERE fetch_status = 'ok' AND primary_kad IS NOT NULL""")
        firms: dict[str, tuple[str, str | None]] = {}
        for r in c.fetchall():
            afm = gemi_client.normalize_afm(r["afm"])
            kad = kad_digits(r["primary_kad"])
            if afm and len(kad) == 8:
                firms[afm] = (kad, nuts2_from_postal(r["zip_code"]))

        c.execute("""CREATE TEMP TABLE _kad_firm (afm text PRIMARY KEY, kad text,
                                                  nuts2 text) ON COMMIT DROP""")
        with c.copy("COPY _kad_firm (afm, kad, nuts2) FROM STDIN") as cp:
            for afm, (kad, nuts2) in firms.items():
                cp.write_row((afm, kad, nuts2))
        c.execute("ANALYZE _kad_firm")

        # The ledger spells a Greek ΑΦΜ three ways (crm._vat_candidates).
        c.execute("""CREATE TEMP TABLE _kad_op ON COMMIT DROP AS
                     SELECT DISTINCT f.afm, eo.operator_id
                       FROM _kad_firm f
                       JOIN proc.economic_operator eo
                         ON eo.vat_number = ANY(ARRAY[f.afm, 'EL' || f.afm,
                                                      ltrim(f.afm, '0')])""")
        c.execute("""CREATE TEMP TABLE _kad_cpv ON COMMIT DROP AS
                     SELECT DISTINCT o.afm, v.prefix AS cpv_prefix
                       FROM _kad_op o
                       JOIN proc.act_operator ao ON ao.operator_id = o.operator_id
                       JOIN proc.procurement_act a ON a.adam = ao.adam
                       JOIN proc.act_object_detail od ON od.adam = a.adam
                       JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
                       CROSS JOIN LATERAL (VALUES (substr(oc.cpv_code, 1, 2)),
                                                  (substr(oc.cpv_code, 1, 4)),
                                                  (oc.cpv_code::text)) v(prefix)
                      WHERE a.type = ANY(%s)
                        AND NOT coalesce(a.cancelled, false)
                        AND a.signed_date >= now() - make_interval(years => %s)""",
                  (list(_AWARD_TYPES), years))
        c.execute("ANALYZE _kad_cpv")
        c.execute("SELECT count(DISTINCT afm) AS n FROM _kad_cpv")
        population = int(c.fetchone()["n"])

        c.execute("DELETE FROM proc.kad_cpv_map")
        if population:
            for level in KAD_LEVELS:
                c.execute("""
                    WITH f AS (SELECT p.afm, p.cpv_prefix, left(k.kad, %(lvl)s) AS kp
                                 FROM _kad_cpv p JOIN _kad_firm k USING (afm)),
                         kf AS (SELECT kp, count(DISTINCT afm) AS kad_firms
                                  FROM f GROUP BY kp),
                         base AS (SELECT cpv_prefix,
                                         count(DISTINCT afm)::float8 / %(pop)s AS share
                                    FROM _kad_cpv GROUP BY cpv_prefix),
                         pairs AS (SELECT kp, cpv_prefix, count(DISTINCT afm) AS n
                                     FROM f GROUP BY kp, cpv_prefix)
                    INSERT INTO proc.kad_cpv_map
                        (kad_prefix, cpv_prefix, n_firms, kad_firms, support, lift)
                    SELECT p.kp, p.cpv_prefix, p.n, kf.kad_firms,
                           p.n::float8 / kf.kad_firms,
                           (p.n::float8 / kf.kad_firms) / b.share
                      FROM pairs p
                      JOIN kf USING (kp)
                      JOIN base b USING (cpv_prefix)
                     WHERE p.n >= %(min_firms)s
                       AND p.n::float8 / kf.kad_firms >= %(min_support)s
                       AND (p.n::float8 / kf.kad_firms) / b.share >= %(min_lift)s
                """, {"lvl": level, "pop": population, "min_firms": min_firms,
                      "min_support": min_support, "min_lift": min_lift})

        # The peer list on prod reads this slim copy, never the raw records.
        c.execute("DELETE FROM proc.operator_kad")
        c.execute("""INSERT INTO proc.operator_kad (afm, kad, nuts2)
                     SELECT f.afm, f.kad, f.nuts2 FROM _kad_firm f
                      WHERE EXISTS (SELECT 1 FROM _kad_op o WHERE o.afm = f.afm)""")
        n_ops = c.rowcount

        c.execute("SELECT count(*) AS n FROM proc.kad_cpv_map")
        n_pairs = int(c.fetchone()["n"])
        params = {"min_firms": min_firms, "min_support": min_support,
                  "min_lift": min_lift, "levels": list(KAD_LEVELS),
                  "enriched": len(firms), "operators": n_ops}
        c.execute("""INSERT INTO proc.kad_cpv_build
                         (n_firms, n_pairs, window_years, params)
                     VALUES (%s, %s, %s, %s)""",
                  (population, n_pairs, years, Json(params)))
    return {"n_firms": population, "n_pairs": n_pairs, **params}


def push(src, dst) -> dict:
    """Copy the built tables from `src` (local) to `dst` (production), whole.

    Prod's free tier cannot hold the raw registry records the build reads, so
    the build runs locally and only its result travels. One transaction on
    the target: a failed push leaves prod's previous mapping in place.
    """
    sc = src.cursor()
    sc.execute("""SELECT kad_prefix, cpv_prefix, n_firms, kad_firms, support, lift
                    FROM proc.kad_cpv_map""")
    pairs = [tuple(r.values()) for r in sc.fetchall()]
    sc.execute("SELECT afm, kad, nuts2 FROM proc.operator_kad")
    ops = [tuple(r.values()) for r in sc.fetchall()]
    sc.execute("""SELECT built_at, n_firms, n_pairs, window_years, params
                    FROM proc.kad_cpv_build ORDER BY id DESC LIMIT 1""")
    last = sc.fetchone()
    if not last or not pairs:
        raise RuntimeError("nothing to push — run `kad_cpv_map.py build` first")
    with dst.transaction():
        dc = dst.cursor()
        dc.execute("DELETE FROM proc.kad_cpv_map")
        with dc.copy("""COPY proc.kad_cpv_map (kad_prefix, cpv_prefix, n_firms,
                                               kad_firms, support, lift)
                        FROM STDIN""") as cp:
            for row in pairs:
                cp.write_row(row)
        dc.execute("DELETE FROM proc.operator_kad")
        with dc.copy("COPY proc.operator_kad (afm, kad, nuts2) FROM STDIN") as cp:
            for row in ops:
                cp.write_row(row)
        dc.execute("""INSERT INTO proc.kad_cpv_build
                          (built_at, n_firms, n_pairs, window_years, params)
                      VALUES (%s, %s, %s, %s, %s)""",
                   (last["built_at"], last["n_firms"], last["n_pairs"],
                    last["window_years"], Json(last["params"])))
    return {"pairs": len(pairs), "operators": len(ops),
            "built_at": last["built_at"]}


# --------------------------------------------------------------------------- #
# Reading it — one customer
# --------------------------------------------------------------------------- #
def last_build(c) -> dict | None:
    c.execute("""SELECT built_at, n_firms, n_pairs, window_years
                   FROM proc.kad_cpv_build ORDER BY id DESC LIMIT 1""")
    return c.fetchone()


def customer_afm(c, uid: int) -> str | None:
    """The ΑΦΜ an admin LINKED (company match), else the one on the profile.

    Never onboarding's declared_afm: that is the customer's own claim, and
    nothing is derived from a claim (CLAUDE.md, first-login wizard)."""
    c.execute("SELECT afm FROM proc.customer_company_match WHERE user_id = %s", (uid,))
    row = c.fetchone()
    if row and gemi_client.normalize_afm(row["afm"]):
        return gemi_client.normalize_afm(row["afm"])
    c.execute("""SELECT vat_number, tax_number FROM proc.customer_profile
                  WHERE user_id = %s""", (uid,))
    row = c.fetchone() or {}
    return (gemi_client.normalize_afm(row.get("vat_number"))
            or gemi_client.normalize_afm(row.get("tax_number")))


def registry_row(c, afm: str | None) -> dict | None:
    if not afm:
        return None
    c.execute("""SELECT afm, legal_name, trade_title, status, status_id, zip_code,
                        city, prefecture, primary_kad, primary_kad_descr,
                        activities_active
                   FROM proc.gemi_enrichment
                  WHERE afm = %s AND fetch_status = 'ok'""", (afm,))
    return c.fetchone()


def company_kads(gemi: dict | None) -> list[dict]:
    """[{kad, descr, primary}], the primary first. From the active activities
    the registry lists; the stored primary_kad column when it lists none."""
    if not gemi:
        return []
    out, seen = [], set()
    for a in gemi.get("activities_active") or []:
        kad = kad_digits(a.get("id"))
        if len(kad) != 8 or kad in seen:
            continue
        seen.add(kad)
        out.append({"kad": kad, "descr": a.get("descr"),
                    "primary": (a.get("type") or "").strip() == "Κύρια"})
    primary = kad_digits(gemi.get("primary_kad"))
    if len(primary) == 8 and primary not in seen:
        out.append({"kad": primary, "descr": gemi.get("primary_kad_descr"),
                    "primary": True})
    if out and not any(k["primary"] for k in out):
        out[0]["primary"] = True
    out.sort(key=lambda k: not k["primary"])          # stable: registry order
    return out


def cpv_labels(c, prefixes, lang: str = "el") -> dict[str, str]:
    """prefix ('3314', '33', '33141100-4') -> the description of its root code."""
    bases = {p: _fit._cpv_base(p).ljust(8, "0") for p in prefixes}
    if not bases:
        return {}
    col = "coalesce(description_en, description)" if lang == "en" else "description"
    c.execute(f"""SELECT split_part(cpv_code, '-', 1) AS base, {col} AS label
                    FROM proc.cpv_code
                   WHERE split_part(cpv_code, '-', 1) = ANY(%s)""",
              (sorted(set(bases.values())),))
    by_base = {r["base"]: r["label"] for r in c.fetchall()}
    return {p: by_base.get(b) or "" for p, b in bases.items()}


def mapping_for(c, kads) -> dict[str, tuple[str | None, list[dict]]]:
    """ΚΑΔ -> (the level used, its kad_cpv_map rows), deepest level first.

    A level is used only if it learned at least one CPV GROUP (4 digits). A
    rare 8-digit ΚΑΔ can keep a single stray exact code and nothing else;
    stopping there would hide everything its parent learned. With no group
    at any level the ΚΑΔ gets (None, []) — no estimate, never a guess.
    """
    kads = [k for k in dict.fromkeys(kads) if k]
    wanted = sorted({k[:lvl] for k in kads for lvl in KAD_LEVELS})
    by_prefix: dict[str, list[dict]] = {}
    if wanted:
        c.execute("""SELECT kad_prefix, cpv_prefix, n_firms, kad_firms, support, lift
                       FROM proc.kad_cpv_map WHERE kad_prefix = ANY(%s)""", (wanted,))
        for r in c.fetchall():
            by_prefix.setdefault(r["kad_prefix"], []).append(r)
    out = {}
    for k in kads:
        out[k] = (None, [])
        for lvl in KAD_LEVELS:
            rows = by_prefix.get(k[:lvl]) or []
            if any(len(r["cpv_prefix"]) == 4 for r in rows):
                out[k] = (k[:lvl], rows)
                break
    return out


# --------------------------------------------------------------------------- #
# From the ΚΑΔ's own description (the default source)
# --------------------------------------------------------------------------- #
# Words that say HOW a firm trades or makes, not WHAT. Stemmed by the same
# Greek dictionary as the CPV text, so every inflection goes with them. Left
# in, the rare ones ("χονδρικού") are exactly the words a CPV description
# almost never uses — high weight, wrong codes.
DESC_STOP = ("χονδρικό χονδρικού λιανικό λιανικού εμπόριο εμπορίου πώλησης "
             "πωλήσεις υπηρεσίες εμπορικοί αντιπρόσωποι μεσολαβούν κατασκευή "
             "παραγωγή γενικά διάφορων ειδών άλλων λοιπών παρόμοιων κυρίως "
             "εξειδικευμένο μη ειδικευμένο καταστήματα προμήθειας")
DESC_MIN_SCORE = 0.30    # cosine below this was noise in the measurement
DESC_MAX_CODES = 25      # codes per ΚΑΔ that enter the profile

# CPV divisions a ΚΑΔ's NACE section may sell into. A wholesaler of surgical
# instruments does not sell "installation of medical equipment" services,
# though the words match; repair (50) and installation (51) were measured and
# cost more than they found. None = unrestricted (services match by text).
GOODS_DIVISIONS = frozenset(f"{d:02d}" for d in range(3, 45)) | {"48"}
WORKS_DIVISIONS = frozenset({"44", "45", "71"})


def allowed_divisions(kad: str | None) -> frozenset | None:
    d = int(kad[:2]) if kad and kad[:2].isdigit() else 0
    if 1 <= d <= 33 or 45 <= d <= 47:      # primary, manufacturing, trade
        return GOODS_DIVISIONS
    if 41 <= d <= 43:                       # construction
        return WORKS_DIVISIONS
    return None


_INDEX: dict = {}
_INDEX_LOCK = threading.Lock()


def _lexemes(c, text: str) -> list[str]:
    c.execute("SELECT tsvector_to_array(to_tsvector('greek', %s)) AS a", (text or "",))
    return [x for x in (c.fetchone()["a"] or []) if len(x) > 1]


def _cpv_index(c) -> dict:
    """CPV lexemes, inverted index and rarity weights, built once per process
    (and again only if the CPV list changes size — it is a fixed vocabulary)."""
    c.execute("SELECT count(*) AS n FROM proc.cpv_code WHERE description IS NOT NULL")
    n = int(c.fetchone()["n"])
    with _INDEX_LOCK:
        if _INDEX.get("n") == n:
            return _INDEX
        c.execute("""SELECT cpv_code, tsvector_to_array(description_tsv) AS lx
                       FROM proc.cpv_code WHERE description IS NOT NULL""")
        terms = {r["cpv_code"]: {x for x in (r["lx"] or []) if len(x) > 1}
                 for r in c.fetchall()}
        df: dict[str, int] = {}
        postings: dict[str, list[str]] = {}
        for code, lx in terms.items():
            for t in lx:
                df[t] = df.get(t, 0) + 1
                postings.setdefault(t, []).append(code)
        idf = {t: math.log(max(n, 1) / k) for t, k in df.items()}
        norm = {code: math.sqrt(sum(idf[t] ** 2 for t in lx)) or 1.0
                for code, lx in terms.items()}
        _INDEX.clear()
        _INDEX.update(n=n, terms=terms, idf=idf, norm=norm, postings=postings,
                      stop=set(_lexemes(c, DESC_STOP)))
        return _INDEX


def describe(c, kad: str | None, descr: str | None) -> dict[str, float]:
    """CPV code -> match score (0..1) for one ΚΑΔ description, best first,
    at most DESC_MAX_CODES, none below DESC_MIN_SCORE."""
    if not descr:
        return {}
    ix = _cpv_index(c)
    q = {t for t in _lexemes(c, descr) if t not in ix["stop"] and t in ix["idf"]}
    if not q:
        return {}
    idf = ix["idf"]
    qn = math.sqrt(sum(idf[t] ** 2 for t in q))
    ok = allowed_divisions(kad)
    shared: dict[str, float] = {}
    for t in q:
        for code in ix["postings"][t]:
            if ok is None or code[:2] in ok:
                shared[code] = shared.get(code, 0.0) + idf[t] ** 2
    scored = {code: v / (qn * ix["norm"][code]) for code, v in shared.items()}
    best = sorted((kv for kv in scored.items() if kv[1] >= DESC_MIN_SCORE),
                  key=lambda kv: (-kv[1], kv[0]))[:DESC_MAX_CODES]
    return dict(best)


@dataclass
class Estimate:
    afm: str | None = None
    gemi: dict | None = None
    kads: list | None = None        # company_kads(), each with `matched`
    region: str | None = None
    profile: object | None = None   # fit.Profile, or None
    areas: list | None = None       # the main CPV groups, explained
    reason: str | None = None       # why there is no profile (a Greek phrase)

    @property
    def usable(self) -> bool:
        return self.profile is not None

    @property
    def region_label(self) -> str | None:
        return REGION_LABELS.get(self.region or "")


def estimate(c, uid: int, *, lang: str = "el") -> Estimate:
    """The customer's ΚΑΔ, mapped, as a fit.Profile — or why there is none.

    Identity (ΑΦΜ, registry row, ΚΑΔ, region) is filled whenever it exists,
    so the page can show WHAT the firm is even when it cannot estimate.
    """
    est = Estimate(kads=[], areas=[])
    est.afm = customer_afm(c, uid)
    if not est.afm:
        est.reason = "δεν υπάρχει ΑΦΜ — συνδέστε πρώτα την εταιρεία από το ΓΕΜΗ"
        return est
    est.gemi = registry_row(c, est.afm)
    if not est.gemi:
        est.reason = "δεν υπάρχουν στοιχεία ΓΕΜΗ για αυτό το ΑΦΜ"
        return est
    est.kads = company_kads(est.gemi)
    c.execute("SELECT postal_code FROM proc.customer_profile WHERE user_id = %s", (uid,))
    prof = c.fetchone() or {}
    est.region = (nuts2_from_postal(est.gemi.get("zip_code"))
                  or nuts2_from_postal(prof.get("postal_code")))
    if not est.kads:
        est.reason = "η εταιρεία δεν έχει ενεργό ΚΑΔ στο ΓΕΜΗ"
        return est
    used = [k for k in est.kads if k["primary"]][:1]
    used += [k for k in est.kads if not k["primary"]][:MAX_SECONDARY]

    # The description only. The learned mapping (build) was measured on the
    # firms it was learned from and still did worse than guessing the most
    # common groups (kad_cpv_map.py evaluate, 2026-10-07): it is not a source.
    raw: dict[str, float] = {}
    why: dict[str, dict] = {}          # cpv prefix -> the match that set it
    for k in used:
        factor = 1.0 if k["primary"] else SECONDARY_FACTOR
        matched = describe(c, k["kad"], k.get("descr"))
        k["matched"] = bool(matched)
        # At every depth the scorer grades by: the code, its group, its
        # division — each at the score of its best-matching code.
        for code, score in matched.items():
            info = {"kad": k["kad"], "primary": k["primary"], "score": score,
                    "code": code}
            for prefix in (code, code[:4], code[:2]):
                if factor * score > raw.get(prefix, 0.0):
                    raw[prefix] = factor * score
                    why[prefix] = info
    if not raw:
        est.reason = "η περιγραφή του ΚΑΔ δεν ταιριάζει σε κανέναν κωδικό CPV"
        return est

    est.profile = _fit.Profile(user_id=uid, cpv=_fit.normalise_within_depth(raw),
                               nuts={est.region} if est.region else set())
    groups = sorted((p for p in raw if len(p) == 4),
                    key=lambda p: (-raw[p], p))[:AREAS_MAX]
    labels = cpv_labels(c, groups, lang)
    est.areas = [{"prefix": p, "label": labels.get(p), **why[p]} for p in groups]
    return est


# --------------------------------------------------------------------------- #
# Who they would be up against
# --------------------------------------------------------------------------- #
def _self_operator_ids(c, afm: str | None) -> list[int]:
    return _fit.operator_ids_for_afm(c, afm) if afm else []


def peers(c, est: Estimate, *, limit: int = PEERS_MAX) -> dict:
    """Contractors registered under the SAME primary ΚΑΔ that already win.

    The deepest ΚΑΔ level that has any is used (8, then 6, then 4 digits) and
    reported, so "same ΚΑΔ" never silently means "same NACE class". Ranked by
    awards in the last RECENT_YEARS; the region is shown, not filtered on.
    """
    # We know the ΚΑΔ only of contractors looked up in ΓΕΜΗ, so an empty list
    # usually means "not known", not "none": the page says out of how many.
    c.execute("SELECT count(*) AS n FROM proc.operator_kad")
    n_known = int(c.fetchone()["n"])
    primary = next((k for k in est.kads or [] if k["primary"]), None)
    if not primary or not n_known:
        return {"rows": [], "level": None, "n_known": n_known}
    ops_self = _self_operator_ids(c, est.afm)
    for lvl in KAD_LEVELS:
        prefix = primary["kad"][:lvl]
        c.execute("""
            WITH p AS (SELECT afm, nuts2 FROM proc.operator_kad
                        WHERE kad LIKE %(pfx)s AND afm <> %(self)s),
                 o AS (SELECT p.afm, p.nuts2, eo.operator_id, eo.name, eo.vat_number
                         FROM p JOIN proc.economic_operator eo
                           ON eo.vat_number = ANY(ARRAY[p.afm, 'EL' || p.afm,
                                                        ltrim(p.afm, '0')])
                        WHERE eo.operator_id <> ALL(%(ops)s)),
                 w AS (SELECT o.afm, count(DISTINCT a.adam) AS n_awards,
                              max(a.signed_date) AS last_award
                         FROM o JOIN proc.act_operator ao ON ao.operator_id = o.operator_id
                         JOIN proc.procurement_act a ON a.adam = ao.adam
                        WHERE a.type = ANY(%(types)s)
                          AND NOT coalesce(a.cancelled, false)
                          AND a.signed_date >= now() - make_interval(years => %(yrs)s)
                        GROUP BY o.afm)
            SELECT w.afm, min(o.name) AS name, min(o.vat_number) AS vat_number,
                   min(o.nuts2) AS nuts2, w.n_awards, w.last_award
              FROM w JOIN o USING (afm)
             GROUP BY w.afm, w.n_awards, w.last_award
             ORDER BY w.n_awards DESC, w.afm
             LIMIT %(limit)s
        """, {"pfx": prefix + "%", "self": est.afm or "", "ops": ops_self,
              "types": list(_AWARD_TYPES), "yrs": RECENT_YEARS, "limit": limit})
        rows = c.fetchall()
        if rows:
            for r in rows:
                r["region_label"] = REGION_LABELS.get(r["nuts2"] or "")
                r["same_region"] = bool(est.region) and r["nuts2"] == est.region
            return {"rows": rows, "level": prefix, "n_known": n_known}
    return {"rows": [], "level": None, "n_known": n_known}


def leader_codes(c, est: Estimate) -> list[str]:
    """Every CPV code in the estimate's strongest groups (LEADER_GROUPS of
    them, each at least LEADER_MIN_WEIGHT). Whole groups, not the matched
    codes: a description matches a group's general code ('33140000-3
    Ιατρικά αναλώσιμα') while awards carry the specific ones ('33141100-4
    Γάντια'). Exact codes because object_detail_cpv is indexed on the code."""
    if not est.usable:
        return []
    cpv = est.profile.cpv
    groups = sorted((p for p, w in cpv.items()
                     if len(p) == 4 and w >= LEADER_MIN_WEIGHT),
                    key=lambda p: (-cpv[p], p))[:LEADER_GROUPS]
    if not groups:
        return []
    c.execute("""SELECT cpv_code FROM proc.cpv_code
                  WHERE substr(cpv_code, 1, 4) = ANY(%s) ORDER BY cpv_code""", (groups,))
    return [r["cpv_code"] for r in c.fetchall()]


def leaders(c, est: Estimate, *, limit: int = LEADERS_MAX) -> dict:
    """Who wins most in the codes this firm would bid for, recently.

    Not "competitors" in fit.competitors' sense — that needs shared BUYERS,
    which a firm with no history has none of. This is the market the firm is
    walking into. Ranked by awards; awards in the firm's own region counted
    beside it (region scores, never filters).
    """
    codes = leader_codes(c, est)
    if not codes:
        return {"rows": [], "n_codes": 0}
    c.execute("""
        WITH hits AS (
            SELECT DISTINCT od.adam
              FROM proc.object_detail_cpv oc
              JOIN proc.act_object_detail od ON od.id = oc.object_detail_id
             WHERE oc.cpv_code = ANY(%(codes)s)),
        pool AS (
            SELECT a.adam, a.nuts_code, ao.operator_id
              FROM hits h
              JOIN proc.procurement_act a ON a.adam = h.adam
              JOIN proc.act_operator ao ON ao.adam = a.adam
             WHERE a.type = ANY(%(types)s)
               AND NOT coalesce(a.cancelled, false)
               AND a.signed_date >= now() - make_interval(years => %(yrs)s)
               AND ao.operator_id <> ALL(%(ops)s))
        SELECT p.operator_id, eo.name, eo.vat_number,
               count(DISTINCT p.adam) AS n_awards,
               count(DISTINCT p.adam) FILTER (
                   WHERE substr(p.nuts_code, 1, 4) = %(region)s) AS n_region
          FROM pool p
          JOIN proc.economic_operator eo ON eo.operator_id = p.operator_id
         GROUP BY p.operator_id, eo.name, eo.vat_number
         ORDER BY n_awards DESC, p.operator_id
         LIMIT %(limit)s
    """, {"codes": codes, "types": list(_AWARD_TYPES), "yrs": RECENT_YEARS,
          "ops": _self_operator_ids(c, est.afm), "region": est.region or "",
          "limit": limit})
    return {"rows": c.fetchall(), "n_codes": len(codes)}
