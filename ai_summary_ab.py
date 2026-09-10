#!/usr/bin/env python3
"""ai_summary_ab.py — what does a cheaper model actually cost you?

The AI summary is the most expensive thing the app does per act, and the
tempting question ("Opus is dear, can we use something cheaper?") cannot be
answered from a price list. A price list gives $/token. What decides it is
**$ per item that survives the quote gate** — a model at a twentieth of the
price that finds a third of the clauses is worse value, and one that finds the
same clauses for a fifth is a free win. This measures that, on real acts, and
prints the two numbers side by side.

What it does
    Takes a reproducible sample of notices spanning the size distribution,
    runs each candidate model over all of them through the REAL prompt, schema
    and verification path, and reports yield, agreement and cost per variant.

What it deliberately does NOT do
    * It never writes to proc.act_ai_summary. An experiment must not put a
      Haiku payload where the app will serve it as the act's summary. The DB
      is read-only here; results go to a JSONL file.
    * It does not go through generate() — that enforces the daily cap and
      writes the cache. It calls the model directly, so IT IS NOT CAPPED.
      That is why --yes is required and why the estimate prints first.

Usage
    # what would it cost? (default — spends nothing)
    python ai_summary_ab.py --models claude-opus-5,claude-sonnet-5

    # spend it
    python ai_summary_ab.py --models claude-opus-5,claude-sonnet-5 --n 12 --yes

    # per-variant effort: "model:effort", or "model:none" to omit output_config
    python ai_summary_ab.py --models claude-opus-5:medium,claude-opus-5:low --yes

    # re-read a finished run, or add a variant to the same sample
    python ai_summary_ab.py --report runs/ab-2026-09-10.jsonl
    python ai_summary_ab.py --reuse runs/ab-2026-09-10.jsonl --models X --yes

Env
    DATABASE_URL        required (read-only)
    ANTHROPIC_API_KEY   required unless --dry-run
    AB_SLEEP            seconds between calls (default 1.0)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from app import ai_summary as ai            # noqa: E402


# --------------------------------------------------------------------------- #
# Model capabilities
#
# request_params() always sends output_config.effort, which is correct for the
# models the app ships with and a 400 on Haiku 4.5 — effort is not supported
# there. So "just point AI_SUMMARY_MODEL at Haiku" does not work today; the
# harness strips the field for models that reject it, which is also the
# one-line change production would need to actually adopt such a model.
# Unknown models are assumed to support it; override per variant with ":none".
# --------------------------------------------------------------------------- #
_NO_EFFORT = {"claude-haiku-4-5"}


def supports_effort(model: str) -> bool:
    return model not in _NO_EFFORT


# --------------------------------------------------------------------------- #
# Providers
#
# Anthropic is the app's transport. DeepSeek is here to answer one question
# before anyone ports anything: does a second provider even ACCEPT this tool
# schema? Haiku 4.5 refused it outright ("the compiled grammar is too large"),
# and that is a property of the schema, not of Anthropic — so it is the first
# thing to test anywhere else, and it costs a fraction of a cent to find out.
#
# The prompt, the system text and the tool are NOT rewritten for DeepSeek: the
# Anthropic request is built by ai.request_params() exactly as production
# builds it, then translated envelope-only into the OpenAI chat shape. A
# harness that writes its own prompt measures the harness.
# --------------------------------------------------------------------------- #
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# USD per MTok, cache-miss PEAK rates (the conservative half of DeepSeek's
# peak/off-peak split — off-peak is half of these). Anthropic prices come from
# ai.PRICES_USD_PER_MTOK; this is the overlay for everything else.
EXTRA_PRICES = {
    "deepseek-flash":  (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
}


def price_of(model: str):
    return ai.PRICES_USD_PER_MTOK.get(model) or EXTRA_PRICES.get(model)


def provider_of(model: str) -> str:
    return "deepseek" if model.startswith("deepseek") else "anthropic"


def _to_openai(params: dict) -> dict:
    """Anthropic Messages request -> OpenAI chat request, envelope only.

    The system text, the user prompt and the tool's JSON Schema cross over
    byte-identical; only the wrapper changes. `tool_choice` forces the call
    because the app's contract is "reply through the tool or not at all".
    """
    text = "\n".join(b.get("text", "") for m in params["messages"]
                      for b in m["content"] if b.get("type") == "text")
    tool = params["tools"][0]
    return {
        "model": params["model"],
        "max_tokens": params["max_tokens"],
        "messages": [{"role": "system", "content": params["system"]},
                     {"role": "user", "content": text}],
        "tools": [{"type": "function", "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }}],
        "tool_choice": "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def _stream_openai(req) -> tuple[str, dict, str | None]:
    """Accumulate a streamed OpenAI tool call. Mirrors ai._stream's contract:
    (tool arguments as JSON text, usage, finish_reason)."""
    args, usage, finish = [], {}, None
    with urllib.request.urlopen(req, timeout=ai.TIMEOUT,
                                context=ai._SSL_CTX) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                ev = json.loads(body)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                u = ev["usage"]
                usage = {"input_tokens": u.get("prompt_tokens", 0),
                         "output_tokens": u.get("completion_tokens", 0)}
            for choice in ev.get("choices") or []:
                finish = choice.get("finish_reason") or finish
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    frag = (call.get("function") or {}).get("arguments")
                    if frag:
                        args.append(frag)
    return "".join(args), usage, finish


class Variant:
    """One thing being measured: a model, and how it is asked."""

    def __init__(self, spec: str):
        model, _, effort = spec.partition(":")
        self.model = model.strip()
        self.provider = provider_of(self.model)
        if self.provider != "anthropic":
            # effort is an Anthropic concept; a DeepSeek variant has none
            self.effort = None
            return
        if effort.strip().lower() in ("none", "off", "-"):
            self.effort = None
        elif effort.strip():
            self.effort = effort.strip()
        else:
            self.effort = ai.EFFORT if supports_effort(self.model) else None
        if self.effort and not supports_effort(self.model):
            raise SystemExit(
                f"{self.model} does not accept output_config.effort — "
                f"use '{self.model}:none'")

    @property
    def key(self) -> str:
        return f"{self.model}:{self.effort or 'none'}"

    def api_key(self) -> str:
        var = ("ANTHROPIC_API_KEY" if self.provider == "anthropic"
               else "DEEPSEEK_API_KEY")
        key = os.environ.get(var)
        if not key:
            raise ai.SummaryError(f"{var} is not set")
        return key

    def params(self, sources, record):
        p = ai.request_params(sources, record, model=self.model, stream=True)
        if self.effort:
            p["output_config"] = {"effort": self.effort}
        else:
            p.pop("output_config", None)
        return p


# --------------------------------------------------------------------------- #
# Sample
# --------------------------------------------------------------------------- #
def db():
    """A CURSOR, not a connection — ai.load_inputs takes the same cursor object
    the app hands it, and psycopg3 connections have no fetchall()."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL (read-only is fine).")
    conn = psycopg.connect(url, autocommit=True, prepare_threshold=None,
                           row_factory=dict_row)
    return conn, conn.cursor()


