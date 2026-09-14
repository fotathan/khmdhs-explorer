"""The act page's sections: tabs on a desktop, an accordion on a phone.

What is pinned here, and why each one would fail silently otherwise:

  SECTIONS  every section is in the HTML, in tab order, each wired to its tab
            button. A section with no button is unreachable once folded.
  NO-JS     nothing is folded by markup alone — the CSS only hides a section
            under `js-tabs`, which act_page.js sets. Hide by default and a
            browser without scripts gets a blank page.
  TEASER    a gated visitor sees what they saw before this layout existed:
            the hero's amounts and dates and a register prompt. No sections,
            no action buttons, and no fact the hero did not already show
            (place of performance is subscriber-only).
  JUMPS     search-hit chips and AI citations land inside the full text,
            which may be a hidden tab. Both scripts reveal the section BEFORE
            scrolling, or the jump goes nowhere.
  URL       the open tab lives in the #hash. A ?tab= would mint a second,
            non-canonical URL per act (app/seo.py).
"""
import pathlib
import re

import pytest

from tests.helpers import grant, login, make_user

ADAM = "TEST-TABS-0001"
NUTS = "ZZ9T"
NUTS_LABEL = "Περιοχή δοκιμής καρτελών"

SECTIONS = ["overview", "items", "fulltext", "market", "linked"]


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_object_detail WHERE adam = %s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))
    cur.execute("DELETE FROM proc.nuts_code WHERE nuts_code = %s", (NUTS,))


