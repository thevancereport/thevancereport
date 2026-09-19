"""
The Vance Report -- daily cash-cushion screen.

Data sources, all free and keyless:

  SEC EDGAR XBRL frames   cash and shares outstanding for every US filer.
                          One request each returns the whole market, so the
                          fundamentals cost two calls no matter how many
                          companies are screened.
  Nasdaq screener         last price, market cap and volume for every listed
                          US stock, in a single request.
  Nasdaq historical       daily closes, fetched ONLY for names retained at or
                          above the snapshot floor, keeping this to ~150 calls.

This replaces an implementation built on Financial Modeling Prep's v3 API,
which FMP retired on 2025-08-31 (HTTP 403 "Legacy Endpoint") and whose
replacement paywalls the screener endpoint (HTTP 402) on the free plan.

Gate order matters. Cash cushion is computed first because it is free for the
entire market; price history is only fetched for the survivors. The previous
implementation did it the other way round and would have needed one call per
candidate just to begin.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

RESEARCH_FILE = "research.json"
SNAPSHOT_FILE = "screen_snapshot.json"

# How far back the price test looks, in trading sessions, and how bad the move
# over that window has to be.
#
# The window was three days by intent. It became five on 17 Sep 2026 because
# the data source of the day (FMP's stock-price-change endpoint) offered 1D,
# 5D, 1M and 3M and nothing in between, so 5D was the closest thing on the
# shelf. Nothing was decided; the API chose. Since the move to Nasdaq's daily
# closes the window is ours again, so it goes back to three.
#
# Both windows are computed and stored on every row from the same request.
# Only DROP_WINDOW_DAYS decides the verdict; the other is there so the cost of
# this choice is visible in the data rather than argued about.
DROP_WINDOW_DAYS = 3
DROP_WINDOWS = (3, 5)
DROP_THRESHOLD = -15.0      # move over the window must be at least this bad
CASH_GATE = 30.0            # cash per share as % of price needed to pass
# What the snapshot keeps, as opposed to what passes. Cutting the file at the
# cash gate meant every name in it had already cleared that test, so the site
# could only ever show rejections on the price move -- and a reader turning
# the cash dial down saw nothing change. Keeping the near misses costs a few
# kilobytes and makes the cash test visible as a test.
SNAPSHOT_FLOOR = 15.0
# The shape of the chart, read over this many trading sessions. TREND_SMOOTH
# sessions are averaged at each end so one loud day does not decide it.
#
# This replaces a "momentum" field that compared one run against the previous
# run. When the job ran twice in a day that was a half-hour move, which is why
# every name on the site read STABILIZING. The twenty days of closes needed to
# answer the question properly were already being fetched and thrown away.
TREND_WINDOW = 10
TREND_SMOOTH = 3
MIN_MARKET_CAP = 50_000_000
MIN_VOLUME = 50_000
MIN_DOLLAR_VOLUME = 250_000  # price x volume; a liquidity floor, not a price floor
MAX_PER_SECTOR = 3           # cap on any one sector in the published list
MIN_RUNWAY_YEARS = 2.0       # net cash divided by annual burn; cash-generative passes
MAX_DILUTION_PCT = 25.0      # year-over-year growth in share count
INSIDER_LOOKBACK_DAYS = 180  # window for open-market insider purchases
MAX_INSIDER_FILINGS = 12     # Form 4s inspected per company
MAX_HISTORY_CALLS = 150      # ceiling on per-ticker history requests per run
MIN_FRAME_ROWS = 2000       # keep merging EDGAR quarters until this many filers

# The SEC asks that automated clients identify themselves. Requests without a
# real contact address get throttled or blocked.
SEC_UA = "TheVanceReport/1.0 (contact: ardenkvance@gmail.com)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FRAME = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
NASDAQ_SCREENER = (
    "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=0&offset=0&download=true"
)
NASDAQ_HISTORY = (
    "https://api.nasdaq.com/api/quote/{symbol}/historical"
    "?assetclass=stocks&fromdate={start}&todate={end}&limit=30"
)


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def fetch_json(url, user_agent, timeout=60, attempts=3):
    """GET and parse JSON, retrying transient failures. Returns None on failure."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            if attempt == attempts:
                print(f"  request failed after {attempts} attempts: {url[:90]} -- {exc}")
                return None
            time.sleep(1.5 * attempt)
    return None