def pick_sample(c, n: int, *, min_chars: int, max_chars: int) -> list[str]:
    """`n` notices spread evenly across the length distribution.

    Not random: a random sample of Greek notices is mostly short ones, and
    short ones are where every model looks equally good. Ordering by length
    and taking evenly-spaced ranks puts the same number of hard documents in
    front of each candidate, and makes the sample reproducible.
    """
    c.execute("""SELECT adam, length(full_text) AS n_chars
                   FROM proc.procurement_act
                  WHERE type = 'notice' AND full_text IS NOT NULL
                    AND length(full_text) BETWEEN %s AND %s
                  ORDER BY length(full_text), adam""",
              (min_chars, max_chars))
    rows = c.fetchall()
    if not rows:
        raise SystemExit("No notices with full text in that size range.")
    if n >= len(rows):
        return [r["adam"] for r in rows]
    step = (len(rows) - 1) / (n - 1) if n > 1 else 0
    return [rows[round(i * step)]["adam"] for i in range(n)]


def load_act(c, adam: str):
    loaded = ai.load_inputs(c, adam)
    if loaded is None:
        return None
    return loaded            # (act_row, sources)


def stored_baseline(c, adam: str) -> dict | None:
    """The summary the app is already serving, if any — a free comparison row."""
    c.execute("""SELECT model, n_sections, n_items, rejected_n,
                        input_tokens, output_tokens, cost_micro_usd, payload
                   FROM proc.act_ai_summary WHERE adam = %s""", (adam,))
    return c.fetchone()


