#!/usr/bin/env python3
# ---------------------------------------------------------------------------- #
# loadtest.py — a tiny, dependency-free load generator for the read paths that
# matter: search (home), analytics, explore, and the act/authority/contractor
# detail + list pages. Standard library only (urllib + threads), so it runs
# anywhere the app does with no extra install.
#
# It first pulls a page of real search results (JSON) to build a pool of act
# ΑΔΑΜs, so the detail-page hits exercise actual rows and their joins — not 404s.
#
# Usage:
#   python3 loadtest.py --base-url http://localhost:8012 --concurrency 10 --duration 20
#   python3 loadtest.py --base-url https://khmdhs-explorer.onrender.com --requests 500
#   # optional login for full (non-teaser) access; needs an https base-url if the
#   # server sets Secure session cookies (SESSION_SECURE / SECRET_KEY):
#   python3 loadtest.py --base-url https://… --username me --password '…' --duration 30
#
# It only issues GETs (plus one login POST). Point it at a NON-production target
# unless you mean it — sustained concurrency will trip the app's rate limiting.
# ---------------------------------------------------------------------------- #
from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# A small mix of realistic Greek search terms (plus the empty "browse all").
TERMS = ["", "δήμος", "προμήθεια", "υπηρεσίες", "νοσοκομείο", "έργα", "καθαριότητα",
         "μελέτη", "εξοπλισμός", "συντήρηση"]

# Scenario weights — a read-mostly explorer: lots of search, a good chunk of act
# detail, the rest spread over analytics / explore / list pages.
WEIGHTS = {"search": 50, "act_detail": 25, "analytics": 10, "explore": 5,
           "authorities": 5, "contractors": 5}


def make_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def login(opener, base, username, password):
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(base + "/login", data=data, method="POST")
    try:
        opener.open(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        e.read()
    # Verify: /account should not bounce to /login if we're authenticated.
    req = urllib.request.Request(base + "/account")
    try:
        code = opener.open(req, timeout=30).getcode()
        return code == 200
    except urllib.error.HTTPError:
        return False


def collect_adams(opener, base, want=60):
    """Grab a pool of act ΑΔΑΜs from the search JSON so detail hits are real."""
    adams: list[str] = []
    for page in range(1, 6):
        req = urllib.request.Request(f"{base}/?page={page}&per_page=50",
                                     headers={"Accept": "application/json"})
        try:
            body = opener.open(req, timeout=30).read()
            data = json.loads(body)
        except (urllib.error.URLError, ValueError):
            break
        rows = data.get("results") or []
        adams += [r["adam"] for r in rows if r.get("adam")]
        if data.get("gated") or len(rows) < 50:
            break                      # gated → capped to page 1; or last page
    # de-dup, keep order
    seen, out = set(), []
    for a in adams:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:want]


def pick_request(base, adams):
    """Return (scenario_name, url) for a weighted-random scenario."""
    pool = dict(WEIGHTS)
    if not adams:
        pool.pop("act_detail", None)          # no data → skip detail hits
    names = list(pool)
    scen = random.choices(names, weights=[pool[n] for n in names])[0]
    if scen == "search":
        q = urllib.parse.quote(random.choice(TERMS))
        page = random.randint(1, 3)
        return scen, f"{base}/?q={q}&page={page}"
    if scen == "act_detail":
        return scen, f"{base}/act/{urllib.parse.quote(random.choice(adams))}"
    return scen, f"{base}/{scen}"             # analytics / explore / authorities / contractors


def worker(opener, base, adams, stop_at, remaining, lock, stats):
    while True:
        if remaining is not None:
            with lock:
                if remaining[0] <= 0:
                    return
                remaining[0] -= 1
        elif time.monotonic() >= stop_at:
            return
        scen, url = pick_request(base, adams)
        t0 = time.monotonic()
        ok = False
        try:
            resp = opener.open(urllib.request.Request(url), timeout=60)
            resp.read()
            ok = resp.getcode() < 400
        except urllib.error.HTTPError as e:
            ok = e.code < 400
            try:
                e.read()
            except Exception:
                pass
        except (urllib.error.URLError, TimeoutError, OSError):
            ok = False
        dt = (time.monotonic() - t0) * 1000.0
        with lock:
            s = stats.setdefault(scen, {"lat": [], "ok": 0, "err": 0})
            s["lat"].append(dt)
            s["ok" if ok else "err"] += 1


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def report(stats, wall):
    total = sum(s["ok"] + s["err"] for s in stats.values())
    errs = sum(s["err"] for s in stats.values())
    print("\n" + "=" * 78)
    print(f"{'scenario':<14}{'reqs':>7}{'err':>6}{'rps':>8}"
          f"{'p50':>8}{'p90':>8}{'p99':>8}{'max':>8}   (ms)")
    print("-" * 78)
    for scen in sorted(stats):
        s = stats[scen]
        n = s["ok"] + s["err"]
        lat = sorted(s["lat"])
        print(f"{scen:<14}{n:>7}{s['err']:>6}{n / wall:>8.1f}"
              f"{_pct(lat, 50):>8.0f}{_pct(lat, 90):>8.0f}"
              f"{_pct(lat, 99):>8.0f}{(lat[-1] if lat else 0):>8.0f}")
    print("-" * 78)
    all_lat = sorted(l for s in stats.values() for l in s["lat"])
    err_pct = (errs / total * 100.0) if total else 0.0
    print(f"{'TOTAL':<14}{total:>7}{errs:>6}{total / wall:>8.1f}"
          f"{_pct(all_lat, 50):>8.0f}{_pct(all_lat, 90):>8.0f}"
          f"{_pct(all_lat, 99):>8.0f}{(all_lat[-1] if all_lat else 0):>8.0f}")
    print("=" * 78)
    print(f"wall {wall:.1f}s · {total / wall:.1f} req/s · "
          f"{err_pct:.1f}% errors · mean {statistics.mean(all_lat):.0f} ms"
          if all_lat else "no requests completed")


def main():
    ap = argparse.ArgumentParser(description="Tiny stdlib load generator for KHMDHS read paths.")
    ap.add_argument("--base-url", default="http://localhost:8012")
    ap.add_argument("--concurrency", type=int, default=10)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--requests", type=int, help="total requests to send (across all workers)")
    g.add_argument("--duration", type=float, help="seconds to run (default 15 if neither given)")
    ap.add_argument("--username")
    ap.add_argument("--password")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    opener = make_opener()

    if args.username:
        ok = login(opener, base, args.username, args.password or "")
        print(f"login as {args.username}: {'OK' if ok else 'FAILED (running anonymous)'}"
              + ("" if ok else " — an https base-url is needed if the server sets Secure cookies"))

    adams = collect_adams(opener, base)
    print(f"target {base} · {args.concurrency} workers · "
          f"{len(adams)} act ΑΔΑΜs in the detail pool"
          + ("" if adams else " (act-detail scenario skipped — no data)"))

    remaining = [args.requests] if args.requests else None
    duration = args.duration if (args.duration or not args.requests) else None
    stop_at = time.monotonic() + (duration or 15) if remaining is None else float("inf")

    lock = threading.Lock()
    stats: dict = {}
    t0 = time.monotonic()
    threads = [threading.Thread(target=worker,
                                args=(opener, base, adams, stop_at, remaining, lock, stats),
                                daemon=True)
               for _ in range(args.concurrency)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\ninterrupted — reporting what completed")
    report(stats, max(time.monotonic() - t0, 1e-6))


if __name__ == "__main__":
    main()
