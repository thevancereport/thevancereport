"""Append each weekday's top names to screen_history.json.

Run after value_daily.py, before the commit step. It exists so that months from
now the site can say when a company first entered the top ten and what its price
has done since -- a record that cannot be reconstructed later, because
value_screen.json only ever holds today.

What it keeps, per session: the top N rows (rank, symbol, name, sector, score,
close) and the session's own metadata. Prices are the closes the scores were
built from, so the history and the scores can never disagree.

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
MIN_RANKED = int(os.environ.get("HISTORY_MIN_RANKED", "200"))

NOTE = (
    "Every weekday's top 25, kept so the site can later say when a company "
    "entered the top ten and what has happened since. Written by "
    "scripts/screen_history.py after each run of the daily screen. Prices are "
    "the session's close, the same ones the scores were built from."
)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


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
    return history


def main():
    screen = load(SCREEN_FILE, None)
    if screen is None:
        print(f"No {SCREEN_FILE}; nothing recorded.")
        return 0

    record, why = entry(screen)
    if record is None:
        print(f"Not recorded: {why}.")
        return 0

    history = load(HISTORY_FILE, {"note": NOTE, "top_n": TOP_N, "sessions": []})
    known = {s.get("session") for s in history.get("sessions", [])}
    history = merge(history, record)

    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=1)
        fh.write("\n")

    verb = "Updated" if record["session"] in known else "Recorded"
    print(
        f"{verb} {record['session']}: top {len(record['top'])} of "
        f"{record['universe']}; {len(history['sessions'])} sessions on file."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
