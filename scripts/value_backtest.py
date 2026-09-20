"""Backtest of the value screen, against the rules frozen in VALUE_SPEC.md.

It imports value_core, so what is tested here is the screen that will run on
the site -- not a second implementation that can quietly drift from it.

Three things this run is trying not to do:

  * See the future. Every fundamental comes from a filing dated on or before
    the observation date (value_data), and every price from a session on or
    before it.
  * Grade itself on a curve. The judgement rule -- decile 1 beats decile 10
    with a monotone gradient, after costs, in the holdout too -- was written
    down before the first run.
  * Pretend survivorship away. Names whose price series stops during a holding
    period are delisted, not absent, and the headline number assumes they lost
    half their value. The optimistic and pessimistic cases are printed beside
    it so the reader can see how much the assumption is carrying.
"""

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen

import value_core as vc
import value_data as vd
import value_prices as vp

# --------------------------------------------------------------------------
# Run settings
# --------------------------------------------------------------------------

FIRST_Q = tuple(int(x) for x in os.environ.get("FIRST_Q", "2015,1").split(","))
LAST_Q = tuple(int(x) for x in os.environ.get("LAST_Q", "2025,2").split(","))
FIRST_DATE = date.fromisoformat(os.environ.get("FIRST_DATE", "2016-01-01"))
LAST_DATE = date.fromisoformat(os.environ.get("LAST_DATE", "2025-03-01"))
HOLDOUT_FROM = date.fromisoformat(os.environ.get("HOLDOUT_FROM", "2023-10-01"))

HORIZONS = {"3m": 63, "6m": 126, "12m": 252}
DECILES = 10
MAX_SYMBOLS = int(os.environ.get("MAX_SYMBOLS", "0"))     # 0 = no cap
PRICE_WORKERS = int(os.environ.get("PRICE_WORKERS", "6"))
OUT_FILE = os.environ.get("OUT_FILE", "value_backtest.json")

# VALUE_SPEC.md section 5.5
COST_LARGE = 0.5          # round trip, %, for >= $1bn
COST_SMALL = 1.0          # round trip, %, for $300m - $1bn
LARGE_CAP = 1_000_000_000

# VALUE_SPEC.md section 6
DELISTED_CASES = {"optimistic": None, "mid": -50.0, "pessimistic": -100.0}
HEADLINE_CASE = "mid"

UA = os.environ.get("SEC_UA", "TheVanceReport research contact@thevancereport.com")


def log(*a):
    print(*a, flush=True)          # Actions buffers otherwise and shows nothing


# --------------------------------------------------------------------------
# Tickers and prices
# --------------------------------------------------------------------------

def _get(url, timeout=60, attempts=3):
    last = None
    for i in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:                          # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def ticker_map():
    """cik -> ticker, from the SEC's own list.

    This list holds current registrants. Companies that were delisted before
    today are missing from it, and that is the one piece of survivorship this
    run cannot fix -- it is measured and reported rather than hidden.
    """
    raw = json.loads(_get("https://www.sec.gov/files/company_tickers.json"))
    out = {}
    for row in raw.values():
        cik, tic = row.get("cik_str"), (row.get("ticker") or "").strip().upper()
        if cik and tic and "." not in tic and "-" not in tic:
            out.setdefault(int(cik), tic)
    return out


def fetch_prices(symbol, start=None, end=None):
    """Delegates to value_prices, which tries several sources and records why
    each one refused. The first smoke run got zero histories and the log could
    not say why; that is what this indirection buys."""
    return vp.fetch_prices(symbol,
                           start or date(FIRST_DATE.year - 2, 1, 1),
                           end or date.today())


def idx_on_or_before(days, when):
    lo, hi, best = 0, len(days) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if days[mid] <= when:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# --------------------------------------------------------------------------
# Rebalance dates
# --------------------------------------------------------------------------

