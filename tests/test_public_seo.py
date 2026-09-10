"""The public/acquisition layer: robots, canonicals, sitemaps, the intro band.

Two things are being protected here, and they are different.

The first is a *safety* property: an unindexed deployment must stay unindexed.
Indexing is off unless the host says otherwise, and every assertion about the
"on" state runs inside a fixture that turns it on explicitly — so if the
default ever flips, these tests fail rather than quietly passing.

The second is the crawl-space bound. The filter form is a GET form over
millions of acts; the rule that keeps a crawler out of it (one allowlisted
facet, everything else noindex) is easy to widen by accident, so the shape of
that rule is asserted directly rather than inferred from a rendered page.
"""
from __future__ import annotations

import pytest

from app import glossary as gl
from app import seo
from tests.helpers import connect, grant, login, make_user


@pytest.fixture()
def indexed(monkeypatch):
    """Turn indexing on for one test, and clear the hour-long counts cache so
    the sitemap index reflects whatever this test inserted."""
    monkeypatch.setenv("SEO_INDEX", "1")
    monkeypatch.setenv("APP_BASE_URL", "https://example.gr")
    seo.reset_cache()
    yield
    seo.reset_cache()


@pytest.fixture()
def acts(db):
    """A handful of recent acts, one authority, one operator — enough for the
    sitemap chunks to have something to list."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'SEO%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id = 'SEO-AUTH-1'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = '999000111'")
    cur.execute("""INSERT INTO proc.authority (org_id, name)
                   VALUES ('SEO-AUTH-1', 'ΔΗΜΟΣ ΔΟΚΙΜΗΣ')""")
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES ('999000111', 'ΑΛΦΑ ΑΕ', true)""")
    for i in range(3):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source,
                          signed_date, authority_id, total_cost_with_vat)
                       VALUES (%s, 'contract', %s, 'import', 'khmdhs',
                               current_date - %s, 'SEO-AUTH-1', 12345.67)""",
                    (f"SEO{i:04d}", f"Δοκιμαστική σύμβαση {i}", i))
    # The two directory pages read precomputed counts; an unpopulated
    # matview is an error, not an empty result.
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_authority_counts")
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_contractor_counts")
    yield
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'SEO%'")
    cur.execute("DELETE FROM proc.authority WHERE org_id = 'SEO-AUTH-1'")
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = '999000111'")


# --------------------------------------------------------------------------- #
# The switch: off by default, and off means off
# --------------------------------------------------------------------------- #
def test_indexing_is_off_by_default_outside_production():
    assert seo.enabled() is False


def test_robots_disallows_everything_when_off(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text.strip() == "User-agent: *\nDisallow: /"
    # no sitemap is advertised on a host that must not be crawled
    assert "Sitemap:" not in r.text


def test_sitemaps_404_when_indexing_is_off(client):
    for path in ("/sitemap.xml", "/sitemap-pages.xml", "/sitemap-acts-1.xml"):
        assert client.get(path).status_code == 404, path


def test_every_page_is_noindex_when_off(client):
    assert 'content="noindex, nofollow"' in client.get("/").text


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "maybe"])
def test_switch_fails_towards_off(monkeypatch, value):
    """Only an explicit yes turns it on. A dashboard-typed 'false' — or a typo —
    must not leave a staging host indexable."""
    monkeypatch.setenv("SEO_INDEX", value)
    monkeypatch.setenv("RENDER", "1")   # even on a production-looking host
    assert seo.enabled() is False


def test_switch_on_in_production_without_the_variable(monkeypatch):
    monkeypatch.delenv("SEO_INDEX", raising=False)
    monkeypatch.setenv("RENDER", "1")
    assert seo.enabled() is True


def test_empty_variable_reads_as_unset(monkeypatch):
    """SEO_INDEX= (empty) is 'I did not answer', not 'no' — same convention as
    every other variable here. On production that still means on; off the
    production hosts it still means off."""
    monkeypatch.setenv("SEO_INDEX", "")
    monkeypatch.setenv("RENDER", "1")
    assert seo.enabled() is True
    monkeypatch.delenv("RENDER")
    assert seo.enabled() is False


# --------------------------------------------------------------------------- #
# robots.txt when on
# --------------------------------------------------------------------------- #
def test_robots_when_on(client, indexed):
    body = client.get("/robots.txt").text
    assert "Sitemap: https://example.gr/sitemap.xml" in body
    for private in ("/admin", "/account", "/export/", "/digests/", "/api/"):
        assert f"Disallow: {private}" in body, private
    # the parameters that make the crawl space unbounded
    for param in ("q", "page", "sort", "fulltext", "cpv"):
        assert f"Disallow: /*?*{param}=" in body, param
    # ...but not the enumerable facets, which ARE landing pages
    assert "Disallow: /*?*type=" not in body


# --------------------------------------------------------------------------- #
# One URL per page
# --------------------------------------------------------------------------- #
def test_canonical_drops_the_search_that_led_here(client, indexed, acts):
    html = client.get("/act/SEO0000?q=%CE%B4%CE%BF%CE%BA").text
    assert '<link rel="canonical" href="https://example.gr/act/SEO0000">' in html
    # arriving from a search is not a page of its own
    assert 'name="robots" content="noindex, follow"' in html


def test_canonical_keeps_a_single_indexable_facet(client, indexed):
    html = client.get("/?type=contract").text
    assert 'href="https://example.gr/?type=contract"' in html
    assert 'name="robots" content="index, follow"' in html


def test_clean_pages_are_indexable(client, indexed, acts):
    for path in ("/", "/authorities", "/contractors", "/glossary"):
        assert 'content="index, follow"' in client.get(path).text, path


@pytest.mark.parametrize("qs", [
    "?q=καθαριότητα",            # free text: unbounded
    "?type=contract&nuts=EL30",  # two facets: combinatorial
    "?type=nonsense",            # not a real value
    "?page=2",                   # pagination
])
def test_query_urls_are_not_indexable(client, indexed, qs):
    html = client.get("/" + qs).text
    assert 'name="robots" content="noindex, follow"' in html
    # the signal still consolidates onto the clean page
    assert '<link rel="canonical" href="https://example.gr/">' in html


def test_act_subresources_are_never_indexable(indexed):
    assert seo.is_indexable("/act/SEO0000", []) is True
    assert seo.is_indexable("/act/SEO0000/ai", []) is False
    assert seo.is_indexable("/act/SEO0000/occurrences", []) is False


def test_private_surface_is_nofollow_too(indexed):
    for path in ("/admin", "/admin/crm/3", "/account/searches", "/export/acts",
                 "/help", "/explore", "/analytics", "/privacy", "/terms"):
        assert seo.robots_for(path, []) == "noindex, nofollow", path


def test_admin_masthead_is_hard_noindex(client, indexed):
    """base.html is admin/legacy surface: noindex regardless of the switch."""
    make_user("seoadm", "goodpassword1", role="admin")
    login(client, "seoadm", "goodpassword1")
    html = client.get("/admin").text
    assert 'content="noindex, nofollow"' in html


# --------------------------------------------------------------------------- #
# Sitemaps
# --------------------------------------------------------------------------- #
def test_sitemap_index_lists_every_chunk(client, indexed, acts):
    body = client.get("/sitemap.xml").text
    assert body.startswith('<?xml version="1.0"')
    assert "https://example.gr/sitemap-pages.xml" in body
    assert "https://example.gr/sitemap-acts-1.xml" in body
    assert "https://example.gr/sitemap-authorities-1.xml" in body
    assert "https://example.gr/sitemap-contractors-1.xml" in body


def test_sitemap_pages_covers_the_landings_and_the_glossary(client, indexed):
    body = client.get("/sitemap-pages.xml").text
    assert "https://example.gr/</loc>" in body
    assert "https://example.gr/glossary</loc>" in body
    assert "https://example.gr/?type=contract</loc>" in body
    assert "https://example.gr/?procedure_type=6</loc>" in body
    for slug in gl.slugs():
        assert f"https://example.gr/glossary/{slug}</loc>" in body, slug


def test_sitemap_pages_only_advertises_indexable_urls(client, indexed):
    """Whatever the pages sitemap lists, the page itself must agree is
    indexable — otherwise we are asking Google to crawl what we then refuse."""
    import re
    from urllib.parse import urlsplit, parse_qsl
    body = client.get("/sitemap-pages.xml").text
    for loc in re.findall(r"<loc>([^<]+)</loc>", body):
        u = urlsplit(loc.replace("&amp;", "&"))
        assert seo.is_indexable(u.path, parse_qsl(u.query)), loc


def test_sitemap_act_chunk(client, indexed, acts):
    body = client.get("/sitemap-acts-1.xml").text
    assert "https://example.gr/act/SEO0000</loc>" in body
    assert "<lastmod>" in body


def test_sitemap_entity_chunks(client, indexed, acts):
    assert "/authority/SEO-AUTH-1</loc>" in client.get("/sitemap-authorities-1.xml").text
    assert "/contractor/999000111</loc>" in client.get("/sitemap-contractors-1.xml").text


def test_sitemap_page_past_the_end_is_404(client, indexed, acts):
    """An empty <urlset> tells a crawler the URLs were withdrawn — not the same
    thing as 'there is no such page'."""
    assert client.get("/sitemap-acts-99.xml").status_code == 404


def test_unknown_sitemap_kind_is_404(client, indexed):
    assert client.get("/sitemap-widgets-1.xml").status_code == 404


def test_act_cap_bounds_what_is_advertised(monkeypatch, client, indexed, acts):
    """The cap is what keeps a small instance from being crawled into the
    ground; it must bound the chunk contents, not just the index."""
    monkeypatch.setenv("SEO_ACT_MAX", "2")
    seo.reset_cache()
    body = client.get("/sitemap-acts-1.xml").text
    assert body.count("<url>") == 2


def test_loc_is_escaped_and_encoded(indexed):
    class _Req:
        pass
    out = seo.loc(_Req(), "/contractor/A&B 1")
    # the segment is percent-encoded, so nothing reaches the XML that could
    # break the document — no raw ampersand, no space
    assert out.endswith("/contractor/A%26B%201")


# --------------------------------------------------------------------------- #
# The first-visit band
# --------------------------------------------------------------------------- #
def test_intro_band_shown_to_anonymous_landing(client):
    html = client.get("/").text
    assert 'class="pi"' in html
    assert "Γλωσσάρι" in html


def test_intro_band_hidden_once_a_query_is_typed(client):
    assert 'class="pi"' not in client.get("/?q=test").text


def test_intro_band_hidden_for_an_entitled_customer(client):
    uid = make_user("seocust", "goodpassword1")
    grant(uid, "pro", days=30)
    login(client, "seocust", "goodpassword1")
    assert 'class="pi"' not in client.get("/").text


def test_intro_band_survives_a_stats_failure(client, monkeypatch):
    """The corpus figures are decoration; the page must render without them."""
    from app import main as m
    monkeypatch.setattr(m, "_public_stats", lambda: None)
    r = client.get("/")
    assert r.status_code == 200 and 'class="pi"' in r.text


# --------------------------------------------------------------------------- #
# Structured data — on the page a crawler actually gets
# --------------------------------------------------------------------------- #
def test_entity_teaser_carries_structured_data(client, indexed, acts):
    """A search engine is an anonymous visitor, so the TEASER render is the one
    it indexes. Structured data added only to the full (signed-in) render would
    never be seen by anything it was written for."""
    for path, name in (("/authority/SEO-AUTH-1", "ΔΗΜΟΣ ΔΟΚΙΜΗΣ"),
                       ("/contractor/999000111", "ΑΛΦΑ ΑΕ")):
        html = client.get(path).text
        assert "reg-cta" in html, f"{path} should be the teaser"
        assert '"@type":"Organization"' in html, path
        assert name in html, path


def test_act_teaser_carries_breadcrumbs(client, indexed, acts):
    html = client.get("/act/SEO0000").text
    assert '"@type":"BreadcrumbList"' in html


@pytest.mark.parametrize("raw,expected", [
    ("Ελλάδα", "GR"), ("EL", "GR"), ("gr", "GR"), ("de", "DE"),
    ("Γερμανία", None), ("Ηνωμένο Βασίλειο", None), ("", None), (None, None),
])
def test_country_is_normalised_or_dropped(raw, expected):
    """schema.org wants an ISO code. A Greek display name in addressCountry
    looks like data and parses as noise, so it is omitted instead."""
    assert seo._country_code(raw) == expected


def test_organization_ld_omits_an_unresolvable_country(indexed):
    class _Req:
        pass
    out = seo.organization_ld(_Req(), name="X", path="/contractor/1",
                              country="Ηνωμένο Βασίλειο")
    assert "addressCountry" not in out


def test_stats_are_hidden_rather_than_shown_as_zero(client, monkeypatch):
    """reltuples is -1 on a never-analysed table and 0 on a fresh database.
    "0 πράξεις" under a headline claiming a unified corpus reads as a broken
    page, so the strip is dropped instead."""
    from app import main as m
    m._stats_cache.update(at=0.0, data=None)
    assert m._public_stats() is None          # the test DB has no acts
    html = client.get("/").text
    assert 'class="pi"' in html and 'class="pi-stats"' not in html
