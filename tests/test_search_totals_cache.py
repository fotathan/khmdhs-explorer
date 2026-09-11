"""The search result headline: an exact count and a summed value over the WHOLE
matching set, not just the page on screen.

Measured on the 2.9M-row local corpus (production is ~62k today, so this is the
direction of travel): the unfiltered sum is 556ms, a filtered count+sum is
163-324ms, and the landing page pays it on every load. Worse, the totals do not
depend on LIMIT/OFFSET — so paging through one filtered result set recomputes
the identical aggregate for page 2, 3, 4.

app.main._search_totals memoises them per filter. What the tests below pin:

  * the same filter reuses the answer, and PAGING does not recompute it;
  * a DIFFERENT filter is never served another filter's numbers (a cache that
    confuses two filters reports the wrong money, which is worse than slow);
  * the rows themselves are never cached — a new act shows up immediately even
    while the headline is still inside its TTL;
  * TTL 0 disables it, and an expired entry recomputes.
"""
import time

import pytest

from tests.helpers import grant, login, make_user


@pytest.fixture()
def acts(db):
    """Two contracts and one notice, with values that make a wrong total
    obvious rather than plausible."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'TOT%'")
    for adam, atype, val in (("TOT-0001", "contract", 1000),
                             ("TOT-0002", "contract", 2000),
                             ("TOT-0003", "notice", 400000)):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, total_cost_with_vat)
                       VALUES (%s, %s, 'Δοκιμή', 'import', 'khmdhs', %s)""",
                    (adam, atype, val))
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'TOT%'")


def _totals(params, limit=10, offset=0):
    from app.main import run_search
    _rows, agg = run_search(params, limit, offset)
    return agg


@pytest.fixture()
def aggregate_queries(monkeypatch):
    """Count the count/sum statements actually sent to Postgres."""
    import psycopg
    seen = []
    real = psycopg.Cursor.execute

    def spy(self, query, params=None, **kw):
        q = str(query)
        if "sum(a.total_cost_with_vat)" in q or "sum(total_cost_with_vat)" in q:
            seen.append(q)
        return real(self, query, params, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", spy)
    return seen


# --------------------------------------------------------------------------- #
# It caches
# --------------------------------------------------------------------------- #
def test_the_same_filter_is_only_totalled_once(acts, aggregate_queries):
    first = _totals({"type": "contract"})
    aggregate_queries.clear()
    second = _totals({"type": "contract"})
    assert aggregate_queries == [], "the aggregate ran again for the same filter"
    assert first == second


def test_paging_does_not_recompute_the_totals(acts, aggregate_queries):
    """The whole point: totals do not depend on LIMIT/OFFSET."""
    page1 = _totals({"type": "contract"}, limit=1, offset=0)
    aggregate_queries.clear()
    page2 = _totals({"type": "contract"}, limit=1, offset=1)
    page3 = _totals({"type": "contract"}, limit=1, offset=2)
    assert aggregate_queries == []
    assert page1["n"] == page2["n"] == page3["n"] == 2
    assert page1["total_value"] == page2["total_value"]


# --------------------------------------------------------------------------- #
# It does not confuse two filters — the failure that would matter
# --------------------------------------------------------------------------- #
def test_a_different_filter_gets_its_own_numbers(acts):
    contracts = _totals({"type": "contract"})
    notices = _totals({"type": "notice"})
    assert contracts["n"] == 2 and contracts["total_value"] == 3000
    assert notices["n"] == 1 and notices["total_value"] == 400000


def test_the_unfiltered_headline_is_not_a_filtered_one(acts):
    """TRUE takes the reltuples branch; a filtered key must never reach it."""
    everything = _totals({})
    contracts = _totals({"type": "contract"})
    assert everything["n"] != contracts["n"] or everything["total_value"] != 3000


def test_value_bounds_are_part_of_the_key(acts):
    cheap = _totals({"value_max": 1500})
    dear = _totals({"value_min": 1500})
    assert cheap["total_value"] == 1000
    assert dear["total_value"] == 402000


# --------------------------------------------------------------------------- #
# The rows are never cached
# --------------------------------------------------------------------------- #
def test_a_new_act_appears_in_the_rows_while_the_headline_is_still_warm(acts, db):
    from app.main import run_search
    rows, _agg = run_search({"type": "contract"}, 10, 0)
    assert len(rows) == 2
    db.cursor().execute("""INSERT INTO proc.procurement_act
                             (adam, type, title, origin, data_source, total_cost_with_vat)
                           VALUES ('TOT-0004','contract','Δοκιμή','import','khmdhs', 5000)""")
    rows2, _agg2 = run_search({"type": "contract"}, 10, 0)
    assert len(rows2) == 3, "the result rows were served from a cache"


# --------------------------------------------------------------------------- #
# Staleness is bounded, and switchable
# --------------------------------------------------------------------------- #
def test_an_expired_entry_is_recomputed(acts, db, monkeypatch, aggregate_queries):
    from app import main as _main
    monkeypatch.setattr(_main, "_TOTALS_TTL_S", 0.05)
    before = _totals({"type": "contract"})
    db.cursor().execute("""INSERT INTO proc.procurement_act
                             (adam, type, title, origin, data_source, total_cost_with_vat)
                           VALUES ('TOT-0005','contract','Δοκιμή','import','khmdhs', 7000)""")
    time.sleep(0.06)
    aggregate_queries.clear()
    after = _totals({"type": "contract"})
    assert aggregate_queries, "an expired entry was served anyway"
    assert after["total_value"] == before["total_value"] + 7000


def test_ttl_zero_turns_the_cache_off(acts, monkeypatch, aggregate_queries):
    from app import main as _main
    monkeypatch.setattr(_main, "_TOTALS_TTL_S", 0.0)
    _totals({"type": "contract"})
    aggregate_queries.clear()
    _totals({"type": "contract"})
    assert aggregate_queries, "TTL 0 still served a cached total"


def test_the_cache_cannot_grow_without_bound(acts, monkeypatch):
    from app import main as _main
    monkeypatch.setattr(_main, "_TOTALS_CACHE_MAX", 4)
    for i in range(12):
        _totals({"value_min": i})
    assert len(_main._totals_cache) <= 4


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #
def test_the_search_page_still_reports_the_right_figures(client, acts):
    uid = make_user("totals_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "totals_cust", "goodpassword1")
    body = client.get("/?type=contract").text
    assert "2" in body
    r = client.get("/?type=contract", headers={"accept": "application/json"}).json()
    assert r["total_count"] == 2 and r["total_value"] == 3000.0


def test_the_filter_form_cancels_a_superseded_request(client):
    """Changing a second filter mid-flight must abort the first, not race it."""
    body = client.get("/").text
    assert 'hx-sync="this:replace"' in body
