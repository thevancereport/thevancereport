"""Does cointegration-based pairs trading have an edge after costs?

The rules are frozen in PAIRS_SPEC.md, written and committed before this
produced a number. Read §0 there first: the published record says this
strategy stopped working once realistic costs were applied, so the expected
answer is no. This exists to find out properly rather than to guess.

Nothing here writes anything the site reads.
"""

import json
import os
import random
import statistics
import sys
from datetime import date, timedelta

import numpy as np

try:
    from statsmodels.tsa.stattools import coint
except ImportError:      # pragma: no cover - the workflow pip-installs it
    # The offline tests stub this. statsmodels is not importable in every
    # environment, and its coint() is its code, well tested by them; what
    # needs testing here is the trading logic, the FDR correction and the
    # cost arithmetic, all of which are mine.
    coint = None

import value_core as vc
import value_data as vd
import value_prices as vp

FORM_DAYS = int(os.environ.get("FORM_DAYS", "252"))     # formation window
TRADE_DAYS = int(os.environ.get("TRADE_DAYS", "126"))   # trading window
STEP_DAYS = int(os.environ.get("STEP_DAYS", "126"))     # gap between formations
ENTRY_Z = float(os.environ.get("ENTRY_Z", "2.0"))
FDR_Q = float(os.environ.get("FDR_Q", "0.05"))
MAX_PER_SECTOR = int(os.environ.get("MAX_PER_SECTOR", "40"))
MIN_DV = float(os.environ.get("MIN_DV", "1e7"))
MIN_PRICE = float(os.environ.get("MIN_PRICE_PAIRS", "5.0"))
COST_LEG_BP = float(os.environ.get("COST_LEG_BP", "20"))     # round trip, per leg
BORROW_BP = float(os.environ.get("BORROW_BP", "100"))        # annualised, short leg
HOLDOUT_FROM = date.fromisoformat(os.environ.get("HOLDOUT_FROM", "2023-01-01"))
FIRST_DATE = date.fromisoformat(os.environ.get("FIRST_DATE", "2016-01-01"))
LAST_DATE = date.fromisoformat(os.environ.get("LAST_DATE", "2025-03-01"))
MAX_SYMBOLS = int(os.environ.get("MAX_SYMBOLS", "0"))
OUT_FILE = os.environ.get("OUT_FILE", "pairs_study.json")


def log(*a):
    print(*a, flush=True)


def _coint():
    """Looked up through the module so a test can substitute it."""
    return sys.modules[__name__].coint


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def benjamini_hochberg(pvals, q):
    """Indices surviving BH false-discovery-rate control at q.

    Without this the study is a machine for manufacturing signals: at p<=0.05
    on twenty thousand pairs, a thousand pass on noise alone.
    """
    n = len(pvals)
    if not n:
        return set()
    order = sorted(range(n), key=lambda i: pvals[i])
    keep = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / n:
            keep = rank
    return set(order[:keep])


def block_bootstrap_ci(by_window, draws=5000, seed=17):
    """Resample whole formation windows, not individual trades.

    Trades opened in the same window share a market, so treating them as
    independent draws would understate the interval badly.
    """
    keys = [k for k, v in by_window.items() if v]
    if len(keys) < 3:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [rng.choice(keys) for _ in keys]
        vals = [x for k in pick for x in by_window[k]]
        if vals:
            means.append(sum(vals) / len(vals))
    if not means:
        return None
    means.sort()
    return (round(means[int(0.025 * len(means))], 4),
            round(means[int(0.975 * len(means))], 4))


def describe(vals):
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean_pct": round(100 * statistics.mean(vals), 3),
        "median_pct": round(100 * statistics.median(vals), 3),
        "hit_rate_pct": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
        "worst_pct": round(100 * min(vals), 2),
        "best_pct": round(100 * max(vals), 2),
    }


# --------------------------------------------------------------------------
# one pair, one window
# --------------------------------------------------------------------------

def formation_stats(la, lb):
    """OLS fit and spread moments over the formation window.

    Returns the intercept as well as the slope. An earlier version returned
    only beta and left the caller to run its own regression for alpha, which
    is two chances to disagree about the same line -- and a test that passed
    a mismatched alpha opened a trade on a pair whose prices never moved.

    Logs, so the hedge ratio is a proportion and the spread is scale-free.
    """
    x = np.vstack([np.ones(len(lb)), lb]).T
    fit = np.linalg.lstsq(x, la, rcond=None)[0]
    alpha, beta = float(fit[0]), float(fit[1])
    spread = la - (alpha + beta * lb)
    return beta, alpha, float(spread.mean()), float(spread.std(ddof=1))


