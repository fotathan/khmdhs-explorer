"""The model A/B harness (ai_summary_ab.py).

This script spends real money and its output decides a model migration, so the
things worth testing are the ones that would make it lie: a request shape that
silently 400s, a sample that quietly stops being reproducible, and a verdict
that recommends a model which does not do the job.

That last one is not hypothetical — the first version of the report called a
variant finding 30% of the clauses "better value per item", because cost per
kept item flatters a model that returns few cheap items. The verdict is now
coverage-first, and `test_verdict_is_coverage_first` is what keeps it that way.
"""
from __future__ import annotations

import json
import sys

import pytest

import ai_summary_ab as ab
from app import ai_summary as ai


# --------------------------------------------------------------------------- #
# Variants — the request shape per model
# --------------------------------------------------------------------------- #
def test_variant_defaults_to_the_app_effort():
    v = ab.Variant("claude-opus-5")
    assert v.model == "claude-opus-5"
    assert v.effort == ai.EFFORT
    assert v.key == f"claude-opus-5:{ai.EFFORT}"


@pytest.mark.parametrize("spec,expected", [
    ("claude-opus-5:low", "low"),
    ("claude-opus-5:max", "max"),
    ("claude-sonnet-5:medium", "medium"),
])
def test_variant_explicit_effort(spec, expected):
    assert ab.Variant(spec).effort == expected


@pytest.mark.parametrize("spec", ["claude-haiku-4-5:none", "claude-haiku-4-5:off",
                                  "claude-haiku-4-5:-", "claude-haiku-4-5"])
def test_haiku_defaults_to_no_effort(spec):
    """Haiku 4.5 rejects output_config.effort. Asking for it is a 400, so the
    harness must not construct that request by accident."""
    assert ab.Variant(spec).effort is None


def test_haiku_with_an_explicit_effort_is_refused_up_front():
    """Better a clear error before the run than a 400 per act, halfway in."""
    with pytest.raises(SystemExit, match="does not accept"):
        ab.Variant("claude-haiku-4-5:medium")


def test_params_carry_effort_only_where_it_is_supported():
    sources = {"full_text": "ΔΙΑΚΗΡΥΞΗ. Η εγγύηση συμμετοχής ορίζεται σε 2%."}
    record = {"adam": "X", "title": "Δοκιμή", "type": "notice"}

    opus = ab.Variant("claude-opus-5:low").params(sources, record)
    assert opus["output_config"] == {"effort": "low"}
    assert opus["model"] == "claude-opus-5"

    haiku = ab.Variant("claude-haiku-4-5").params(sources, record)
    assert "output_config" not in haiku
    assert haiku["model"] == "claude-haiku-4-5"


def test_params_are_otherwise_the_real_request():
    """The experiment is only worth anything if it measures the SAME prompt and
    schema the app uses — a harness with its own prompt measures the harness."""
    sources = {"full_text": "ΔΙΑΚΗΡΥΞΗ κάτι."}
    record = {"adam": "X", "title": "Δοκιμή", "type": "notice"}
    mine = ab.Variant("claude-opus-5").params(sources, record)
    theirs = ai.request_params(sources, record, model="claude-opus-5", stream=True)
    assert mine["system"] == theirs["system"]
    assert mine["tools"] == theirs["tools"]
    assert mine["messages"] == theirs["messages"]
    assert mine["max_tokens"] == theirs["max_tokens"]


# --------------------------------------------------------------------------- #
# A second provider
#
# The point of the DeepSeek path is to answer "does anything else accept this
# tool schema", so what matters is that the request crossing over carries the
# SAME prompt, system text and schema. A harness that quietly rewrites the
# prompt for the challenger measures the harness, not the challenger.
# --------------------------------------------------------------------------- #
def test_provider_is_inferred_from_the_model():
    assert ab.provider_of("claude-opus-5") == "anthropic"
    assert ab.provider_of("deepseek-flash") == "deepseek"


