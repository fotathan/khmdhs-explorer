#!/usr/bin/env python3
"""cpv_notes_gen.py — write one Greek + English paragraph per CPV code, locally,
then ship the finished rows to production.

docs/specs/public-detail-pages.md slice 3 (app/cpv_notes.py holds the prompt,
the checks and the storage). Every step that SPENDS money needs --yes; the
default is a dry run that prints what it would do.

    python cpv_notes_gen.py plan                 # how many codes need a note; no spend
    python cpv_notes_gen.py plan --show 50220000-3   # the exact request for one code

    python cpv_notes_gen.py sample --n 40 --yes  # immediate calls, full price, for review
    python cpv_notes_gen.py review               # write runs/cpv_notes_review.html

    python cpv_notes_gen.py submit --yes         # ONE batch with every code still missing
    python cpv_notes_gen.py submit --yes --chunk 1900   # …or several smaller ones
    python cpv_notes_gen.py cancel <batch_id>    # stop one; finished requests stay
    python cpv_notes_gen.py collect              # store the results of ended batches
                                             # (re-run until it says nothing is open)

    python cpv_notes_gen.py hide 50220000-3 --reason "…"   # take one note down
    python cpv_notes_gen.py push --to "$PROD_URL"           # copy the rows to prod

DATABASE_URL is the source (local). ANTHROPIC_API_KEY must be set for sample
and submit/collect. Resumable everywhere: a code with a current note (same
input_hash) is never paid for twice.
"""
from __future__ import annotations

import argparse
import html
import os
import pathlib
import random
import sys

import psycopg
from psycopg.rows import dict_row

from app import cpv_notes as cn

RUNS = pathlib.Path(__file__).resolve().parent / "runs"


def _connect(dsn: str):
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def _context(c):
    codes = cn.load_codes(c)
    parent, children = cn.build_tree(codes)
    return codes, parent, children


def _pending(c, codes, parent, children) -> list[tuple[str, dict, str]]:
    """(code, params, input_hash) for every code without a CURRENT note."""
    have = cn.current_hashes(c)
    out = []
    for code in sorted(codes):
        params = cn.request_params(code, codes, parent, children)
        h = cn.input_hash(params)
        if have.get(code) != h:
            out.append((code, params, h))
    return out


def _usd(micro) -> str:
    return f"${float(micro or 0) / 1e6:,.2f}"


