"""Offline tests for the point-in-time reads.

The only thing that makes a backtest worth reading is that it could not see
the future. These tests are that claim, checked against data built to break it:
restatements filed years later, annual figures that land after the quarter,
a filing that arrives the day after the observation date.
"""

from datetime import date

import value_data as vd

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


D = date

print("\nquarters_between")
check("one year", vd.quarters_between((2016, 1), (2016, 4)),
      ["2016q1", "2016q2", "2016q3", "2016q4"])
check("crosses a year", vd.quarters_between((2016, 3), (2017, 2)),
      ["2016q3", "2016q4", "2017q1", "2017q2"])
check("single quarter", vd.quarters_between((2020, 2), (2020, 2)), ["2020q2"])

print("\nlatest_instant -- the look-ahead guard")
series = [
    (D(2020, 2, 20), D(2019, 12, 31), 0, 1000.0),   # FY2019, filed Feb 2020
    (D(2020, 5, 10), D(2020, 3, 31), 0, 1100.0),    # Q1 2020, filed May 2020
    (D(2020, 8, 10), D(2020, 6, 30), 0, 1200.0),    # Q2 2020, filed Aug 2020
]
check("before anything was filed", vd.latest_instant(series, D(2020, 1, 1)), None)
check("sees only the FY2019 filing", vd.latest_instant(series, D(2020, 3, 1)), 1000.0)
check("the day before Q1 is public", vd.latest_instant(series, D(2020, 5, 9)), 1000.0)
check("the day Q1 becomes public", vd.latest_instant(series, D(2020, 5, 10)), 1100.0)
check("later still", vd.latest_instant(series, D(2021, 1, 1)), 1200.0)

# a restatement filed in 2022 must not change what 2020 could see
restated = series + [(D(2022, 3, 1), D(2019, 12, 31), 0, 7777.0)]
check("a 2022 restatement does not leak into 2020",
      vd.latest_instant(restated, D(2020, 3, 1)), 1000.0)

# and after tidy(), the earlier-filed row is the one that survives
facts = {1: {"assets": list(restated)}}
vd.tidy(facts)
check("tidy keeps the originally filed figure",
      vd.latest_instant(facts[1]["assets"], D(2023, 1, 1)), 1200.0)
check("tidy dropped the restatement row",
      any(v == 7777.0 for *_r, v in facts[1]["assets"]), False)

# out-of-order input must not confuse the 'most recent period' choice
jumbled = [series[2], series[0], series[1]]
check("input order does not matter", vd.latest_instant(jumbled, D(2021, 1, 1)), 1200.0)

print("\nttm")
# four clean quarters
q = [
    (D(2023, 5, 1), D(2023, 3, 31), 1, 10.0),
    (D(2023, 8, 1), D(2023, 6, 30), 1, 11.0),
    (D(2023, 11, 1), D(2023, 9, 30), 1, 12.0),
    (D(2024, 2, 1), D(2023, 12, 31), 1, 13.0),
]
check("sums four quarters", vd.ttm(q, D(2024, 3, 1)), 46.0)
check("will not sum quarters that are not public yet", vd.ttm(q, D(2023, 12, 1)), None)
check("nothing filed yet", vd.ttm(q, D(2023, 1, 1)), None)

# duplicate period reported twice must not be double counted
dupe = q + [(D(2024, 2, 15), D(2023, 12, 31), 1, 13.0)]
check("a repeated period is counted once", vd.ttm(dupe, D(2024, 3, 1)), 46.0)

# annual fallback
annual = [(D(2024, 2, 20), D(2023, 12, 31), 4, 50.0)]
check("falls back to the annual figure", vd.ttm(annual, D(2024, 3, 1)), 50.0)
check("annual fallback respects the filing date", vd.ttm(annual, D(2024, 2, 19)), None)

