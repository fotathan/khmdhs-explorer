"""
onboarding.py — the first-login wizard ("Ρύθμιση ραντάρ") that turns a few
answers into ordinary saved searches. Spec: docs/specs/onboarding-wizard.md.

What it is for
--------------
A self-registered customer used to land on an empty search box with seven
days of trial, and had to discover the CPV filter, the region filter and the
keyword syntax before the product showed them anything of their own. This
asks five short questions and writes the answers down as saved searches —
the same proc.search_profile rows /account/searches creates, through the same
helper, with keys search_profiles already understands. Nothing here is a new
kind of search.

Where the suggestions come from
-------------------------------
The award ledger, by ΑΦΜ: what the firm has actually WON (fit.ledger_summary).
A website says what a firm claims; the ledger says what it delivered. When the
ledger has nothing, ΓΕΜΗ can still confirm the company's name, but it gives
activity codes (ΚΑΔ), not CPVs, and there is no crosswalk — so the customer
picks categories by hand.

The ΑΦΜ is a claim
------------------
Whatever the customer types is stored ONLY in proc.onboarding.declared_afm. It
never reaches customer_profile.vat_number / tax_number / operator_id, so
fit.operator_ids_for never sees it and seed_from_ledger never runs from it.
Those columns decide the fit score, the ledger link and eventually an invoice,
and are written only when an admin links the company on the CRM card
(company_match.apply_match). The ledger lookup is read-only for the same
reason. Both halves are test-enforced (tests/test_onboarding.py).

Why several searches, not one
-----------------------------
build_where ORs CPV codes together but ANDs the CPV group with the keyword box.
One combined search would only match acts that have a chosen CPV AND a keyword
— the narrowest reading, and exactly the one that misses the tenders filed
under the wrong CPV, which keywords are there to catch. So: one search by
subject, one by keyword, and (optionally) one for awards in the same field.

Keywords go into `q`, not `fulltext`: `q` already searches the title AND the
document text (search_tsv), while `fulltext` reads only the document body and
would miss a keyword that appears only in the title.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import unicodedata
from collections import Counter
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Json

try:
    from app import auth as _auth
    from app import fit as _fit
    from app import gemi_client as _gemi
    from app import i18n as _i18n
except ImportError:                      # pragma: no cover — run with --app-dir=app
    import auth as _auth
    import fit as _fit
    import gemi_client as _gemi
    import i18n as _i18n


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #
# Same vocabulary as login_links.enabled, and for the same reason: a switch
# that only recognises "0" leaves the feature ON for an operator who typed
# "false" into a hosting dashboard.
_OFF_VALUES = frozenset({"0", "false", "no", "off", "n", "f", "disabled"})


def enabled() -> bool:
    """ONBOARDING_ENABLED=0 (or false/no/off/disabled, any case) removes the
    wizard, the registration question, the post-signup redirect and the
    banner. On by default; anything unrecognised is treated as on."""
    raw = (os.environ.get("ONBOARDING_ENABLED") or "").strip().lower()
    return raw not in _OFF_VALUES if raw else True


# --------------------------------------------------------------------------- #
# Tunables — argued, not fitted (there is no data yet on what a good first
# search looks like). Each is used in exactly one rule below.
# --------------------------------------------------------------------------- #
# Ledger CPV chips: 4-digit classes, busiest first, until they cover this share
# of the firm's awards — and never more than MAX_CPV_CHIPS. 2 digits is a whole
# industry (33 = all of healthcare); 8 is one product.
CPV_COVER = 0.80
MAX_CPV_CHIPS = 10
# Ledger regions: those holding at least this share of the firm's awards. A
# firm that works in more than MAX_REGIONS regions is a national supplier, and
# a region filter would only hide tenders from it.
REGION_SHARE = 0.10
MAX_REGIONS = 5
# Keywords. Bable asks for 5 minimum; ours are an addition to the CPV search,
# not the search itself, so none are required.
MAX_KEYWORDS = 15
MIN_KEYWORD_LEN = 3
MAX_KEYWORD_LEN = 60
KEYWORD_SUGGESTIONS = 12
# A suggested term must appear in at least this many of the firm's award
# titles; below it the "suggestion" is one contract's wording.
MIN_SUGGESTION_TITLES = 3
# ΑΦΜ lookups per customer before the login throttle locks the key for a few
# minutes. ΓΕΜΗ has a quota, and the lookup is also the one place a stranger
# could enumerate firms (the award data is public, the pace should not be).
# The lock itself is auth.throttle_fail's (_MAX_FAILS, _LOCK_SECONDS).
THROTTLE_PREFIX = "onboarding-afm:"
# Overview counts look back this far over submission_date.
PREVIEW_DAYS = 30
PREVIEW_TOO_MANY = 1000

STEPS = (0, 1, 2, 3, 4, 5)
LAST_STEP = 5

# Default names of the searches the wizard creates. Editable on the overview.
NAME_NOTICES = "Νέοι διαγωνισμοί στο αντικείμενό μου"
NAME_KEYWORDS = "Διαγωνισμοί με τις λέξεις-κλειδιά μου"
NAME_AWARDS = "Αναθέσεις στον κλάδο μου"


# --------------------------------------------------------------------------- #
# ΑΦΜ
# --------------------------------------------------------------------------- #
def afm_valid(raw: str | None) -> str | None:
    """The 9-digit ΑΦΜ if it is well formed AND its check digit holds, else
    None. gemi_client.normalize_afm handles the spelling (EL prefix, spaces,
    a dropped leading zero); the check digit catches a typo before it costs a
    registry call. 000000000 passes the arithmetic and is still nobody."""
    afm = _gemi.normalize_afm(raw)
    if not afm or afm == "000000000":
        return None
    total = sum(int(d) * (2 ** (8 - i)) for i, d in enumerate(afm[:8]))
    return afm if (total % 11) % 10 == int(afm[8]) else None


# --------------------------------------------------------------------------- #
# Keywords
# --------------------------------------------------------------------------- #
def fold(s: str | None) -> str:
    """Lowercase, accents off, final sigma folded — the comparison form."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("ς", "σ").strip()


