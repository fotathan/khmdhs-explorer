"""Award criterion shows the stored API label, not the unreliable code."""
from tests.helpers import login, make_user


def test_kv_label_extracts_value():
    from khmdhs_ingest import kv_label
    assert kv_label({"assignCriteria": {"key": "9", "value": "Βάσει τιμής"}},
                    "assignCriteria") == "Βάσει τιμής"
    assert kv_label({"assignCriteria": "9"}, "assignCriteria") is None
    assert kv_label({}, "assignCriteria") is None


def test_act_page_shows_criterion_label_not_code(client, db):
    adam = "TEST-CRIT-0001"
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
    # code 9 is the unreliable key; the label is the truth we want shown
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source,
                      assign_criteria_code, assign_criteria_label)
                   VALUES (%s,'contract','Δοκιμή','import','khmdhs','9','Βάσει τιμής')""",
                (adam,))
    make_user("actadmin", "goodpassword1", role="admin")
    login(client, "actadmin", "goodpassword1")
    r = client.get(f"/act/{adam}", follow_redirects=False)
    assert r.status_code == 200
    assert "Κριτήριο ανάθεσης" in r.text
    assert "Βάσει τιμής" in r.text            # the label is shown
    # the raw code must NOT be shown as the value
    assert "<dd>9</dd>" not in r.text
    cur.execute("DELETE FROM proc.procurement_act WHERE adam=%s", (adam,))
