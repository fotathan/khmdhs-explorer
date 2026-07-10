"""Per-line delivery/realisation addresses: ingest mapping + act-page display."""
from tests.helpers import login, make_user


def test_kv_key_handles_address_shapes():
    from khmdhs_ingest import kv_key
    od = {
        "city": "Ρόδος",
        "countryOfDelivery": {"key": "GR", "value": "Ελλάδα"},  # {key,value}
        "streetNumber": None,
        "addressForDelivery": "Πλατεία 1",
    }
    assert kv_key(od, "city") == "Ρόδος"
    assert kv_key(od, "countryOfDelivery") == "GR"          # keeps the ISO key
    assert kv_key(od, "streetNumber") is None
    assert kv_key(od, "addressForDelivery") == "Πλατεία 1"


def test_delivery_address_renders_on_act_page(client, db):
    adam = "TEST-DELIV-0001"
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
    cur.execute("""INSERT INTO proc.procurement_act (adam, type, title, origin, data_source)
                   VALUES (%s, 'contract', 'Δοκιμαστική πράξη', 'import', 'khmdhs')""", (adam,))
    cur.execute("""INSERT INTO proc.act_object_detail
                     (adam, line_no, short_description,
                      delivery_city, delivery_country, delivery_street)
                   VALUES (%s, 0, 'Ανταλλακτικό', 'Ρόδος', 'GR', 'Οδός 5')""", (adam,))

    # an admin is ungated, so line items (and their addresses) are shown
    make_user("actadmin", "goodpassword1", role="admin")
    login(client, "actadmin", "goodpassword1")
    r = client.get(f"/act/{adam}", follow_redirects=False)
    assert r.status_code == 200
    assert "Τόπος παράδοσης" in r.text     # the new column header
    assert "Ρόδος" in r.text               # delivery city
    assert "Οδός 5" in r.text or "GR" in r.text

    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