# Words that match nearly every procurement act, or none of meaning. Refused
# as keywords ("too generic") and never suggested. Stored folded.
_STOP = {
    "και", "του", "τησ", "των", "τον", "την", "το", "τα", "οι", "η", "ο", "σε",
    "στο", "στη", "στην", "στον", "στα", "στισ", "στουσ", "απο", "με", "για",
    "κατα", "προσ", "δια", "επι", "μετα", "ωσ", "εωσ", "ή", "η", "ανα", "υπο",
    "τουσ", "τισ", "ενοσ", "μιασ", "ενα", "μια", "αυτο", "οπωσ", "λοιπα", "λοιπων",
    "the", "and", "for", "of", "to", "in", "on", "with",
}
_BOILERPLATE = {
    "προμηθεια", "προμηθειασ", "προμηθειεσ", "προμηθειων",
    "παροχη", "παροχησ", "παροχεσ", "υπηρεσια", "υπηρεσιασ", "υπηρεσιεσ",
    "υπηρεσιων", "αναθεση", "αναθεσησ", "αναθεσεισ", "εργο", "εργου", "εργα",
    "εργων", "εργασια", "εργασιεσ", "εργασιων", "συμβαση", "συμβασησ",
    "συμβασεισ", "διαγωνισμοσ", "διαγωνισμου", "διαγωνισμων", "προκηρυξη",
    "διακηρυξη", "διακηρυξησ", "αποφαση", "αποφασησ", "πραξη", "δημοσ", "δημου",
    "δημων", "περιφερεια", "περιφερειασ", "νοσοκομειο", "νοσοκομειου",
    "ετοσ", "ετουσ", "ετη", "ετων", "ειδη", "ειδων", "υλικα", "υλικων", "υλικο",
    "διαφορα", "διαφορων", "αγορα", "αγορασ", "απευθειασ", "συνοπτικου",
    "ανοικτου", "ηλεκτρονικου", "δαπανη", "δαπανησ", "εγκριση", "εγκρισησ",
    "κατακυρωση", "κατακυρωσησ", "ανοικτοσ", "συνοπτικοσ", "πλαισιο",
    "συμφωνια", "συμφωνιασ", "τμημα", "τμηματα", "ομαδα", "ομαδεσ", "cpv",
    "πρακτικο", "πρακτικου", "πρακτικα", "πρακτικων", "αιτημα", "αιτηματοσ",
    "αιτηματα", "χρηση", "χρησησ", "χρηστη", "αναγκεσ", "αναγκων", "καλυψη",
    "καλυψησ", "αριθμ", "αριθ", "αρ",
    "supply", "services", "service", "works", "contract",
}
_GENERIC = _STOP | _BOILERPLATE

# Characters that mean something to websearch_to_tsquery or to the q box's
# own parser: quotes delimit phrases, a leading '-' excludes, a trailing '*'
# switches the whole box to prefix mode. A keyword is a phrase, nothing more.
_SYNTAX = re.compile(r'["*]')


def clean_keyword(raw: str | None) -> str:
    k = _SYNTAX.sub(" ", raw or "")
    k = re.sub(r"\s+", " ", k).strip().lstrip("-").strip()
    return k[:MAX_KEYWORD_LEN]


def keyword_problem(k: str) -> str | None:
    """Why this keyword is refused, or None. The reason is a translation key."""
    words = [w for w in re.split(r"[\s,.;:/()\-]+", fold(k)) if w]
    if len(k) < MIN_KEYWORD_LEN or not words:
        return "πολύ σύντομη"
    if all(w in _GENERIC or w == "or" for w in words):
        return "πολύ γενική — θα ταίριαζε σχεδόν σε όλα"
    return None