def test_deepseek_variant_has_no_effort():
    """effort is an Anthropic concept; asking DeepSeek for it is meaningless."""
    v = ab.Variant("deepseek-flash")
    assert v.provider == "deepseek" and v.effort is None
    assert ab.Variant("deepseek-flash:medium").effort is None


def test_deepseek_is_priced():
    assert ab.price_of("deepseek-flash") == (0.30, 1.20)
    assert ab.price_of("claude-opus-5") == ai.PRICES_USD_PER_MTOK["claude-opus-5"]
    assert ab.price_of("something-invented") is None


def test_cost_uses_the_overlay_for_non_anthropic_models():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert ab._cost(usage, "deepseek-flash") == round(0.30e6 + 1.20e6)
    assert ab._cost(usage, "something-invented") is None


def test_api_key_names_the_right_variable():
    with pytest.raises(ai.SummaryError, match="DEEPSEEK_API_KEY"):
        ab.Variant("deepseek-flash").api_key()


def test_openai_translation_is_envelope_only():
    sources = {"full_text": "ΔΙΑΚΗΡΥΞΗ. Η εγγύηση συμμετοχής ορίζεται σε 2%."}
    record = {"adam": "X", "title": "Δοκιμή", "type": "notice"}
    p = ab.Variant("deepseek-flash").params(sources, record)
    o = ab._to_openai(p)

    assert o["messages"][0]["content"] == p["system"]
    assert o["messages"][1]["content"] == p["messages"][0]["content"][0]["text"]
    assert o["tools"][0]["function"]["parameters"] == p["tools"][0]["input_schema"]
    assert o["tools"][0]["function"]["name"] == "record_summary"
    # "auto", matching production, which declares one tool and does not force
    # it — and which DeepSeek rejects in thinking mode if you do force it
    assert o["tool_choice"] == "auto"
    assert "tool_choice" not in p
    # nothing Anthropic-shaped leaks across
    assert "output_config" not in o and "system" not in o


def test_openai_stream_accumulates_a_tool_call(monkeypatch):
    """The arguments arrive as fragments across events, and the usage only in
    the last one — reassembling that wrongly would look like a model failure."""
    chunks = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"{\\"a\\":"}}]}}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"1}"}}]}}]}\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],'
        b'"usage":{"prompt_tokens":120,"completion_tokens":34}}\n',
        b'data: [DONE]\n',
    ]

    class _Resp:
        def __iter__(self):
            return iter(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ab.urllib.request, "urlopen", lambda *a, **k: _Resp())
    args, usage, finish = ab._stream_openai(object())
    assert json.loads(args) == {"a": 1}
    assert usage == {"input_tokens": 120, "output_tokens": 34}
    # translated into the Anthropic vocabulary by the app's reader, which this
    # harness now shares — the two paths must fail identically as well as
    # succeed identically, or a transport difference reads as a quality one
    assert finish == "tool_use"


def test_openai_stream_ignores_unparseable_events(monkeypatch):
    chunks = [b"event: ping\n", b"\n", b"data: not json\n", b"data: [DONE]\n"]

    class _Resp:
        def __iter__(self):
            return iter(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ab.urllib.request, "urlopen", lambda *a, **k: _Resp())
    args, usage, finish = ab._stream_openai(object())
    assert args == "" and usage == {} and finish is None


# --------------------------------------------------------------------------- #
# Sampling
# --------------------------------------------------------------------------- #
@pytest.fixture()
def notices(db):
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'ABS%'")
    for i in range(30):
        cur.execute("""INSERT INTO proc.procurement_act
                         (adam, type, title, origin, data_source, full_text)
                       VALUES (%s,'notice','Δοκιμή','import','khmdhs',%s)""",
                    (f"ABS{i:03d}", "α" * (2000 + i * 3000)))
    yield cur
    cur.execute("DELETE FROM proc.procurement_act WHERE adam LIKE 'ABS%'")


def test_sample_is_reproducible(notices):
    a = ab.pick_sample(notices, 8, min_chars=1500, max_chars=120000)
    b = ab.pick_sample(notices, 8, min_chars=1500, max_chars=120000)
    assert a == b and len(a) == 8


