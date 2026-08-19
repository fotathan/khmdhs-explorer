"""Pure-unit tests for the CRM email builder's merge (no database).

Mirrors the standalone Multilingual-HTML-Template-Builder's htmlMerge/splitBlocks
suites, which this module is a port of. As there, changes to merge behaviour
should be checked against BOTH the HTML output and to_plain_text, since the
plain-text alternative is derived from the merged HTML rather than computed
independently — that is the whole point of deriving it, and a regression shows
up in only one of the two.
"""
import pytest

from app import email_builder as eb

# A template exercising every rule at once: a protected salutation carrying a
# merge field, two ordinary slots with inline styling to preserve, a list whose
# <li> carries its own style, and a protected sign-off holding an @@token.
TEMPLATE = (
    '<div style="font-family:Fira Sans">'
    '<p data-keep>Αγαπητή [[full_name]],</p>'
    '<p style="color:#434343" align="left">first slot</p>'
    '<p class="body">second slot</p>'
    '<ul><li style="margin:4px 0">item</li></ul>'
    '<p>Με εκτίμηση,<br>@@ts4_footer</p>'
    '</div>'
)


# --------------------------------------------------------------------------- #
# segmentation
# --------------------------------------------------------------------------- #
def test_blank_line_starts_a_new_block():
    segments = eb.segment_text("one\n\ntwo\n\n\n\nthree")
    assert [s.kind for s in segments] == ["prose"] * 3
    assert [s.text for s in segments] == ["one", "two", "three"]


def test_single_newline_stays_inside_a_block():
    segments = eb.segment_text("line one\nline two")
    assert len(segments) == 1
    assert segments[0].text == "line one\nline two"


def test_bullet_run_becomes_a_list_without_a_blank_line():
    # Writers routinely end a list and carry straight on; treating the whole
    # block as one paragraph would lose the list entirely.
    segments = eb.segment_text("intro\n- alpha\n* beta\ntrailing sentence")
    assert [s.kind for s in segments] == ["prose", "list", "prose"]
    assert segments[1].items == ("alpha", "beta")
    assert segments[2].text == "trailing sentence"


def test_crlf_and_blank_padding_are_normalised():
    assert eb.segment_text("a\r\n\r\n   \r\nb") == [
        eb.Segment("prose", text="a"), eb.Segment("prose", text="b")]


def test_a_dash_without_a_space_is_not_a_bullet():
    segments = eb.segment_text("-not a bullet")
    assert segments[0].kind == "prose"


def test_segment_counts_matches_segment_text():
    text = "one\n\n- a\n- b\n\ntwo"
    assert eb.segment_counts(text) == (2, 1)


# --------------------------------------------------------------------------- #
# merge — filling, cloning, removing
# --------------------------------------------------------------------------- #
def test_slots_fill_in_document_order_keeping_attributes():
    html = eb.merge_into_template("alpha\n\nbeta", TEMPLATE).html
    assert '<p style="color:#434343" align="left">alpha</p>' in html
    assert '<p class="body">beta</p>' in html


def test_soft_break_becomes_a_br():
    html = eb.merge_into_template("one\ntwo", TEMPLATE).html
    assert "one<br>two" in html


def test_list_items_clone_the_first_li_with_its_style():
    html = eb.merge_into_template("- alpha\n- beta", TEMPLATE).html
    assert '<li style="margin:4px 0">alpha</li>' in html
    assert '<li style="margin:4px 0">beta</li>' in html


def test_surplus_blocks_clone_the_last_paragraph():
    result = eb.merge_into_template("a\n\nb\n\nc\n\nd", TEMPLATE)
    assert result.created == 2
    # The clones inherit the prototype's attributes, not bare <p>s.
    assert result.html.count('<p class="body">') == 3


def test_surplus_slots_are_removed_not_left_empty():
    result = eb.merge_into_template("only one", TEMPLATE)
    assert result.removed == 2                    # one <p> and the <ul>
    assert "<p></p>" not in result.html
    assert "<ul>" not in result.html              # never a stray empty bullet


def test_bullets_fall_back_to_prose_when_the_template_has_no_list():
    html = eb.merge_into_template("- a\n- b", "<div><p>slot</p></div>").html
    assert "- a<br>- b" in html


def test_template_with_no_paragraph_gets_bare_ones():
    result = eb.merge_into_template("a\n\nb", "<div><span>x</span></div>")
    assert result.created == 2
    assert "<p>a</p>" in result.html and "<p>b</p>" in result.html


def test_fragment_is_not_wrapped_in_a_document():
    # document mode would corrupt every generated body with <html><body>.
    html = eb.merge_into_template("x", "<p>slot</p>").html
    assert html == "<p>x</p>"


def test_empty_text_clears_every_slot():
    result = eb.merge_into_template("", TEMPLATE)
    assert result.filled == 0 and result.removed == 3
    assert "@@ts4_footer" in result.html          # protected, so still there


