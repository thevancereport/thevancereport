"""Offline tests for the price cache. No network, no fixtures, a second to run."""

import gzip
import os
import sys
import tempfile
from datetime import date, timedelta

import value_cache as vcache


FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok   {name}")


def truthy(name, got):
    if not got:
        FAILURES.append(f"{name}: got {got!r}")
        print(f"  FAIL {name}: got {got!r}")
    else:
        print(f"  ok   {name}")


def make(n=400, end=date(2026, 9, 21), start_price=10.0):
    days, closes, vols = [], [], []
    d = end - timedelta(days=n - 1)
    for i in range(n):
        days.append(d + timedelta(days=i))
        closes.append(start_price + i * 0.01)
        vols.append(1_000_000.0)
    return days, closes, vols


def test_round_trip():
    print("round trip")
    days, closes, vols = make()
    c = vcache.blank()
    vcache.put(c, "AAA", days, closes, vols)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prices.json.gz")
        size = vcache.save(c, p, through=date(2026, 9, 21))
        truthy("writes a file", size > 0)
        truthy("it is gzip", gzip.open(p, "rb").read(2) is not None)
        back, note = vcache.load(p)
        check("through survives", back["through"], "2026-09-21")
        got = vcache.series(back, "AAA")
        check("same number of sessions", len(got[0]), len(days))
        check("same last close", round(got[1][-1], 6), round(closes[-1], 6))
        check("same last day", got[0][-1], days[-1])
        truthy("note describes it", "1 symbols" in note or "1 symbol" in note)


def test_trims():
    print("trimming")
    days, closes, vols = make(n=900)
    c = vcache.blank()
    vcache.put(c, "AAA", days, closes, vols)
    got = vcache.series(c, "AAA")
    check("trimmed to the cap", len(got[0]), vcache.MAX_SESSIONS)
    check("kept the newest end", got[0][-1], days[-1])


def test_bad_files():
    print("a bad cache costs a slow run, never a wrong number")
    with tempfile.TemporaryDirectory() as d:
        missing, note = vcache.load(os.path.join(d, "nope.gz"))
        check("missing file is empty", missing["symbols"], {})
        truthy("and says so", "no cache" in note)

        p = os.path.join(d, "junk.gz")
        open(p, "wb").write(b"this is not gzip at all")
        junk, note = vcache.load(p)
        check("junk file is empty", junk["symbols"], {})
        truthy("and says why", "unreadable" in note)

        p2 = os.path.join(d, "old.gz")
        with gzip.open(p2, "wt") as fh:
            fh.write('{"schema": 0, "symbols": {"AAA": {}}}')
        old, note = vcache.load(p2)
        check("old schema is empty", old["symbols"], {})
        truthy("and says which", "schema 0" in note)


def test_usable():
    print("deciding what can be extended")
    as_of = date(2026, 9, 22)
    c = vcache.blank()
    vcache.put(c, "FRESH", *make(end=date(2026, 9, 21)))
    vcache.put(c, "FRIDAY", *make(end=date(2026, 9, 18)))
    vcache.put(c, "STALE", *make(end=date(2026, 9, 10)))
    vcache.put(c, "SHORT", *make(n=40, end=date(2026, 9, 21)))

    ok, _ = vcache.usable(c, "FRESH", as_of, 300)
    check("yesterday is usable", ok, True)
    ok, _ = vcache.usable(c, "FRIDAY", as_of, 300)
    check("across a weekend is usable", ok, True)
    ok, why = vcache.usable(c, "STALE", as_of, 300)
    check("twelve days old is not", ok, False)
    truthy("and says where it stops", "2026-09-10" in why)
    ok, why = vcache.usable(c, "SHORT", as_of, 300)
    check("a short history is not", ok, False)
    truthy("and says how short", "40 sessions" in why)
    ok, why = vcache.usable(c, "NEVER", as_of, 300)
    check("an unknown symbol is not", ok, False)
    check("and says so", why, "not cached")


def test_append():
    print("appending the day")
    c = vcache.blank()
    vcache.put(c, "AAA", *make(end=date(2026, 9, 21), start_price=10.0))
    last = vcache.series(c, "AAA")[1][-1]

    ok, why = vcache.append(c, "AAA", date(2026, 9, 22), last * 1.02, 2_000_000)
    check("a normal day goes in", (ok, why), (True, None))
    got = vcache.series(c, "AAA")
    check("it is the new last day", got[0][-1], date(2026, 9, 22))
    check("with its volume", got[2][-1], 2_000_000.0)

    ok, why = vcache.append(c, "AAA", date(2026, 9, 22), last, 1)
    check("the same day twice is refused", ok, False)
    check("and says why", why, "not newer")

    ok, why = vcache.append(c, "AAA", date(2026, 9, 20), last, 1)
    check("an older day is refused", ok, False)

    ok, why = vcache.append(c, "AAA", date(2026, 9, 23), 0, 1)
    check("a zero price is refused", ok, False)
    check("and says why", why, "no price")


