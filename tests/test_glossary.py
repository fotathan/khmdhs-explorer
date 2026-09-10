"""The glossary: the page that has to work for someone who knows nothing.

Most of these are content-integrity tests rather than route tests, and that is
deliberate. The glossary is 34 hand-written entries in two languages that link
into a filter vocabulary defined somewhere else entirely; the way it breaks is
not a 500, it is an entry that quietly renders an empty English body, or a
"see it in the data" link that points at a facet nobody kept.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from markupsafe import escape  # the same escaping Jinja applies

import pytest

from app import glossary as gl
from app import seo


# --------------------------------------------------------------------------- #
# Content integrity
# --------------------------------------------------------------------------- #
def test_slugs_are_unique():
    assert len(gl.slugs()) == len(set(gl.slugs()))


def test_every_term_has_both_languages():
    for t in gl.TERMS:
        for lang in ("el", "en"):
            assert t[lang]["term"].strip(), (t["slug"], lang)
            assert t[lang]["short"].strip(), (t["slug"], lang)
            assert t[lang]["body"], (t["slug"], lang)
            assert all(p.strip() for p in t[lang]["body"]), (t["slug"], lang)


def test_english_bodies_are_not_the_greek_ones():
    """A copy-paste that leaves the Greek text in the English field is the
    likeliest way this file rots — it renders fine and reads as a bug only to
    an English speaker."""
    for t in gl.TERMS:
        assert t["en"]["short"] != t["el"]["short"], t["slug"]
        assert t["en"]["body"] != t["el"]["body"], t["slug"]


def test_every_term_belongs_to_a_declared_section():
    known = {s["slug"] for s in gl.SECTIONS}
    for t in gl.TERMS:
        assert t["section"] in known, t["slug"]


def test_every_section_has_terms():
    """An empty section renders as a heading with nothing under it."""
    assert {s["slug"] for s in gl.sections_with_terms()} == {s["slug"] for s in gl.SECTIONS}


def test_related_terms_all_resolve():
    for t in gl.TERMS:
        for slug in t["related"]:
            assert slug in gl.BY_SLUG, (t["slug"], slug)


def test_no_term_relates_to_itself():
    for t in gl.TERMS:
        assert t["slug"] not in t["related"], t["slug"]


def test_see_links_point_at_real_indexable_pages(client, monkeypatch):
    """Each 'see it in the data' link is a promise that the filter still
    exists. A renamed facet value would otherwise land the reader on an
    unfiltered page that looks like it worked.

    Takes `client` because the facet allowlist is registered from the real code
    lists when app.main imports — asserting against an empty registry would
    pass for the wrong reason."""
    monkeypatch.setenv("SEO_INDEX", "1")
    for t in gl.TERMS:
        for link in t["see"]:
            u = urlsplit(link["href"])
            assert u.path in ("/", "/authorities", "/contractors"), link
            assert seo.is_indexable(u.path, parse_qsl(u.query)), link
            assert link["el"].strip() and link["en"].strip(), (t["slug"], link)


def test_lookup_helpers():
    assert gl.get("cpv")["el"]["term"] == "CPV"
    assert gl.get("no-such-term") is None


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #
def test_index_lists_every_term(client):
    r = client.get("/glossary")
    assert r.status_code == 200
    for t in gl.TERMS:
        assert f'/glossary/{t["slug"]}' in r.text, t["slug"]
        # escaped: a term like "ΑΦΜ & ΓΕΜΗ" reaches the page as &amp;
        assert str(escape(t["el"]["term"])) in r.text, t["slug"]


def test_index_is_public(client):
    """No login, no teaser: this is the page a stranger arrives on."""
    r = client.get("/glossary", follow_redirects=False)
    assert r.status_code == 200
    assert "reg-cta" not in r.text


@pytest.mark.parametrize("slug", gl.slugs())
def test_every_term_page_renders(client, slug):
    r = client.get(f"/glossary/{slug}")
    assert r.status_code == 200
    term = gl.get(slug)
    assert str(escape(term["el"]["term"])) in r.text
    assert str(escape(term["el"]["short"])) in r.text
    # the disclaimer is on every page, not left to the reader's good sense
    assert "νομική συμβουλή" in r.text


def test_unknown_term_is_404(client):
    assert client.get("/glossary/not-a-real-term").status_code == 404


def test_term_page_renders_in_english(client):
    client.get("/set-lang?lang=en", follow_redirects=False)
    r = client.get("/glossary/cpv")
    assert r.status_code == 200
    assert "Common Procurement Vocabulary" in r.text
    assert "not legal advice" in r.text


def test_term_page_carries_structured_data(client, monkeypatch):
    monkeypatch.setenv("SEO_INDEX", "1")
    r = client.get("/glossary/adam")
    assert '"@type":"DefinedTerm"' in r.text
    assert '"@type":"BreadcrumbList"' in r.text


def test_term_page_description_is_the_short_definition(client):
    r = client.get("/glossary/kimdis")
    assert gl.get("kimdis")["el"]["short"] in r.text


def test_glossary_is_linked_from_every_page(client):
    """The footer link is what makes the glossary reachable — and crawlable —
    from anywhere on the site."""
    for path in ("/", "/glossary", "/data-sources"):
        assert 'href="/glossary"' in client.get(path).text, path
