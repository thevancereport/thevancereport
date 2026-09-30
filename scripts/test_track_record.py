"""Offline tests for the scorecard and the Vance Index."""

import track_record as tr

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


def near(name, got, want, tol=1e-6):
    check(name, got is not None and abs(got - want) < tol, True)


print("\npct")
check("ten percent", tr.pct(100.0, 110.0), 10.0)
check("no base price", tr.pct(None, 110.0), None)
check("zero base price", tr.pct(0.0, 110.0), None)

print("\nscorecard")
history = {
    "sessions": [{"session": d} for d in ("2026-09-21", "2026-09-22", "2026-09-23")],
    "entries": {
        "AAA": {"entered": "2026-09-21", "entry_rank": 1, "entry_price": 10.0, "name": "AAA HOLDINGS, INC.", "sector": "X"},
        "BBB": {"entered": "2026-09-21", "entry_rank": 2, "entry_price": 20.0, "name": "BBB CORP", "sector": "Y"},
        "CCC": {"entered": "2026-09-23", "entry_rank": 3, "entry_price": 5.0, "name": "CCC", "sector": "Z"},
        "DDD": {"entered": "2026-09-21", "entry_rank": 4, "entry_price": 7.0, "name": "DDD", "sector": "Z"},
    },
    "prices": {
        "AAA": {"2026-09-21": 10.0, "2026-09-22": 11.0, "2026-09-23": 12.1},
        "BBB": {"2026-09-21": 20.0, "2026-09-22": 20.0, "2026-09-23": 18.0},
        "CCC": {"2026-09-23": 5.0},
        "DDD": {"2026-09-21": 7.0},           # left the universe the next day
    },
}
spy = {"2026-09-21": 100.0, "2026-09-22": 100.0, "2026-09-23": 101.0}
card = tr.scorecard(history, spy, {"AAA": 1, "BBB": 30}, "2026-09-23")
rows = {r["symbol"]: r for r in card["companies"]}
check("measured from the session after entry, not the entry close", rows["AAA"]["from"], "2026-09-22")
near("AAA return is 11 -> 12.1", rows["AAA"]["return_pct"], 10.0)
near("S&P 500 over the same days", rows["AAA"]["spy_pct"], 1.0)
near("excess over the S&P 500", rows["AAA"]["vs_spy_pct"], 9.0)
near("a loser is kept, not hidden", rows["BBB"]["return_pct"], -10.0)
check("entered today: waits for a tradeable close", rows["CCC"]["status"], "new")
check("never priced after entry is reported, not dropped", rows["DDD"]["status"], "no price")
check("every company is listed", card["summary"]["companies"], 4)
check("only measurable ones count in the average", card["summary"]["measured"], 2)
near("average is of winners and losers alike", card["summary"]["average_pct"], 0.0)
check("one of two beat the S&P 500", card["summary"]["beat_spy"], 1)
check("names are tidied", rows["AAA"]["name"], "Aaa Holdings")
card = tr.scorecard(history, spy, {"AAA": 1}, "2026-09-23",
                    outside={"DDD": {"2026-09-22": 7.0, "2026-09-23": 3.5}})
rows = {r["symbol"]: r for r in card["companies"]}
near("a name that fell out of the universe is still measured", rows["DDD"]["return_pct"], -50.0)
check("and the fall counts in the average", card["summary"]["measured"], 3)

print("\nindex")
def ranked(prices, caps=None):
    return [{"symbol": s, "price": p, "market_cap": (caps or {}).get(s, 5e9), "rank": i + 1}
            for i, (s, p) in enumerate(prices)]

ix = tr.blank_index()
tr.RULES["holdings"] = 2      # small book for the test
# 30 Sep: first run only starts the clock, no trade
check("first session", tr.step_index(ix, "2026-09-30", ranked([("A", 10.0), ("B", 20.0), ("C", 5.0)]), 400.0), "recorded")
check("nothing held before the first signal", ix["holdings"], [])
# 1 Oct: first session of October signals the top two
tr.step_index(ix, "2026-10-01", ranked([("A", 10.0), ("B", 20.0), ("C", 5.0)]), 400.0)
check("October signal waits for the next close", ix["pending"]["symbols"], ["A", "B"])
check("not started until the trade", ix["started"], None)
# 2 Oct: bought at the close
tr.step_index(ix, "2026-10-02", ranked([("C", 5.0), ("A", 10.0), ("B", 20.0)]), 400.0)
check("started on the day of the first trade", ix["started"], "2026-10-02")
check("bought the signalled names, not today's top", sorted(h["symbol"] for h in ix["holdings"]), ["A", "B"])
near("costs come off the starting 100", ix["level"], 100.0 * (1 - 0.0025) / 1.0, 1e-6)
check("the public date is three months on", ix["public_from"], "2027-01-01")
# 5 Oct: A up 10%, B flat, S&P 500 up 1%
lvl0 = ix["level"]
tr.step_index(ix, "2026-10-05", ranked([("A", 11.0), ("B", 20.0), ("C", 5.0)]), 404.0)
near("equal weight: half the book up 10% is +5%", ix["level"] / lvl0, 1.05, 1e-9)
check("S&P 500 line starts at 100 on the start day", ix["series"][-1]["sp500"], 101.0)
near("the average ranked company moved (10% + 0 + 0) / 3", ix["universe_level"], 100.0 * (1 + 0.1 / 3), 1e-3)
check("no new signal inside the month", ix["pending"], None)
check("re-running a session changes nothing", tr.step_index(ix, "2026-10-05", ranked([("A", 99.0)]), 1.0), "already recorded")
# a holding that drops out of the ranked universe is carried, not dropped
tr.step_index(ix, "2026-10-06", ranked([("B", 20.0), ("C", 5.0)]), 404.0)
check("an unranked holding keeps its last price", [h["price"] for h in ix["holdings"] if h["symbol"] == "A"], [11.0])
# 2 Nov: new month signals, 3 Nov trades
tr.step_index(ix, "2026-11-02", ranked([("C", 5.0), ("B", 20.0), ("A", 11.0)]), 404.0)
check("November signal", ix["pending"]["symbols"], ["C", "B"])
tr.step_index(ix, "2026-11-03", ranked([("C", 5.0), ("B", 20.0), ("A", 11.0)]), 404.0)
check("rebalanced into the new top two", sorted(h["symbol"] for h in ix["holdings"]), ["B", "C"])
check("the log says what came in and went out", (ix["rebalances"][-1]["in"], ix["rebalances"][-1]["out"]), (["C"], ["A"]))
check("B kept its original purchase date", [h["bought"] for h in ix["holdings"] if h["symbol"] == "B"], ["2026-10-02"])
tr.RULES["holdings"] = 10

print("\n" + "=" * 60)
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("all track-record tests pass")
