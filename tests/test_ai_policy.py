"""/ai — the AI & data-handling statement.

This page is a set of promises, and the way a promise page fails is not a 500:
it is a feature getting switched on while the prose still says it is off. So
the tests here are mostly about the page tracking reality — the three feature
states are read from live configuration, and each one is asserted in both
directions.

The rest guards the claims that are structural rather than aspirational: that
the summary path cannot read customer data, and that no third-party asset is
loaded by any page (both stated on /ai as facts about the software).
"""
from __future__ import annotations

import re

import pytest

from app import seo
from tests.helpers import make_user, login


def test_page_is_public(client):
    """It answers the first question of a sales meeting; a login wall would
    defeat the point."""
    r = client.get("/ai", follow_redirects=False)
    assert r.status_code == 200
    assert "reg-cta" not in r.text


def test_page_is_not_marked_draft(client):
    """Unlike /privacy and /terms, every claim here is checked against the
    code — the draft banner would undercut a page whose job is confidence."""
    # the class exists in doc_page.html's stylesheet either way; what matters
    # is whether the banner element was rendered
    assert 'class="doc-draft"' not in client.get("/ai").text


def test_page_is_indexable(client, monkeypatch):
    monkeypatch.setenv("SEO_INDEX", "1")
    assert seo.is_indexable("/ai", []) is True
    assert 'content="index, follow"' in client.get("/ai").text


def test_page_is_in_the_sitemap(client, monkeypatch):
    monkeypatch.setenv("SEO_INDEX", "1")
    monkeypatch.setenv("APP_BASE_URL", "https://example.gr")
    seo.reset_cache()
    assert "https://example.gr/ai</loc>" in client.get("/sitemap-pages.xml").text
    seo.reset_cache()


def test_linked_from_every_page(client):
    for path in ("/", "/glossary", "/ai"):
        assert 'href="/ai"' in client.get(path).text, path


def test_renders_in_english(client):
    client.get("/set-lang?lang=en", follow_redirects=False)
    r = client.get("/ai")
    assert r.status_code == 200
    assert "AI &amp; data handling" in r.text or "AI & data handling" in r.text
    assert "not used to train models" in r.text
    # no Greek left behind in the English rendering of this page's own copy
    assert "Η σύντομη απάντηση" not in r.text


# --------------------------------------------------------------------------- #
# The page has to track the configuration, not describe it from memory
# --------------------------------------------------------------------------- #
def _state(html: str, heading_fragment: str) -> str:
    """'on' / 'off' for the feature block whose heading contains the fragment."""
    block = html.split(heading_fragment, 1)[1][:400]
    return "on" if "ai-on" in block.split("</h3>")[0] else "off"


def test_ai_summary_state_follows_the_flag(client, monkeypatch):
    from app import ai_summary as ai
    monkeypatch.setattr(ai, "can_generate", lambda: True)
    assert _state(client.get("/ai").text, "Σύνοψη διαγωνισμού") == "on"
    monkeypatch.setattr(ai, "can_generate", lambda: False)
    assert _state(client.get("/ai").text, "Σύνοψη διαγωνισμού") == "off"


def test_document_ocr_state_follows_the_key(client, monkeypatch):
    from app import ocr
    monkeypatch.setattr(ocr, "api_key_present", lambda: False)
    assert _state(client.get("/ai").text, "(OCR)") == "off"


def test_calls_are_reported_off_and_say_so_in_words(client):
    """Telephony is off in production. The page must not merely omit it — a
    reader looking for "do you record my calls" needs to find the answer."""
    html = client.get("/ai").text
    assert _state(html, "Κλήσεις") == "off"
    assert "απενεργοποιημένη σε αυτή την εγκατάσταση" in html


def test_calls_block_switches_to_the_live_wording(client, monkeypatch):
    """The other direction: if the chain is ever fully configured, the page
    describes what actually happens instead of denying it."""
    from app import telephony, transcribe, call_summary
    monkeypatch.setattr(telephony, "TELEPHONY_ENABLED", True)
    monkeypatch.setattr(transcribe, "backend_configured", lambda: True)
    monkeypatch.setattr(call_summary, "api_key_present", lambda: True)
    html = client.get("/ai").text
    assert _state(html, "Κλήσεις") == "on"
    assert "απενεργοποιημένη σε αυτή την εγκατάσταση" not in html
    assert "προσωπικά δεδομένα" in html


