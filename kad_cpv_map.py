#!/usr/bin/env python3
"""kad_cpv_map.py — learn the ΚΑΔ→CPV mapping locally, ship it to production.

docs/specs/crm-brief-kad.md. The CRM brief estimates what a company with no
award history sells from its ΚΑΔ (app/kad_cpv.py). The mapping behind that is
learned from contractors whose ΚΑΔ we know (proc.gemi_enrichment, filled by
gemi_enrich.py) and what they actually win.

    # The CRM estimate works WITHOUT any of this: it matches the customer's
    # ΚΑΔ description against the CPV text (kad_cpv.describe). This tool is
    # for measuring that, and for the optional learned mapping:

    python kad_cpv_map.py evaluate          # description vs baseline vs learned

    # 1. optional, slow: fetch contractors' ΚΑΔ (ΓΕΜΗ rate-limits to roughly
    #    one call every 6-8 s; resumable — done rows are skipped)
    python gemi_enrich.py --scope suppliers --priority --limit 5000

    # 2. learn the mapping (local DB, a minute)
    python kad_cpv_map.py build
    python kad_cpv_map.py report            # what it learned, for a sanity check

    # 3. ship ONLY the result to prod (its free tier cannot hold the raw
    #    registry records the build reads). Migration 20261007120000 first.
    python kad_cpv_map.py push --to "$PROD_DATABASE_URL"

DATABASE_URL is the source (local). `push` refuses a target equal to it.
Re-run build + push whenever the enrichment has grown; both replace whole.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

from app import kad_cpv


def _connect(dsn: str):
    return psycopg.connect(dsn, autocommit=True, prepare_threshold=None,
                           row_factory=dict_row)


def cmd_build(args) -> None:
    with _connect(args.db) as conn:
        out = kad_cpv.build(conn, years=args.years, min_firms=args.min_firms,
                            min_support=args.min_support, min_lift=args.min_lift)
    print(f"enriched ΑΦΜ with a ΚΑΔ : {out['enriched']:,}")
    print(f"... found in the ledger  : {out['operators']:,}")
    print(f"... with an award in {args.years}y: {out['n_firms']:,}")
    print(f"(ΚΑΔ, CPV) pairs kept    : {out['n_pairs']:,}")


def cmd_report(args) -> None:
    """Coverage by ΚΑΔ depth, then a sample of ΚΑΔ with their top CPV groups."""
    with _connect(args.db) as conn:
        c = conn.cursor()
        b = kad_cpv.last_build(c)
        if not b:
            sys.exit("no build yet — run `kad_cpv_map.py build`")
        print(f"built {b['built_at']:%Y-%m-%d %H:%M} from {b['n_firms']:,} firms, "
              f"{b['n_pairs']:,} pairs, {b['window_years']}y window\n")
        c.execute("""SELECT length(kad_prefix) AS depth, count(DISTINCT kad_prefix) AS kads,
                            count(*) AS pairs
                       FROM proc.kad_cpv_map GROUP BY 1 ORDER BY 1 DESC""")
        for r in c.fetchall():
            print(f"  ΚΑΔ {r['depth']} digits: {r['kads']:>6,} ΚΑΔ, {r['pairs']:>8,} pairs")
        # Share of enriched contractors whose primary ΚΑΔ gets an estimate at
        # all — by the same level rule the CRM uses (kad_cpv.mapping_for).
        c.execute("SELECT kad, count(*) AS n FROM proc.operator_kad GROUP BY kad")
        per_kad = {r["kad"]: int(r["n"]) for r in c.fetchall()}
        mapped = kad_cpv.mapping_for(c, list(per_kad))
        total = sum(per_kad.values())
        covered = sum(n for k, n in per_kad.items() if mapped[k][0])
        if total:
            print(f"\n  contractors whose ΚΑΔ gets an estimate: "
                  f"{covered:,} / {total:,} ({100 * covered / total:.1f}%)")
        kads = sorted(per_kad, key=lambda k: -per_kad[k])[:args.sample]
        print(f"\n  the {len(kads)} commonest primary ΚΑΔ, top CPV groups:")
        for kad in kads:
            level, rows = mapped[kad]
            rows = sorted((r for r in rows if len(r["cpv_prefix"]) == 4),
                          key=lambda r: -r["support"])[:4]
            labels = kad_cpv.cpv_labels(c, [r["cpv_prefix"] for r in rows])
            print(f"   {kad} (via {level or '—'})")
            for r in rows:
                print(f"      {r['cpv_prefix']} {r['support']:>5.0%} of {r['kad_firms']:>4} "
                      f"lift {r['lift']:>5.1f}  {(labels.get(r['cpv_prefix']) or '')[:60]}")


def cmd_evaluate(args) -> None:
    """How well does a ΚΑΔ predict what a firm wins? Measured on every
    enriched contractor with at least --min-awards awards in the window: the
    top-k CPV groups each method predicts from the PRIMARY ΚΑΔ, against the
    groups the firm actually won. The naive baseline guesses the k most common
    groups for everyone; a method that cannot beat it is not worth showing.

      hit       — firms where at least one predicted group was won
      precision — share of predicted groups that were won
      coverage  — share of the firm's awards inside the predicted groups
    The learned mapping is scored on the very firms it was learned from, so
    its figures are optimistic — the description method is not.
    """
    import collections
    k = args.top
    with _connect(args.db) as conn:
        c = conn.cursor()
        c.execute("""
            WITH f AS (SELECT afm FROM proc.gemi_enrichment
                        WHERE fetch_status = 'ok' AND primary_kad IS NOT NULL),
                 o AS (SELECT f.afm, eo.operator_id FROM f
                         JOIN proc.economic_operator eo
                           ON eo.vat_number = ANY(ARRAY[f.afm, 'EL' || f.afm,
                                                        ltrim(f.afm, '0')]))
            SELECT o.afm, substr(oc.cpv_code, 1, 4) AS g, count(DISTINCT a.adam) AS n
              FROM o JOIN proc.act_operator ao ON ao.operator_id = o.operator_id
              JOIN proc.procurement_act a ON a.adam = ao.adam
              JOIN proc.act_object_detail od ON od.adam = a.adam
              JOIN proc.object_detail_cpv oc ON oc.object_detail_id = od.id
             WHERE a.type = ANY(%s) AND NOT coalesce(a.cancelled, false)
               AND a.signed_date >= now() - make_interval(years => %s)
             GROUP BY 1, 2""", (list(kad_cpv._AWARD_TYPES), args.years))
        won = collections.defaultdict(dict)
        for r in c.fetchall():
            won[r["afm"]][r["g"]] = int(r["n"])
        c.execute("""SELECT afm, primary_kad, primary_kad_descr FROM proc.gemi_enrichment
                      WHERE fetch_status = 'ok' AND primary_kad IS NOT NULL""")
        firms = [r for r in c.fetchall()
                 if sum(won[r["afm"]].values()) >= args.min_awards]
        if args.exclude:
            firms = [f for f in firms
                     if not kad_cpv.kad_digits(f["primary_kad"]).startswith(args.exclude)]
        if not firms:
            sys.exit("no enriched contractor with enough awards to measure on")
        popular = [g for g, _ in collections.Counter(
            g for f in firms for g in won[f["afm"]]).most_common(k)]

        def by_description(f):
            groups: dict[str, float] = {}
            for code, s in kad_cpv.describe(c, kad_cpv.kad_digits(f["primary_kad"]),
                                            f["primary_kad_descr"]).items():
                groups[code[:4]] = max(groups.get(code[:4], 0.0), s)
            return [g for g, _ in sorted(groups.items(), key=lambda x: (-x[1], x[0]))[:k]]

        kads = [kad_cpv.kad_digits(f["primary_kad"]) for f in firms]
        mapped = kad_cpv.mapping_for(c, kads) if kad_cpv.last_build(c) else {}

        def by_ledger(f):
            _lvl, rows = mapped.get(kad_cpv.kad_digits(f["primary_kad"]), (None, []))
            rows = sorted((r for r in rows if len(r["cpv_prefix"]) == 4),
                          key=lambda r: -r["support"])
            return [r["cpv_prefix"] for r in rows[:k]]

        print(f"{len(firms):,} contractors with ≥{args.min_awards} awards in "
              f"{args.years}y{' (excluding ΚΑΔ ' + args.exclude + '…)' if args.exclude else ''}; "
              f"top {k} CPV groups\n")
        print(f"  {'method':34s} {'answered':>9s} {'hit':>6s} {'precision':>10s} {'coverage':>9s}")
        for label, fn in (("baseline: most common groups", lambda f: popular),
                          ("ΚΑΔ description (default)", by_description),
                          ("learned mapping (in-sample)", by_ledger)):
            n = hit = prec = cov = 0
            for f in firms:
                pred = fn(f)
                if not pred:
                    continue
                w = won[f["afm"]]
                n += 1
                hit += any(g in w for g in pred)
                prec += sum(g in w for g in pred) / len(pred)
                cov += sum(w.get(g, 0) for g in pred) / sum(w.values())
            if not n:
                print(f"  {label:34s} {'—':>9s}")
                continue
            print(f"  {label:34s} {n:>9,} {hit / n:>6.0%} {prec / n:>10.0%} {cov / n:>9.0%}")


def cmd_push(args) -> None:
    if not args.to:
        sys.exit("--to <target DATABASE_URL> is required")
    if args.to == args.db:
        sys.exit("target is the source database — nothing to push")
    with _connect(args.db) as src, _connect(args.to) as dst:
        out = kad_cpv.push(src, dst)
    print(f"pushed {out['pairs']:,} pairs and {out['operators']:,} contractors "
          f"(build of {out['built_at']:%Y-%m-%d %H:%M})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL"),
                    help="source database (default: $DATABASE_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("build", help="learn the mapping from the local ledger")
    sp.add_argument("--years", type=int, default=kad_cpv.LEARN_YEARS)
    sp.add_argument("--min-firms", type=int, default=kad_cpv.MIN_FIRMS)
    sp.add_argument("--min-support", type=float, default=kad_cpv.MIN_SUPPORT)
    sp.add_argument("--min-lift", type=float, default=kad_cpv.MIN_LIFT)
    sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("report", help="print what the last build learned")
    sp.add_argument("--sample", type=int, default=12)
    sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("evaluate", help="measure how well a ΚΑΔ predicts what firms win")
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--years", type=int, default=kad_cpv.LEARN_YEARS)
    sp.add_argument("--min-awards", type=int, default=5)
    sp.add_argument("--exclude", default="",
                    help="leave out ΚΑΔ starting with this (e.g. 4646, to see past "
                         "a sample dominated by one trade)")
    sp.set_defaults(fn=cmd_evaluate)
    sp = sub.add_parser("push", help="copy the built tables to another database")
    sp.add_argument("--to", default=None, help="target DATABASE_URL (production)")
    sp.set_defaults(fn=cmd_push)
    args = ap.parse_args()
    if not args.db:
        sys.exit("DATABASE_URL not set (or pass --db)")
    args.fn(args)


if __name__ == "__main__":
    main()