def month_starts(first, last):
    out, y, m = [], first.year, first.month
    while date(y, m, 1) <= last:
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Conditional slices
#
# The headline run ranks every eligible company against every other, which
# assumes the screen means the same thing for a $400m company as for a $40bn
# one. That assumption is worth testing rather than believing: the value effect
# is widely reported to be stronger in smaller companies, and if it is, a
# single pooled number is an average of a real signal and no signal at all.
#
# This is EXPLORATORY. Slicing a result several ways and reporting the best
# slice is how people fool themselves. Anything found here is a hypothesis for
# a fresh pre-registration, not a finding, and the output says so.
# --------------------------------------------------------------------------

def size_bucket(cap):
    if not isinstance(cap, (int, float)):
        return None
    if cap < 1_000_000_000:
        return "300m-1bn"
    if cap < 5_000_000_000:
        return "1-5bn"
    if cap < 20_000_000_000:
        return "5-20bn"
    return "20bn+"


def profit_bucket(row):
    ebit = row.get("ebit")
    if not isinstance(ebit, (int, float)):
        return None
    return "profitable" if ebit > 0 else "loss-making"


def asset_bucket(row):
    """Asset-light or asset-heavy, by revenue per dollar of assets. Gross
    profit over assets structurally favours the asset-light, so it is worth
    knowing whether the whole score inherits that tilt."""
    rev, assets = row.get("revenue"), row.get("assets")
    if not isinstance(rev, (int, float)) or not isinstance(assets, (int, float)) or assets <= 0:
        return None
    return "asset-light" if rev / assets >= 0.7 else "asset-heavy"


SLICES = {
    "size": lambda r: size_bucket(r.get("market_cap")),
    "profitability": profit_bucket,
    "asset intensity": asset_bucket,
    "sector": lambda r: r.get("sector"),
}


