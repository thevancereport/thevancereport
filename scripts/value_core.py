"""The scoring core of the Vance Report value screen.

This module is the only place the rules live. Both the daily screen and the
backtest import it, so the thing that gets tested is the thing that runs. The
premise-check study earned that rule the hard way.

The rules themselves are frozen in scripts/VALUE_SPEC.md, committed before any
test of them was run. If you change a number here, change it there too, and
write down why.

Nothing in this file touches the network or the clock. Everything is a pure
function of the numbers handed to it, which is what makes it testable without
waiting twenty minutes for an Actions run.
"""

from collections import namedtuple

# --------------------------------------------------------------------------
# Sentinels
#
# MISSING and INVALID are different and the difference matters. MISSING means
# we could not read the number -- the company is not penalised for our gap, the
# metric is simply left out of its pillar's average. INVALID means we read it
# and it disqualifies the company on that measure: a negative EBIT has no
# meaningful EV/EBIT, and letting it sit out would hand every loss-maker a free
# pass through the cheapness test. INVALID therefore scores the worst
# percentile, tied with every other name in the same position.
# --------------------------------------------------------------------------

MISSING = None


class _Invalid:
    __slots__ = ()

    def __repr__(self):
        return "INVALID"

    def __bool__(self):
        return False


INVALID = _Invalid()


# --------------------------------------------------------------------------
# Weights and metric definitions -- VALUE_SPEC.md section 3
# --------------------------------------------------------------------------

PILLAR_WEIGHTS = {
    "cheapness": 0.40,
    "quality": 0.30,
    "safety": 0.20,
    "confirmation": 0.10,
}

Metric = namedtuple("Metric", "key pillar higher_better label")

METRICS = [
    # cheapness
    Metric("ev_ebit",        "cheapness",    False, "EV / EBIT"),
    Metric("ev_fcf",         "cheapness",    False, "EV / free cash flow"),
    Metric("ev_sales",       "cheapness",    False, "EV / revenue"),
    Metric("shareholder_yield", "cheapness",  True, "Shareholder yield"),
    # quality
    Metric("gross_profitability", "quality",  True, "Gross profit / assets"),
    Metric("roic",           "quality",       True, "Return on invested capital"),
    Metric("accruals",       "quality",      False, "Accruals / assets"),
    Metric("gp_change",      "quality",       True, "Change in gross profitability"),
    # safety
    Metric("net_debt_ebitda", "safety",      False, "Net debt / EBITDA"),
    Metric("interest_cover",  "safety",       True, "Interest coverage"),
    Metric("net_issuance",    "safety",      False, "Net share issuance"),
    Metric("working_capital", "safety",       True, "Working capital / assets"),
    Metric("retained_earnings", "safety",     True, "Retained earnings / assets"),
    # confirmation
    Metric("momentum_12_1",  "confirmation",  True, "12-1 month momentum"),
    Metric("price_vs_200d",  "confirmation",  True, "Price vs 200-day average"),
]

METRICS_BY_PILLAR = {}
for _m in METRICS:
    METRICS_BY_PILLAR.setdefault(_m.pillar, []).append(_m)


# --------------------------------------------------------------------------
# Universe rules -- VALUE_SPEC.md section 1
# --------------------------------------------------------------------------

MIN_MARKET_CAP = 300_000_000
MIN_DOLLAR_VOLUME = 2_000_000
MIN_PRICE = 3.00
MIN_PRICE_SESSIONS = 252
MAX_FILING_AGE_DAYS = 200
MIN_QUARTERS = 4

# Enterprise-value metrics do not describe a bank's or a REIT's balance sheet,
# so those names are not ranked against anything here. Excluding them is more
# honest than scoring them with a formula that does not apply.
EXCLUDED_SIC = (6000, 6799)

# A sector thinner than this is not a peer group, it is a rounding error, so
# its names are ranked against the whole universe instead.
MIN_SECTOR_SIZE = 20

TAX_RATE = 0.21


# --------------------------------------------------------------------------
# Sector buckets
# --------------------------------------------------------------------------

