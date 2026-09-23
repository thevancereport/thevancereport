"""Append each weekday's top names, and the price line of everything that has
ever reached the top ten, to screen_history.json.

Run after value_daily.py, before the commit step. It exists so that months from
now the site can say when a company first entered the top ten and what its price
has done since -- a record that cannot be reconstructed later, because
value_screen.json only ever holds today.

Three things are kept:

  * ``sessions``  the top N of each session (rank, symbol, name, sector, score,
    close) plus that session's metadata;
  * ``entries``   the first time each company reached the top ten, with the
    close on that day;
  * ``prices``    for every company in ``entries``, its close in every session
    since, for as long as it stays in the ranked universe.

The third is the point. Without it a company that enters the top ten and later
slips to fortieth stops being priced, and "since it entered" goes stale exactly
when it becomes interesting. The screen already computes a close for every
ranked company, so following them costs nothing but the lines below.

Prices here are the closes the scores were built from, so the history and the
scores can never disagree.

Guards, in the same spirit as daily_alert.py:

  * a thin file is refused, so a bad data day never enters the record;
  * a session already in the file is replaced, not duplicated, which makes a
    re-run safe;
  * sessions stay sorted by date, so the file reads as a timeline.

Nothing here decides what the site shows. It only makes sure the evidence
exists.
"""

import json
import os
import sys

SCREEN_FILE = os.environ.get("SCREEN_FILE", "value_screen.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "screen_history.json")
TOP_N = int(os.environ.get("HISTORY_TOP_N", "25"))
ENTRY_RANK = int(os.environ.get("HISTORY_ENTRY_RANK", "10"))
MIN_RANKED = int(os.environ.get("HISTORY_MIN_RANKED", "200"))

NOTE = (
    "Every weekday's top 25, when each company first reached the top ten, and "
    "the close of every such company in every session since. Written by "
    "scripts/screen_history.py after each run of the daily screen. Prices are "
    "the session's close, the same ones the scores were built from."
)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def blank():
    return {"note": NOTE, "top_n": TOP_N, "entry_rank": ENTRY_RANK,
            "sessions": [], "entries": {}, "prices": {}}


def entry(screen, top_n=TOP_N):
    """One session's record, or a reason it cannot be made."""
    meta = screen.get("_meta") or {}
    ranked = screen.get("ranked") or []
    session = meta.get("as_of")
    if not session:
        return None, "no as_of in the screen file"
    if len(ranked) < MIN_RANKED:
        return None, f"thin file: {len(ranked)} ranked"

    top = []
    for row in ranked[:top_n]:
        score = row.get("score")
        top.append({
            "rank": row.get("rank"),
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "sector": row.get("sector"),
            "score": round(score, 2) if isinstance(score, (int, float)) else None,
            "price": row.get("price"),
        })

    return {
        "session": session,
        "prices_through": meta.get("prices_through", session),
        "generated_at": meta.get("generated_at"),
        "universe": meta.get("universe", len(ranked)),
        "top": top,
    }, None


def merge(history, record):
    """Replace the session if it is already recorded; keep the file sorted."""
    sessions = [s for s in history.get("sessions", []) if s.get("session") != record["session"]]
    sessions.append(record)
    sessions.sort(key=lambda s: s.get("session") or "")
    history["sessions"] = sessions
    history["note"] = history.get("note") or NOTE
    history["top_n"] = TOP_N
    history["entry_rank"] = ENTRY_RANK
    return history


def follow(history, screen, session, entry_rank=ENTRY_RANK):
    """Note new arrivals in the top ten, then price everything followed.

    Returns (new_symbols, priced_count). A followed company missing from the
    ranked universe is simply not priced for that session: a gap is honest,
    an invented price is not.
    """
    ranked = screen.get("ranked") or []
    entries = history.setdefault("entries", {})
    prices = history.setdefault("prices", {})

    new = []
    for row in ranked:
        rank, symbol = row.get("rank"), row.get("symbol")
        if not symbol or not isinstance(rank, int) or rank > entry_rank:
            continue
        if symbol in entries:
            continue
        entries[symbol] = {
            "entered": session,
            "entry_rank": rank,
            "entry_price": row.get("price"),
            "name": row.get("name"),
            "sector": row.get("sector"),
        }
        new.append(symbol)

    by_symbol = {r.get("symbol"): r for r in ranked}
    priced = 0
    for symbol in entries:
        row = by_symbol.get(symbol)
        price = row.get("price") if row else None
        if not isinstance(price, (int, float)):
            continue
        prices.setdefault(symbol, {})[session] = price
        priced += 1

    return new, priced


def main():
    screen = load(SCREEN_FILE, None)
    if screen is None:
        print(f"No {SCREEN_FILE}; nothing recorded.")
        return 0

    record, why = entry(screen)
    if record is None:
        print(f"Not recorded: {why}.")
        return 0

    history = load(HISTORY_FILE, None) or blank()
    known = {s.get("session") for s in history.get("sessions", [])}
    history = merge(history, record)
    new, priced = follow(history, screen, record["session"])

    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=1)
        fh.write("\n")

    verb = "Updated" if record["session"] in known else "Recorded"
    print(
        f"{verb} {record['session']}: top {len(record['top'])} of "
        f"{record['universe']}; {len(history['sessions'])} sessions on file."
    )
    print(
        f"Following {len(history['entries'])} companies that have reached the "
        f"top {ENTRY_RANK}"
        + (f" (new: {', '.join(new)})" if new else "")
        + f"; priced {priced} this session."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
