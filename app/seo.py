"""app/seo.py — the acquisition layer: what a *crawler* is allowed to see.

Nothing here changes what a **person** may read. The freemium teaser rule
(`main._is_gated`) still decides that, server-side, exactly as before: an
anonymous visitor gets page 1 of a list and the hero of an act, and Googlebot
is an anonymous visitor like any other. This module only decides what a robot
is *told* — robots.txt, the sitemaps, the canonical URL and the per-page
`<meta name="robots">` — so the corpus can be found at all.

Three rules shape everything below.

1. **Off unless the deployment says otherwise.** A dev box, a preview deploy
   and a staging copy serve the same HTML as production. The one mistake you
   cannot take back is Google indexing three copies of the corpus under three
   hostnames, so `enabled()` is true only in production, and a production
   operator can still turn it off. It fails towards *noindex*.

2. **One URL per page.** Every act is reachable as `/act/X`, `/act/X?q=…`
   (from a search) and `/act/X?cpv=…`; only the bare path is canonical. The
   query string survives on the page — highlighting still works — but the
   `<link rel="canonical">` points at the clean URL, so the ranking signal
   lands in one place instead of being split across every search that led
   there.

3. **A bounded crawl space.** The filter form is a GET form over 2.9M acts:
   left alone, a crawler would walk an unbounded product of facets, each one a
   query no faster than a human's. So only a handful of *single-facet* landing
   pages are indexable (one act type, one procedure, one region…), the free-
   text and pagination parameters are disallowed outright in robots.txt, and
   everything else is `noindex, follow` — followable, so the crawler still
   reaches the act pages, but never worth indexing.

The sitemaps follow the same logic: the acts of the last year (capped), every
authority and contractor, the glossary. Not 2.9M URLs — a free instance would
be crawled into the ground for pages nobody searches for.
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode, quote

# --------------------------------------------------------------------------- #
# Switch
# --------------------------------------------------------------------------- #
# Spellings of "yes". Mirrors mailer._flag: an operator who means "on" has five
# ways to say it, and ANYTHING else — including the empty string, "maybe" or a
# typo — leaves indexing off. The asymmetry is deliberate; see rule 1 above.
_ON_VALUES = frozenset({"1", "true", "yes", "on", "y"})

def _is_prod() -> bool:
    """Read at call time, not import time — a test (and an operator flipping a
    dashboard variable) must be able to change the answer."""
    return bool(os.environ.get("RENDER")
                or os.environ.get("APP_ENV", "").lower() == "production")


def enabled() -> bool:
    """May search engines index this deployment at all?

    Default: on in production, off everywhere else. `SEO_INDEX` overrides in
    both directions — `SEO_INDEX=1` to let a staging host be indexed on
    purpose, anything not in `_ON_VALUES` (including `0` and `false`) to switch
    production off. Unset in production means on, which is the only case where
    silence means yes, and production is the one host that should be found.
    """
    raw = (os.environ.get("SEO_INDEX") or "").strip().lower()
    if raw:
        return raw in _ON_VALUES
    return _is_prod()


def base_url(request=None) -> str:
    """Absolute origin for canonical links and sitemap entries.

    `APP_BASE_URL` is the same variable the digest emails already use, so a
    deployment that can mail a working link can also emit a working canonical.
    Falling back to the request's own origin keeps dev and tests honest rather
    than hard-coding localhost into a sitemap.
    """
    env = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8000"


def absolute(request, path: str) -> str:
    return f"{base_url(request)}{path}"


# --------------------------------------------------------------------------- #
# What may be indexed
# --------------------------------------------------------------------------- #
# Exact paths that are indexable when they carry no query string.
_INDEXABLE_PATHS = frozenset({
    "/", "/authorities", "/contractors", "/glossary", "/data-sources",
    # /ai is a factual statement about the software, kept current from the live
    # configuration — unlike /privacy and /terms, which are draft legal text
    # and stay out of the index until a lawyer has been over them.
    "/ai",
})

# Two-segment detail pages: /act/<adam>, /authority/<org_id>, …. A third
# segment is always a sub-resource (/act/X/ai, /act/X/occurrences,
# /contractor/X/gemi-refresh) and never a page in its own right.
_INDEXABLE_PREFIXES = ("/act/", "/authority/", "/contractor/", "/glossary/")

# Never indexed, whatever else is true: the private surface, the machine
# endpoints, the two aggregation pages (expensive, and a crawler has no use for
# them) and the draft legal text.
_NOINDEX_PREFIXES = (
    "/admin", "/account", "/api", "/export", "/digests", "/login", "/logout",
    "/register", "/set-lang", "/tables", "/help", "/explore", "/analytics",
    "/telephony", "/healthz", "/version", "/static", "/privacy", "/terms",
    "/notice/",
)

# The only query parameters that can make an indexable landing page, and the
# values each one accepts. main.py fills these in from the real code lists
# (set_facet_values) so the allowlist cannot drift from the filters that exist.
# A facet page is indexable only with EXACTLY ONE of these and nothing else —
# combinations multiply without bound and are the whole reason crawlers get
# lost in faceted search.
FACET_PARAMS = ("type", "procedure_type", "contract_type", "nuts", "source")
_FACET_VALUES: dict[str, frozenset[str]] = {k: frozenset() for k in FACET_PARAMS}


def set_facet_values(mapping: dict) -> None:
    """Register the legal values per facet (called once from main at import)."""
    for k in FACET_PARAMS:
        _FACET_VALUES[k] = frozenset(str(v) for v in mapping.get(k, ()))


def facet_values(param: str) -> frozenset[str]:
    return _FACET_VALUES.get(param, frozenset())


def _is_detail_path(path: str) -> bool:
    if not path.startswith(_INDEXABLE_PREFIXES):
        return False
    # exactly two non-empty segments — "/act/25SYMV1" yes, "/act/25SYMV1/ai" no
    return len([s for s in path.split("/") if s]) == 2


def _single_facet(query_items: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The one allowlisted facet this query is, or None if it is anything else."""
    real = [(k, v) for k, v in query_items if v.strip()]
    if len(real) != 1:
        return None
    k, v = real[0]
    if k in FACET_PARAMS and v in _FACET_VALUES.get(k, ()):
        return (k, v)
    return None


