"""Daily closes, from whichever free source will actually answer.

The first smoke run read 7,537 SEC filers correctly and then got zero price
histories out of Stooq, and the log could not say why -- the fetcher swallowed
the reason and returned None. So this module does two things the old one did
not: it tries more than one source, and it records exactly why each refusal
happened, so a failure costs one look at the log rather than another run.

Run it directly to probe the sources without touching the backtest:

    python value_prices.py AAPL MSFT IWM
"""

import json
import os
import re
import sys
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIN_SESSIONS = 300

# Every refusal lands here with its reason, so the run can print a breakdown
# instead of a bare zero.
REASONS = {}


def _note(source, reason):
    REASONS.setdefault(source, {})
    REASONS[source][reason] = REASONS[source].get(reason, 0) + 1


def _fetch(url, headers=None, timeout=45, attempts=2):
    last = None
    for i in range(attempts):
        try:
            req = Request(url, headers=headers or {"User-Agent": BROWSER_UA})
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace"), None
        except HTTPError as exc:
            return None, f"http {exc.code}"
        except URLError as exc:
            last = f"url error: {exc.reason}"
        except Exception as exc:                            # noqa: BLE001
            last = f"{type(exc).__name__}"
        time.sleep(1.0 * (i + 1))
    return None, last or "failed"