def test_sample_spans_the_size_range(notices):
    """A random sample of Greek notices is mostly short ones, and short ones
    are where every model looks equally good."""
    picked = ab.pick_sample(notices, 6, min_chars=1500, max_chars=120000)
    notices.execute("""SELECT adam, length(full_text) AS n FROM proc.procurement_act
                        WHERE adam = ANY(%s)""", (picked,))
    lengths = sorted(r["n"] for r in notices.fetchall())
    assert lengths[0] < 6000                    # the small end is represented
    assert lengths[-1] > 60000                  # so is the hard end
    assert len(set(picked)) == len(picked)      # no act measured twice


def test_sample_respects_the_size_bounds(notices):
    picked = ab.pick_sample(notices, 5, min_chars=20000, max_chars=40000)
    notices.execute("""SELECT length(full_text) AS n FROM proc.procurement_act
                        WHERE adam = ANY(%s)""", (picked,))
    assert all(20000 <= r["n"] <= 40000 for r in notices.fetchall())


def test_sample_smaller_than_n_returns_everything(notices):
    assert len(ab.pick_sample(notices, 500, min_chars=1500, max_chars=120000)) == 30


# --------------------------------------------------------------------------- #
# Quote identity — how "did they find the same clause" is decided
# --------------------------------------------------------------------------- #
def test_quote_keys_ignore_accents_case_and_whitespace():
    """Two models quoting the same sentence with different spacing or accents
    have found the same clause, and must count as agreement."""
    a = {"sections": [{"items": [{"quote": "Η ΕΓΓΥΗΣΗ   συμμετοχής"}]}]}
    b = {"sections": [{"items": [{"quote": "η εγγυηση συμμετοχης"}]}]}
    assert ab._quote_keys(a) == ab._quote_keys(b)


def test_quote_keys_skip_empty_quotes():
    payload = {"sections": [{"items": [{"quote": ""}, {"quote": "  "},
                                       {"quote": "πραγματικό"}]}]}
    assert len(ab._quote_keys(payload)) == 1


# --------------------------------------------------------------------------- #
# Quote matching — "did they find the same clause"
#
# The first version compared quotes with string equality and reported 42%
# agreement between two models that had largely read the same document. Models
# choose different span boundaries around one sentence; that is not a
# disagreement, and scoring it as one recommends against a viable model.
# --------------------------------------------------------------------------- #
def test_identical_quotes_match():
    assert ab._matches("η εγγυηση συμμετοχης οριζεται σε 2%",
                       "η εγγυηση συμμετοχης οριζεται σε 2%")


def test_a_longer_span_of_the_same_clause_matches():
    short = "η εκδοση ηλεκτρονικου τιμολογιου γινεται μεσω των παροχων"
    longer = short + " και υποβαλλεται εντος πεντε ημερων"
    assert ab._matches(short, longer) and ab._matches(longer, short)


def test_substantial_word_overlap_matches():
    assert ab._matches(
        "τα δικαιολογητικα δεν απαιτουνται σε δημοσιες συμβασεις με εκτιμωμενη αξια",
        "τα δικαιολογητικα δεν απαιτουνται σε συμβασεις με εκτιμωμενη αξια κατω")


def test_different_clauses_do_not_match():
    assert not ab._matches(
        "η εγγυηση συμμετοχης οριζεται σε δυο τοις εκατο της αξιας",
        "η καταληκτικη ημερομηνια υποβολης προσφορων ειναι η 30η Ιουνιου")


def test_short_fragments_need_exact_equality():
    """Containment on tiny strings would match everything that shares a word."""
    assert not ab._matches("φορολογικη ενημεροτητα", "ασφαλιστικη ενημεροτητα")


