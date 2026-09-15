"""Rich full text (app/rich_text.py).

full_text_html is rendered with |safe, loaded into the Quill editor, and — for
imports — built from copied web pages. The tests guard the three ways that goes
wrong: markup that is unsafe to render, markup the editor silently mangles
(a two-line cell becomes two cells; a bullet list comes back numbered), and
page clutter that survives into the stored document.
"""
from __future__ import annotations

import pytest

from app import rich_text as rt


# --------------------------------------------------------------------------- #
# sanitize — the security step
# --------------------------------------------------------------------------- #
def test_sanitize_strips_script_handlers_and_styles():
    out = rt.sanitize('<p style="color:red" onclick="x()">Hi<script>alert(1)</script></p>')
    assert out == "<p>Hi</p>"


def test_sanitize_drops_javascript_links_and_adds_rel():
    assert "javascript" not in (rt.sanitize('<a href="javascript:alert(1)">x</a>') or "")
    out = rt.sanitize('<a href="https://example.gr/a">x</a>')
    assert 'href="https://example.gr/a"' in out and 'rel="noopener noreferrer"' in out


def test_sanitize_keeps_what_quill_needs_to_round_trip():
    quill = ('<ol><li data-list="bullet"><span class="ql-ui" contenteditable="false"></span>a</li></ol>'
             '<table><tbody><tr><td data-row="row-abc1">x</td><td data-row="row-abc1">y</td></tr></tbody></table>')
    out = rt.sanitize(quill)
    assert 'data-list="bullet"' in out            # else bullets reload as numbers
    assert out.count('data-row="row-abc1"') == 2  # else Quill cannot rebuild the row
    assert "contenteditable" not in out


def test_sanitize_rejects_odd_attribute_values():
    out = rt.sanitize('<ol><li data-list="evil">a</li></ol><table><tbody><tr>'
                      '<td data-row="x y&quot;">1</td></tr></tbody></table>')
    assert "data-list" not in out and "data-row" not in out


@pytest.mark.parametrize("empty", ["", "   ", None, "<p><br></p>", "<div>&nbsp;</div>"])
def test_sanitize_empty_editor_is_no_html(empty):
    assert rt.sanitize(empty) is None


def test_sanitize_is_idempotent_on_normalised_output():
    html = rt.normalise_imported_html(
        "<div><b>Τίτλος</b></div><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert rt.sanitize(html) == html


# --------------------------------------------------------------------------- #
# normalise_imported_html — the shape step
# --------------------------------------------------------------------------- #
def test_div_soup_becomes_paragraphs():
    raw = ('<div><div style="font-size:9pt"><div><em>Αναρτήθηκε</em></div>'
           '<div><b>04/08/2026 10:43</b></div><div>&nbsp;</div></div></div>')
    assert rt.normalise_imported_html(raw) == (
        "<p><em>Αναρτήθηκε</em></p><p><strong>04/08/2026 10:43</strong></p>")


def test_form_widgets_and_converter_leftovers_are_removed_with_content():
    raw = ('<div>Κείμενο<input value="secret"><button>Υποβολή</button>'
           '<select><option>Επιλογή</option></select><xsl:value-of select="x">ΧΣΛ</xsl:value-of>'
           '<script>bad()</script></div>')
    out = rt.normalise_imported_html(raw)
    assert out == "<p>Κείμενο</p>"


def test_inline_formatting_and_headings_are_mapped():
    out = rt.normalise_imported_html("<h1>Α</h1><h5>Β</h5><div><b>γ</b> <i>δ</i> <span>ε</span></div>")
    assert out == "<h2>Α</h2><h4>Β</h4><p><strong>γ</strong> <em>δ</em> ε</p>"


def test_br_separates_paragraphs():
    assert rt.normalise_imported_html("γραμμή 1<br>γραμμή 2<br><br>") == "<p>γραμμή 1</p><p>γραμμή 2</p>"


def test_lists_are_kept_flat():
    out = rt.normalise_imported_html("<ul><li><div>ένα</div></li><li> δύο </li><li></li></ul>")
    assert out == "<ul><li>ένα</li><li>δύο</li></ul>"


def test_data_table_is_quill_shaped():
    raw = ("<table><thead><tr><th>Είδος</th><th>Ποσό</th></tr></thead>"
           "<tbody><tr><td><p>Γραμμή α</p><p>συνέχεια</p></td><td>1.000,00 €</td></tr>"
           "<tr><td colspan='2'>Σύνολο</td></tr></tbody></table>")
    out = rt.normalise_imported_html(raw)
    assert out == (
        '<table><tbody>'
        '<tr><td data-row="r1"><strong>Είδος</strong></td><td data-row="r1"><strong>Ποσό</strong></td></tr>'
        '<tr><td data-row="r2">Γραμμή α συνέχεια</td><td data-row="r2">1.000,00 €</td></tr>'
        '<tr><td data-row="r3">Σύνολο</td><td data-row="r3"></td></tr>'
        '</tbody></table>')


def test_single_column_layout_table_becomes_paragraphs():
    out = rt.normalise_imported_html("<table><tr><td>Α</td></tr><tr><td></td></tr><tr><td>Β</td></tr></table>")
    assert out == "<p>Α</p><p>Β</p>"


def test_nested_table_is_flattened_into_its_cell():
    raw = ("<table><tr><td>Έξω</td><td><table><tr><td>μέσα 1</td><td>μέσα 2</td></tr></table></td></tr></table>")
    out = rt.normalise_imported_html(raw)
    assert out.count("<table>") == 1
    assert "μέσα 1 · μέσα 2" in out


def test_empty_and_unparseable_input():
    assert rt.normalise_imported_html("") is None
    assert rt.normalise_imported_html("<div> &nbsp; </div>") is None
    assert rt.normalise_imported_html("απλό κείμενο") == "<p>απλό κείμενο</p>"


# --------------------------------------------------------------------------- #
# html_to_text / import_full_text
# --------------------------------------------------------------------------- #
def test_plain_text_follows_the_html():
    html = ('<h2>Τίτλος</h2><p>Παράγραφος</p><ul><li>α</li><li>β</li></ul>'
            '<table><tbody><tr><td data-row="r1">x</td><td data-row="r1">y</td></tr></tbody></table>')
    assert rt.html_to_text(html) == "Τίτλος\n\nΠαράγραφος\n\n• α\n• β\n\nx | y"


def test_import_full_text_returns_both_columns():
    html, text = rt.import_full_text("<div><b>ΑΔΑΜ</b></div><div>26PROC019571715</div>")
    assert html == "<p><strong>ΑΔΑΜ</strong></p><p>26PROC019571715</p>"
    assert text == "ΑΔΑΜ\n\n26PROC019571715"
    assert rt.import_full_text(None) == (None, None)