def is_indexable(path: str, query_items: list[tuple[str, str]]) -> bool:
    """Should this exact URL be offered to a search engine's index?"""
    if not enabled():
        return False
    if path != "/" and path.rstrip("/") == "":
        return False
    if any(path.startswith(p) for p in _NOINDEX_PREFIXES):
        return False
    facet = _single_facet(query_items)
    if [k for k, v in query_items if v.strip()] and not facet:
        return False           # any other query string: crawl it, don't index it
    if facet:
        # Facet landings exist for the list pages only — an act detail page with
        # "?type=contract" hung off it is still just that act.
        return path in ("/", "/authorities", "/contractors")
    return path in _INDEXABLE_PATHS or _is_detail_path(path)


def robots_for(path: str, query_items: list[tuple[str, str]]) -> str:
    """The page's `<meta name="robots">` content.

    `noindex, follow` rather than `noindex, nofollow` for ordinary non-indexable
    pages: the crawler should still walk their links to reach the act pages that
    ARE worth indexing. The private surface gets `nofollow` too — there is
    nothing behind a login worth crawling towards.
    """
    if not enabled():
        return "noindex, nofollow"
    if any(path.startswith(p) for p in _NOINDEX_PREFIXES):
        return "noindex, nofollow"
    return "index, follow" if is_indexable(path, query_items) else "noindex, follow"


