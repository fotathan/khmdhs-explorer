"""The phone layout's markup contract (beta_base.html + customer pages).

The responsive CSS is additive — the desktop layout is untouched — but it hangs
off a handful of hooks in the markup. Delete one in a template edit and the page
still works on a desktop, while on a phone the menu vanishes or a table spills
sideways. Nobody would notice until a customer did, so the hooks are pinned:

  MENU     the masthead has a toggle button that controls #mast-nav, and the
           `js-mobile` class is set in <head> (before paint) so the phone CSS
           may hide the nav behind that button.
  FILTERS  the search page has the filters toggle.
  TABLES   the act page's party / line-item tables are `.stack` tables whose
           cells carry data-label captions, since the header row is hidden.
  I18N     the new label is translated.
"""
import pytest

from tests.helpers import grant, login, make_user

ADAM = "TEST-MOBILE-0001"
VAT = "999000222"


def _cleanup(cur):
    cur.execute("DELETE FROM proc.act_operator WHERE adam = %s", (ADAM,))
    cur.execute("DELETE FROM proc.act_object_detail WHERE adam = %s", (ADAM,))
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = %s", (ADAM,))
    cur.execute("DELETE FROM proc.economic_operator WHERE vat_number = %s", (VAT,))


@pytest.fixture()
def act(db):
    """A contract with a winner and one line item, so both stacked tables render."""
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("""INSERT INTO proc.economic_operator (vat_number, name, is_greek_vat)
                   VALUES (%s, 'ΑΝΑΔΟΧΟΣ ΚΙΝΗΤΟΥ ΑΕ', true)
                   RETURNING operator_id""", (VAT,))
    op = cur.fetchone()["operator_id"]
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, total_cost_with_vat)
                   VALUES (%s, 'contract', 'Προμήθεια για κινητό', 'import', 'khmdhs',
                           12000)""", (ADAM,))
    cur.execute("""INSERT INTO proc.act_object_detail (adam, short_description)
                   VALUES (%s, 'είδος δοκιμής')""", (ADAM,))
    cur.execute("""INSERT INTO proc.act_operator (adam, operator_id, role)
                   VALUES (%s, %s, 'winner')""", (ADAM, op))
    yield ADAM
    _cleanup(cur)


@pytest.fixture()
def reader(client):
    """An entitled customer: the gated teaser renders no tables at all."""
    uid = make_user("mobile_cust", "goodpassword1")
    grant(uid, "pro", days=365)
    login(client, "mobile_cust", "goodpassword1")
    return client


def test_the_masthead_has_a_menu_button_for_the_nav(client):
    body = client.get("/").text
    assert 'class="nav-toggle"' in body
    assert 'aria-controls="mast-nav"' in body
    assert 'id="mast-nav"' in body


def test_js_mobile_is_set_in_the_head_before_paint(client):
    body = client.get("/").text
    head = body[: body.index("</head>")]
    assert "classList.add('js-mobile')" in head


def test_the_phone_css_ships_and_its_style_block_is_closed(client):
    body = client.get("/").text
    assert "@media(max-width:760px)" in body
    assert body.count("<style") == body.count("</style>")


def test_the_search_page_can_fold_its_filters(client):
    body = client.get("/").text
    assert 'class="filters-toggle"' in body


def test_the_act_tables_stack_with_captions(reader, act):
    body = reader.get(f"/act/{act}").text
    assert "ΑΝΑΔΟΧΟΣ ΚΙΝΗΤΟΥ ΑΕ" in body, "the operators table did not render"
    assert "είδος δοκιμής" in body, "the line-items table did not render"
    assert body.count('class="dtable stack"') >= 2
    assert 'class="cell-main"' in body
    assert 'data-label="ΑΦΜ"' in body


def test_the_authority_totals_table_stacks_rather_than_scrolls():
    """Five columns (type, acts, cancelled, value, filter link) need ~440-470px
    in either language; a phone has ~330px. It shipped as a sideways-scrolling
    table once and the link was cut off, so it is a stacked table now."""
    import pathlib
    src = pathlib.Path("app/templates/beta_authority.html").read_text(encoding="utf-8")
    panel = src[src.index('t("Σύνολα ανά τύπο πράξης")'):]
    panel = panel[: panel.index("</table>")]
    assert 'class="dtable stack"' in panel
    assert "table-scroll" not in panel
    assert 'class="val cell-link"' in panel


def test_the_menu_label_is_translated(client):
    client.cookies.set("lang", "en")
    body = client.get("/").text
    assert 'aria-label="Menu"' in body
