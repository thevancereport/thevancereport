import json
import os
from datetime import datetime, timezone
import requests

API_KEY = os.environ.get("FMP_API_KEY")
RESEARCH_FILE = "research.json"
SNAPSHOT_FILE = "screen_snapshot.json"

DROP_THRESHOLD = -15.0   # 5-day drop must be at least this bad to enter the screen
CASH_GATE = 30.0         # cash-per-share as % of price must clear this to pass
MOMENTUM_MOVE = 1.0      # day-over-day % move needed to call REBOUND/FALLING vs STABILIZING
SCREENER_LIMIT = 25      # candidates pulled per run; each one costs API calls below.
                         # Held low because the FMP free plan allows 250 calls/day:
                         # one run costs 1 + SCREENER_LIMIT + one balance sheet per
                         # name clearing the drop gate.


def fetch_screener_candidates():
    url = (
        "https://financialmodelingprep.com/stable/company-screener"
        "?marketCapMoreThan=50000000&volumeMoreThan=50000"
        f"&isActivelyTrading=true&country=US&limit={SCREENER_LIMIT}&apikey={API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=20)
    except Exception as e:
        print(f"Error reaching FMP Screener: {e}")
        return []

    try:
        data = resp.json()
    except Exception:
        print(f"FMP Screener returned non-JSON (HTTP {resp.status_code}): {resp.text[:400]}")
        return []

    # FMP reports quota, plan and auth problems as a JSON OBJECT, not a list.
    # Iterating that object yields its KEYS -- plain strings -- which is what
    # produced "AttributeError: 'str' object has no attribute 'get'" and killed
    # the run instead of reporting why. Surface the payload and bail cleanly.
    if not isinstance(data, list):
        print(
            f"FMP Screener returned {type(data).__name__}, not a list "
            f"(HTTP {resp.status_code}): {str(data)[:400]}"
        )
        return []

    print(f"FMP Screener returned {len(data)} candidates.")
    return data


def fetch_drop_pct(ticker):
    url = (
        "https://financialmodelingprep.com/stable/stock-price-change"
        f"?symbol={ticker}&apikey={API_KEY}"
    )
    try:
        data = requests.get(url, timeout=20).json()
        if isinstance(data, dict):
            data = [data]
        if data and isinstance(data, list) and isinstance(data[0], dict):
            row = data[0]
            for key in ("5D", "5d", "fiveDay"):
                if key in row:
                    return row[key]
            print(f"{ticker}: no 5-day field in price-change payload; keys={list(row.keys())[:14]}")
    except Exception as e:
        print(f"Error fetching price change for {ticker}: {e}")
    return 0.0


def fetch_cash_and_shares(ticker, price, market_cap):
    url = (
        "https://financialmodelingprep.com/stable/balance-sheet-statement"
        f"?symbol={ticker}&period=quarter&limit=1&apikey={API_KEY}"
    )
    try:
        bs_data = requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"Error fetching balance sheet for {ticker}: {e}")
        return None, None

    # Plan restrictions and quota errors arrive as a JSON object, not a list.
    # Say so per ticker, otherwise a paywalled figure is indistinguishable from
    # a company that simply reports no cash.
    if isinstance(bs_data, dict):
        print(f"{ticker}: balance sheet unavailable -> {str(bs_data)[:220]}")
        return None, None

    if not bs_data or not isinstance(bs_data, list):
        print(f"{ticker}: unexpected balance sheet payload type {type(bs_data).__name__}")
        return None, None

    row = bs_data[0] if isinstance(bs_data[0], dict) else {}
    cash = None
    for key in ("cashAndCashEquivalents", "cashAndShortTermInvestments", "cashAndCashEquivalentsAtCarryingValue"):
        if key in row:
            cash = row[key]
            break
    if cash is None:
        print(f"{ticker}: no cash field in balance sheet; keys={list(row.keys())[:14]}")
        return None, None
    shares = market_cap / price if price > 0 else 1
    return cash, shares


def resolve_momentum(return_pct):
    if return_pct is None:
        return "STABILIZING"  # no baseline yet — neutral rather than a guess
    if return_pct >= MOMENTUM_MOVE:
        return "REBOUND"
    if return_pct <= -MOMENTUM_MOVE:
        return "FALLING"
    return "STABILIZING"


