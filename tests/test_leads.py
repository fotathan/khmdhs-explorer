"""Prospective-lead import from the Contractor Database: engine + routes.
DB-backed — skips without TEST_DATABASE_URL."""
import pytest

from app import leads as L
from tests.helpers import connect, get_csrf, login, make_user


@pytest.fixture()
def leads_clean(_clean):
    """_clean truncates app_user (cascades customer_profile/contact); also clear
    the operators + freemail table we create per test, and (re)seed freemail."""
    with connect() as c:
        c.execute("DELETE FROM proc.economic_operator WHERE vat_number LIKE 'LT%'")
        c.execute("INSERT INTO proc.crm_freemail_domain(domain) VALUES ('gmail.com') "
                  "ON CONFLICT DO NOTHING")
    yield


def _op(cur, vat, name, **extra):
    cols = {"vat_number": vat, "name": name, "country": "GR"}
    cols.update(extra)
    keys = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    cur.execute(f"INSERT INTO proc.economic_operator ({keys}) VALUES ({ph}) RETURNING operator_id",
                list(cols.values()))
    return cur.fetchone()["operator_id"]


def _oprow(cur, oid):
    cur.execute("SELECT * FROM proc.economic_operator WHERE operator_id=%s", (oid,))
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
def test_map_and_create_lead(leads_clean):
    with connect() as conn:
        c = conn.cursor()
        make_user("boss", "goodpassword1", role="admin")     # for round-robin
        oid = _op(c, "LT111", "ΑΛΦΑ ΑΕ", statistical_or_tax_number="TAX1", ar_gemi="GEMI1",
                  street_address="Οδός 1", city="Αθήνα", postal_code="11111",
                  contact_person="Γιώργος Παπαδόπουλος", contact_email="info@alpha.gr",
                  contact_phone="2101234567", orgdb_id="ORG1")
        lead = L.map_operator(c, _oprow(c, oid))
        assert lead["company"] == "ΑΛΦΑ ΑΕ" and lead["tax_number"] == "TAX1"
        assert lead["reg_number"] == "GEMI1" and lead["contact_email"] == "info@alpha.gr"

        uid = L.create_lead(c, lead, by=None)
        c.execute("""SELECT u.email, u.password_hash, p.crm_stage, p.service,
                            p.creation_source, p.manager_id, p.operator_id,
                            p.tax_number, p.reg_number, p.company
                     FROM proc.app_user u JOIN proc.customer_profile p ON p.user_id=u.id
                     WHERE u.id=%s""", (uid,))
        row = c.fetchone()
        assert row["crm_stage"] == "prospective" and row["service"] == "TAS"
        assert row["creation_source"] == "OrgDB" and row["manager_id"] is not None
        assert row["operator_id"] == oid and row["tax_number"] == "TAX1"
        assert row["email"] == "info@alpha.gr" and row["password_hash"]   # random hash present
        # main contact created, active
        c.execute("SELECT first_name,last_name,is_main,is_active FROM proc.customer_contact WHERE user_id=%s", (uid,))
        ct = c.fetchone()
        assert ct["first_name"] == "Γιώργος" and ct["is_main"] and ct["is_active"]


def test_generated_email_and_placeholder_contact(leads_clean):
    with connect() as conn:
        c = conn.cursor()
        make_user("boss", "goodpassword1", role="admin")
        oid = _op(c, "LT222", "ΒΗΤΑ ΟΕ")   # no email, no contact
        uid = L.create_lead(c, L.map_operator(c, _oprow(c, oid)))
        c.execute("SELECT email FROM proc.app_user WHERE id=%s", (uid,))
        assert c.fetchone()["email"] == f"{uid}@prospective.com"
        c.execute("SELECT first_name,last_name FROM proc.customer_contact WHERE user_id=%s AND is_main", (uid,))
        ct = c.fetchone()
        assert (ct["first_name"], ct["last_name"]) == ("FirstName", "LastName")