def parse_keywords(text: str | None, extra: list[str] | None = None
                   ) -> tuple[list[str], list[tuple[str, str]]]:
    """(kept, refused) from the textarea (one per line; commas also split)
    plus any ticked suggestions. Deduplicated on the folded form, order kept.
    Refused = [(keyword, reason)] so the page can say which and why."""
    raw = re.split(r"[\n,;]+", text or "") + list(extra or [])
    kept, refused, seen = [], [], set()
    for item in raw:
        k = clean_keyword(item)
        if not k:
            continue
        key = fold(k)
        if key in seen:
            continue
        seen.add(key)
        why = keyword_problem(k)
        if why:
            refused.append((k, why))
        else:
            kept.append(k)
    return kept, refused


def keywords_query(keywords: list[str]) -> str:
    """The q-box string: every keyword a quoted phrase, joined with OR. The
    customer never sees the syntax."""
    return " or ".join(f'"{clean_keyword(k)}"' for k in keywords if clean_keyword(k))


def suggest_keywords(titles: list[str], *, limit: int = KEYWORD_SUGGESTIONS,
                     min_titles: int = MIN_SUGGESTION_TITLES) -> list[str]:
    """Frequent 1–2 word terms in the firm's award titles, generic words out.

    Counted once per title (a title that says "γάντια" four times is one
    vote), compared folded, shown in the spelling the titles used — preferring
    a lowercase, accented one when any title had it. Most titles are in
    capitals, and lowercasing those loses the accents: "αγγειοπλαστικης"
    reads like a typo, "ΑΓΓΕΙΟΠΛΑΣΤΙΚΗΣ" reads like the notice. Two-word terms
    need both words meaningful; a pair that is only ever seen together
    replaces its own single words so the list does not say the same thing
    three times."""
    votes: Counter = Counter()
    shown: dict[str, Counter] = {}
    for title in titles or []:
        words = [w for w in re.findall(r"[0-9A-Za-zͰ-Ͽἀ-῿]+",
                                       title or "")]
        seen_here = set()
        for i, w in enumerate(words):
            grams = [(w,)]
            if i + 1 < len(words):
                grams.append((w, words[i + 1]))
            for g in grams:
                folded = tuple(fold(x) for x in g)
                if any(len(x) < 4 or x in _GENERIC or x.isdigit() for x in folded):
                    continue
                key = " ".join(folded)
                if key in seen_here:
                    continue
                seen_here.add(key)
                votes[key] += 1
                shown.setdefault(key, Counter())[" ".join(g)] += 1
    pairs = {k for k in votes if " " in k and votes[k] >= min_titles}
    out = []
    for key, n in votes.most_common():
        if n < min_titles:
            break
        if " " not in key and any(key in p.split(" ") and votes[p] >= n for p in pairs):
            continue            # always appears inside a better pair
        forms = shown[key]
        cased = [f for f in forms if f != f.upper()]
        out.append(max(cased, key=forms.get) if cased else forms.most_common(1)[0][0])
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Ledger → suggestions (pure, so the rules are testable without a DB)
# --------------------------------------------------------------------------- #
def pick_cpv(cpv4: list[dict], n_awards: int) -> list[str]:
    """4-digit classes, busiest first, until CPV_COVER of the awards is
    covered, capped at MAX_CPV_CHIPS. An act with several CPVs counts under
    each, so coverage is measured against the award count, not the sum."""
    out, covered = [], 0
    total = max(int(n_awards or 0), 1)
    for row in cpv4 or []:
        if len(out) >= MAX_CPV_CHIPS or covered >= CPV_COVER * total:
            break
        if not (row.get("prefix") or "").isdigit():
            continue
        out.append(row["prefix"])
        covered += int(row.get("n_acts") or 0)
    return out


def pick_regions(nuts: list[dict], valid_codes: set[str]) -> list[str]:
    """Regions with ≥ REGION_SHARE of the firm's located awards; [] (= all of
    Greece, no filter) when the firm works in more than MAX_REGIONS regions or
    nothing clears the bar. Only codes the region filter offers are kept."""
    rows = [r for r in (nuts or []) if r.get("prefix") in valid_codes]
    total = sum(int(r.get("n_acts") or 0) for r in rows)
    if not total:
        return []
    if len(rows) > MAX_REGIONS:
        return []
    return [r["prefix"] for r in rows
            if int(r.get("n_acts") or 0) / total >= REGION_SHARE]


