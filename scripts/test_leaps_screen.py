"""Offline tests for the LEAPS screen."""

from datetime import date

import leaps_screen as ls

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f"   got={got!r} want={want!r}"))


AS_OF = date(2026, 10, 16)


def opt(code, bid, ask, delta, oi=1000, iv=0.3):
    return {"option": code, "bid": bid, "ask": ask, "delta": delta, "open_interest": oi, "iv": iv, "volume": 0}


print("\nparse_option")
exp, cp, k, dte = ls.parse_option("ABC280121C00042500", AS_OF)
check("expiry", exp, date(2028, 1, 21))
check("type", cp, "C")
check("strike", k, 42.5)
check("days", dte, 462)

print("\ncontracts")
chain = {"current_price": 100.0, "iv30": 30.0, "options": [
    opt("ABC280121C00080000", 26.0, 27.0, 0.80),            # the one
    opt("ABC280121C00085000", 22.0, 23.0, 0.76, oi=10),       # illiquid
    opt("ABC280121C00070000", 33.0, 34.0, 0.88),             # delta too high
    opt("ABC261120C00080000", 20.5, 21.0, 0.95),             # too short
    opt("ABC280121C00075000", 0.0, 30.0, 0.83),              # no bid
    opt("ABC261120C00110000", 1.9, 2.1, 0.25),               # PMCC short leg, 35 days
    opt("ABC261120C00105000", 3.0, 3.2, 0.33),               # delta too high for the short
]}
cs = ls.contracts(chain, AS_OF)
check("drops no-bid contracts", len(cs), 6)
c80 = next(c for c in cs if c["strike"] == 80)
check("midpoint", c80["mid"], 26.5)
check("spread % of mid", c80["spread_pct"], round(1 / 26.5 * 100, 2))

print("\nstock replacement")
leap = ls.stock_replacement(cs, 100.0)
check("picks the liquid 0.80 call", leap["strike"], 80.0)
e = ls.call_economics(leap, 100.0)
check("cost of one contract", e["cost"], 2650.0)
check("saving vs 100 shares", e["saving_pct"], 73.5)
check("extrinsic", e["extrinsic"], 6.5)
check("extrinsic share", e["extrinsic_pct"], round(6.5 / 26.5 * 100, 1))
check("breakeven", e["breakeven"], 106.5)
check("liquid", ls.liquid(leap), True)
check("illiquid one fails", ls.liquid(next(c for c in cs if c["strike"] == 85)), False)
check("prefers liquid over nearer delta", ls.pick([dict(c80, delta=0.78), dict(c80, delta=0.80, oi=5)], 0.80)["oi"], 1000)

print("\nPMCC")
p = ls.pmcc(cs, 100.0)
check("long leg nearest 0.85", p["long"]["strike"], 70.0)
check("short leg", p["short"]["strike"], 110.0)
check("premium per cycle", p["premium_pct_of_leap"], round(2.0 / 33.5 * 100, 2))
check("net debit", p["net_debit"], 3150.0)
check("sound when width > net cost", p["sound"], True)
bad = [dict(c, strike=99.0) if c["strike"] == 110 else c for c in cs]
check("unsound when width < net cost", ls.pmcc(bad, 98.0)["sound"], False)

print("\nvolatility rank")
check("rank in range", ls.rank_in(30, 20, 40), 50.0)
check("clipped low", ls.rank_in(10, 20, 40), 0.0)
check("clipped high", ls.rank_in(50, 20, 40), 100.0)
check("flat range", ls.rank_in(30, 20, 20), None)
closes = [(f"d{i}", 100 * (1.01 if i % 2 else 0.99) ** (i % 7)) for i in range(400)]
rng = ls.realized_vol_range(closes)
check("realized range found", rng is not None and rng[0] <= rng[1], True)
hist = {"ABC": [[f"2025-{i:03d}", 20.0 + (i % 21)] for i in range(ls.IV_RANK_MIN_DAYS)]}
r, kind = ls.vol_rank("ABC", 25.0, hist, None)
check("true IV rank with a year of history", (r, kind), (25.0, "iv_rank"))
r, kind = ls.vol_rank("XYZ", 25.0, {}, closes)
check("stand-in without history", kind, "realized_proxy")
check("nothing without either", ls.vol_rank("XYZ", 25.0, {}, None), (None, None))
h = {}
ls.remember_iv(h, "ABC", "2026-10-16", 30.0)
ls.remember_iv(h, "ABC", "2026-10-16", 31.0)
check("one entry per session", h["ABC"], [["2026-10-16", 31.0]])

print("\nassess")
row = {"symbol": "ABC", "name": "ABC CORP", "sector": "X", "rank": 3, "score": 80.0,
       "price": 100.0, "metrics": {"price_vs_200d": 1.1}}
a = ls.assess(row, chain, closes, {}, AS_OF, "2026-10-16")
check("trend passes", a["checks"]["trend"], True)
check("liquidity passes", a["checks"]["liquidity"], True)
check("has a PMCC", a["pmcc"] is not None, True)
row2 = dict(row, metrics={"price_vs_200d": 0.9})
check("below 200-day fails trend", ls.assess(row2, chain, closes, {}, AS_OF, "2026-10-16")["checks"]["trend"], False)

print("\nhedges")
spy = {"current_price": 700.0, "options": [
    opt("SPY271217P00665000", 25.0, 25.2, -0.3),
    opt("SPY271217P00630000", 18.0, 18.2, -0.2),
    opt("SPY271217P00700000", 35.0, 35.2, -0.45),
]}
hs = ls.hedges({"SPY": spy}, AS_OF)
check("two strikes for one expiry", sorted(h["contract"]["strike"] for h in hs), [630.0, 665.0])
h5 = next(h for h in hs if h["contract"]["strike"] == 665)
check("5% below", h5["otm_pct"], 5.0)
check("cost % of index", h5["cost_pct"], round(25.1 / 700 * 100, 2))

print()
if fails:
    print(f"{len(fails)} failed")
    raise SystemExit(1)
print("all passed")