def canonical_for(request) -> str:
    """One URL per page (rule 2).

    A single indexable facet keeps its parameter — `/?type=contract` is a page
    in its own right. Everything else canonicalises to the bare path, which
    folds every `/act/X?q=…` arrival back onto `/act/X`.
    """
    path = request.url.path
    items = list(request.query_params.multi_items())
    facet = _single_facet(items)
    if facet and path in ("/", "/authorities", "/contractors"):
        return absolute(request, f"{path}?{urlencode([facet])}")
    return absolute(request, path)


# --------------------------------------------------------------------------- #
# robots.txt
# --------------------------------------------------------------------------- #
# Parameters that must never be crawled. Free text (q/fulltext/tables_q) is
# literally unbounded; page/sort/per_page multiply every other URL by their own
# cardinality; the date and value ranges are continuous. Blocking them here is
# cheaper than serving the crawl and then telling it not to index the result.
_BLOCKED_PARAMS = ("q", "fulltext", "tables_q", "page", "sort", "per_page",
                   "date_from", "date_to", "deadline_from", "deadline_to",
                   "value_min", "value_max", "cpv", "cat", "authority")

_DISALLOW_PATHS = ("/admin", "/account", "/api/", "/export/", "/digests/",
                   "/login", "/logout", "/register", "/set-lang", "/tables",
                   "/help", "/explore", "/analytics", "/notice/")


def robots_txt(request) -> str:
    """The body of /robots.txt.

    When indexing is off this is a flat `Disallow: /` — no sitemap line, no
    exceptions. That is the entire staging story.
    """
    if not enabled():
        return "User-agent: *\nDisallow: /\n"
    lines = ["User-agent: *"]
    lines += [f"Disallow: {p}" for p in _DISALLOW_PATHS]
    lines += [f"Disallow: /*?*{p}=" for p in _BLOCKED_PARAMS]
    lines += ["Allow: /", "",
              f"Sitemap: {absolute(request, '/sitemap.xml')}", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Sitemaps
# --------------------------------------------------------------------------- #
# One sitemap file holds at most 50k URLs by the standard; 10k keeps each
# response around a megabyte, which matters more here — the body is built in
# memory on a small instance.
CHUNK = 10_000

# How much of the corpus is advertised. The window is what a person plausibly
# searches for; the cap is what a free instance can afford to have crawled.
# Both are env-tunable because widening them later is the cheap experiment.
def act_window_days() -> int:
    try:
        return max(1, int(os.environ.get("SEO_ACT_WINDOW_DAYS", "365")))
    except ValueError:
        return 365


def act_cap() -> int:
    try:
        return max(0, int(os.environ.get("SEO_ACT_MAX", "50000")))
    except ValueError:
        return 50_000


_XML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def xml_escape(s: str) -> str:
    return "".join(_XML_ESC.get(ch, ch) for ch in s)


def loc(request, path: str) -> str:
    """An absolute, percent-encoded, XML-safe <loc>.

    Act ADAMs and authority ids are ASCII, but a contractor key is whatever the
    source published; encoding the path segment keeps a stray character from
    producing an invalid sitemap that a crawler rejects wholesale.
    """
    head, _, tail = path.rpartition("/")
    return xml_escape(absolute(request, f"{head}/{quote(tail, safe='')}"))


def url_entry(location: str, lastmod=None, changefreq=None, priority=None) -> str:
    parts = [f"<url><loc>{location}</loc>"]
    if lastmod:
        parts.append(f"<lastmod>{lastmod}</lastmod>")
    if changefreq:
        parts.append(f"<changefreq>{changefreq}</changefreq>")
    if priority:
        parts.append(f"<priority>{priority}</priority>")
    parts.append("</url>")
    return "".join(parts)


def urlset(entries: list[str]) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(entries) + "\n</urlset>\n")


def iso_date(v) -> str | None:
    """<lastmod> as a plain date. Accepts a date or a timestamp."""
    if v is None:
        return None
    to_date = getattr(v, "date", None)          # datetime -> date; date has none
    return (to_date() if callable(to_date) else v).isoformat()


