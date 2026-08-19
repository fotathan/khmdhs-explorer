"""Storage + routes for the CRM email builder: proc.email_template's helpers in
app/auth.py, the customer-page preview/count endpoints in app/crm.py, and the
/admin/email-templates CRUD in app/admin.py.

The merge itself is covered without a database in tests/test_email_builder.py;
what matters here is that the right body reaches it and the right output comes
back out.
"""
import pytest

from app import auth
from tests.helpers import connect, get_csrf, login, make_user

BODY = ('<div><p data-keep>Αγαπητή [[full_name]],</p>'
        '<p style="color:#434343">slot</p>'
        '<ul><li style="margin:2px">item</li></ul>'
        '<p>@@ts4_footer</p></div>')


@pytest.fixture()
def templates_clean(_clean):
    """_clean truncates app_user but not proc.email_template (its only FK,
    updated_by, is ON DELETE SET NULL), so clear the templates per test."""
    with connect() as c:
        c.execute("DELETE FROM proc.email_template")
    yield


def _admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")


def _customer(full_name="Μαρία Παπαδοπούλου", company="ACME ΑΕ"):
    uid = make_user("acme", "goodpassword1")
    with connect() as conn:
        auth.upsert_profile(conn.cursor(), uid,
                            {"full_name": full_name, "company": company},
                            updated_by=None)
    return uid


def _template(slug="outreach", lang="el", name="Πρώτη επαφή",
              subject="Πρόταση για [[company]]", body=BODY):
    with connect() as conn:
        return auth.upsert_email_template(conn.cursor(), slug=slug, lang=lang,
                                          name=name, subject=subject,
                                          body_html=body, updated_by=None)


# --------------------------------------------------------------------------- #
# storage (app/auth.py)
# --------------------------------------------------------------------------- #
def test_upsert_replaces_the_row_for_one_slug_and_lang(templates_clean):
    first = _template()
    again = _template(name="Πρώτη επαφή v2", subject=None)
    assert again == first                                  # same row, not a second
    with connect() as conn:
        row = auth.get_email_template(conn.cursor(), "outreach", "el")
    assert row["name"] == "Πρώτη επαφή v2"
    assert row["subject"] is None                          # a cleared subject nulls


def test_languages_are_separate_rows(templates_clean):
    _template()
    _template(lang="en", name="First contact")
    with connect() as conn:
        c = conn.cursor()
        assert auth.get_email_template(c, "outreach", "el")["name"] == "Πρώτη επαφή"
        assert auth.get_email_template(c, "outreach", "en")["name"] == "First contact"
        # the picker offers one entry per slug — language is chosen separately
        assert len(auth.email_template_options(c)) == 1


def test_slug_is_normalised_on_write_and_read(templates_clean):
    _template(slug="OutReach")
    with connect() as conn:
        c = conn.cursor()
        assert auth.get_email_template(c, "outreach", "el") is not None
        assert auth.get_email_template(c, "OUTREACH", "el") is not None


@pytest.mark.parametrize("lang", ["fr", "", None])
def test_unsupported_language_is_refused(templates_clean, lang):
    with pytest.raises(ValueError):
        _template(lang=lang)


@pytest.mark.parametrize("kwargs", [{"slug": ""}, {"name": ""}, {"body": "   "}])
def test_blank_required_fields_are_refused(templates_clean, kwargs):
    with pytest.raises(ValueError):
        _template(**kwargs)


def test_delete_removes_only_that_language(templates_clean):
    tid = _template()
    _template(lang="en", name="First contact")
    with connect() as conn:
        c = conn.cursor()
        auth.delete_email_template(c, tid)
        assert auth.get_email_template(c, "outreach", "el") is None
        assert auth.get_email_template(c, "outreach", "en") is not None


# --------------------------------------------------------------------------- #
# preview route (app/crm.py)
# --------------------------------------------------------------------------- #
def _preview(client, uid, **overrides):
    data = {"template": "outreach", "lang": "el", "text": "Πρώτη\n\n- alpha\n- beta"}
    data.update(overrides)
    return client.post(f"/admin/crm/{uid}/email/preview", data=data,
                       headers={"X-CSRF-Token": get_csrf(client)})


