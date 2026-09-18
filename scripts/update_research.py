"""
The Vance Report -- daily cash-cushion screen.

Data sources, all free and keyless:

  SEC EDGAR XBRL frames   cash and shares outstanding for every US filer.
                          One request each returns the whole market, so the
                          fundamentals cost two calls no matter how many
                          companies are screened.
  Nasdaq screener         last price, market cap and volume for every listed
                          US stock, in a single request.
  Nasdaq historical       daily closes, fetched ONLY for names that already
                          clear the cash gate, which keeps this to ~100 calls.

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
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

RESEARCH_FILE = "research.json"
SNAPSHOT_FILE = "screen_snapshot.json"

DROP_THRESHOLD = -15.0      # 5-day move must be at least this bad to qualify
CASH_GATE = 30.0            # cash per share as % of price needed to pass
MOMENTUM_MOVE = 1.0         # day-over-day % move to call REBOUND / FALLING
MIN_MARKET_CAP = 50_000_000
MIN_VOLUME = 50_000
MAX_HISTORY_CALLS = 150     # ceiling on per-ticker history requests per run
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
    merged = {}
    for period in current_quarter_frames():
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


def fetch_five_day_drop(symbol):
    """Percentage move from the close 5 sessions ago to the latest close."""
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
    if len(closes) < 6:
        return None

    latest, prior = closes[0], closes[5]   # Nasdaq returns newest first
    if not prior:
        return None
    return round((latest - prior) / prior * 100, 1)


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------

def resolve_momentum(return_pct):
    if return_pct is None:
        return "STABILIZING"          # no baseline yet -- neutral, not a guess
    if return_pct >= MOMENTUM_MOVE:
        return "REBOUND"
    if return_pct <= -MOMENTUM_MOVE:
        return "FALLING"
    return "STABILIZING"


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


def build_cash_candidates(rows, ticker_cik, cash_by_cik, shares_by_cik, liab_by_cik):
    """Names clearing the net-cash gate. Costs no extra requests."""
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
        if cushion < CASH_GATE:
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
            "sector": row.get("sector") or None,
        })

    candidates.sort(key=lambda c: c["cash_cushion_pct"], reverse=True)
    return candidates


def build_record(candidate, drop_pct, previous):
    price = candidate["price"]
    previous_price = previous.get("price") if previous else None

    if previous_price and previous_price > 0:
        dollar_change = price - previous_price
        return_pct = (dollar_change / previous_price) * 100
    else:
        dollar_change = return_pct = None

    passes = drop_pct is not None and drop_pct <= DROP_THRESHOLD
    gate = "PASS" if passes else ("UNKNOWN" if drop_pct is None else "FAIL")

    record = {
        "name": candidate["name"],
        "exchange": None,
        "price": price,
        "previous_price": round(previous_price, 2) if previous_price else None,
        "dollar_change": round(dollar_change, 2) if dollar_change is not None else None,
        "return_pct": round(return_pct, 2) if return_pct is not None else None,
        "market_cap": candidate["market_cap"],
        "five_day_drop_pct": drop_pct,
        "trend": resolve_momentum(return_pct),
        "cps": candidate["cps"],
        "cash_cushion_pct": candidate["cash_cushion_pct"],
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

    if not ticker_cik or not cash_by_cik or not shares_by_cik or not liab_by_cik:
        print("SEC fundamentals unavailable; leaving files unchanged.")
        return

    candidates = build_cash_candidates(
        rows, ticker_cik, cash_by_cik, shares_by_cik, liab_by_cik
    )
    print(f"{len(candidates)} names clear the {CASH_GATE:.0f}% NET cash gate.")
    if not candidates:
        print("Nothing cleared the cash gate; leaving files unchanged.")
        return

    checked = candidates[:MAX_HISTORY_CALLS]
    if len(candidates) > MAX_HISTORY_CALLS:
        print(f"Checking price history for the top {MAX_HISTORY_CALLS} by cushion.")

    previous_snapshot = strip_meta(load_json(SNAPSHOT_FILE))
    evaluated = {}
    for i, candidate in enumerate(checked, 1):
        drop_pct = fetch_five_day_drop(candidate["symbol"])
        evaluated[candidate["symbol"]] = build_record(
            candidate, drop_pct, previous_snapshot.get(candidate["symbol"])
        )
        if i % 25 == 0:
            print(f"  ...{i}/{len(checked)} price histories fetched")
        time.sleep(0.2)   # be a considerate client

    if not evaluated:
        print("Nothing usable this run; leaving files unchanged.")
        return

    generated_at = datetime.now(timezone.utc).isoformat()
    previous_meta = load_json(SNAPSHOT_FILE).get("_meta", {})

    snapshot_out = dict(evaluated)
    snapshot_out["_meta"] = {
        "generated_at": generated_at,
        "previous_generated_at": previous_meta.get("generated_at"),
        "sources": ["SEC EDGAR XBRL frames", "Nasdaq screener"],
    }
    with open(SNAPSHOT_FILE, "w") as fh:
        json.dump(snapshot_out, fh, indent=2)

    passing = {t: r for t, r in evaluated.items() if r["gate"] == "PASS"}
    ranked = sorted(passing.items(), key=lambda kv: kv[1]["cash_cushion_pct"], reverse=True)
    research_out = dict(ranked[:10])          # the site shows a top ten
    research_out["_meta"] = {"generated_at": generated_at}
    with open(RESEARCH_FILE, "w") as fh:
        json.dump(research_out, fh, indent=2)

    print(
        f"Done. {len(evaluated)} evaluated, {len(passing)} passing both gates, "
        f"{len(ranked[:10])} published."
    )


if __name__ == "__main__":
    update_database()
