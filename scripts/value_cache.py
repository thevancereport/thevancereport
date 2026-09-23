"""Price histories kept between runs, so a normal night only fetches the day.

The nightly run used to ask Nasdaq for every symbol's last two years of closes:
one request per symbol, about three thousand symbols, six at a time. That is
roughly a million and a half rows downloaded to use the three thousand at the
end of them, and it is where the run's forty to eighty minutes went. It also
meant the screen could not go up until hours after the close, which is the
part that matters -- people read this in the evening, and a list that arrives
at eleven at night is a list for tomorrow.

So the histories are kept between runs and only the new session is fetched.

Three things can go wrong with a cache of prices, and each has a guard here:

  * It goes stale. A symbol whose last cached session is more than
    MAX_GAP_DAYS old is refetched in full rather than having a day stapled
    onto a hole. Five days covers a weekend plus a Monday holiday.

  * A company splits. Nasdaq's historical table is split-adjusted and its
    quote is not, so a 2-for-1 would land in the cache as a 50% fall and
    poison momentum and the 200-day average for that name. `append` refuses
    any day that moves more than JUMP_LIMIT and asks for a refetch instead.
    A genuine crash of that size gets refetched too, which costs one request.

  * It drifts quietly. `due_for_refresh` retires a slice of the cache every
    night -- with the default one in twenty, the whole thing is rebuilt from
    source about once a month -- so no error can live in it indefinitely.

Nothing here reaches the network. The caller fetches; this module only decides
what has to be fetched and keeps what came back.
"""

import gzip
import json
import os
import zlib
from datetime import date, timedelta

SCHEMA = 1

# About two and a half years, which is what the 200-day average and the 12-1
# momentum window need with room to spare. Trimming keeps the file to roughly
# eight megabytes gzipped for three thousand symbols.
MAX_SESSIONS = int(os.environ.get("CACHE_MAX_SESSIONS", "620"))

# A cached series must reach within this many calendar days of the session
# being ranked, or it is refetched rather than extended.
MAX_GAP_DAYS = int(os.environ.get("CACHE_MAX_GAP_DAYS", "5"))

# A one-day move larger than this is treated as a corporate action rather than
# a price, because the cache is split-adjusted and a live quote is not.
JUMP_LIMIT = float(os.environ.get("CACHE_JUMP_LIMIT", "0.35"))

# Share of the cache refetched from source each night, so nothing stays wrong
# forever. One in twenty rebuilds everything in about a month.
REFRESH_SHARE = float(os.environ.get("CACHE_REFRESH_SHARE", "0.05"))


def blank():
    return {"schema": SCHEMA, "through": None, "symbols": {}}


def load(path):
    """The cache at `path`, or an empty one.

    Any failure -- missing, truncated, not gzip, wrong schema -- returns an
    empty cache with the reason, because a bad cache must cost one slow run
    and never a wrong number. Returns (cache, note).
    """
    if not path or not os.path.exists(path):
        return blank(), "no cache file yet"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        return blank(), f"unreadable ({type(exc).__name__})"
    if not isinstance(got, dict) or got.get("schema") != SCHEMA:
        return blank(), f"schema {got.get('schema') if isinstance(got, dict) else '?'}, wanted {SCHEMA}"
    if not isinstance(got.get("symbols"), dict):
        return blank(), "no symbols in it"
    return got, f"{len(got['symbols']):,} symbols, through {got.get('through')}"


def save(cache, path, through=None):
    """Write the cache, atomically. Returns the size in bytes."""
    if not path:
        return 0
    cache["schema"] = SCHEMA
    if through is not None:
        cache["through"] = through.isoformat() if hasattr(through, "isoformat") else through
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
        json.dump(cache, fh, separators=(",", ":"))
    os.replace(tmp, path)
    return os.path.getsize(path)