# --------------------------------------------------------------------------- #
# Answers → saved searches (pure)
# --------------------------------------------------------------------------- #
def _num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def build_profiles(answers: dict) -> list[dict]:
    """[{key, name, params}] for what these answers describe.

    Every params dict uses only keys in search_profiles._SINGLE / _MULTI, with
    strings where the query string carries strings — so a created search is
    exactly what saving that URL by hand would have stored, and it opens,
    edits and alerts like one."""
    a = answers or {}
    names = a.get("names") or {}
    cpv = [c for c in (a.get("cpv") or []) if c]
    cat = [c for c in (a.get("cat") or []) if c]
    nuts = [n for n in (a.get("nuts") or []) if n]
    vmin, vmax = _num(a.get("value_min")), _num(a.get("value_max"))
    keywords = [k for k in (a.get("keywords") or []) if clean_keyword(k)]

    def subject(p):
        if cpv:
            p["cpv"] = list(cpv)
        if cat:
            p["cat"] = list(cat)
        if nuts:
            p["nuts"] = list(nuts)
        return p

    def value(p):
        if vmin is not None:
            p["value_min"] = str(vmin)
        if vmax is not None:
            p["value_max"] = str(vmax)
        return p

    out = []
    if cpv or cat:
        out.append({"key": "notices",
                    "name": (names.get("notices") or NAME_NOTICES).strip(),
                    "params": value(subject({"type": ["notice"], "status": "active"}))})
    if keywords:
        p = {"type": ["notice"], "status": "active", "q": keywords_query(keywords)}
        if nuts:
            p["nuts"] = list(nuts)
        out.append({"key": "keywords",
                    "name": (names.get("keywords") or NAME_KEYWORDS).strip(),
                    "params": value(p)})
    if (cpv or cat) and a.get("awards", True):
        out.append({"key": "awards",
                    "name": (names.get("awards") or NAME_AWARDS).strip(),
                    "params": subject({"type": ["contract"]})})
    for p in out:
        p["name"] = p["name"][:120]
    return out


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def get_state(c, uid: int) -> dict | None:
    c.execute("SELECT * FROM proc.onboarding WHERE user_id = %s", (uid,))
    row = c.fetchone()
    return dict(row) if row else None


def ensure_state(c, uid: int) -> dict:
    c.execute("""INSERT INTO proc.onboarding (user_id) VALUES (%s)
                 ON CONFLICT (user_id) DO NOTHING""", (uid,))
    return get_state(c, uid)


def save_state(c, uid: int, *, step: int | None = None, answers: dict | None = None,
               **cols):
    """Update the row. `cols` may carry declared_afm / ledger_found /
    completed_at / skipped_at / created_profile_ids — nothing else."""
    allowed = {"declared_afm", "ledger_found", "completed_at", "skipped_at",
               "created_profile_ids"}
    sets, args = [], []
    if step is not None:
        sets.append("step = %s"); args.append(int(step))
    if answers is not None:
        sets.append("answers = %s"); args.append(Json(answers))
    for k, v in cols.items():
        if k not in allowed:
            raise ValueError(k)
        sets.append(f"{k} = %s"); args.append(v)
    if not sets:
        return
    c.execute(f"UPDATE proc.onboarding SET {', '.join(sets)} WHERE user_id = %s",
              args + [uid])


def start_at_registration(c, uid: int, *, experience: bool | None,
                          afm: str | None):
    """What /register records: the yes/no on the profile (and ONLY that
    column), the typed ΑΦΜ as a claim on the wizard row. No lookup here — a
    slow registry must never stand between a person and their account."""
    c.execute("""
        INSERT INTO proc.customer_profile (user_id, tender_experience, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
           SET tender_experience = EXCLUDED.tender_experience, updated_at = now()
    """, (uid, experience))
    c.execute("""INSERT INTO proc.onboarding (user_id, declared_afm)
                 VALUES (%s, %s)
                 ON CONFLICT (user_id) DO UPDATE SET declared_afm = EXCLUDED.declared_afm
              """, (uid, afm if experience else None))


def get_experience(c, uid: int) -> bool | None:
    c.execute("SELECT tender_experience FROM proc.customer_profile WHERE user_id = %s",
              (uid,))
    row = c.fetchone()
    return row["tender_experience"] if row else None


def set_experience(c, uid: int, value: bool | None):
    c.execute("""
        INSERT INTO proc.customer_profile (user_id, tender_experience, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
           SET tender_experience = EXCLUDED.tender_experience, updated_at = now()
    """, (uid, value))


def wants_prompt(c, user: dict | None) -> bool:
    """Show the "set up your searches" banner on /? Customers only; not once
    they finished or skipped; not for someone who already keeps saved searches
    of their own (an older account that found its way without us)."""
    if not enabled() or not user or user.get("role") != "customer":
        return False
    st = get_state(c, user["id"])
    if st and (st.get("completed_at") or st.get("skipped_at")):
        return False
    c.execute("""SELECT 1 FROM proc.search_profile
                  WHERE scope = 'customer' AND owner_user_id = %s LIMIT 1""",
              (user["id"],))
    return c.fetchone() is None


def funnel(c) -> dict:
    """Started / completed / skipped — one line on /admin/crm."""
    c.execute("""SELECT count(*) AS started,
                        count(completed_at) AS completed,
                        count(*) FILTER (WHERE skipped_at IS NOT NULL
                                           AND completed_at IS NULL) AS skipped
                   FROM proc.onboarding""")
    return dict(c.fetchone() or {})