# --------------------------------------------------------------------------- #
# protection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("template", [
    '<div><p data-keep>keep</p><p>slot</p></div>',
    '<div data-no-translate><p>keep</p></div><div><p>slot</p></div>',
    '<div><p data-no-translate>keep</p><p>slot</p></div>',      # self, not just ancestors
    '<div><p>keep @@token</p><p>slot</p></div>',
    '<div><p>keep [[full_name]]</p><p>slot</p></div>',
])
def test_protected_paragraphs_are_neither_filled_nor_counted(template):
    result = eb.merge_into_template("filler", template)
    assert "keep" in result.html
    assert result.filled == 1 and result.removed == 0
    assert "filler" in result.html


def test_a_merge_field_paragraph_survives_to_be_resolved():
    # Without protection the merge would overwrite the salutation and the
    # personalisation would vanish silently instead of erroring.
    merged = eb.merge_into_template("body text", TEMPLATE)
    assert "[[full_name]]" in merged.html
    assert "Μαρία" in eb.resolve_fields(merged.html, {"full_name": "Μαρία"})


# --------------------------------------------------------------------------- #
# sanitising
# --------------------------------------------------------------------------- #
def test_allowed_inline_markup_survives():
    out = eb.render_block("a <strong>b</strong> <em>c</em> <u>d</u> <span>e</span>")
    assert out == "a <strong>b</strong> <em>c</em> <u>d</u> <span>e</span>"


@pytest.mark.parametrize("payload, gone", [
    ("<script>alert(1)</script>hi", "alert"),
    ('<img src=x onerror=alert(1)>hi', "onerror"),
    ('<span style="color:red" onclick="x()">s</span>', "onclick"),
    ('<a href="javascript:evil()">t</a>', "javascript"),
    ('<a href="data:text/html,x">t</a>', "data:"),
])
def test_dangerous_markup_is_stripped(payload, gone):
    assert gone not in eb.render_block(payload)


@pytest.mark.parametrize("href", ["https://x.gr", "http://x.gr", "mailto:a@b.gr"])
def test_safe_hrefs_survive(href):
    assert href in eb.render_block(f'<a href="{href}">t</a>')


def test_text_is_escaped_not_interpreted():
    assert eb.render_block("A & B < C") == "A &amp; B &lt; C"


# --------------------------------------------------------------------------- #
# merge fields
# --------------------------------------------------------------------------- #
def test_fields_resolve_and_are_html_escaped():
    out = eb.resolve_fields("<p>[[company]]</p>", {"company": "A & <B>"})
    assert out == "<p>A &amp; &lt;B&gt;</p>"


@pytest.mark.parametrize("values", [{}, {"full_name": ""}, {"full_name": "   "},
                                    {"full_name": None}])
def test_a_field_without_a_usable_value_is_refused(values):
    with pytest.raises(eb.UnresolvedFieldsError) as excinfo:
        eb.resolve_fields("<p>[[full_name]]</p>", values)
    assert excinfo.value.fields == ["full_name"]


def test_every_missing_field_is_reported_at_once():
    with pytest.raises(eb.UnresolvedFieldsError) as excinfo:
        eb.resolve_fields("[[a]] [[b]] [[a]]", {})
    assert excinfo.value.fields == ["a", "b"]


def test_field_names_lists_what_a_template_asks_for():
    assert eb.field_names(TEMPLATE) == ["full_name"]
    assert eb.field_names("[[b]] [[a]] [[b]]") == ["b", "a"]   # first-seen order


# --------------------------------------------------------------------------- #
# plain text
# --------------------------------------------------------------------------- #
def test_plain_text_comes_from_the_merged_html():
    result = eb.merge_into_template("alpha\n\n- one\n- two", TEMPLATE)
    # Protected salutation and sign-off are included exactly as they will send.
    assert result.text.startswith("Αγαπητή [[full_name]],")
    assert "- one\n- two" in result.text
    assert result.text.endswith("Με εκτίμηση,\n@@ts4_footer")


def test_plain_text_drops_markup_and_keeps_soft_breaks():
    text = eb.to_plain_text("<p>a <strong>b</strong><br>c</p>")
    assert text == "a b\nc"


def test_nested_blocks_are_not_emitted_twice():
    assert eb.to_plain_text("<ul><li><p>inner</p></li></ul>") == "- inner"


def test_empty_list_items_are_dropped():
    assert eb.to_plain_text("<ul><li>a</li><li>  </li></ul>") == "- a"


# --------------------------------------------------------------------------- #
# build_email — the one call the route makes
# --------------------------------------------------------------------------- #
def test_build_email_merges_resolves_and_derives_text():
    result = eb.build_email("alpha\n\n- one", TEMPLATE, {"full_name": "Μαρία"})
    assert "Αγαπητή Μαρία," in result.html
    assert result.text.startswith("Αγαπητή Μαρία,")
    assert "[[" not in result.html


def test_build_email_refuses_text_over_the_cap():
    with pytest.raises(ValueError, match=str(eb.MAX_SOURCE_CHARS)):
        eb.build_email("x" * (eb.MAX_SOURCE_CHARS + 1), TEMPLATE, {"full_name": "M"})
