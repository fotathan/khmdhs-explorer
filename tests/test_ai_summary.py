# -*- coding: utf-8 -*-
"""AI summary: the quote gate, reconciliation against the record, the cache key.

No database and no API key. Everything here is the part of app/ai_summary.py
that decides what a reader is allowed to see — spec §4 and §8 — driven by
recorded payloads rather than live calls, so it runs in CI and costs nothing.
"""
import json

import pytest

from app import ai_summary as ai


TEXT = (
    "Η καταληκτική ημερομηνία υποβολής προσφορών είναι η 27/06/2026.\n"
    "Απαιτείται  εγγύηση\n     συμμετοχής ύψους 2% της εκτιμώμενης αξίας, "
    "ήτοι 50.000 €.\n"
    "Ο προϋπολογισμός ανέρχεται σε 2.500.000,00 ευρώ.\n"
    "Ο ανάδοχος υποχρεούται να διαθέτει πιστοποιητικό «ISO 9001:2015».\n"
)

RECORD = {
    "adam": "24PROC000000001",
    "type": "notice",
    "title": "Υπηρεσίες καθαρισμού κτιρίων",
    "final_submission_date": "2026-06-27",
    "budget": 2500000,
    "total_cost_without_vat": None,
    "total_cost_with_vat": None,
}


# The configured default is a DeepSeek model, so a test that means to exercise
# the Anthropic transport has to say so. Naming it once here keeps the intent
# readable and stops the suite silently following a future default change.
ANTHROPIC_MODEL = "claude-opus-5"


def _item(**kw):
    base = {"label": "Ετικέτα", "value": "Τιμή", "obligation": "mandatory",
            "quote": "εγγύηση\n     συμμετοχής", "source": "full_text",
            "confidence": "high"}
    base.update(kw)
    return base


def _raw(**sections):
    payload = {k: [] for k in ai.SECTION_KEYS}
    payload["not_found"] = []
    payload.update(sections)
    return payload


# --------------------------------------------------------------------------- #
# §8 — the quote gate
# --------------------------------------------------------------------------- #
def test_locate_exact():
    span = ai.locate(TEXT, "εγγύηση")
    assert span and TEXT[span[0]:span[1]] == "εγγύηση"


@pytest.mark.parametrize("quote", [
    "ΕΓΓΥΗΣΗ ΣΥΜΜΕΤΟΧΗΣ",            # case
    "εγγυηση συμμετοχης",             # accents stripped
    "εγγύηση συμμετοχήσ",             # medial sigma for final
    "Απαιτείται εγγύηση συμμετοχής",  # collapses "  " and "\n     "
])
def test_locate_folds_like_search_does(quote):
    """The gate must accept what search would call the same words — one
    normaliser (app/textmatch.fold), not a second opinion living here."""
    assert ai.locate(TEXT, quote) is not None


def test_locate_offsets_slice_back_to_the_real_text():
    span = ai.locate(TEXT, "Απαιτείται εγγύηση συμμετοχής")
    assert span
    sliced = TEXT[span[0]:span[1]]
    assert sliced.startswith("Απαιτείται") and sliced.endswith("συμμετοχής")


def test_locate_normalises_typographic_quotes():
    """Models rewrite « » as \" \" without being asked. That must not cost the
    item its citation."""
    assert ai.locate(TEXT, 'πιστοποιητικό "ISO 9001:2015"') is not None


def test_locate_returns_none_for_absent_text():
    assert ai.locate(TEXT, "εγγύηση καλής εκτέλεσης") is None


def test_hallucinated_item_is_dropped_not_flagged():
    out = ai.verify(
        _raw(pricing=[_item(label="Εγγύηση καλής εκτέλεσης", value="5%",
                            quote="εγγύηση καλής εκτέλεσης 5%")]),
        {"full_text": TEXT}, RECORD)
    assert out["sections"] == []
    assert out["rejected_n"] == 1


def test_item_citing_a_source_that_does_not_exist_is_dropped():
    out = ai.verify(
        _raw(pricing=[_item(source="attachment:99")]), {"full_text": TEXT}, RECORD)
    assert out["sections"] == [] and out["rejected_n"] == 1


def test_surviving_item_carries_offsets():
    out = ai.verify(_raw(pricing=[_item()]), {"full_text": TEXT}, RECORD)
    item = out["sections"][0]["items"][0]
    assert TEXT[item["start"]:item["end"]].startswith("εγγύηση")