def declared_afm(c, uid: int) -> str | None:
    c.execute("SELECT declared_afm FROM proc.onboarding WHERE user_id = %s", (uid,))
    row = c.fetchone()
    return row["declared_afm"] if row else None


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #
def lookup(c, afm: str, *, valid_regions: set[str]) -> dict:
    """What we can say about this ΑΦΜ, and the suggestions it yields.

    status: 'ledger' (awards found) | 'gemi' (registry only) | 'none'.
    Reads the ledger; the only write is ΓΕΜΗ's own cache (gemi_enrichment),
    which holds registry facts, never anything the customer said."""
    op_ids = _fit.operator_ids_for_afm(c, afm)
    summary = _fit.ledger_summary(c, op_ids) if op_ids else {"n_awards": 0}
    if summary.get("n_awards"):
        cpv = pick_cpv(summary["cpv4"], summary["n_awards"])
        cpv_rows = {r["prefix"]: r["n_acts"] for r in summary["cpv4"]}
        return {
            "status": "ledger",
            "name": summary.get("name"),
            "n_awards": summary["n_awards"],
            "n_buyers": summary.get("n_buyers"),
            "p10": _num(summary.get("p10")), "p90": _num(summary.get("p90")),
            "cpv": cpv,
            "cpv_counts": {p: cpv_rows.get(p, 0) for p in cpv},
            "nuts": pick_regions(summary["nuts"], valid_regions),
            "nuts_top": [r["prefix"] for r in summary["nuts"]
                         if r["prefix"] in valid_regions][:3],
            "keywords": suggest_keywords(summary.get("titles") or []),
        }
    gemi = _gemi_row(c, afm)
    if gemi and (gemi.get("legal_name") or gemi.get("trade_title")):
        return {"status": "gemi",
                "name": gemi.get("legal_name") or gemi.get("trade_title"),
                "city": gemi.get("city") or gemi.get("municipality")}
    return {"status": "none"}


def _gemi_row(c, afm: str) -> dict | None:
    """The registry record: our cached copy first, then one live call if a key
    is configured. A registry failure is 'not found', never an error page."""
    c.execute("""SELECT legal_name, trade_title, city, municipality, fetch_status
                   FROM proc.gemi_enrichment WHERE afm = %s""", (afm,))
    row = c.fetchone()
    if row and row.get("fetch_status") == "ok":
        return dict(row)
    if row or not os.environ.get("GEMI_API_KEY"):
        return None
    try:
        _gemi.enrich_one(c, afm)
    except Exception:                    # noqa: BLE001 — a suggestion, not a page
        return None
    c.execute("""SELECT legal_name, trade_title, city, municipality, fetch_status
                   FROM proc.gemi_enrichment WHERE afm = %s""", (afm,))
    row = c.fetchone()
    return dict(row) if row and row.get("fetch_status") == "ok" else None


def throttled_lookup(c, uid: int, afm: str, *, valid_regions: set[str]) -> dict:
    """lookup(), counted against the customer's quota. Every lookup counts —
    a ledger hit costs no registry call, but it is still a firm looked up."""
    key = THROTTLE_PREFIX + str(uid)
    if _auth.throttle_blocked(c, key):
        return {"status": "throttled"}
    _auth.throttle_fail(c, key)
    return lookup(c, afm, valid_regions=valid_regions)


# --------------------------------------------------------------------------- #
# Validation against the reference tables
# --------------------------------------------------------------------------- #
def valid_cpv_prefixes(c, prefixes: list[str]) -> list[str]:
    """Keep the prefixes (2–8 digits, check digit dropped) that name at least
    one real CPV code. Order kept, repeats dropped."""
    out = []
    for p in prefixes or []:
        p = (p or "").strip().split("-", 1)[0]
        if not re.fullmatch(r"\d{2,8}", p) or p in out:
            continue
        c.execute("SELECT 1 FROM proc.cpv_code WHERE cpv_code LIKE %s LIMIT 1",
                  (p + "%",))
        if c.fetchone():
            out.append(p)
    return out


def valid_categories(c, values: list[str]) -> list[str]:
    ids = sorted({int(v[2:]) for v in values or []
                  if isinstance(v, str) and v.startswith("c:") and v[2:].isdigit()})
    if not ids:
        return []
    c.execute("SELECT id FROM proc.tender_category WHERE id = ANY(%s)", (ids,))
    ok = {r["id"] for r in c.fetchall()}
    return [f"c:{i}" for i in ids if i in ok]


def cpv_labels(c, prefixes: list[str], lang: str) -> dict:
    """prefix -> the description of its head code ('3314' -> 33140000-3)."""
    out = {}
    col = "coalesce(description_en, description)" if lang == "en" else "description"
    for p in prefixes or []:
        head = p.ljust(8, "0")
        c.execute(f"""SELECT {col} AS d FROM proc.cpv_code
                        WHERE cpv_code LIKE %s ORDER BY cpv_code LIMIT 1""",
                  (head + "%",))
        row = c.fetchone()
        out[p] = row["d"] if row else None
    return out


def category_options(c, lang: str) -> list[dict]:
    col = "coalesce(name_en, name)" if lang == "en" else "name"
    c.execute(f"SELECT id, {col} AS name FROM proc.tender_category ORDER BY 2")
    return [{"value": f"c:{r['id']}", "label": r["name"]} for r in c.fetchall()]


