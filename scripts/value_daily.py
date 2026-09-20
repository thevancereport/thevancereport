"""The daily run: rank the eligible universe and write it out for the site.

Same rules as the backtest, same code -- it imports value_core, so what the
site shows is what was tested. The difference is only that this one looks at
today instead of at 90 historical dates, and writes a file the pages read.

It ranks rather than gates. Every eligible company gets a score and a place in
the order, which is why the site always has something to show instead of the
thirty-empty-days-in-fifty-two the cash-cushion screen produced.

What it writes:

  value_screen.json   the whole ranked universe, ~1,000 names, with each name's
                      four pillar scores and the figures behind them. One file
                      serves the home page, the report, the screen engine and
                      the per-stock profiles.

A pass is not a recommendation and this file says so in its own metadata. The
backtest found the ranking ordered but the top of it about level with the
market, so the honest claim is "worth a look", not "worth buying".
"""

import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import value_core as vc
import value_data as vd
import value_prices as vp

# How much filing history to read. Four quarters is the minimum the universe
# gate asks for; six gives the year-on-year comparisons something to work with
# and keeps the download under a couple of minutes.
QUARTERS_BACK = int(os.environ.get("QUARTERS_BACK", "7"))
MAX_SYMBOLS = int(os.environ.get("MAX_SYMBOLS", "0"))
PRICE_WORKERS = int(os.environ.get("PRICE_WORKERS", "6"))
OUT_FILE = os.environ.get("OUT_FILE", "value_screen.json")
AS_OF = os.environ.get("AS_OF", "")          # blank = today

UA = os.environ.get("SEC_UA", "TheVanceReport research contact@thevancereport.com")


def log(*a):
    print(*a, flush=True)


def recent_quarters(when, n):
    """The n most recent SEC data sets likely to exist at `when`.

    The SEC publishes a quarter's file some weeks after it closes, so the
    current quarter is never available and the one before it may not be.
    Asking for a file that does not exist costs one failed request, which
    load_quarter already survives, but there is no reason to ask.
    """
    y, q = when.year, (when.month - 1) // 3 + 1
    q -= 2                                   # step back off the unpublished edge
    while q < 1:
        y, q = y - 1, q + 4
    out = []
    for _ in range(n):
        out.append(f"{y}q{q}")
        q -= 1
        if q < 1:
            y, q = y - 1, 4
    return list(reversed(out))


def rounded(v, places=2):
    return round(v, places) if isinstance(v, float) else None


