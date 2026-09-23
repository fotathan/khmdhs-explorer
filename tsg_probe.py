#!/usr/bin/env python3
"""tsg_probe.py — what does the Tender Service feed actually give a Greek key?

Tender Service Group's Data Export API v3 already feeds ConnectContractors with
Romanian notices. Before any of it is built into KHMDHS, this measures what the
GREEK key's profile returns — because in Romania the published spec was wrong
in ways only real payloads showed (see ConnectContractors
src/lib/adapters/ts4-dataexport.ts, DATA_EXPORT_LIMITATIONS).

What it answers
    * Which environment the key belongs to (production or TEST).
    * The portal's date pattern and the filters the key may use.
    * Whether an unknown parameter is silently IGNORED (it was in Romania:
      contractAwardDate_from returned all 17.6M records). Same trap as the ΓΕΜΗ
      registry — see gemi_client.REGISTRY_GUARD.
    * Whether lastUpdated_from really filters (it decides how catch-up works).
    * Whether archive access (status=EXPIRED) is enabled for this key.
    * For one day of notices and awards: field coverage, the Greek display
      labels, which upstream sources the feed aggregates (dataSource), and
      whether the records carry OUR identifiers (ΑΔΑΜ, ΑΔΑ, TED number) — the
      thing cross-source de-duplication will have to join on.

What it deliberately does NOT do
    * It never touches the database. Everything lands under runs/ (git-ignored).
    * It never prints, logs or saves the key; it goes in the X-API-Key header,
      never in a URL.
    * It spends a hard-capped number of requests (--budget, default 40). A
      100-requests-per-day cap was observed once on a Romanian key, so a probe
      must not be the thing that uses a day's quota.

Usage
    python tsg_probe.py                       # auto: production first, then TEST
    python tsg_probe.py --env test --day 2026-09-10 --pages 3
    python tsg_probe.py --report runs/tsg-probe-20260914-221500

Env
    TSG_API_KEY   read from the environment, else from ~/.khmdhs.env
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import re
import sys
import time
from collections import Counter

import requests

PROD_BASE = "https://api.tender-service.com/dataexport/api"
TEST_BASE = "https://test-api.tender-service.com/dataexport/api"

# Romania's key allowed 10 per page; the server names the real limit in its 400.
START_PAGE_SIZE = 10
RATE_LIMIT_FLOOR = 20
MAX_ATTEMPTS = 4
TIMEOUT = (10, 60)

# Fields whose distinct values are worth seeing (vocabulary, not personal data).
VOCAB_FIELDS = [
    "typeOfDocument", "typeOfDocumentOriginal", "subTypeOfDocument",
    "natureOfContract", "procedure", "status", "dataSource", "dataSourceToShow",
    "originalLanguages", "regulationOfProcurement", "awardingCriteria",
    "typeOfBidRequired", "eauction", "dps", "divisionIntoLots",
    "frameworkAgreement", "nutsCodesCountry",
]
# Fields that might carry an identifier we already hold.
ID_FIELDS = [
    "internalID", "externalId", "referenceNumber", "journalNumber",
    "authorityReference", "sourceUrl",
]
# Fields whose FORMAT matters to the mapper (dates, amounts).
SHAPE_FIELDS = [
    "publicationDate", "deadlineDate", "demandingDate", "sendDate",
    "contractAwardDate", "contractEndDateInternal", "systemExpirationDate",
    "estimatedPrices", "estimatedPricesAbove", "estimatedPricesBelow",
    "contractValue", "contractorPrices", "cpvCodes", "nutsCodes",
    "contractorStatisticalOrTaxNumber", "authorityIdentifier",
]

# ΑΔΑΜ: 2-digit year + kind + digits, e.g. 24PROC015123456, 25SYMV017000001.
ADAM_RE = re.compile(r"\b\d{2}(?:PROC|REQ|AWRD|SYMV|PAY)\d{6,}\b")
# ΑΔΑ (Diavgeia): e.g. 9ΛΥΠ46ΜΤΛΡ-8ΘΡ, and older short ones like 9ΥΠΓΩ6Ζ-ΓΝ8 —
# must contain at least one Greek capital. Tender Service appends a timestamp
# ('94ΩΕ46907Τ-ΚΜΗ-09-11143505'), so no word boundary is required after it.
ADA_RE = re.compile(r"(?<![0-9Α-ΩA-Z])[0-9Α-ΩA-Z]{6,10}-[0-9Α-ΩA-Z]{3}(?![0-9Α-ΩA-Z])")
GREEK_CAP = re.compile(r"[Α-Ω]")
# TED publication number: 123456-2026.
TED_RE = re.compile(r"\b\d{5,8}-(?:19|20)\d{2}\b")


# --------------------------------------------------------------------------- #
# pure helpers (tested in tests/test_tsg_probe.py)
# --------------------------------------------------------------------------- #
def load_key(env: dict | None = None, env_file: pathlib.Path | None = None) -> str | None:
    """The key from the environment, else from ~/.khmdhs.env.

    Whitespace and surrounding quotes are stripped: a space after the '=' once
    made every shell read the key as empty (and try to RUN it as a command)."""
    env = os.environ if env is None else env
    val = (env.get("TSG_API_KEY") or "").strip().strip("'\"").strip()
    if val:
        return val
    path = env_file or pathlib.Path.home() / ".khmdhs.env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(?:export\s+)?TSG_API_KEY\s*=(.*)$", line)
            if m:
                val = m.group(1).strip().strip("'\"").strip()
                return val or None
    except OSError:
        return None
    return None


def parse_rate_limit(header: str | None) -> tuple[int | None, int | None]:
    """Rate-Limit-Remaining is prose: '299 requests remaining for the next 579
    seconds' or 'All requests consumed, so it will take 17 seconds to reset'."""
    if not header:
        return None, None
    nums = [int(n) for n in re.findall(r"\d+", header)]
    if re.search(r"all requests consumed", header, re.I):
        return 0, (nums[0] if nums else None)
    return (nums[0] if nums else None), (nums[1] if len(nums) > 1 else None)


