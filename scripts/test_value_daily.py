"""Offline tests for the daily run.

The network parts cannot run here. The quarter arithmetic, the payload shape
and the decile assignment can, and those are the parts that would quietly put
a wrong file on the site.
"""

import json
import os
import random
from datetime import date, timedelta

os.environ.update(OUT_FILE="/tmp/value_screen_test.json", AS_OF="2026-09-01")

import value_daily as vdaily        # noqa: E402
import value_data as vd             # noqa: E402
import value_backtest as vb         # noqa: E402
import value_prices as vp           # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


print("\nrecent_quarters")
# The SEC publishes a quarter weeks after it closes, so the newest file asked
# for must never be the current quarter.
q = vdaily.recent_quarters(date(2026, 9, 19), 7)
check("seven quarters", len(q), 7)
check("newest is two quarters back", q[-1], "2026q1")
check("oldest follows from that", q[0], "2024q3")
check("ascending order", q, sorted(q))
check("start of a year steps back cleanly",
      vdaily.recent_quarters(date(2026, 1, 5), 3), ["2025q1", "2025q2", "2025q3"])
check("no quarter zero or five",
      all(1 <= int(x[-1]) <= 4 for x in vdaily.recent_quarters(date(2026, 4, 1), 12)), True)

print("\nrounded")
check("rounds a float", vdaily.rounded(1.23456, 2), 1.23)
check("leaves None alone", vdaily.rounded(None), None)
check("an INVALID sentinel is not a number", vdaily.rounded(vc_invalid := __import__("value_core").INVALID), None)
check("an int is not rounded into existence", vdaily.rounded(5), None)

print("\na whole run on invented data")
SESSIONS = []
d = date(2023, 1, 1)
while d <= date(2026, 9, 1):
    if d.weekday() < 5:
        SESSIONS.append(d)
    d += timedelta(days=1)

rng = random.Random(9)
COMPANIES = [{"cik": 2000 + k, "sym": f"Y{k:03d}", "good": rng.random(),
              "sic": rng.choice([2836, 7372, 3674, 5651, 2810])} for k in range(300)]


def fake_prices(symbol, start, end, order=None):
    c = next((x for x in COMPANIES if x["sym"] == symbol.upper()), None)
    if c is None:
        return None
    px, closes, vols = 15.0 + c["good"] * 50.0, [], []
    for _ in SESSIONS:
        px *= (1.0 + rng.gauss(0, 0.015))
        closes.append(max(px, 1.0))
        vols.append(rng.uniform(400_000, 900_000))
    return list(SESSIONS), closes, vols


def fake_load_quarter(q, facts, subs):
    year, qn = int(q[:4]), int(q[-1])
    period_end = [date(year, 3, 31), date(year, 6, 30),
                  date(year, 9, 30), date(year, 12, 31)][qn - 1]
    filed = period_end + timedelta(days=40)
    for c in COMPANIES:
        cik, good = c["cik"], c["good"]
        subs.setdefault(cik, {"name": "Company " + c["sym"], "sic": c["sic"],
                              "last_filed": filed, "filings": 0})
        subs[cik]["filings"] += 1
        subs[cik]["last_filed"] = max(subs[cik]["last_filed"], filed)
        f = facts.setdefault(cik, {})

        def put(field, qtrs, value):
            f.setdefault(field, []).append((filed, period_end, qtrs, value))

        scale = 2_000_000_000 * (0.5 + good)
        put("assets", 0, scale)
        put("current_assets", 0, scale * 0.4)
        put("current_liabilities", 0, scale * 0.2)
        put("cash", 0, scale * 0.1)
        put("equity", 0, scale * 0.5)
        put("retained_earnings", 0, scale * good * 0.3)
        put("lt_debt", 0, scale * 0.2)
        put("shares", 0, 60_000_000.0)
        put("revenue", 1, scale * 0.2)
        put("gross_profit", 1, scale * 0.05 * (0.5 + good))
        put("ebit", 1, scale * 0.02 * (0.3 + good))
        put("net_income", 1, scale * 0.015)
        put("cfo", 1, scale * 0.025)
        put("capex", 1, scale * 0.004)
        put("dda", 1, scale * 0.006)
        put("buybacks", 1, scale * 0.002 * good)
        put("dividends", 1, 0.0)
        put("interest_expense", 1, scale * 0.002)


vp.fetch_prices = fake_prices
vd.load_quarter = fake_load_quarter
vb.ticker_map = lambda: {c["cik"]: c["sym"] for c in COMPANIES}
vdaily.vd = vd
vdaily.vp = vp

rc = vdaily.main()
check("the run completed", rc, 0)

payload = json.load(open("/tmp/value_screen_test.json"))
meta, ranked = payload["_meta"], payload["ranked"]

check("metadata is present", sorted(meta) == sorted([
    "generated_at", "as_of", "universe", "unranked", "spec", "weights",
    "floors", "pillars", "how_to_read", "excluded"]), True)
check("as_of is the date asked for", meta["as_of"], "2026-09-01")
check("the universe count matches the list", meta["universe"], len(ranked))
check("the weights are the frozen ones", meta["weights"], vc_weights := __import__("value_core").PILLAR_WEIGHTS)
check("the file says a rank is not a recommendation",
      "not a recommendation" in meta["how_to_read"], True)
check("and says why banks are missing", "REIT" in meta["excluded"], True)

check("something got ranked", len(ranked) > 200, True)
check("ranks start at one", ranked[0]["rank"], 1)
check("ranks are contiguous", [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1)), True)
check("sorted by score, best first",
      all(ranked[i]["score"] >= ranked[i + 1]["score"] for i in range(len(ranked) - 1)), True)

first, last = ranked[0], ranked[-1]
check("the best name is in decile 1", first["decile"], 1)
check("the worst name is in decile 10", last["decile"], 10)
check("deciles never stray outside 1..10",
      all(1 <= r["decile"] <= 10 for r in ranked), True)
check("every name carries four pillars",
      all(sorted(r["pillars"]) == ["cheapness", "confirmation", "quality", "safety"]
          for r in ranked), True)
check("every name carries the figures behind the score",
      all("ev_ebit" in r["metrics"] and "net_issuance_pct" in r["metrics"] for r in ranked), True)
check("prices are real", all(isinstance(r["price"], float) and r["price"] > 0 for r in ranked), True)
check("market caps clear the floor",
      all(r["market_cap"] >= __import__("value_core").MIN_MARKET_CAP for r in ranked), True)
check("names are carried through", bool(first["name"]), True)
check("sectors are carried through", bool(first["sector"]), True)

# The site reads this file; it must be small enough to serve and parse.
size = os.path.getsize("/tmp/value_screen_test.json")
per = size / max(len(ranked), 1)
print(f"\n  {len(ranked)} names, {size/1024:.0f} KB, {per:.0f} bytes each")
check("each row stays compact", per < 700, True)

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("daily run tests pass")
