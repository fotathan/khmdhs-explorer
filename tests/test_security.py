"""Security response headers and the per-IP rate limit."""


def test_security_headers_present(client):
    r = client.get("/")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy")
    assert "content-security-policy" in r.headers
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # HTMX's hx-vals='js:{…}' (CPV/NUTS typeahead, admin-acts widgets) needs
    # 'unsafe-eval' in script-src; without it those requests never fire. Guard
    # against a well-meaning CSP tightening silently re-breaking them.
    assert "'unsafe-eval'" in csp


def test_rate_limit_returns_429(client, monkeypatch):
    import app.main as m
    # Turn the limiter on with a tiny budget for this test only.
    monkeypatch.setattr(m, "_RL_ENABLED", True)
    monkeypatch.setattr(m, "_RL_PER_MIN", 5)
    m._rl_hits.clear()
    codes = [client.get("/explore", follow_redirects=False).status_code for _ in range(7)]
    # First 5 are allowed (not 429); the 6th trips the limit.
    assert 429 not in codes[:5]
    assert codes[5] == 429


def test_search_route_is_rate_limited(client, monkeypatch):
    """The main search endpoint (/) runs live queries — it must be limited too."""
    import app.main as m
    monkeypatch.setattr(m, "_RL_ENABLED", True)
    monkeypatch.setattr(m, "_RL_SEARCH_PER_MIN", 5)
    m._rl_hits.clear()
    codes = [client.get("/", follow_redirects=False).status_code for _ in range(7)]
    assert 429 not in codes[:5]
    assert codes[5] == 429


def test_client_ip_prefers_forwarded_for():
    """Behind a proxy the socket peer is the load balancer; the real client is the
    first X-Forwarded-For hop, so rate-limits key per visitor not per proxy."""
    from app.main import _client_ip

    class _Req:
        def __init__(self, xff=None, peer=None):
            self.headers = {"x-forwarded-for": xff} if xff else {}
            self.client = type("C", (), {"host": peer})() if peer else None

    assert _client_ip(_Req(xff="203.0.113.7, 10.0.0.1", peer="10.0.0.1")) == "203.0.113.7"
    assert _client_ip(_Req(peer="9.9.9.9")) == "9.9.9.9"      # no XFF → socket peer
    assert _client_ip(_Req()) == "?"
