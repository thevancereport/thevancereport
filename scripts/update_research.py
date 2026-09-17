import json
import os
import requests

API_KEY = os.environ.get("FMP_API_KEY")
RESEARCH_FILE = "research.json"

def fetch_financial_data(ticker):
    try:
        # Real-time price & changes
        quote_url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={API_KEY}"
        quote_res = requests.get(quote_url).json()
        
        # Quarterly Balance Sheet
        balance_url = f"https://financialmodelingprep.com/api/v3/balance-sheet-statement/{ticker}?period=quarter&limit=1&apikey={API_KEY}"
        balance_res = requests.get(balance_url).json()

        if not quote_res or not balance_res:
            return None

        price = quote_res[0].get("price", 0.0)
        change_pct = quote_res[0].get("changesPercentage", 0.0)
        
        cash_and_equivalents = balance_res[0].get("cashAndCashEquivalents", 0)
        shares_outstanding = quote_res[0].get("sharesOutstanding", 1)
        
        cash_per_share = cash_and_equivalents / shares_outstanding if shares_outstanding else 0
        cps_ratio = (cash_per_share / price) * 100 if price else 0

        return {
            "price": f"${price:.2f}",
            "three_day_drop": f"{change_pct:.1f}%",
            "cash_per_share": f"${cash_per_share:.2f}",
            "cps": f"{cps_ratio:.1f}%"
        }
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None

def main():
    if not os.path.exists(RESEARCH_FILE):
        return

    with open(RESEARCH_FILE, "r") as f:
        data = json.load(f)

    for ticker, info in data.items():
        metrics = fetch_financial_data(ticker)
        if metrics:
            info["price"] = metrics["price"]
            info["three_day_drop"] = metrics["three_day_drop"]
            info["cash_per_share"] = metrics["cash_per_share"]
            info["cps"] = metrics["cps"]

    with open(RESEARCH_FILE, "w") as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    main()
