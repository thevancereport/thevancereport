"""The LEAPS screen: long-dated option setups on the top of the value ranking.

Runs after each night's value screen. It reads value_screen.json, takes the
top of the ranking (UNIVERSE_TOP), and for each company asks Cboe's free
delayed-quote feed for its whole option chain. From that it builds three
tables, written to leaps_screen.json for leaps.html to draw:

Stock replacement
    One deep in-the-money call, 365-730 days out, with a delta of 0.75-0.85
    (closest to 0.80 among the liquid ones). Shows what it costs against 100
    shares, how much of the price is time premium, and the liquidity.

Poor Man's Covered Call
    A deep in-the-money LEAP call (delta 0.80-0.90) as the base, paired with
    the 30-45 day call nearest 0.25 delta (0.20-0.30). Shows the short call's
    premium as a share of the LEAP's cost, and checks that the pair is sound:
    the gap between the strikes must be larger than the net cost, or being
    called away early locks in a loss.

Hedges
    Puts on SPY and QQQ 5-10% below the price, about one and two years out,
    with the cost as a share of the index, per year of protection.

Every company carries a pass/fail mark per filter (FILTERS), so a reader
sees why a name qualified or did not.

Volatility rank
    True IV Rank needs a year of a stock's implied volatility, and the feed
    only gives today's. So every night the stock's 30-day implied volatility
    is saved to leaps_iv_history.json. Until a stock has IV_RANK_MIN_DAYS of
    that history, the rank is a stand-in: where today's IV sits within the
    range of the stock's own 30-day realized volatility over the past year
    (0 = the bottom of that range, 100 = the top). It is labelled as such.
    Once the history is long enough the true IV Rank takes over by itself.

Prices are Cboe's delayed quotes; midpoints are (bid + ask) / 2.
Educational research only, not investment advice.
"""

import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCREEN_FILE = os.environ.get("SCREEN_FILE", "value_screen.json")
OUT_FILE = os.environ.get("LEAPS_FILE", "leaps_screen.json")
IV_FILE = os.environ.get("IV_HISTORY_FILE", "leaps_iv_history.json")

UNIVERSE_TOP = int(os.environ.get("LEAPS_UNIVERSE", "300"))
HEDGE_SYMBOLS = ("SPY", "QQQ")
WORKERS = 6
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
HISTORY_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/{sym}.json"

FILTERS = {
    "delta":    {"label": "Delta 0.75-0.85 (deep in the money)", "lo": 0.75, "hi": 0.85},
    "dte":      {"label": "365+ days to expiry (12-24 months)", "lo": 365, "hi": 730},
    "iv_rank":  {"label": "Volatility rank under 30", "max": 30},
    "liquidity": {"label": "Open interest 500+ and bid/ask spread at most 10% of the midpoint",
                  "min_oi": 500, "max_spread_pct": 10.0},
    "trend":    {"label": "Price above its 200-day average", "min_ratio": 1.0},
}
TARGET_DELTA = 0.80
PMCC_LONG = (0.80, 0.90, 0.85)       # lo, hi, target
PMCC_SHORT = (0.20, 0.30, 0.25)
PMCC_SHORT_DTE = (30, 45, 38)
HEDGE_OTM = (0.05, 0.10)             # 5-10% below the price
HEDGE_DTE = (365, 730)
IV_RANK_MIN_DAYS = 230               # about a year of sessions
IV_KEEP_DAYS = 400
SHOW_ROWS = 40                       # rows per table on the page


# ---------------------------------------------------------------- fetching

def _get_json(url, attempts=3):
    for i in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=60) as r:
                return json.load(r)
        except HTTPError as e:
            if e.code in (403, 404):       # no listed options
                return None
        except Exception:
            pass
        time.sleep(2 + 3 * i)
    return None


def fetch_chain(sym):
    d = _get_json(CHAIN_URL.format(sym=sym))
    return (d or {}).get("data")


def fetch_closes(sym):
    d = _get_json(HISTORY_URL.format(sym=sym))
    rows = (d or {}).get("data") or []
    return [(r["date"], float(r["close"])) for r in rows if r.get("close")]


# ---------------------------------------------------------------- arithmetic