# stale data must be refused rather than passed off as current
old = [(D(2019, 2, 1), D(2018, 12, 31), 4, 99.0)]
check("a five-year-old annual is refused", vd.ttm(old, D(2024, 1, 1)), None)
check("but is fine shortly after it was filed", vd.ttm(old, D(2019, 6, 1)), 99.0)

# four quarters that do not actually span a year must not be called a year
bunched = [
    (D(2023, 5, 1), D(2023, 3, 31), 1, 10.0),
    (D(2023, 5, 2), D(2023, 2, 28), 1, 10.0),
    (D(2023, 5, 3), D(2023, 1, 31), 1, 10.0),
    (D(2023, 5, 4), D(2022, 12, 31), 1, 10.0),
]
check("four bunched periods are not a year", vd.ttm(bunched, D(2023, 6, 1)), None)

print("\nfigures_for")
facts = {
    42: {
        "assets":  [(D(2024, 2, 1), D(2023, 12, 31), 0, 1600.0),
                    (D(2023, 2, 1), D(2022, 12, 31), 0, 1500.0)],
        "cash":    [(D(2024, 2, 1), D(2023, 12, 31), 0, 100.0)],
        "equity":  [(D(2024, 2, 1), D(2023, 12, 31), 0, 900.0)],
        "lt_debt": [(D(2024, 2, 1), D(2023, 12, 31), 0, 200.0)],
        "shares":  [(D(2024, 2, 1), D(2023, 12, 31), 0, 100.0),
                    (D(2023, 2, 1), D(2022, 12, 31), 0, 98.0)],
        "ebit":    [(D(2024, 2, 1), D(2023, 12, 31), 4, 100.0)],
        "cfo":     [(D(2024, 2, 1), D(2023, 12, 31), 4, 120.0)],
        "capex":   [(D(2024, 2, 1), D(2023, 12, 31), 4, 70.0)],
        "dda":     [(D(2024, 2, 1), D(2023, 12, 31), 4, 50.0)],
        "gross_profit": [(D(2024, 2, 1), D(2023, 12, 31), 4, 400.0),
                         (D(2023, 2, 1), D(2022, 12, 31), 4, 350.0)],
        "revenue": [(D(2024, 2, 1), D(2023, 12, 31), 4, 2000.0)],
    }
}
g = vd.figures_for(42, facts, D(2024, 6, 1))
check("assets", g["assets"], 1600.0)
check("debt is summed across the debt tags", g["total_debt"], 200.0)
check("fcf is operating cash flow less capex", g["fcf"], 50.0)
check("ebitda adds back D&A", g["ebitda"], 150.0)
check("revenue", g["revenue"], 2000.0)
check("prior-year assets come from the prior-year filing", g["assets_prior"], 1500.0)
check("prior-year shares likewise", g["shares_prior"], 98.0)
check("prior-year gross profit likewise", g["gross_profit_prior"], 350.0)
check("a tag with no rows reads as None", g["retained_earnings"], None)
check("an unknown cik gives nothing", vd.figures_for(999, facts, D(2024, 6, 1)), None)

# the whole point, restated as one assertion
g_early = vd.figures_for(42, facts, D(2024, 1, 31))
check("nothing from the Feb filing is visible in January",
      (g_early["assets"], g_early["ebit"]), (1500.0, None))

print("\nno figure can be read before it was filed")
bad = []
for on in (D(2023, 6, 1), D(2023, 12, 31), D(2024, 1, 31), D(2024, 2, 1), D(2024, 6, 1)):
    g = vd.figures_for(42, facts, on) or {}
    for field, value in g.items():
        if value is None:
            continue
        # find any series that could have produced it and check the date
        for series in facts[42].values():
            for filed, _dd, _q, v in series:
                if v == value and filed > on and field not in (
                    "fcf", "ebitda", "total_debt"):   # derived, checked via parts
                    bad.append((on, field, value, filed))
check("no value predates its filing", bad, [])

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("all point-in-time tests pass")
