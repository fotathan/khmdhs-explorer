"""Coverage and identity report for the single-source trial (spec §8.1–2).

Compares the acts we hold today (`procurement`, from KHMDHS / Diavgeia / TED)
with the Tender Service records collected into the trial (`procurement_tsg`).
READ-ONLY on both databases, no API calls.

    python tsg_coverage.py --start 2026-09-01 --end 2026-09-30 \
        --out runs/coverage-2026-09.md

Two things keep the numbers honest:
  * A day is compared only where BOTH sides hold it completely: Tender Service
    days whose walk is done, and on our side days strictly before the last day
    each (source, type) was ingested (that last day may be partial). Locally our
    KHMDHS awards/contracts/payments stopped earlier than its notices, so each
    type gets its own window, and a type with none is reported as "no overlap"
    rather than as 100% missing.
  * An act is "found" if Tender Service holds a record with its key ANYWHERE,
    whatever its publication date; the date is compared separately.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import sys

import tsg_ingest as tg

OURS_DSN = "postgresql://postgres:pw@127.0.0.1:5433/procurement"
TRIAL_DSN = "postgresql://postgres:pw@127.0.0.1:5433/procurement_tsg"
OUR_SOURCES = ("khmdhs", "diavgeia", "ted")
# Which of our sources each Tender Service key kind answers for.
KIND_SOURCE = {"adam": "khmdhs", "ada": "diavgeia", "ted": "ted"}

LOADED_DAYS_SQL = """
SELECT w.day FROM proc.tsg_ingest_window w
WHERE w.kind = 'pub:all' AND w.day BETWEEN %(s)s AND %(e)s
  AND (w.status = 'done'
       OR (w.status = 'over_cap'
           AND (SELECT count(*) FROM proc.tsg_ingest_window t
                WHERE t.kind LIKE 'pub:type:%%' AND t.day = w.day
                  AND t.status IN ('done', 'partial')) = %(ntypes)s))