def run_trade(la_t, lb_t, beta, mu, sigma, alpha):
    """Walk the trading window with formation-period mu and sigma.

    The z-score never sees data from inside the trade. Recomputing the mean
    and standard deviation on the trading window is the mistake that makes
    every backtest of this look good: the last observation ends up helping
    to define its own extremity.
    """
    if sigma <= 0:
        return None
    spread = la_t - (alpha + beta * lb_t)
    z = (spread - mu) / sigma
    entry = None
    for i in range(len(z)):
        if entry is None:
            if abs(z[i]) >= ENTRY_Z:
                entry = i
                side = -1 if z[i] > 0 else 1      # -1 = short A / long B
        else:
            crossed = (z[i] <= 0) if z[entry] > 0 else (z[i] >= 0)
            if crossed:
                return _pnl(la_t, lb_t, beta, entry, i, side)
    if entry is not None:
        return _pnl(la_t, lb_t, beta, entry, len(z) - 1, side)
    return None


def _pnl(la_t, lb_t, beta, i, j, side):
    """Return on capital committed, after costs, for one round trip.

    Equal dollars on each leg at entry, so the return is the average of the
    two legs' returns with the short leg's sign flipped. Costs are charged
    on both legs, plus borrow on the short leg for the days held.
    """
    ra = float(np.exp(la_t[j] - la_t[i]) - 1.0)
    rb = float(np.exp(lb_t[j] - lb_t[i]) - 1.0)
    gross = 0.5 * (side * ra + (-side) * rb)
    days = max(j - i, 1)
    cost = 2 * (COST_LEG_BP / 10000.0)
    borrow = 0.5 * (BORROW_BP / 10000.0) * (days / 252.0)
    return {"ret": gross - cost - borrow, "gross": gross,
            "days": days, "beta": round(float(beta), 3)}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def sessions_between(days, a, b):
    return [i for i, d in enumerate(days) if a <= d <= b]


