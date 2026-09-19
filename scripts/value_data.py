"""Point-in-time fundamentals from SEC Financial Statement Data Sets.

Why these files and not the XBRL frames API: the quarterly data sets contain
every company that filed in that quarter, including the ones that have since
been delisted, acquired or wound up. The frames API describes the world as it
is now. The premise-check study was an upper bound precisely because it used a
universe of survivors, and this module exists so the value screen's test does
not repeat that.

Each quarter's ZIP holds sub.txt (one row per filing: cik, name, sic, form,
period, and crucially `filed`) and num.txt (one row per reported number: adsh,
tag, ddate, qtrs, value). Joining them on `adsh` gives every figure with the
date it actually became public, which is the only date a look-ahead guard can
honestly use.

Nothing here scores anything. It hands raw figures to value_core.
"""

import csv
import io
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen

FSDS_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{q}.zip"

# The SEC asks for a real contact in the User-Agent and throttles anything that
# looks anonymous. This is the project's own address, not a person's.
UA = os.environ.get("SEC_UA", "TheVanceReport research contact@thevancereport.com")

# Only these tags are kept out of num.txt. Everything else is thrown away as it
# streams, which is the difference between a few hundred megabytes of RAM and
# several gigabytes.
#
# qtrs semantics in this data set: 0 = a balance-sheet instant, 1 = one
# quarter, 4 = a full year. Duration tags therefore arrive as quarters from
# 10-Qs and as years from 10-Ks, and ttm() below has to cope with both.
INSTANT_TAGS = {
    "Assets": "assets",
    "AssetsCurrent": "current_assets",
    "LiabilitiesCurrent": "current_liabilities",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "StockholdersEquity": "equity",
    "RetainedEarningsAccumulatedDeficit": "retained_earnings",
    "LongTermDebtNoncurrent": "lt_debt",
    "LongTermDebtCurrent": "st_debt",
    "ShortTermBorrowings": "st_borrow",
    "EntityCommonStockSharesOutstanding": "shares",
    "CommonStockSharesOutstanding": "shares_alt",
}

DURATION_TAGS = {
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue_alt",
    "SalesRevenueNet": "revenue_alt2",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "ebit",
    "NetIncomeLoss": "net_income",
    "DepreciationDepletionAndAmortization": "dda",
    "NetCashProvidedByUsedInOperatingActivities": "cfo",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
    "PaymentsForRepurchaseOfCommonStock": "buybacks",
    "PaymentsOfDividendsCommonStock": "dividends",
    "InterestExpense": "interest_expense",
}