"""


# --------------------------------------------------------------------------- #
# pure part (tests/test_tsg_coverage.py)
# --------------------------------------------------------------------------- #
def comparable_days(start: dt.date, end: dt.date, our_last: dt.date | None,
                    loaded: set[dt.date]) -> set[dt.date]:
    """Days in [start, end] that Tender Service has finished AND that lie
    strictly before the last day we ingested (which may be partial)."""
    if our_last is None:
        return set()
    return {d for d in loaded if start <= d <= end and d < our_last}


def tsg_key_type(key: str, kind: str, label: str | None) -> str:
    return tg.single_source_type(key, kind, {"typeOfDocument": label})[0]


def compare(ours: list[tuple], our_last: dict, tsg: list[tuple], loaded: set[dt.date],
            start: dt.date, end: dt.date, samples: int = 10) -> dict:
    """ours: (adam, source, type, date, title). our_last: (source, type) → last
    ingested day. tsg: (internal_id, key, kind, label, data_source, date, title).
    Returns per-(source, type) coverage, the Tender Service side by kind, and
    the records Tender Service has that we do not."""
    by_key = {}
    for row in tsg:
        by_key.setdefault(row[1], row)
    windows = {st: comparable_days(start, end, last, loaded) for st, last in our_last.items()}

    cov: dict = {}
    for adam, source, typ, day, title in ours:
        w = windows.get((source, typ), set())
        c = cov.setdefault((source, typ), {"window": w, "n": 0, "found": 0, "same_date": 0,
                                           "missing": []})
        if day not in w:
            continue
        c["n"] += 1
        hit = by_key.get(adam)
        if hit:
            c["found"] += 1
            c["same_date"] += int(hit[5] == day)
        elif len(c["missing"]) < samples:
            c["missing"].append((adam, day, title))
    for st, w in windows.items():
        cov.setdefault(st, {"window": w, "n": 0, "found": 0, "same_date": 0, "missing": []})

    our_keys = {a for a, *_ in ours}
    side: dict = collections.defaultdict(lambda: collections.Counter())
    extra: dict = {}
    in_loaded = [r for r in tsg if r[5] in loaded and start <= r[5] <= end]
    for internal, key, kind, label, ds, day, title in in_loaded:
        side[(ds or "?", label or "?")][kind] += 1
        source = KIND_SOURCE.get(kind)
        if not source:
            continue
        typ = tsg_key_type(key, kind, label)
        # Only where we would hold it if it existed: our window for that type.
        if day in windows.get((source, typ), set()) and key not in our_keys:
            e = extra.setdefault((source, typ), {"n": 0, "samples": []})
            e["n"] += 1
            if len(e["samples"]) < samples:
                e["samples"].append((key, internal, day, title))
    return {"coverage": cov, "tsg_side": {k: dict(v) for k, v in side.items()},
            "extra": extra, "tsg_total": len(in_loaded)}


def _span(days: set) -> str:
    return f"{min(days)} … {max(days)} ({len(days)} days)" if days else "no overlap"


def _cut(s, n=70):
    s = (s or "").replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def render(r: dict, start: dt.date, end: dt.date, loaded: set, made: str) -> str:
    out = [f"# Tender Service trial: coverage and identity, {start} … {end}", "",
           f"Made {made}. Read-only on `procurement` and `procurement_tsg`; no API calls.", "",
           f"Tender Service days fully loaded: {_span(loaded)}. "
           f"Records on those days: {r['tsg_total']:,}.", "",
           "## 1. Our acts: does Tender Service have them? (identity = found by key)", "",
           "| Our source | Type | Compared days | Our acts | Found | % | Same publication date |",
           "|---|---|---|---|---|---|---|"]
    for (source, typ), c in sorted(r["coverage"].items()):
        pct = f"{100 * c['found'] / c['n']:.1f}%" if c["n"] else "–"
        same = f"{100 * c['same_date'] / c['found']:.1f}%" if c["found"] else "–"
        out.append(f"| {source} | {typ} | {_span(c['window'])} | {c['n']:,} | {c['found']:,} | {pct} | {same} |")
    out += ["", "## 2. Tender Service records on the loaded days, by key kind", "",
            "| Tender Service source | Label | ΑΔΑΜ | ΑΔΑ | TED | own id only |", "|---|---|---|---|---|---|"]
    for (ds, label), k in sorted(r["tsg_side"].items(), key=lambda kv: -sum(kv[1].values())):
        out.append(f"| {_cut(ds, 40)} | {label} | {k.get('adam', 0):,} | {k.get('ada', 0):,} | "
                   f"{k.get('ted', 0):,} | {k.get('tsg', 0):,} |")
    out += ["", "## 3. Keyed records Tender Service has and we don't (within our compared days)", "",
            "| Source the key belongs to | Type | Records |", "|---|---|---|"]
    for (source, typ), e in sorted(r["extra"].items()):
        out.append(f"| {source} | {typ} | {e['n']:,} |")
    if not r["extra"]:
        out.append("| – | – | none |")
    out += ["", "## 4. Samples", ""]
    for (source, typ), c in sorted(r["coverage"].items()):
        if c["missing"]:
            out += [f"**Missing from Tender Service: {source} {typ}**", ""]
            out += [f"- `{a}` {d} {_cut(t)}" for a, d, t in c["missing"]] + [""]
    for (source, typ), e in sorted(r["extra"].items()):
        out += [f"**Only in Tender Service: {source} {typ}**", ""]
        out += [f"- `{k}` (tsg {i}) {d} {_cut(t)}" for k, i, d, t in e["samples"]] + [""]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# database part
# --------------------------------------------------------------------------- #
def load(ours_dsn: str, trial_dsn: str, start: dt.date, end: dt.date):
    from db import Database
    with Database(ours_dsn) as db:
        ours = db.query("""SELECT adam, data_source, type, submission_date::date, title
                           FROM proc.procurement_act
                           WHERE data_source = ANY(%s) AND submission_date >= %s
                             AND submission_date < %s""",
                        (list(OUR_SOURCES), start, end + dt.timedelta(days=1)))
        our_last = {(s, t): d for s, t, d in db.query(
            """SELECT data_source, type, max(ingested_at AT TIME ZONE 'Europe/Athens')::date
               FROM proc.procurement_act WHERE data_source = ANY(%s) GROUP BY 1, 2""",
            (list(OUR_SOURCES),))}
    with Database(trial_dsn) as db:
        loaded = {r[0] for r in db.query(LOADED_DAYS_SQL, {"s": start, "e": end,
                                                           "ntypes": len(tg.DOCUMENT_TYPES)})}
        rows = db.query("""SELECT internal_id, external_id, raw_json->>'sourceUrl', type_label,
                                  data_source, publication_date, title
                           FROM proc.tsg_record""")
    tsg = []
    for internal, ext, url, label, ds, day, title in rows:
        key, kind = tg.single_source_key(internal, {"externalId": ext, "sourceUrl": url})
        tsg.append((internal, key, kind, label, ds, day, title))
    return [tuple(r) for r in ours], our_last, tsg, loaded


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--ours", default=OURS_DSN)
    p.add_argument("--trial", default=TRIAL_DSN)
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--out", help="write the markdown report here (also printed)")
    a = p.parse_args(argv)
    if a.ours == a.trial:
        sys.exit("--ours and --trial are the same database")
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    ours, our_last, tsg, loaded = load(a.ours, a.trial, start, end)
    report = render(compare(ours, our_last, tsg, loaded, start, end, a.samples),
                    start, end, loaded, dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    if a.out:
        pathlib.Path(a.out).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
