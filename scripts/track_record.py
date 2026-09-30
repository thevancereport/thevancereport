"""The screen's public track record, and the Vance Index.

Runs after each night's screen. Two outputs, both rebuilt from files the
screen already writes, so nothing here can disagree with the site:

scorecard.json
    Every company that has reached the top ten (screen_history.json
    "entries"), what it has done since, and what the S&P 500 did over exactly
    the same days. Winners and losers alike: a list that drops the misses is
    an advert, not a record.

vance_index.json
    A paper portfolio that follows the screen by fixed rules (RULES below),
    compared with the S&P 500 and with the average company the screen ranks.
    It is kept from the first day, but not shown on the site until it has
    three months behind it.

Measured from the first price a reader could actually have had. The screen
publishes in the evening, after the close it was built from, so a name that
enters on Monday could first be bought on Tuesday. Both the scorecard and the
index therefore start from the close of the session AFTER a name appears.

Prices are closes, without dividends, for the stocks and the S&P 500 alike.
"""

import json
import os
import statistics
import sys
from datetime import date, timedelta

HISTORY_FILE = os.environ.get("HISTORY_FILE", "screen_history.json")
SCREEN_FILE = os.environ.get("SCREEN_FILE", "value_screen.json")
SCORECARD_FILE = os.environ.get("SCORECARD_FILE", "scorecard.json")
INDEX_FILE = os.environ.get("INDEX_FILE", "vance_index.json")
BENCH = "SPY"

RULES = {
    "holdings": 10,
    "weighting": "equal",
    "selection": "the ten highest-ranked companies on the screen",
    "rebalance": ("monthly: the ranking of the first session of each month, "
                  "bought and sold at the close of the next session"),
    "costs": "0.5% round trip on companies worth $1bn or more, 1.0% below that (the backtest's costs)",
    "prices": "closing prices, dividends not included, for the index and the S&P 500 alike",
    "benchmarks": ["S&P 500 (SPY)", "the average company the screen ranks, equal weight, daily"],
    "public_after": "three months of history",
    "frozen": "2026-09-30",
}
ONE_WAY_COST_BIG = 0.0025   # half of the 0.5% round trip
ONE_WAY_COST_SMALL = 0.005  # half of the 1.0% round trip
BIG = 1_000_000_000
PUBLIC_AFTER_DAYS = 91


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def pct(a, b):
    """Percent change from a to b, or None."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a <= 0:
        return None
    return round((b / a - 1.0) * 100.0, 2)


def title_name(name):
    small = {"and", "of", "the", "&"}
    keep = {"INC", "INC.", "CORP", "CORP.", "LTD", "LTD.", "PLC", "CO", "CO.", "N.V.", "S.A."}
    words = []
    for w in (name or "").replace(",", "").split():
        if w.upper() in keep:
            continue
        lw = w.lower()
        words.append(lw if lw in small and words else lw[:1].upper() + lw[1:])
    return " ".join(words) or name


# --------------------------------------------------------------- benchmark --

def spy_closes(sessions, have, fetch=None):
    """Fill in the S&P 500 close for every session we need but lack.

    "fetch" returns {iso_date: close} for a date range; in the job it asks
    Nasdaq (value_prices), in the tests it is a stub. A session it cannot
    price stays missing: a gap is honest, an invented close is not.
    """
    need = [s for s in sessions if s not in have]
    if need and fetch is not None:
        try:
            got = fetch(min(need), max(need)) or {}
        except Exception as exc:  # noqa: BLE001 -- never fail the night over this
            print(f"benchmark fetch failed: {exc}")
            got = {}
        for s in need:
            if s in got:
                have[s] = got[s]
    return have


def nasdaq_fetch(start_iso, end_iso):
    """{iso: close} for SPY between two dates, from the sources the screen uses."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import value_prices as vp  # noqa: E402 -- only in the job, not the tests
    start = date.fromisoformat(start_iso) - timedelta(days=720)   # the source wants 300+ sessions
    end = date.fromisoformat(end_iso)
    out = {}
    got = vp.from_nasdaq(BENCH, start, end + timedelta(days=1), classes=("etf",))
    if got:
        for d, c in zip(got[0], got[1]):
            out[d.isoformat()] = c
    # The historical table can lag the day by hours; the closing quote does not.
    q = vp.latest_close(BENCH, classes=("etf",))
    if q and q[0].isoformat() <= end_iso:
        out[q[0].isoformat()] = q[1]
    return out


# --------------------------------------------------------------- scorecard --

