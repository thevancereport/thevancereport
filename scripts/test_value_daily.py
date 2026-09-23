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
# One quarter back, not two. Two was the bug that emptied the first live
# run: the filing-age gate throws out anything older than 200 days, so a
# discarded quarter ages every company by three months.
check("newest is one quarter back", q[-1], "2026q2")
check("oldest follows from that", q[0], "2024q4")
check("ascending order", q, sorted(q))
check("start of a year steps back cleanly",
      vdaily.recent_quarters(date(2026, 1, 5), 3), ["2025q2", "2025q3", "2025q4"])
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

# 1,100 companies across thirteen sectors, because the production sanity
# checks refuse to publish a universe under 300 names or one covering fewer
# than eight sectors -- and a fixture that cannot satisfy the real thresholds
# is a fixture that stops testing them. The first version of this file had
# 300 companies in 5 sectors and failed both, which is the check working.
_SICS = [2836,   # Biotech & Pharma
         7372,   # Technology
         3674,   # Technology
         5651,   # Retail
         2810,   # Chemicals
         1311,   # Energy
         3312,   # Industrials
         2011,   # Food & Beverage
         4911,   # Utilities
         1531,   # Construction
         5122,   # Wholesale
         3841,   # Medical Devices
         8711,   # Business Services
         4213]   # Transport
COMPANIES = [{"cik": 2000 + k, "sym": f"Y{k:04d}", "good": rng.random(),
              "sic": _SICS[k % len(_SICS)]} for k in range(1100)]


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

# A file left by an earlier local run would make this one decline to publish
# (same prices, same session), which is the behaviour tested further down.
if os.path.exists("/tmp/value_screen_test.json"):
    os.remove("/tmp/value_screen_test.json")
rc = vdaily.main()
check("the run completed", rc, 0)

payload = json.load(open("/tmp/value_screen_test.json"))
meta, ranked = payload["_meta"], payload["ranked"]

check("metadata is present", sorted(meta) == sorted([
    "generated_at", "as_of", "universe", "unranked", "spec", "weights",
    "floors", "pillars", "how_to_read", "excluded", "prices_through"]), True)
check("as_of is the date asked for", meta["as_of"], "2026-09-01")
check("the universe count matches the list", meta["universe"], len(ranked))
check("the weights are the frozen ones", meta["weights"], vc_weights := __import__("value_core").PILLAR_WEIGHTS)
check("the file says a rank is not a recommendation",
      "not a recommendation" in meta["how_to_read"], True)
check("and says why banks are missing", "REIT" in meta["excluded"], True)

check("something got ranked", len(ranked) > 900, True)
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


print("\nthe sanity checks actually fire")
# A check that never fires is a check nobody has tested. Each case below
# breaks the payload in one specific way and asserts the right FAIL comes
# back -- including the two real bugs that reached the live site.
import copy                                              # noqa: E402

BASE = json.load(open("/tmp/value_screen_test.json"))
QS = vdaily.recent_quarters(date(2026, 9, 20), 8)


_UNSET = object()


def fires(payload, quarters=None, loaded=_UNSET, level="FAIL"):
    # `loaded or QS` would turn the empty list -- the case that matters --
    # back into a full one, so the no-data test could never fail. Sentinel,
    # not falsiness.
    out = vdaily.sanity_check(payload, quarters or QS,
                              QS if loaded is _UNSET else loaded)
    return [m for lvl, m in out if lvl == level]


check("a clean payload raises nothing", fires(BASE), [])

# The 20 September bug: a discarded quarter shrank the universe to 234.
small = copy.deepcopy(BASE)
small["ranked"] = small["ranked"][:234]
check("a 234-name universe fails", bool(fires(small)), True)

# The other 20 September bug: 87xx mapped wholesale to Biotech & Pharma.
lumped = copy.deepcopy(BASE)
for r in lumped["ranked"][: int(len(lumped["ranked"]) * 0.45)]:
    r["sector"] = "Biotech & Pharma"
check("45% in one sector fails", bool(fires(lumped)), True)

collapsed = copy.deepcopy(BASE)
for r in collapsed["ranked"]:
    r["sector"] = "Technology"
check("one sector for everything fails", bool(fires(collapsed)), True)

unsorted_p = copy.deepcopy(BASE)
unsorted_p["ranked"][5], unsorted_p["ranked"][500] = (
    unsorted_p["ranked"][500], unsorted_p["ranked"][5])
check("rows out of score order fail", bool(fires(unsorted_p)), True)

tiny_cap = copy.deepcopy(BASE)
tiny_cap["ranked"][3]["market_cap"] = 1_000_000
check("a name under the cap floor fails", bool(fires(tiny_cap)), True)

cheap = copy.deepcopy(BASE)
cheap["ranked"][7]["price"] = 0.40
check("a name under the price floor fails", bool(fires(cheap)), True)

nopillar = copy.deepcopy(BASE)
del nopillar["ranked"][2]["pillars"]["safety"]
check("a missing pillar fails", bool(fires(nopillar)), True)

dupe = copy.deepcopy(BASE)
dupe["ranked"][9]["symbol"] = dupe["ranked"][8]["symbol"]
check("a duplicate symbol fails", bool(fires(dupe)), True)

check("losing both newest quarters fails",
      bool(fires(BASE, quarters=QS, loaded=QS[:-2])), True)