# --------------------------------------------------------------------------- #
# §4 — the record beats the model
# --------------------------------------------------------------------------- #
def test_restating_the_submission_deadline_is_dropped_as_duplicate():
    out = ai.verify(
        _raw(timeline=[_item(label="Καταληκτική ημερομηνία υποβολής",
                             value="27/06/2026",
                             quote="καταληκτική ημερομηνία υποβολής")]),
        {"full_text": TEXT}, RECORD)
    assert out["sections"] == []
    assert out["duplicate_n"] == 1
    assert out["conflicts"] == []          # agreeing with the record is not news


def test_contradicting_the_budget_is_a_conflict_and_is_not_rendered():
    out = ai.verify(
        _raw(pricing=[_item(label="Προϋπολογισμός", value="3.000.000 €",
                            quote="Ο προϋπολογισμός ανέρχεται")]),
        {"full_text": TEXT}, RECORD)
    assert out["sections"] == []            # never shown to a bidder
    assert len(out["conflicts"]) == 1       # but recorded for an admin
    c = out["conflicts"][0]
    assert c["field"] == "budget" and c["record"] == 2500000 and c["model"] == 3000000


def test_amounts_parse_both_greek_and_english_grouping():
    assert ai._amounts_in("2.500.000,00 ευρώ") == {2500000}
    assert ai._amounts_in("1,234,567.89") == {1234567}
    assert 50000 in ai._amounts_in("ήτοι 50.000 €")


def test_dates_parse_day_first_and_iso():
    assert ai._dates_in("έως 27/06/2026") == {"2026-06-27"}
    assert ai._dates_in("2026-07-01") == {"2026-07-01"}


# --------------------------------------------------------------------------- #
# §5 — the catalogue
# --------------------------------------------------------------------------- #
def test_sections_render_in_catalogue_order_not_model_order():
    out = ai.verify(
        _raw(attention=[_item()], timeline=[_item(value="σχόλιο")]),
        {"full_text": TEXT}, RECORD)
    assert [s["key"] for s in out["sections"]] == ["timeline", "attention"]


def test_unknown_section_key_is_ignored():
    raw = _raw(pricing=[_item()])
    raw["invented_section"] = [_item()]
    out = ai.verify(raw, {"full_text": TEXT}, RECORD)
    assert [s["key"] for s in out["sections"]] == ["pricing"]


def test_empty_sections_do_not_render():
    out = ai.verify(_raw(), {"full_text": TEXT}, RECORD)
    assert out["sections"] == [] and out["n_items"] == 0


def test_not_found_survives_and_is_capped():
    raw = _raw()
    raw["not_found"] = [f"θέμα {i}" for i in range(30)]
    assert len(ai.verify(raw, {"full_text": TEXT}, RECORD)["not_found"]) == 20


def test_unspecified_obligation_becomes_null():
    out = ai.verify(_raw(pricing=[_item(obligation="unspecified")]),
                    {"full_text": TEXT}, RECORD)
    assert out["sections"][0]["items"][0]["obligation"] is None


def test_item_missing_a_required_field_is_dropped():
    out = ai.verify(_raw(pricing=[_item(quote="")]), {"full_text": TEXT}, RECORD)
    assert out["rejected_n"] == 1 and out["sections"] == []


