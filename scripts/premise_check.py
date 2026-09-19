"""
Does the premise hold? A one-off study, not a product.

The question this answers is narrow and worth stating exactly: over the last
few years, did the names this screen would have picked beat the market over
three and six months? It is not a backtest of a strategy -- there is no
position sizing, no costs, no rebalancing rule -- and nothing it prints should
appear on the site without the limitations printed beside it.

METHOD

  For each observation date, the screen is rebuilt from what was knowable on
  that date and the names that clear are followed forward.

  Fundamentals are point-in-time by construction. A calendar quarter's balance
  sheet is only used once at least LOOKAHEAD_GUARD_DAYS have passed since the
  quarter ended, which is longer than the filing deadline for every filer size.
  No figure is used before it could have been read.

  Prices are daily closes. Entry is the close on the observation date, exits
  are 63 and 126 trading sessions later -- about three and six months, the
  horizon the site tells readers it has in mind.

  Every name is compared against IWM over the identical index range, because
  "up eight percent" means nothing until you know what the market did.

THREE LIMITATIONS, AND NONE OF THEM ARE SMALL

  1. Survivorship. The universe comes from today's listed symbols. Anything
     that delisted, went bankrupt or was acquired is already missing. For a
     screen that deliberately buys damaged companies this is the bias that
     flatters hardest, and it only ever flatters. Read every number here as an
     upper bound. The useful asymmetry: if the screen does not beat the market
     even with that help, the premise is dead, and that is a real finding.

  2. Sampling. Fetching five years of daily closes for every eligible filer is
     tens of thousands of requests. A seeded random sample of the eligible
     universe is used instead, so the counts per date are a fraction of what
     the live screen sees. The return distribution is still unbiased; the
     error bars are just wider.

  3. Fewer tests than the live screen. Cash, liabilities, shares, the price
     move, market cap and liquidity are all applied. Runway and dilution are
     not: they need two more concepts per quarter and they mostly reject names
     rather than add them, so leaving them out is another way this study is
     kinder to the screen than reality.

Run it from the Actions tab. It writes nothing to the site.
"""

import json
import os
import random
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The live screen's own code, so this studies the screen the site runs rather
# than a second implementation that could quietly disagree with it.
import update_research as live

CASH_GATE = live.CASH_GATE
DROP_THRESHOLD = live.DROP_THRESHOLD
DROP_WINDOW_DAYS = live.DROP_WINDOW_DAYS
MIN_MARKET_CAP = live.MIN_MARKET_CAP
MIN_DOLLAR_VOLUME = live.MIN_DOLLAR_VOLUME

FIRST_OBSERVATION = date(2021, 6, 1)
LAST_OBSERVATION = date(2025, 9, 1)
HORIZONS = {"3m": 63, "6m": 126}      # trading sessions
LOOKAHEAD_GUARD_DAYS = 100            # a quarter is unusable until this has passed
MIN_NET_CASH = 15_000_000             # below this, no price can clear both gates
SAMPLE_SIZE = int(os.environ.get("SAMPLE_SIZE", "500"))
SAMPLE_SEED = int(os.environ.get("SAMPLE_SEED", "20260919"))
# IWM is the small-cap benchmark this screen should be judged against. The
# others are here only so a single bad endpoint cannot waste a whole run; the
# one actually used is named in the output and in the JSON.
BENCHMARKS = ("IWM", "VTWO", "SPY")
BENCHMARK = BENCHMARKS[0]
OUT_FILE = "premise_check.json"


# --------------------------------------------------------------------------
# Why MIN_NET_CASH is a free filter
#
# Clearing the cash gate needs price <= cps / (CASH_GATE/100).
# Clearing the size floor needs price >= MIN_MARKET_CAP / shares.
# Both can only be true at once if
#     net_cash / shares / (CASH_GATE/100) >= MIN_MARKET_CAP / shares
# and the share count cancels, leaving net_cash >= MIN_MARKET_CAP * CASH_GATE/100.
# No price history is needed to rule the rest out.
# --------------------------------------------------------------------------


def observation_dates():
    """The first of each month across the study window."""
    out, d = [], FIRST_OBSERVATION
    while d <= LAST_OBSERVATION:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def usable_quarters(on: date):
    """Frame periods readable on `on`, newest first.

    A quarter ending 31 March is not used until at least LOOKAHEAD_GUARD_DAYS
    after that date, which clears the 40/45-day filing deadlines with room to
    spare. Two quarters are returned so a company that has not filed the newest
    one yet falls back, exactly as the live screen does.
    """
    ends = []
    year = on.year
    for y in (year, year - 1, year - 2):
        for q, (m, dd) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), start=1):
            ends.append((date(y, m, dd), f"CY{y}Q{q}I"))
    ends = [(e, p) for e, p in ends if (on - e).days >= LOOKAHEAD_GUARD_DAYS]
    ends.sort(reverse=True)
    return [p for _, p in ends[:2]]


