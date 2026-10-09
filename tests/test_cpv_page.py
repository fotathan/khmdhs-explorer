# -*- coding: utf-8 -*-
"""Public CPV pages (app/cpv_page.py, docs/specs/public-detail-pages.md
slice 4) and the CPV entry on /ai.

URLS       /cpv/<full code> is the page; 8 digits redirect to it (301); a
           supplementary or unknown code is a 404.

CONTENT    the exact official name, the AI paragraph WITH its AI label and a
           link to /ai — never a hidden one, and no empty box when there is
           none; ancestors and direct sub-codes; 12-month figures counting each
           act once per prefix; open tenders only.

PUBLIC     no login needed; indexable; in the sitemap only with a visible
           paragraph (a bare name is thin content).

/ai        the CPV entry's badge follows the table, and Anthropic is declared
           as its processor even when the summary runs on DeepSeek.
"""
import pytest

from app import cpv_page as cp

DIV = "98000000-3"       # a division of its own in the test schema
GRP = "98300000-6"
LEAF = "98310000-9"
SUPP = "ZZ99-9"          # a supplementary-style code: not a CPV subject
NOTE_EL = ("Η κατηγορία καλύπτει δοκιμαστικές υπηρεσίες πλυντηρίων και στεγνοκαθαριστηρίων, "
           "όπως πλύσιμο, σιδέρωμα και καθαρισμό ρούχων και κλινοσκεπασμάτων για δομές.")
NOTE_EN = ("This category covers test laundry and dry-cleaning services, such as "
           "washing, ironing and cleaning of clothes and bed linen for institutions.")


def _cleanup(cur):
    cur.execute("UPDATE proc.procurement_act SET duplicate_of = NULL WHERE adam LIKE 'CPVP-%'")
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'CPVP-%'")
    cur.execute("DELETE FROM proc.cpv_code WHERE cpv_code IN (%s, %s, %s, %s)",
                (DIV, GRP, LEAF, SUPP))


def _act(cur, adam, atype="notice", *, cpv=LEAF, deadline_days=None, days_ago=10,
         title="Δοκιμή CPV", value=1000):
    cur.execute("""
        INSERT INTO proc.procurement_act
          (adam, type, title, origin, data_source, total_cost_with_vat,
           submission_date, final_submission_date)
        VALUES (%s, %s::proc.act_type, %s, 'import', 'khmdhs', %s,
                now() - make_interval(days => %s),
                CASE WHEN %s::int IS NULL THEN NULL
                     ELSE now() + make_interval(days => %s::int) END)""",
        (adam, atype, title, value, days_ago, deadline_days, deadline_days))
    for code in (cpv if isinstance(cpv, tuple) else (cpv,)):
        cur.execute("INSERT INTO proc.act_object_detail (adam) VALUES (%s) RETURNING id", (adam,))
        od = cur.fetchone()["id"]
        cur.execute("INSERT INTO proc.object_detail_cpv (object_detail_id, cpv_code) "
                    "VALUES (%s, %s)", (od, code))


@pytest.fixture()
def codes(db):
    cur = db.cursor()
    _cleanup(cur)
    cur.execute("""INSERT INTO proc.cpv_code (cpv_code, description, description_en) VALUES
                   (%s, 'Δοκιμαστικές υπηρεσίες', 'Test services'),
                   (%s, 'Δοκιμαστικές υπηρεσίες πλυντηρίων', 'Test laundry services'),
                   (%s, 'Δοκιμαστικό πλύσιμο', 'Test washing'),
                   (%s, 'Συμπληρωματικός', 'Supplementary')""", (DIV, GRP, LEAF, SUPP))
    cp._tree_cache["codes"] = None           # the hierarchy is cached per process
    yield cur
    _cleanup(cur)
    cp._tree_cache["codes"] = None
    cur.execute("REFRESH MATERIALIZED VIEW proc.mv_cpv_activity WITH NO DATA")


@pytest.fixture()
def with_note(codes):
    codes.execute("""INSERT INTO proc.cpv_note (cpv_code, text_el, text_en, model,
                       prompt_version, input_hash) VALUES (%s, %s, %s, 'm', 1, 'h')""",
                  (GRP, NOTE_EL, NOTE_EN))
    return codes


