# The Vance Report value screen — pre-registration

**Frozen:** 19 September 2026, before any backtest of these rules was run.
**Why this file exists:** a threshold chosen after seeing the result is not a
finding, it is a story. Everything below is fixed in advance. If a rule changes
after the first test, the change is recorded at the bottom with its date and
reason, and the old rule stays visible.

---

## 0. What this screen claims

It ranks companies by how cheap they are relative to what they earn and own,
adjusted for whether the business is any good and whether it is likely to
survive. It does **not** predict prices. A high rank means the name is worth
reading about, and nothing more.

---

## 1. Universe

Evaluated at each rebalance date `T` using only information a person could
have had on the morning of `T`.

| Rule | Value |
|---|---|
| Listing | US common stock, NYSE / Nasdaq / AMEX |
| Filings | Has a CIK and at least 4 quarterly filings on record, the latest **filed** no more than 200 days before `T` |
| Market cap | ≥ $300,000,000 |
| Liquidity | Median daily dollar volume over the prior 60 sessions ≥ $2,000,000 |
| Price | ≥ $3.00 |
| Price history | ≥ 252 sessions before `T` |
| Excluded sectors | SIC 6000–6799 (banks, insurers, REITs, funds) — enterprise-value metrics do not describe these balance sheets |
| Excluded | Any company whose latest filing is a first filing after an IPO less than 12 months before `T` |

Market cap at `T` = (shares outstanding from the most recent filing **filed on
or before `T`**) × (close on the last session at or before `T`).

## 2. The look-ahead rule

A financial fact may be used at `T` only if its **filing date** (`filed` in the
SEC data, not the period end) is on or before `T`. No exceptions, no grace
period, no estimated-availability heuristic. Where two filings cover the same
period, the earlier-filed one is used, so a later restatement never leaks
backwards.

## 3. The four pillars

Every metric is converted to a **percentile within its sector** (GICS-style
grouping derived from SIC), 0–100, higher = better, computed across the
eligible universe at `T` only. A sector with fewer than 20 eligible names at
`T` is ranked against the whole universe instead.

Trailing-twelve-month (`ttm`) means the sum of the four most recent quarterly
values whose filings are available at `T`.

`EV` = market cap + total debt − cash and equivalents.

### Cheapness — weight 0.40

| Metric | Direction |
|---|---|
| EV / EBIT (ttm) | lower better |
| EV / free cash flow (ttm), FCF = operating cash flow − capex | lower better |
| EV / revenue (ttm) | lower better |
| Shareholder yield = (buybacks + dividends, ttm) / market cap | higher better |

A negative or zero denominator (loss-making, cash-burning) scores the **worst
percentile**, not "excluded". A screen that lets loss-makers skip the
cheapness test is not a value screen.

### Quality — weight 0.30

| Metric | Direction |
|---|---|
| Gross profitability = gross profit (ttm) / total assets | higher better |
| ROIC = EBIT(ttm) × 0.79 / (total debt + equity − cash) | higher better |
| Accruals = (net income − operating cash flow, ttm) / total assets | lower better |
| Change in gross profitability vs the same quarter a year earlier | higher better |

### Safety — weight 0.20

| Metric | Direction |
|---|---|
| Net debt / EBITDA (ttm); net cash scores best | lower better |
| Interest coverage = EBIT / interest expense | higher better |
| Net share issuance = change in shares outstanding over the trailing year | lower better |
| Working capital / total assets | higher better |
| Retained earnings / total assets | higher better |

### Confirmation — weight 0.10

| Metric | Direction |
|---|---|
| 12-1 momentum: return from `T`−252 to `T`−21 sessions | higher better |
| Close at `T` ÷ 200-session moving average | higher better |

**This pillar is why the screen does not buy into a fall.** It is deliberately
the smallest weight: it is there to stop the screen catching a falling knife,
not to chase a rally.

### Composite

```
score = 0.40·Cheapness + 0.30·Quality + 0.20·Safety + 0.10·Confirmation
```