def build_record(item, previous):
    """
    Evaluates one ticker fully (drop gate + cash gate) and returns a record
    for screen_snapshot.json (pass or fail), or None if it doesn't even clear
    the 5-day-drop gate to be considered a candidate at all.

    `previous` is that ticker's record from the LAST screen_snapshot.json, if
    it was evaluated then — used purely for day-over-day price comparison and
    to carry editorial fields (thesis/catalyst/risks) forward. Never used to
    invent a gate result.
    """
    ticker = item.get("symbol")
    price = item.get("price", 0.0)
    market_cap = item.get("marketCap", 0.0)

    if not ticker or price <= 0:
        return None

    drop_pct = fetch_drop_pct(ticker)
    if drop_pct > DROP_THRESHOLD:
        return None  # doesn't even clear the initial dislocation screen

    cash, shares = fetch_cash_and_shares(ticker, price, market_cap)
    cps = None
    cash_cushion_pct = None
    if cash is not None and shares:
        cps = cash / shares if shares > 0 else 0.0
        cash_cushion_pct = (cps / price) * 100 if price > 0 else None

    # Explicit unknown state, never a silent passing default. A missing
    # balance-sheet figure is "gate unknown," not "gate pass."
    if cash_cushion_pct is None:
        gate = "UNKNOWN"
    elif cash_cushion_pct >= CASH_GATE:
        gate = "PASS"
    else:
        gate = "FAIL"

    # Real day-over-day tracking: compare today's price to the price stored
    # in yesterday's snapshot for this same ticker, not a fabricated number.
    previous_price = previous.get("price") if previous else None
    if previous_price and previous_price > 0:
        dollar_change = price - previous_price
        return_pct = (dollar_change / previous_price) * 100
    else:
        dollar_change = None
        return_pct = None

    record = {
        "name": item.get("companyName", ticker),
        "exchange": item.get("exchangeShortName", "NASDAQ"),
        "price": round(price, 2),
        "previous_price": round(previous_price, 2) if previous_price else None,
        "dollar_change": round(dollar_change, 2) if dollar_change is not None else None,
        "return_pct": round(return_pct, 2) if return_pct is not None else None,
        "market_cap": round(market_cap) if market_cap else None,
        "five_day_drop_pct": round(drop_pct, 1),
        "trend": resolve_momentum(return_pct),
        "cps": round(cps, 2) if cps is not None else None,
        "cash_cushion_pct": round(cash_cushion_pct, 1) if cash_cushion_pct is not None else None,
        "gate": gate,
        "target_zone": f"${round(price * 0.95, 2)} - ${round(price * 1.02, 2)}",
    }

    if previous:
        record["catalyst"] = previous.get("catalyst", "Automated candidate discovery via Vance Report Cash-to-Price screener.")
        record["thesis"] = previous.get("thesis") or (
            f"{record['name']} cleared the automated dislocation screen with a "
            f"{'%.1f' % cash_cushion_pct + '%' if cash_cushion_pct is not None else 'pending'} cash cushion."
        )
        record["risks"] = previous.get("risks", ["Pending fundamental desk review."])
    else:
        print(f"New candidate evaluated: {ticker} ({gate})")
        record["catalyst"] = "Automated candidate discovery via Vance Report Cash-to-Price screener."
        record["thesis"] = (
            f"{record['name']} cleared the automated dislocation screen with a "
            f"{'%.1f' % cash_cushion_pct + '%' if cash_cushion_pct is not None else 'pending'} cash cushion."
        )
        record["risks"] = ["Pending fundamental desk review."]

    return record


def run_screen(previous_snapshot):
    print("Running Vance Report Screener Pipeline...")
    candidates = fetch_screener_candidates()
    if not candidates:
        print("No candidates returned from screener.")
        return {}

    evaluated = {}
    for item in candidates:
        if not isinstance(item, dict):
            print(f"Skipping unexpected screener entry: {str(item)[:120]}")
            continue
        ticker = item.get("symbol")
        if not ticker:
            continue
        previous = previous_snapshot.get(ticker)
        record = build_record(item, previous)
        if record is not None:
            evaluated[ticker] = record

    return evaluated


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def strip_meta(data):
    return {k: v for k, v in data.items() if not k.startswith("_")}


def update_database():
    previous_snapshot = strip_meta(load_json(SNAPSHOT_FILE))

    evaluated = run_screen(previous_snapshot)
    if not evaluated:
        print("Screener returned nothing usable this run; leaving files unchanged.")
        return

    generated_at = datetime.now(timezone.utc).isoformat()
    previous_meta = load_json(SNAPSHOT_FILE).get("_meta", {})
    previous_generated_at = previous_meta.get("generated_at")

    # screen_snapshot.json: everything evaluated this run, pass or fail —
    # the full audit trail report.html needs.
    snapshot_out = dict(evaluated)
    snapshot_out["_meta"] = {
        "generated_at": generated_at,
        "previous_generated_at": previous_generated_at,
    }
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshot_out, f, indent=2)

    # research.json: only the current winners — the clean list index.html
    # and stock.html show. A ticker that no longer passes is dropped here
    # even though it stays visible in screen_snapshot.json.
    passing = {t: r for t, r in evaluated.items() if r["gate"] == "PASS"}
    research_out = dict(passing)
    research_out["_meta"] = {"generated_at": generated_at}
    with open(RESEARCH_FILE, "w") as f:
        json.dump(research_out, f, indent=2)

    print(
        f"Database update complete. {len(evaluated)} evaluated, "
        f"{len(passing)} currently passing."
    )


if __name__ == "__main__":
    update_database()
