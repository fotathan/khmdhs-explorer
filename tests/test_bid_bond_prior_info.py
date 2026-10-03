"""Bid bond filter and the «Προηγούμενες Πληροφορίες» document type.

What matters: the bid bond range only ever matches an act that STATES an
amount (unknown is not zero), junk in the box is ignored rather than an error,
a saved search keeps the bounds, and neither the new type nor the bond filter
is offered before the database holds data for it. prior_info never becomes a
public SEO facet (an empty indexable page in production). The ingester side
(bidBond → bid_bond_amount, the label → prior_info) is in test_tsg_ingest.py.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from decimal import Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = "migrations/20261003120000_prior_info_type_bid_bond_index.sql"
SMALL, BIG, NONE, PRIOR = "TSG:930000001", "TSG:930000002", "TSG:930000003", "TSG:930000004"


# --------------------------------------------------------------------------- #
# pure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("typed,amount", [
    ("5000", Decimal("5000")),
    ("5000.50", Decimal("5000.50")),
    ("5.000,50", Decimal("5000.50")),        # Greek grouping
    ("5000,50", Decimal("5000.50")),
    ("10.000", Decimal("10000")),            # Greek thousands, no comma
    ("1.234.567", Decimal("1234567")),
    (" 2 000 € ", Decimal("2000")),
    ("", None),
    (None, None),
    ("πολλά", None),
    ("-5", None),
    ("nan", None),
])
def test_a_typed_amount_is_read_or_ignored(typed, amount):
    from app import main
    assert main._amount(typed) == amount


def test_junk_in_the_bond_box_adds_no_condition():
    from app import main
    assert main.build_where({"bond_min": "abc", "bond_max": ""}) == main.build_where({})


def test_the_new_type_is_labelled_in_both_languages():
    from app import i18n, main
    assert main.TYPE_LABELS["prior_info"] == "Προηγούμενες Πληροφορίες"
    assert i18n.TYPE_LABELS_EN["prior_info"] == "Prior information"


def test_prior_info_is_never_a_public_facet():
    from app import main, seo
    assert "prior_info" not in main.TYPE_FILTER_ORDER
    assert "prior_info" not in seo.facet_values("type")
    assert not seo.is_indexable("/", [("type", "prior_info")])


def test_bond_bounds_are_never_crawled():
    from app import seo
    assert "bond_min" in seo._BLOCKED_PARAMS and "bond_max" in seo._BLOCKED_PARAMS


def test_a_saved_search_keeps_the_bond_bounds():
    import search_profiles as sp
    assert sp.params_from_qs("bond_max=5000&type=prior_info") == {
        "bond_max": "5000", "type": ["prior_info"]}


def test_the_crm_names_the_bond_bounds():
    from app import crm
    assert crm.describe_params({"bond_min": "100"}) == "εγγύηση από: 100"
    assert crm.describe_params({"bond_max": "9"}, lang="en") == "bid bond to: 9"


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
@pytest.fixture()
def acts(db):
    from app import main
    c = db.cursor()
    for adam, atype, bond in ((SMALL, "notice", Decimal("1000.00")),
                              (BIG, "notice", Decimal("50000.00")),
                              (NONE, "notice", None),
                              (PRIOR, "prior_info", None)):
        c.execute("""INSERT INTO proc.procurement_act
                       (adam, type, title, origin, data_source, bid_bond_amount, ingested_at)
                     VALUES (%s, %s, 'Δοκιμή εγγύησης', 'import', 'tsg', %s, now())""",
                  (adam, atype, bond))
    main._lookup_cache.clear()
    yield c
    c.execute("DELETE FROM proc.procurement_act WHERE adam = ANY(%s)", ([SMALL, BIG, NONE, PRIOR],))
    main._lookup_cache.clear()


def _matching(c, params):
    from app import main
    where, args = main.build_where(params)
    c.execute(f"SELECT a.adam FROM proc.procurement_act a WHERE {where} AND a.adam = ANY(%s) "
              "ORDER BY a.adam", args + [[SMALL, BIG, NONE, PRIOR]])
    return [r["adam"] for r in c.fetchall()]


def test_a_bond_bound_matches_only_acts_that_state_one(acts):
    assert _matching(acts, {"bond_max": "5000"}) == [SMALL]         # NONE is unknown, not 0
    assert _matching(acts, {"bond_min": "10.000"}) == [BIG]
    assert _matching(acts, {"bond_min": "1000", "bond_max": "50000"}) == [SMALL, BIG]
    assert _matching(acts, {}) == [SMALL, BIG, NONE, PRIOR]


def test_the_type_filter_reaches_prior_info(acts):
    assert _matching(acts, {"type": ["prior_info"]}) == [PRIOR]


def test_the_lists_offer_the_new_filters_only_once_there_is_data(db):
    from app import main
    main._lookup_cache.clear()
    try:
        built = main._build_lookups()
        assert built["type_options"] == main.TYPE_FILTER_ORDER
        assert built["has_bid_bond"] is False
    finally:
        main._lookup_cache.clear()


def test_the_lists_offer_them_when_an_act_carries_them(acts):
    from app import main
    built = main._build_lookups()
    assert built["type_options"] == main.TYPE_FILTER_ORDER + ["prior_info"]
    assert built["has_bid_bond"] is True


def test_the_search_page_shows_the_new_filters(acts, client):
    r = client.get("/?bond_max=5000")
    assert r.status_code == 200
    assert 'name="bond_max"' in r.text and 'value="prior_info"' in r.text


def test_the_migration_runs_twice():
    for _ in range(2):
        r = subprocess.run(["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1", "-q",
                            "-f", str(ROOT / MIGRATION)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