def test_recall_counts_each_candidate_quote_once():
    """One quote cannot answer for two different baseline clauses, or a model
    that repeats itself would score perfect coverage."""
    base = {"η εγγυηση συμμετοχης οριζεται σε δυο τοις εκατο της αξιας",
            "η εγγυηση καλης εκτελεσης οριζεται σε τεσσερα τοις εκατο"}
    found, spare = ab._recall(
        base, {"η εγγυηση συμμετοχης οριζεται σε δυο τοις εκατο της αξιας"})
    assert found == 1 and spare == 0


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _row(adam, variant, model, *, items, rejected, quotes, micro, out_tok=5000):
    return {"adam": adam, "variant": variant, "model": model, "effort": None,
            "ok": True, "n_sections": 5, "n_items": items, "rejected_n": rejected,
            "duplicate_n": 0, "conflicts_n": 0, "not_found_n": 0,
            "input_tokens": 20000, "output_tokens": out_tok,
            "cost_micro_usd": micro, "quotes": quotes, "seconds": 10.0}


def _thin_vs_rich():
    """A rich baseline, and a cheap variant that finds a third of the clauses
    — the shape that fooled the first version of the verdict."""
    rows = []
    for a in ("A", "B"):
        full = [f"quote {a} {j}" for j in range(9)]
        rows.append(_row(a, "base:medium", "claude-opus-5",
                         items=9, rejected=0, quotes=full, micro=225_000))
        rows.append(_row(a, "cheap:none", "claude-haiku-4-5",
                         items=3, rejected=6, quotes=full[:3], micro=35_000))
    return rows


def test_verdict_is_coverage_first(capsys):
    """A model finding a third of the clauses is not a cheaper option — it is
    a different, worse product, whatever the price says."""
    ab.report(_thin_vs_rich(), "base:medium")
    out = capsys.readouterr().out
    assert "NOT A SUBSTITUTE" in out
    assert "33%" in out
    assert "BETTER value" not in out


def test_verdict_recognises_an_equivalent_cheaper_model(capsys):
    rows = []
    for a in ("A", "B"):
        full = [f"quote {a} {j}" for j in range(10)]
        rows.append(_row(a, "base:medium", "claude-opus-5",
                         items=10, rejected=0, quotes=full, micro=225_000))
        rows.append(_row(a, "cheap:none", "claude-sonnet-5",
                         items=10, rejected=0, quotes=full, micro=90_000))
    ab.report(rows, "base:medium")
    out = capsys.readouterr().out
    assert "EQUIVALENT coverage (100%)" in out
    assert "2.5x less" in out


def test_report_shows_cost_and_yield_columns(capsys):
    ab.report(_thin_vs_rich(), "base:medium")
    out = capsys.readouterr().out
    assert "COST — what you pay" in out and "YIELD — what you get" in out
    assert "$/item" in out and "found base" in out
    assert "67%" in out           # the cheap variant's drop rate


def test_report_survives_a_variant_that_failed_everywhere(capsys):
    rows = _thin_vs_rich() + [
        {"adam": "A", "variant": "broken:none", "model": "x", "ok": False,
         "error": "HTTP 400", "seconds": 0.1}]
    ab.report(rows, "base:medium")
    out = capsys.readouterr().out
    assert "broken:none" in out          # reported, not crashed on


def test_a_rerun_supersedes_the_failure_it_replaces():
    """Re-running what failed, then reporting over both files, must not count
    the act twice — nor keep the failure that the re-run answered."""
    rows = [
        _row("A", "v", "m", items=5, rejected=0, quotes=["q"], micro=100),
        {"adam": "B", "variant": "v", "model": "m", "ok": False,
         "error": "hit max_tokens", "seconds": 1.0},
        _row("B", "v", "m", items=9, rejected=0, quotes=["q2"], micro=200),
    ]
    out = {(r["adam"], r["variant"]): r for r in ab.dedupe(rows)}
    assert len(out) == 2
    assert out[("B", "v")]["ok"] is True and out[("B", "v")]["n_items"] == 9


def test_dedupe_keeps_a_failure_when_there_is_no_success():
    rows = [{"adam": "A", "variant": "v", "model": "m", "ok": False,
             "error": "boom", "seconds": 1.0}]
    assert len(ab.dedupe(rows)) == 1


def test_report_on_an_empty_run(capsys):
    ab.report([], None)
    assert "No results." in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Diff — the part a person has to judge