# --- counts, cached ---------------------------------------------------------
# The index file has to name every chunk, which means knowing how many rows
# each kind has. Those are three aggregate queries; a crawler hitting the index
# repeatedly must not turn them into load, and the numbers only have to be
# roughly right — a sitemap is a hint, not a contract.
_COUNT_TTL = 3600
_counts_cache: dict = {"at": 0.0, "data": None}


def counts(c) -> dict:
    now = time.monotonic()
    if _counts_cache["data"] is not None and now - _counts_cache["at"] < _COUNT_TTL:
        return _counts_cache["data"]
    c.execute("""SELECT count(*) AS n FROM proc.procurement_act
                 WHERE signed_date >= current_date - %s::int""",
              (act_window_days(),))
    n_acts = min(int(c.fetchone()["n"]), act_cap())
    c.execute("SELECT count(*) AS n FROM proc.authority")
    n_auth = int(c.fetchone()["n"])
    c.execute("SELECT count(*) AS n FROM proc.economic_operator")
    n_contr = int(c.fetchone()["n"])
    data = {"acts": n_acts, "authorities": n_auth, "contractors": n_contr}
    _counts_cache.update(at=now, data=data)
    return data


def reset_cache() -> None:
    """Test hook — the counts are cached for an hour by design."""
    _counts_cache.update(at=0.0, data=None)