def parse_option(code, as_of):
    """'AAPL271217C00250000' -> (expiry date, 'C', 250.0, days to expiry)."""
    tail = code[-15:]
    exp = datetime.strptime(tail[:6], "%y%m%d").date()
    return exp, tail[6], int(tail[7:]) / 1000.0, (exp - as_of).days


def contracts(chain, as_of):
    out = []
    for o in chain.get("options", []):
        try:
            exp, cp, k, dte = parse_option(o["option"], as_of)
        except (KeyError, ValueError):
            continue
        bid, ask = float(o.get("bid") or 0), float(o.get("ask") or 0)
        if bid <= 0 or ask < bid:
            continue                      # no live two-sided market
        mid = (bid + ask) / 2
        out.append({
            "code": o["option"], "expiry": exp.isoformat(), "type": cp, "strike": k, "dte": dte,
            "bid": bid, "ask": ask, "mid": round(mid, 4),
            "spread_pct": round((ask - bid) / mid * 100, 2),
            "delta": float(o.get("delta") or 0), "iv": float(o.get("iv") or 0),
            "oi": int(o.get("open_interest") or 0), "volume": int(o.get("volume") or 0),
        })
    return out


def liquid(c):
    f = FILTERS["liquidity"]
    return c["oi"] >= f["min_oi"] and c["spread_pct"] <= f["max_spread_pct"]


def pick(cands, target):
    """Liquid contracts first, then nearest the target delta, then the most open interest."""
    if not cands:
        return None
    return min(cands, key=lambda c: (not liquid(c), abs(abs(c["delta"]) - target), -c["oi"]))


def call_economics(c, spot):
    intrinsic = max(spot - c["strike"], 0.0)
    extrinsic = max(c["mid"] - intrinsic, 0.0)
    years = c["dte"] / 365.0
    return {
        "cost": round(c["mid"] * 100, 2),
        "shares_cost": round(spot * 100, 2),
        "saving_pct": round((1 - c["mid"] / spot) * 100, 1),
        "extrinsic": round(extrinsic, 2),
        "extrinsic_pct": round(extrinsic / c["mid"] * 100, 1) if c["mid"] else None,
        "time_cost_pct_per_year": round(extrinsic / spot / years * 100, 2) if years else None,
        "breakeven": round(c["strike"] + c["mid"], 2),
        "breakeven_move_pct": round(((c["strike"] + c["mid"]) / spot - 1) * 100, 1),
        "leverage": round(abs(c["delta"]) * spot / c["mid"], 2) if c["mid"] else None,
    }


def realized_vol_range(closes, window=30, lookback=252):
    """Min and max of rolling 30-day annualized realized volatility over the past year, in %."""
    px = [c for _, c in closes][-(lookback + window + 1):]
    if len(px) < window + 60:
        return None
    rets = [math.log(b / a) for a, b in zip(px, px[1:]) if a > 0 and b > 0]
    vols = [statistics.pstdev(rets[i - window:i]) * math.sqrt(252) * 100
            for i in range(window, len(rets) + 1)]
    return (min(vols), max(vols)) if vols else None


def rank_in(value, lo, hi):
    if value is None or lo is None or hi is None or hi <= lo:
        return None
    return round(max(0.0, min(100.0, (value - lo) / (hi - lo) * 100)), 1)


def vol_rank(sym, iv30, history, closes):
    """True IV Rank when a year of saved IV exists, else the realized-volatility stand-in."""
    past = [v for _, v in history.get(sym, [])]
    if len(past) >= IV_RANK_MIN_DAYS:
        return rank_in(iv30, min(past), max(past)), "iv_rank"
    rng = realized_vol_range(closes) if closes else None
    if rng:
        return rank_in(iv30, *rng), "realized_proxy"
    return None, None


def remember_iv(history, sym, session, iv30):
    if not iv30:
        return
    rows = [r for r in history.get(sym, []) if r[0] != session]
    rows.append([session, round(iv30, 2)])
    rows.sort()
    history[sym] = rows[-IV_KEEP_DAYS:]


# ---------------------------------------------------------------- the three tables