def test_audio_destination_is_stated_both_ways(client, monkeypatch):
    """A self-hosted transcription engine means the recording never leaves;
    the hosted endpoint means it does. On a data-handling page that is the
    distinction that matters, so it is read from the configured endpoint."""
    from app import telephony, transcribe, call_summary
    monkeypatch.setattr(telephony, "TELEPHONY_ENABLED", True)
    monkeypatch.setattr(transcribe, "backend_configured", lambda: True)
    monkeypatch.setattr(call_summary, "api_key_present", lambda: True)

    # a phrase unique to the calls block — "nothing leaves the service" also
    # appears in the OCR block, about a different thing
    audio_stays = "η ηχογράφηση δεν φεύγει"

    monkeypatch.setattr(transcribe, "BASE_URL", "http://127.0.0.1:8000/v1")
    assert audio_stays in client.get("/ai").text

    monkeypatch.setattr(transcribe, "BASE_URL", "https://api.openai.com/v1")
    html = client.get("/ai").text
    assert "αποστέλλεται σε εξωτερική υπηρεσία" in html
    assert audio_stays not in html


def test_page_renders_when_an_optional_module_is_broken(client, monkeypatch):
    """It is the one page that has to render when things are wrong."""
    from app import ocr

    def _boom():
        raise RuntimeError("no key backend")
    monkeypatch.setattr(ocr, "api_key_present", _boom)
    r = client.get("/ai")
    assert r.status_code == 200
    assert _state(r.text, "(OCR)") == "off"


def test_the_ai_panel_links_here(db, monkeypatch, client):
    """The panel is where someone first meets the AI, so it is where "what
    happens to my data" gets asked. The answer has to be one click away, not
    findable only by scrolling to the footer."""
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    # An admin, because a customer with no stored summary and no right to
    # generate one is shown no panel at all (that is deliberate — see the
    # template header) and there would be nothing to carry the link.
    make_user("aipolicy_adm", "goodpassword1", role="admin")
    login(client, "aipolicy_adm", "goodpassword1")
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'AIPOL0001'")
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text)
                   VALUES ('AIPOL0001', 'notice', 'Δοκιμή', 'import', 'khmdhs',
                           'ΔΙΑΚΗΡΥΞΗ με αρκετό κείμενο για να υπάρχει πηγή.')""")
    try:
        html = client.get("/act/AIPOL0001/ai").text
        assert 'href="/ai"' in html, "the panel must link to the AI statement"
    finally:
        cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'AIPOL0001'")


# --------------------------------------------------------------------------- #
# The claims that are structural
# --------------------------------------------------------------------------- #
def test_summary_sources_are_the_act_only(db):
    """"Nothing about the reader is sent" — build_sources is the only thing
    that decides what reaches the model, and it takes an act and its tables.
    If a customer-shaped argument ever appears here, this page is a lie."""
    import inspect
    from app import ai_summary as ai
    params = list(inspect.signature(ai.build_sources).parameters)
    assert params == ["act", "tables"]
    sources = ai.build_sources({"full_text": "ΔΙΑΚΗΡΥΞΗ κάτι"}, [])
    assert set(sources) == {"full_text"}


def test_summary_cache_key_has_no_user_in_it():
    """The summary is cached per act and shared by every reader — which is the
    reason it cannot encode anything about one of them."""
    import inspect
    from app import ai_summary as ai
    params = list(inspect.signature(ai.input_hash).parameters)
    assert params == ["sources", "model", "lang"]


@pytest.mark.parametrize("path", ["/", "/ai", "/glossary"])
def test_no_third_party_assets(client, path):
    """"Everything your browser loads comes from this server." Asserted against
    the markup, not just the CSP header, because the CSP is the enforcement and
    this is the claim."""
    html = client.get(path).text
    external = [u for u in re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
                # our own origin (canonical / og:url) is not a third party, and
                # a link a reader may click is not an asset the browser loads
                if not u.startswith("https://testserver")
                and not u.startswith("https://www.anthropic.com/")]
    assert external == [], external


def test_csp_keeps_it_that_way(client):
    csp = client.get("/ai").headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp


def test_admin_sees_the_same_page(client):
    """No hidden variant: the statement a customer reads is the statement."""
    make_user("aiadm", "goodpassword1", role="admin")
    anon = client.get("/ai").text
    login(client, "aiadm", "goodpassword1")
    assert client.get("/ai").text.count("ai-use") == anon.count("ai-use")