# --------------------------------------------------------------------------- #
# Creating the searches
# --------------------------------------------------------------------------- #
def create_profiles(c, uid: int, answers: dict, *, cap: int) -> dict:
    """Write the saved searches. Returns {'created': [(id, name)],
    'not_created': [name]} — what fits under the cap is created, the rest is
    reported, never silently dropped. Never edits or deletes a search from an
    earlier run."""
    c.execute("""SELECT count(*) AS n FROM proc.search_profile
                  WHERE scope = 'customer' AND owner_user_id = %s""", (uid,))
    room = max(0, cap - int(c.fetchone()["n"]))
    created, not_created = [], []
    for p in build_profiles(answers):
        if room <= 0:
            not_created.append(p["name"])
            continue
        pid = _auth.create_search_profile(
            c, name=p["name"], scope="customer", owner_id=uid,
            params=p["params"], based_on_id=None, created_by=uid)
        created.append((pid, p["name"]))
        room -= 1
    return {"created": created, "not_created": not_created}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def make_router(templates: Jinja2Templates, cursor, *, nuts_regions: list[dict],
                count_fn=None, max_saved=None) -> APIRouter:
    """count_fn(params) -> int | None is main's search count (build_where), so
    the overview's numbers are exactly what the saved search will show.
    max_saved() -> int reads account_searches.MAX_SAVED_SEARCHES at call time."""
    def _switched_on():
        # Off = the routes do not exist, as far as anyone can tell.
        if not enabled():
            raise HTTPException(404)

    router = APIRouter(prefix="/welcome", tags=["onboarding"],
                       dependencies=[Depends(_switched_on)])
    region_codes = {r["code"] for r in nuts_regions}
    region_label = {r["code"]: r["label"] for r in nuts_regions}

    def _user(request):
        return getattr(request.state, "user", None)

    def _signed_in_post(request):
        u = _user(request)
        if not u:
            raise HTTPException(403, "sign in first")
        return u

    def _go(step: int, **q):
        url = "/welcome/step/%d" % step
        q = {k: v for k, v in q.items() if v}
        return RedirectResponse(url + ("?" + urlencode(q) if q else ""),
                                status_code=303)

    def _next_after_welcome(experience):
        # "Όχι" skips the ΑΦΜ screen entirely: there is nothing to look up.
        return 2 if experience is False else 1

    def _render(request, step, st, *, error=None, refused=None, status_code=200,
                extra=None):
        lang = _i18n.lang_from_request(request)
        answers = st.get("answers") or {}
        with cursor() as c:
            experience = get_experience(c, st["user_id"])
            ctx = {
                "step": step, "last_step": LAST_STEP, "answers": answers,
                "declared_afm": st.get("declared_afm"),
                "experience": experience,
                "error": error, "refused": refused or [],
                "nuts_regions": nuts_regions, "region_label": region_label,
                "max_keywords": MAX_KEYWORDS,
                "restart": bool(st.get("completed_at") or st.get("skipped_at")),
            }
            if step == 1:
                ctx["found"] = answers.get("lookup")
            if step == 2:
                chips = list(dict.fromkeys(
                    (answers.get("cpv") or [])
                    + ((answers.get("lookup") or {}).get("cpv") or [])))
                ctx["cpv_chips"] = chips
                ctx["cpv_label"] = cpv_labels(c, chips, lang)
                ctx["cpv_counts"] = (answers.get("lookup") or {}).get("cpv_counts") or {}
                ctx["categories"] = category_options(c, lang)
            if step == 3:
                ctx["suggestions"] = [
                    s for s in ((answers.get("lookup") or {}).get("keywords") or [])
                    if fold(s) not in {fold(k) for k in answers.get("keywords") or []}]
            if step == 5:
                profiles = build_profiles(answers)
                for p in profiles:
                    p["count"] = _preview_count(p["params"])
                ctx["profiles"] = profiles
                ctx["cpv_label"] = cpv_labels(c, answers.get("cpv") or [], lang)
                cats = {o["value"]: o["label"] for o in category_options(c, lang)}
                ctx["cat_label"] = cats
                ctx["preview_days"] = PREVIEW_DAYS
                ctx["too_many"] = PREVIEW_TOO_MANY
                c.execute("""SELECT name FROM proc.search_profile
                              WHERE scope = 'customer' AND owner_user_id = %s
                              ORDER BY created_at""", (st["user_id"],))
                ctx["existing"] = [r["name"] for r in c.fetchall()]
            if extra:
                ctx.update(extra)
        return templates.TemplateResponse(request, "onboarding.html", ctx,
                                          status_code=status_code)

    def _preview_count(params):
        if not count_fn:
            return None
        since = (dt.date.today() - dt.timedelta(days=PREVIEW_DAYS)).isoformat()
        try:
            return count_fn({**params, "date_from": since})
        except Exception:                # noqa: BLE001 — a hint, not the page
            return None

    def _state(uid):
        with cursor() as c:
            return ensure_state(c, uid)

    # ---- entry ------------------------------------------------------------ #
    @router.get("")
    def welcome(request: Request):
        u = _user(request)
        if not u:
            return RedirectResponse("/login?next=/welcome", status_code=303)
        st = _state(u["id"])
        step = 0 if (st.get("completed_at") or st.get("skipped_at")) else int(st["step"] or 0)
        return _go(min(max(step, 0), LAST_STEP))

    @router.get("/step/{n}", response_class=HTMLResponse)
    def step_get(n: int, request: Request):
        u = _user(request)
        if not u:
            return RedirectResponse("/login?next=/welcome", status_code=303)
        if n not in STEPS:
            raise HTTPException(404)
        st = _state(u["id"])
        answers = dict(st.get("answers") or {})
        if n == 1:
            with cursor() as c:
                experience = get_experience(c, u["id"])
                if experience is False:
                    return _go(2)
                # "Ναι" + an ΑΦΜ given at registration: the result is on screen
                # when the page opens, so the customer only confirms.
                if (experience and st.get("declared_afm")
                        and "lookup" not in answers):
                    found = throttled_lookup(c, u["id"], st["declared_afm"],
                                             valid_regions=region_codes)
                    if found["status"] != "throttled":
                        answers["lookup"] = found
                        save_state(c, u["id"], answers=answers,
                                   ledger_found=found["status"] == "ledger")
                        st["answers"] = answers
        return _render(request, n, st)

    # ---- step 0: welcome -------------------------------------------------- #
    @router.post("/step/0")
    async def step0_post(request: Request):
        u = _signed_in_post(request)
        with cursor() as c:
            st = ensure_state(c, u["id"])
            # (Re)starting keeps the ΑΦΜ claim and its lookup — asking the
            # registry again for the same number buys nothing.
            keep = {k: v for k, v in (st.get("answers") or {}).items() if k == "lookup"}
            experience = get_experience(c, u["id"])
            nxt = _next_after_welcome(experience)
            save_state(c, u["id"], step=nxt, answers=keep)
        return _go(nxt)

    # ---- step 1: the business -------------------------------------------- #
    @router.post("/step/1")
    async def step1_post(request: Request):
        u = _signed_in_post(request)
        form = await request.form()
        action = (form.get("action") or "").strip()
        with cursor() as c:
            st = ensure_state(c, u["id"])
            answers = dict(st.get("answers") or {})
            if action == "back":
                return _go(0)
            if action == "experience":
                val = {"yes": True, "no": False}.get(form.get("tender_experience"))
                if val is None:
                    return _render(request, 1, st, status_code=400,
                                   error="Επιλέξτε Ναι ή Όχι.")
                set_experience(c, u["id"], val)
                if val is False:
                    save_state(c, u["id"], step=2)
                    return _go(2)
                return _go(1)
            if action == "lookup":
                afm = afm_valid(form.get("afm"))
                if not afm:
                    return _render(request, 1, st, status_code=400,
                                   error="Μη έγκυρο ΑΦΜ — ελέγξτε τα 9 ψηφία.",
                                   extra={"afm_typed": (form.get("afm") or "")[:20]})
                found = throttled_lookup(c, u["id"], afm, valid_regions=region_codes)
                if found["status"] == "throttled":
                    return _render(request, 1, st, status_code=429,
                                   error="Πολλές αναζητήσεις ΑΦΜ — δοκιμάστε ξανά σε λίγα λεπτά.")
                answers["lookup"] = found
                save_state(c, u["id"], answers=answers, declared_afm=afm,
                           ledger_found=found["status"] == "ledger")
                return _go(1)
            if action == "confirm":
                found = answers.get("lookup") or {}
                if found.get("status") == "ledger":
                    # Pre-fill once. A customer who comes back to this screen
                    # after editing their chips must not have them reset.
                    # The value band is NOT pre-filled: p10–p90 as a filter
                    # would hide a fifth of the sizes the firm actually wins,
                    # plus every tender with no published budget. Step 4
                    # shows it as a hint instead.
                    if not answers.get("prefilled"):
                        answers["cpv"] = list(found.get("cpv") or [])
                        answers["nuts"] = list(found.get("nuts") or [])
                        answers["prefilled"] = True
                save_state(c, u["id"], step=2, answers=answers)
                return _go(2)
            if action == "reject":
                # "Not us": the claim goes, with everything derived from it.
                for k in ("lookup", "prefilled"):
                    answers.pop(k, None)
                save_state(c, u["id"], step=1, answers=answers,
                           declared_afm=None, ledger_found=None)
                return _go(1)
            # Continue without an ΑΦΜ.
            save_state(c, u["id"], step=2, answers=answers)
        return _go(2)

    # ---- step 2: what you offer ------------------------------------------ #
    @router.post("/step/2")
    async def step2_post(request: Request):
        u = _signed_in_post(request)
        form = await request.form()
        with cursor() as c:
            st = ensure_state(c, u["id"])
            answers = dict(st.get("answers") or {})
            typed = re.split(r"[\s,;]+", form.get("cpv_add") or "")
            cpv = valid_cpv_prefixes(c, list(form.getlist("cpv")) + typed)
            cat = valid_categories(c, list(form.getlist("cat")))
            answers["cpv"], answers["cat"] = cpv, cat
            save_state(c, u["id"], answers=answers)
            if form.get("action") == "back":
                return _go(1 if get_experience(c, u["id"]) is not False else 0)
            if not (cpv or cat):
                st["answers"] = answers
                return _render(request, 2, st, status_code=400,
                               error="Επιλέξτε τουλάχιστον ένα αντικείμενο ή μία κατηγορία.")
            save_state(c, u["id"], step=3)
        return _go(3)

    # ---- step 3: keywords -------------------------------------------------- #
    @router.post("/step/3")
    async def step3_post(request: Request):
        u = _signed_in_post(request)
        form = await request.form()
        kept, refused = parse_keywords(form.get("keywords"),
                                       list(form.getlist("kw_suggest")))
        with cursor() as c:
            st = ensure_state(c, u["id"])
            answers = dict(st.get("answers") or {})
            back = form.get("action") == "back"
            if len(kept) > MAX_KEYWORDS:
                refused = refused + [(k, "πάνω από το όριο") for k in kept[MAX_KEYWORDS:]]
                kept = kept[:MAX_KEYWORDS]
            answers["keywords"] = kept
            save_state(c, u["id"], answers=answers)
            if back:
                return _go(2)
            if refused:
                st["answers"] = answers
                return _render(request, 3, st, status_code=400, refused=refused,
                               error="Κάποιες λέξεις δεν έγιναν δεκτές.")
            save_state(c, u["id"], step=4)
        return _go(4)

    # ---- step 4: where and how big -------------------------------------- #
    @router.post("/step/4")
    async def step4_post(request: Request):
        u = _signed_in_post(request)
        form = await request.form()
        nuts = [n for n in dict.fromkeys(form.getlist("nuts")) if n in region_codes]
        vmin, vmax = _num(form.get("value_min")), _num(form.get("value_max"))
        with cursor() as c:
            st = ensure_state(c, u["id"])
            answers = dict(st.get("answers") or {})
            answers["nuts"] = nuts
            answers["value_min"], answers["value_max"] = vmin, vmax
            answers["awards"] = form.get("awards") == "1"
            save_state(c, u["id"], answers=answers)
            if form.get("action") == "back":
                return _go(3)
            if (vmin is not None and vmin < 0) or (vmax is not None and vmax < 0) \
                    or (vmin is not None and vmax is not None and vmin > vmax):
                st["answers"] = answers
                return _render(request, 4, st, status_code=400,
                               error="Ελέγξτε το εύρος αξίας: το «από» δεν μπορεί να ξεπερνά το «έως».")
            save_state(c, u["id"], step=5)
        return _go(5)

    # ---- step 5: overview -> create --------------------------------------- #
    @router.post("/step/5")
    async def step5_post(request: Request):
        u = _signed_in_post(request)
        form = await request.form()
        with cursor() as c:
            st = ensure_state(c, u["id"])
            answers = dict(st.get("answers") or {})
            names = dict(answers.get("names") or {})
            for key in ("notices", "keywords", "awards"):
                v = (form.get(f"name_{key}") or "").strip()
                if v:
                    names[key] = v[:120]
            answers["names"] = names
            save_state(c, u["id"], answers=answers)
            if form.get("action") == "back":
                return _go(4)
            if not build_profiles(answers):
                return _go(2)
            cap = max_saved() if max_saved else 25
            out = create_profiles(c, u["id"], answers, cap=cap)
            ids = [pid for pid, _ in out["created"]]
            save_state(c, u["id"], step=0,
                       completed_at=dt.datetime.now(dt.timezone.utc),
                       created_profile_ids=list(st.get("created_profile_ids") or []) + ids)
        if not ids:
            # Nothing fitted under the cap. Say so where the searches live.
            msg = "Δεν δημιουργήθηκαν αναζητήσεις: έχετε φτάσει το όριο αποθηκευμένων αναζητήσεων."
            return RedirectResponse("/account/searches?" + urlencode({"flash": msg}),
                                    status_code=303)
        if out["not_created"]:
            msg = ("Δημιουργήθηκαν όσες αναζητήσεις χωρούσαν· οι υπόλοιπες "
                   "ξεπερνούσαν το όριο αποθηκευμένων αναζητήσεων.")
            return RedirectResponse("/account/searches?" + urlencode({"flash": msg}),
                                    status_code=303)
        # Straight to the results of the main search — that is the point. The
        # count rides in the session, not the URL, so it is said once and does
        # not follow the customer through paging and re-searching.
        request.session["onboarding_created"] = len(ids)
        return RedirectResponse(f"/search-profiles/{ids[0]}/apply", status_code=303)

    # ---- skip ------------------------------------------------------------- #
    @router.post("/skip")
    async def skip(request: Request):
        u = _signed_in_post(request)
        with cursor() as c:
            ensure_state(c, u["id"])
            save_state(c, u["id"], skipped_at=dt.datetime.now(dt.timezone.utc))
        return RedirectResponse("/", status_code=303)

    return router