A pillar's score is the mean of its available metric percentiles. A name needs
**at least half the metrics in every pillar** to be scored at all; otherwise it
is unranked and reported as unranked, never silently dropped.

Ties break by the Safety pillar, then by liquidity.

## 4. Output

The full ranked list, every session. The site shows the top 20. Every name
displays its four pillar scores, so a reader can see the trade-off that put it
there. Names are never hidden because they look bad.

## 5. How this will be judged

Fixed in advance, so the test cannot be graded on a curve:

1. **Decile spread is the headline.** Sort the universe by composite at each
   rebalance, form ten equal buckets, hold 3/6/12 months, equal weight. The
   claim is supported only if decile 1 beats decile 10 **and** the table as a
   whole is ordered — Spearman ρ between decile number and decile mean of
   −0.6 or stronger. A good top decile with a random middle is noise that
   happened to land well. *(Amended 19 Sep 2026; see the change log.)*
2. **Benchmark** is the equal-weighted eligible universe at the same date, not
   an index. That removes size and sector drift from the comparison.
3. **Rebalance** monthly, first trading day.
4. **Period** 2010-01 to 2025-09. The last two years (2023-10 onward) are a
   **holdout**: reported separately and not looked at until the rest is frozen.
5. **Costs** 0.5% round trip on names ≥ $1bn, 1.0% on $300m–$1bn.
6. **Survivorship** is handled, not assumed away — see §6.
7. **Significance** by date-block bootstrap, because names picked on the same
   date are not independent observations.

### What would falsify it

- Decile 1 does not beat decile 10 after costs, or
- the decile table is not ordered (ρ weaker than −0.6), or
- decile 1's mean excess has a date-block bootstrap interval touching zero, or
- the result depends on a single year, or
- the result disappears in the holdout, or
- the result disappears under the mid-case delisting assumption.

Any of those and the screen does not go on the site as a ranked
recommendation. It may still go on as a description of what companies look
like, which is a different and honest claim.

## 6. Survivorship

The universe at `T` is built from the SEC **Financial Statement Data Sets**,
which contain every filer for that quarter — including companies that have
since been delisted, acquired or wound up. The universe is therefore
point-in-time and complete.

Prices for dead companies are the hard part, and free data does not fully
solve it. So the bias is **bounded rather than ignored**: when a name in the
universe has no price data covering its holding period, the run reports it and
the result is recomputed under three assumptions for those names —

| Case | Assumed return over the holding period |
|---|---|
| Optimistic | performs like the equal-weighted universe |
| **Mid (the headline)** | **−50%** |
| Pessimistic | −100% |

— and the headline number is the mid case. If a conclusion only survives the
optimistic case, it is reported as not surviving.

---

## Changes after freezing

**19 Sep 2026 — the gradient test in §5.1.**

*Superseded rule:* "the gradient across the deciles is broadly monotone",
implemented as the share of adjacent decile pairs that step the right way,
with a pass at ≥70%.

*New rule:* Spearman rank correlation between decile number and decile mean,
with a pass at **ρ ≤ −0.6**. The pairwise figure is still printed, but it no
longer decides anything.

*Reason:* adjacent deciles hold neighbouring scores and are not statistically
distinguishable from one another, so the order of any given pair is close to a
coin flip even when the table as a whole is clearly ordered. On a synthetic
universe built with a **known** planted signal, the pipeline recovered a
+12.2pp decile-1-minus-decile-10 spread with a visibly descending table, and
the pairwise measure scored it 56% — it would have failed a screen that was
working perfectly. Spearman asks the question the claim actually makes, which
is whether the whole ranking is ordered.

*When:* made against synthetic data, **before any run against real market
data**. No real result had been computed under either rule at the time of the
change. The new gate is not looser: it still rejects a table where one good
decile sits above a random middle, which is the failure mode §5.1 was written
to catch, and that case is in the test suite.