# --------------------------------------------------------------------------- #
def cmd_plan(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        codes, parent, children = _context(c)
        if args.show:
            if args.show not in codes:
                sys.exit(f"{args.show} is not an official CPV code")
            p = cn.request_params(args.show, codes, parent, children)
            print("SYSTEM:\n" + p["system"] + "\n\nUSER:\n" + p["messages"][0]["content"])
            return
        pending = _pending(c, codes, parent, children)
        c.execute("SELECT count(*) AS n, sum(cost_micro_usd) AS cost, "
                  "count(*) FILTER (WHERE hidden_at IS NOT NULL) AS hidden "
                  "FROM proc.cpv_note")
        have = c.fetchone()
        c.execute("SELECT avg(cost_micro_usd) AS per FROM proc.cpv_note WHERE batch_id IS NULL")
        per = (c.fetchone() or {}).get("per")
    by_level: dict[int, int] = {}
    for code, _p, _h in pending:
        by_level[cn.level(code)] = by_level.get(cn.level(code), 0) + 1
    print(f"official codes: {len(codes):,}")
    print(f"notes stored:   {have['n']:,} ({have['hidden']} hidden), spent {_usd(have['cost'])}")
    print(f"still needed:   {len(pending):,}  by level {dict(sorted(by_level.items()))}")
    if per:
        est = float(per) * len(pending) / 2        # batch = half the immediate price
        print(f"batch estimate: ~{_usd(est)} (measured {_usd(per)} per code at full price)")
    else:
        print("batch estimate: run `sample` first — it measures the cost per code")


def cmd_sample(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        codes, parent, children = _context(c)
        pending = _pending(c, codes, parent, children)
        rng = random.Random(args.seed)
        # Stratified: every level is represented, so the review sees broad
        # divisions and the narrowest codes alike.
        by_level: dict[int, list] = {}
        for item in pending:
            by_level.setdefault(cn.level(item[0]), []).append(item)
        picks = []
        for lvl in sorted(by_level):
            items = by_level[lvl]
            picks += rng.sample(items, min(len(items), max(1, args.n // len(by_level))))
        picks = picks[: args.n]
        print(f"{len(picks)} codes, immediate calls at FULL price, model {cn.MODEL}")
        if not args.yes:
            print("dry run — add --yes to spend"); return
        spent, failed = 0, []
        for i, (code, params, h) in enumerate(picks, 1):
            try:
                msg = cn.call_one(params)
            except Exception as e:      # noqa: BLE001 — report and continue
                failed.append((code, str(e)[:120])); continue
            reply, err = cn.parse_message(msg)
            problems = [err] if err else cn.validate(
                code, reply["el"], reply["en"],
                allowed=cn.allowed_numbers(params["messages"][0]["content"]))
            if problems:
                failed.append((code, "; ".join(problems))); continue
            usage = msg.get("usage") or {}
            cn.store(c, code, reply, ihash=h, usage=usage, batch_id=None)
            spent += cn.cost_micro_usd(usage, batch=False)
            print(f"  {i:>3}/{len(picks)} {code} ok  {usage.get('input_tokens')}/{usage.get('output_tokens')} tok")
    print(f"stored {len(picks) - len(failed)}, failed {len(failed)}, spent {_usd(spent)}")
    for code, why in failed:
        print(f"  FAILED {code}: {why}")
    print("next: python cpv_notes_gen.py review   (then plan, for the full-run estimate)")


def cmd_review(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        codes, parent, _children = _context(c)
        c.execute("""SELECT n.cpv_code, n.text_el, n.text_en, n.hidden_at
                     FROM proc.cpv_note n ORDER BY random() LIMIT %s""", (args.n,))
        rows = sorted(c.fetchall(), key=lambda r: r["cpv_code"])
    RUNS.mkdir(exist_ok=True)
    out = RUNS / "cpv_notes_review.html"
    parts = ["<!doctype html><meta charset=utf-8><title>CPV notes — review</title>",
             "<style>body{font:15px/1.55 system-ui;max-width:860px;margin:24px auto;padding:0 16px}"
             "h2{font-size:15px;margin:28px 0 4px}.c{color:#777;font:12px monospace}"
             ".chain{color:#777;font-size:12.5px}p{margin:6px 0}.en{color:#444}"
             ".hid{color:#b41034;font-weight:600}</style>",
             f"<h1>CPV notes — {len(rows)} for review</h1>"]
    for r in rows:
        code = r["cpv_code"]
        chain = " › ".join(codes[a]["el"] for a in cn.ancestors(code, parent))
        parts.append(f"<h2>{html.escape(codes[code]['el'])} <span class=c>{code}</span>"
                     + (" <span class=hid>HIDDEN</span>" if r["hidden_at"] else "") + "</h2>")
        if chain:
            parts.append(f"<div class=chain>{html.escape(chain)}</div>")
        parts.append(f"<p>{html.escape(r['text_el'])}</p><p class=en>{html.escape(r['text_en'])}</p>")
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} notes)")


def cmd_submit(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        codes, parent, children = _context(c)
        c.execute("SELECT count(*) AS n FROM proc.cpv_note_batch WHERE collected_at IS NULL")
        if c.fetchone()["n"] and not args.force:
            sys.exit("a batch is still open — run `collect` first (or --force)")
        pending = _pending(c, codes, parent, children)
        if args.limit:
            pending = pending[: args.limit]
        n_batches = -(-len(pending) // args.chunk) if args.chunk else 1
        print(f"{len(pending):,} codes → {n_batches} batch(es), model {cn.MODEL}")
        if not pending:
            return
        if not args.yes:
            print("dry run — add --yes to spend (see `plan` for the estimate)"); return
        size = args.chunk or len(pending)
        ids = []
        for i in range(0, len(pending), size):
            part = pending[i:i + size]
            bid = cn.submit_batch([{"custom_id": cn.custom_id(code), "params": params}
                                   for code, params, _h in part])
            c.execute("""INSERT INTO proc.cpv_note_batch (batch_id, model, prompt_version, n_requests)
                         VALUES (%s, %s, %s, %s)""", (bid, cn.MODEL, cn.PROMPT_VERSION, len(part)))
            ids.append((bid, len(part)))
            print(f"submitted batch {bid} ({len(part):,} codes)")
    print(f"{len(ids)} batch(es). Most finish within an hour; run `collect` to store.")


def cmd_collect(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        codes, parent, children = _context(c)
        c.execute("SELECT batch_id FROM proc.cpv_note_batch WHERE collected_at IS NULL "
                  "ORDER BY submitted_at")
        open_ = [r["batch_id"] for r in c.fetchall()]
        if not open_:
            print("no open batches"); return
        for bid in open_:
            info = cn._ai.batch_status(bid)
            status = info.get("processing_status")
            if status != "ended":
                counts = info.get("request_counts") or {}
                print(f"{bid}: {status} — {counts}"); continue
            stored, failed, spent = 0, [], 0
            for cid, kind, payload in cn.batch_results(bid):
                code = cn.code_of(cid or "")
                if code not in codes:
                    failed.append((cid, "unknown code")); continue
                if kind != "succeeded":
                    failed.append((code, f"{kind}: {str(payload)[:100]}")); continue
                # The hash is recomputed from TODAY's context: a code renamed
                # since submission stays stale and is regenerated next time.
                params = cn.request_params(code, codes, parent, children)
                reply, err = cn.parse_message(payload)
                problems = [err] if err else cn.validate(
                    code, reply["el"], reply["en"],
                    allowed=cn.allowed_numbers(params["messages"][0]["content"]))
                if problems:
                    failed.append((code, "; ".join(problems))); continue
                usage = payload.get("usage") or {}
                cn.store(c, code, reply, ihash=cn.input_hash(params), usage=usage, batch_id=bid)
                stored += 1
                spent += cn.cost_micro_usd(usage, batch=True)
            c.execute("""UPDATE proc.cpv_note_batch SET collected_at = now(),
                           n_stored = %s, n_failed = %s WHERE batch_id = %s""",
                      (stored, len(failed), bid))
            print(f"{bid}: stored {stored:,}, failed {len(failed)}, spent {_usd(spent)}")
            for code, why in failed[:40]:
                print(f"  FAILED {code}: {why}")
            if failed:
                print("  (failed codes stay pending: `submit --yes` retries just those)")


def cmd_cancel(args) -> None:
    """Cancel an open batch. Finished requests stay (collect stores them);
    the rest are not billed. Once it has ended, `collect` closes it here."""
    info = cn.cancel_batch(args.batch_id)
    print(f"{args.batch_id}: {info.get('processing_status')} — {info.get('request_counts')}")
    print("run `collect` once it reports ended, then `submit --yes` for what is left")


def cmd_hide(args) -> None:
    with _connect(args.db) as conn, conn.cursor() as c:
        c.execute("""UPDATE proc.cpv_note SET hidden_at = now(), hidden_reason = %s
                     WHERE cpv_code = %s""", (args.reason, args.code))
        print("hidden" if c.rowcount else f"no note for {args.code}")


def cmd_push(args) -> None:
    if not args.to:
        sys.exit("--to <target DATABASE_URL> is required")
    if args.to == args.db:
        sys.exit("target is the source database — nothing to push")
    cols = ("cpv_code, text_el, text_en, model, prompt_version, input_hash, input_tokens, "
            "output_tokens, cost_micro_usd, batch_id, generated_at, hidden_at, hidden_reason")
    with _connect(args.db) as src, psycopg.connect(args.to) as dst:
        rows = src.execute(f"SELECT {cols} FROM proc.cpv_note").fetchall()
        with dst.transaction(), dst.cursor() as cur:
            cur.execute("DELETE FROM proc.cpv_note")
            with cur.copy(f"COPY proc.cpv_note ({cols}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row([r[k.strip()] for k in cols.split(",")])
    print(f"pushed {len(rows):,} notes (replaces the target's table)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL"),
                    help="source database (default: $DATABASE_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan"); sp.add_argument("--show"); sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("sample")
    sp.add_argument("--n", type=int, default=40)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_sample)
    sp = sub.add_parser("review"); sp.add_argument("--n", type=int, default=60)
    sp.set_defaults(fn=cmd_review)
    sp = sub.add_parser("submit")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--chunk", type=int, help="codes per batch (default: one batch)")
    sp.set_defaults(fn=cmd_submit)
    sp = sub.add_parser("cancel", help="cancel an open batch")
    sp.add_argument("batch_id")
    sp.set_defaults(fn=cmd_cancel)
    sp = sub.add_parser("collect"); sp.set_defaults(fn=cmd_collect)
    sp = sub.add_parser("hide"); sp.add_argument("code"); sp.add_argument("--reason", required=True)
    sp.set_defaults(fn=cmd_hide)
    sp = sub.add_parser("push"); sp.add_argument("--to"); sp.set_defaults(fn=cmd_push)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