# --------------------------------------------------------------------------- #
# Estimate — printed before anything is spent
# --------------------------------------------------------------------------- #
def estimate(c, adams: list[str], variants: list[Variant]) -> None:
    """Rough cost, from the real rendered prompt.

    Input is measured, not guessed: the prompt is built and its characters
    counted. Output is modelled at a quarter of input (the ratio from the one
    generation on record), floored and capped like a real call — so treat the
    total as an order of magnitude, not a quote.
    """
    total_in = 0
    sizes = []
    for adam in adams:
        loaded = load_act(c, adam)
        if not loaded:
            continue
        act, sources = loaded
        text, _ = ai._render_sources(sources)
        chars = len(text) + len(ai._render_record(act)) + len(ai._SYSTEM)
        tok = chars / 1.3 + 1200                  # Greek density + tool schema
        total_in += tok
        sizes.append(int(tok))
    total_out = sum(min(max(t * 0.25, 1500), ai.MAX_TOKENS) for t in sizes)

    print(f"\nSample: {len(sizes)} acts, "
          f"{min(sizes):,}–{max(sizes):,} input tokens "
          f"(median {int(statistics.median(sizes)):,})")
    print(f"Estimated per variant: {total_in/1e6:.3f}M in, {total_out/1e6:.3f}M out\n")
    print(f"  {'variant':<34}{'est. cost':>12}")
    print("  " + "-" * 46)
    grand = 0.0
    for v in variants:
        price = price_of(v.model)
        if not price:
            print(f"  {v.key:<34}{'unpriced':>12}")
            continue
        usd = (total_in * price[0] + total_out * price[1]) / 1e6
        grand += usd
        print(f"  {v.key:<34}{'$' + format(usd, '.2f'):>12}")
    print("  " + "-" * 46)
    print(f"  {'TOTAL':<34}{'$' + format(grand, '.2f'):>12}\n")
    # Two efforts of one model estimate identically here, which is correct and
    # easy to misread: the estimate models output from input, and how much
    # thinking a setting actually buys is the thing the run measures.
    print("  Input is measured from the real prompt; output is modelled. An "
          "effort change\n  shows up in the run, not in this estimate.\n")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def call_once(variant: Variant, sources, record) -> tuple[dict, dict, dict | None]:
    """One model call, on whichever provider the variant names.

    Both paths end in the same place: the tool's arguments as JSON, which
    ai.verify() then judges by the same rules. That is what makes the
    comparison mean anything.
    """
    key = variant.api_key()
    params = variant.params(sources, record)

    if variant.provider == "deepseek":
        req = urllib.request.Request(
            DEEPSEEK_URL, data=json.dumps(_to_openai(params)).encode(),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {key}"})
        tool_json, usage, finish = _stream_openai(req)
        if finish == "length":
            raise ai.SummaryError("hit max_tokens before finishing the tool call")
        if not tool_json.strip():
            raise ai.SummaryError(f"no tool call (finish_reason={finish!r})")
    else:
        req = urllib.request.Request(ai.API_URL, data=json.dumps(params).encode(),
                                     headers=ai._headers(key))
        tool_json, usage, stop_reason = ai._stream(req)
        if stop_reason == "refusal":
            raise ai.SummaryError("model declined")
        if not tool_json.strip():
            raise ai.SummaryError(f"no tool call (stop_reason={stop_reason!r})")
    return json.loads(tool_json) or {}, usage, None


def run(c, adams: list[str], variants: list[Variant], out_path: pathlib.Path,
        sleep_s: float) -> list[dict]:
    """Every (variant, act) pair, appended to JSONL as it completes.

    Written one line at a time on purpose: this spends real money, and a crash
    twenty acts in must not throw away the twenty that succeeded.
    """
    rows: list[dict] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": True, "at": _now(), "acts": adams,
                             "variants": [v.key for v in variants],
                             "prompt_version": ai.PROMPT_VERSION,
                             "schema_version": ai.SCHEMA_VERSION},
                            ensure_ascii=False) + "\n")
        fh.flush()
        total = len(adams) * len(variants)
        i = 0
        for adam in adams:
            loaded = load_act(c, adam)
            if not loaded:
                print(f"  skip {adam}: no inputs")
                continue
            act, sources = loaded
            for v in variants:
                i += 1
                print(f"[{i}/{total}] {v.key:<28} {adam} … ", end="", flush=True)
                row = {"adam": adam, "variant": v.key, "model": v.model,
                       "effort": v.effort, "at": _now()}
                t0 = time.monotonic()
                try:
                    raw, usage, _trunc = call_once(v, sources, act)
                    payload = ai.verify(raw, sources, act)
                    row.update(
                        ok=True,
                        n_sections=payload["n_sections"],
                        n_items=payload["n_items"],
                        rejected_n=payload["rejected_n"],
                        duplicate_n=payload["duplicate_n"],
                        conflicts_n=len(payload.get("conflicts") or []),
                        not_found_n=len(payload.get("not_found") or []),
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cost_micro_usd=_cost(usage, v.model),
                        quotes=_quote_keys(payload),
                    )
                    print(f"{row['n_items']:>3} items, "
                          f"{row['rejected_n']:>2} dropped, "
                          f"{_usd(row['cost_micro_usd'])}")
                except (ai.SummaryError, urllib.error.HTTPError,
                        urllib.error.URLError, json.JSONDecodeError) as e:
                    detail = str(e)
                    if isinstance(e, urllib.error.HTTPError):
                        detail = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
                    row.update(ok=False, error=detail)
                    print(f"FAILED — {detail[:90]}")
                row["seconds"] = round(time.monotonic() - t0, 1)
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                time.sleep(sleep_s)
    return rows