def chunks_for(n: int) -> int:
    return max(1, (n + CHUNK - 1) // CHUNK) if n else 0


# --- the four kinds ---------------------------------------------------------
def act_rows(c, page: int) -> list:
    """The `page`-th block of recent acts, newest first.

    Ordered by (signed_date DESC, adam) so paging is stable and the cap really
    does take the most recent acts. ix_act_signed_date carries the window.
    """
    cap = act_cap()
    offset = (page - 1) * CHUNK
    if offset >= cap:
        return []
    limit = min(CHUNK, cap - offset)
    c.execute("""SELECT adam, signed_date, ingested_at, last_update_date
                 FROM proc.procurement_act
                 WHERE signed_date >= current_date - %s::int
                 ORDER BY signed_date DESC, adam
                 LIMIT %s OFFSET %s""",
              (act_window_days(), limit, offset))
    return c.fetchall()


def authority_rows(c, page: int) -> list:
    c.execute("""SELECT org_id FROM proc.authority
                 ORDER BY org_id LIMIT %s OFFSET %s""",
              (CHUNK, (page - 1) * CHUNK))
    return c.fetchall()


def contractor_rows(c, page: int) -> list:
    c.execute("""SELECT vat_number FROM proc.economic_operator
                 ORDER BY vat_number LIMIT %s OFFSET %s""",
              (CHUNK, (page - 1) * CHUNK))
    return c.fetchall()


# --------------------------------------------------------------------------- #
# Descriptions and structured data
# --------------------------------------------------------------------------- #
# The <meta name="description"> is the one line a person reads in the results
# page before deciding whether to click, so it is built from the record's own
# facts — type, authority, value, date — rather than a template sentence with
# the title dropped in. Both languages live here because the page is one URL
# serving whichever language the visitor has chosen.

def _clamp(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip(" ,.·-—") + "…"


def fmt_eur(v) -> str:
    """1234567.8 -> '1.234.567,80 €' (Greek convention, used in both languages
    because the figure is a euro amount published in Greece)."""
    if v is None:
        return ""
    try:
        s = f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return ""
    return s.replace(",", " ").replace(".", ",").replace(" ", ".") + " €"


def describe_act(n, type_label: str, lang: str = "el") -> str:
    title = _clamp(n.get("title") or n.get("adam") or "", 110)
    auth = _clamp(n.get("authority_name") or "", 60)
    value = fmt_eur(n.get("resolved_value") or n.get("total_cost_with_vat"))
    date = n.get("signed_date")
    date_s = date.isoformat() if date is not None and hasattr(date, "isoformat") else ""
    if lang == "en":
        bits = [f"{type_label}: {title}" if title else type_label]
        if auth:
            bits.append(f"Contracting authority: {auth}")
        if value:
            bits.append(f"Value {value}")
        if date_s:
            bits.append(date_s)
        return _clamp(". ".join(bits) + ".", 300)
    bits = [f"{type_label}: {title}" if title else type_label]
    if auth:
        bits.append(f"Αναθέτουσα αρχή: {auth}")
    if value:
        bits.append(f"Αξία {value}")
    if date_s:
        bits.append(date_s)
    return _clamp(". ".join(bits) + ".", 300)


def describe_entity(kind: str, name: str, n_acts, lang: str = "el") -> str:
    """kind is 'authority' or 'contractor'."""
    name = _clamp(name or "", 90)
    try:
        n = int(n_acts or 0)
    except (TypeError, ValueError):
        n = 0
    # The count is absent on the teaser render (the gated page deliberately runs
    # no aggregate queries), so the sentence has to read correctly without it
    # rather than trailing an empty "0 acts".
    if lang == "en":
        what = "Contracting authority" if kind == "authority" else "Contractor"
        head = f"{what}: {name}."
        lead = f" {n:,} acts recorded —" if n else ""
        return _clamp(f"{head}{lead} notices, awards, contracts and payments, "
                      f"with values and counterparties.", 300)
    what = "Αναθέτουσα αρχή" if kind == "authority" else "Ανάδοχος"
    head = f"{what}: {name}."
    lead = f" {n:,} καταγεγραμμένες πράξεις —".replace(",", ".") if n else ""
    return _clamp(f"{head}{lead} Προκηρύξεις, αναθέσεις, συμβάσεις και πληρωμές, "
                  f"με αξίες και αντισυμβαλλομένους.", 300)


# --- JSON-LD ---------------------------------------------------------------
# Kept deliberately small. schema.org has no type that honestly models a public
# procurement act, and inventing one (Product, Offer) would be lying to a
# machine about what the page is. Breadcrumbs are true and useful in a results
# page; an authority and a contractor really are Organizations; a glossary
# entry really is a DefinedTerm. That is the whole vocabulary used here.
def json_ld(obj) -> str:
    """Serialise for embedding in a <script type="application/ld+json">.

    `<` and `&` are escaped so nothing inside the data — a company name with an
    angle bracket, say — can close the script element early.
    """
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def breadcrumbs(request, trail: list[tuple[str, str]]) -> str:
    """trail = [(name, path), …] from the site root to this page."""
    return json_ld({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name,
             "item": absolute(request, path)}
            for i, (name, path) in enumerate(trail)],
    })


# The country column holds whatever the source published — "Ελλάδα", "EL",
# "Ηνωμένο Βασίλειο", or nothing. schema.org wants an ISO 3166-1 alpha-2 code,
# and a Greek display name in `addressCountry` is worse than no address at all:
# it looks like data and parses as noise. So the value is normalised, and
# anything that cannot be resolved to a code is simply omitted.
_COUNTRY_CODES = {"ελλαδα": "GR", "ελλάδα": "GR", "el": "GR", "gr": "GR"}


def _country_code(raw: str | None) -> str | None:
    v = (raw or "").strip()
    if not v:
        return None
    known = _COUNTRY_CODES.get(v.lower())
    if known:
        return known
    return v.upper() if len(v) == 2 and v.isalpha() else None


def organization_ld(request, *, name: str, path: str, vat: str | None = None,
                    country: str | None = None) -> str:
    data = {"@context": "https://schema.org", "@type": "Organization",
            "name": name, "url": absolute(request, path)}
    if vat:
        data["taxID"] = vat
        data["identifier"] = vat
    code = _country_code(country)
    if code:
        data["address"] = {"@type": "PostalAddress", "addressCountry": code}
    return json_ld(data)