_SECTOR_RANGES = [
    ((100, 999), "Agriculture"),
    ((1000, 1099), "Metals & Mining"),
    ((1200, 1299), "Coal"),
    ((1300, 1399), "Energy"),
    ((1400, 1499), "Metals & Mining"),
    ((1500, 1799), "Construction"),
    ((2000, 2199), "Food & Beverage"),
    ((2200, 2399), "Textiles & Apparel"),
    ((2400, 2599), "Materials"),
    ((2600, 2699), "Materials"),
    ((2700, 2799), "Media & Publishing"),
    ((2800, 2829), "Chemicals"),
    ((2830, 2836), "Biotech & Pharma"),
    ((2840, 2899), "Chemicals"),
    ((2900, 2999), "Energy"),
    ((3000, 3299), "Materials"),
    ((3300, 3499), "Industrials"),
    ((3500, 3569), "Industrials"),
    ((3570, 3579), "Technology"),
    ((3580, 3599), "Industrials"),
    ((3600, 3659), "Electrical Equipment"),
    ((3660, 3699), "Technology"),
    ((3700, 3799), "Autos & Transport Equipment"),
    ((3800, 3829), "Instruments"),
    ((3830, 3851), "Medical Devices"),
    ((3852, 3999), "Consumer Goods"),
    ((4000, 4499), "Transport"),
    ((4500, 4599), "Airlines"),
    ((4600, 4799), "Transport"),
    ((4800, 4899), "Telecom"),
    ((4900, 4999), "Utilities"),
    ((5000, 5199), "Wholesale"),
    ((5200, 5999), "Retail"),
    ((7000, 7299), "Consumer Services"),
    ((7370, 7379), "Technology"),
    ((7300, 7369), "Business Services"),
    ((7380, 7999), "Business Services"),
    ((8000, 8099), "Healthcare Services"),
    ((8200, 8399), "Consumer Services"),
    ((8700, 8799), "Biotech & Pharma"),
    ((8800, 8999), "Business Services"),
]


