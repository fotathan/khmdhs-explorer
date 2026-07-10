"""Act export (CSV / XLSX): the pure serializers + the /export/acts route
(auth gate, formats, row cap, columns)."""
import datetime as dt
import io
import zipfile

from tests.helpers import login, make_user


# --------------------------------------------------------------------------- #
# pure serializers (no DB)
# --------------------------------------------------------------------------- #
def test_rows_to_csv_has_bom_and_rows():
    from app.act_export import rows_to_csv
    data = rows_to_csv(["ΑΔΑΜ", "Αξία"], [["24ABC", 1234.5], ["24DEF", None]])
    assert data[:3] == b"\xef\xbb\xbf"                 # UTF-8 BOM (Excel opens Greek)
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "ΑΔΑΜ,Αξία"
    assert "24ABC,1234.5" in lines[1]
    assert lines[2] == "24DEF,"                        # None → empty cell


def test_rows_to_xlsx_is_valid_zip():
    from app.act_export import rows_to_xlsx
    data = rows_to_xlsx(["A", "B"], [[1, "x"], [2, "y"]], sheet_title="KHMDHS")
    assert data[:2] == b"PK"                           # xlsx is a zip
    assert zipfile.ZipFile(io.BytesIO(data)).namelist()  # opens cleanly


def test_rows_to_xlsx_strips_tzinfo():
    """Postgres timestamptz values are tz-aware; openpyxl rejects those, so the
    serializer must drop tzinfo instead of raising."""
    from app.act_export import rows_to_xlsx
    aware = dt.datetime(2026, 7, 3, 12, 0, tzinfo=dt.timezone.utc)
    data = rows_to_xlsx(["When"], [[aware]])           # must not raise
    assert data[:2] == b"PK"


# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #
def _seed_acts(db, n, prefix="EXP"):
    cur = db.cursor()
    for i in range(n):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, submission_date, origin, data_source)
                       VALUES (%s, 'notice', %s, now(), 'import', 'khmdhs')
                       ON CONFLICT (adam) DO NOTHING""",
                    (f"{prefix}-{i:05d}", f"Δοκιμή {i}"))


def test_anonymous_export_redirects_to_login(client):
    r = client.get("/export/acts?fmt=csv", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/login")


def test_csv_export_download(client, db):
    _seed_acts(db, 3)
    make_user("exp_csv", "goodpassword1", role="customer")
    login(client, "exp_csv", "goodpassword1")
    r = client.get("/export/acts?type=notice&fmt=csv", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.csv"')
    body = r.content.decode("utf-8-sig")
    assert "ΑΔΑΜ" in body.splitlines()[0]               # header present
    assert "EXP-00000" in body


def test_xlsx_export_download(client, db):
    _seed_acts(db, 2, prefix="XL")
    make_user("exp_xlsx", "goodpassword1", role="customer")
    login(client, "exp_xlsx", "goodpassword1")
    r = client.get("/export/acts?type=notice&fmt=xlsx", follow_redirects=False)
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"


def test_bad_format_rejected(client):
    make_user("exp_bad", "goodpassword1", role="customer")
    login(client, "exp_bad", "goodpassword1")
    r = client.get("/export/acts?fmt=pdf", follow_redirects=False)
    assert r.status_code == 400


def test_row_cap_enforced(client, db, monkeypatch):
    import csv
    import app.main as m
    monkeypatch.setattr(m, "_EXPORT_CAP_CUSTOMER", 2)   # tiny cap for the test
    _seed_acts(db, 5, prefix="CAP")
    make_user("exp_cap", "goodpassword1", role="customer")
    login(client, "exp_cap", "goodpassword1")
    r = client.get("/export/acts?type=notice&fmt=csv", follow_redirects=False)
    assert r.status_code == 200
    rows = list(csv.reader(r.content.decode("utf-8-sig").splitlines()))
    assert len(rows) - 1 <= 2                            # data rows never exceed the cap
