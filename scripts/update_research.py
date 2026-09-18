import json
import os
from datetime import datetime, timezone
import requests

API_KEY = os.environ.get("FMP_API_KEY")
RESEARCH_FILE = "research.json"

DROP_THRESHOLD = -15.0   # 5-day drop must be at least this bad to enter the screen
CASH_GATE = 30.0         # cash-per-share as % of price must clear this to pass
MOMENTUM_MOVE = 1.0      # day-over-day %% move needed to call it REBOUND/FALLING vs STABILIZING


def fetch_screener_candidates():
    url = (
        "https://financialmodelingprep.com/api/v3/stock-screener"
        "?marketCapMoreThan=50000000&volumeMoreThan=50000"
        f"&isActivelyTrading=true&country=US&limit=100&apikey={API_KEY}"
    )
    try:
        return requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"Error reaching FMP Screener: {e}")
        return []


def fetch_drop_pct(ticker):
    url = f"https://financialmodelingprep.com/api/v3/stock-price-change/{ticker}?apikey={API_KEY}"
    try:
        data = requests.get(url, timeout=20).json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0].get("5D", 0.0)
    except Exception as e:
        print(f"Error fetching price change for {ticker}: {e}")
    return 0.0


def fetch_cash_and_shares(ticker, price, market_cap):
    url = (
        f"https://financialmodelingprep.com/api/v3/balance-sheet-statement/{ticker}"
        f"?period=quarter&limit=1&apikey={API_KEY}"
    )
    try:
        bs_data = requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"Error fetching balance sheet for {ticker}: {e}")
        return None, None

    if not bs_data or not isinstance(bs_data, list):
        return None, None

    cash = bs_data[0].get("cashAndCashEquivalents", 0.0)
    shares = market_cap / price if price > 0 else 1
    return cash, shares


def build_record(item, prior_price=None):
    """Fetches fresh metrics for one ticker and returns a full record, or None if it
    doesn't clear the screen's gates."""
    ticker = item.get("symbol")
    price = item.get("price", 0.0)
    market_cap = item.get("marketCap", 0.0)

    if not ticker or price <= 0:
        return None

    drop_pct = fetch_drop_pct(ticker)
    if drop_pct > DROP_THRESHOLD:
        return None

    cash, shares = fetch_cash_and_shares(ticker, price, market_cap)
    if cash is None or not shares:
        return None

    cps = cash / shares if shares > 0 else 0.0
    cps_ratio = (cps / price) * 100 if price > 0 else 0.0

    if cps_ratio < CASH_GATE:
        return None

    # Real momentum: compare today's price to what was stored yesterday.
    # A fresh (never-before-seen) ticker has no prior price to compare against,
    # so it starts neutral rather than guessing.
    if prior_price and prior_price > 0:
        day_change_pct = ((price - prior_price) / prior_price) * 100
        if day_change_pct >= MOMENTUM_MOVE:
            trend = "REBOUND"
        elif day_change_pct <= -MOMENTUM_MOVE:
            trend = "FALLING"
        else:
            trend = "STABILIZING"
    else:
        trend = "STABILIZING"

    return {
        "name": item.get("companyName", ticker),
        "exchange": item.get("exchangeShortName", "NASDAQ"),
        "price": round(price, 2),
        "market_cap": round(market_cap) if market_cap else None,
        "intrinsic_value": round(price * 1.85, 2),
        "drop": f"{round(drop_pct, 1)}%",
        "trend": trend,
        "cps": f"{round(cps, 2)}",
        "cps_cushion": f"{round(cps_ratio, 1)}%",
        "gate": "Pass",
        "target_zone": f"${round(price * 0.95, 2)} - ${round(price * 1.02, 2)}",
    }


def run_vance_screener(existing_data):
    print("Running Vance Report Screener Pipeline...")
    candidates = fetch_screener_candidates()
    if not candidates:
        print("No candidates returned from screener — leaving existing data untouched.")
        return {}

    updated = {}
    for item in candidates:
        ticker = item.get("symbol")
        if not ticker:
            continue

        prior = existing_data.get(ticker)
        prior_price = prior.get("price") if prior else None

        record = build_record(item, prior_price=prior_price)
        if record is None:
            continue

        # Preserve the editorial fields a human may have written, rather than
        # overwriting them with generic boilerplate every single day.
        if prior:
            record["catalyst"] = prior.get("catalyst", "Automated candidate discovery via Vance Report Cash-to-Price screener.")
            record["thesis"] = prior.get("thesis") or (
                f"{record['name']} triggered the automated Cash-to-Price filter "
                f"with a {record['cps_cushion']} cash cushion following a recent pullback."
            )
            record["risks"] = prior.get("risks", ["Pending fundamental desk review."])
        else:
            record["catalyst"] = "Automated candidate discovery via Vance Report Cash-to-Price screener."
            record["thesis"] = (
                f"{record['name']} triggered the automated Cash-to-Price filter "
                f"with a {record['cps_cushion']} cash cushion following a recent pullback."
            )
            record["risks"] = ["Pending fundamental desk review."]
            print(f"Adding new candidate: {ticker}")

        updated[ticker] = record

    return updated


def update_database():
    if not os.path.exists(RESEARCH_FILE):
        existing_data = {}
    else:
        with open(RESEARCH_FILE, "r") as f:
            try:
                existing_data = json.load(f)
            except Exception:
                existing_data = {}

    # Every ticker still clearing the gate gets a fresh record this run.
    # A ticker that no longer clears the gate (price recovered, cash cushion
    # thinned, etc.) is intentionally dropped rather than left stale forever.
    refreshed = run_vance_screener(existing_data)

    if not refreshed:
        print("Screener returned nothing usable this run; leaving research.json unchanged.")
        return

    # "_meta" is a reserved key, not a ticker — the front end skips any key
    # starting with "_" when it builds the list of stocks to render.
    refreshed["_meta"] = {"generated_at": datetime.now(timezone.utc).isoformat()}

    with open(RESEARCH_FILE, "w") as f:
        json.dump(refreshed, f, indent=2)
    print(f"Database update complete. {len(refreshed) - 1} tickers currently clear the gate.")


if __name__ == "__main__":
    update_database()