def main():
    as_of = date.fromisoformat(AS_OF) if AS_OF else date.today()
    log(__doc__.strip().split("\n\n")[0])
    log(f"As of {as_of}.\n")

    quarters = recent_quarters(as_of, QUARTERS_BACK)
    log(f"Reading {len(quarters)} SEC data sets, {quarters[0]} to {quarters[-1]}.")
    facts, subs = {}, {}
    for q in quarters:
        try:
            vd.load_quarter(q, facts, subs)
            log(f"  {q}: {len(subs):,} filers")
        except Exception as exc:                          # noqa: BLE001
            log(f"  {q}: skipped ({exc})")
    if not facts:
        log("No SEC data reached. Nothing written -- the site keeps yesterday's file.")
        return 1
    vd.tidy(facts)

    import value_backtest as vb                            # ticker_map lives there
    tickers = vb.ticker_map()
    mapped = {c: tickers[c] for c in subs if c in tickers}
    symbols = sorted(set(mapped.values()))
    if MAX_SYMBOLS:
        symbols = symbols[:MAX_SYMBOLS]
    log(f"\n{len(mapped):,} filers carry a ticker. Fetching {len(symbols):,} price histories.")

    prices, done = {}, [0]

    def grab(sym):
        p = vp.fetch_prices(sym, date(as_of.year - 2, 1, 1), as_of + timedelta(days=1))
        done[0] += 1
        if done[0] % 500 == 0:
            log(f"  ...{done[0]:,}/{len(symbols):,}")
        return sym, p

    with ThreadPoolExecutor(max_workers=PRICE_WORKERS) as pool:
        for sym, p in pool.map(grab, symbols):
            if p:
                prices[sym] = p
    log(f"{len(prices):,} usable price histories.")
    log(vp.report() + "\n")
    if len(prices) < 200:
        log("Too few price histories. Nothing written -- the site keeps yesterday's file.")
        return 1

    # ----------------------------------------------------------------------
    # Build and score
    # ----------------------------------------------------------------------
    rows, rejected = [], {}
    for cik, sym in mapped.items():
        series = prices.get(sym)
        if not series:
            continue
        days, closes, vols = series
        i = vb.idx_on_or_before(days, as_of)
        if i is None or i < vc.MIN_PRICE_SESSIONS:
            rejected["not enough price history"] = rejected.get("not enough price history", 0) + 1
            continue
        g = vd.figures_for(cik, facts, as_of)
        if not g or g.get("shares") is None:
            rejected["no usable filing"] = rejected.get("no usable filing", 0) + 1
            continue
        meta = subs.get(cik) or {}
        price = closes[i]
        lo = max(0, i - 59)
        dv = [closes[k] * vols[k] for k in range(lo, i + 1)]
        row = dict(g)
        row.update(
            symbol=sym, cik=cik, sic=meta.get("sic"), name=meta.get("name"),
            price=price, market_cap=price * g["shares"],
            dollar_volume=statistics.median(dv) if dv else 0.0,
            price_sessions=i, quarters_filed=meta.get("filings", 0),
        )
        row["ma_200"] = sum(closes[i - 199:i + 1]) / 200.0 if i >= 199 else None
        if i >= 252 and closes[i - 252] > 0:
            row["momentum_12_1"] = (closes[i - 21] - closes[i - 252]) / closes[i - 252]
        age = (as_of - meta["last_filed"]).days if meta.get("last_filed") else None
        ok, why = vc.passes_universe(row, filing_age_days=age)
        if ok:
            rows.append(row)
        else:
            rejected[why] = rejected.get(why, 0) + 1

    log(f"{len(rows):,} companies clear the universe rules.")
    for why, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
        log(f"  {n:,} excluded: {why}")

    if len(rows) < 50:
        log("\nUniverse too thin to publish. Nothing written.")
        return 1

    scored, unranked = vc.score_rows(rows)
    log(f"\n{len(scored):,} ranked, {len(unranked):,} unranked for want of data.\n")

    def metrics_of(r):
        m = r["metrics"]
        return {
            "ev_ebit": rounded(m.get("ev_ebit"), 1),
            "ev_fcf": rounded(m.get("ev_fcf"), 1),
            "ev_sales": rounded(m.get("ev_sales"), 2),
            "shareholder_yield_pct": rounded(
                m["shareholder_yield"] * 100 if isinstance(m.get("shareholder_yield"), float) else None, 2),
            "gross_profitability": rounded(m.get("gross_profitability"), 3),
            "roic_pct": rounded(m["roic"] * 100 if isinstance(m.get("roic"), float) else None, 1),
            "accruals": rounded(m.get("accruals"), 3),
            "net_debt_ebitda": rounded(m.get("net_debt_ebitda"), 2),
            "interest_cover": rounded(m.get("interest_cover"), 1),
            "net_issuance_pct": rounded(
                m["net_issuance"] * 100 if isinstance(m.get("net_issuance"), float) else None, 1),
            "momentum_12_1_pct": rounded(
                m["momentum_12_1"] * 100 if isinstance(m.get("momentum_12_1"), float) else None, 1),
            "price_vs_200d": rounded(m.get("price_vs_200d"), 3),
        }

    ranked = []
    for r in scored:
        ranked.append({
            "rank": r["rank"],
            "symbol": r["symbol"],
            "name": r.get("name"),
            "sector": r.get("sector"),
            "score": r["score"],
            "decile": min(10, (r["rank"] - 1) * 10 // max(len(scored), 1) + 1),
            "pillars": r["pillars"],
            "price": round(r["price"], 2),
            "market_cap": round(r["market_cap"]),
            "dollar_volume": round(r["dollar_volume"]),
            "metrics": metrics_of(r),
        })

    payload = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": as_of.isoformat(),
            "universe": len(scored),
            "unranked": len(unranked),
            "spec": "scripts/VALUE_SPEC.md",
            "weights": vc.PILLAR_WEIGHTS,
            "floors": {
                "market_cap": vc.MIN_MARKET_CAP,
                "dollar_volume": vc.MIN_DOLLAR_VOLUME,
                "price": vc.MIN_PRICE,
            },
            "pillars": {
                "cheapness": "EV/EBIT, EV/free cash flow, EV/revenue, shareholder yield",
                "quality": "gross profit over assets, return on invested capital, "
                           "accruals, change in gross profitability",
                "safety": "net debt to EBITDA, interest cover, net share issuance, "
                          "working capital, retained earnings",
                "confirmation": "12-1 month momentum, price against its 200-day average",
            },
            "how_to_read":
                "Every figure is a percentile against companies in the same sector, "
                "today, where 100 is the best reading on the board and 0 the worst. "
                "A high rank means the arithmetic is favourable and nothing more. "
                "Testing found the ranking ordered but the top of it performed about "
                "in line with the market, so this is a filter for what to read about, "
                "not a forecast and not a recommendation.",
            "excluded": "Banks, insurers and REITs are not ranked: enterprise-value "
                        "measures do not describe those balance sheets.",
        },
        "ranked": ranked,
    }

    with open(OUT_FILE, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    size = os.path.getsize(OUT_FILE)
    log(f"Wrote {OUT_FILE}: {len(ranked):,} names, {size / 1024:.0f} KB.")

    log("\nTop 10 today:")
    for r in ranked[:10]:
        p = r["pillars"]
        log(f"  {r['rank']:>3}. {r['symbol']:<6} {r['score']:>5.1f}   "
            f"cheap {p['cheapness']:>5.1f}  qual {p['quality']:>5.1f}  "
            f"safe {p['safety']:>5.1f}  conf {p['confirmation']:>5.1f}   "
            f"{(r['name'] or '')[:38]}")
    write_summary(payload)
    return 0


def write_summary(payload):
    """Put today's top of the list on the run page.

    Cheap insurance: if the committed file ever looks wrong, the run page says
    what the job actually produced, without re-running it.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    meta, ranked = payload["_meta"], payload["ranked"]
    L = [f"## Value screen, {meta['as_of']}\n",
         f"{meta['universe']:,} ranked, {meta['unranked']:,} unranked for want of data.\n",
         "| # | symbol | name | sector | score | cheap | qual | safe | conf | price | EV/EBIT |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ranked[:25]:
        p = r["pillars"]
        L.append(f"| {r['rank']} | {r['symbol']} | {(r['name'] or '')[:34]} | "
                 f"{r['sector'] or ''} | {r['score']:.1f} | {p['cheapness']:.0f} | "
                 f"{p['quality']:.0f} | {p['safety']:.0f} | {p['confirmation']:.0f} | "
                 f"{r['price']:.2f} | {r['metrics']['ev_ebit'] if r['metrics']['ev_ebit'] is not None else '--'} |")
    L.append("")
    L.append("_A rank is not a recommendation. " + meta["how_to_read"] + "_")
    try:
        with open(path, "a") as fh:
            fh.write("\n".join(L) + "\n")
    except OSError as exc:                                  # noqa: BLE001
        log(f"Could not write the step summary: {exc}")


if __name__ == "__main__":
    sys.exit(main())