# --------------------------------------------------------------------------- #
# The tool schema — strict mode is unforgiving, and a 400 costs a whole run
# --------------------------------------------------------------------------- #
def test_tool_schema_is_strict_and_fully_required():
    tool = ai.build_tool()
    schema = tool["input_schema"]
    assert tool["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["properties"]["pricing"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_tool_schema_uses_no_union_types_or_null_enums():
    """Both are the shapes most likely to come back as a 400. An empty array and
    the literal "unspecified" say the same thing with no schema risk."""
    blob = json.dumps(ai.build_tool())
    assert "null" not in blob
    for prop in ai.build_tool()["input_schema"]["properties"].values():
        assert isinstance(prop["type"], str)


def test_tool_schema_is_json_serialisable_and_covers_the_catalogue():
    schema = ai.build_tool()["input_schema"]
    assert set(ai.SECTION_KEYS) <= set(schema["properties"])


# --------------------------------------------------------------------------- #
# §9 — the cache key
# --------------------------------------------------------------------------- #
def test_hash_is_stable_for_identical_inputs():
    a = ai.input_hash({"full_text": TEXT})
    assert a == ai.input_hash({"full_text": TEXT})


@pytest.mark.parametrize("sources", [
    {"full_text": TEXT + " "},                     # the text changed
    {"full_text": TEXT, "table:1": "a\tb"},        # a table was published
    {},                                             # everything went away
])
def test_hash_changes_when_any_input_changes(sources):
    assert ai.input_hash(sources) != ai.input_hash({"full_text": TEXT})


def test_hash_changes_with_model_and_language():
    base = ai.input_hash({"full_text": TEXT})
    assert ai.input_hash({"full_text": TEXT}, model="claude-sonnet-5") != base
    assert ai.input_hash({"full_text": TEXT}, lang="en") != base


def test_hash_changes_when_the_prompt_is_revised(monkeypatch):
    """A payload produced by different instructions is a different answer.
    Serving it as though nothing changed is how a prompt regression hides."""
    base = ai.input_hash({"full_text": TEXT})
    monkeypatch.setattr(ai, "PROMPT_VERSION", ai.PROMPT_VERSION + 1)
    assert ai.input_hash({"full_text": TEXT}) != base


def test_hash_is_order_independent_across_tables():
    a = ai.input_hash({"table:2": "x", "table:1": "y"})
    b = ai.input_hash({"table:1": "y", "table:2": "x"})
    assert a == b


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def test_build_sources_labels_match_what_the_model_is_told_to_cite():
    sources = ai.build_sources(
        {"full_text": TEXT},
        [{"id": 7, "locator": "σελ. 3, πίνακας 1", "rows": [["Α", "Β"], ["1", "2"]]}])
    assert set(sources) == {"full_text", "table:7"}
    assert "σελ. 3, πίνακας 1" in sources["table:7"]
    assert "Α\tΒ" in sources["table:7"]


def test_build_sources_skips_empty_full_text_and_empty_tables():
    assert ai.build_sources({"full_text": "   "}, [{"id": 1, "rows": []}]) == {}


def test_a_quote_from_a_table_is_locatable():
    sources = ai.build_sources(
        {"full_text": ""}, [{"id": 3, "locator": "πίνακας 1",
                             "rows": [["Είδος", "Ποσότητα"], ["Θύρες", "4"]]}])
    out = ai.verify(_raw(requirements=[_item(quote="Θύρες", source="table:3")]),
                    sources, RECORD)
    assert out["n_items"] == 1


# --------------------------------------------------------------------------- #
# §6 — truncation is never silent
# --------------------------------------------------------------------------- #
def test_oversized_input_is_capped_and_reported(monkeypatch):
    monkeypatch.setattr(ai, "MAX_INPUT_CHARS", 100)
    text, truncated = ai._render_sources({"full_text": "α" * 500})
    assert truncated == {"source": "full_text", "kept": 100, "total": 500}
    assert text.count("α") == 100


def test_full_text_is_read_before_tables_when_the_cap_bites(monkeypatch):
    monkeypatch.setattr(ai, "MAX_INPUT_CHARS", 10)
    text, _ = ai._render_sources({"table:1": "β" * 50, "full_text": "α" * 50})
    assert "α" in text and "β" not in text


def test_within_the_cap_nothing_is_reported_as_truncated():
    _text, truncated = ai._render_sources({"full_text": TEXT})
    assert truncated is None


# --------------------------------------------------------------------------- #
# §12 — the switch, which spends money when it is on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "enabled"])
def test_enabled_only_for_an_affirmative_value(monkeypatch, value):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", value)
    assert ai.enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "disabled",
                                   "ture", "maybe", " "])
def test_disabled_for_everything_else_including_a_typo(monkeypatch, value):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", value)
    assert ai.enabled() is False


def test_default_is_off(monkeypatch):
    monkeypatch.delenv("AI_SUMMARY_ENABLED", raising=False)
    assert ai.enabled() is False


def test_generation_needs_both_the_switch_and_a_key(monkeypatch):
    monkeypatch.setenv("AI_SUMMARY_ENABLED", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.enabled() is True and ai.can_generate() is False


def test_call_model_without_a_key_raises_rather_than_calling_out(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ai.SummaryError, match="ANTHROPIC_API_KEY"):
        ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)


