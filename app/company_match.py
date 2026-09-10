"""
company_match.py — which ΓΕΜΗ company is this customer?

Customers register without an ΑΦΜ and only give one when they start paying, so
the CRM is full of accounts we cannot connect to the award ledger, to a fit
score, or to an invoice. This finds the company from a NAME (or from the label
in their email domain), an admin picks from the candidates, and the pick fills
the empty profile fields.

Three things shape the whole module:

  * **The registry cannot be asked about a domain.** Measured against the live
    API: `afm` and `name` filter, everything else (email, url, city,
    companyName, coNameEl) is silently ignored. So the customer's email domain
    is never a QUERY — it is a CONFIRMATION, checked against the candidate's own
    registry email, and at ~76% coverage it is the strongest tie-break we have.

  * **The registry's ranking is not trustworthy** and its candidate pool is
    capped at roughly twenty rows that no parameter can narrow. We re-rank every
    candidate here, on one scale, and the admin always sees the list.

  * **Never auto-link.** The ΑΦΜ decides the fit score, the ledger connection
    and eventually an invoice. Even a perfect score goes through the picker.

Candidates come from two places and are merged on the ΑΦΜ: our own contractor
ledger (proc.economic_operator — a hit there also links the customer to what
they have actually won) and the registry name search.

Linking posts ONE thing back: the ΑΦΜ. The company data is then rebuilt here
from the registry and the ledger, and re-scored, rather than read out of the
form. A registry record is up to 22KB and carries the company's officers; it
has no business making a round trip through a browser to become a customer
record, and one exact-ΑΦΜ call is a cheap price for that.

The import is fill-only-if-empty through leads.fill_if_empty, and what it wrote
is recorded in proc.customer_company_match.filled so unlink can revert exactly
that and nothing an admin typed afterwards.
"""

from __future__ import annotations

import difflib
import re

from psycopg.types.json import Json

try:
    from app import gemi_client
except ImportError:                       # pragma: no cover - run with --app-dir=app
    import gemi_client

try:
    from app import leads as _leads
except ImportError:                       # pragma: no cover
    import leads as _leads


# --------------------------------------------------------------------------- #
# what a link is allowed to touch
# --------------------------------------------------------------------------- #
# Written outright: the admin explicitly chose this company, so the identifiers
# are the point of the exercise. A DIFFERENT value already on the profile needs
# confirm=True (the route asks) — but an empty one is just filled.
LINK_FIELDS = ("vat_number", "reg_number", "operator_id")

# How the ΑΦΜ was arrived at (mirrors the CHECK on proc.customer_company_match).
METHODS = ("afm", "ledger", "gemi_name", "manual")

# Filled only into blanks, never over anything.
FILL_FIELDS = ("company", "city", "postal_code", "address", "phone",
               "country", "industry")

# Greek labels for the fields a link can write, so the card can say WHICH ones
# were filled in words rather than in column names.
FIELD_LABELS = {
    "company": "Επωνυμία", "city": "Πόλη", "postal_code": "Τ.Κ.",
    "address": "Διεύθυνση", "phone": "Τηλέφωνο", "country": "Χώρα",
    "industry": "Κλάδος", "vat_number": "ΑΦΜ", "reg_number": "Αρ. ΓΕΜΗ",
    "operator_id": "Ανάδοχος",
}

# Never written by this module, in either direction:
#   full_name    — a person, not a company
#   crm_stage / service / manager_id / lead_source / is_recipient
#                — the relationship, which the registry knows nothing about
#   about        — free notes
# app_user.email is not here because it is not ours to touch at all:
# auth.set_email clears email_verified_at and kills outstanding sign-in links.
PROTECTED = ("full_name", "crm_stage", "service", "manager_id",
             "lead_source", "about", "is_recipient")

# Scoring weights. Argued, not fitted — there is no win/loss data to fit to.
W_NAME = 0.45          # folded, legal-form-stripped name similarity
W_EMAIL_DOMAIN = 0.25  # customer's email domain == the candidate's registry email
W_SITE_DOMAIN = 0.10   # ... or its website host
W_PLACE = 0.10         # city / postal code agree
W_LEDGER = 0.10        # already a contractor in our award ledger