def series(cache, symbol):
    """(days, closes, vols) for `symbol` with real dates, or None."""
    row = (cache.get("symbols") or {}).get(symbol)
    if not row:
        return None
    try:
        days = [date.fromisoformat(d) for d in row["days"]]
        closes = [float(c) for c in row["closes"]]
        vols = [float(v) for v in row["vols"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not days or not (len(days) == len(closes) == len(vols)):
        return None
    return days, closes, vols


def put(cache, symbol, days, closes, vols):
    """Store a freshly fetched history, trimmed to MAX_SESSIONS."""
    days, closes, vols = list(days), list(closes), list(vols)
    if len(days) > MAX_SESSIONS:
        days, closes, vols = days[-MAX_SESSIONS:], closes[-MAX_SESSIONS:], vols[-MAX_SESSIONS:]
    cache.setdefault("symbols", {})[symbol] = {
        "days": [d.isoformat() for d in days],
        "closes": [round(float(c), 6) for c in closes],
        "vols": [float(v) for v in vols],
    }


def drop(cache, symbol):
    (cache.get("symbols") or {}).pop(symbol, None)


def usable(cache, symbol, as_of, min_sessions):
    """Can this symbol be extended rather than refetched?

    Returns (True, series) or (False, reason). The series is returned so the
    caller does not have to parse the dates twice.
    """
    got = series(cache, symbol)
    if got is None:
        return False, "not cached"
    days, _closes, _vols = got
    if len(days) < min_sessions:
        return False, f"only {len(days)} sessions"
    if days[-1] > as_of:
        return False, f"cached past the session ({days[-1]})"
    if (as_of - days[-1]).days > MAX_GAP_DAYS:
        return False, f"stops at {days[-1]}"
    return True, got


def due_for_refresh(symbol, as_of, share=None):
    """True for a stable slice of the universe each day.

    The slice is a function of the symbol and the date, so it needs no state
    and no ordering, and a given symbol comes up about every 1/share days.
    """
    share = REFRESH_SHARE if share is None else share
    if share <= 0:
        return False
    if share >= 1:
        return True
    seed = f"{symbol}:{as_of.isoformat()}".encode("utf-8")
    return (zlib.crc32(seed) % 10000) < share * 10000


def append(cache, symbol, when, close, vol):
    """Add one session. Returns (True, None) or (False, reason).

    Refuses anything that is not strictly newer than what is there, and
    anything that moves more than JUMP_LIMIT, which in a split-adjusted
    history means a corporate action rather than a day's trading.
    """
    got = series(cache, symbol)
    if got is None:
        return False, "not cached"
    days, closes, vols = got
    if when <= days[-1]:
        return False, "not newer"
    if close is None or close <= 0:
        return False, "no price"
    last = closes[-1]
    if last > 0 and abs(close / last - 1.0) > JUMP_LIMIT:
        return False, f"moved {(close / last - 1.0) * 100:+.0f}% -- looks like a split"
    put(cache, symbol, days + [when], closes + [float(close)], vols + [float(vol or 0.0)])
    return True, None


def plan(cache, symbols, as_of, min_sessions, share=None):
    """Split the universe into what must be refetched and what can be extended.

    Returns (full, extend, why) -- two symbol lists and a Counter-ish dict of
    reasons, for the log. The caller fetches `full` the slow way and asks only
    for `extend`'s latest close.
    """
    full, extend, why = [], [], {}

    def note(reason):
        why[reason] = why.get(reason, 0) + 1

    for sym in symbols:
        if due_for_refresh(sym, as_of, share):
            full.append(sym)
            note("in tonight's refresh slice")
            continue
        ok, detail = usable(cache, sym, as_of, min_sessions)
        if ok:
            extend.append(sym)
        else:
            full.append(sym)
            note(detail)
    return full, extend, why


def summary(cache):
    syms = cache.get("symbols") or {}
    if not syms:
        return "cache: empty"
    ends = {}
    rows = 0
    for row in syms.values():
        days = row.get("days") or []
        rows += len(days)
        if days:
            ends[days[-1]] = ends.get(days[-1], 0) + 1
    top = sorted(ends.items(), key=lambda kv: -kv[1])[:3]
    return (f"cache: {len(syms):,} symbols, {rows:,} sessions, "
            + ", ".join(f"{n:,} end {d}" for d, n in top))


def prune(cache, symbols):
    """Forget symbols that are no longer in the universe."""
    keep = set(symbols)
    syms = cache.get("symbols") or {}
    gone = [s for s in syms if s not in keep]
    for s in gone:
        del syms[s]
    return len(gone)
