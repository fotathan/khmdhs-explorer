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