@pytest.fixture()
def with_acts(codes):
    _act(codes, "CPVP-N-OPEN", deadline_days=5, title="Ανοιχτή πλύση")
    _act(codes, "CPVP-N-SHUT", deadline_days=-5, title="Κλειστή πλύση")
    _act(codes, "CPVP-C-1", "contract", cpv=(LEAF, LEAF))      # two lines, one act
    _act(codes, "CPVP-C-OLD", "contract", days_ago=500)
    codes.execute("REFRESH MATERIALIZED VIEW proc.mv_cpv_activity")
    return codes


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #
def test_eight_digits_redirect_to_the_canonical_code(client, codes):
    r = client.get("/cpv/98300000", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == f"/cpv/{GRP}"


@pytest.mark.parametrize("raw", [SUPP, "nonsense", "12345678"])
def test_anything_else_is_a_404(client, codes, raw):
    assert client.get(f"/cpv/{raw}").status_code == 404


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
def test_the_page_is_public_and_labels_the_ai_text(client, with_note):
    body = client.get(f"/cpv/{GRP}").text
    assert "Δοκιμαστικές υπηρεσίες πλυντηρίων" in body          # exact official name
    assert NOTE_EL in body
    assert "γραμμένη με τεχνητή νοημοσύνη" in body and 'href="/ai"' in body
    assert f'href="/cpv/{DIV}"' in body and f'href="/cpv/{LEAF}"' in body


def test_a_hidden_note_is_never_shown(client, with_note):
    with_note.execute("UPDATE proc.cpv_note SET hidden_at = now() WHERE cpv_code = %s", (GRP,))
    body = client.get(f"/cpv/{GRP}").text
    assert NOTE_EL not in body and 'class="panel cpv-note"' not in body


def test_english_readers_get_the_english_text(client, with_note):
    client.cookies.set("lang", "en")
    body = client.get(f"/cpv/{GRP}").text
    assert NOTE_EN in body and "Test laundry services" in body


def test_figures_count_each_act_once_and_only_the_window(client, with_acts):
    p = cp.page(with_acts, GRP, "el")
    f = p["figures"]
    assert (f["n_notices"], f["n_contracts"]) == (2, 1)
    assert f["n_valued"] == 1 and float(f["value"]) == 1000


def test_open_tenders_only(client, with_acts):
    body = client.get(f"/cpv/{DIV}").text
    assert "Ανοιχτή πλύση" in body and "Κλειστή πλύση" not in body


def test_no_figures_before_the_view_is_populated(client, codes):
    assert cp.page(codes, GRP, "el")["figures"] is None
    assert "Δεν δημοσιεύτηκαν" in client.get(f"/cpv/{GRP}").text


def test_the_index_lists_the_divisions(client, codes):
    body = client.get("/cpv").text
    assert f'href="/cpv/{DIV}"' in body and f'href="/cpv/{GRP}"' not in body


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #
def test_cpv_pages_are_indexable(monkeypatch):
    from app import seo
    monkeypatch.setattr(seo, "enabled", lambda: True)
    assert seo.is_indexable(f"/cpv/{GRP}", []) and seo.is_indexable("/cpv", [])
    assert not seo.is_indexable(f"/cpv/{GRP}/x", [])


def test_only_codes_with_a_visible_note_reach_the_sitemap(client, with_note, monkeypatch):
    from app import seo
    monkeypatch.setattr(seo, "enabled", lambda: True)
    assert "/sitemap-cpv-1.xml" in client.get("/sitemap.xml").text
    body = client.get("/sitemap-cpv-1.xml").text
    assert f"/cpv/{GRP}" in body and f"/cpv/{LEAF}" not in body
    with_note.execute("UPDATE proc.cpv_note SET hidden_at = now()")
    assert client.get("/sitemap-cpv-1.xml").status_code == 404


# --------------------------------------------------------------------------- #
# /ai
# --------------------------------------------------------------------------- #
def test_the_ai_badge_follows_the_table(client, codes):
    body = client.get("/ai").text
    block = body[body.index("Περιγραφές κωδικών CPV"):][:200]
    assert "ανενεργό" in block
    codes.execute("""INSERT INTO proc.cpv_note (cpv_code, text_el, text_en, model,
                       prompt_version, input_hash) VALUES (%s, 'α', 'b', 'm', 1, 'h')""", (GRP,))
    body = client.get("/ai").text
    block = body[body.index("Περιγραφές κωδικών CPV"):][:200]
    assert "ενεργό" in block and "ανενεργό" not in block


def test_anthropic_is_declared_for_cpv_notes_under_a_deepseek_summary(
        client, with_note, monkeypatch):
    from app import ai_summary as ai
    monkeypatch.setattr(ai, "MODEL", "deepseek-flash")
    body = client.get("/ai").text
    assert "Οι περιγραφές κωδικών CPV γράφτηκαν με το API της Anthropic" in body