def test_preview_merges_and_personalises(client, templates_clean):
    uid = _customer()
    _template()
    _admin(client)
    r = _preview(client, uid)
    assert r.status_code == 200
    assert "Μαρία Παπαδοπούλου" in r.text              # [[full_name]] resolved
    assert "Πρόταση για ACME ΑΕ" in r.text             # subject resolved too
    assert "alpha" in r.text and "beta" in r.text      # list filled
    assert "@@ts4_footer" in r.text                    # protected paragraph kept
    assert "[[full_name]]" not in r.text


def test_preview_keeps_template_styling(client, templates_clean):
    uid = _customer()
    _template()
    _admin(client)
    assert "color:#434343" in _preview(client, uid).text


def test_preview_refuses_when_the_profile_lacks_a_field(client, templates_clean):
    uid = _customer(full_name="")
    _template(body='<p>Αγαπητέ [[full_name]] από [[city]]</p><p>slot</p>',
              subject=None)
    _admin(client)
    r = _preview(client, uid)
    assert r.status_code == 200
    # Both missing fields named, so the admin can fill the profile in once.
    assert "full_name" in r.text and "city" in r.text


@pytest.mark.parametrize("overrides", [{"template": "nope"}, {"lang": "fr"}])
def test_preview_reports_a_missing_template(client, templates_clean, overrides):
    uid = _customer()
    _template()
    _admin(client)
    r = _preview(client, uid, **overrides)
    assert r.status_code == 200 and "πρότυπο" in r.text


def test_preview_404s_for_an_unknown_customer(client, templates_clean):
    _template()
    _admin(client)
    assert _preview(client, 999999).status_code == 404


def test_preview_is_admin_only(client, templates_clean):
    uid = _customer()
    _template()
    login(client, "acme", "goodpassword1")             # the customer, not an admin
    assert _preview(client, uid).status_code == 403


def test_count_endpoint_uses_the_shared_segmenter(client, templates_clean):
    uid = _customer()
    _admin(client)
    r = client.post(f"/admin/crm/{uid}/email/count",
                    data={"text": "one\n\n- a\n- b\n\ntwo"},
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 200
    assert "2 παράγραφοι" in r.text and "1 λίστες" in r.text


def test_customer_page_offers_the_picker(client, templates_clean):
    uid = _customer()
    _template()
    _admin(client)
    html = client.get(f"/admin/crm/{uid}").text
    assert 'value="outreach"' in html and "Πρώτη επαφή" in html


# --------------------------------------------------------------------------- #
# admin CRUD (app/admin.py)
# --------------------------------------------------------------------------- #
def test_admin_creates_a_template(client, templates_clean):
    _admin(client)
    r = client.post("/admin/email-templates",
                    data={"slug": "FollowUp", "lang": "en", "name": "Follow-up",
                          "subject": "Hi [[company]]", "body_html": "<p>x</p>"},
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 200
    with connect() as conn:
        assert auth.get_email_template(conn.cursor(), "followup", "en") is not None


def test_admin_edit_view_prefills_and_lists_required_fields(client, templates_clean):
    tid = _template()
    _admin(client)
    html = client.get(f"/admin/email-templates?edit={tid}").text
    assert "Επεξεργασία προτύπου" in html
    assert "[[full_name]]" in html and "[[company]]" in html


def test_admin_rejects_a_blank_body_without_losing_the_draft(client, templates_clean):
    _admin(client)
    r = client.post("/admin/email-templates",
                    data={"slug": "x1", "lang": "el", "name": "Κράτα με",
                          "subject": "", "body_html": "   "},
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 400
    assert "Κράτα με" in r.text                        # the draft is redisplayed
    assert "Ελέγξτε τα υποχρεωτικά πεδία" in r.text    # localised, and the
    assert "body_html are required" not in r.text      # helper's English never leaks


def test_admin_deletes_a_template(client, templates_clean):
    tid = _template()
    _admin(client)
    r = client.post(f"/admin/email-templates/{tid}/delete",
                    headers={"X-CSRF-Token": get_csrf(client)})
    assert r.status_code == 200
    with connect() as conn:
        assert auth.get_email_template(conn.cursor(), "outreach", "el") is None


def test_admin_templates_page_is_admin_only(client, templates_clean):
    make_user("acme", "goodpassword1")
    login(client, "acme", "goodpassword1")
    assert client.get("/admin/email-templates").status_code == 403