def _clean_number(text):
    s = str(text).replace("$", "").replace(",", "").strip()
    if s in ("", "--", "N/A"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def from_stooq(symbol, start, end):
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    text, err = _fetch(url)
    if err:
        _note("stooq", err)
        return None
    head = text[:120].lower()
    if "exceeded" in head or "limit" in head:
        _note("stooq", "rate limited")
        return None
    lines = text.strip().split("\n")
    if not lines or not lines[0].lower().startswith("date"):
        _note("stooq", f"unexpected body: {text[:60]!r}")
        return None
    days, closes, vols = [], [], []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            d = date.fromisoformat(parts[0])
        except ValueError:
            continue
        c, v = _clean_number(parts[4]), _clean_number(parts[5])
        if c is None or c <= 0:
            continue
        days.append(d)
        closes.append(c)
        vols.append(v or 0.0)
    if len(days) < MIN_SESSIONS:
        _note("stooq", f"only {len(days)} sessions")
        return None
    return days, closes, vols


def from_nasdaq(symbol, start, end, classes=("stocks", "etf")):
    """Nasdaq keys this endpoint by asset class and answers a wrong class with
    an empty table rather than an error, so the classes are tried in turn. The
    premise-check study lost a whole run to exactly this."""
    rows = None
    for cls in classes:
        url = (f"https://api.nasdaq.com/api/quote/{symbol}/historical"
               f"?assetclass={cls}&fromdate={start.isoformat()}"
               f"&todate={end.isoformat()}&limit=9999")
        text, err = _fetch(url, headers={"User-Agent": BROWSER_UA,
                                         "Accept": "application/json"})
        if err:
            _note("nasdaq", err)
            return None
        try:
            data = json.loads(text)
        except ValueError:
            _note("nasdaq", "not json")
            return None
        rows = (((data or {}).get("data") or {}).get("tradesTable") or {}).get("rows")
        if rows:
            break
    if not rows:
        _note("nasdaq", "empty table (not a listed common stock or ETF)")
        return None
    out = []
    for r in rows:
        c = _clean_number(r.get("close"))
        if c is None or c <= 0:
            continue
        raw = str(r.get("date", "")).strip()
        try:
            m, d, y = raw.split("/")
            when = date(int(y), int(m), int(d))
        except (ValueError, TypeError):
            continue
        out.append((when, c, _clean_number(r.get("volume")) or 0.0))
    out.sort()
    if len(out) < MIN_SESSIONS:
        _note("nasdaq", f"only {len(out)} sessions")
        return None
    return [o[0] for o in out], [o[1] for o in out], [o[2] for o in out]


def from_yahoo(symbol, start, end):
    p1 = int(time.mktime(start.timetuple()))
    p2 = int(time.mktime(end.timetuple()))
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d")
    text, err = _fetch(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
    if err:
        _note("yahoo", err)
        return None
    try:
        data = json.loads(text)
    except ValueError:
        _note("yahoo", "not json")
        return None
    res = ((data.get("chart") or {}).get("result") or [None])[0]
    if not res:
        _note("yahoo", "no result")
        return None
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes_in = quote.get("close") or []
    vols_in = quote.get("volume") or []
    days, closes, vols = [], [], []
    for i, ts in enumerate(stamps):
        c = closes_in[i] if i < len(closes_in) else None
        if c is None or c <= 0:
            continue
        days.append(date.fromtimestamp(ts))
        closes.append(float(c))
        vols.append(float(vols_in[i] or 0.0) if i < len(vols_in) else 0.0)
    if len(days) < MIN_SESSIONS:
        _note("yahoo", f"only {len(days)} sessions")
        return None
    return days, closes, vols



_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def latest_close(symbol, classes=("stocks", "etf")):
    """The day's official close, from the quote Nasdaq publishes at the bell.

    fetch_prices reads Nasdaq's historical table, which does not carry a
    session's row until hours after it ends. On 21 Sep 2026 it still stopped
    at Friday at 8 p.m. Eastern, so the evening run ranked 1,185 companies on
    Friday's prices and stamped them Monday. The quote endpoint had Monday's
    close ($19.41 for YELP) the whole time.

    Returns (date, close, volume), or None. It answers only when Nasdaq says
    the market is Closed, so an after-hours print can never be taken for the
    close. The date comes from Nasdaq's own "last trade" stamp, never from the
    runner's clock.
    """
    data = None
    for cls in classes:
        url = f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass={cls}"
        text, err = _fetch(url, headers={"User-Agent": BROWSER_UA,
                                         "Accept": "application/json"})
        if err:
            _note("nasdaq-quote", err)
            return None
        try:
            data = (json.loads(text) or {}).get("data")
        except ValueError:
            _note("nasdaq-quote", "not json")
            return None
        if data:
            break
    if not data:
        _note("nasdaq-quote", "no quote")
        return None
    if str(data.get("marketStatus", "")).strip().lower() != "closed":
        _note("nasdaq-quote", "market not closed yet")
        return None
    primary = data.get("primaryData") or {}
    close = _clean_number(primary.get("lastSalePrice"))
    stamp = re.search(r"([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})",
                      str(primary.get("lastTradeTimestamp", "")))
    if close is None or close <= 0 or not stamp or stamp.group(1) not in _MONTHS:
        _note("nasdaq-quote", "unreadable quote")
        return None
    when = date(int(stamp.group(3)), _MONTHS[stamp.group(1)], int(stamp.group(2)))
    return when, close, _clean_number(primary.get("volume")) or 0.0

# Measured from GitHub Actions on 19 Sep 2026: Nasdaq served 8 of 10 symbols,
# Stooq returned an HTML block page rather than CSV, and Yahoo answered 429 on
# the first request. So Nasdaq is the source and the other two are kept only as
# a fallback that has to be asked for -- two dead requests per symbol across
# four thousand symbols is an hour of nothing.
_ALL = {"nasdaq": from_nasdaq, "stooq": from_stooq, "yahoo": from_yahoo}
_WANTED = [s.strip() for s in os.environ.get("PRICE_SOURCES", "nasdaq").split(",") if s.strip()]
SOURCES = [(n, _ALL[n]) for n in _WANTED if n in _ALL] or [("nasdaq", from_nasdaq)]

# Which source answered for each symbol, so the run can report the mix rather
# than pretending one number came from one place.
SERVED = {}
COVERAGE = {"earliest": None, "latest": None, "shortest": None, "longest": None}


def fetch_prices(symbol, start, end, order=None):
    for name, fn in (order or SOURCES):
        try:
            got = fn(symbol, start, end)
        except Exception as exc:                            # noqa: BLE001
            _note(name, f"raised {type(exc).__name__}")
            got = None
        if got:
            SERVED[name] = SERVED.get(name, 0) + 1
            days = got[0]
            c = COVERAGE
            c["earliest"] = days[0] if not c["earliest"] else min(c["earliest"], days[0])
            c["latest"] = days[-1] if not c["latest"] else max(c["latest"], days[-1])
            n = len(days)
            c["shortest"] = n if c["shortest"] is None else min(c["shortest"], n)
            c["longest"] = n if c["longest"] is None else max(c["longest"], n)
            return got
    return None


def report():
    lines = ["Price sources:"]
    if SERVED:
        for name, n in sorted(SERVED.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name}: served {n:,} symbols")
    else:
        lines.append("  nothing served a single symbol")
    c = COVERAGE
    if c["earliest"]:
        lines.append(f"  coverage: {c['earliest']} to {c['latest']}, "
                     f"{c['shortest']:,}-{c['longest']:,} sessions per symbol")
    for name, reasons in REASONS.items():
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
        lines.append(f"  {name} refusals: " + ", ".join(f"{r} x{n:,}" for r, n in top))
    return "\n".join(lines)


if __name__ == "__main__":
    syms = sys.argv[1:] or ["AAPL", "MSFT", "JNJ", "IWM", "PLAB", "F"]
    start, end = date(2015, 1, 1), date.today()
    print(f"Probing {len(SOURCES)} sources on {len(syms)} symbols, "
          f"{start} to {end}.\n")
    for name, fn in SOURCES:
        print(f"--- {name}")
        for s in syms:
            t0 = time.time()
            try:
                got = fn(s, start, end)
            except Exception as exc:                        # noqa: BLE001
                print(f"  {s:6s} RAISED {type(exc).__name__}: {exc}")
                continue
            dt = time.time() - t0
            if not got:
                last = list(REASONS.get(name, {}))[-1:] or ["no reason recorded"]
                print(f"  {s:6s} none ({last[0]})  {dt:.1f}s")
            else:
                days, closes, _v = got
                print(f"  {s:6s} {len(days):,} sessions, {days[0]} to {days[-1]}, "
                      f"last close {closes[-1]:.2f}  {dt:.1f}s")
            time.sleep(0.3)
        print()
    print(report())