PENALTY_INACTIVE = 0.6   # struck off / in liquidation — demoted, never hidden
PENALTY_BRANCH = 0.9     # a branch (υποκατάστημα) of another entity

ACTIVE_STATUS_ID = 3     # ΓΕΜΗ status.id for 'Ενεργή'

LEDGER_CANDIDATES = 60   # rows pulled from the ledger before re-ranking
MAX_CANDIDATES = 12      # rows shown to the admin

# The ledger search MUST be written in this nesting order — f_unaccent(lower(x)),
# not lower(f_unaccent(x)) as leads._fold_sql builds — because that is the
# expression ix_eo_name_trgm was created on and Postgres matches index
# expressions structurally. The other order sequential-scans 143k rows.
_FOLD = "translate(proc.f_unaccent(lower({})), 'ς', 'σ')"

_LEGAL_NOISE = (
    "ανωνυμη", "ανωνυμος", "εταιρεια", "εταιρια", "μονοπροσωπη", "ιδιωτικη",
    "κεφαλαιουχικη", "εμπορικη", "βιομηχανικη", "και", "σια", "υιοι", "αφοι",
    "αε", "επε", "οε", "εε", "ικε", "αβεε", "αεβε", "ατε", "αξτε", "ltd",
    "sa", "ae", "ike", "oe", "ee",
)
_STATUS_PREFIX_RE = re.compile(r"^\s*\([^)]*\)\s*")
_NON_WORD_RE = re.compile(r"[^0-9a-zα-ω]+")


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def _s(v) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


