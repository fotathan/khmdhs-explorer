"""Deterministic full-text field scanner: the pure extractors + the
/admin/acts/extract route (suggest-only, no AI)."""
from tests.helpers import get_csrf, login, make_user

SAMPLE = """ΔΗΜΟΣ ΡΟΔΟΥ
ΘΕΜΑ: Προμήθεια ιατρικού εξοπλισμού
ΑΦΜ 094019245
CPV 33100000-1
Καταληκτική ημερομηνία υποβολής: 15/06/2026
Ημερομηνία δημοσίευσης: 01/06/2026
Προϋπολογισθείσα δαπάνη 50.000,00 € χωρίς ΦΠΑ
Συνολική αξία συμπεριλαμβανομένου ΦΠΑ 62.000,00 €
Εγγύηση συμμετοχής 1.000,00 €
Ταχ. Κώδικας 851 00 Ρόδος
"""


# --------------------------------------------------------------------------- #
# pure extractors (no DB, no AI)
# --------------------------------------------------------------------------- #
def test_afm_mod11_validation():
    from app.text_extract import valid_afm
    assert valid_afm("094019245") is True          # a real, checksum-valid ΑΦΜ
    assert valid_afm("123456789") is False          # fails the check digit
    assert valid_afm("000000000") is False
    assert valid_afm("12345") is False


def test_dates_anchor_to_closest_keyword():
    from app.text_extract import find_dates
    got = {d["raw"]: d["target"] for d in find_dates(SAMPLE)}
    assert got["15/06/2026"] == "final_submission_date"   # "καταληκτική … υποβολής"
    assert got["01/06/2026"] == "submission_date"         # "δημοσίευσης"


def test_written_greek_date_formats():
    from app.text_extract import find_dates

    def iso(text):
        got = find_dates(text)
        return got[0]["iso"] if got else None

    assert iso("28 Ιουλίου 2026") == "2026-07-28"
    assert iso("28ης Ιουλίου του 2026") == "2026-07-28"    # ordinal suffix + "του"
    assert iso("1ης Σεπτεμβρίου 2026") == "2026-09-01"
    assert iso("έως και την 15η Μαΐου 2027") == "2027-05-15"   # Μαΐου (ΐ)
    # a real word between number and year that ISN'T a month must NOT match
    assert find_dates("5 προϊόντων του 2026") == []


def test_amounts_anchor_and_parse():
    from app.text_extract import find_amounts
    got = {a["raw"].split()[0]: (a["value"], a["target"]) for a in find_amounts(SAMPLE)}
    assert got["50.000,00"] == ("50000.00", "budget")
    assert got["62.000,00"] == ("62000.00", "total_cost_with_vat")
    assert got["1.000,00"] == ("1000.00", "bid_bond_amount")


def test_cpv_postal_title_authority():
    from app import text_extract as tx
    assert "33100000" in tx.find_cpv_prefixes(SAMPLE)
    assert "85100" in tx.find_postals(SAMPLE)
    assert tx.find_title(SAMPLE) == "Προμήθεια ιατρικού εξοπλισμού"
    assert tx.find_authority_hint(SAMPLE) == "ΔΗΜΟΣ ΡΟΔΟΥ"   # not the boilerplate line
    assert tx.find_afms(SAMPLE) == ["094019245"]


# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #
def test_extract_route(client, db):
    # seed a CPV code + a postal→NUTS so the DB-validated candidates resolve
    cur = db.cursor()
    cur.execute("INSERT INTO proc.cpv_code (cpv_code, description) VALUES "
                "('33100000-1','Ιατρικές συσκευές') ON CONFLICT (cpv_code) DO NOTHING")
    cur.execute("INSERT INTO proc.nuts_code (nuts_code, label) VALUES "
                "('EL421','Δωδεκάνησα') ON CONFLICT (nuts_code) DO NOTHING")
    cur.execute("INSERT INTO proc.postal_nuts (postal_code, nuts_code) VALUES "
                "('85100','EL421') ON CONFLICT DO NOTHING")

    make_user("scanadmin", "goodpassword1", role="admin")
    login(client, "scanadmin", "goodpassword1")
    tok = get_csrf(client)
    r = client.post("/admin/acts/extract",
                    data={"text": SAMPLE, "csrf_token": tok},
                    follow_redirects=False)
    assert r.status_code == 200
    sugg = r.json()["suggestions"]
    by_kind = {}
    for s in sugg:
        by_kind.setdefault(s["kind"], []).append(s)

    # CPV validated against the vocabulary
    assert any(s["value"] == "33100000-1" for s in by_kind.get("cpv", []))
    # postal → NUTS from the gazetteer
    postal = next(s for s in by_kind["postal"] if s["value"] == "85100")
    assert postal["nuts"] == "EL421" and postal["target"] == "postal_code"
    # dates + amounts anchored to the right target fields
    dt = {s["display"]: s["target"] for s in by_kind["date"]}
    assert dt["15/06/2026"] == "final_submission_date"
    am = {s["display"].split()[0]: s["target"] for s in by_kind["amount"]}
    assert am["1.000,00"] == "bid_bond_amount"
    # title
    assert any(s["value"] == "Προμήθεια ιατρικού εξοπλισμού" for s in by_kind["title"])


def test_extract_requires_admin(client):
    make_user("scancustomer", "goodpassword1", role="customer")
    login(client, "scancustomer", "goodpassword1")
    tok = get_csrf(client)
    r = client.post("/admin/acts/extract",
                    data={"text": "x", "csrf_token": tok}, follow_redirects=False)
    assert r.status_code in (302, 303, 403)      # admin-gated (redirect or forbidden)
