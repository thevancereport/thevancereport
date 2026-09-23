"""How soon after the bell does Nasdaq's quote carry the day's close?

The nightly job will not take a price until Nasdaq marks the market Closed,
which is 8 p.m. Eastern -- four hours after the bell. The guard exists for a
good reason: on 21 Sep 2026 the run published Friday's prices under Monday's
date. But it answers the wrong question. What we actually need to know is
whether the quote's own last-trade stamp already says today, and Nasdaq does
not document what that stamp looks like while trading is still going on.

At 11 p.m. Eastern a closed-market quote reads:

    "lastSalePrice": "$18.96", "lastTradeTimestamp": "Sep 22, 2026",
    "isRealTime": false, "marketStatus": "Closed"

-- a bare date, no time. Nobody here has seen what it reads at 4:05 p.m., and
guessing at it is how the September 21st bug happened in the first place. So
this prints the raw fields at whatever time it is run, along with the share of
a real sample that already carries today, and the rule gets written from what
comes back rather than from an assumption.

Run it a few times between 4 and 8 p.m. Eastern. It writes nothing, changes
nothing, and can be deleted once the schedule is settled.

    PROBE_N=400 python probe_close.py
"""

import json
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import value_prices as vp

SCREEN_FILE = os.environ.get("SCREEN_FILE", "value_screen.json")
PROBE_N = int(os.environ.get("PROBE_N", "400"))
PROBE_WORKERS = int(os.environ.get("PROBE_WORKERS", "6"))
SHOW = int(os.environ.get("PROBE_SHOW", "6"))
SEED = int(os.environ.get("PROBE_SEED", "20260923"))


def log(*a):
    print(*a, flush=True)


def universe():
    """A sample of the published ranking, not a hand-picked list.

    Sampling the real file keeps the answer honest: it is the same mix of
    liquid and thin names the nightly run has to get a close for. A probe of
    forty mega-caps would say yes an hour too early.
    """
    try:
        with open(SCREEN_FILE, encoding="utf-8") as fh:
            screen = json.load(fh)
    except (OSError, ValueError) as exc:
        log(f"Could not read {SCREEN_FILE} ({type(exc).__name__}).")
        return []
    syms = [r.get("symbol") for r in (screen.get("ranked") or []) if r.get("symbol")]
    if len(syms) > PROBE_N:
        syms = random.Random(SEED).sample(syms, PROBE_N)
    return sorted(syms)


def main():
    syms = sys.argv[1:] or universe()
    if not syms:
        log("Nothing to probe.")
        return 1

    now = datetime.now(timezone.utc)
    log(f"Probing {len(syms):,} symbols at {now:%Y-%m-%d %H:%M} UTC "
        f"({PROBE_WORKERS} workers).\n")

    t0 = time.time()
    quotes = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        for sym, q in pool.map(lambda s: (s, vp.raw_quote(s)), syms):
            quotes[sym] = q
    took = time.time() - t0

    answered = {s: q for s, q in quotes.items() if q}
    log(f"{len(answered):,} of {len(syms):,} answered in {took:,.0f}s "
        f"({took / max(1, len(syms)) * 1000:,.0f} ms each). "
        f"The full ~3,100-name universe would take "
        f"{took / max(1, len(syms)) * 3100 / 60:,.1f} minutes at this rate.\n")

    log(f"--- What the quote looks like right now (first {SHOW}) ---")
    for sym in list(answered)[:SHOW]:
        p = answered[sym].get("primaryData") or {}
        log(f"  {sym:6s} status={answered[sym].get('marketStatus')!r} "
            f"price={p.get('lastSalePrice')!r} stamp={p.get('lastTradeTimestamp')!r} "
            f"realtime={p.get('isRealTime')!r} volume={p.get('volume')!r}")

    log("\n--- marketStatus across the sample ---")
    for status, n in Counter(str(q.get("marketStatus")) for q in answered.values()).most_common():
        log(f"  {status!r}: {n:,}")

    log("\n--- last-trade stamps (shape matters as much as the date) ---")
    stamps = Counter(str((q.get("primaryData") or {}).get("lastTradeTimestamp"))
                     for q in answered.values())
    for stamp, n in stamps.most_common(8):
        log(f"  {stamp!r}: {n:,} ({n / len(syms):.0%} of the sample)")

    log("\n--- what latest_close() would do with this ---")
    t1 = time.time()
    closes = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        for sym, c in pool.map(lambda s: (s, vp.latest_close(s)), syms):
            if c:
                closes[sym] = c
    by_day = Counter(c[0] for c in closes.values())
    log(f"  {len(closes):,} of {len(syms):,} usable, in {time.time() - t1:,.0f}s.")
    for when, n in by_day.most_common(4):
        log(f"    {when}: {n:,} ({n / len(syms):.0%})")
    if by_day:
        newest = max(by_day)
        share = by_day[newest] / len(syms)
        log(f"\n  Newest session: {newest} on {share:.0%} of the sample. "
            f"The run needs 90% on one session before it will publish a day.")
        log("  VERDICT: " + ("a run at this hour could publish."
                             if share >= 0.9 else "too thin to publish at this hour."))
    else:
        log("\n  VERDICT: latest_close() returns nothing at this hour.")

    log("\n" + vp.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