def test_extra_contacts_inactive(leads_clean):
    with connect() as conn:
        c = conn.cursor()
        make_user("boss", "goodpassword1", role="admin")
        oid = _op(c, "LT250", "ΓΑΜΑ ΑΕ", contact_person="Κύριος Α", contact_email="a@gama.gr")
        # projected TED/khmdhs act + an act_contractor row linking a 2nd contact
        c.execute("INSERT INTO proc.procurement_act(adam,type,data_source,origin,title) "
                  "VALUES ('LEADACT1','contract','manual','authored','x')")
        c.execute("""INSERT INTO proc.act_contractor(adam,ord,name,operator_id,contact_person,email)
                     VALUES ('LEADACT1',0,'ΓΑΜΑ ΑΕ',%s,'Κυρία Β','b@gama.gr')""", (oid,))
        uid = L.create_lead(c, L.map_operator(c, _oprow(c, oid)))
        c.execute("SELECT email,is_main,is_active FROM proc.customer_contact WHERE user_id=%s ORDER BY is_main DESC", (uid,))
        rows = c.fetchall()
        assert len(rows) == 2
        assert rows[0]["is_main"] and rows[0]["is_active"]           # a@gama.gr main
        assert (not rows[1]["is_main"]) and (not rows[1]["is_active"])  # b@gama.gr inactive


def test_conflict_classes(leads_clean):
    # economic_operator.vat_number is UNIQUE, so the strong-id case uses a shared
    # ΓΕΜΗ (ar_gemi) across two different ΑΦΜ instead of a duplicate ΑΦΜ.
    with connect() as conn:
        c = conn.cursor()
        make_user("boss", "goodpassword1", role="admin")
        base = _op(c, "LT300", "ΔΕΛΤΑ ΑΕ", statistical_or_tax_number="TAXD",
                   ar_gemi="GEMID", contact_email="sales@delta.gr")
        L.create_lead(c, L.map_operator(c, _oprow(c, base)))

        strong = _op(c, "LT301", "ΑΛΛΗ ΕΠΩΝΥΜΙΑ", ar_gemi="GEMID")  # same ΓΕΜΗ
        conf = L.detect_conflict(c, L.map_operator(c, _oprow(c, strong)))
        assert conf["kind"] == "strong_id" and "create" not in conf["allowed"]

        exact = _op(c, "LT302", "ΕΨΙΛΟΝ ΑΕ", contact_email="sales@delta.gr")  # same email
        conf = L.detect_conflict(c, L.map_operator(c, _oprow(c, exact)))
        assert conf["kind"] == "exact_email" and "create_new_email" in conf["allowed"]

        dom = _op(c, "LT303", "ΖΗΤΑ ΑΕ", contact_email="info@delta.gr")  # same non-freemail domain
        conf = L.detect_conflict(c, L.map_operator(c, _oprow(c, dom)))
        assert conf["kind"] == "email_domain"

        free = _op(c, "LT304", "ΗΤΑ ΑΕ", contact_email="someone@gmail.com")  # freemail → not a domain conflict
        conf = L.detect_conflict(c, L.map_operator(c, _oprow(c, free)))
        assert conf["kind"] in ("clean", "name_soft")

        conn.rollback()


def test_round_robin_manager_distributes(leads_clean):
    with connect() as conn:
        c = conn.cursor()
        a1 = make_user("adm1", "goodpassword1", role="admin")
        a2 = make_user("adm2", "goodpassword1", role="admin")
        mgrs = []
        for i in range(4):
            oid = _op(c, f"LT40{i}", f"Ε{i} ΑΕ")
            uid = L.create_lead(c, L.map_operator(c, _oprow(c, oid)))
            c.execute("SELECT manager_id FROM proc.customer_profile WHERE user_id=%s", (uid,))
            mgrs.append(c.fetchone()["manager_id"])
        # both admins used, balanced 2/2
        assert set(mgrs) == {a1, a2} and mgrs.count(a1) == 2
        conn.rollback()


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
def _admin(client):
    make_user("boss", "goodpassword1", role="admin")
    login(client, "boss", "goodpassword1")