def scorecard(history, spy, today_ranks, as_of, outside=None):
    """One row per company that has ever reached the top ten.

    "outside" holds closes fetched for companies that have since left the
    ranked universe, so a name that collapses below the screen's floors keeps
    being measured instead of freezing at its last good price.
    """
    sessions = [s["session"] for s in history.get("sessions", [])]
    prices = {k: dict(v) for k, v in history.get("prices", {}).items()}
    for sym, extra in (outside or {}).items():
        prices.setdefault(sym, {}).update({d: c for d, c in extra.items() if d not in prices.get(sym, {})})
    sessions = sorted(set(sessions) | {d for v in (outside or {}).values() for d in v})
    rows = []
    for sym, e in history.get("entries", {}).items():
        entered = e.get("entered")
        after = [s for s in sessions if s > entered]
        p = prices.get(sym, {})
        row = {
            "symbol": sym,
            "name": title_name(e.get("name")),
            "sector": e.get("sector"),
            "entered": entered,
            "entry_rank": e.get("entry_rank"),
            "rank_now": today_ranks.get(sym),
        }
        if not after:
            row["status"] = "new"      # first tradeable close is tomorrow's
            rows.append(row)
            continue
        priced = sorted(d for d in p if d > entered)
        if not priced:
            row["status"] = "no price"  # never priced after it entered
            rows.append(row)
            continue
        # Normally the next session. If a data gap left that session unpriced,
        # the first close we do have, and the row says so.
        start, last = priced[0], priced[-1]
        if start != after[0]:
            row["late_start"] = True
        row.update({
            "from": start, "from_price": p[start],
            "to": last, "to_price": p[last],
            "return_pct": pct(p[start], p[last]),
            "spy_pct": pct(spy.get(start), spy.get(last)),
            "status": "tracked" if last == as_of else "stopped",
        })
        if row["return_pct"] is not None and row["spy_pct"] is not None:
            row["vs_spy_pct"] = round(row["return_pct"] - row["spy_pct"], 2)
        rows.append(row)

    measured = [r for r in rows if r.get("return_pct") is not None]
    rets = [r["return_pct"] for r in measured]
    ex = [r["vs_spy_pct"] for r in measured if r.get("vs_spy_pct") is not None]
    summary = {
        "companies": len(rows),
        "measured": len(measured),
        "up": sum(1 for x in rets if x > 0),
        "down": sum(1 for x in rets if x < 0),
        "average_pct": round(statistics.mean(rets), 2) if rets else None,
        "median_pct": round(statistics.median(rets), 2) if rets else None,
        "average_vs_spy_pct": round(statistics.mean(ex), 2) if ex else None,
        "beat_spy": sum(1 for x in ex if x > 0),
        "best": max(measured, key=lambda r: r["return_pct"])["symbol"] if measured else None,
        "worst": min(measured, key=lambda r: r["return_pct"])["symbol"] if measured else None,
    }
    rows.sort(key=lambda r: (r.get("entered") or "", r.get("entry_rank") or 99))
    return {
        "_about": ("Every company that has reached the screen's top ten, measured from the close of the "
                   "session after it got there (the first price a reader could have had), against the "
                   "S&P 500 over the same days. Closing prices, dividends not included. Written by "
                   "scripts/track_record.py after each screen. Past results do not predict future ones."),
        "as_of": as_of,
        "first_session": sessions[0] if sessions else None,
        "summary": summary,
        "companies": rows,
    }


# ------------------------------------------------------------------- index --

def blank_index():
    return {
        "_about": ("The Vance Index: a paper portfolio that follows the screen by fixed rules. Kept from "
                   "the first day; not shown on the site until it has three months of history. Written "
                   "by scripts/track_record.py after each screen."),
        "rules": RULES,
        "started": None, "public_from": None, "last_session": None,
        "level": None, "holdings": [], "pending": None, "last_signal": None,
        "prev_prices": {}, "universe_level": 100.0, "spy_start": None,
        "series": [], "rebalances": [],
    }


def cost_rate(cap):
    return ONE_WAY_COST_BIG if isinstance(cap, (int, float)) and cap >= BIG else ONE_WAY_COST_SMALL