def parse_max_from_error(body: str) -> int | None:
    """'Requested tender amount is greater than limit: 10' → 10."""
    m = re.search(r"greater than limit:\s*(\d+)", body or "", re.I)
    n = int(m.group(1)) if m else 0
    return n if n > 0 else None


def java_to_strftime(pattern: str) -> str:
    """The Java date pattern /branch/dateFormat reports → strftime.
    Only the date tokens the API has been seen to use; anything else raises,
    because a request date in the wrong format silently returns the wrong day."""
    out, i = [], 0
    tokens = [("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d")]
    while i < len(pattern):
        for tok, rep in tokens:
            if pattern.startswith(tok, i):
                out.append(rep)
                i += len(tok)
                break
        else:
            ch = pattern[i]
            if ch.isalpha():
                raise ValueError(f"unsupported date pattern: {pattern!r}")
            out.append(ch)
            i += 1
    return "".join(out)


def extract_records(body) -> list[dict]:
    """The tender list: a bare array, or the first array of objects in a wrapper."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and all(isinstance(x, dict) for x in v):
                return v
    return []


def extract_meta(body) -> dict:
    """Every non-list top-level value of a wrapper — where a total would be."""
    if not isinstance(body, dict):
        return {}
    return {k: v for k, v in body.items() if not isinstance(v, (list, dict))}


def find_total(meta: dict) -> int | None:
    for k, v in meta.items():
        if re.search(r"total|count|numfound|hits", k, re.I):
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def value_shape(value) -> str:
    """'13.08.27 16:00' → '99.99.99 99:99'; '41.109,00 EUR' → '99.999,99 AAA'.
    Shapes, not values: enough to write a parser, nothing personal."""
    s = str(value)
    s = re.sub(r"\d", "9", s)
    s = re.sub(r"[A-ZΑ-Ω]", "A", s)
    s = re.sub(r"[a-zα-ωάέήίόύώϊϋΐΰς]", "a", s)
    s = re.sub(r"(9)\1{3,}", "9…", s)
    s = re.sub(r"([Aa])\1{3,}", r"\1…", s)
    return s[:60]


def classify_ids(text) -> set[str]:
    """Which of our identifier kinds appear in a string."""
    s = str(text or "")
    kinds = set()
    if ADAM_RE.search(s):
        kinds.add("adam")
    if any(GREEK_CAP.search(m.group(0)) for m in ADA_RE.finditer(s)):
        kinds.add("ada")
    if TED_RE.search(s):
        kinds.add("ted")
    return kinds


def coverage(records: list[dict]) -> list[tuple[str, int, int]]:
    """(field, populated, percent), most populated first."""
    n = len(records) or 1
    c = Counter()
    for r in records:
        for k, v in r.items():
            if v not in (None, "", [], {}):
                c[k] += 1
    return sorted(((k, v, round(100 * v / n)) for k, v in c.items()),
                  key=lambda t: (-t[1], t[0]))


def summarise(records: list[dict]) -> dict:
    vocab = {f: Counter(str(r[f]).strip() for r in records if r.get(f) not in (None, ""))
             for f in VOCAB_FIELDS}
    ids = {}
    for f in ID_FIELDS:
        kinds = Counter()
        for r in records:
            for k in classify_ids(r.get(f)):
                kinds[k] += 1
        ids[f] = {"kinds": dict(kinds),
                  "samples": [str(r[f])[:120] for r in records if r.get(f)][:8]}
    shapes = {f: Counter(value_shape(r[f]) for r in records if r.get(f) not in (None, ""))
              for f in SHAPE_FIELDS}
    return {
        "records": len(records),
        "coverage": coverage(records),
        "vocab": {f: c.most_common(40) for f, c in vocab.items() if c},
        "ids": ids,
        "shapes": {f: c.most_common(8) for f, c in shapes.items() if c},
    }


def pick_day(today: dt.date) -> dt.date:
    """The most recent weekday at least three days back — settled, not a Sunday."""
    d = today - dt.timedelta(days=3)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #
class BudgetExhausted(RuntimeError):
    pass


class AuthRefused(RuntimeError):
    pass


class Probe:
    def __init__(self, key: str, base: str, budget: int, out: pathlib.Path):
        self.key, self.base, self.budget, self.out = key, base, budget, out
        self.spent = 0
        self.page_size = START_PAGE_SIZE
        self.remaining: int | None = None
        self.reset: int | None = None
        self.s = requests.Session()
        (out / "responses").mkdir(parents=True, exist_ok=True)

    def _save(self, step: str, url: str, resp, body):
        keep = {h: resp.headers.get(h) for h in
                ("Rate-Limit-Remaining", "x-error-message", "x-api-key-source",
                 "x-request-id", "Content-Type") if resp is not None and resp.headers.get(h)}
        (self.out / "responses" / f"{step}.json").write_text(json.dumps(
            {"url": url, "status": getattr(resp, "status_code", 0), "headers": keep,
             "body": body}, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, step: str, path: str, params: dict | None = None):
        if self.remaining is not None and self.remaining <= RATE_LIMIT_FLOOR:
            wait = max(1, self.reset or 60)
            print(f"  rate limit low ({self.remaining}); waiting {wait}s", flush=True)
            time.sleep(wait)
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        last = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.spent >= self.budget:
                raise BudgetExhausted(f"request budget of {self.budget} spent")
            self.spent += 1
            try:
                resp = self.s.get(self.base + path, params=params, timeout=TIMEOUT,
                                  headers={"X-API-Key": self.key, "Accept": "application/json"})
            except requests.RequestException as e:
                last = (None, f"{type(e).__name__}")
                resp = None
            if resp is not None:
                self.remaining, self.reset = parse_rate_limit(resp.headers.get("Rate-Limit-Remaining"))
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = resp.text
                    self._save(step, resp.url, resp, body)
                    return body
                last = (resp, resp.text)
                if not (resp.status_code == 429 or resp.status_code >= 500):
                    break
            if attempt < MAX_ATTEMPTS:
                delay = 2 ** attempt * (0.5 + random.random())
                why = resp.status_code if resp is not None else last[1]
                print(f"  {why} on {path} (attempt {attempt}/{MAX_ATTEMPTS}); retrying in {delay:.1f}s",
                      flush=True)
                time.sleep(delay)
        resp, text = last
        if resp is not None:
            self._save(step, resp.url, resp, text[:2000])
            if resp.status_code == 403:
                raise AuthRefused(resp.headers.get("x-error-message") or "403, no reason given")
            raise RuntimeError(f"{resp.status_code} for {path}"
                               f" ({resp.headers.get('x-error-message') or text[:200]})"
                               f" [request {resp.headers.get('x-request-id')}]")
        raise RuntimeError(f"{path} did not answer: {text}")

    def tenders(self, step: str, params: dict, offset: int = 0):
        q = {**params, "format": "json", "max": self.page_size, "offset": offset}
        try:
            body = self.get(step, "/branch/tenders", q)
        except RuntimeError as e:
            permitted = parse_max_from_error(str(e))
            if not str(e).startswith("400") or permitted in (None, self.page_size):
                raise
            print(f"  page size {self.page_size} refused; the server allows {permitted}")
            self.page_size = permitted
            body = self.get(step, "/branch/tenders", {**q, "max": permitted})
        return extract_records(body), extract_meta(body)

    def walk(self, step: str, params: dict, pages: int):
        out, meta0, offset = [], {}, 0
        for p in range(pages):
            recs, meta = self.tenders(f"{step}-p{p + 1}", params, offset)
            meta0 = meta0 or meta
            out.extend(recs)
            if len(recs) < self.page_size:
                break
            offset += len(recs)
        return out, meta0


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
def run(args) -> int:
    key = load_key()
    if not key:
        print("No TSG_API_KEY in the environment or ~/.khmdhs.env.")
        return 2
    print(f"Key found ({len(key)} characters).")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path(args.out or f"runs/tsg-probe-{stamp}")
    envs = {"prod": [PROD_BASE], "test": [TEST_BASE], "auto": [PROD_BASE, TEST_BASE]}[args.env]

    probe, pattern, refusals = None, None, []
    for base in envs:
        p = Probe(key, base, args.budget, out)
        print(f"\nTrying {base}")
        try:
            fmt = p.get("01-dateFormat", "/branch/dateFormat")
        except AuthRefused as e:
            refusals.append((base, str(e)))
            print(f"  refused: {e}")
            continue
        pattern = (fmt or {}).get("dateFormat") or (fmt or {}).get("format") if isinstance(fmt, dict) else None
        probe = p
        break
    if probe is None:
        print("\nThe key was refused by every environment tried:")
        for base, why in refusals:
            print(f"  {base}: {why}")
        print("If the reason is 'Invalid API key', check the key was copied whole. Otherwise the key "
              "may have no search profile yet — ask Tender Service which profile it carries.")
        return 1
    print(f"  accepted. Portal date pattern: {pattern!r}")

    findings: dict = {"base": probe.base, "date_pattern": pattern, "steps": {}}
    try:
        strf = java_to_strftime(pattern or "dd.MM.yy")
    except ValueError as e:
        print(f"  {e}; falling back to dd.MM.yy for request dates")
        strf = "%d.%m.%y"

    def fmtd(d: dt.date) -> str:
        return d.strftime(strf)

    try:
        opts = probe.get("02-filterOptions", "/branch/filterOptions")
        findings["filter_options"] = opts
        print(f"\nFilter options the key reports: {json.dumps(opts, ensure_ascii=False)[:600]}")

        # The baseline and the three "does this parameter do anything" checks.
        checks = [
            ("03-unfiltered", {}),
            ("04-ignored-param", {"zzProbeNotAParameter": "1"}),
            ("05-lastUpdated-yesterday", {"lastUpdated_from": fmtd(dt.date.today() - dt.timedelta(days=1))}),
            ("06-status-expired", {"status": "EXPIRED"}),
        ]
        first_ids = {}
        for step, params in checks:
            recs, meta = probe.tenders(step, params)
            total = find_total(meta)
            first_ids[step] = [r.get("internalID") for r in recs]
            findings["steps"][step] = {"params": params, "meta": meta, "total": total,
                                       "page": len(recs), "first_ids": first_ids[step]}
            print(f"  {step:<28} total={total if total is not None else '?':<12} page={len(recs)}")

        base_total = findings["steps"]["03-unfiltered"]["total"]

        def verdict(step):
            st = findings["steps"][step]
            if base_total is not None and st["total"] is not None:
                return "IGNORED (same total as unfiltered)" if st["total"] == base_total else "filters"
            return ("IGNORED (same first page)" if st["first_ids"] == first_ids["03-unfiltered"]
                    else "filters (different first page)")

        findings["verdicts"] = {s: verdict(s) for s in
                                ("04-ignored-param", "05-lastUpdated-yesterday", "06-status-expired")}

        # One day of each feed.
        day = dt.date.fromisoformat(args.day) if args.day else pick_day(dt.date.today())
        window = {"publicationDate_from": fmtd(day),
                  "publicationDate_to": fmtd(day + dt.timedelta(days=1))}   # _to is exclusive
        findings["day"] = day.isoformat()
        feeds = [
            ("10-tender", {"typeOfDocument": "TENDER", **window}),
            ("11-result-active", {"typeOfDocument": "RESULT", **window}),
            ("12-result-expired", {"typeOfDocument": "RESULT", "status": "EXPIRED", **window}),
            # Unfiltered means ACTIVE only: a notice from a past day whose
            # deadline has gone is EXPIRED and would otherwise never be seen.
            ("13-tender-expired", {"typeOfDocument": "TENDER", "status": "EXPIRED", **window}),
        ]
        all_records: dict[str, dict] = {}
        print(f"\nOne day of each feed: {day} (window {window})")
        for step, params in feeds:
            recs, meta = probe.walk(step, params, args.pages)
            findings["steps"][step] = {"params": params, "meta": meta, "total": find_total(meta),
                                       "fetched": len(recs)}
            print(f"  {step:<28} total={find_total(meta) if find_total(meta) is not None else '?':<12}"
                  f" fetched={len(recs)}")
            for r in recs:
                all_records[str(r.get("internalID") or r.get("externalId") or id(r))] = {**r, "_feed": step}
    except BudgetExhausted as e:
        print(f"\nStopped: {e}. What was fetched is still analysed.")
        findings["stopped"] = str(e)
    except (RuntimeError, AuthRefused) as e:
        print(f"\nStopped: {e}. What was fetched is still analysed.")
        findings["stopped"] = str(e)
    else:
        pass
    finally:
        findings["requests_spent"] = probe.spent
        findings["page_size"] = probe.page_size
        findings["rate_limit_remaining"] = probe.remaining

    records = list(locals().get("all_records", {}).values())
    (out / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    findings["summary"] = summarise(records)
    (out / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2, default=str),
                                       encoding="utf-8")
    report(findings)
    print(f"\nWrote {out}/findings.json, records.json and responses/")
    return 1 if findings.get("stopped") else 0


def report(f: dict) -> None:
    s = f["summary"]
    print("\n" + "=" * 72)
    print(f"Environment: {f['base']}   requests spent: {f['requests_spent']}   "
          f"page size: {f['page_size']}   rate remaining: {f['rate_limit_remaining']}")
    for step, v in (f.get("verdicts") or {}).items():
        print(f"  {step:<28} {v}")
    print(f"\nRecords analysed: {s['records']}")
    if not s["records"]:
        return
    print("\nField coverage (top 45):")
    for field, n, pct in s["coverage"][:45]:
        print(f"  {pct:>3}%  {field}")
    print("\nVocabulary (display labels as returned):")
    for field, vals in s["vocab"].items():
        print(f"  {field}:")
        for v, n in vals[:12]:
            print(f"      {n:>4}  {v}")
    print("\nOur identifiers inside the feed:")
    for field, d in s["ids"].items():
        if d["samples"]:
            print(f"  {field:<20} kinds={d['kinds'] or '-'}  e.g. {d['samples'][:3]}")
    print("\nValue shapes (for the parser):")
    for field, vals in s["shapes"].items():
        print(f"  {field:<34} " + "  |  ".join(f"{v} ×{n}" for v, n in vals[:4]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", choices=["auto", "prod", "test"], default="auto")
    ap.add_argument("--day", help="YYYY-MM-DD publication day to sample (default: a recent weekday)")
    ap.add_argument("--pages", type=int, default=3, help="pages per feed (default 3)")
    ap.add_argument("--budget", type=int, default=40, help="hard cap on requests (default 40)")
    ap.add_argument("--out", help="output directory (default runs/tsg-probe-<stamp>)")
    ap.add_argument("--report", help="re-print a finished run's findings")
    args = ap.parse_args(argv)
    if args.report:
        report(json.loads((pathlib.Path(args.report) / "findings.json").read_text(encoding="utf-8")))
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
