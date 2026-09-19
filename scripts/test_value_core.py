"""Offline tests for the scoring core.

There is no network in the build container, so everything that can be checked
without one is checked here before an Actions run gets to burn twenty minutes
finding out that a percentile was upside down.
"""

import value_core as vc
from value_core import INVALID, MISSING

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + (f"   got={got!r} want={want!r}" if not ok else ""))


def approx(name, got, want, tol=1e-6):
    ok = got is not None and abs(got - want) < tol
    if not ok:
        fails.append(f"{name}: got {got!r}, want ~{want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + (f"   got={got!r} want~{want!r}" if not ok else ""))


print("\npercentile_ranks")
# higher_better: biggest value must score 100, smallest 0
r = vc.percentile_ranks([1, 2, 3, 4, 5], higher_better=True)
check("higher_better puts the max at 100", r[4], 100.0)
check("higher_better puts the min at 0", r[0], 0.0)
check("higher_better is monotone", r == sorted(r), True)

# lower_better must be the exact mirror
r = vc.percentile_ranks([1, 2, 3, 4, 5], higher_better=False)
check("lower_better puts the min at 100", r[0], 100.0)
check("lower_better puts the max at 0", r[4], 0.0)

# ties share a percentile -- otherwise input order silently decides rank
r = vc.percentile_ranks([5, 5, 5, 1], higher_better=True)
check("ties share one percentile", r[0] == r[1] == r[2], True)
check("the tied block beats the loner", r[0] > r[3], True)

# MISSING is not ranked at all
r = vc.percentile_ranks([1, MISSING, 3], higher_better=True)
check("MISSING comes back None", r[1], None)
check("MISSING does not shift the others", (r[0], r[2]), (0.0, 100.0))

# INVALID ties at the bottom whichever way the metric points. Several of them
# share the positions they occupy, so the block sits above a flat zero -- what
# has to hold is that no real value ever lands at or below them.
r = vc.percentile_ranks([10, INVALID, 5, INVALID], higher_better=True)
check("INVALIDs tie with each other", r[1] == r[3], True)
check("real values outrank INVALID", r[0] > r[1] and r[2] > r[1], True)
r1 = vc.percentile_ranks([10, INVALID, 5], higher_better=True)
check("a lone INVALID scores a flat zero", r1[1], 0.0)
r = vc.percentile_ranks([10, INVALID, 5], higher_better=False)
check("INVALID is worst (lower_better)", r[1], 0.0)
check("lower_better still prefers 5 to 10", r[2] > r[0], True)

check("all MISSING gives all None", vc.percentile_ranks([MISSING, MISSING], True), [None, None])
check("single value sits mid-table", vc.percentile_ranks([7], True), [50.0])

print("\ncompute_metrics")
base = dict(market_cap=1000.0, total_debt=200.0, cash=100.0, ebit=100.0, fcf=50.0,
            revenue=2000.0, buybacks=10.0, dividends=5.0, gross_profit=400.0,
            assets=1600.0, net_income=80.0, cfo=120.0, equity=900.0, ebitda=150.0,
            interest_expense=20.0, shares=100.0, shares_prior=98.0,
            current_assets=600.0, current_liabilities=300.0, retained_earnings=250.0,
            gross_profit_prior=350.0, assets_prior=1500.0,
            momentum_12_1=0.12, price=25.0, ma_200=22.0)

m = vc.compute_metrics(base)
approx("EV is cap + debt - cash", vc.enterprise_value(base), 1100.0)
approx("EV/EBIT", m["ev_ebit"], 11.0)
approx("EV/FCF", m["ev_fcf"], 22.0)
approx("shareholder yield", m["shareholder_yield"], 0.015)
approx("gross profitability", m["gross_profitability"], 0.25)
approx("accruals are net income minus cash flow", m["accruals"], (80.0 - 120.0) / 1600.0)
approx("ROIC", m["roic"], 100.0 * 0.79 / (900.0 + 200.0 - 100.0))
approx("gp change", m["gp_change"], 0.25 - (350.0 / 1500.0))
approx("net debt / ebitda", m["net_debt_ebitda"], 100.0 / 150.0)
approx("interest cover", m["interest_cover"], 5.0)
approx("net issuance", m["net_issuance"], 2.0 / 98.0)
approx("working capital / assets", m["working_capital"], 300.0 / 1600.0)
approx("price vs 200d", m["price_vs_200d"], 25.0 / 22.0)

# the rule that stops loss-makers walking through the cheapness test
loss = dict(base, ebit=-50.0, fcf=-10.0, revenue=0.0)
m = vc.compute_metrics(loss)
check("negative EBIT is INVALID, not missing", m["ev_ebit"], INVALID)
check("negative FCF is INVALID", m["ev_fcf"], INVALID)
check("zero revenue is INVALID", m["ev_sales"], INVALID)

# a company below its own net cash should read as the cheapest thing there is
netcash = dict(base, market_cap=100.0, cash=500.0, total_debt=0.0)
m = vc.compute_metrics(netcash)
check("negative EV is kept, not discarded", isinstance(m["ev_ebit"], float), True)
check("negative EV scores below any positive one", m["ev_ebit"] < 0, True)
check("net cash takes the best net-debt reading", m["net_debt_ebitda"], -1.0)

# net cash with no EBITDA at all must still not be punished
m = vc.compute_metrics(dict(base, cash=500.0, total_debt=0.0, ebitda=None))
check("net cash beats a missing EBITDA", m["net_debt_ebitda"], -1.0)
# but debt with no earnings to service it is a real red flag
m = vc.compute_metrics(dict(base, ebitda=-5.0))
check("debt with negative EBITDA is INVALID", m["net_debt_ebitda"], INVALID)

# missing inputs must not be read as zeros
m = vc.compute_metrics(dict(base, gross_profit=None))
check("missing gross profit is MISSING", m["gross_profitability"], MISSING)
m = vc.compute_metrics(dict(base, shares_prior=None))
check("missing prior shares is MISSING", m["net_issuance"], MISSING)
m = vc.compute_metrics(dict(base, assets=0.0))
check("zero assets is INVALID, not a divide by zero", m["gross_profitability"], INVALID)

# no interest expense is not the same as no earnings
m = vc.compute_metrics(dict(base, interest_expense=0.0))
check("profitable with no interest cost scores top", m["interest_cover"], 1000.0)
m = vc.compute_metrics(dict(base, interest_expense=0.0, ebit=-1.0))
check("loss-making with no interest cost is INVALID", m["interest_cover"], INVALID)

print("\nsector buckets")
check("biotech by SIC", vc.sector_of(2836), "Biotech & Pharma")
check("research services land with biotech", vc.sector_of(8731), "Biotech & Pharma")
check("software is technology", vc.sector_of(7372), "Technology")
check("semiconductors are technology", vc.sector_of(3674), "Technology")
check("retail", vc.sector_of(5651), "Retail")
check("unknown SIC gets its own bucket", vc.sector_of(None), "Unclassified")
check("banks are excluded", vc.is_excluded_sector(6021), True)
check("REITs are excluded", vc.is_excluded_sector(6798), True)
check("a chemical company is not", vc.is_excluded_sector(2810), False)

print("\nuniverse gate")
ok_row = dict(sic=2836, market_cap=500e6, dollar_volume=5e6, price=12.0,
              price_sessions=300, quarters_filed=8)
check("a clean row passes", vc.passes_universe(ok_row)[0], True)
check("a bank fails", vc.passes_universe(dict(ok_row, sic=6021))[0], False)
check("under the cap floor fails", vc.passes_universe(dict(ok_row, market_cap=299e6))[0], False)
check("exactly at the cap floor passes", vc.passes_universe(dict(ok_row, market_cap=300e6))[0], True)
check("illiquid fails", vc.passes_universe(dict(ok_row, dollar_volume=1e6))[0], False)
check("penny price fails", vc.passes_universe(dict(ok_row, price=2.99))[0], False)
check("short history fails", vc.passes_universe(dict(ok_row, price_sessions=251))[0], False)
check("too few filings fails", vc.passes_universe(dict(ok_row, quarters_filed=3))[0], False)
check("a stale filing fails", vc.passes_universe(ok_row, filing_age_days=201)[0], False)
check("a fresh filing passes", vc.passes_universe(ok_row, filing_age_days=200)[0], True)

print("\nscore_rows")
# 30 names in one sector so the sector path (not the thin-sector fallback) runs.
rows = []
for i in range(30):
    rows.append(dict(
        symbol=f"S{i:02d}", sic=2836, market_cap=1000.0 + i, dollar_volume=5e6,
        total_debt=100.0, cash=50.0,
        ebit=10.0 + i * 5, fcf=5.0 + i * 3, revenue=500.0, buybacks=i, dividends=0.0,
        gross_profit=100.0 + i * 4, assets=1000.0, net_income=50.0, cfo=60.0 + i,
        equity=800.0, ebitda=20.0 + i * 5, interest_expense=10.0,
        shares=100.0, shares_prior=100.0 + i * 0.1,
        current_assets=400.0, current_liabilities=200.0, retained_earnings=100.0 + i,
        gross_profit_prior=100.0, assets_prior=1000.0,
        momentum_12_1=i / 100.0, price=20.0, ma_200=18.0,
    ))
scored, unranked = vc.score_rows(rows)
check("everything got scored", (len(scored), len(unranked)), (30, 0))
check("ranks run 1..n", [r["rank"] for r in scored][:3], [1, 2, 3])
check("sorted best first", all(scored[i]["score"] >= scored[i + 1]["score"] for i in range(len(scored) - 1)), True)
check("the strongest fundamentals win", scored[0]["symbol"], "S29")
check("pillars all present", sorted(scored[0]["pillars"]), ["cheapness", "confirmation", "quality", "safety"])
check("score is inside 0-100", 0 <= scored[0]["score"] <= 100, True)

# weights must actually be the frozen ones
p = scored[0]["pillars"]
want = sum(p[k] * w for k, w in vc.PILLAR_WEIGHTS.items())
approx("composite uses the spec weights", scored[0]["score"], round(want, 2), 0.011)
check("weights sum to one", round(sum(vc.PILLAR_WEIGHTS.values()), 6), 1.0)

# a name missing most of a pillar must be reported, not quietly dropped
gappy = dict(rows[0], symbol="GAPPY", ebit=None, fcf=None, revenue=None,
             market_cap=None, buybacks=None, dividends=None)
scored2, unranked2 = vc.score_rows([dict(r) for r in rows] + [gappy])
check("the gappy name is unranked", [u["symbol"] for u in unranked2], ["GAPPY"])
check("it is reported with a reason", bool(unranked2[0]["unranked_reason"]), True)
check("the rest still rank", len(scored2), 30)

# thin sectors must fall back rather than rank 3 names against each other
few = [dict(rows[i], symbol=f"T{i}", sic=1311) for i in range(3)]
scored3, _ = vc.score_rows([dict(r) for r in rows] + few)
check("thin-sector names are still scored", len(scored3), 33)
check("thin-sector names keep their own sector label",
      {r["sector"] for r in scored3 if r["symbol"].startswith("T")}, {"Energy"})

print("\nrescoring is not contaminated by the previous pass")
# The backtest scores a universe once a month for fifteen years. If a row
# carries last month's percentiles, or two rows share one dict, the scores
# quietly overwrite each other -- which is exactly what happened the first
# time these tests ran.
fresh = [dict(r) for r in rows]
a, _ = vc.score_rows(fresh)
b, _ = vc.score_rows(fresh)              # same objects, scored twice
check("scoring the same rows twice is stable",
      [r["symbol"] for r in a] == [r["symbol"] for r in b], True)
shared = dict(rows[0]); shared["symbol"] = "SHARED"
c, unranked_c = vc.score_rows([dict(r) for r in rows] + [shared])
check("a row copied from a scored one does not poison its source",
      len(c) + len(unranked_c), 31)
check("and nothing got dropped", len(c), 31)

print("\nlook-ahead discipline (the caller's job, asserted here)")
# compute_metrics must never reach for a figure the caller did not hand it.
sentinel = dict(base)
sentinel["ebit_future"] = 99999.0
m = vc.compute_metrics(sentinel)
check("unknown keys are ignored", m["ev_ebit"], vc.compute_metrics(base)["ev_ebit"])

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("all core tests pass")