def describe(values):
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    return {
        "n": n,
        "median": round(statistics.median(v), 2),
        "mean": round(statistics.mean(v), 2),
        "p25": round(v[n // 4], 2),
        "p75": round(v[(3 * n) // 4], 2),
        "hit_rate_pct": round(100.0 * sum(1 for x in v if x > 0) / n, 1),
    }


def block_bootstrap_ci(by_date, draws=5000, seed=17):
    """95% interval for a mean, resampling whole dates.

    Names picked on the same day share whatever moved the market that day, so
    treating them as independent observations would make the interval far too
    narrow. That mistake is how a crash-and-bounce month turns into a finding.
    """
    import random
    dates = list(by_date)
    if len(dates) < 3:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pool = []
        for _ in range(len(dates)):
            pool.extend(by_date[rng.choice(dates)])
        if pool:
            means.append(statistics.mean(pool))
    if not means:
        return None
    means.sort()
    return [round(means[int(0.025 * len(means))], 2),
            round(means[int(0.975 * len(means))], 2)]


def monotone_score(decile_means):
    """Share of adjacent decile pairs that step the right way.

    Reported for colour, but NOT used as the gate. Adjacent deciles hold
    neighbouring scores and are not statistically distinguishable from each
    other, so their pairwise order is close to a coin flip even when the
    gradient across all ten is unmistakable. Gating on it would be grading the
    noise. See gradient_rho.
    """
    steps = [decile_means[i] >= decile_means[i + 1] for i in range(len(decile_means) - 1)]
    return round(100.0 * sum(steps) / len(steps), 1) if steps else 0.0


def gradient_rho(decile_means):
    """Spearman correlation between decile number and decile mean.

    -1.0 means decile 1 is best and every step down the table is worse; 0.0
    means the ranking carries no information. This is the gradient test from
    VALUE_SPEC.md section 5.1: it asks whether the whole table is ordered,
    which is the claim, rather than whether each neighbouring pair happens to
    be, which is not.
    """
    n = len(decile_means)
    if n < 3:
        return 0.0
    order = sorted(range(n), key=lambda i: decile_means[i])
    rank = [0.0] * n
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and decile_means[order[end]] == decile_means[order[pos]]:
            end += 1
        share = sum(range(pos, end)) / (end - pos) + 1.0
        for k in range(pos, end):
            rank[order[k]] = share
        pos = end
    xs = list(range(1, n + 1))
    mx, my = statistics.mean(xs), statistics.mean(rank)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, rank))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in rank)) ** 0.5
    return round(num / den, 3) if den else 0.0


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def main():
    log(__doc__.strip().split("\n\n")[0])
    log()

    if os.environ.get("PROBE_ONLY", "").lower() in ("1", "true", "yes"):
        log("PROBE ONLY: checking the price sources, not running the backtest.\n")
        for sym in ("AAPL", "MSFT", "JNJ", "PLAB", "F", "IWM"):
            got = fetch_prices(sym, date(2015, 1, 1), date.today())
            if got:
                d, c, _ = got
                log(f"  {sym:6s} {len(d):,} sessions, {d[0]} to {d[-1]}, "
                    f"last close {c[-1]:.2f}")
            else:
                log(f"  {sym:6s} nothing")
        log("\n" + vp.report())
        return 0

    quarters = vd.quarters_between(FIRST_Q, LAST_Q)
    log(f"Reading {len(quarters)} SEC data sets, {quarters[0]} to {quarters[-1]}.")
    facts, subs = {}, {}
    for i, q in enumerate(quarters, start=1):
        try:
            vd.load_quarter(q, facts, subs)
        except Exception as exc:                          # noqa: BLE001
            log(f"  {q}: SKIPPED ({exc})")
            continue
        if i % 4 == 0 or i == len(quarters):
            log(f"  {q}: {len(subs):,} filers, {len(facts):,} with figures")
    if not facts:
        log("No SEC data; nothing to test.")
        return 1
    vd.tidy(facts)

    tickers = ticker_map()
    log(f"\n{len(tickers):,} CIKs carry a current ticker.")

    # Who we could even try to price. The gap between this and the filer count
    # is the survivorship this run cannot repair, and it is reported, not
    # quietly dropped.
    filers = set(subs)
    mapped = {c: tickers[c] for c in filers if c in tickers}
    log(f"{len(filers):,} filers in the window; {len(mapped):,} of them are still listed "
        f"({100.0 * len(mapped) / max(len(filers), 1):.1f}%).")
    log(f"The other {len(filers) - len(mapped):,} were delisted, acquired or wound up "
        f"before today and cannot be priced from free data. See 'irreducible' below.")

    symbols = sorted(set(mapped.values()))
    if MAX_SYMBOLS:
        symbols = symbols[:MAX_SYMBOLS]
    log(f"\nFetching daily closes for {len(symbols):,} symbols.")

    prices = {}
    done = [0]

    def grab(sym):
        p = fetch_prices(sym)
        done[0] += 1
        if done[0] % 250 == 0:
            log(f"  ...{done[0]:,}/{len(symbols):,}")
        return sym, p

    with ThreadPoolExecutor(max_workers=PRICE_WORKERS) as pool:
        for sym, p in pool.map(grab, symbols):
            if p:
                prices[sym] = p
    log(f"{len(prices):,} usable price histories.")
    log(vp.report() + "\n")
    if len(prices) < 200:
        log("Too few price histories to say anything. Stopping.")
        log("The refusal breakdown above says which source failed and how; "
            "fix that rather than re-running this unchanged.")
        return 1

    data_end = max(days[-1] for days, _c, _v in prices.values())
    log(f"Price data runs to {data_end}.")

    by_cik = {c: t for c, t in mapped.items() if t in prices}

    dates = month_starts(FIRST_DATE, LAST_DATE)
    log(f"{len(dates)} monthly rebalances, {dates[0]} to {dates[-1]}, "
        f"holdout from {HOLDOUT_FROM}.\n")

    # decile -> horizon -> case -> {date: [returns]}
    buckets = {d: {h: {c: {} for c in DELISTED_CASES} for h in HORIZONS}
               for d in range(1, DECILES + 1)}
    holdout = {d: {h: {c: {} for c in DELISTED_CASES} for h in HORIZONS}
               for d in range(1, DECILES + 1)}
    universe_sizes, unranked_counts, delisted_counts, top_names = [], [], [], []
    slices = {}

    for when in dates:
        rows = []
        for cik, sym in by_cik.items():
            days, closes, vols = prices[sym]
            i = idx_on_or_before(days, when)
            if i is None or i < vc.MIN_PRICE_SESSIONS:
                continue
            g = vd.figures_for(cik, facts, when)
            if not g or g.get("shares") is None:
                continue
            meta = subs.get(cik) or {}
            price = closes[i]
            window = slice(max(0, i - 59), i + 1)
            dollar_vols = [closes[k] * vols[k] for k in range(window.start, window.stop)]
            row = dict(g)
            row.update(
                symbol=sym, cik=cik, sic=meta.get("sic"), name=meta.get("name"),
                price=price, market_cap=price * g["shares"],
                dollar_volume=statistics.median(dollar_vols) if dollar_vols else 0.0,
                price_sessions=i, quarters_filed=meta.get("filings", 0),
                _i=i, _sym=sym,
            )
            row["ma_200"] = sum(closes[i - 199:i + 1]) / 200.0 if i >= 199 else None
            if i >= 252:
                past, recent = closes[i - 252], closes[i - 21]
                row["momentum_12_1"] = (recent - past) / past if past > 0 else None
            age = (when - meta["last_filed"]).days if meta.get("last_filed") else None
            ok, _why = vc.passes_universe(row, filing_age_days=age)
            if ok:
                rows.append(row)

        if len(rows) < DECILES * 5:
            continue

        scored, unranked = vc.score_rows(rows)
        if len(scored) < DECILES * 5:
            continue
        universe_sizes.append(len(scored))
        unranked_counts.append(len(unranked))

        # forward returns, with delisting handled rather than assumed away
        per_horizon = {}
        vanished_here = 0
        for h, sessions in HORIZONS.items():
            outcomes = []
            for r in scored:
                days, closes, _v = prices[r["_sym"]]
                i = r["_i"]
                j = i + sessions
                if j < len(days):
                    raw = (closes[j] - closes[i]) / closes[i] * 100.0
                    outcomes.append((r, raw, False))
                elif days[-1] < data_end - timedelta(days=20):
                    # the series stops well before the data does: delisted
                    outcomes.append((r, None, True))
                    vanished_here += 1
                # else: simply not enough forward data yet -- dropped for every
                # decile alike, which is fair even if it is a smaller sample
            per_horizon[h] = outcomes
        delisted_counts.append(vanished_here / max(len(HORIZONS), 1))

        # exploratory: same returns, cut by company type
        if when < HOLDOUT_FROM:
            live6 = per_horizon.get("6m") or []
            if live6:
                ok6 = [(r, v) for r, v, gone in live6 if not gone]
                if ok6:
                    bench6 = statistics.mean([v for _r, v in ok6])
                    ranked6 = sorted(ok6, key=lambda rv: -rv[0]["score"])
                    size6 = max(1, len(ranked6) // DECILES)
                    for pos, (r, v) in enumerate(ranked6):
                        d = min(DECILES, pos // size6 + 1)
                        cost = COST_LARGE if (r.get("market_cap") or 0) >= LARGE_CAP else COST_SMALL
                        for slice_name, fn in SLICES.items():
                            try:
                                key = fn(r)
                            except Exception:            # noqa: BLE001
                                key = None
                            if not key:
                                continue
                            slices.setdefault(slice_name, {}).setdefault(key, {}).setdefault(
                                d, []).append(v - bench6 - cost)

        target = holdout if when >= HOLDOUT_FROM else buckets
        for h, outcomes in per_horizon.items():
            if not outcomes:
                continue
            live = [o[1] for o in outcomes if not o[2]]
            for case, assumed in DELISTED_CASES.items():
                filler = statistics.mean(live) if (assumed is None and live) else assumed
                if filler is None:
                    continue
                vals = [(r, (filler if gone else raw)) for r, raw, gone in outcomes]
                bench = statistics.mean([v for _r, v in vals])
                ranked = sorted(vals, key=lambda rv: -rv[0]["score"])
                size = max(1, len(ranked) // DECILES)
                for d in range(1, DECILES + 1):
                    lo = (d - 1) * size
                    hi = len(ranked) if d == DECILES else d * size
                    for r, v in ranked[lo:hi]:
                        cost = COST_LARGE if (r.get("market_cap") or 0) >= LARGE_CAP else COST_SMALL
                        target[d][h][case].setdefault(when.isoformat(), []).append(
                            v - bench - cost
                        )

        if scored:
            # The ranked list itself, not just a sample. This is the thing a
            # reader would actually be shown, so it is worth carrying out of
            # the run rather than reconstructing it later.
            top_names.append({
                "date": when.isoformat(),
                "universe": len(scored),
                "top": [{"symbol": r["symbol"], "name": r.get("name"),
                         "sector": r.get("sector"), "score": r["score"],
                         "pillars": r["pillars"],
                         "price": round(r["price"], 2),
                         "market_cap_m": round((r.get("market_cap") or 0) / 1e6),
                         "ev_ebit": (lambda v: round(v, 1) if isinstance(v, float) else None)(r["metrics"].get("ev_ebit")),
                         "ev_fcf": (lambda v: round(v, 1) if isinstance(v, float) else None)(r["metrics"].get("ev_fcf")),
                         "net_issuance_pct": (lambda v: round(v * 100, 1) if isinstance(v, float) else None)(r["metrics"].get("net_issuance")),
                         } for r in scored[:25]],
            })

    if not universe_sizes:
        log("No rebalance produced a usable universe. Stopping.")
        return 1

    log(f"Median eligible universe: {int(statistics.median(universe_sizes)):,} names "
        f"(min {min(universe_sizes):,}, max {max(universe_sizes):,}).")
    log(f"Median unranked for want of data: {int(statistics.median(unranked_counts)):,}.")
    log(f"Median delisted during a holding period: {statistics.median(delisted_counts):.1f} "
        f"per rebalance.\n")

    result = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "spec": "scripts/VALUE_SPEC.md, frozen 2026-09-19 before this ran",
        "window": [FIRST_DATE.isoformat(), LAST_DATE.isoformat()],
        "holdout_from": HOLDOUT_FROM.isoformat(),
        "rebalances": len(universe_sizes),
        "median_universe": int(statistics.median(universe_sizes)),
        "weights": vc.PILLAR_WEIGHTS,
        "costs_pct": {"large": COST_LARGE, "small": COST_SMALL},
        "irreducible": {
            "filers_in_window": len(filers),
            "still_listed": len(mapped),
            "pct_still_listed": round(100.0 * len(mapped) / max(len(filers), 1), 1),
            "note": "Companies delisted before today carry no ticker and cannot be "
                    "priced from free data, so they are absent from every rebalance. "
                    "Results are therefore still an upper bound, though a tighter one "
                    "than a survivor-only universe: names that die DURING a holding "
                    "period are caught and charged.",
        },
        "delisting_cases": DELISTED_CASES,
        "headline_case": HEADLINE_CASE,
        "main": {}, "holdout": {}, "verdict": {},
        "sample_top_names": top_names[-12:],
        "scan_note": "top 25 of the ranked list at each of the last rebalances",
    }

    def summarise(store, label):
        out = {}
        log("=" * 72)
        log(label.upper())
        for h in HORIZONS:
            out[h] = {}
            for case in DELISTED_CASES:
                means = []
                for d in range(1, DECILES + 1):
                    flat = [v for vs in store[d][h][case].values() for v in vs]
                    means.append(statistics.mean(flat) if flat else 0.0)
                d1 = [v for vs in store[1][h][case].values() for v in vs]
                d10 = [v for vs in store[DECILES][h][case].values() for v in vs]
                out[h][case] = {
                    "decile_means": [round(m, 2) for m in means],
                    "spread_d1_minus_d10": round(means[0] - means[-1], 2),
                    "gradient_rho": gradient_rho(means),
                    "monotone_pct": monotone_score(means),
                    "decile_1": describe(d1),
                    "decile_10": describe(d10),
                    "decile_1_ci": block_bootstrap_ci(store[1][h][case]),
                }
                if case == HEADLINE_CASE:
                    log(f"\n{h} (delisted assumed {DELISTED_CASES[case]}%), "
                        f"excess vs the equal-weighted universe, after costs")
                    log("  deciles 1..10: " + "  ".join(f"{m:+.1f}" for m in means))
                    log(f"  decile 1 minus decile 10: {means[0] - means[-1]:+.2f}pp")
                    log(f"  gradient rho: {gradient_rho(means):+.3f} "
                        f"(-1 = perfectly ordered; {monotone_score(means):.0f}% of "
                        f"adjacent pairs step the right way)")
                    ci = out[h][case]["decile_1_ci"]
                    if ci:
                        log(f"  decile 1 mean 95% CI (date-block): [{ci[0]:+.2f}, {ci[1]:+.2f}]")
        return out

    result["main"] = summarise(buckets, f"main period {FIRST_DATE} to {HOLDOUT_FROM}")
    if any(holdout[1][h][HEADLINE_CASE] for h in HORIZONS):
        result["holdout"] = summarise(holdout, f"holdout {HOLDOUT_FROM} to {LAST_DATE}")

    # ----------------------------------------------------------------------
    # Exploratory: does the screen mean the same thing for every company?
    # ----------------------------------------------------------------------
    if slices:
        log("\n" + "=" * 72)
        log("EXPLORATORY -- 6 month excess by company type (main period only)")
        log("Hypothesis generation, not a finding. Reported because slicing a")
        log("result several ways and keeping the best slice is how people fool")
        log("themselves; acting on any of this needs its own pre-registration.")
        out = {}
        for slice_name, groups in slices.items():
            log(f"\n  {slice_name}")
            out[slice_name] = {}
            for key, by_decile in sorted(groups.items()):
                n = sum(len(v) for v in by_decile.values())
                if n < 500:
                    log(f"    {key:<16} only {n:,} observations, not reported")
                    continue
                means = []
                for d in range(1, DECILES + 1):
                    vals = by_decile.get(d) or []
                    means.append(statistics.mean(vals) if vals else 0.0)
                spread = means[0] - means[-1]
                rho = gradient_rho(means)
                d1 = describe(by_decile.get(1) or [])
                if d1 is None:
                    # Enough observations overall, none of them in the top
                    # decile -- a bucket that only ever appears low down the
                    # ranking. Reporting a spread off an empty cell would be
                    # inventing a number.
                    log(f"    {key:<16} n={n:,} but nothing in decile 1, not reported")
                    continue
                out[slice_name][key] = {
                    "n": n, "decile_means": [round(m, 2) for m in means],
                    "spread_d1_minus_d10": round(spread, 2),
                    "gradient_rho": rho, "decile_1": d1,
                }
                log(f"    {key:<16} n={n:>7,}  d1-d10 {spread:+7.2f}pp  rho {rho:+.3f}"
                    f"  d1 median {d1['median']:+6.2f}  hit {d1['hit_rate_pct']:.0f}%")
        result["exploratory"] = {
            "note": "Post-hoc slices of the main period at six months. Hypothesis "
                    "generation only: these cuts were chosen after the headline "
                    "result was known, so their apparent significance is not "
                    "honest significance. Any rule change arising from them needs "
                    "a fresh pre-registration and an untouched holdout.",
            "slices": out,
        }

    # The falsification rules from section 5, applied mechanically so the run
    # cannot talk itself into a pass.
    log("\n" + "=" * 72)
    log("AGAINST THE PRE-REGISTERED TEST")
    verdict = {}
    for h in HORIZONS:
        m = result["main"].get(h, {}).get(HEADLINE_CASE)
        ho = result["holdout"].get(h, {}).get(HEADLINE_CASE) if result["holdout"] else None
        if not m:
            continue
        checks = {
            "d1_beats_d10": m["spread_d1_minus_d10"] > 0,
            "gradient_is_ordered": m["gradient_rho"] <= -0.6,
            "decile_1_ci_excludes_zero": bool(m["decile_1_ci"]) and m["decile_1_ci"][0] > 0,
            "holds_in_holdout": bool(ho) and ho["spread_d1_minus_d10"] > 0,
            "survives_pessimistic_case":
                result["main"][h]["pessimistic"]["spread_d1_minus_d10"] > 0,
        }
        verdict[h] = {"checks": checks, "passes": all(checks.values())}
        log(f"\n{h}:")
        for k, v in checks.items():
            log(f"  {'PASS' if v else 'FAIL'}  {k}")
        log(f"  => {'SUPPORTED' if all(checks.values()) else 'NOT SUPPORTED'}")
    result["verdict"] = verdict

    log("\n" + "=" * 72)
    log("What this still cannot tell you:")
    log("  - " + result["irreducible"]["note"])
    log("  - Costs are a flat assumption, not measured spreads.")
    log("  - Equal weight, held to the horizon, no sizing and no risk control.")

    with open(OUT_FILE, "w") as fh:
        json.dump(result, fh, indent=2)
    log(f"\nWritten to {OUT_FILE}.")
    write_summary(result)
    return 0


def write_summary(result):
    """Put the result on the run page itself, not only in the log.

    A forty-minute run that ends with its answer buried in a log the browser
    loads thirty lines at a time is a run you have to do again to read. The
    step summary renders straight into the run page, so the numbers survive
    the run without anyone having to download anything.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    L = []
    w = L.append
    w(f"## Value screen backtest\n")
    w(f"{result['rebalances']} rebalances, {result['window'][0]} to {result['window'][1]}, "
      f"median universe {result['median_universe']:,}. Holdout from {result['holdout_from']}.\n")
    irr = result["irreducible"]
    w(f"Filers in window {irr['filers_in_window']:,}; still listed today "
      f"{irr['still_listed']:,} ({irr['pct_still_listed']}%).\n")

    w("### Headline: excess vs the equal-weighted universe, after costs\n")
    w("| horizon | d1-d10 | rho | d1 mean | d1 median | d1 hit | d1 95% CI | holdout d1-d10 | verdict |")
    w("|---|---|---|---|---|---|---|---|---|")
    for h in HORIZONS:
        m = result["main"].get(h, {}).get(HEADLINE_CASE)
        if not m:
            continue
        ho = (result["holdout"].get(h, {}) or {}).get(HEADLINE_CASE)
        ci = m["decile_1_ci"]
        d1 = m["decile_1"] or {}
        cells = [
            h,
            f"{m['spread_d1_minus_d10']:+.2f}pp",
            f"{m['gradient_rho']:+.3f}",
            f"{d1['mean']:+.2f}" if d1 else "--",
            f"{d1['median']:+.2f}" if d1 else "--",
            f"{d1['hit_rate_pct']:.0f}%" if d1 else "--",
            f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "--",
            f"{ho['spread_d1_minus_d10']:+.2f}pp" if ho else "--",
            "SUPPORTED" if result["verdict"].get(h, {}).get("passes") else "not supported",
        ]
        w("| " + " | ".join(cells) + " |")
    w("")

    exp = result.get("exploratory")
    if exp:
        w("### Exploratory: six-month excess by company type\n")
        w("_Post-hoc slices chosen after the headline was known. Hypothesis "
          "generation, not a finding._\n")
        for slice_name, groups in exp["slices"].items():
            if not groups:
                continue
            w(f"**{slice_name}**\n")
            w("| bucket | n | d1-d10 | rho | d1 median | d1 hit |")
            w("|---|---|---|---|---|---|")
            for key, g in groups.items():
                d1 = g["decile_1"] or {}
                w(f"| {key} | {g['n']:,} | {g['spread_d1_minus_d10']:+.2f}pp | "
                  f"{g['gradient_rho']:+.3f} | {d1.get('median', 0):+.2f} | "
                  f"{d1.get('hit_rate_pct', 0):.0f}% |")
            w("")

    w("<details><summary>Full result JSON</summary>\n")
    w("```json")
    w(json.dumps(result, separators=(",", ":")))
    w("```\n</details>")
    try:
        with open(path, "a") as fh:
            fh.write("\n".join(L) + "\n")
    except OSError as exc:                                  # noqa: BLE001
        log(f"Could not write the step summary: {exc}")


if __name__ == "__main__":
    sys.exit(main())