def test_the_missing_key_error_names_the_variable_for_THIS_model(monkeypatch):
    """Two providers, two keys. Telling an admin to check ANTHROPIC_API_KEY
    when the configured model runs on DeepSeek sends them to inspect a variable
    the request never read."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ai.SummaryError) as e:
        ai.call_model({"full_text": TEXT}, RECORD, model="deepseek-flash")
    assert "DEEPSEEK_API_KEY" in str(e.value)
    assert "ANTHROPIC_API_KEY" not in str(e.value)


def test_call_model_with_no_sources_raises_before_spending_anything(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    with pytest.raises(ai.SummaryError, match="no full text"):
        ai.call_model({}, RECORD)


# --------------------------------------------------------------------------- #
# §13 — cost, in exact integers
# --------------------------------------------------------------------------- #
def test_cost_is_integer_micro_dollars():
    # 20k in @ $5/MTok = 100_000 µ$; 3k out @ $25/MTok = 75_000 µ$
    usage = {"input_tokens": 20_000, "output_tokens": 3_000}
    assert ai.cost_micro_usd(usage, "claude-opus-5") == 175_000


def test_cached_reads_are_billed_too():
    usage = {"input_tokens": 0, "cache_read_input_tokens": 20_000,
             "output_tokens": 0}
    assert ai.cost_micro_usd(usage, "claude-opus-5") == 100_000


def test_unknown_model_records_no_cost_rather_than_a_guess():
    assert ai.cost_micro_usd({"input_tokens": 1}, "claude-something-new") is None


# --------------------------------------------------------------------------- #
# The SSE stream reader
#
# Streaming is what keeps a multi-minute Opus call alive, so the parser is worth
# testing without spending anything: a recorded event sequence, no API key.
# --------------------------------------------------------------------------- #
class _FakeStream:
    """Stands in for the urlopen response: a context manager over byte lines."""

    def __init__(self, events):
        lines = []
        for ev in events:
            lines += [b"event: x\n", b"data: " + json.dumps(ev).encode() + b"\n", b"\n"]
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def _serve(monkeypatch, events):
    monkeypatch.setattr(ai.urllib.request, "urlopen",
                        lambda *a, **k: _FakeStream(events))


def _tool_call(chunks, stop_reason="tool_use"):
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1234}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "…"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "name": "record_summary"}},
        *[{"type": "content_block_delta", "index": 1,
           "delta": {"type": "input_json_delta", "partial_json": c}} for c in chunks],
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason},
         "usage": {"output_tokens": 567}},
        {"type": "message_stop"},
    ]


def test_stream_reassembles_tool_arguments_across_chunks(monkeypatch):
    _serve(monkeypatch, _tool_call(['{"pri', 'cing": [], "not_', 'found": ["α"]}']))
    body, usage, stop = ai._stream(object())
    assert json.loads(body) == {"pricing": [], "not_found": ["α"]}
    assert usage["input_tokens"] == 1234 and usage["output_tokens"] == 567
    assert stop == "tool_use"


def test_stream_ignores_thinking_blocks(monkeypatch):
    """Adaptive thinking is on, so thinking streams too — it must not pollute
    the tool arguments."""
    _serve(monkeypatch, _tool_call(['{"not_found": []}']))
    body, _usage, _stop = ai._stream(object())
    assert json.loads(body) == {"not_found": []}


def test_stream_survives_ping_and_unparseable_framing(monkeypatch):
    events = _tool_call(['{"not_found": []}'])
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _Framed(events))
    body, _u, _s = ai._stream(object())
    assert json.loads(body) == {"not_found": []}


class _Framed(_FakeStream):
    def __iter__(self):
        return iter([b": keep-alive\n", b"event: ping\n", b"data: not json\n",
                     *self._lines])


def test_stream_raises_on_an_error_event(monkeypatch):
    _serve(monkeypatch, [{"type": "error",
                          "error": {"message": "overloaded_error"}}])
    with pytest.raises(ai.SummaryError, match="overloaded_error"):
        ai._stream(object())


def test_a_refusal_is_reported_not_parsed(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    _serve(monkeypatch, _tool_call(['{"not_found": []}'], stop_reason="refusal"))
    with pytest.raises(ai.SummaryError, match="declined"):
        ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)


def test_truncated_tool_arguments_say_so(monkeypatch):
    """Hitting the output cap mid-JSON is now DIAGNOSED, not guessed at.

    It used to fall through to the JSON parser and surface as "tool arguments
    did not parse", which reads as a schema problem several layers from the
    cause. Seen live on the largest notice in the corpus at the old default.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    _serve(monkeypatch, _tool_call(['{"pricing": [{"label": "Εγγ'],
                                    stop_reason="max_tokens"))
    with pytest.raises(ai.SummaryError) as e:
        ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    msg = str(e.value)
    assert "output cap" in msg
    assert "AI_SUMMARY_MAX_TOKENS" in msg      # names the way out
    assert "still billed" in msg               # and the cost of not taking it
    assert "did not parse" not in msg          # not the old misdiagnosis