def sector_of(sic):
    """Coarse sector bucket from an SIC code. Unknown codes get their own
    bucket rather than being lumped in with something they are not."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unclassified"
    for (lo, hi), name in _SECTOR_RANGES:
        if lo <= code <= hi:
            return name
    return "Unclassified"


def is_excluded_sector(sic):
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return False
    return EXCLUDED_SIC[0] <= code <= EXCLUDED_SIC[1]


# --------------------------------------------------------------------------
# Metric arithmetic
#
# Each function takes a dict of raw figures and returns a number, MISSING, or
# INVALID. They never raise: a company with a weird filing should drop out of
# one metric, not kill the run.
# --------------------------------------------------------------------------

def _num(v):
    if v is None or isinstance(v, _Invalid):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _ratio(numerator, denominator, denom_must_be_positive=True):
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None:
        return MISSING
    if denom_must_be_positive:
        if d <= 0:
            return INVALID
    elif d == 0:
        return MISSING
    return n / d


def enterprise_value(f):
    cap, debt, cash = _num(f.get("market_cap")), _num(f.get("total_debt")), _num(f.get("cash"))
    if cap is None:
        return MISSING
    return cap + (debt or 0.0) - (cash or 0.0)


def compute_metrics(f):
    """Raw metric values for one company at one date.

    `f` carries point-in-time figures only -- the caller is responsible for
    having filtered on filing date. This function has no way to check that, so
    the look-ahead guard lives in the callers and is tested there.
    """
    out = {}
    ev = enterprise_value(f)
    cap = _num(f.get("market_cap"))
    assets = _num(f.get("assets"))

    # --- cheapness -------------------------------------------------------
    # A negative EV means the market values the company below its net cash.
    # That is a real and interesting state, not an error, and it should score
    # as the cheapest thing on the board rather than being thrown out.
    for key, denom in (("ev_ebit", f.get("ebit")),
                       ("ev_fcf", f.get("fcf")),
                       ("ev_sales", f.get("revenue"))):
        d = _num(denom)
        if ev is MISSING or d is None:
            out[key] = MISSING
        elif d <= 0:
            out[key] = INVALID
        else:
            out[key] = ev / d

    out["shareholder_yield"] = _ratio(
        (_num(f.get("buybacks")) or 0.0) + (_num(f.get("dividends")) or 0.0), cap
    ) if cap is not None else MISSING

    # --- quality ---------------------------------------------------------
    out["gross_profitability"] = _ratio(f.get("gross_profit"), assets)
    out["accruals"] = _ratio(
        (_num(f.get("net_income")) - _num(f.get("cfo")))
        if _num(f.get("net_income")) is not None and _num(f.get("cfo")) is not None
        else None,
        assets,
    )

    ebit = _num(f.get("ebit"))
    equity, debt, cash = _num(f.get("equity")), _num(f.get("total_debt")), _num(f.get("cash"))
    if ebit is None or equity is None:
        out["roic"] = MISSING
    else:
        invested = equity + (debt or 0.0) - (cash or 0.0)
        out["roic"] = INVALID if invested <= 0 else ebit * (1 - TAX_RATE) / invested

    gp_now = out["gross_profitability"]
    gp_prior = _ratio(f.get("gross_profit_prior"), _num(f.get("assets_prior")))
    out["gp_change"] = (gp_now - gp_prior) if (
        isinstance(gp_now, float) and isinstance(gp_prior, float)
    ) else MISSING

    # --- safety ----------------------------------------------------------
    ebitda = _num(f.get("ebitda"))
    net_debt = None if debt is None and cash is None else (debt or 0.0) - (cash or 0.0)
    if net_debt is None:
        out["net_debt_ebitda"] = MISSING
    elif net_debt <= 0:
        # Net cash. There is no amount of EBITDA that makes this worse, so it
        # takes the best available reading rather than a division that flips
        # sign and lands it at the wrong end of the table.
        out["net_debt_ebitda"] = -1.0
    elif ebitda is None:
        out["net_debt_ebitda"] = MISSING
    elif ebitda <= 0:
        out["net_debt_ebitda"] = INVALID       # debt, no earnings to service it
    else:
        out["net_debt_ebitda"] = net_debt / ebitda

    interest = _num(f.get("interest_expense"))
    if ebit is None:
        out["interest_cover"] = MISSING
    elif interest is None or interest <= 0:
        out["interest_cover"] = 1000.0 if ebit > 0 else INVALID   # no debt cost
    else:
        out["interest_cover"] = ebit / interest

    sh_now, sh_prior = _num(f.get("shares")), _num(f.get("shares_prior"))
    out["net_issuance"] = (
        (sh_now - sh_prior) / sh_prior if sh_now is not None and sh_prior else MISSING
    )

    wc = None
    ca, cl = _num(f.get("current_assets")), _num(f.get("current_liabilities"))
    if ca is not None and cl is not None:
        wc = ca - cl
    out["working_capital"] = _ratio(wc, assets) if wc is not None else MISSING
    out["retained_earnings"] = _ratio(f.get("retained_earnings"), assets)

    # --- confirmation ----------------------------------------------------
    out["momentum_12_1"] = _num(f.get("momentum_12_1"))
    if out["momentum_12_1"] is None:
        out["momentum_12_1"] = MISSING
    out["price_vs_200d"] = _ratio(f.get("price"), f.get("ma_200"))

    return out


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def percentile_ranks(values, higher_better):
    """Percentile 0-100 for each value, higher always meaning better.

    Ties share the average of the positions they occupy, so a block of
    identical values cannot be ordered by an accident of input order. INVALID
    entries tie together at the bottom. MISSING entries come back as None and
    are left out of their pillar's average by the caller.
    """
    indexed = [(i, v) for i, v in enumerate(values)]
    rankable = [(i, v) for i, v in indexed if v is not MISSING]
    if not rankable:
        return [None] * len(values)

    worst = float("-inf") if higher_better else float("inf")

    def sort_key(pair):
        v = pair[1]
        return worst if isinstance(v, _Invalid) else float(v)

    ordered = sorted(rankable, key=sort_key, reverse=not higher_better)
    n = len(ordered)

    out = [None] * len(values)
    if n == 1:
        out[ordered[0][0]] = 50.0
        return out

    # Walk runs of equal value so ties get the same percentile.
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and sort_key(ordered[end]) == sort_key(ordered[pos]):
            end += 1
        share = sum(range(pos, end)) / (end - pos)
        pct = 100.0 * share / (n - 1)
        for k in range(pos, end):
            out[ordered[k][0]] = pct
        pos = end
    return out


def score_rows(rows):
    """Rank a universe. `rows` are dicts with the raw figures plus `symbol`
    and `sic`. Returns the same dicts with pillar scores, a composite and a
    rank added, sorted best first, and a list of the ones that could not be
    scored -- reported, never silently dropped.
    """
    for r in rows:
        r["sector"] = sector_of(r.get("sic"))
        r["metrics"] = compute_metrics(r)
        # A fresh dict every time, never setdefault. The backtest scores a
        # universe once a month for fifteen years, and two rows that arrived
        # sharing a percentile dict -- or one carrying last month's -- would
        # overwrite each other's scores silently.
        r["pct"] = {}
        r.pop("pillars", None)
        r.pop("score", None)
        r.pop("rank", None)
        r.pop("unranked_reason", None)

    # Percentiles are computed within sector, but a thin sector is not a peer
    # group, so its members fall back to the whole universe.
    by_sector = {}
    for r in rows:
        by_sector.setdefault(r["sector"], []).append(r)
    thin = [r for s, g in by_sector.items() if len(g) < MIN_SECTOR_SIZE for r in g]
    groups = [g for g in by_sector.values() if len(g) >= MIN_SECTOR_SIZE]
    if thin:
        groups.append(thin)

    for group in groups:
        for m in METRICS:
            vals = [r["metrics"][m.key] for r in group]
            pct = percentile_ranks(vals, m.higher_better)
            for r, p in zip(group, pct):
                r["pct"][m.key] = p

    scored, unranked = [], []
    for r in rows:
        pillars, complete = {}, True
        for pillar, metrics in METRICS_BY_PILLAR.items():
            have = [r["pct"][m.key] for m in metrics if r["pct"].get(m.key) is not None]
            if len(have) * 2 < len(metrics):        # needs at least half
                complete = False
                break
            pillars[pillar] = sum(have) / len(have)
        if not complete:
            r["unranked_reason"] = "not enough data in every pillar"
            unranked.append(r)
            continue
        r["pillars"] = {k: round(v, 1) for k, v in pillars.items()}
        r["score"] = round(sum(pillars[k] * w for k, w in PILLAR_WEIGHTS.items()), 2)
        scored.append(r)

    # Ties break on safety, then liquidity -- VALUE_SPEC.md section 3.
    scored.sort(
        key=lambda r: (-r["score"], -r["pillars"]["safety"], -(r.get("dollar_volume") or 0))
    )
    for i, r in enumerate(scored, start=1):
        r["rank"] = i
    return scored, unranked


def passes_universe(r, filing_age_days=None):
    """The section 1 gate. Returns (ok, reason)."""
    if is_excluded_sector(r.get("sic")):
        return False, "financial or REIT"
    cap = _num(r.get("market_cap"))
    if cap is None or cap < MIN_MARKET_CAP:
        return False, "market cap below floor"
    dv = _num(r.get("dollar_volume"))
    if dv is None or dv < MIN_DOLLAR_VOLUME:
        return False, "too illiquid"
    px = _num(r.get("price"))
    if px is None or px < MIN_PRICE:
        return False, "price below floor"
    if (r.get("price_sessions") or 0) < MIN_PRICE_SESSIONS:
        return False, "not enough price history"
    if (r.get("quarters_filed") or 0) < MIN_QUARTERS:
        return False, "fewer than four quarters on file"
    if filing_age_days is not None and filing_age_days > MAX_FILING_AGE_DAYS:
        return False, "latest filing too old"
    return True, ""
