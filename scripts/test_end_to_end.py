"""A whole run, on invented data, with the network stubbed out.

The unit tests cover the arithmetic. This covers the wiring: the rebalance
loop, the universe gate, the decile split, the delisting path and the verdict
gate, all driven end to end. It exists because the last study got every hard
part right and then fell over on plumbing after twenty-five minutes.

The fake world is built with a planted signal -- companies with better
fundamentals are given better forward returns -- so a run that reports no
signal here means the pipeline is broken, not that the idea is wrong.
"""

import json
import os
import random
from datetime import date, timedelta

os.environ.update(
    FIRST_DATE="2018-01-01", LAST_DATE="2022-06-01", HOLDOUT_FROM="2021-07-01",
    OUT_FILE="/tmp/e2e.json",
)

import value_backtest as vb           # noqa: E402
import value_data as vd               # noqa: E402

N = 240
rng = random.Random(4)

SESSIONS = []
d = date(2015, 1, 1)
while d <= date(2023, 6, 30):
    if d.weekday() < 5:
        SESSIONS.append(d)
    d += timedelta(days=1)

# Each company gets a hidden "goodness". Fundamentals reflect it, and so do
# forward returns -- that is the planted signal the pipeline must recover.
COMPANIES = []
for k in range(N):
    good = rng.random()
    COMPANIES.append({
        "cik": 1000 + k,
        "sym": f"Z{k:03d}",
        "good": good,
        "sic": rng.choice([2836, 7372, 3674, 5651, 2810, 3560]),
        "drift": (good - 0.5) * 0.0016,           # per session
    })

# One in twelve dies partway through, so the delisting path actually runs.
DOOMED = {c["cik"] for c in COMPANIES if rng.random() < 1 / 12}


def fake_prices(symbol):
    c = next((x for x in COMPANIES if x["sym"] == symbol.upper()), None)
    if c is None:
        return None
    days, closes, vols = [], [], []
    px = 20.0 + c["good"] * 40.0
    stop = len(SESSIONS)
    if c["cik"] in DOOMED:
        stop = int(len(SESSIONS) * rng.uniform(0.55, 0.85))
    for i in range(stop):
        px *= (1.0 + c["drift"] + rng.gauss(0, 0.018))
        px = max(px, 0.4)
        days.append(SESSIONS[i])
        closes.append(px)
        vols.append(rng.uniform(300_000, 900_000))
    return days, closes, vols


def fake_ticker_map():
    return {c["cik"]: c["sym"] for c in COMPANIES}


def fake_load_quarter(q, facts, subs):
    year, qn = int(q[:4]), int(q[-1])
    period_end = [date(year, 3, 31), date(year, 6, 30),
                  date(year, 9, 30), date(year, 12, 31)][qn - 1]
    filed = period_end + timedelta(days=40)
    for c in COMPANIES:
        cik, good = c["cik"], c["good"]
        subs.setdefault(cik, {"name": c["sym"], "sic": c["sic"],
                              "last_filed": filed, "filings": 0})
        subs[cik]["filings"] += 1
        subs[cik]["last_filed"] = max(subs[cik]["last_filed"], filed)
        f = facts.setdefault(cik, {})

        def put(field, qtrs, value):
            f.setdefault(field, []).append((filed, period_end, qtrs, value))

        scale = 1_000_000_000 * (0.6 + good)
        put("assets", 0, scale)
        put("current_assets", 0, scale * 0.45)
        put("current_liabilities", 0, scale * 0.2)
        put("cash", 0, scale * (0.05 + good * 0.15))
        put("equity", 0, scale * 0.5)
        put("retained_earnings", 0, scale * (good * 0.3))
        put("lt_debt", 0, scale * (0.3 - good * 0.2))
        put("shares", 0, 40_000_000.0 * (1.0 + (1 - good) * 0.02 * qn))
        put("revenue", 1, scale * 0.18)
        put("gross_profit", 1, scale * 0.05 * (0.5 + good))
        put("ebit", 1, scale * 0.02 * (0.2 + good))
        put("net_income", 1, scale * 0.015 * (0.2 + good))
        put("cfo", 1, scale * 0.025 * (0.2 + good))
        put("capex", 1, scale * 0.005)
        put("dda", 1, scale * 0.006)
        put("buybacks", 1, scale * 0.002 * good)
        put("dividends", 1, 0.0)
        put("interest_expense", 1, scale * 0.002)


vb.fetch_prices = fake_prices
vb.ticker_map = fake_ticker_map
vb._get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network allowed"))
vd.load_quarter = fake_load_quarter
vb.vd = vd

print("running a full backtest against invented data...\n")
rc = vb.main()

print("\n" + "=" * 60)
fails = []


def check(name, cond):
    if not cond:
        fails.append(name)
    print(("  ok   " if cond else "  FAIL ") + name)


check("the run completed", rc == 0)
res = json.load(open("/tmp/e2e.json"))
check("it produced rebalances", res["rebalances"] > 20)
check("the universe is a real size", res["median_universe"] >= 100)
check("a main-period result exists", bool(res["main"]))
check("a holdout result exists", bool(res["holdout"]))
check("all three horizons are reported", sorted(res["main"]) == ["12m", "3m", "6m"])
check("all three delisting cases are reported",
      sorted(res["main"]["6m"]) == ["mid", "optimistic", "pessimistic"])
check("the irreducible gap is stated", "note" in res["irreducible"])
check("the spec is named in the output", "VALUE_SPEC" in res["spec"])

m6 = res["main"]["6m"]["mid"]
print(f"\n  planted-signal 6m deciles: {m6['decile_means']}")
print(f"  spread d1-d10: {m6['spread_d1_minus_d10']:+.2f}pp, "
      f"rho {m6['gradient_rho']:+.3f} (pairwise {m6['monotone_pct']:.0f}%)")
check("the planted signal was recovered (d1 > d10)", m6["spread_d1_minus_d10"] > 0)
check("and the table is ordered top to bottom", m6["gradient_rho"] <= -0.6)
check("ten deciles were reported", len(m6["decile_means"]) == 10)
check("the verdict gate ran", bool(res["verdict"]))
check("the verdict has every check", set(res["verdict"]["6m"]["checks"]) == {
    "d1_beats_d10", "gradient_is_ordered", "decile_1_ci_excludes_zero",
    "holds_in_holdout", "survives_pessimistic_case"})

# The pessimistic case charges delisted names -100%, so it must never look
# better than the optimistic one.
opt = res["main"]["6m"]["optimistic"]["spread_d1_minus_d10"]
pess = res["main"]["6m"]["pessimistic"]["spread_d1_minus_d10"]
print(f"  optimistic spread {opt:+.2f}pp vs pessimistic {pess:+.2f}pp")
check("the delisting assumption actually changes the answer", opt != pess)

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("end-to-end pipeline works")