def fold(s: str) -> str:
    """Lowercase, strip accents and final sigma — the Python twin of _FOLD."""
    s = _s(s).lower()
    out = []
    for ch in s:
        out.append({
            "ά": "α", "έ": "ε", "ή": "η", "ί": "ι", "ϊ": "ι", "ΐ": "ι",
            "ό": "ο", "ύ": "υ", "ϋ": "υ", "ΰ": "υ", "ώ": "ω", "ς": "σ",
            "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        }.get(ch, ch))
    return "".join(out)


def normalize_name(s: str) -> str:
    """A company name reduced to what actually identifies it: folded, without
    the registry's status prefix ('(ΔΙΑΓΡΑΦΗΚΕ)…'), without punctuation, and
    without the legal-form words every third Greek company shares.

    'Π.ΠΑΠΑΔΟΠΟΥΛΟΣ ΚΑΙ ΣΙΑ Ο.Ε.' and 'ΠΑΠΑΔΟΠΟΥΛΟΣ Ο.Ε.' both reduce to
    something built from 'παπαδοπουλοσ', which is the part worth comparing.
    """
    s = _STATUS_PREFIX_RE.sub("", _s(s))
    s = fold(s)
    words = [w for w in _NON_WORD_RE.split(s) if w]
    kept = [w for w in words if w not in _LEGAL_NOISE and len(w) > 1]
    return " ".join(kept or words)


def best_name_match(query: str, *names) -> tuple[float, str]:
    """(similarity, the string that matched) for `query` against any of `names`
    — a legal name and its trade titles.

    Which one matched is worth returning, not just how well: a sole trader
    called ΓΙΑΝΝΙΤΣΑΝΟΣ ΠΕΤΡΟΣ trading as ΕΛΛΑΔΙΚΑ ΠΕΤΡΕΛΑΙΑ scores 0.89 against
    ΕΛΛΗΝΙΚΑ ΠΕΤΡΕΛΑΙΑ, and an admin looking at the legal name alone would read
    that number as a bug.
    """
    q = normalize_name(query)
    if not q:
        return 0.0, ""
    best, matched = 0.0, ""
    for n in names:
        for candidate in (n if isinstance(n, (list, tuple)) else [n]):
            cn = normalize_name(candidate)
            if not cn:
                continue
            ratio = difflib.SequenceMatcher(None, q, cn).ratio()
            # a full containment ('ιντρακατ' inside 'κοινοπραξια ιντρακατ εργω')
            # is a strong signal that the raw ratio punishes for length
            if q in cn or cn in q:
                ratio = max(ratio, 0.82)
            if ratio > best:
                best, matched = ratio, _s(candidate)
    return round(best, 4), matched


def name_similarity(query: str, *names) -> float:
    """best_name_match's score on its own."""
    return best_name_match(query, *names)[0]


def domain_of(value: str) -> str:
    """The registrable-looking domain out of an email address or a URL."""
    v = _s(value).lower()
    if not v:
        return ""
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0].split(":")[0]
    return _leads.normalize_domain(v)


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def score_candidate(cand: dict, profile: dict, cust: dict,
                    freemail: set | None = None) -> tuple[float, dict]:
    """(score, signals) for one candidate against one customer.

    Components are always returned and always displayed — a total on its own
    tells an admin nothing about whether to trust the pick (same rule as
    app/fit.py).
    """
    profile = profile or {}
    cust = cust or {}
    freemail = {d.lower() for d in (freemail or set())}
    signals: dict = {}

    query = _s(profile.get("company")) or _s(cand.get("_query"))
    sim, matched = best_name_match(query, cand.get("name"),
                                   cand.get("titles") or [],
                                   cand.get("ledger_name"))
    # Only name the string that matched when it is NOT the name on the row.
    alias = matched if normalize_name(matched) != normalize_name(cand.get("name")) else ""
    signals["name"] = {"weight": W_NAME, "value": sim, "detail": alias}
    total = W_NAME * sim

    # The email domain can only ever confirm — see the module docstring.
    cust_domain = domain_of(cust.get("email"))
    if cust_domain and cust_domain in freemail:
        signals["email_domain"] = {"weight": W_EMAIL_DOMAIN, "value": 0.0,
                                   "detail": f"{cust_domain} (freemail)"}
    else:
        cand_domain = domain_of(cand.get("email"))
        hit = bool(cust_domain and cand_domain and cust_domain == cand_domain)
        signals["email_domain"] = {
            "weight": W_EMAIL_DOMAIN, "value": 1.0 if hit else 0.0,
            "detail": cand_domain or ""}
        total += W_EMAIL_DOMAIN * (1.0 if hit else 0.0)

        site_domain = domain_of(cand.get("url"))
        site_hit = bool(cust_domain and site_domain and cust_domain == site_domain)
        signals["site_domain"] = {
            "weight": W_SITE_DOMAIN, "value": 1.0 if site_hit else 0.0,
            "detail": site_domain or ""}
        total += W_SITE_DOMAIN * (1.0 if site_hit else 0.0)

    place = 0.0
    detail = ""
    if _s(profile.get("postal_code")) and _s(profile.get("postal_code")) == _s(cand.get("zip_code")):
        place, detail = 1.0, _s(cand.get("zip_code"))
    elif _s(profile.get("city")) and fold(profile.get("city")) == fold(cand.get("city")):
        place, detail = 0.7, _s(cand.get("city"))
    signals["place"] = {"weight": W_PLACE, "value": place, "detail": detail}
    total += W_PLACE * place

    in_ledger = 1.0 if cand.get("operator_id") else 0.0
    # No detail: the ledger name is the candidate's own name, and repeating it
    # under the row it labels is noise.
    signals["ledger"] = {"weight": W_LEDGER, "value": in_ledger, "detail": ""}
    total += W_LEDGER * in_ledger

    penalties = []
    if cand.get("status_id") is not None and cand.get("status_id") != ACTIVE_STATUS_ID:
        total *= PENALTY_INACTIVE
        penalties.append(_s(cand.get("status")) or "ανενεργή")
    if cand.get("is_branch"):
        total *= PENALTY_BRANCH
        penalties.append("υποκατάστημα")
    signals["penalties"] = penalties

    return round(min(total, 1.0), 3), signals


SIGNAL_LABELS = {
    "name": "ομοιότητα επωνυμίας",
    "email_domain": "ίδιο domain email",
    "site_domain": "ίδιο domain ιστοσελίδας",
    "place": "ίδια έδρα",
    "ledger": "υπάρχει στο μητρώο αναδόχων",
}


def explain(signals: dict) -> list[dict]:
    """The components behind a score, as [{'why', 'detail'}] — `why` is a fixed
    Greek phrase (a translation key), the variable part rides in `detail`, the
    same contract app/fit.py uses. Only components that actually contributed
    are listed; penalties are listed last because they are why a good-looking
    candidate scored low."""
    out = []
    for key, label in SIGNAL_LABELS.items():
        sig = (signals or {}).get(key) or {}
        value = sig.get("value") or 0
        if not value:
            continue
        detail = _s(sig.get("detail"))
        if key == "name":
            pct = f"{round(value * 100)}%"
            detail = f"{pct} ({detail})" if detail else pct
        out.append({"why": label, "detail": detail})
    for pen in (signals or {}).get("penalties") or []:
        out.append({"why": "μειωμένη βαθμολογία", "detail": _s(pen)})
    return out


# --------------------------------------------------------------------------- #
# candidate sources
# --------------------------------------------------------------------------- #
_TRGM_SCHEMA = None


def _trgm_schema(c) -> str:
    """Where pg_trgm lives. It is `public` on the local/prod databases and
    `proc` in the test harness, and neither similarity() nor the % operator is
    schema-qualified by default — an unqualified call resolves through
    search_path and simply does not exist in the other layout. Everything below
    is qualified explicitly instead (app/admin.py takes the other route and
    swallows the error, but there the fuzzy match is a bonus; here it is one of
    only two candidate sources)."""
    global _TRGM_SCHEMA
    if _TRGM_SCHEMA is None:
        c.execute("""SELECT n.nspname FROM pg_extension e
                     JOIN pg_namespace n ON n.oid = e.extnamespace
                     WHERE e.extname = 'pg_trgm'""")
        row = c.fetchone() or {}
        _TRGM_SCHEMA = row.get("nspname") or "public"
    return _TRGM_SCHEMA


def ledger_candidates(c, query: str, limit: int = LEDGER_CANDIDATES) -> list[dict]:
    """Contractors whose name is close to `query`, via ix_eo_name_trgm.

    Both operators are used on purpose: `%` catches a whole-name resemblance,
    `<%` catches the query appearing as a word inside a longer name (a joint
    venture, a name with the town appended). Both hit the same index, and the
    word match is discounted so a whole-name hit still sorts first.
    """
    q = fold(query)
    if not q:
        return []
    col = _FOLD.format("name")
    sch = _trgm_schema(c)
    c.execute(f"""
        SELECT operator_id, vat_number, name, city, postal_code, street_address,
               contact_email, contact_url, contact_phone, ar_gemi, country,
               greatest({sch}.similarity({col}, %(q)s),
                        {sch}.word_similarity(%(q)s, {col}) * 0.75) AS sim
        FROM proc.economic_operator
        WHERE {col} OPERATOR({sch}.%%) %(q)s
           OR %(q)s OPERATOR({sch}.<%%) {col}
        ORDER BY sim DESC, operator_id
        LIMIT %(lim)s
    """, {"q": q, "lim": limit})
    out = []
    for r in c.fetchall():
        afm = gemi_client.normalize_afm(r.get("vat_number"))
        out.append({
            "afm": afm or _s(r.get("vat_number")) or None,
            "name": _s(r.get("name")),
            "ledger_name": _s(r.get("name")),
            "titles": [],
            "city": _s(r.get("city")),
            "zip_code": _s(r.get("postal_code")),
            "email": _s(r.get("contact_email")),
            "url": _s(r.get("contact_url")),
            "phone": _s(r.get("contact_phone")),
            "address": _s(r.get("street_address")),
            "ar_gemi": _s(r.get("ar_gemi")),
            "country": _s(r.get("country")),
            "status": None, "status_id": None, "is_branch": None,
            "operator_id": r["operator_id"],
            "record": None,
            "source": "ledger",
        })
    return out


def _from_record(rec: dict) -> dict:
    """One ΓΕΜΗ search result → a candidate. Keeps the raw record so linking
    needs no second call."""
    flat = gemi_client.flatten(rec)
    return {
        "afm": gemi_client.normalize_afm(rec.get("afm")) or _s(rec.get("afm")) or None,
        "name": _s(flat.get("legal_name")),
        "ledger_name": "",
        "titles": [t for t in (rec.get("coTitlesEl") or []) if _s(t)],
        "city": _s(flat.get("city")),
        "zip_code": _s(flat.get("zip_code")),
        "email": _s(flat.get("email")),
        "url": _s(flat.get("url")),
        "phone": _s(flat.get("phone")),
        "address": " ".join(x for x in [_s(flat.get("street")),
                                        _s(flat.get("street_number"))] if x),
        "ar_gemi": _s(flat.get("ar_gemi")),
        "country": "GR",
        "status": _s(flat.get("status")),
        "status_id": flat.get("status_id"),
        "is_branch": flat.get("is_branch"),
        "legal_type": _s(flat.get("legal_type")),
        "industry": _s(flat.get("primary_kad_descr")),
        "operator_id": None,
        "record": rec,
        "source": "gemi",
    }


def _merge(ledger: list[dict], registry: list[dict]) -> list[dict]:
    """One company = one row. A candidate found in both keeps the registry's
    data (it is authoritative and fresher) plus the ledger's operator_id."""
    by_afm: dict = {}
    order: list = []
    for cand in list(ledger) + list(registry):
        key = cand.get("afm") or f"_op{cand.get('operator_id')}"
        if key not in by_afm:
            by_afm[key] = dict(cand)
            order.append(key)
            continue
        have = by_afm[key]
        if cand["source"] == "gemi":
            operator_id = have.get("operator_id") or cand.get("operator_id")
            ledger_name = have.get("ledger_name") or cand.get("ledger_name")
            merged = dict(cand)
            merged["operator_id"] = operator_id
            merged["ledger_name"] = ledger_name
            merged["source"] = "both" if operator_id else "gemi"
            by_afm[key] = merged
        else:
            have["operator_id"] = have.get("operator_id") or cand.get("operator_id")
            have["ledger_name"] = have.get("ledger_name") or cand.get("ledger_name")
            if have.get("operator_id"):
                have["source"] = "both" if have["source"] == "gemi" else have["source"]
    return [by_afm[k] for k in order]


def query_for(profile: dict, cust: dict) -> str:
    """What to search for when the admin has not typed anything: the company
    name if we have one, else the label of a non-generic email domain."""
    company = _s((profile or {}).get("company"))
    if company:
        return company
    domain = domain_of((cust or {}).get("email"))
    if domain:
        return domain.split(".")[0]
    return ""


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def search(c, uid: int, query: str | None = None,
           use_registry: bool = True) -> dict:
    """Candidates for one customer, best first.

    Returns {'query', 'status', 'registry_status', 'candidates'}. `status` is
    'ok' when there is anything to show; the registry's own outcome is kept
    separately so a ledger-only result is still useful when the registry is
    unreachable or the key is missing.
    """
    c.execute("""SELECT u.id, u.email, u.username
                 FROM proc.app_user u WHERE u.id = %s""", (uid,))
    cust = dict(c.fetchone() or {})
    c.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    profile = dict(c.fetchone() or {})

    q = _s(query) or query_for(profile, cust)
    if not q:
        return {"query": "", "status": "no_query", "registry_status": None,
                "candidates": []}

    ledger = ledger_candidates(c, q)

    registry: list[dict] = []
    registry_status = None
    if use_registry:
        try:
            registry_status, records = gemi_client.search_by_name_env(q)
        except RuntimeError:
            registry_status = "no_key"
            records = []
        registry = [_from_record(r) for r in records]

    cands = _merge(ledger, registry)
    freemail = set(_leads.list_freemail(c))
    for cand in cands:
        cand["_query"] = q
        cand["score"], cand["signals"] = score_candidate(
            cand, profile or {"company": q}, cust, freemail)
    cands.sort(key=lambda x: (-x["score"], x.get("name") or ""))
    cands = cands[:MAX_CANDIDATES]
    for cand in cands:
        cand["why"] = explain(cand["signals"])
        cand.pop("record", None)      # the raw record never reaches a template

    status = "ok" if cands else (registry_status or "not_found")
    return {"query": q, "status": status, "registry_status": registry_status,
            "candidates": cands}


# --------------------------------------------------------------------------- #
# link / unlink
# --------------------------------------------------------------------------- #
def current_match(c, uid: int):
    c.execute("""SELECT m.*, o.name AS operator_name
                 FROM proc.customer_company_match m
                 LEFT JOIN proc.economic_operator o ON o.operator_id = m.operator_id
                 WHERE m.user_id = %s""", (uid,))
    return c.fetchone()


def _fill_payload(gemi: dict | None, op: dict | None) -> dict:
    """The customer_profile values a linked company offers: the registry first,
    the contractor ledger where the registry is silent."""
    gemi = dict(gemi or {})
    op = dict(op or {})
    street = " ".join(x for x in [_s(gemi.get("street")),
                                  _s(gemi.get("street_number"))] if x)
    return {
        "company": _s(gemi.get("legal_name")) or _s(gemi.get("trade_title"))
                   or _s(op.get("name")) or None,
        "city": _s(gemi.get("city")) or _s(op.get("city")) or None,
        "postal_code": _s(gemi.get("zip_code")) or _s(op.get("postal_code")) or None,
        "address": street or _s(op.get("street_address")) or None,
        "phone": _s(gemi.get("phone")) or _s(op.get("contact_phone")) or None,
        "country": _s(op.get("country")) or "GR",
        "industry": _s(gemi.get("primary_kad_descr")) or None,
    }


def stored_gemi(c, afm: str):
    """The stored ΓΕΜΗ row for an ΑΦΜ, or None."""
    c.execute("""SELECT * FROM proc.gemi_enrichment
                 WHERE afm = %s AND fetch_status = 'ok'""", (afm,))
    return c.fetchone()


def _operator_for(c, afm: str):
    if not afm:
        return None
    c.execute("""SELECT operator_id, name, vat_number, city, postal_code,
                        street_address, contact_phone, contact_email,
                        contact_url, ar_gemi, country
                 FROM proc.economic_operator
                 WHERE vat_number = ANY(%s) ORDER BY operator_id LIMIT 1""",
              ([afm, afm.lstrip("0") or afm, "EL" + afm],))
    return c.fetchone()


class VatConflict(Exception):
    """The profile already carries a DIFFERENT ΑΦΜ. The admin must confirm."""

    def __init__(self, existing: str):
        super().__init__(existing)
        self.existing = existing


def _candidate_from_rows(gemi: dict | None, op: dict | None, afm: str) -> dict:
    """A candidate rebuilt from what we hold, for re-scoring at link time."""
    gemi = dict(gemi or {})
    op = dict(op or {})
    return {
        "afm": afm,
        "name": _s(gemi.get("legal_name")) or _s(op.get("name")),
        "ledger_name": _s(op.get("name")),
        "titles": [t for t in [_s(gemi.get("trade_title"))] if t],
        "city": _s(gemi.get("city")) or _s(op.get("city")),
        "zip_code": _s(gemi.get("zip_code")) or _s(op.get("postal_code")),
        "email": _s(gemi.get("email")) or _s(op.get("contact_email")),
        "url": _s(gemi.get("url")) or _s(op.get("contact_url")),
        "status": _s(gemi.get("status")) or None,
        "status_id": gemi.get("status_id"),
        "is_branch": gemi.get("is_branch"),
        "operator_id": op.get("operator_id"),
    }


def apply_match(c, uid: int, afm: str, by=None, confirm: bool = False) -> dict:
    """Link a customer to a picked company and fill the blanks it can fill.

    Everything but the ΑΦΜ is rebuilt here, from the registry and from our own
    ledger. The browser posts an identifier, never company data: a form field
    is not a trustworthy source for something we are about to write into a
    customer record, and the pick is worth one exact-ΑΦΜ call to confirm.

    In order:
      1. pull the registry row for this ΑΦΜ (which also fills the contractor
         row fill-only-if-empty, via gemi_client.upsert);
      2. write the identifiers;
      3. fill the empty profile fields, only-if-empty;
      4. record the pick, and exactly what step 3 wrote.

    Returns {'afm', 'filled', 'operator_id', 'gemi_status', 'method'}. Raises
    VatConflict when a different ΑΦΜ is already on the profile and confirm is
    False.
    """
    afm = gemi_client.normalize_afm(afm) or _s(afm)
    if not afm:
        raise ValueError("no ΑΦΜ to link")

    c.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    profile = dict(c.fetchone() or {})
    existing_vat = _s(profile.get("vat_number"))
    if existing_vat and gemi_client.normalize_afm(existing_vat) != afm and not confirm:
        raise VatConflict(existing_vat)

    # A registry pull is best-effort: a customer can be linked to a contractor
    # we know from the award ledger even when ΓΕΜΗ is unreachable or unkeyed.
    gemi_status = "skipped"
    try:
        gemi_status, _afm = gemi_client.enrich_one(c, afm)
    except RuntimeError:              # GEMI_API_KEY not set
        gemi_status = "no_key"
    except Exception:                 # noqa: BLE001 — never block the link
        gemi_status = "error"
    gemi = stored_gemi(c, afm)

    op = _operator_for(c, afm)
    operator_id = (op or {}).get("operator_id")

    # Re-score here rather than believe a number the browser posted: the
    # components are what an admin (or a later reader) judges the link by.
    c.execute("SELECT id, email, username FROM proc.app_user WHERE id = %s", (uid,))
    cust = dict(c.fetchone() or {})
    cand = _candidate_from_rows(gemi, op, afm)
    cand["_query"] = _s(profile.get("company"))
    score, signals = score_candidate(cand, profile, cust, set(_leads.list_freemail(c)))
    method = "gemi_name" if gemi else ("ledger" if operator_id else "manual")

    payload = _fill_payload(gemi, op)
    merged, filled = _leads.fill_if_empty(
        profile, [(col, payload.get(col)) for col in FILL_FIELDS])

    # The identifiers are written, not filled: the admin chose this company.
    ar_gemi = _s((gemi or {}).get("ar_gemi")) or _s((op or {}).get("ar_gemi"))
    link = {"vat_number": afm}
    if ar_gemi:
        link["reg_number"] = ar_gemi
    if operator_id:
        link["operator_id"] = operator_id
    for col, value in link.items():
        # Only a blank counts as ours to revert. Overwriting a value the admin
        # confirmed is deliberate, and unlink must not silently blank it later.
        if not _s(profile.get(col)):
            filled.setdefault(col, value)
    merged.update(link)

    cols = [col for col in list(FILL_FIELDS) + list(LINK_FIELDS)
            if col in filled or col in link]
    if cols:
        sets = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols)
        c.execute(
            f"""INSERT INTO proc.customer_profile (user_id, {', '.join(cols)},
                                                   updated_at, updated_by)
                VALUES (%s, {', '.join(['%s'] * len(cols))}, now(), %s)
                ON CONFLICT (user_id) DO UPDATE
                  SET {sets}, updated_at = now(), updated_by = EXCLUDED.updated_by""",
            [uid] + [merged.get(col) for col in cols] + [by])

    c.execute("""
        INSERT INTO proc.customer_company_match
            (user_id, afm, ar_gemi, operator_id, method, score, signals, filled,
             matched_by, matched_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
        ON CONFLICT (user_id) DO UPDATE SET
            afm = EXCLUDED.afm, ar_gemi = EXCLUDED.ar_gemi,
            operator_id = EXCLUDED.operator_id, method = EXCLUDED.method,
            score = EXCLUDED.score, signals = EXCLUDED.signals,
            filled = EXCLUDED.filled, matched_by = EXCLUDED.matched_by,
            matched_at = now()
    """, (uid, afm, ar_gemi or None, operator_id, method, score,
          Json(signals or {}), Json({k: str(v) for k, v in filled.items()}), by))

    return {"afm": afm, "filled": sorted(filled), "operator_id": operator_id,
            "gemi_status": gemi_status, "method": method, "score": score}


def unlink(c, uid: int, by=None) -> list[str]:
    """Drop the link and revert ONLY the fields the import wrote that still hold
    exactly what it wrote. Anything an admin edited since is left alone.
    Returns the reverted column names."""
    row = current_match(c, uid)
    if not row:
        return []
    filled = dict(row.get("filled") or {})
    c.execute("SELECT * FROM proc.customer_profile WHERE user_id = %s", (uid,))
    profile = dict(c.fetchone() or {})

    reverted = []
    for col, written in filled.items():
        if col in PROTECTED:                     # belt and braces
            continue
        if _s(profile.get(col)) == _s(written):
            reverted.append(col)
    if reverted:
        sets = ", ".join(f"{col} = NULL" for col in reverted)
        c.execute(f"""UPDATE proc.customer_profile
                         SET {sets}, updated_at = now(), updated_by = %s
                       WHERE user_id = %s""", (by, uid))
    c.execute("DELETE FROM proc.customer_company_match WHERE user_id = %s", (uid,))
    return sorted(reverted)