def step_index(ix, session, ranked, spy_close, fallback_price=None):
    """Advance the index by one session. Returns what happened, for the log.

    Order within a session: value the book at today's closes, then carry out
    a trade signalled at the previous session (at today's close), then decide
    whether today's ranking signals the next trade.
    """
    if ix.get("last_session") and session <= ix["last_session"]:
        return "already recorded"

    px = {r["symbol"]: r.get("price") for r in ranked if isinstance(r.get("price"), (int, float))}
    caps = {r["symbol"]: r.get("market_cap") for r in ranked}
    notes = []

    def price_of(sym, last):
        p = px.get(sym)
        if p is None and fallback_price is not None:
            p = fallback_price(sym)
        if p is None:
            notes.append(f"{sym} unpriced, carried at {last}")
            return last
        return p

    # 1. value the book
    for h in ix["holdings"]:
        h["price"] = price_of(h["symbol"], h["price"])
    value = sum(h["shares"] * h["price"] for h in ix["holdings"])

    # the average ranked company, day on day
    prev = ix.get("prev_prices") or {}
    moves = [px[s] / prev[s] - 1.0 for s in px if s in prev and prev[s] > 0]
    if moves and ix.get("started"):
        ix["universe_level"] = round(ix["universe_level"] * (1.0 + statistics.mean(moves)), 4)
    ix["prev_prices"] = px

    # 2. trade what was signalled yesterday, at today's close
    pend = ix.get("pending")
    if pend:
        if not ix.get("started"):
            ix["started"] = session
            ix["public_from"] = (date.fromisoformat(session) + timedelta(days=PUBLIC_AFTER_DAYS)).isoformat()
            ix["spy_start"] = spy_close
            ix["universe_level"] = 100.0
            value = 100.0
        target = [s for s in pend["symbols"] if px.get(s) is not None][:RULES["holdings"]]
        old = {h["symbol"]: h["shares"] * h["price"] for h in ix["holdings"]}
        if target:
            per = value / len(target)
            new = {s: per for s in target}
            traded = sum(abs(new.get(s, 0.0) - old.get(s, 0.0)) * cost_rate(caps.get(s))
                         for s in set(new) | set(old))
            value -= traded
            per = value / len(target)
            ix["holdings"] = [{"symbol": s, "shares": per / px[s], "price": px[s],
                               "bought": session if s not in old else
                               next(h["bought"] for h in ix["holdings"] if h["symbol"] == s)}
                              for s in target]
            ix["rebalances"].append({
                "signal": pend["signal"], "traded": session,
                "in": sorted(set(new) - set(old)), "out": sorted(set(old) - set(new)),
                "cost_pct": round(traded / (value + traded) * 100.0, 3) if value + traded else 0.0,
            })
            notes.append(f"rebalanced into {', '.join(target)}")
        ix["pending"] = None

    # 3. does today's ranking signal a trade? (first session of a month)
    month = session[:7]
    if ix.get("last_signal") is None or ix["last_signal"][:7] != month:
        first_run = ix.get("last_session") is None
        if not first_run:      # the very first session only starts the clock
            ix["pending"] = {"signal": session,
                             "symbols": [r["symbol"] for r in ranked[:RULES["holdings"]]]}
            ix["last_signal"] = session
            notes.append(f"signal: next session buys the {session} top {RULES['holdings']}")
        else:
            ix["last_signal"] = session   # this month is spoken for; October's signal comes first

    # 4. record the day
    if ix.get("started"):
        ix["level"] = round(value, 4)
        spy_level = (round(100.0 * spy_close / ix["spy_start"], 4)
                     if spy_close and ix.get("spy_start") else None)
        ix["series"].append({"session": session, "index": ix["level"], "sp500": spy_level,
                             "universe": ix["universe_level"], "holdings": len(ix["holdings"])})
    ix["last_session"] = session
    return "; ".join(notes) or "recorded"


# -------------------------------------------------------------------- main --

def main():
    history = load(HISTORY_FILE)
    screen = load(SCREEN_FILE)
    if not history or not screen:
        print("screen_history.json or value_screen.json missing; nothing to do")
        return 0
    as_of = (screen.get("_meta") or {}).get("as_of")
    ranked = screen.get("ranked") or []
    if not as_of or len(ranked) < 200:
        print(f"screen file not usable (as_of={as_of}, {len(ranked)} ranked); nothing to do")
        return 0

    ix = load(INDEX_FILE) or blank_index()
    spy = dict((ix.get("sp500_closes") or {}))
    sessions = sorted({s["session"] for s in history.get("sessions", [])} | {as_of})
    spy = spy_closes(sessions, spy, fetch=None if os.environ.get("NO_FETCH") else nasdaq_fetch)
    ix["sp500_closes"] = dict(sorted(spy.items()))

    fallback = None
    if not os.environ.get("NO_FETCH"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import value_prices as vp  # noqa: E402

        def fallback(sym):
            q = vp.latest_close(sym)
            return q[1] if q and q[0].isoformat() == as_of else None

    what = step_index(ix, as_of, ranked, spy.get(as_of), fallback)
    save(INDEX_FILE, ix)

    ranks = {r["symbol"]: r.get("rank") for r in ranked}
    outside = ix.setdefault("outside_prices", {})
    if fallback is not None:
        for sym in history.get("entries", {}):
            if sym not in ranks and as_of not in outside.get(sym, {}):
                c = fallback(sym)
                if c is not None:
                    outside.setdefault(sym, {})[as_of] = c
        save(INDEX_FILE, ix)
    card = scorecard(history, spy, ranks, as_of, outside)
    save(SCORECARD_FILE, card)

    s = card["summary"]
    print(f"{as_of}: index {what}. Scorecard: {s['measured']} of {s['companies']} measured, "
          f"average {s['average_pct']}% vs S&P 500 {s['average_vs_spy_pct']:+}% "
          if s["average_vs_spy_pct"] is not None else
          f"{as_of}: index {what}. Scorecard: {s['measured']} of {s['companies']} measured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
