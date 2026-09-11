"""Static assets must be cacheable, and must cost nothing to serve.

Four properties, one per symptom that made live /static responses uncacheable
(see app/static_assets.py for why each one happens):

  no Set-Cookie      Cloudflare refuses to cache a response that sets one, so a
                     CSRF token minted on a stylesheet request was enough to
                     make every asset CF-Cache-Status: DYNAMIC
  no Vary: Cookie    splits the edge cache per visitor even without a cookie
  a Cache-Control    StaticFiles sends none, so a warm browser still paid a
                     conditional request per asset
  no DB round trip   AuthMiddleware resolved the session to a live account on
                     every request — once per asset, per page view

The last one is asserted by counting real cursor() calls, not by reading the
middleware: the point of the test is that the short-circuit is still in front
of the lookup after someone edits the middleware.
"""
import pytest

from tests.helpers import grant, login, make_user

ASSET = "/static/vendor/htmx-1.9.12.min.js"


@pytest.fixture()
def reader(client):
    uid = make_user("static_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "static_cust", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# The response headers an edge cache reads
# --------------------------------------------------------------------------- #
def test_an_asset_sets_no_cookie_and_does_not_vary_on_one(client):
    r = client.get(ASSET)
    assert r.status_code == 200
    assert "set-cookie" not in r.headers, r.headers.get("set-cookie")
    assert "cookie" not in r.headers.get("vary", "").lower()


def test_an_asset_sets_no_cookie_for_a_signed_in_reader_either(reader):
    """The session cookie is already on the request here — the middleware must
    still not touch it, or every asset re-issues it."""
    r = reader.get(ASSET)
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert "cookie" not in r.headers.get("vary", "").lower()


def test_an_unstamped_asset_is_cacheable_for_an_hour(client):
    cc = client.get(ASSET).headers["cache-control"]
    assert "public" in cc and "max-age=3600" in cc
    assert "immutable" not in cc


def test_a_stamped_asset_is_immutable_for_a_year(client):
    cc = client.get(f"{ASSET}?v=deadbeef").headers["cache-control"]
    assert "max-age=31536000" in cc and "immutable" in cc


def test_a_conditional_request_still_states_its_freshness(client):
    """A 304 is the browser asking whether its copy is still good; an answer
    without Cache-Control means it asks again on the very next view."""
    first = client.get(f"{ASSET}?v=deadbeef")
    etag = first.headers["etag"]
    again = client.get(f"{ASSET}?v=deadbeef", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert "immutable" in again.headers["cache-control"]
    assert "set-cookie" not in again.headers


def test_the_security_headers_still_apply_to_an_asset(client):
    """Skipping the session must not have skipped the outer hardening."""
    h = client.get(ASSET).headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"


# --------------------------------------------------------------------------- #
# What serving an asset costs
# --------------------------------------------------------------------------- #
def test_an_asset_costs_a_signed_in_reader_no_database_lookup(reader, monkeypatch):
    from app import main as _main
    calls = []
    real = _main.cursor

    def counting_cursor(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(_main, "cursor", counting_cursor)
    assert reader.get(ASSET).status_code == 200
    assert calls == [], f"{len(calls)} DB round trip(s) to serve a static file"
    # ...and the same client on a real page DOES resolve its account, so the
    # assertion above is about the short-circuit, not about a broken fixture.
    calls.clear()
    reader.get("/account")
    assert calls, "the account lookup is gone from ordinary requests too"


# --------------------------------------------------------------------------- #
# The stamp that makes the immutable tier safe
# --------------------------------------------------------------------------- #
def test_pages_emit_stamped_asset_urls(client):
    body = client.get("/").text
    assert "/static/vendor/htmx-1.9.12.min.js?v=" in body
    assert "/static/fonts/fira.css?v=" in body


def test_the_stamp_is_the_commit_in_production(monkeypatch, tmp_path):
    """A deploy must change every asset URL, or the year-long tier serves the
    previous build's stylesheet."""
    import importlib
    from app import static_assets
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef0123")
    mod = importlib.reload(static_assets)
    try:
        asset = mod.make_asset_url(str(tmp_path))
        assert asset("/static/css/x.css") == "/static/css/x.css?v=0123456789ab"
    finally:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
        importlib.reload(static_assets)


def test_an_unstampable_path_is_returned_untouched(tmp_path):
    """No file, no stamp — and the URL still has to work, in the one-hour tier."""
    from app import static_assets
    asset = static_assets.make_asset_url(str(tmp_path))
    assert asset("/static/nope.css") == "/static/nope.css"
    assert asset("https://example.org/x.css") == "https://example.org/x.css"


def test_the_stamp_follows_a_local_edit(tmp_path):
    """In development the stamp is the file's mtime, so a saved CSS edit is
    visible on the next reload without restarting the server."""
    import os
    from app import static_assets
    f = tmp_path / "x.css"
    f.write_text("a{}")
    asset = static_assets.make_asset_url(str(tmp_path))
    before = asset("/static/x.css")
    os.utime(f, (0, 0))
    assert asset("/static/x.css") != before


def test_the_stamp_cannot_be_walked_out_of_the_static_tree(tmp_path):
    from app import static_assets
    asset = static_assets.make_asset_url(str(tmp_path))
    assert asset("/static/../../etc/passwd") == "/static/../../etc/passwd"
