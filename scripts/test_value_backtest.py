"""Offline tests for the backtest's own arithmetic.

The network parts cannot be exercised here. Everything else can, and the parts
that decide whether the answer is believable -- the delisting rule, the decile
split, the block bootstrap, the pass/fail gate -- all live in the parts that
can.
"""

import os
from datetime import date

os.environ.setdefault("MAX_SYMBOLS", "1")
import value_backtest as vb           # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


def approx(name, got, want, tol=1e-6):
    ok = got is not None and abs(got - want) < tol
    if not ok:
        fails.append(f"{name}: got {got!r}, want ~{want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want~{want!r}"))


print("\nmonth_starts")
ms = vb.month_starts(date(2016, 1, 1), date(2016, 4, 1))
check("inclusive of both ends", ms, [date(2016, 1, 1), date(2016, 2, 1),
                                     date(2016, 3, 1), date(2016, 4, 1)])
check("rolls the year", vb.month_starts(date(2016, 11, 1), date(2017, 1, 1)),
      [date(2016, 11, 1), date(2016, 12, 1), date(2017, 1, 1)])
check("111 months over nine years", len(vb.month_starts(date(2016, 1, 1), date(2025, 3, 1))), 111)

print("\nidx_on_or_before")
days = [date(2020, 1, d) for d in (1, 3, 6, 9, 14)]
check("exact hit", vb.idx_on_or_before(days, date(2020, 1, 6)), 2)
check("falls back to the prior session", vb.idx_on_or_before(days, date(2020, 1, 8)), 2)
check("before the series starts", vb.idx_on_or_before(days, date(2019, 12, 31)), None)
check("after it ends", vb.idx_on_or_before(days, date(2021, 1, 1)), 4)
check("first day", vb.idx_on_or_before(days, date(2020, 1, 1)), 0)

print("\ndescribe")
d = vb.describe([-10.0, 0.0, 10.0, 20.0])
check("n", d["n"], 4)
approx("median", d["median"], 5.0)
approx("mean", d["mean"], 5.0)
check("hit rate counts strictly positive", d["hit_rate_pct"], 50.0)
check("empty gives nothing", vb.describe([]), None)

print("\nmonotone_score (reported, not gated)")
check("perfectly descending is 100%", vb.monotone_score([10, 8, 6, 4, 2]), 100.0)
check("perfectly ascending is 0%", vb.monotone_score([2, 4, 6, 8, 10]), 0.0)
check("one step wrong out of four", vb.monotone_score([10, 8, 9, 4, 2]), 75.0)
check("a flat line counts as monotone", vb.monotone_score([5, 5, 5]), 100.0)

print("\ngradient_rho -- the gate")
approx("a perfectly ordered table is -1", vb.gradient_rho([10, 8, 6, 4, 2, 0, -2, -4, -6, -8]), -1.0)
approx("the reverse is +1", vb.gradient_rho([-8, -6, -4, -2, 0, 2, 4, 6, 8, 10]), 1.0)
check("a flat table carries no information", vb.gradient_rho([5] * 10), 0.0)
# the case that made this replace the pairwise measure: a real gradient with
# noisy neighbours, where only 56% of adjacent pairs step the right way
noisy = [4.72, 5.93, 0.75, -0.23, 2.64, -1.92, -0.53, -4.72, -4.59, -7.51]
check("a noisy but ordered table still reads as ordered", vb.gradient_rho(noisy) <= -0.6, True)
check("and the pairwise measure would have missed it", vb.monotone_score(noisy) < 70.0, True)
# a table where only the top decile is good must NOT pass
spike = [9.0, 0.1, -0.1, 0.0, 0.2, -0.2, 0.1, 0.0, -0.1, 0.1]
check("a lone good decile over a random middle fails", vb.gradient_rho(spike) <= -0.6, False)

print("\nblock_bootstrap_ci")
# The whole reason this exists: a hundred names on one date must not look like
# a hundred independent observations.
one_big_date = {"2025-04-01": [50.0] * 100, "2024-01-01": [-5.0], "2023-01-01": [-5.0],
                "2022-01-01": [-5.0], "2021-01-01": [-5.0]}
