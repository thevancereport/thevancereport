"""Offline tests for the pairs study.

statsmodels' coint() is statsmodels' problem; it is stubbed here. What needs
testing is mine: the false-discovery-rate correction, the out-of-sample
z-score, the entry and exit logic, and the cost arithmetic.

The test that matters most is the last one. A backtest that cannot find a
signal deliberately planted in the data is not evidence of anything when it
comes back empty, and this pipeline is expected to come back empty.
"""

import math
import random
import sys

import numpy as np

sys.path.insert(0, ".")
import pairs_study as ps          # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name +
          ("" if ok else f"   got={got!r} want={want!r}"))


def close(name, got, want, tol):
    ok = abs(got - want) <= tol
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r} +/- {tol}")
    print(("  ok   " if ok else "  FAIL ") + name +
          ("" if ok else f"   got={got!r} want~{want!r}"))


# --------------------------------------------------------------------------
print("\nBenjamini-Hochberg")
# All null: with p-values spread evenly, almost nothing should survive.
evenly = [(i + 1) / 1000 for i in range(1000)]
check("uniform p-values yield almost nothing", len(ps.benjamini_hochberg(evenly, 0.05)) <= 1, True)

# 50 real signals hiding in 950 nulls should mostly be found.
planted = [1e-6] * 50 + [0.2 + 0.8 * (i / 950) for i in range(950)]
kept = ps.benjamini_hochberg(planted, 0.05)
check("planted signals are recovered", len(kept) >= 50, True)
check("and nothing else creeps in", all(i < 50 for i in kept), True)

check("an empty list is handled", ps.benjamini_hochberg([], 0.05), set())
# The uncorrected count is what BH is protecting against.
_r = random.Random(4)
noise = [_r.random() for _ in range(2000)]   # one generator, not 2,000 identical draws
raw = sum(1 for p in noise if p <= 0.05)
check("BH is far stricter than raw p<=0.05",
      len(ps.benjamini_hochberg(noise, 0.05)) < raw, True)
print(f"       (raw kept {raw} of 2,000 pure-noise pairs; BH kept "
      f"{len(ps.benjamini_hochberg(noise, 0.05))})")

# --------------------------------------------------------------------------
print("\ncost arithmetic")
# Two legs that do not move at all must lose exactly the trading cost.
flat = np.zeros(30)
t = ps._pnl(flat, flat, 1.0, 0, 20, 1)
expect_cost = 2 * (ps.COST_LEG_BP / 10000.0) + 0.5 * (ps.BORROW_BP / 10000.0) * (20 / 252.0)
close("a flat pair loses exactly the costs", t["ret"], -expect_cost, 1e-12)
check("gross is zero when nothing moves", abs(t["gross"]) < 1e-12, True)
check("days held are recorded", t["days"], 20)

# A converging spread must make money gross.
la = np.log(np.array([100.0] * 10 + [96.0] * 10))
lb = np.log(np.array([100.0] * 20))
t2 = ps._pnl(la, lb, 1.0, 0, 19, -1)      # short A, which then falls
check("shorting the leg that falls is profitable gross", t2["gross"] > 0, True)
check("and costs reduce it", t2["ret"] < t2["gross"], True)

# --------------------------------------------------------------------------
print("\nthe z-score never sees inside the trade")
rng = np.random.default_rng(11)
form = rng.normal(0, 1, 252)
la_f = np.cumsum(rng.normal(0, 0.01, 252)) + 4.0
lb_f = la_f - 0.02 * form
beta, alpha_f, mu, sigma = ps.formation_stats(la_f, lb_f)
check("a hedge ratio comes back", isinstance(beta, float), True)
check("sigma is positive", sigma > 0, True)

# A trading window whose spread never reaches 2 sigma must open no trade.
quiet_a = np.full(126, la_f[-1])
quiet_b = np.full(126, lb_f[-1])
check("a quiet window opens nothing",
      ps.run_trade(quiet_a, quiet_b, beta, mu, sigma, alpha_f), None)
check("zero sigma is refused rather than dividing by it",
      ps.run_trade(quiet_a, quiet_b, beta, mu, 0.0, alpha_f), None)

# --------------------------------------------------------------------------
print("\na planted mean-reverting spread is found and traded")
# Build a pair whose spread is a genuinely stationary OU process. If the
# machinery cannot make money here, a null result from it means nothing.
def ou_pair(seed, kappa=0.08, shock=0.30):
    r = np.random.default_rng(seed)
    n = 252 + 126
    common = np.cumsum(r.normal(0.0002, 0.012, n))
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = s[i - 1] * (1 - kappa) + r.normal(0, 0.02)
    s[252] += shock                       # a dislocation right at entry
    la = 4.0 + common + s / 2
    lb = 4.0 + common - s / 2
    return la, lb


wins = 0
rets = []
for seed in range(60):
    la, lb = ou_pair(seed)
    b, alpha, m, sg = ps.formation_stats(la[:252], lb[:252])
    tr = ps.run_trade(la[252:], lb[252:], b, m, sg, alpha)
    if tr:
        rets.append(tr["ret"])
        wins += 1 if tr["ret"] > 0 else 0

check("trades are opened on the planted dislocations", len(rets) >= 40, True)
check("and most of them make money after costs", wins > len(rets) * 0.6, True)
mean_ret = sum(rets) / len(rets) if rets else 0
print(f"       ({len(rets)} trades, {wins} winners, mean {100*mean_ret:+.2f}% after costs)")
check("the mean is positive when the signal is real", mean_ret > 0, True)

# --------------------------------------------------------------------------
print("\nno signal means no profit")
# The same machinery on two independent random walks should not produce a
# convincing edge. This is the control for the test above.
def rw_pair(seed):
    r = np.random.default_rng(1000 + seed)
    return (4.0 + np.cumsum(r.normal(0, 0.015, 378)),
            4.0 + np.cumsum(r.normal(0, 0.015, 378)))


rrets = []
for seed in range(120):
    la, lb = rw_pair(seed)
    b, alpha, m, sg = ps.formation_stats(la[:252], lb[:252])
    tr = ps.run_trade(la[252:], lb[252:], b, m, sg, alpha)
    if tr:
        rrets.append(tr["ret"])
noise_mean = sum(rrets) / len(rrets) if rrets else 0
print(f"       ({len(rrets)} trades on pure random walks, mean {100*noise_mean:+.2f}%)")
check("random walks do not beat the planted signal", noise_mean < mean_ret, True)

# --------------------------------------------------------------------------
print("\nbootstrap and reporting")
_rb = random.Random(7)
# windows must actually differ, or every resample returns the same mean
by_w = {f"w{i}": [_rb.gauss(0.004, 0.03) for _ in range(12)] for i in range(10)}
ci = ps.block_bootstrap_ci(by_w, draws=400)
check("a confidence interval comes back", ci is not None and ci[0] < ci[1], True)
check("too few windows returns nothing", ps.block_bootstrap_ci({"a": [0.1]}), None)

d = ps.describe([0.01, -0.02, 0.03, 0.0])
check("describe counts", d["n"], 4)
check("describe hit rate ignores zero", d["hit_rate_pct"], 50.0)
check("describe is empty-safe", ps.describe([]), None)

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("pairs study tests pass")
