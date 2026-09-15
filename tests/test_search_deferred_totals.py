# -*- coding: utf-8 -*-
"""The search page renders its rows before its headline.

The count and value over the whole matching set were most of a search's cost
on a broad keyword (0.5s in production, up to 2.4s on the 2.9M-act local
corpus) while the page of rows took tens of ms. What these tests pin:

  * the HTML page and the HTMX partial issue NO whole-set aggregate, and mount
    /search/totals instead; the JSON shape still carries the totals inline;
  * /search/totals reports the right figures, for the filter on screen, and
    only applies them while its `seq` still names the results being shown;
  * the pager no longer needs the count to know there is a next page;
  * the filter lists are served stale-while-rebuilding once past their TTL,
    and the totals cache now lives ten minutes.
"""
import re
import time

import pytest

from tests.helpers import grant, login, make_user


@pytest.fixture()
def acts(db):
    """Two contracts and one notice (values that make a wrong total obvious)."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DEF-%'")
    for adam, atype, val in (("DEF-0001", "contract", 1000),
                             ("DEF-0002", "contract", 2000),
                             ("DEF-0003", "notice", 400000)):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, total_cost_with_vat)
                       VALUES (%s, %s, 'Δοκιμή αναβολής', 'import', 'khmdhs', %s)""",
                    (adam, atype, val))
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'DEF-%'")


@pytest.fixture()
def reader(client):
    uid = make_user("deferred_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "deferred_cust", "goodpassword1")
    return client


@pytest.fixture()
def fresh_totals(monkeypatch):
    from app import main as _main
    monkeypatch.setattr(_main, "_totals_cache", type(_main._totals_cache)())


def _aggregates(client, monkeypatch, url, **kw):
    """The whole-set count/sum statements one request sends."""
    import psycopg
    seen = []
    real = psycopg.Cursor.execute

    def spy(self, query, params=None, **k):
        q = str(query)
        if "sum(a.total_cost_with_vat)" in q or "sum(total_cost_with_vat)" in q:
            seen.append(q)
        return real(self, query, params, **k)

    # Restore ONLY the spy. monkeypatch.undo() would also revert the
    # fresh_totals fixture, swapping the cache between two requests — which is
    # exactly the thing a cache-sharing test must not do.
    psycopg.Cursor.execute = spy
    try:
        r = client.get(url, **kw)
    finally:
        psycopg.Cursor.execute = real
    return r, seen


# --------------------------------------------------------------------------- #
# Rows first
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("headers", [{}, {"HX-Request": "true"}], ids=["page", "partial"])
def test_the_html_search_runs_no_whole_set_aggregate(reader, acts, fresh_totals, monkeypatch, headers):
    r, seen = _aggregates(reader, monkeypatch, "/?type=contract", headers=headers)
    assert r.status_code == 200
    assert seen == [], "the page still waits for the count and value"
    assert 'id="totals-mount"' in r.text
    assert "/search/totals?type=contract&amp;per_page=10" in r.text
    assert "DEF-0001" in r.text                       # the rows are there at once


def test_the_totals_mount_does_not_inherit_the_filter_forms_target(reader, acts):
    """The mount lives inside <form id="filters" hx-target="#results"
    hx-push-url="true">. Inherited, the totals script replaced the whole result
    list and pushed /search/totals into the address bar (seen in a browser)."""
    body = reader.get("/?type=contract").text
    mount = re.search(r'<div id="totals-mount"[^>]*>', body, re.S)
    assert mount
    assert 'hx-target="this"' in mount.group(0)
    assert 'hx-push-url="false"' in mount.group(0)


def test_the_json_shape_still_carries_the_totals(reader, acts, fresh_totals):
    r = reader.get("/?type=contract", headers={"accept": "application/json"}).json()
    assert r["total_count"] == 2 and r["total_value"] == 3000.0


# --------------------------------------------------------------------------- #
# The headline, afterwards
# --------------------------------------------------------------------------- #
def test_the_totals_route_reports_the_filter_on_screen(reader, acts, fresh_totals):
    body = reader.get("/search/totals?type=contract&per_page=1&seq=abcd1234").text
    assert "window.__searchSeq !== \"abcd1234\"" in body
    assert 'var count = "2"' in body
    assert "3.000" in body
    assert 'set(\'pager-total\', "2")' in body         # 2 contracts, 1 per page


def test_a_different_filter_gets_its_own_totals(reader, acts, fresh_totals):
    body = reader.get("/search/totals?type=notice&seq=x").text
    assert 'var count = "1"' in body and "400.000" in body


def test_the_seq_ties_the_page_to_its_own_totals_request(reader, acts, fresh_totals):
    """The partial stamps a seq and asks for totals under that same seq."""
    body = reader.get("/?type=contract", headers={"HX-Request": "true"}).text
    stamped = re.search(r'window.__searchSeq = "([0-9a-f]+)"', body)
    assert stamped, "the results partial does not stamp a seq"
    assert f"seq={stamped.group(1)}" in body


def test_paging_and_the_totals_share_one_cache_entry(reader, acts, fresh_totals, monkeypatch):
    _, first = _aggregates(reader, monkeypatch, "/search/totals?type=contract&seq=a")
    _, again = _aggregates(reader, monkeypatch, "/search/totals?type=contract&page=3&seq=b")
    assert first and again == []


# --------------------------------------------------------------------------- #
# The pager without the count
# --------------------------------------------------------------------------- #
def test_the_pager_knows_a_next_page_exists_without_counting(reader, acts):
    first = reader.get("/?type=contract&per_page=1").text
    assert "page=2" in first
    last = reader.get("/?type=contract&per_page=1&page=2").text
    assert "page=3" not in last
    assert 'id="pager-total"' in first


# --------------------------------------------------------------------------- #
# Caches
# --------------------------------------------------------------------------- #
def test_the_totals_cache_now_lives_ten_minutes():
    import os
    from app import main as _main
    if "SEARCH_TOTALS_TTL_SECONDS" not in os.environ:
        assert _main._TOTALS_TTL_S == 600


def test_stale_filter_lists_are_served_while_rebuilding(monkeypatch):
    """Past the TTL, lookups() returns the old lists at once and rebuilds once
    in the background; it never blocks the request on the rebuild."""
    from app import main as _main
    calls = []

    def slow_build():
        calls.append(time.monotonic())
        time.sleep(0.3)
        return {"authorities": ["new"]}

    monkeypatch.setattr(_main, "_build_lookups", slow_build)
    _main._lookup_cache.clear()
    _main._lookup_cache.update({"authorities": ["old"]})
    monkeypatch.setattr(_main, "_lookup_built_at", time.monotonic() - 10_000)
    monkeypatch.setattr(_main, "_lookup_refreshing", False)
    try:
        t0 = time.monotonic()
        served = _main.lookups()
        assert time.monotonic() - t0 < 0.2, "the request waited for the rebuild"
        assert served["authorities"] == ["old"]
        _main.lookups()                               # already rebuilding: no second thread
        for _ in range(40):
            if _main._lookup_cache["authorities"] == ["new"]:
                break
            time.sleep(0.02)
        assert _main._lookup_cache["authorities"] == ["new"]
        assert len(calls) == 1
    finally:
        _main._lookup_cache.clear()                   # the next caller rebuilds for real


def test_the_first_call_still_builds_synchronously(monkeypatch):
    from app import main as _main
    monkeypatch.setattr(_main, "_build_lookups", lambda: {"authorities": ["built"]})
    _main._lookup_cache.clear()
    try:
        assert _main.lookups()["authorities"] == ["built"]
    finally:
        _main._lookup_cache.clear()