def test_a_genuinely_malformed_reply_still_reports_a_parse_error(monkeypatch):
    """The cap check must not swallow the case it was mistaken for."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    _serve(monkeypatch, _tool_call(["not json at all"], stop_reason="end_turn"))
    with pytest.raises(ai.SummaryError, match="did not parse"):
        ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)


def test_the_cap_leaves_room_for_the_largest_acts_seen():
    """16,000 was not enough for the biggest notices, and a ceiling costs
    nothing until it is reached — you are billed for tokens generated."""
    assert ai.MAX_TOKENS >= 32000


def test_empty_stream_is_an_error_not_an_empty_summary(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    _serve(monkeypatch, [{"type": "message_start", "message": {"usage": {}}},
                         {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                          "usage": {}}])
    with pytest.raises(ai.SummaryError, match="no structured output"):
        ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)


def test_the_request_actually_asks_for_a_stream(monkeypatch):
    """The whole point: a non-streaming request times out on a real notice."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    seen = {}

    def _capture(req, *a, **k):
        seen["body"] = json.loads(req.data)
        return _FakeStream(_tool_call(['{"not_found": []}']))

    monkeypatch.setattr(ai.urllib.request, "urlopen", _capture)
    ai.call_model({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    assert seen["body"]["stream"] is True


# --------------------------------------------------------------------------- #
# The batch path — half price, asynchronous
# --------------------------------------------------------------------------- #
def test_batch_custom_id_is_acceptable_for_a_greek_adam():
    """custom_id must match ^[a-zA-Z0-9_-]{1,64}$ — every ΑΔΑΜ here fails that,
    which is why the id is a hash and the caller keeps the map back."""
    import re as _re
    for adam in ("ΨΔΞΞΟΡΛΟ-Χ5Δ", "24PROC000000001", "ΨΧ3846ΨΧ0Ε-6Κ8"):
        cid = ai.batch_custom_id(adam)
        assert _re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cid), cid


def test_batch_custom_id_is_stable_and_distinct():
    assert ai.batch_custom_id("ΨΔΞΞΟΡΛΟ-Χ5Δ") == ai.batch_custom_id("ΨΔΞΞΟΡΛΟ-Χ5Δ")
    assert ai.batch_custom_id("ΨΔΞΞΟΡΛΟ-Χ5Δ") != ai.batch_custom_id("ΨΧ3846ΨΧ0Ε-6Κ8")


def test_batch_requests_must_not_ask_for_a_stream(monkeypatch):
    """A batch request carrying "stream" is rejected; only call_model streams."""
    assert "stream" not in ai.request_params({"full_text": TEXT}, RECORD)
    assert ai.request_params({"full_text": TEXT}, RECORD, stream=True)["stream"] is True


def test_both_paths_send_the_same_prompt_and_schema():
    """A prompt that differed between paths would poison the cache: input_hash
    covers PROMPT_VERSION, not which code path produced the row.

    Anthropic on both sides: batch is Anthropic-only, so "the same prompt on
    both paths" is only a claim about that provider."""
    imm = ai.request_params({"full_text": TEXT}, RECORD, stream=True,
                            model=ANTHROPIC_MODEL)
    bat = ai.request_params({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    assert imm["system"] == bat["system"]
    assert imm["tools"] == bat["tools"]
    assert imm["messages"] == bat["messages"]
    assert imm["output_config"] == bat["output_config"]


def test_submit_batch_posts_one_entry_per_act_and_maps_the_ids(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    seen = {}

    def _capture(req, *a, **k):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Bytes(json.dumps({"id": "msgbatch_01", "processing_status": "in_progress"}))

    monkeypatch.setattr(ai.urllib.request, "urlopen", _capture)
    batch_id, ids = ai.submit_batch([
        ("ΨΔΞΞΟΡΛΟ-Χ5Δ", {"full_text": TEXT}, RECORD),
        ("ΨΧ3846ΨΧ0Ε-6Κ8", {"full_text": TEXT}, RECORD),
    ], model=ANTHROPIC_MODEL)
    assert batch_id == "msgbatch_01"
    assert seen["url"].endswith("/v1/messages/batches")
    assert len(seen["body"]["requests"]) == 2
    assert set(ids.values()) == {"ΨΔΞΞΟΡΛΟ-Χ5Δ", "ΨΧ3846ΨΧ0Ε-6Κ8"}
    assert all("stream" not in r["params"] for r in seen["body"]["requests"])


def test_submit_batch_skips_acts_with_nothing_to_read(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(ai.urllib.request, "urlopen",
                        lambda *a, **k: _Bytes(json.dumps({"id": "msgbatch_02"})))
    _bid, ids = ai.submit_batch([("ΑΔΕΙΟ", {}, RECORD),
                                 ("ΨΔΞΞΟΡΛΟ-Χ5Δ", {"full_text": TEXT}, RECORD)],
                                model=ANTHROPIC_MODEL)
    assert list(ids.values()) == ["ΨΔΞΞΟΡΛΟ-Χ5Δ"]


def test_submit_batch_refuses_rather_than_silently_truncating(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(ai, "BATCH_MAX_REQUESTS", 1)
    with pytest.raises(ai.SummaryError, match="chunk the work"):
        ai.submit_batch([("Α", {"full_text": TEXT}, RECORD),
                         ("Β", {"full_text": TEXT}, RECORD)],
                        model=ANTHROPIC_MODEL)


def test_batch_results_are_keyed_by_custom_id_not_position(monkeypatch):
    """Results come back in any order — the docs are explicit about it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    a, b = ai.batch_custom_id("Α"), ai.batch_custom_id("Β")
    lines = [
        {"custom_id": b, "result": {"type": "succeeded", "message": {
            "content": [{"type": "thinking", "thinking": "…"},
                        {"type": "tool_use", "name": "record_summary",
                         "input": {"not_found": ["β"]}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 2}}}},
        {"custom_id": a, "result": {"type": "errored",
                                    "error": {"type": "invalid_request_error"}}},
    ]
    _serve_batch(monkeypatch, lines)
    got = {cid: (kind, payload) for cid, kind, payload in ai.batch_results("msgbatch_03")}
    assert got[b][0] == "succeeded"
    assert got[b][1][0] == {"not_found": ["β"]}
    assert got[a][0] == "errored"


def test_batch_results_refuse_to_read_an_unfinished_batch(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _Bytes(
        json.dumps({"processing_status": "in_progress", "results_url": None})))
    with pytest.raises(ai.SummaryError, match="not ended"):
        list(ai.batch_results("msgbatch_04"))


def test_batch_tokens_cost_half():
    usage = {"input_tokens": 20_000, "output_tokens": 3_000}
    assert ai.cost_micro_usd(usage, "claude-opus-5") == 175_000
    assert ai.cost_micro_usd(usage, "claude-opus-5", batch=True) == 87_500


def test_effort_defaults_to_medium():
    """Thinking bills at the output rate, and this is extraction against a strict
    schema, not open-ended reasoning."""
    assert ai.EFFORT == "medium"
    params = ai.request_params({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    assert params["output_config"]["effort"] == "medium"


class _Bytes:
    """urlopen stand-in for a plain JSON body."""

    def __init__(self, text):
        self._text = text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._text


class _Lines(_Bytes):
    def __init__(self, lines):
        self._lines = [json.dumps(l).encode() + b"\n" for l in lines]

    def __iter__(self):
        return iter(self._lines)


def _serve_batch(monkeypatch, lines):
    def _route(req, *a, **k):
        if req.full_url.endswith("/results"):
            return _Lines(lines)
        return _Bytes(json.dumps({"processing_status": "ended",
                                  "results_url": ai.BATCH_URL + "/x/results"}))
    monkeypatch.setattr(ai.urllib.request, "urlopen", _route)



# --------------------------------------------------------------------------- #
# Two providers
#
# The summary runs on DeepSeek by default and on Anthropic when asked, and the
# MODEL NAME is the only switch. What these guard is that the switch reaches all
# the way down — endpoint, auth header, request shape, error vocabulary — because
# a half-applied switch does not fail loudly. It posts a well-formed request,
# with the right key, to the wrong company.
# --------------------------------------------------------------------------- #
DEEPSEEK_MODEL = "deepseek-flash"


def test_the_default_model_is_deepseek():
    """Not a preference — a measurement (ai_summary_ab.py): comparable yield at
    roughly a twelfth of the price. Changing it changes what /ai tells the
    public, so it is asserted rather than assumed."""
    assert ai.MODEL.startswith("deepseek")
    assert ai.provider_of() == "deepseek"


@pytest.mark.parametrize("model,provider,var", [
    ("deepseek-flash", "deepseek", "DEEPSEEK_API_KEY"),
    ("deepseek-v4-pro", "deepseek", "DEEPSEEK_API_KEY"),
    ("claude-opus-5", "anthropic", "ANTHROPIC_API_KEY"),
    ("claude-sonnet-5", "anthropic", "ANTHROPIC_API_KEY"),
])
def test_the_model_name_picks_the_provider_and_the_key(model, provider, var):
    assert ai.provider_of(model) == provider
    assert ai.key_var(model) == var


def test_an_unknown_model_is_treated_as_anthropic():
    """It has to land somewhere. Anthropic's model list answers a bad name with
    a clear 404; posting it to DeepSeek would be a 404 from a provider that was
    never meant to see the request at all."""
    assert ai.provider_of("gpt-9-turbo") == "anthropic"


def test_api_key_present_asks_about_the_right_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert ai.api_key_present("claude-opus-5") is True
    assert ai.api_key_present("deepseek-flash") is False
    # zero-arg asks about the CONFIGURED model — which is what can_generate()
    # and the /ai page mean by "is this feature available"
    assert ai.api_key_present() is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    assert ai.api_key_present() is True


def test_effort_is_anthropic_only(monkeypatch):
    """output_config.effort is an Anthropic field. DeepSeek rejects the request
    outright when it is present, so it is never built rather than stripped."""
    a = ai.request_params({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    d = ai.request_params({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL)
    assert a["output_config"] == {"effort": ai.EFFORT}
    assert "output_config" not in d


def test_deepseek_gets_the_identical_prompt_schema_and_system_text():
    """The whole reason the A/B result transfers to production: nothing is
    reworded per provider, only rewrapped. A per-provider prompt would make the
    measured yield a statement about a request nobody sends."""
    a = ai.request_params({"full_text": TEXT}, RECORD, model=ANTHROPIC_MODEL)
    d = ai.request_params({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL)
    o = ai._to_openai(d)
    assert o["messages"][0]["content"] == a["system"]
    assert o["messages"][1]["content"] == a["messages"][0]["content"][0]["text"]
    assert o["tools"][0]["function"]["parameters"] == a["tools"][0]["input_schema"]
    assert o["tools"][0]["function"]["name"] == "record_summary"


def test_the_deepseek_request_does_not_force_the_tool_call():
    """DeepSeek refuses a forced tool_choice while thinking is on, and the app
    has never forced one — "reply through the tool or not at all" is enforced
    after the reply, not in the request."""
    d = ai.request_params({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL)
    assert ai._to_openai(d)["tool_choice"] == "auto"


def test_nothing_anthropic_shaped_crosses_over():
    d = ai.request_params({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL,
                          stream=True)
    o = ai._to_openai(d)
    assert "system" not in o and "output_config" not in o
    assert o["stream"] is True and o["stream_options"]["include_usage"] is True


def test_auth_header_matches_the_provider():
    assert ai._headers("k", DEEPSEEK_MODEL) == {
        "content-type": "application/json", "authorization": "Bearer k"}
    an = ai._headers("k", ANTHROPIC_MODEL)
    assert an["x-api-key"] == "k" and an["anthropic-version"] == ai.API_VERSION
    assert "authorization" not in an


# --- the DeepSeek stream ---------------------------------------------------- #
def _openai_events(fragments, finish="tool_calls", usage=True):
    events = [{"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"name": "record_summary", "arguments": f}}]}}]}
        for f in fragments]
    events.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    if usage:
        events.append({"choices": [],
                       "usage": {"prompt_tokens": 1234, "completion_tokens": 567}})
    return events


def _serve_openai(monkeypatch, events):
    lines = [f"data: {json.dumps(e)}\n".encode() for e in events]
    lines.append(b"data: [DONE]\n")

    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter(lines)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _S())


def test_openai_stream_reassembles_arguments_and_usage(monkeypatch):
    """Arguments arrive as fragments and the usage only in the final event.
    Reassembling either wrongly reads as a model failure, not a parser bug."""
    _serve_openai(monkeypatch, _openai_events(['{"pri', 'cing": [], "not_',
                                               'found": ["α"]}']))
    body, usage, stop = ai._stream_openai(object())
    assert json.loads(body) == {"pricing": [], "not_found": ["α"]}
    # renamed to the Anthropic names: cost_micro_usd and the act_ai_summary
    # columns already speak them, on rows generated before this provider existed
    assert usage == {"input_tokens": 1234, "output_tokens": 567}
    assert stop == "tool_use"


def test_openai_finish_reasons_are_translated(monkeypatch):
    """One vocabulary past the transport. "length" arriving untranslated would
    turn the output-cap diagnosis back into "did not parse" — the exact
    misdiagnosis that check was added to end."""
    for finish, expected in [("length", "max_tokens"),
                             ("content_filter", "refusal"),
                             ("stop", "end_turn")]:
        _serve_openai(monkeypatch, _openai_events(['{"not_found": []}'], finish))
        _b, _u, stop = ai._stream_openai(object())
        assert stop == expected, finish


def test_openai_stream_raises_on_an_error_frame(monkeypatch):
    _serve_openai(monkeypatch, [{"error": {"message": "rate limit exceeded"}}])
    with pytest.raises(ai.SummaryError, match="rate limit exceeded"):
        ai._stream_openai(object())


def test_openai_stream_survives_unparseable_framing(monkeypatch):
    events = _openai_events(['{"not_found": []}'])
    lines = [b": keep-alive\n", b"data: not json\n",
             *[f"data: {json.dumps(e)}\n".encode() for e in events], b"data: [DONE]\n"]

    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter(lines)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: _S())
    body, _u, _s = ai._stream_openai(object())
    assert json.loads(body) == {"not_found": []}


def test_call_model_posts_a_deepseek_model_to_deepseek(monkeypatch):
    """The switch has to reach the SOCKET. A request with the right key, the
    right schema and the wrong URL is the failure this exists to catch."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    seen = {}
    lines = [f"data: {json.dumps(e)}\n".encode()
             for e in _openai_events(['{"not_found": []}'])] + [b"data: [DONE]\n"]

    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter(lines)

    def _capture(req, *a, **k):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["body"] = json.loads(req.data)
        return _S()

    monkeypatch.setattr(ai.urllib.request, "urlopen", _capture)
    raw, usage, _t = ai.call_model({"full_text": TEXT}, RECORD,
                                   model=DEEPSEEK_MODEL)
    assert seen["url"] == ai.DEEPSEEK_URL
    assert seen["headers"]["authorization"] == "Bearer sk-not-a-real-key"
    assert "x-api-key" not in seen["headers"]
    assert seen["body"]["model"] == DEEPSEEK_MODEL
    assert seen["body"]["stream"] is True
    assert raw == {"not_found": []}
    assert usage["input_tokens"] == 1234


def test_the_output_cap_is_diagnosed_on_deepseek_too(monkeypatch):
    """Same error, same wording, same way out — the message an admin reads must
    not depend on which provider was configured."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    _serve_openai(monkeypatch, _openai_events(['{"pricing": [{"label": "Εγγ'],
                                               finish="length"))
    with pytest.raises(ai.SummaryError) as e:
        ai.call_model({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL)
    msg = str(e.value)
    assert "output cap" in msg and "AI_SUMMARY_MAX_TOKENS" in msg
    assert "did not parse" not in msg


def test_a_deepseek_transport_error_names_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")

    def _boom(*a, **k):
        raise ai.urllib.error.URLError("nodename nor servname provided")
    monkeypatch.setattr(ai.urllib.request, "urlopen", _boom)
    with pytest.raises(ai.SummaryError) as e:
        ai.call_model({"full_text": TEXT}, RECORD, model=DEEPSEEK_MODEL)
    assert "DeepSeek" in str(e.value) and "Anthropic" not in str(e.value)


# --- batch stays Anthropic-only --------------------------------------------- #
def test_batch_refuses_a_deepseek_model(monkeypatch):
    """Batch exists for ONE reason — half price — and DeepSeek publishes no
    batch endpoint. Falling back to full-rate single calls would charge more
    for the job someone asked to run cheaply, silently."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    with pytest.raises(ai.SummaryError, match="Anthropic-only"):
        ai.submit_batch([("Α", {"full_text": TEXT}, RECORD)],
                        model=DEEPSEEK_MODEL)


def test_batch_refuses_the_configured_default_when_it_is_deepseek(monkeypatch):
    """submit_batch() with no model must not quietly inherit a DeepSeek default
    and post an Anthropic-shaped body to Anthropic under a DeepSeek name."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    with pytest.raises(ai.SummaryError, match="Anthropic-only"):
        ai.submit_batch([("Α", {"full_text": TEXT}, RECORD)])


def test_batch_auth_does_not_follow_the_configured_model(monkeypatch):
    """The batch path reads its auth from _anthropic_headers, not from the
    configured model — which is a DeepSeek one. Reading it from the model would
    send a Bearer token to Anthropic and read as a revoked key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    seen = {}

    def _capture(req, *a, **k):
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _Bytes(json.dumps({"id": "msgbatch_09"}))

    monkeypatch.setattr(ai.urllib.request, "urlopen", _capture)
    ai.submit_batch([("Α", {"full_text": TEXT}, RECORD)], model=ANTHROPIC_MODEL)
    assert seen["headers"]["x-api-key"] == "sk-ant-not-a-real-key"
    assert "authorization" not in seen["headers"]


# --- money ------------------------------------------------------------------ #
def test_deepseek_is_priced_at_the_peak_rate():
    """The conservative half of DeepSeek's peak/off-peak split. Recording the
    off-peak number would understate every act generated in business hours, and
    this column is what any "what did this cost" answer reads."""
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert ai.cost_micro_usd(usage, "deepseek-flash") == round(0.30e6 + 1.20e6)


def test_the_cheaper_provider_is_actually_cheaper():
    """The reason for the move, asserted so a future price edit that reverses it
    cannot land quietly."""
    usage = {"input_tokens": 20_000, "output_tokens": 3_000}
    assert (ai.cost_micro_usd(usage, "deepseek-flash")
            < ai.cost_micro_usd(usage, "claude-opus-5") / 10)