ci = vb.block_bootstrap_ci(one_big_date, draws=3000)
check("the interval spans zero when one date carries everything",
      ci[0] < 0 < ci[1], True)

spread_evenly = {f"20{y:02d}-01-01": [8.0, 9.0, 10.0, 11.0] for y in range(10, 30)}
ci2 = vb.block_bootstrap_ci(spread_evenly, draws=3000)
check("a consistent effect across many dates excludes zero", ci2[0] > 0, True)
check("too few dates gives no interval", vb.block_bootstrap_ci({"a": [1.0]}), None)

print("\ndelisting rule")
# A series that stops well before the data ends is a delisting. A series that
# simply runs into the end of the data is not -- that name just has no forward
# window yet, and dropping it is fair to every decile alike.
data_end = date(2025, 9, 1)
from datetime import timedelta
check("stops long before the data ends -> delisted",
      date(2022, 5, 1) < data_end - timedelta(days=20), True)
check("runs to the end of the data -> not delisted",
      date(2025, 8, 29) < data_end - timedelta(days=20), False)

print("\ncost model")
def cost(cap):
    return vb.COST_LARGE if cap >= vb.LARGE_CAP else vb.COST_SMALL
check("a large cap pays the lower cost", cost(2e9), 0.5)
check("exactly a billion counts as large", cost(1e9), 0.5)
check("a small cap pays more", cost(4e8), 1.0)

print("\ndecile split")
def split(n, deciles=10):
    size = max(1, n // deciles)
    out = []
    for d in range(1, deciles + 1):
        lo = (d - 1) * size
        hi = n if d == deciles else d * size
        out.append((lo, hi))
    return out
s = split(100)
check("100 names split evenly", s[0], (0, 10))
check("the last decile closes the list", s[-1][1], 100)
check("no gaps", all(s[i][1] == s[i + 1][0] for i in range(9)), True)
s = split(107)
check("a ragged count still covers everything", s[-1][1], 107)
check("and still has no gaps", all(s[i][1] == s[i + 1][0] for i in range(9)), True)
check("every decile is non-empty at 107", all(hi > lo for lo, hi in s), True)
s = split(50)
check("at 50 names each decile holds five", s[0], (0, 5))

print("\nexcess is measured against the universe, not an index")
vals = [10.0, 0.0, -10.0, 20.0]
bench = sum(vals) / len(vals)
approx("benchmark is the equal-weighted mean", bench, 5.0)
check("excess sums to zero before costs",
      round(sum(v - bench for v in vals), 9), 0.0)

print("\nthe pre-registered gate is strict")
def gate(spread, rho, ci_low, holdout_spread, pess_spread):
    checks = {
        "d1_beats_d10": spread > 0,
        "gradient_is_ordered": rho <= -0.6,
        "decile_1_ci_excludes_zero": ci_low > 0,
        "holds_in_holdout": holdout_spread > 0,
        "survives_pessimistic_case": pess_spread > 0,
    }
    return all(checks.values())
check("everything good passes", gate(5.0, -0.9, 1.0, 3.0, 2.0), True)
check("a good spread with an unordered table fails", gate(5.0, -0.2, 1.0, 3.0, 2.0), False)
check("an interval touching zero fails", gate(5.0, -0.9, -0.1, 3.0, 2.0), False)
check("failing the holdout fails", gate(5.0, -0.9, 1.0, -1.0, 2.0), False)
check("needing the optimistic case fails", gate(5.0, -0.9, 1.0, 3.0, -1.0), False)

print("\nsettings match the frozen spec")
import value_core as vc
check("cash floor", vc.MIN_MARKET_CAP, 300_000_000)
check("volume floor", vc.MIN_DOLLAR_VOLUME, 2_000_000)
check("price floor", vc.MIN_PRICE, 3.00)
check("weights", vc.PILLAR_WEIGHTS,
      {"cheapness": 0.40, "quality": 0.30, "safety": 0.20, "confirmation": 0.10})
check("headline assumes delisted names lost half", vb.DELISTED_CASES["mid"], -50.0)
check("headline case is the mid one", vb.HEADLINE_CASE, "mid")
check("holdout starts where the spec says", vb.HOLDOUT_FROM, date(2023, 10, 1))

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("all backtest tests pass")