def test_split_is_refused():
    print("a split is refused rather than cached")
    c = vcache.blank()
    vcache.put(c, "AAA", *make(end=date(2026, 9, 21), start_price=100.0))
    last = vcache.series(c, "AAA")[1][-1]

    ok, why = vcache.append(c, "AAA", date(2026, 9, 22), last / 2, 1_000_000)
    check("two-for-one is refused", ok, False)
    truthy("and is called a split", "split" in why)
    check("the history is untouched", vcache.series(c, "AAA")[0][-1], date(2026, 9, 21))

    ok, _ = vcache.append(c, "AAA", date(2026, 9, 22), last * 1.30, 1_000_000)
    check("a hard but real day still goes in", ok, True)


def test_refresh_slice():
    print("the nightly refresh slice")
    as_of = date(2026, 9, 22)
    syms = [f"S{i:04d}" for i in range(4000)]
    picked = [s for s in syms if vcache.due_for_refresh(s, as_of, share=0.05)]
    truthy(f"about one in twenty ({len(picked)} of 4,000)", 120 <= len(picked) <= 280)

    again = [s for s in syms if vcache.due_for_refresh(s, as_of, share=0.05)]
    check("the same slice twice on one date", picked, again)

    tomorrow = [s for s in syms if vcache.due_for_refresh(s, as_of + timedelta(days=1), share=0.05)]
    truthy("a different slice tomorrow", set(picked) != set(tomorrow))

    seen = set()
    for i in range(40):
        seen |= {s for s in syms if vcache.due_for_refresh(s, as_of + timedelta(days=i), share=0.05)}
    truthy(f"most of the universe within 40 days ({len(seen)} of 4,000)", len(seen) > 3200)

    check("share 0 refreshes nothing", vcache.due_for_refresh("AAA", as_of, share=0), False)
    check("share 1 refreshes everything", vcache.due_for_refresh("AAA", as_of, share=1), True)


def test_plan():
    print("planning a night's fetching")
    as_of = date(2026, 9, 22)
    c = vcache.blank()
    vcache.put(c, "FRESH", *make(end=date(2026, 9, 21)))
    vcache.put(c, "STALE", *make(end=date(2026, 8, 1)))
    vcache.put(c, "SHORT", *make(n=20, end=date(2026, 9, 21)))
    syms = ["FRESH", "STALE", "SHORT", "NEW"]

    full, extend, why = vcache.plan(c, syms, as_of, 300, share=0)
    check("only the good one is extended", extend, ["FRESH"])
    check("the rest are refetched", sorted(full), ["NEW", "SHORT", "STALE"])
    check("every refetch has a reason", sum(why.values()), 3)

    full, extend, _ = vcache.plan(c, syms, as_of, 300, share=1)
    check("a full refresh extends nothing", extend, [])
    check("and refetches everything", len(full), 4)


def test_prune():
    print("forgetting delisted symbols")
    c = vcache.blank()
    for s in ("AAA", "BBB", "CCC"):
        vcache.put(c, s, *make(n=310))
    gone = vcache.prune(c, ["AAA", "CCC"])
    check("one dropped", gone, 1)
    check("two left", sorted(c["symbols"]), ["AAA", "CCC"])


def test_end_to_end():
    print("a week of nights")
    as_of = date(2026, 9, 21)
    c = vcache.blank()
    vcache.put(c, "AAA", *make(n=400, end=as_of, start_price=50.0))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "prices.json.gz")
        vcache.save(c, p, through=as_of)
        for i in range(1, 6):
            day = as_of + timedelta(days=i)
            c, _ = vcache.load(p)
            full, extend, _ = vcache.plan(c, ["AAA"], day, 300, share=0)
            check(f"night {i}: nothing refetched", full, [])
            check(f"night {i}: one extended", extend, ["AAA"])
            last = vcache.series(c, "AAA")[1][-1]
            ok, _ = vcache.append(c, "AAA", day, last * 1.01, 1_000_000)
            check(f"night {i}: the day went in", ok, True)
            vcache.save(c, p, through=day)
        final, _ = vcache.load(p)
        got = vcache.series(final, "AAA")
        check("five sessions added", len(got[0]), 405)
        check("ending on the last night", got[0][-1], as_of + timedelta(days=5))


def main():
    for fn in (test_round_trip, test_trims, test_bad_files, test_usable,
               test_append, test_split_is_refused, test_refresh_slice,
               test_plan, test_prune, test_end_to_end):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed:")
        for f in FAILURES:
            print("  " + f)
        sys.exit(1)
    print("value_cache: all checks passed.")


if __name__ == "__main__":
    main()