def main():
    if coint is None:
        log("statsmodels is not installed; cannot run the cointegration test.")
        return 1
    log(__doc__.strip().split("\n\n")[0])
    log(f"Spec: PAIRS_SPEC.md. Formation {FORM_DAYS}d, trading {TRADE_DAYS}d, "
        f"entry |z|>={ENTRY_Z}, BH q={FDR_Q}.\n")

    quarters = []
    y, q = FIRST_DATE.year, 1
    while (y, q) <= (LAST_DATE.year, (LAST_DATE.month - 1) // 3 + 1):
        quarters.append(f"{y}q{q}")
        q += 1
        if q > 4:
            y, q = y + 1, 1
    facts, subs = {}, {}
    for qq in quarters:
        try:
            vd.load_quarter(qq, facts, subs)
        except Exception as exc:                            # noqa: BLE001
            log(f"  {qq}: skipped ({exc})")
    log(f"{len(subs):,} filers read from {len(quarters)} SEC data sets.")
    vd.tidy(facts)

    import value_backtest as vb
    tickers = vb.ticker_map()
    mapped = {c: tickers[c] for c in subs if c in tickers}
    symbols = sorted(set(mapped.values()))
    if MAX_SYMBOLS:
        symbols = symbols[:MAX_SYMBOLS]
    log(f"{len(symbols):,} carry a ticker. Fetching prices.")

    prices = {}
    from concurrent.futures import ThreadPoolExecutor
    done = [0]

    def grab(s):
        p = vp.fetch_prices(s, date(FIRST_DATE.year - 2, 1, 1), LAST_DATE + timedelta(days=TRADE_DAYS * 2))
        done[0] += 1
        if done[0] % 500 == 0:
            log(f"  ...{done[0]:,}/{len(symbols):,}")
        return s, p

    with ThreadPoolExecutor(max_workers=6) as pool:
        for s, p in pool.map(grab, symbols):
            if p:
                prices[s] = p
    log(f"{len(prices):,} usable price histories.")
    log(vp.report() + "\n")
    if len(prices) < 200:
        log("Too few price histories to study pairs. Stopping.")
        return 1

    sector = {}
    for cik, sym in mapped.items():
        s = (subs.get(cik) or {}).get("sic")
        if not vc.is_excluded_sector(s):
            sector[sym] = vc.sector_of(s)

    # formation window start dates
    all_days = sorted({d for v in prices.values() for d in v[0]})
    starts = []
    d = FIRST_DATE
    while d <= LAST_DATE:
        starts.append(d)
        d += timedelta(days=int(STEP_DAYS * 365 / 252))
    log(f"{len(starts)} formation windows, {starts[0]} to {starts[-1]}.\n")

    trades, by_window, tested_total, survived_total = [], {}, 0, 0
    for w, start in enumerate(starts, start=1):
        form_end = start
        # index positions per symbol
        elig = []
        for sym, (days, closes, vols) in prices.items():
            if sym not in sector:
                continue
            k = vb.idx_on_or_before(days, form_end)
            if k is None or k < FORM_DAYS or k + TRADE_DAYS >= len(days):
                continue
            if closes[k] < MIN_PRICE:
                continue
            dv = statistics.median([closes[i] * vols[i] for i in range(k - 59, k + 1)])
            if dv < MIN_DV:
                continue
            elig.append((sym, k, dv))
        # keep the most liquid per sector
        bysec = {}
        for sym, k, dv in elig:
            bysec.setdefault(sector[sym], []).append((dv, sym, k))
        pool_syms = []
        for sec, lst in bysec.items():
            lst.sort(reverse=True)
            pool_syms += [(sym, k, sec) for _dv, sym, k in lst[:MAX_PER_SECTOR]]

        pvals, cand = [], []
        for a in range(len(pool_syms)):
            sym_a, ka, sec_a = pool_syms[a]
            da, ca, _ = prices[sym_a]
            la = np.log(np.array(ca[ka - FORM_DAYS + 1:ka + 1], dtype=float))
            for b in range(a + 1, len(pool_syms)):
                sym_b, kb, sec_b = pool_syms[b]
                if sec_a != sec_b:
                    continue
                db, cb, _ = prices[sym_b]
                lb = np.log(np.array(cb[kb - FORM_DAYS + 1:kb + 1], dtype=float))
                if len(la) != len(lb):
                    continue
                try:
                    p = _coint()(la, lb)[1]
                except Exception:                            # noqa: BLE001
                    continue
                pvals.append(p)
                cand.append((sym_a, ka, sym_b, kb, la, lb))
        tested_total += len(pvals)
        keep = benjamini_hochberg(pvals, FDR_Q)
        raw = sum(1 for p in pvals if p <= FDR_Q)
        survived_total += len(keep)
        log(f"  window {w:>2} {start}: {len(pool_syms):,} names, {len(pvals):,} pairs tested, "
            f"{raw:,} at p<={FDR_Q} uncorrected, {len(keep):,} survive BH")

        key = start.isoformat()
        by_window.setdefault(key, [])
        for idx in keep:
            sym_a, ka, sym_b, kb, la, lb = cand[idx]
            beta, alpha, mu, sigma = formation_stats(la, lb)
            ca = prices[sym_a][1]; cb = prices[sym_b][1]
            la_t = np.log(np.array(ca[ka + 1:ka + 1 + TRADE_DAYS], dtype=float))
            lb_t = np.log(np.array(cb[kb + 1:kb + 1 + TRADE_DAYS], dtype=float))
            if len(la_t) < 10 or len(la_t) != len(lb_t):
                continue
            t = run_trade(la_t, lb_t, beta, mu, sigma, alpha)
            if t:
                t.update(window=key, a=sym_a, b=sym_b, sector=sector[sym_a],
                         holdout=start >= HOLDOUT_FROM, p=round(pvals[idx], 5))
                trades.append(t)
                by_window[key].append(t["ret"])

    if not trades:
        log("\nNo trades were opened at all. Nothing to test.")
        return 1

    log(f"\n{len(trades):,} trades from {tested_total:,} pair-tests "
        f"({survived_total:,} survived BH).\n")
    return report(trades, by_window, tested_total, survived_total)


def report(trades, by_window, tested_total, survived_total):
    main_t = [t for t in trades if not t["holdout"]]
    hold_t = [t for t in trades if t["holdout"]]
    rets = [t["ret"] for t in main_t]
    hrets = [t["ret"] for t in hold_t]

    main_windows = {k: v for k, v in by_window.items() if v and
                    date.fromisoformat(k) < HOLDOUT_FROM}
    ci = block_bootstrap_ci(main_windows)

    # excluding the single best window
    best = max(main_windows, key=lambda k: statistics.mean(main_windows[k])) if main_windows else None
    ex_best = [x for k, v in main_windows.items() if k != best for x in v]

    # doubled costs
    dbl = []
    for t in main_t:
        extra = 2 * (COST_LEG_BP / 10000.0) + 0.5 * (BORROW_BP / 10000.0) * (t["days"] / 252.0)
        dbl.append(t["ret"] - extra)

    res = {
        "spec": "scripts/PAIRS_SPEC.md",
        "pair_tests": tested_total,
        "survived_bh": survived_total,
        "trades": len(trades),
        "main": describe(rets),
        "holdout": describe(hrets),
        "excluding_best_window": describe(ex_best),
        "doubled_costs": describe(dbl),
        "mean_ci_95": ci,
        "best_window": best,
        "by_sector": {},
    }
    bysec = {}
    for t in main_t:
        bysec.setdefault(t["sector"], []).append(t["ret"])
    for k, v in sorted(bysec.items(), key=lambda kv: -len(kv[1]))[:12]:
        res["by_sector"][k] = describe(v)

    log("=" * 72)
    log("RESULT, after costs, per trade")
    for label, d in (("main period", res["main"]), ("holdout", res["holdout"]),
                     ("excluding best window", res["excluding_best_window"]),
                     ("doubled costs", res["doubled_costs"])):
        if d:
            log(f"  {label:<24} n={d['n']:>5}  mean {d['mean_pct']:+.3f}%  "
                f"median {d['median_pct']:+.3f}%  hit {d['hit_rate_pct']:.0f}%")
    if ci:
        log(f"  mean 95% CI (window bootstrap): [{100*ci[0]:+.3f}%, {100*ci[1]:+.3f}%]")

    checks = {
        "positive_after_costs": bool(res["main"] and res["main"]["mean_pct"] > 0),
        "ci_excludes_zero": bool(ci and ci[0] > 0),
        "not_one_period": bool(res["excluding_best_window"] and
                               res["excluding_best_window"]["mean_pct"] > 0),
        "holds_in_holdout": bool(res["holdout"] and res["holdout"]["mean_pct"] > 0),
        "survives_doubled_costs": bool(res["doubled_costs"] and
                                       res["doubled_costs"]["mean_pct"] > 0),
        "enough_trades": bool(res["main"] and res["main"]["n"] >= 200 and
                              res["holdout"] and res["holdout"]["n"] >= 30),
    }
    res["checks"] = checks
    res["supported"] = all(checks.values())

    log("\n" + "=" * 72)
    log("AGAINST THE PRE-REGISTERED TEST")
    for k, v in checks.items():
        log(f"  {'PASS' if v else 'FAIL'}  {k}")
    log(f"  => {'SUPPORTED' if res['supported'] else 'NOT SUPPORTED'}")
    log("\nUpper bound in any case: pairs whose leg delisted are absent from "
        "the universe entirely, and a leg going to zero is exactly how this "
        "strategy loses badly. See PAIRS_SPEC.md section 7.")

    with open(OUT_FILE, "w") as fh:
        json.dump(res, fh, indent=2)
    log(f"\nWritten to {OUT_FILE}.")
    write_summary(res)
    return 0


def write_summary(res):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    L = ["## Pairs study\n",
         f"{res['pair_tests']:,} pair-tests, {res['survived_bh']:,} survived "
         f"Benjamini-Hochberg, {res['trades']:,} trades.\n",
         "| slice | n | mean | median | hit |", "|---|---|---|---|---|"]
    for label, key in (("main period", "main"), ("holdout", "holdout"),
                       ("excluding best window", "excluding_best_window"),
                       ("doubled costs", "doubled_costs")):
        d = res.get(key)
        if d:
            L.append(f"| {label} | {d['n']:,} | {d['mean_pct']:+.3f}% | "
                     f"{d['median_pct']:+.3f}% | {d['hit_rate_pct']:.0f}% |")
    ci = res.get("mean_ci_95")
    if ci:
        L.append(f"\nMean 95% CI: [{100*ci[0]:+.3f}%, {100*ci[1]:+.3f}%]\n")
    L.append("### Pre-registered checks\n")
    for k, v in res["checks"].items():
        L.append(("- **PASS** " if v else "- FAIL ") + k)
    L.append(f"\n**{'SUPPORTED' if res['supported'] else 'NOT SUPPORTED'}**\n")
    L.append("<details><summary>Full result</summary>\n\n```json")
    L.append(json.dumps(res, separators=(",", ":")))
    L.append("```\n</details>")
    try:
        with open(path, "a") as fh:
            fh.write("\n".join(L) + "\n")
    except OSError as exc:                                  # noqa: BLE001
        log(f"Could not write the step summary: {exc}")


if __name__ == "__main__":
    sys.exit(main())