def stock_replacement(cs, spot):
    f = FILTERS
    window = [c for c in cs if c["type"] == "C" and f["dte"]["lo"] <= c["dte"] <= f["dte"]["hi"]
              and c["strike"] < spot]
    in_band = [c for c in window if f["delta"]["lo"] <= c["delta"] <= f["delta"]["hi"]]
    return pick(in_band, TARGET_DELTA)


def pmcc(cs, spot):
    lo, hi, tgt = PMCC_LONG
    longs = [c for c in cs if c["type"] == "C" and FILTERS["dte"]["lo"] <= c["dte"] <= FILTERS["dte"]["hi"]
             and lo <= c["delta"] <= hi and c["strike"] < spot]
    leg = pick(longs, tgt)
    if not leg:
        return None
    slo, shi, stgt = PMCC_SHORT
    dlo, dhi, dtgt = PMCC_SHORT_DTE
    shorts = [c for c in cs if c["type"] == "C" and dlo <= c["dte"] <= dhi
              and slo <= c["delta"] <= shi and c["strike"] > spot]
    if not shorts:
        return None
    short = min(shorts, key=lambda c: (abs(c["dte"] - dtgt), abs(c["delta"] - stgt), -c["oi"]))
    net = leg["mid"] - short["mid"]
    width = short["strike"] - leg["strike"]
    per_cycle = short["mid"] / leg["mid"] * 100
    return {
        "long": leg, "short": short,
        "net_debit": round(net * 100, 2),
        "premium": round(short["mid"] * 100, 2),
        "premium_pct_of_leap": round(per_cycle, 2),
        "strike_width": round(width, 2),
        "sound": width > net,
        "long_liquid": liquid(leg),
    }


def hedges(chains, as_of):
    out = []
    for sym in HEDGE_SYMBOLS:
        ch = chains.get(sym)
        if not ch:
            continue
        spot = float(ch.get("current_price") or 0)
        cs = [c for c in contracts(ch, as_of) if c["type"] == "P" and HEDGE_DTE[0] <= c["dte"] <= HEDGE_DTE[1]]
        expiries = sorted({c["expiry"] for c in cs})
        if not expiries:
            continue
        # about one year out and about two years out
        chosen = []
        for target in (365 + 30, 730):
            e = min(expiries, key=lambda x: abs((date.fromisoformat(x) - as_of).days - target))
            if e not in chosen:
                chosen.append(e)
        for e in chosen:
            for otm in (0.05, 0.10):
                k_target = spot * (1 - otm)
                ps = [c for c in cs if c["expiry"] == e and spot * (1 - HEDGE_OTM[1] - 0.01) <= c["strike"] <= spot * (1 - HEDGE_OTM[0] + 0.01)]
                if not ps:
                    continue
                c = min(ps, key=lambda c: (abs(c["strike"] - k_target), -c["oi"]))
                years = c["dte"] / 365.0
                out.append({
                    "symbol": sym, "spot": spot, "contract": c,
                    "otm_pct": round((1 - c["strike"] / spot) * 100, 1),
                    "cost": round(c["mid"] * 100, 2),
                    "cost_pct": round(c["mid"] / spot * 100, 2),
                    "cost_pct_per_year": round(c["mid"] / spot / years * 100, 2),
                    "protected_below": c["strike"],
                    "liquid": liquid(c),
                })
    return out


# ---------------------------------------------------------------- one company

def assess(row, chain, closes, history, as_of, session):
    sym = row["symbol"]
    spot = float(chain.get("current_price") or row.get("price") or 0)
    iv30 = float(chain.get("iv30") or 0)
    remember_iv(history, sym, session, iv30)
    cs = contracts(chain, as_of)
    leap = stock_replacement(cs, spot)
    rank, rank_kind = vol_rank(sym, iv30, history, closes)
    ratio = (row.get("metrics") or {}).get("price_vs_200d")
    checks = {
        "delta": bool(leap),
        "dte": bool(leap),
        "iv_rank": rank is not None and rank < FILTERS["iv_rank"]["max"],
        "liquidity": bool(leap) and liquid(leap),
        "trend": isinstance(ratio, (int, float)) and ratio > FILTERS["trend"]["min_ratio"],
    }
    out = {
        "symbol": sym, "name": row.get("name"), "sector": row.get("sector"),
        "value_rank": row.get("rank"), "value_score": row.get("score"),
        "spot": round(spot, 2), "iv30": round(iv30, 1),
        "vol_rank": rank, "vol_rank_kind": rank_kind,
        "price_vs_200d": ratio, "checks": checks,
        "passes": sum(checks.values()), "all_pass": all(checks.values()),
        "leap": None, "pmcc": None,
    }
    if leap:
        out["leap"] = dict(leap, **call_economics(leap, spot))
    p = pmcc(cs, spot)
    if p:
        out["pmcc"] = p
    return out