def fetch_history(symbol, start, end, asset_classes=("stocks",)):
    """Daily closes for one symbol as [(date, close, volume)], oldest first.

    Nasdaq keys this endpoint by asset class and returns an empty table, not an
    error, when the class is wrong. Every name in the screen's universe is a
    common stock, but the benchmark is an ETF, so the caller passes the classes
    to try and we stop at the first one that answers with rows.
    """
    rows = None
    for cls in asset_classes:
        url = live.NASDAQ_HISTORY.format(
            symbol=quote(symbol), start=start.isoformat(), end=end.isoformat()
        ).replace("limit=30", "limit=5000").replace("assetclass=stocks", "assetclass=" + cls)
        data = live.fetch_json(url, live.BROWSER_UA, timeout=45, attempts=2)
        rows = (((data or {}).get("data") or {}).get("tradesTable") or {}).get("rows")
        if rows:
            break
        time.sleep(0.4)
    out = []
    for r in rows or []:
        close = live.money(r.get("close"))
        if not close:
            continue
        try:
            d = datetime.strptime(r.get("date", ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        vol = 0
        try:
            vol = int(str(r.get("volume", "0")).replace(",", ""))
        except ValueError:
            pass
        out.append((d, close, vol))
    out.sort()
    return out


def index_on_or_before(series, when):
    """Position of the last session at or before `when`, or None."""
    lo, hi, found = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= when:
            found, lo = mid, mid + 1
        else:
            hi = mid - 1
    return found


def pct(a, b):
    """Move from b to a, in percent."""
    return None if not b else (a - b) / b * 100.0


def summarise(values):
    if not values:
        return None
    vals = sorted(values)
    n = len(vals)
    return {
        "n": n,
        "median": round(statistics.median(vals), 2),
        "mean": round(statistics.fmean(vals), 2),
        "p25": round(vals[n // 4], 2),
        "p75": round(vals[(3 * n) // 4], 2),
        "worst": round(vals[0], 2),
        "best": round(vals[-1], 2),
        "hit_rate_pct": round(100.0 * sum(1 for v in vals if v > 0) / n, 1),
    }


def main():
    print(__doc__.strip().split("\n\n")[0])
    print()

    rows = live.fetch_market_rows()
    ticker_cik = live.fetch_ticker_to_cik()
    if not rows or not ticker_cik:
        print("Market or ticker data unavailable; nothing to study.")
        return 1

    listed = {}
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        if not sym or "." in sym or "^" in sym:
            continue
        if not live.is_common_stock(row.get("name") or ""):
            continue
        cik = ticker_cik.get(sym)
        if cik is not None:
            listed[sym] = cik
    print(f"{len(listed):,} listed common stocks carry a CIK today.")

    dates = observation_dates()
    quarters = sorted({q for d in dates for q in usable_quarters(d)})
    print(f"{len(dates)} observation dates, {len(quarters)} EDGAR quarters to read.\n")

    # ---- fundamentals, one read per quarter, reused by every date ----
    facts = {}
    for period in quarters:
        facts[period] = {
            "cash": live.fetch_frame_periods(
                "us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD", [period]),
            "liab": live.fetch_frame_periods("us-gaap", "Liabilities", "USD", [period]),
            "shares": live.fetch_frame_periods(
                "dei", "EntityCommonStockSharesOutstanding", "shares", [period]),
        }

    def facts_on(when):
        """Merged cash/liabilities/shares readable on `when`, newest quarter first."""
        merged = {"cash": {}, "liab": {}, "shares": {}}
        for period in usable_quarters(when):
            for key in merged:
                for cik, val in facts.get(period, {}).get(key, {}).items():
                    merged[key].setdefault(cik, val)
        return merged

    # ---- who could ever clear, on fundamentals alone ----
    eligible = set()
    for when in dates:
        f = facts_on(when)
        for sym, cik in listed.items():
            cash, liab, sh = f["cash"].get(cik), f["liab"].get(cik), f["shares"].get(cik)
            if not cash or liab is None or not sh or sh <= 0:
                continue
            if cash - liab >= MIN_NET_CASH:
                eligible.add(sym)
    print(f"\n{len(eligible):,} symbols could clear both gates at some point on "
          f"fundamentals alone (net cash >= ${MIN_NET_CASH:,}).")

    sample = sorted(eligible)
    random.Random(SAMPLE_SEED).shuffle(sample)
    sample = sorted(sample[:SAMPLE_SIZE])
    print(f"Studying a seeded random sample of {len(sample):,} of them.\n")

    # ---- prices, one read per symbol, reused by every date ----
    span_start = FIRST_OBSERVATION - timedelta(days=30)
    span_end = min(date.today(), LAST_OBSERVATION + timedelta(days=300))
    # The benchmark goes first. Without it there is nothing to compare against
    # and the run is wasted, so we find that out in ten seconds rather than
    # twenty-five minutes.
    history = {}
    bench = None
    for candidate in BENCHMARKS:
        bench = fetch_history(candidate, span_start, span_end,
                              asset_classes=("etf", "stocks", "index"))
        if len(bench or []) > 200:
            globals()["BENCHMARK"] = candidate
            history[candidate] = bench
            print(f"Benchmark {candidate}: {len(bench):,} sessions.\n")
            break
        print(f"Benchmark {candidate}: no usable history, trying the next one.")
        bench = None
    if not bench:
        print("No benchmark history; refusing to report returns without one.")
        return 1

    for i, sym in enumerate(sample, start=1):
        series = fetch_history(sym, span_start, span_end)
        if len(series) > 200:
            history[sym] = series
        if i % 25 == 0:
            print(f"  ...{i}/{len(sample)} price histories")
        time.sleep(0.2)
    print(f"{len(history):,} usable histories.\n")

    picks, per_date = [], []
    for when in dates:
        f = facts_on(when)
        bi = index_on_or_before(bench, when)
        if bi is None:
            continue
        cleared = []
        for sym in sample:
            series = history.get(sym)
            if not series:
                continue
            cik = listed.get(sym)
            cash, liab, sh = f["cash"].get(cik), f["liab"].get(cik), f["shares"].get(cik)
            if not cash or liab is None or not sh or sh <= 0:
                continue
            net_cash = cash - liab
            if net_cash <= 0:
                continue

            i = index_on_or_before(series, when)
            if i is None or i < DROP_WINDOW_DAYS:
                continue
            price = series[i][1]
            if not price:
                continue

            cushion = (net_cash / sh) / price * 100.0
            if cushion < CASH_GATE:
                continue
            move = pct(price, series[i - DROP_WINDOW_DAYS][1])
            if move is None or move > DROP_THRESHOLD:
                continue
            if price * sh < MIN_MARKET_CAP:
                continue
            if price * series[i][2] < MIN_DOLLAR_VOLUME:
                continue

            row = {"date": when.isoformat(), "symbol": sym,
                   "price": round(price, 2), "cushion_pct": round(cushion, 1),
                   "move_pct": round(move, 1)}
            for label, ahead in HORIZONS.items():
                j, bj = i + ahead, bi + ahead
                if j < len(series) and bj < len(bench):
                    r = pct(series[j][1], price)
                    b = pct(bench[bj][1], bench[bi][1])
                    row[label] = None if r is None else round(r, 2)
                    row[label + "_excess"] = None if r is None or b is None else round(r - b, 2)
            cleared.append(row)

        picks.extend(cleared)
        per_date.append({"date": when.isoformat(), "cleared": len(cleared)})

    print(f"{len(picks):,} picks across {len(dates)} dates "
          f"(median {statistics.median([p['cleared'] for p in per_date]) if per_date else 0} per date).\n")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": [FIRST_OBSERVATION.isoformat(), LAST_OBSERVATION.isoformat()],
        "rules": {"cash_gate": CASH_GATE, "drop_threshold": DROP_THRESHOLD,
                  "drop_window_days": DROP_WINDOW_DAYS,
                  "min_market_cap": MIN_MARKET_CAP},
        "sample": {"size": len(sample), "seed": SAMPLE_SEED, "eligible": len(eligible)},
        "benchmark": BENCHMARK,
        "limitations": [
            "Universe is today's listed symbols: delisted and bankrupt names are "
            "absent, so every figure here is an upper bound.",
            "A seeded random sample of the eligible universe, so counts per date "
            "are a fraction of the live screen's.",
            "Runway and dilution tests are not applied; both mostly reject names.",
            "No costs, no slippage, no position sizing. Equal weight, hold to horizon.",
        ],
        "per_date": per_date,
        "picks": picks,
        "summary": {},
    }

    for label in HORIZONS:
        raw = [p[label] for p in picks if p.get(label) is not None]
        exc = [p[label + "_excess"] for p in picks if p.get(label + "_excess") is not None]
        result["summary"][label] = {"return": summarise(raw), "excess_vs_" + BENCHMARK: summarise(exc)}

    with open(OUT_FILE, "w") as fh:
        json.dump(result, fh, indent=2)

    print("=" * 72)
    for label in HORIZONS:
        s = result["summary"][label]
        print(f"\n{label.upper()} after the screen cleared")
        for key in ("return", "excess_vs_" + BENCHMARK):
            v = s[key]
            if not v:
                print(f"  {key:<18} no data")
                continue
            print(f"  {key:<18} n={v['n']:<5} median {v['median']:+.2f}%   "
                  f"mean {v['mean']:+.2f}%   p25 {v['p25']:+.2f}%   p75 {v['p75']:+.2f}%   "
                  f"hit {v['hit_rate_pct']:.1f}%")
    print("\n" + "=" * 72)
    for line in result["limitations"]:
        print("  - " + line)
    print(f"\nWritten to {OUT_FILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
