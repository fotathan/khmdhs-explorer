# -*- coding: utf-8 -*-
"""The search URL must not grow every time you turn a page.

The bug this file pins down: each pager link spelled the whole current
querystring into its `hx-get` AND carried `hx-include="#filters"`. The pager
anchors sit inside `<form id="filters">`, which holds those same filters as
inputs, so htmx appended a second copy of every one of them; the form's
`hx-push-url="true"` then wrote the doubled querystring into the address bar,
and the NEXT pager link was built from that. Six page clicks, six copies of
`?q=…&cpv=…` — which `match_qs` faithfully carried onto every act link, where
each copy became its own "Γιατί ταιριάζει" chip.

Three guards, one per layer: the templates must not pair the two attributes
again, a querystring that already carries repeats must not propagate them, and
a repeated filter value must not reach the SQL twice.
"""
import pathlib
import re

from starlette.datastructures import QueryParams

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"

# Every partial that renders a pager whose hx-get spells out the current
# querystring. Adding one without adding it here is the only way past this test.
PAGER_TEMPLATES = ("beta_results.html", "_results.html",
                   "beta_authorities_results.html", "beta_contractors_results.html")

# The anchor tag itself, attributes and all — comments explaining the rule are
# allowed to name hx-include, the markup is not.
_ANCHOR = re.compile(r"<a\b[^>]*>", re.S)


def _pager_anchors(name: str) -> list[str]:
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    return [a for a in _ANCHOR.findall(html) if "multi_items()" in a]


def test_pagers_that_spell_out_the_url_do_not_also_include_the_form():
    for name in PAGER_TEMPLATES:
        anchors = _pager_anchors(name)
        assert anchors, f"{name}: no pager anchor found — did the markup move?"
        for a in anchors:
            assert "hx-include" not in a, (
                f"{name}: this pager already carries every filter in its URL; "
                f"hx-include would send a second copy of each and hx-push-url "
                f"would write the doubled querystring to the address bar.\n{a}")


def test_match_qs_drops_exact_repeats():
    """A URL that already grew must not pass its duplicates on to the act link."""
    from app import main

    class _Req:
        pass

    r = _Req()
    r.query_params = QueryParams("q=φαρμακα&cpv=336&q=φαρμακα&cpv=336&q=φαρμακα&cpv=336")
    assert main.match_qs(r) == "?q=%CF%86%CE%B1%CF%81%CE%BC%CE%B1%CE%BA%CE%B1&cpv=336"


def test_match_qs_keeps_genuinely_different_values():
    """CPV is multi-valued: two different codes are two filters, not a repeat."""
    from app import main

    class _Req:
        pass

    r = _Req()
    r.query_params = QueryParams("q=a&cpv=336&cpv=451")
    assert main.match_qs(r) == "?q=a&cpv=336&cpv=451"


def test_match_qs_from_params_drops_repeats_too():
    """The digest results page builds the same link from a stored filter set."""
    from app import main
    assert (main.match_qs_from_params({"q": "a", "cpv": ["336", "336", "451"]})
            == "?q=a&cpv=336&cpv=451")


def test_a_repeated_filter_value_reaches_the_sql_once():
    """`cpv=336&cpv=336` is one filter written twice — one LIKE, one argument."""
    from app import main
    sql, args = main.build_where({"cpv": ["336", "336", "336"]})
    assert sql.count("oc.cpv_code LIKE") == 1
    assert args == ["336%"]


def test_distinct_filter_values_are_all_kept():
    from app import main
    sql, args = main.build_where({"cpv": ["336", "451"]})
    assert sql.count("oc.cpv_code LIKE") == 2
    assert args == ["336%", "451%"]