@pytest.fixture()
def act(db):
    """An open notice with full text, a region and a deadline three days out."""
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("INSERT INTO proc.nuts_code (nuts_code, label) VALUES (%s, %s)",
                (NUTS, NUTS_LABEL))
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text,
                      total_cost_with_vat, nuts_code, submission_date,
                      final_submission_date)
                   VALUES (%s, 'notice', 'Προμήθεια καρτελών', 'import', 'khmdhs',
                           'Κείμενο διακήρυξης.', 25000, %s, now(),
                           now() + interval '3 days')""", (ADAM, NUTS))
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES (%s, 'είδος καρτέλας')""", (ADAM,))
    yield ADAM
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    uid = make_user("tabs_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "tabs_cust", "goodpassword1")
    return client


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
def test_every_section_has_a_tab_and_they_share_one_order(reader, act):
    body = reader.get(f"/act/{act}").text
    assert 'id="act-tabs"' in body and 'role="tablist"' in body
    bar = body[body.index('id="act-tabs"'):]
    bar = bar[: bar.index("</div>")]
    tab_order = re.findall(r'data-tab="([a-z]+)" aria-controls="tab-\1"', bar)
    assert tab_order[: len(SECTIONS)] == SECTIONS

    positions = [body.index(f'<section class="apanel{" on" if k == "overview" else ""}" id="tab-{k}"')
                 for k in SECTIONS]
    assert positions == sorted(positions), "section order drifted from tab order"


def test_the_first_section_is_marked_open_in_the_markup(reader, act):
    body = reader.get(f"/act/{act}").text
    assert '<section class="apanel on" id="tab-overview"' in body
    assert body.count('class="apanel on"') == 1


def test_no_full_text_means_no_full_text_tab(db, reader, act):
    db.cursor().execute("UPDATE proc.procurement_act SET full_text = NULL WHERE adam = %s",
                        (act,))
    body = reader.get(f"/act/{act}").text
    assert 'id="tab-fulltext"' not in body
    assert 'data-tab="fulltext"' not in body


def test_each_section_carries_its_phone_header(reader, act):
    body = reader.get(f"/act/{act}").text
    assert body.count('class="apanel-toggle"') == body.count('<section class="apanel')


def test_the_lazy_sections_may_remove_themselves(reader, act):
    body = reader.get(f"/act/{act}").text
    market = body[body.rindex("<section", 0, body.index('id="tab-market"')):]
    assert "data-autohide" in market[: market.index(">")]
    assert "/act/%s/top-contractors" % act in market[: market.index("</section>")]


def test_the_ai_summary_is_its_own_tab_right_after_the_overview(reader, act, monkeypatch):
    from app.main import templates
    monkeypatch.setitem(templates.env.globals, "ai_summary_enabled", True)
    body = reader.get(f"/act/{act}").text
    bar = body[body.index('id="act-tabs"'):]
    bar = bar[: bar.index("</div>")]
    order = re.findall(r'data-tab="([a-z]+)" aria-controls', bar)
    assert order[:3] == ["overview", "ai", "items"]

    section = body[body.rindex("<section", 0, body.index('id="tab-ai"')):]
    assert "data-autohide" in section[: section.index(">")]
    section = section[: section.index("</section>")]
    assert 'id="ai-summary-mount"' in section
    overview = body[body.index('id="tab-overview"'):body.index('id="tab-ai"')]
    assert "ai-summary-mount" not in overview


def test_no_ai_tab_when_the_feature_is_off_or_the_act_is_not_a_notice(db, reader, act, monkeypatch):
    from app.main import templates
    monkeypatch.setitem(templates.env.globals, "ai_summary_enabled", False)
    assert 'data-tab="ai"' not in reader.get(f"/act/{act}").text
    monkeypatch.setitem(templates.env.globals, "ai_summary_enabled", True)
    db.cursor().execute("UPDATE proc.procurement_act SET type = 'contract' WHERE adam = %s",
                        (act,))
    assert 'data-tab="ai"' not in reader.get(f"/act/{act}").text


# --------------------------------------------------------------------------- #
# No-JS: folding is the script's job, never the markup's
# --------------------------------------------------------------------------- #
def test_sections_are_only_folded_once_the_script_says_so():
    css = pathlib.Path("app/templates/beta_base.html").read_text(encoding="utf-8")
    assert ".js-tabs .apanel{display:none;}" in css
    assert re.search(r"(?<!js-tabs )\.apanel\{display:none", css) is None
    assert ".atabs{display:none;}" in css and ".js-tabs .atabs{display:flex;" in css
    js = pathlib.Path("app/static/js/act_page.js").read_text(encoding="utf-8")
    assert "classList.add('js-tabs')" in js


def test_the_phone_layout_is_an_accordion():
    css = pathlib.Path("app/templates/beta_base.html").read_text(encoding="utf-8")
    phone = css[css.index("@media(max-width:760px)"):]
    assert ".js-tabs .atabs{display:none;}" in phone
    assert ".js-tabs .apanel.open .apanel-body{display:block;}" in phone


def test_the_page_loads_its_script(reader, act):
    assert "/static/js/act_page.js" in reader.get(f"/act/{act}").text


# --------------------------------------------------------------------------- #
# Teaser: a gated visitor sees no more than before
# --------------------------------------------------------------------------- #
def test_a_gated_visitor_gets_no_sections_and_no_actions(client, act):
    body = client.get(f"/act/{act}").text
    assert "reg-cta" in body, "the register prompt is gone"
    assert 'id="act-tabs"' not in body
    assert 'class="apanel' not in body
    assert 'class="act-actions"' not in body
    assert "είδος καρτέλας" not in body


def test_a_gated_visitor_does_not_get_the_place_of_performance(client, reader, act):
    assert NUTS_LABEL in reader.get(f"/act/{act}").text, "the subscriber lost the region"
    client.cookies.clear()
    assert NUTS_LABEL not in client.get(f"/act/{act}").text


def test_a_gated_visitor_keeps_the_deadline_they_always_had(client, act):
    body = client.get(f"/act/{act}").text
    assert "λήξη υποβολής" in body
    assert 'class="dl-left"' in body


# --------------------------------------------------------------------------- #
# Deadline badge
# --------------------------------------------------------------------------- #
def test_the_badge_is_translated_for_the_script(reader, act):
    reader.cookies.set("lang", "en")
    body = reader.get(f"/act/{act}").text
    assert 'data-l-days="closes in {n} days"' in body
    assert 'data-l-closed="deadline passed"' in body


def test_a_cancelled_act_is_never_counted_down(db, reader, act):
    db.cursor().execute("UPDATE proc.procurement_act SET cancelled = true WHERE adam = %s",
                        (act,))
    assert 'class="dl-left"' not in reader.get(f"/act/{act}").text


# --------------------------------------------------------------------------- #
# Jumps into a hidden section
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("script", ["occurrences.js", "ai_summary.js"])
def test_jumps_reveal_the_section_before_scrolling(script):
    src = pathlib.Path(f"app/static/js/{script}").read_text(encoding="utf-8")
    go = src[src.index("function goTo"):]
    assert "actTabs.reveal(target)" in go
    assert go.index("actTabs.reveal(target)") < go.index("scrollIntoView")


# --------------------------------------------------------------------------- #
# URL and language
# --------------------------------------------------------------------------- #
def test_the_open_tab_never_goes_into_the_query_string():
    js = pathlib.Path("app/static/js/act_page.js").read_text(encoding="utf-8")
    assert "searchParams" not in js
    assert "'#tab-'" in js


def test_the_tab_labels_are_translated(reader, act):
    reader.cookies.set("lang", "en")
    body = reader.get(f"/act/{act}").text
    for label in ("Overview", "Items &amp; CPV", "Competition", "Linked acts"):
        assert label in body, label