# ---------------------------------------------------------------- main

def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main():
    screen = load(SCREEN_FILE, None)
    if not screen:
        sys.exit(f"{SCREEN_FILE} missing")
    session = screen["_meta"]["as_of"]
    as_of = date.fromisoformat(session)
    top = screen["ranked"][:UNIVERSE_TOP]
    history = load(IV_FILE, {})

    syms = [r["symbol"] for r in top] + list(HEDGE_SYMBOLS)
    print(f"Fetching option chains for {len(syms)} symbols (session {session})...", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        chains = dict(zip(syms, pool.map(fetch_chain, syms)))
    listed = [r for r in top if chains.get(r["symbol"])]
    print(f"  {len(listed)} of {len(top)} have listed options.", flush=True)

    # closes only for names that need the stand-in volatility rank
    need = [r["symbol"] for r in listed if len(history.get(r["symbol"], [])) < IV_RANK_MIN_DAYS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        closes = dict(zip(need, pool.map(fetch_closes, need)))

    quote_times = sorted({(chains[s] or {}).get("last_trade_time", "")[:10] for s in syms if chains.get(s)})
    rows = [assess(r, chains[r["symbol"]], closes.get(r["symbol"]), history, as_of, session) for r in listed]
    with_leap = [r for r in rows if r["leap"]]
    with_leap.sort(key=lambda r: (-r["passes"], r["value_rank"]))
    pm = [r for r in rows if r["pmcc"]]
    pm.sort(key=lambda r: (not (r["pmcc"]["sound"] and r["pmcc"]["long_liquid"] and r["checks"]["trend"]),
                           r["value_rank"]))

    for s in HEDGE_SYMBOLS:
        if chains.get(s):
            remember_iv(history, s, session, float(chains[s].get("iv30") or 0))

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": session,
            "quotes_as_of": quote_times[-1] if quote_times else None,
            "source": "Cboe delayed quotes (cdn.cboe.com); prices are bid/ask midpoints",
            "universe": f"Top {len(top)} of the Vance value ranking",
            "checked": len(top), "with_options": len(listed), "with_leap": len(with_leap),
            "all_pass": sum(1 for r in rows if r["all_pass"]),
            "filters": {k: v["label"] for k, v in FILTERS.items()},
            "vol_rank_note": ("Volatility rank: true IV Rank once a stock has about a year of saved "
                              "implied volatility; until then, where today's 30-day implied volatility "
                              "sits within the range of the stock's own 30-day realized volatility over "
                              "the past year (0 = bottom of that range, 100 = top)."),
            "pmcc_rule": ("Sound when the gap between the two strikes is larger than the net cost; "
                          "otherwise being assigned on the short call locks in a loss."),
            "disclaimer": ("Educational research only, not investment advice. Options can lose 100% "
                           "of what is paid for them, and quotes are delayed."),
        },
        "stock_replacement": with_leap[:SHOW_ROWS],
        "pmcc": [{k: r[k] for k in ("symbol", "name", "value_rank", "spot", "price_vs_200d", "vol_rank",
                                     "vol_rank_kind", "pmcc")} for r in pm[:SHOW_ROWS]],
        "hedges": hedges(chains, as_of),
    }
    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=1)
    with open(IV_FILE, "w") as f:
        json.dump(history, f, separators=(",", ":"))
    m = out["_meta"]
    print(f"  {m['with_leap']} with a LEAP in range, {m['all_pass']} pass every filter, "
          f"{len(out['pmcc'])} PMCC pairs, {len(out['hedges'])} hedge rows. Quotes {m['quotes_as_of']}.")


if __name__ == "__main__":
    main()