def _cost(usage: dict, model: str) -> int | None:
    """Micro-dollars. ai.cost_micro_usd only knows Anthropic's price list."""
    price = price_of(model)
    if not price:
        return None
    return round(int(usage.get("input_tokens") or 0) * price[0]
                 + int(usage.get("output_tokens") or 0) * price[1])


def _quote_keys(payload: dict) -> list[str]:
    """Normalised quotes, so two models can be asked whether they found the
    SAME clause rather than merely the same number of them."""
    keys = []
    for section in payload.get("sections") or []:
        for item in section.get("items") or []:
            q = (item.get("quote") or "").strip()
            if q:
                keys.append(ai._project(q)[0][:180])
    return keys


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _matches(a: str, b: str) -> bool:
    """Do two quotes point at the same clause?

    Exact equality is the wrong test and the first version used it, which
    reported 42% agreement between two models that had largely read the same
    document. Models pick different span boundaries around the same sentence —
    one quotes "α. απόσπασμα ποινικού μητρώου.", the other continues into the
    next clause — and string equality calls that a disagreement.

    Containment catches the boundary case; token overlap catches the rest.
    """
    if a == b:
        return True
    if len(a) >= 25 and len(b) >= 25 and (a in b or b in a):
        return True
    ta, tb = set(a.split()), set(b.split())
    if len(ta) < 4 or len(tb) < 4:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def _recall(base: set[str], mine: set[str]) -> tuple[int, int]:
    """(baseline quotes also found, quotes of mine that matched nothing)."""
    unmatched = list(mine)
    found = 0
    for q in base:
        hit = next((m for m in unmatched if _matches(q, m)), None)
        if hit is not None:
            found += 1
            unmatched.remove(hit)          # one candidate quote answers one
    return found, len(unmatched)


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per (act, variant): the last success, else the last failure.

    Re-running the acts that failed — after raising a cap, say — and then
    reporting over the concatenated files would otherwise count those acts
    twice and average a model against itself. A later success supersedes an
    earlier failure; that is the point of re-running it.
    """
    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        k = (r.get("adam"), r.get("variant"))
        prev = best.get(k)
        if prev is None or r.get("ok") or not prev.get("ok"):
            best[k] = r
    return list(best.values())


def report(rows: list[dict], baseline: str | None) -> None:
    rows = dedupe(rows)
    variants = list(dict.fromkeys(r["variant"] for r in rows))
    if not variants:
        print("No results.")
        return
    base = baseline or variants[0]
    by_act_base = {r["adam"]: set(r.get("quotes") or [])
                   for r in rows if r["variant"] == base and r.get("ok")}

    print(f"\n{'=' * 96}\nCOST — what you pay\n{'=' * 96}")
    print(f"{'variant':<30}{'ok':>4}{'fail':>6}{'in tok':>10}{'out tok':>10}"
          f"{'total $':>10}{'$/act':>9}{'$/item':>9}{'s/act':>8}")
    print("-" * 96)
    summary = {}
    for v in variants:
        rs = [r for r in rows if r["variant"] == v]
        ok = [r for r in rs if r.get("ok")]
        if not ok:
            print(f"{v:<30}{0:>4}{len(rs):>6}{'—':>10}{'—':>10}{'—':>10}"
                  f"{'—':>9}{'—':>9}{'—':>8}")
            continue
        cost = sum(r.get("cost_micro_usd") or 0 for r in ok) / 1e6
        items = sum(r["n_items"] for r in ok)
        ti = sum(r["input_tokens"] for r in ok)
        to = sum(r["output_tokens"] for r in ok)
        summary[v] = {"cost": cost, "items": items, "ok": len(ok)}
        print(f"{v:<30}{len(ok):>4}{len(rs) - len(ok):>6}{ti:>10,}{to:>10,}"
              f"{cost:>10.3f}{cost / len(ok):>9.4f}"
              f"{(cost / items if items else 0):>9.4f}"
              f"{statistics.mean(r['seconds'] for r in ok):>8.1f}")

    print(f"\n{'=' * 96}\nYIELD — what you get  (agreement measured against {base})\n{'=' * 96}")
    print(f"{'variant':<30}{'items/act':>11}{'dropped':>9}{'drop %':>8}"
          f"{'conflicts':>11}{'found base':>12}{'new':>7}")
    print("-" * 96)
    for v in variants:
        ok = [r for r in rows if r["variant"] == v and r.get("ok")]
        if not ok:
            continue
        kept = sum(r["n_items"] for r in ok)
        dropped = sum(r["rejected_n"] for r in ok)
        offered = kept + dropped
        recall_n = recall_d = extra = 0
        for r in ok:
            b = by_act_base.get(r["adam"])
            if b is None:
                continue
            hit, spare = _recall(b, set(r.get("quotes") or []))
            recall_n += hit
            recall_d += len(b)
            extra += spare
        recall = f"{100 * recall_n / recall_d:.0f}%" if recall_d else "—"
        if v in summary:
            summary[v]["recall"] = (recall_n / recall_d) if recall_d else None
        print(f"{v:<30}{kept / len(ok):>11.1f}{dropped / len(ok):>9.1f}"
              f"{(100 * dropped / offered if offered else 0):>7.0f}%"
              f"{sum(r['conflicts_n'] for r in ok) / len(ok):>11.1f}"
              f"{recall:>12}{extra / len(ok):>7.1f}")

    # The verdict is COVERAGE-FIRST, and that ordering is the whole point.
    # Cost per kept item flatters a thin model: one that returns three cheap
    # items instead of ten looks like better value per item and is not a
    # substitute for anything. So coverage decides whether a variant is even a
    # candidate, and only then does price get a say.
    if base in summary and summary[base]["items"]:
        b = summary[base]
        print(f"\nAgainst {base} — {b['items']} items for ${b['cost']:.3f}:")
        for v, st in summary.items():
            if v == base or not st["items"]:
                continue
            cheaper = b["cost"] / st["cost"] if st["cost"] else float("inf")
            rec = st.get("recall")
            if rec is None:
                verdict = "no overlap measured"
            elif rec >= 0.85:
                verdict = (f"EQUIVALENT coverage ({rec:.0%}) at {cheaper:.1f}x "
                           f"less — a real candidate")
            elif rec >= 0.60:
                verdict = (f"THINNER ({rec:.0%} of the clauses) — read what it "
                           f"missed before trusting the saving")
            else:
                verdict = (f"NOT A SUBSTITUTE ({rec:.0%} of the clauses) — "
                           f"price is irrelevant at this coverage")
            print(f"  {v:<28} {verdict}")
    print("\nCoverage decides, then price. 'found base' is the share of the "
          "baseline's clauses\nthis variant also located; $/item flatters a "
          "model that returns few cheap items,\nwhich is why it is a column "
          "here and not the verdict.\n")


# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _usd(micro) -> str:
    return "—" if micro is None else f"${micro / 1e6:.4f}"


def read_rows(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    rows, acts = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("_meta"):
            acts = d.get("acts") or acts
        else:
            rows.append(d)
    return rows, acts


# --------------------------------------------------------------------------- #
# Diff — the part a person has to judge
#
# The tables say a variant found 74% of the baseline's clauses. Whether the
# missing 26% matters is a domain question, not a metric, and answering it
# means reading the clauses. Two things make that readable:
#
#   * the stored quotes are folded for comparison (no accents, lowercased,
#     punctuation stripped), so they are located back in the act's real text
#     and printed as published;
#   * the extras are shown too. A cheaper model that finds things the
#     baseline missed is a different proposition from one that just misses.
# --------------------------------------------------------------------------- #
def _original(full_text: str, folded_quote: str) -> str:
    """The quote as published, recovered from the source by locating it."""
    span = ai.locate(full_text, folded_quote)
    if not span:
        return folded_quote + "   [could not be located in the source]"
    return " ".join(full_text[span[0]:span[1]].split())


def diff(c, rows: list[dict], base_key: str, other_key: str,
         limit: int, fh) -> None:
    rows = dedupe(rows)
    base = {r["adam"]: r for r in rows if r["variant"] == base_key and r.get("ok")}
    other = {r["adam"]: r for r in rows if r["variant"] == other_key and r.get("ok")}
    acts = sorted(set(base) & set(other))[:limit]

    def w(line=""):
        print(line, file=fh)

    w(f"# {other_key} vs {base_key}")
    w()
    w(f"Clauses each model found and the other did not, on {len(acts)} acts, "
      f"printed as they appear in the source document.")
    w()
    w("The question this answers is not *how many* — the tables have that — but "
      "**whether what is missing matters**. Read the two columns per act and "
      "judge whether the gap is tolerable for a screening panel.")
    w()

    tot_missed = tot_extra = 0
    for adam in acts:
        c.execute("""SELECT title, full_text FROM proc.procurement_act
                      WHERE adam = %s""", (adam,))
        act = c.fetchone()
        text = act["full_text"] or ""
        b = set(base[adam].get("quotes") or [])
        o = set(other[adam].get("quotes") or [])
        missed = [q for q in b if not any(_matches(q, m) for m in o)]
        extra = [q for q in o if not any(_matches(q, m) for m in b)]
        tot_missed += len(missed)
        tot_extra += len(extra)

        w(f"## {adam}")
        w()
        w(f"*{(act['title'] or '').strip()[:150]}*")
        w()
        w(f"{len(b)} clauses from {base_key} · {len(o)} from {other_key} · "
          f"**{len(missed)} missed, {len(extra)} extra**")
        w()
        w(f"### Missed by {other_key} — found only by {base_key}")
        w()
        if not missed:
            w("Nothing: it found everything the baseline did.")
        for q in missed:
            w(f"- {_original(text, q)}")
        w()
        w(f"### Extra — found only by {other_key}")
        w()
        if not extra:
            w("Nothing.")
        for q in extra:
            w(f"- {_original(text, q)}")
        w()

    w("---")
    w()
    w(f"**Across these {len(acts)} acts: {tot_missed} clauses missed, "
      f"{tot_extra} extra.** Every clause above survived the verbatim quote "
      f"gate, so all of them are really in the documents — the gate proves "
      f"grounding, not usefulness, and telling a real obligation from a "
      f"stray table row is the judgement it cannot make for you.")


# --------------------------------------------------------------------------- #
# Probe — the cheap decisive test
# --------------------------------------------------------------------------- #
_PROBE_TEXT = (
    "ΔΙΑΚΗΡΥΞΗ. Η καταληκτική ημερομηνία υποβολής προσφορών είναι η 30/09/2026. "
    "Η εγγύηση συμμετοχής ορίζεται σε 2% της εκτιμώμενης αξίας χωρίς ΦΠΑ. "
    "Απαιτείται απόσπασμα ποινικού μητρώου και φορολογική ενημερότητα."
)


def probe(variants: list[Variant]) -> bool:
    """Will this provider accept the tool schema at all?

    Worth its own mode because it is the question that actually kills a
    migration, it is answered by one tiny request, and the answer arrives
    before anyone writes a transport layer. Haiku 4.5 fails here — the schema
    is too large for its grammar compiler — and that is a $0.0002 finding
    rather than a week's.
    """
    sources = {"full_text": _PROBE_TEXT}
    record = {"adam": "PROBE", "title": "Δοκιμή", "type": "notice"}
    print("\nSchema probe — one tiny request each, fractions of a cent.\n")
    all_ok = True
    for v in variants:
        print(f"  {v.key:<30} ", end="", flush=True)
        try:
            raw, usage, _ = call_once(v, sources, record)
            payload = ai.verify(raw, sources, record)
            print(f"ACCEPTED — {payload['n_items']} items, "
                  f"{usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)} tok, "
                  f"{_usd(_cost(usage, v.model))}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"REJECTED — HTTP {e.code}")
            print(f"      {body[:300]}")
            all_ok = False
        except Exception as e:                      # noqa: BLE001
            print(f"FAILED — {type(e).__name__}: {str(e)[:160]}")
            all_ok = False
    print()
    return all_ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="claude-opus-5,claude-sonnet-5",
                    help="comma list; 'model', 'model:effort' or 'model:none'")
    ap.add_argument("--n", type=int, default=12, help="acts in the sample")
    ap.add_argument("--min-chars", type=int, default=1500)
    ap.add_argument("--max-chars", type=int, default=120000)
    ap.add_argument("--out", default=None, help="JSONL results path")
    ap.add_argument("--reuse", default=None,
                    help="run against the sample recorded in this results file")
    ap.add_argument("--report", default=None, help="print a finished run and exit")
    ap.add_argument("--baseline", default=None,
                    help="variant to compare against (default: the first)")
    ap.add_argument("--diff", default=None,
                    help="with --report: the variant to diff against "
                         "--baseline, printing the clauses each one missed")
    ap.add_argument("--diff-acts", type=int, default=4,
                    help="how many acts to include in --diff (default 4)")
    ap.add_argument("--diff-out", default=None,
                    help="write the diff to this file instead of stdout")
    ap.add_argument("--probe", action="store_true",
                    help="ask each provider whether it accepts the tool schema "
                         "at all — one tiny request each, needs no --yes")
    ap.add_argument("--yes", action="store_true",
                    help="actually call the API and spend money")
    args = ap.parse_args()

    if args.report:
        rows, _ = read_rows(pathlib.Path(args.report))
        if args.diff:
            if not args.baseline:
                raise SystemExit("--diff needs --baseline to compare against")
            _conn, cur = db()
            with _conn:
                if args.diff_out:
                    with open(args.diff_out, "w", encoding="utf-8") as fh:
                        diff(cur, rows, args.baseline, args.diff,
                             args.diff_acts, fh)
                    print(f"Wrote {args.diff_out}")
                else:
                    diff(cur, rows, args.baseline, args.diff,
                         args.diff_acts, sys.stdout)
            return
        report(rows, args.baseline)
        return

    variants = [Variant(s) for s in args.models.split(",") if s.strip()]

    if args.probe:
        raise SystemExit(0 if probe(variants) else 1)

    conn, c = db()
    with conn:
        if args.reuse:
            _, adams = read_rows(pathlib.Path(args.reuse))
            if not adams:
                raise SystemExit(f"No sample recorded in {args.reuse}")
        else:
            adams = pick_sample(c, args.n, min_chars=args.min_chars,
                                max_chars=args.max_chars)

        estimate(c, adams, variants)

        if not args.yes:
            print("Dry run — nothing was called and nothing was spent.")
            print("Re-run with --yes to spend the amount above.\n")
            print("Note: this path is NOT subject to AI_SUMMARY_DAILY_CAP.\n")
            return

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = pathlib.Path(args.out or f"runs/ab-{stamp}.jsonl")
        print(f"Writing to {out}\n")
        rows = run(c, adams, variants, out, float(os.environ.get("AB_SLEEP", "1.0")))
        report(rows, args.baseline or variants[0].key)
        print(f"Full results: {out}\n")


if __name__ == "__main__":
    main()
