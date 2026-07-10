"""The create/edit act form renders the full-text field-picker with the UX fixes.

(Client-side behaviour — focus, arrow-key nav, wheel-scroll — is verified in the
browser; this is a render/regression guard that the markup + JS ship.)"""
from tests.helpers import login, make_user


def test_create_form_ships_field_picker_js(client):
    make_user("formadmin", "goodpassword1", role="admin")
    login(client, "formadmin", "goodpassword1")
    r = client.get("/admin/acts/new", follow_redirects=False)
    assert r.status_code == 200
    html = r.text
    assert "ft-selmenu-filter" in html          # the filter input
    assert "filterEl.focus" in html             # auto-focus on open
    assert "ArrowDown" in html and "ArrowUp" in html   # keyboard navigation
    assert "menu.contains(e.target)" in html    # internal-scroll guard (wheel works)