def test_review_and_import_flow(client, leads_clean):
    with connect() as conn:
        c = conn.cursor()
        clean = _op(c, "LT500", "ΚΑΘΑΡΗ ΑΕ", contact_email="hi@clean.gr")
    _admin(client)
    tok = get_csrf(client)
    r = client.post("/admin/crm/leads/review", data={"vat": ["LT500"]},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200 and "ΚΑΘΑΡΗ ΑΕ" in r.text

    r = client.post("/admin/crm/leads/import",
                    data={"operator_id": [clean], f"action_{clean}": "create"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    with connect() as conn:
        c = conn.cursor()
        c.execute("""SELECT p.crm_stage FROM proc.customer_profile p
                     WHERE p.operator_id=%s""", (clean,))
        assert c.fetchone()["crm_stage"] == "prospective"
    # now it appears under the Prospective segment
    html = client.get("/admin/crm?segment=prospective").text
    assert "ΚΑΘΑΡΗ ΑΕ" in html or "prospective" in html.lower()


def test_freemail_crud_and_normalize(leads_clean):
    assert L.normalize_domain(" @WWW.Foo.GR ") == "foo.gr"
    with connect() as conn:
        c = conn.cursor()
        L.add_freemail(c, "Example.COM")
        assert "example.com" in L.list_freemail(c)
        with pytest.raises(ValueError):
            L.add_freemail(c, "not a domain")
        L.remove_freemail(c, "example.com")
        assert "example.com" not in L.list_freemail(c)
        conn.rollback()


def test_freemail_admin_screen(client, leads_clean):
    _admin(client)
    tok = get_csrf(client)
    r = client.post("/admin/crm/freemail/add", data={"domain": "@FooBar.gr"},
                    headers={"X-CSRF-Token": tok}, follow_redirects=False)
    assert r.status_code == 303
    with connect() as conn:
        assert "foobar.gr" in L.list_freemail(conn.cursor())
    assert "foobar.gr" in client.get("/admin/crm/freemail").text
    client.post("/admin/crm/freemail/delete", data={"domain": "foobar.gr"},
                headers={"X-CSRF-Token": tok}, follow_redirects=False)
    with connect() as conn:
        assert "foobar.gr" not in L.list_freemail(conn.cursor())


def test_import_requires_admin(client, leads_clean):
    with connect() as conn:
        c = conn.cursor()
        oid = _op(c, "LT600", "ΑΝΕΥ ΑΔΕΙΑΣ ΑΕ")
    # not logged in
    r = client.post("/admin/crm/leads/review", data={"vat": ["LT600"]},
                    follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)


def test_strong_id_create_blocked_via_route(client, leads_clean):
    with connect() as conn:
        c = conn.cursor()
        base = _op(c, "LT700", "ΒΑΣΗ ΑΕ", ar_gemi="GEMIX")
        # pre-create a lead for base
    _admin(client)
    tok = get_csrf(client)
    with connect() as conn:
        c = conn.cursor()
        from app import leads as _L
        c.execute("SELECT * FROM proc.economic_operator WHERE operator_id=%s", (base,))
        _L.create_lead(c, _L.map_operator(c, c.fetchone()), by=None)
        dup = _op(c, "LT701", "ΔΙΠΛΗ ΑΕ", ar_gemi="GEMIX")  # same ΓΕΜΗ → strong id
    # try to CREATE from the route → must be skipped (not created)
    r = client.post("/admin/crm/leads/import",
                    data={"operator_id": [dup], f"action_{dup}": "create"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    with connect() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) AS n FROM proc.customer_profile WHERE operator_id=%s", (dup,))
        assert c.fetchone()["n"] == 0     # creation forbidden by the hard block