#
# Whether a coverage gap matters is a domain question, so the only job here is
# to make the clauses readable: the stored quotes are folded for comparison
# (accents stripped, lowercased, punctuation removed) and unreadable as Greek,
# so they are located back in the source and printed as published.
# --------------------------------------------------------------------------- #
def test_original_recovers_the_published_wording():
    text = ("ΔΙΑΚΗΡΥΞΗ\n\nΗ εγγύηση συμμετοχής ορίζεται σε 2% της "
            "εκτιμώμενης αξίας, χωρίς Φ.Π.Α.")
    folded = ai._project("η εγγυηση συμμετοχης οριζεται σε 2%")[0]
    got = ab._original(text, folded)
    assert got == "Η εγγύηση συμμετοχής ορίζεται σε 2%"      # accents restored


def test_original_collapses_whitespace_from_the_source():
    text = "Η εγγύηση\n   συμμετοχής   ορίζεται"
    assert ab._original(text, ai._project("η εγγυηση συμμετοχης οριζεται")[0]) \
        == "Η εγγύηση συμμετοχής ορίζεται"


def test_original_says_so_when_it_cannot_locate_the_quote():
    """Silently printing the folded form would look like a transcription bug."""
    out = ab._original("κάτι εντελώς άλλο", "μια φραση που δεν υπαρχει")
    assert "could not be located" in out


def test_diff_reports_both_directions(db, capsys):
    """A cheaper model that finds things the baseline missed is a different
    proposition from one that only misses, so the extras are shown too."""
    cur = db.cursor()
    cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'DIFF001'")
    cur.execute("""INSERT INTO proc.procurement_act
                     (adam, type, title, origin, data_source, full_text)
                   VALUES ('DIFF001','notice','Δοκιμή','import','khmdhs',%s)""",
                (("Η εγγύηση συμμετοχής ορίζεται σε 2%. "
                  "Η διάρκεια της σύμβασης ορίζεται σε ένα έτος. "
                  "Απαιτείται φορολογική ενημερότητα."),))
    base_q = [ai._project("η εγγυηση συμμετοχης οριζεται σε 2%")[0],
              ai._project("η διαρκεια της συμβασης οριζεται σε ενα ετος")[0]]
    mine_q = [ai._project("η εγγυηση συμμετοχης οριζεται σε 2%")[0],
              ai._project("απαιτειται φορολογικη ενημεροτητα")[0]]
    rows = [_row("DIFF001", "base:medium", "m", items=2, rejected=0,
                 quotes=base_q, micro=100),
            _row("DIFF001", "cheap:none", "m2", items=2, rejected=0,
                 quotes=mine_q, micro=10)]
    try:
        ab.diff(cur, rows, "base:medium", "cheap:none", 4, sys.stdout)
        out = capsys.readouterr().out
        assert "Η διάρκεια της σύμβασης ορίζεται σε ένα έτος" in out   # missed
        assert "Απαιτείται φορολογική ενημερότητα" in out              # extra
        assert "1 missed, 1 extra" in out
    finally:
        cur.execute("DELETE FROM proc.procurement_act WHERE adam = 'DIFF001'")


# --------------------------------------------------------------------------- #
# Results file
# --------------------------------------------------------------------------- #
def test_read_rows_separates_the_sample_from_the_results(tmp_path):
    p = tmp_path / "run.jsonl"
    p.write_text("\n".join([
        json.dumps({"_meta": True, "acts": ["A", "B"], "variants": ["v"]}),
        json.dumps(_row("A", "v", "m", items=1, rejected=0, quotes=["q"], micro=1)),
    ]), encoding="utf-8")
    rows, acts = ab.read_rows(p)
    assert acts == ["A", "B"]
    assert len(rows) == 1 and rows[0]["adam"] == "A"


def test_read_rows_tolerates_blank_lines(tmp_path):
    p = tmp_path / "run.jsonl"
    p.write_text(json.dumps({"_meta": True, "acts": ["A"]}) + "\n\n", encoding="utf-8")
    rows, acts = ab.read_rows(p)
    assert acts == ["A"] and rows == []