def money(text):
    """'$1,234.50' or '1234.50' -> float. Returns None if unparseable."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = str(text).replace("$", "").replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned in ("--", "N/A", "NA"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def current_quarter_frames():
    """
    Candidate EDGAR frame periods, newest first.

    Frames are published per calendar quarter and a quarter only becomes
    populated once filings arrive, so the newest one is often empty for weeks.
    Walking backwards means the screen still runs early in a quarter.
    """
    now = datetime.now(timezone.utc)
    year, quarter = now.year, (now.month - 1) // 3 + 1
    periods = []
    for _ in range(4):
        periods.append(f"CY{year}Q{quarter}I")
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
    return periods


def annual_frames():
    """
    Calendar-year periods for cash-flow concepts, newest first.

    Duration concepts must be requested annually. The quarterly frame for
    operating cash flow carried 322 filers against 5,752 for the year, because
    most companies report cumulative year-to-date figures rather than discrete
    quarters.
    """
    year = datetime.now(timezone.utc).year
    return [f"CY{year - 1}", f"CY{year - 2}"]


def year_ago_quarter_frames():
    """The same quarters as current_quarter_frames(), one year earlier."""
    return [f"CY{int(p[2:6]) - 1}{p[6:]}" for p in current_quarter_frames()]


# --------------------------------------------------------------------------
# Source fetchers
# --------------------------------------------------------------------------

def fetch_ticker_to_cik():
    data = fetch_json(SEC_TICKERS, SEC_UA)
    if not isinstance(data, dict):
        print("SEC ticker map unavailable.")
        return {}
    mapping = {}
    for row in data.values():
        if isinstance(row, dict) and row.get("ticker") and row.get("cik_str") is not None:
            mapping[str(row["ticker"]).upper()] = int(row["cik_str"])
    print(f"SEC ticker map: {len(mapping):,} tickers.")
    return mapping


def fetch_frame(taxonomy, tag, unit):
    """
    Return {cik: value} for one XBRL concept, merged across recent quarters.

    A quarter only fills up as companies file, so the newest one is nearly
    empty for weeks after it opens -- CY2026Q3I carried 5 filers the day this
    was written, against 4,184 in CY2026Q2I. Taking the newest quarter that
    returns ANY rows therefore screens almost nothing.

    Quarters are merged newest-first instead: a company that has already filed
    contributes its latest figure, everyone else falls back to the quarter
    before. Stops once enough filers are covered, so this is normally two
    requests.
    """
    return fetch_frame_periods(taxonomy, tag, unit, current_quarter_frames())


def fetch_frame_periods(taxonomy, tag, unit, periods):
    """Merge one XBRL concept across an explicit list of frame periods."""
    merged = {}
    for period in periods:
        url = SEC_FRAME.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period)
        data = fetch_json(url, SEC_UA)
        rows = data.get("data") if isinstance(data, dict) else None
        added = 0
        for row in rows or []:
            cik, val = row.get("cik"), row.get("val")
            if cik is None or not isinstance(val, (int, float)):
                continue
            cik = int(cik)
            if cik not in merged:          # newest quarter wins
                merged[cik] = float(val)
                added += 1
        print(f"EDGAR {tag} [{period}]: +{added:,} filers (running total {len(merged):,}).")
        if len(merged) >= MIN_FRAME_ROWS:
            break
    if not merged:
        print(f"EDGAR {tag}: no data in any recent quarter.")
    return merged


def fetch_market_rows():
    data = fetch_json(NASDAQ_SCREENER, BROWSER_UA)
    rows = None
    if isinstance(data, dict):
        rows = (data.get("data") or {}).get("rows")
        if rows is None:
            rows = ((data.get("data") or {}).get("table") or {}).get("rows")
    if not rows:
        print("Nasdaq screener returned no rows.")
        return []
    print(f"Nasdaq screener: {len(rows):,} listed symbols.")
    return rows


def fetch_price_read(symbol):
    """Everything the daily closes can say, from one request.

    Returns {"drops": {sessions: pct}, "trend": {...}} -- the move over each
    window in DROP_WINDOWS, and the shape of the chart. Windows are counted in
    trading sessions, not calendar days, so a holiday week does not quietly
    shorten the lookback. None if the history is unusable.
    """
    now = time.time()
    url = NASDAQ_HISTORY.format(
        symbol=quote(symbol),
        start=time.strftime("%Y-%m-%d", time.gmtime(now - 20 * 86400)),
        end=time.strftime("%Y-%m-%d", time.gmtime(now)),
    )
    data = fetch_json(url, BROWSER_UA, timeout=30, attempts=2)
    rows = (((data or {}).get("data") or {}).get("tradesTable") or {}).get("rows")
    if not rows:
        return None

    closes = [money(r.get("close")) for r in rows if money(r.get("close"))]
    latest = closes[0] if closes else None   # Nasdaq returns newest first
    if not latest:
        return None

    drops = {}
    for sessions in DROP_WINDOWS:
        if len(closes) <= sessions:
            continue
        prior = closes[sessions]
        if not prior:
            continue
        drops[sessions] = round((latest - prior) / prior * 100, 1)

    trend = read_trend(closes)
    if not drops and not trend:
        return None
    return {"drops": drops, "trend": trend}


def fetch_insider_buys(cik):
    """
    Count open-market insider purchases in the last INSIDER_LOOKBACK_DAYS.

    Only transaction code "P" counts. Code "A" is a grant and "M" an option
    exercise -- both are compensation arriving on a schedule, not somebody
    choosing to buy their own beaten-down stock. Screens that count every
    acquisition report insider buying that never happened.

    Deliberately not a gate: insiders not buying says very little, so this is
    reported rather than filtered on.
    """
    padded = str(int(cik)).zfill(10)
    data = fetch_json(f"https://data.sec.gov/submissions/CIK{padded}.json", SEC_UA, timeout=30, attempts=2)
    recent = ((data or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    if not forms:
        return {"buys": 0, "shares": 0, "value": 0.0}

    cutoff = datetime.now(timezone.utc).date().toordinal() - INSIDER_LOOKBACK_DAYS
    buys = shares_total = 0
    value_total = 0.0
    inspected = 0

    for i, form in enumerate(forms):
        if form != "4" or inspected >= MAX_INSIDER_FILINGS:
            continue
        try:
            filed = recent["filingDate"][i]
            if datetime.strptime(filed, "%Y-%m-%d").date().toordinal() < cutoff:
                continue
            accession = recent["accessionNumber"][i].replace("-", "")
            document = recent["primaryDocument"][i]
        except Exception:
            continue

        # primaryDocument points at the XSL-rendered HTML; the machine-readable
        # XML sits beside it, without that directory prefix.
        document = document.split("/")[-1]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
        inspected += 1
        try:
            with urlopen(Request(url, headers={"User-Agent": SEC_UA}), timeout=30) as resp:
                xml = resp.read().decode("utf-8", "replace")
        except Exception:
            continue

        codes = re.findall(r"<transactionCode>([^<]+)</transactionCode>", xml)
        counts = re.findall(r"<transactionShares>\s*<value>([^<]+)</value>", xml)
        prices = re.findall(r"<transactionPricePerShare>\s*<value>([^<]+)</value>", xml)
        for j, code in enumerate(codes):
            if code.strip().upper() != "P":
                continue
            buys += 1
            try:
                n = float(counts[j])
                shares_total += int(n)
                value_total += n * float(prices[j]) if j < len(prices) else 0.0
            except Exception:
                pass
        time.sleep(0.12)

    return {"buys": buys, "shares": shares_total, "value": round(value_total)}


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------

def read_trend(closes):
    """Up, down or sideways, from the shape of the last TREND_WINDOW sessions.

    The middle close of the last TREND_SMOOTH sessions against the middle close
    of the TREND_SMOOTH sessions at the far end. Middles rather than averages,
    because an average lets one loud day carry the verdict: a stock flat for a
    month that jumps fifteen percent this morning is a stock that jumped this
    morning, not a stock in an uptrend.

    The move between the two ends is called a trend only when it is larger than
    what this stock moves anyway over the same span. A fixed percentage would
    label almost every name here a trend, because many of them move five
    percent on a quiet day. The yardstick is the median absolute daily move,
    scaled by the square root of the distance between the two ends.

    This is a description of the chart. It is not a forecast, and nothing about
    it is an opinion on where the price goes next.
    """
    if len(closes) < TREND_WINDOW + 1:
        return None

    recent = sorted(closes[:TREND_SMOOTH])
    older = sorted(closes[TREND_WINDOW - TREND_SMOOTH:TREND_WINDOW])
    if not all(recent) or not all(older):
        return None

    head = recent[len(recent) // 2]
    tail = older[len(older) // 2]
    if tail <= 0:
        return None
    move_pct = (head - tail) / tail * 100

    steps = []
    for i in range(TREND_WINDOW):
        prior = closes[i + 1]
        if prior:
            steps.append(abs(closes[i] - prior) / prior * 100)
    if not steps:
        return None
    steps.sort()
    typical = steps[len(steps) // 2]
    span = TREND_WINDOW - TREND_SMOOTH      # sessions between the two centres
    band = max(typical * (span ** 0.5), 1.0)  # a floor, for names that barely move

    if move_pct > band:
        label = "UP"
    elif move_pct < -band:
        label = "DOWN"
    else:
        label = "SIDEWAYS"

    return {
        "trend": label,
        "trend_pct": round(move_pct, 1),
        "trend_band_pct": round(band, 1),
        "trend_window_days": TREND_WINDOW,
    }


COMMON_STOCK = ("common stock", "ordinary share", "ordinary shares")
NOT_COMMON = ("warrant", "unit", "right", "preferred", "note", "notes",
              "depositary", "debenture", "trust")


def is_common_stock(name):
    """
    True only for ordinary equity.

    Nasdaq's feed mixes 304 warrants, 293 units, 263 preferreds, 149 notes and
    456 depositary receipts in with the common stock. Those derivatives share
    their issuer's CIK, so they inherit the issuer's cash -- Opendoor's Series A
    and Series Z warrants screened at 1,318% and 1,203% "cash cushion" on
    7-cent prices, because Opendoor's balance sheet was being divided by a
    warrant price. That cash belongs to shareholders, not warrant holders.
    """
    lowered = (name or "").lower()
    if not any(kind in lowered for kind in COMMON_STOCK):
        return False
    return not any(kind in lowered for kind in NOT_COMMON)


def build_cash_candidates(rows, ticker_cik, cash_by_cik, shares_by_cik, liab_by_cik,
                          burn_by_cik, shares_prior_by_cik):
    """Names at or above the snapshot floor. Costs no extra requests.

    The floor is deliberately below the cash gate so that the near misses
    survive into the published file and the cash test stays visible as a
    test rather than as a silent precondition.
    """
    candidates = []
    for row in rows:
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol or "." in symbol or "^" in symbol:
            continue
        if not is_common_stock(row.get("name")):
            continue

        price = money(row.get("lastsale"))
        market_cap = money(row.get("marketCap"))
        volume = money(row.get("volume"))
        if not price or price <= 0:
            continue
        if not market_cap or market_cap < MIN_MARKET_CAP:
            continue
        if volume is not None and volume < MIN_VOLUME:
            continue

        # A liquidity floor rather than a price floor. Screening out cheap stocks
        # would cut exactly the dislocated names this service exists to find; what
        # actually needs excluding is the untradeable. A 60-cent stock turning over
        # a million shares stays, a 60-cent stock turning over four thousand does not.
        if volume is not None and price * volume < MIN_DOLLAR_VOLUME:
            continue

        cik = ticker_cik.get(symbol)
        if cik is None:
            continue
        cash, shares = cash_by_cik.get(cik), shares_by_cik.get(cik)
        liabilities = liab_by_cik.get(cik)
        if not cash or not shares or shares <= 0:
            continue

        # Gross cash flatters leveraged companies badly. Cable One screened at a
        # 168% "cash cushion" while carrying billions in debt. Total liabilities
        # is used rather than the debt-specific tags because those are filed by
        # only about a third of companies, and treating the rest as debt-free
        # would reproduce the very error this removes.
        if liabilities is None:
            continue

        net_cash = cash - liabilities
        if net_cash <= 0:
            continue

        cps = net_cash / shares
        cushion = cps / price * 100
        if cushion < SNAPSHOT_FLOOR:
            continue

        # Runway, not just cash. Two companies with the same cash per share are
        # not comparable if one burns four times as fast: cash is a stock, burn
        # is a flow, and only the flow decides whether a company reaches its
        # catalyst or has to raise money first. Positive operating cash flow
        # means no runway constraint at all.
        operating_cf = burn_by_cik.get(cik)
        runway_years = None
        if operating_cf is not None:
            if operating_cf >= 0:
                runway_years = float("inf")
            else:
                annual_burn = abs(operating_cf)
                runway_years = (net_cash / annual_burn) if annual_burn else None
        if runway_years is not None and runway_years < MIN_RUNWAY_YEARS:
            continue

        # Dilution is how this thesis usually dies: a company trading below its
        # cash issues equity, and the cash per share just screened on is cut.
        # A share count up sharply year over year is the company telling you
        # what it intends to do next.
        prior_shares = shares_prior_by_cik.get(cik)
        dilution_pct = None
        if prior_shares and prior_shares > 0:
            dilution_pct = (shares - prior_shares) / prior_shares * 100
            if dilution_pct > MAX_DILUTION_PCT:
                continue

        candidates.append({
            "symbol": symbol,
            "name": (row.get("name") or symbol).replace(" Common Stock", "").strip(),
            "price": round(price, 2),
            "market_cap": round(market_cap),
            "cps": round(cps, 2),
            "cash_cushion_pct": round(cushion, 1),
            "gross_cash_per_share": round(cash / shares, 2),
            "liabilities_per_share": round(liabilities / shares, 2),
            "runway_years": (None if runway_years is None
                             else ("cash generative" if runway_years == float("inf")
                                   else round(runway_years, 1))),
            "dilution_pct": None if dilution_pct is None else round(dilution_pct, 1),
            "cik": cik,
            "sector": row.get("sector") or None,
        })

    candidates.sort(key=lambda c: c["cash_cushion_pct"], reverse=True)
    return candidates


def build_record(candidate, price_read, previous):
    price = candidate["price"]
    previous_price = previous.get("price") if previous else None

    if previous_price and previous_price > 0:
        dollar_change = price - previous_price
        return_pct = (dollar_change / previous_price) * 100
    else:
        dollar_change = return_pct = None

    # Two tests, and the file now carries names that fail either one, so the
    # verdict has to check both rather than assume the cash test was already
    # settled by the retention cut above.
    price_read = price_read or {}
    drops = price_read.get("drops") or {}
    trend = price_read.get("trend") or {}
    drop_pct = drops.get(DROP_WINDOW_DAYS)
    cushion = candidate["cash_cushion_pct"]
    if drop_pct is None:
        gate = "UNKNOWN"
    elif cushion >= CASH_GATE and drop_pct <= DROP_THRESHOLD:
        gate = "PASS"
    else:
        gate = "FAIL"

    record = {
        "name": candidate["name"],
        "exchange": None,
        "price": price,
        "previous_price": round(previous_price, 2) if previous_price else None,
        "dollar_change": round(dollar_change, 2) if dollar_change is not None else None,
        "return_pct": round(return_pct, 2) if return_pct is not None else None,
        "market_cap": candidate["market_cap"],
        # drop_pct is the figure the verdict was made on. The per-window
        # figures sit beside it so a reader, and the next person to argue
        # about the window, can see what the other one would have said.
        "drop_pct": drop_pct,
        "drop_window_days": DROP_WINDOW_DAYS,
        "three_day_drop_pct": drops.get(3),
        # Kept under its old name as well: the pages, the stock profile and
        # the video all read this field, and a rename is not worth a day of
        # blank columns.
        "five_day_drop_pct": drops.get(5),
        # The shape of the chart over TREND_WINDOW sessions: UP, DOWN or
        # SIDEWAYS, with the move and the band it was judged against, so a
        # reader can check the label rather than take it on trust.
        "trend": trend.get("trend"),
        "trend_pct": trend.get("trend_pct"),
        "trend_band_pct": trend.get("trend_band_pct"),
        "trend_window_days": trend.get("trend_window_days"),
        "cps": candidate["cps"],
        "cash_cushion_pct": candidate["cash_cushion_pct"],
        "sector": candidate.get("sector"),
        "runway_years": candidate.get("runway_years"),
        "dilution_pct": candidate.get("dilution_pct"),
        "gross_cash_per_share": candidate.get("gross_cash_per_share"),
        "liabilities_per_share": candidate.get("liabilities_per_share"),
        "gate": gate,
        "target_zone": f"${round(price * 0.95, 2)} - ${round(price * 1.02, 2)}",
    }

    # Editorial fields are carried forward from the previous snapshot so a
    # hand-written thesis survives; only generated for names never seen before.
    if previous and previous.get("thesis"):
        record["catalyst"] = previous.get("catalyst", "Automated candidate discovery via the Vance Report cash-to-price screen.")
        record["thesis"] = previous["thesis"]
        record["risks"] = previous.get("risks", ["Pending fundamental desk review."])
    else:
        record["catalyst"] = "Automated candidate discovery via the Vance Report cash-to-price screen."
        record["thesis"] = (
            f"{record['name']} holds ${candidate['cps']:.2f} of NET cash per share -- "
            f"cash after total liabilities -- against a ${price:.2f} price, a "
            f"{candidate['cash_cushion_pct']:.1f}% net cash cushion. Cash, liabilities and "
            "share counts are taken from the company's most recent SEC filing."
        )
        record["risks"] = ["Pending fundamental desk review."]

    return record


def strip_meta(data):
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def cap_by_sector(ranked, limit, per_sector):
    """
    Take the best `limit` names, allowing at most `per_sector` from any one sector.

    Cash-rich companies trading below net cash are overwhelmingly clinical-stage
    biotech: they raise large cash piles against small market caps, then halve on
    trial news. That is the anomaly this screen looks for, so the concentration is
    real rather than a bug -- but a published list that is 10-for-10 biotech every
    day is a sector bet wearing a screen's clothes. Raising the market-cap floor
    does not help; the names span 66m to 1.4bn and are biotech at every size.
    """
    taken, counts, overflow = [], {}, []
    for symbol, record in ranked:
        sector = record.get("sector") or "Unclassified"
        if counts.get(sector, 0) >= per_sector:
            overflow.append((symbol, record))
            continue
        counts[sector] = counts.get(sector, 0) + 1
        taken.append((symbol, record))
        if len(taken) == limit:
            return taken, counts

    # Backfill from the names held back, rather than publishing a short list
    # purely to honour the cap.
    for item in overflow:
        if len(taken) == limit:
            break
        taken.append(item)
    return taken, counts


def update_database():
    print("Running Vance Report screen (SEC EDGAR + Nasdaq)...")

    rows = fetch_market_rows()
    if not rows:
        print("No market data; leaving files unchanged.")
        return

    ticker_cik = fetch_ticker_to_cik()
    cash_by_cik = fetch_frame("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD")
    shares_by_cik = fetch_frame("dei", "EntityCommonStockSharesOutstanding", "shares")
    liab_by_cik = fetch_frame("us-gaap", "Liabilities", "USD")
    burn_by_cik = fetch_frame_periods(
        "us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD", annual_frames()
    )
    shares_prior_by_cik = fetch_frame_periods(
        "dei", "EntityCommonStockSharesOutstanding", "shares", year_ago_quarter_frames()
    )

    if not ticker_cik or not cash_by_cik or not shares_by_cik or not liab_by_cik:
        print("SEC fundamentals unavailable; leaving files unchanged.")
        return

    candidates = build_cash_candidates(
        rows, ticker_cik, cash_by_cik, shares_by_cik, liab_by_cik,
        burn_by_cik, shares_prior_by_cik
    )
    clearing = sum(1 for c in candidates if c["cash_cushion_pct"] >= CASH_GATE)
    print(f"{len(candidates)} names retained at or above the {SNAPSHOT_FLOOR:.0f}% "
          f"snapshot floor; {clearing} of them clear the {CASH_GATE:.0f}% NET cash gate.")
    if not candidates:
        print("Nothing cleared the snapshot floor; leaving files unchanged.")
        return

    checked = candidates[:MAX_HISTORY_CALLS]
    if len(candidates) > MAX_HISTORY_CALLS:
        print(f"Checking price history for the top {MAX_HISTORY_CALLS} by cushion.")

    generated_at = datetime.now(timezone.utc).isoformat()
    previous_meta = load_json(SNAPSHOT_FILE).get("_meta", {})
    previous_snapshot = strip_meta(load_json(SNAPSHOT_FILE))

    # The baseline for "1-day change" is the last stored run. When the job
    # runs more than once in a day, rolling the baseline forward every time
    # turns a day's move into a half-hour's move while the column still says
    # one day. On a rerun the earlier baseline is carried forward instead, so
    # the comparison stays a session against a session.
    prior_generated_at = previous_meta.get("generated_at")
    same_day = bool(prior_generated_at) and prior_generated_at[:10] == generated_at[:10]
    if same_day:
        baseline = {
            symbol: {"price": record.get("previous_price")}
            for symbol, record in previous_snapshot.items()
        }
        baseline_generated_at = previous_meta.get("previous_generated_at")
        print("Rerun on the same date; carrying the previous session's baseline forward.")
    else:
        baseline = previous_snapshot
        baseline_generated_at = prior_generated_at

    evaluated_ciks = {c["symbol"]: c.get("cik") for c in checked}
    evaluated = {}
    for i, candidate in enumerate(checked, 1):
        price_read = fetch_price_read(candidate["symbol"])
        evaluated[candidate["symbol"]] = build_record(
            candidate, price_read, baseline.get(candidate["symbol"])
        )
        if i % 25 == 0:
            print(f"  ...{i}/{len(checked)} price histories fetched")
        time.sleep(0.2)   # be a considerate client

    if not evaluated:
        print("Nothing usable this run; leaving files unchanged.")
        return

    # What the other window would have done, printed every run. The window is
    # a judgement call and this is the only place the cost of it is cheap to
    # see: same data, same threshold, different lookback.
    for sessions in DROP_WINDOWS:
        field = {3: "three_day_drop_pct", 5: "five_day_drop_pct"}[sessions]
        measured = [r for r in evaluated.values() if r.get(field) is not None]
        would_pass = sum(
            1 for r in measured
            if r[field] <= DROP_THRESHOLD and r["cash_cushion_pct"] >= CASH_GATE
        )
        marker = "  <- in force" if sessions == DROP_WINDOW_DAYS else ""
        print(f"  {sessions}-session window at {DROP_THRESHOLD:.1f}%: "
              f"{would_pass} of {len(measured)} measured would clear both tests{marker}")

    # The thresholds travel with the data. The pages used to have 30.0 and
    # -15.0 typed into them in half a dozen places, so changing a number here
    # left the site describing a screen that no longer existed.
    criteria = {
        "cash_gate": CASH_GATE,
        "drop_threshold": DROP_THRESHOLD,
        "drop_window_days": DROP_WINDOW_DAYS,
        "trend_window_days": TREND_WINDOW,
        "snapshot_floor": SNAPSHOT_FLOOR,
        "min_runway_years": MIN_RUNWAY_YEARS,
        "max_dilution_pct": MAX_DILUTION_PCT,
        "min_market_cap": MIN_MARKET_CAP,
        "min_dollar_volume": MIN_DOLLAR_VOLUME,
        "max_per_sector": MAX_PER_SECTOR,
    }

    snapshot_out = dict(evaluated)
    snapshot_out["_meta"] = {
        "generated_at": generated_at,
        "previous_generated_at": baseline_generated_at,
        "sources": ["SEC EDGAR XBRL frames", "Nasdaq screener"],
        "criteria": criteria,
    }
    with open(SNAPSHOT_FILE, "w") as fh:
        json.dump(snapshot_out, fh, indent=2)

    passing = {t: r for t, r in evaluated.items() if r["gate"] == "PASS"}
    ranked = sorted(passing.items(), key=lambda kv: kv[1]["cash_cushion_pct"], reverse=True)
    published, sector_counts = cap_by_sector(ranked, 10, MAX_PER_SECTOR)
    if sector_counts:
        print("Sector mix published: " + ", ".join(
            f"{name} {count}" for name, count in sorted(sector_counts.items())
        ))
    # Insider purchases are looked up only for the names being published --
    # roughly ten companies rather than the whole candidate set, which keeps
    # this to a few dozen requests.
    for symbol, record in published:
        cik = evaluated_ciks.get(symbol)
        if not cik:
            continue
        insider = fetch_insider_buys(cik)
        record["insider_buys"] = insider["buys"]
        record["insider_shares"] = insider["shares"]
        record["insider_value"] = insider["value"]
        if insider["buys"]:
            print(f"  {symbol}: {insider['buys']} open-market insider purchase(s), "
                  f"${insider['value']:,} total")

    research_out = dict(published)            # the site shows a top ten
    research_out["_meta"] = {"generated_at": generated_at, "criteria": criteria}
    with open(RESEARCH_FILE, "w") as fh:
        json.dump(research_out, fh, indent=2)

    print(
        f"Done. {len(evaluated)} evaluated, {len(passing)} passing both gates, "
        f"{len(published)} published."
    )


if __name__ == "__main__":
    update_database()
