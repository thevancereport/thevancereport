import json
import os
import requests

API_KEY = os.environ.get("FMP_API_KEY")
RESEARCH_FILE = "research.json"

def run_vance_screener():
    """Queries the FMP API to replicate The Vance Report Cash-to-Price screener."""
    print("Running Vance Report Screener Pipeline...")
    
    # 1. Fetch active US equities meeting baseline volume & market cap filters
    screener_url = f"https://financialmodelingprep.com/api/v3/stock-screener?marketCapMoreThan=50000000&volumeMoreThan=50000&isActivelyTrading=true&country=US&limit=100&apikey={API_KEY}"
    
    try:
        candidates = requests.get(screener_url).json()
    except Exception as e:
        print(f"Error reaching FMP Screener: {e}")
        return []

    screened_results = []

    for item in candidates:
        ticker = item.get("symbol")
        price = item.get("price", 0.0)
        
        if not ticker or price <= 0:
            continue

        # 2. Get short-term price change metrics (Look for >= 15% drop)
        change_url = f"https://financialmodelingprep.com/api/v3/stock-price-change/{ticker}?apikey={API_KEY}"
        change_data = requests.get(change_url).json()
        
        drop_pct = 0.0
        if change_data and isinstance(change_data, list) and len(change_data) > 0:
            drop_pct = change_data[0].get("5D", 0.0)
        
        # Filter for pullback >= 15%
        if drop_pct > -15.0:
            continue

        # 3. Pull Balance Sheet to compute Cash-Per-Share (CPS)
        bs_url = f"https://financialmodelingprep.com/api/v3/balance-sheet-statement/{ticker}?period=quarter&limit=1&apikey={API_KEY}"
        bs_data = requests.get(bs_url).json()

        if not bs_data or not isinstance(bs_data, list):
            continue

        cash = bs_data[0].get("cashAndCashEquivalents", 0.0)
        shares = item.get("marketCap", 0.0) / price if price > 0 else 1

        cps = cash / shares if shares > 0 else 0.0
        cps_ratio = (cps / price) * 100 if price > 0 else 0.0

        # Gatekeeper Rule: Cash Cushion >= 30.0%
        if cps_ratio >= 30.0:
            screened_results.append({
                "ticker": ticker,
                "name": item.get("companyName", ticker),
                "price": round(price, 2),
                "intrinsic_value": round(price * 1.85, 2),
                "drop": f"{round(drop_pct, 1)}%",
                "trend": "REBOUND" if drop_pct < -20 else "STABILIZING",
                "cps": f"{round(cps, 2)}",
                "cps_cushion": f"{round(cps_ratio, 1)}%",
                "gate": "Pass",
                "target_zone": f"${round(price * 0.95, 2)} - ${round(price * 1.02, 2)}"
            })

    return screened_results


def update_database():
    if not os.path.exists(RESEARCH_FILE):
        data = {}
    else:
        with open(RESEARCH_FILE, 'r') as f:
            try:
                data = json.load(f)
            except Exception:
                data = {}

    # Run screener logic
    new_finds = run_vance_screener()

    # Append new discoveries to research.json
    for item in new_finds:
        t = item["ticker"]
        if t not in data:
            print(f"Adding new candidate: {t}")
            data[t] = {
                "name": item["name"],
                "price": item["price"],
                "intrinsic_value": item["intrinsic_value"],
                "drop": item["drop"],
                "trend": item["trend"],
                "cps": item["cps"],
                "cps_cushion": item["cps_cushion"],
                "gate": item["gate"],
                "target_zone": item["target_zone"],
                "catalyst": "Automated candidate discovery via Vance Report Cash-to-Price screener.",
                "thesis": f"{item['name']} triggered the automated Cash-to-Price filter with a {item['cps_cushion']} cash cushion following a recent pullback.",
                "risks": ["Pending fundamental desk review."]
            }

    # Write back to research.json
    with open(RESEARCH_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print("Database update complete.")

if __name__ == "__main__":
    update_database()