WANTED = set(INSTANT_TAGS) | set(DURATION_TAGS)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def quarters_between(start, end):
    """['2016q1', '2016q2', ...] inclusive."""
    out = []
    y, q = start
    while (y, q) <= end:
        out.append(f"{y}q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def _get(url, timeout=120, attempts=4):
    last = None
    for i in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:                       # noqa: BLE001 - report, retry
            last = exc
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def load_quarter(q, facts, subs):
    """Fold one quarter's data set into `facts` and `subs` in place.

    facts[cik][field] -> list of (filed:date, ddate:date, qtrs:int, value:float)
    subs[cik] -> {'name':..., 'sic':..., 'last_filed': date}
    """
    raw = _get(FSDS_URL.format(q=q))
    zf = zipfile.ZipFile(io.BytesIO(raw))

    adsh_meta = {}
    with zf.open("sub.txt") as fh:
        rdr = csv.DictReader(io.TextIOWrapper(fh, "utf-8", errors="replace"), delimiter="\t")
        for row in rdr:
            form = (row.get("form") or "").strip()
            if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                continue
            try:
                cik = int(row["cik"])
                filed = datetime.strptime(row["filed"].strip(), "%Y%m%d").date()
            except (ValueError, KeyError, TypeError):
                continue
            adsh_meta[row["adsh"]] = (cik, filed)
            sic = (row.get("sic") or "").strip()
            prev = subs.get(cik)
            if prev is None:
                subs[cik] = {"name": (row.get("name") or "").strip(), "sic": sic,
                             "last_filed": filed, "filings": 1}
            else:
                prev["filings"] += 1
                if filed > prev["last_filed"]:
                    prev["last_filed"] = filed
                    if sic:
                        prev["sic"] = sic

    with zf.open("num.txt") as fh:
        rdr = csv.DictReader(io.TextIOWrapper(fh, "utf-8", errors="replace"), delimiter="\t")
        for row in rdr:
            tag = row.get("tag")
            if tag not in WANTED:
                continue
            meta = adsh_meta.get(row.get("adsh"))
            if meta is None:
                continue
            # A segmented fact (coval/segments populated) describes a slice of
            # the business, not the company, so only the consolidated figure is
            # kept. The column is named differently across vintages.
            seg = row.get("segments") or row.get("coreg") or ""
            if seg.strip():
                continue
            try:
                value = float(row["value"])
                ddate = datetime.strptime(row["ddate"].strip(), "%Y%m%d").date()
                qtrs = int(row.get("qtrs") or 0)
            except (ValueError, TypeError, KeyError):
                continue
            field = INSTANT_TAGS.get(tag) or DURATION_TAGS.get(tag)
            cik, filed = meta
            facts.setdefault(cik, {}).setdefault(field, []).append((filed, ddate, qtrs, value))


def tidy(facts):
    """Sort each series and drop restatements.

    Where the same period is reported twice, the earlier-FILED row wins. A
    later 10-K/A that restates a 2019 number must never reach back and change
    what the screen would have seen in 2019.
    """
    for _cik, fields in facts.items():
        for field, rows in fields.items():
            best = {}
            for filed, ddate, qtrs, value in rows:
                key = (ddate, qtrs)
                if key not in best or filed < best[key][0]:
                    best[key] = (filed, ddate, qtrs, value)
            fields[field] = sorted(best.values(), key=lambda r: (r[1], r[0]))


# --------------------------------------------------------------------------
# Point-in-time reads
# --------------------------------------------------------------------------

def latest_instant(series, on):
    """Most recent instantaneous value whose filing date is on or before `on`."""
    best = None
    for filed, ddate, qtrs, value in series or ():
        if filed > on:
            continue                       # not public yet -- the whole guard
        if best is None or ddate > best[1] or (ddate == best[1] and filed < best[0]):
            best = (filed, ddate, qtrs, value)
    return None if best is None else best[3]


def ttm(series, on, max_stale_days=420):
    """Trailing twelve months from data public on or before `on`.

    Prefers four consecutive quarterly figures. Falls back to the most recent
    annual figure, which is a lagged but honest TTM. Returns None rather than
    guessing when neither is available or the data is too old to mean anything.
    """
    avail = [r for r in (series or ()) if r[0] <= on]
    if not avail:
        return None

    quarters = sorted([r for r in avail if r[2] == 1], key=lambda r: r[1], reverse=True)
    picked, seen = [], set()
    for filed, ddate, qtrs, value in quarters:
        if ddate in seen:
            continue
        seen.add(ddate)
        picked.append((ddate, value))
        if len(picked) == 4:
            break
    if len(picked) == 4:
        span = (picked[0][0] - picked[3][0]).days
        if 250 <= span <= 460 and (on - picked[0][0]).days <= max_stale_days:
            return sum(v for _d, v in picked)

    annual = sorted([r for r in avail if r[2] == 4], key=lambda r: r[1], reverse=True)
    if annual and (on - annual[0][1]).days <= max_stale_days:
        return annual[0][3]
    return None


def figures_for(cik, facts, on):
    """Everything value_core needs for one company at one date, or None."""
    f = facts.get(cik)
    if not f:
        return None
    g = {}

    for field in ("assets", "current_assets", "current_liabilities", "cash",
                  "equity", "retained_earnings"):
        g[field] = latest_instant(f.get(field), on)

    shares = latest_instant(f.get("shares"), on)
    if shares is None:
        shares = latest_instant(f.get("shares_alt"), on)
    g["shares"] = shares

    debt = 0.0
    seen_debt = False
    for field in ("lt_debt", "st_debt", "st_borrow"):
        v = latest_instant(f.get(field), on)
        if v is not None:
            debt += v
            seen_debt = True
    g["total_debt"] = debt if seen_debt else None

    for field in ("gross_profit", "ebit", "net_income", "dda", "cfo", "capex",
                  "buybacks", "dividends", "interest_expense"):
        g[field] = ttm(f.get(field), on)

    rev = ttm(f.get("revenue"), on)
    if rev is None:
        rev = ttm(f.get("revenue_alt"), on)
    if rev is None:
        rev = ttm(f.get("revenue_alt2"), on)
    g["revenue"] = rev

    g["fcf"] = None
    if g["cfo"] is not None:
        g["fcf"] = g["cfo"] - (g["capex"] or 0.0)

    g["ebitda"] = None
    if g["ebit"] is not None:
        g["ebitda"] = g["ebit"] + (g["dda"] or 0.0)

    # A year earlier, for the two change-over-time metrics. Same guard: what
    # was public then, not what we know now.
    a_year_ago = on - timedelta(days=365)
    g["gross_profit_prior"] = ttm(f.get("gross_profit"), a_year_ago)
    g["assets_prior"] = latest_instant(f.get("assets"), a_year_ago)
    prior_shares = latest_instant(f.get("shares"), a_year_ago)
    if prior_shares is None:
        prior_shares = latest_instant(f.get("shares_alt"), a_year_ago)
    g["shares_prior"] = prior_shares

    return g