check("losing only the newest quarter warns, not fails",
      fires(BASE, quarters=QS, loaded=QS[:-1]), [])
check("and it does warn",
      bool(fires(BASE, quarters=QS, loaded=QS[:-1], level="WARN")), True)
check("no data at all fails", bool(fires(BASE, quarters=QS, loaded=[])), True)

print("\ndating by the prices, not the clock")
# 21 Sep 2026: the evening run found no Monday closes, fell back to Friday's,
# and published them as Monday's. The file is now dated by its prices, and a
# run whose prices are no newer than the published file writes nothing.
check("the file records the session its prices describe", meta["prices_through"], "2026-09-01")
check("and as_of is that session", meta["as_of"], meta["prices_through"])
_gen = meta["generated_at"]
check("a rerun on prices no newer exits cleanly", vdaily.main(), 0)
check("and leaves the published file alone",
      json.load(open("/tmp/value_screen_test.json"))["_meta"]["generated_at"], _gen)

_WEEK = [date(2026, 9, 14) + timedelta(days=k) for k in range(5)]      # Mon-Fri


def _hist(n=10):
    return {f"T{k}": (list(_WEEK), [20.0] * 5, [500_000.0] * 5) for k in range(n)}


check("session_of reads the prices, not the calendar",
      vdaily.session_of(_hist(), date(2026, 9, 21)), date(2026, 9, 18))

print("\ntop_up")
_real_latest = vp.latest_close
vp.latest_close = lambda sym: (date(2026, 9, 21), 21.0, 600_000.0)
h = _hist()
added, asked, sess, jumped = vdaily.top_up(h, date(2026, 9, 22))
check("every name asked gets the close", (added, asked), (10, 10))
check("the session is the quote's date", sess, date(2026, 9, 21))
check("nothing looked like a split", jumped, [])
check("the close is appended", (h["T0"][0][-1], h["T0"][1][-1]), (date(2026, 9, 21), 21.0))
check("session_of then moves on", vdaily.session_of(h, date(2026, 9, 22)), date(2026, 9, 21))

vp.latest_close = lambda sym: (date(2026, 9, 21), 21.0, 600_000.0) if sym < "T5" else None
h = _hist()
added, asked, sess, jumped = vdaily.top_up(h, date(2026, 9, 22))
check("half a day is not used", (added, sess), (0, None))
check("and nothing is appended", h["T0"][0][-1], date(2026, 9, 18))

vp.latest_close = lambda sym: (date(2026, 9, 23), 21.0, 600_000.0)
h = _hist()
check("a close dated after as_of is ignored", vdaily.top_up(h, date(2026, 9, 22))[0], 0)

# A two-for-one halves the quote while the history behind it is already
# split-adjusted, so taking the quote would show the company down 50% on the
# day and poison its momentum and 200-day readings. The cache cannot repair
# that, so the name is left alone and refetched from source next time.
vp.latest_close = lambda sym: (date(2026, 9, 21), 10.0, 600_000.0)
h = _hist()
added, asked, sess, jumped = vdaily.top_up(h, date(2026, 9, 22))
check("a halved price is refused", (added, sess), (0, None))
check("and every name is reported", sorted(jumped), sorted(_hist()))
check("with the history untouched", h["T0"][0][-1], date(2026, 9, 18))

vp.latest_close = lambda sym: (date(2026, 9, 21), 24.0, 600_000.0)
h = _hist()
added, _asked, _sess, jumped = vdaily.top_up(h, date(2026, 9, 22))
check("a hard but believable day still goes in", (added, jumped), (10, []))
vp.latest_close = _real_latest

print("\nlatest_close reads Nasdaq's quote")
_real_fetch = vp._fetch
_Q = {}


def _fake_fetch(url, headers=None, timeout=45, attempts=2):
    return json.dumps({"data": {"marketStatus": _Q["status"], "primaryData": {
        "lastSalePrice": "$19.41", "lastTradeTimestamp": _Q["stamp"],
        "volume": "1,478,917"}}}), None


vp._fetch = _fake_fetch
_Q.update(status="Closed", stamp="Sep 21, 2026")
check("a closed market gives the close", vp.latest_close("YELP"),
      (date(2026, 9, 21), 19.41, 1_478_917.0))
_Q.update(stamp="DATA AS OF Sep 21, 2026 4:00 PM ET")
check("a longer stamp still reads", vp.latest_close("YELP")[0], date(2026, 9, 21))
_Q.update(status="After-Hours", stamp="Sep 21, 2026")
check("after hours is never taken for the close", vp.latest_close("YELP"), None)
_Q.update(status="Closed", stamp="")
check("no stamp, no close", vp.latest_close("YELP"), None)

# raw_quote exists so probe_close.py can show what Nasdaq actually says at
# four in the afternoon, which is what decides whether the screen can publish
# then. It must never change what latest_close does with the same answer.
_Q.update(status="Market Open", stamp="Sep 22, 2026 2:31 PM ET")
_raw = vp.raw_quote("YELP")
check("raw_quote hands back what Nasdaq said",
      (_raw["marketStatus"], _raw["primaryData"]["lastTradeTimestamp"]),
      ("Market Open", "Sep 22, 2026 2:31 PM ET"))
check("and latest_close still refuses an open market", vp.latest_close("YELP"), None)
vp._fetch = _real_fetch

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("daily run tests pass")
