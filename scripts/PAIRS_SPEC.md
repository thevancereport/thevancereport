# Pairs study — pre-registration

**Written 20 September 2026, before any result existed.** Same discipline as
`VALUE_SPEC.md`: the rules and the conditions for declaring failure are set
down first, so the study cannot be adjusted into a pass afterwards.

## 0. Why this is a study and not a screen

The published record on this strategy is not encouraging. Do and Faff found
distance-method pairs trading peaked in the 1970s and 80s, declined through
the 1990s, and — once realistic time-varying trading costs were applied —
was **on average not profitable**. Of 29 portfolios they built, 4 still made
money, at roughly 28bp a month. Most of what survived after 1989 came from
the 2000–2002 bear market rather than from steady returns.

So the expected outcome here is "no edge". We are running it because
cointegration-based pairs is a variant of what they tested, because the
machinery already exists, and because a negative answer arrived at properly
is worth more than a guess. **Nothing from this study goes on the site
unless every check in §5 passes.**

## 1. Universe

Formed once per formation period, from the same SEC filer/ticker map the
value screen uses.

| Rule | Value | Reason |
|---|---|---|
| Median daily dollar volume | ≥ $10m | five times the value screen's floor. This strategy requires shorting, and traded value is what actually governs whether a borrow exists and what it costs — more directly than market capitalisation does, which is why no separate cap floor is applied |
| Minimum price | ≥ $5 | avoids the widest relative spreads |
| Price history | ≥ 378 sessions at formation | 252 formation + 126 trading |
| Sector | SIC-mapped, per `value_core.sector_of` | see §2 |
| Excluded | SIC 6000–6799 | as the value screen: banks, insurers, REITs |

## 2. Pairs are formed within sector only

Two reasons, one economic and one statistical.

Economically, cointegration between two companies should come from shared
fundamentals — the same input costs, the same customers, the same cycle. A
pair drawn from unrelated sectors that passes a cointegration test has most
likely passed it by coincidence.

Statistically, restricting to sectors cuts the number of tests by roughly an
order of magnitude, which directly reduces the multiple-comparison problem
described in §4.

## 3. Method

**Formation window:** 252 trading days.
**Trading window:** the 126 sessions immediately after, never overlapping it.

Within each formation window, for every same-sector pair (A, B):

1. Regress log price A on log price B by OLS. The slope is the hedge ratio β.
   Logs, not levels, so the hedge ratio is a proportion rather than a
   dollar amount and the spread is scale-free.
2. Spread = log(A) − β·log(B). Record its mean μ and standard deviation σ
   **over the formation window only**.
3. Test the spread for a unit root with `statsmodels.tsa.stattools.coint`,
   which applies MacKinnon critical values appropriate to a residual whose
   β was estimated. Plain `adfuller` on the residual uses the wrong null and
   is biased toward finding cointegration; this study does not use it.

**Trading.** In the trading window, z is computed with the **formation**
period's μ and σ — never recomputed on data the trade can see.

- Enter when |z| ≥ 2.0: short the rich leg, long the cheap leg, equal dollar
  amounts at entry.
- Exit when z crosses 0, or at the end of the trading window, whichever is
  first.
- One position per pair per trading window. No pyramiding, no re-entry.
- A leg that stops pricing during the window (delisting) closes the trade at
  the last available price, and that case is counted separately.

## 4. Multiple comparisons

Every formation window tests thousands of pairs. At p ≤ 0.05 with no
correction, one test in twenty passes on noise alone; on 20,000 pairs that
is a thousand false positives, which is more than the number of real
signals we could plausibly expect.

Benjamini–Hochberg false-discovery-rate control is applied **within each
formation window**, at q = 0.05. Only pairs surviving BH are eligible to
trade.

The uncorrected count is reported alongside, so the size of the correction
is visible rather than implied.

## 5. Costs

Charged on **both legs**, at entry and exit, plus a borrow cost on the short
leg for the days held.

| Component | Assumption |
|---|---|
| Round-trip cost per leg | 20bp |
| Legs per trade | 2 |
| Total trading cost per trade | 80bp |
| Short borrow | 100bp annualised on the short leg's notional |

These are deliberately unkind. Do and Faff's central finding is that this
strategy dies at realistic costs, so a version of it that only works at
optimistic costs has not answered the question.

## 6. Falsification — all must pass

Applied mechanically by the script. Any failure means the strategy is not
supported and does not go on the site.

1. **Positive after costs.** Mean return per trade > 0.
2. **Not a fluke.** The 95% confidence interval on mean trade return,
   from a date-block bootstrap resampling whole formation windows, excludes
   zero.
3. **Not one period.** Excluding the single best formation window, mean
   return per trade remains > 0.
4. **Holds out of sample.** Formation windows from 2023-01-01 onward are a
   holdout, untouched while the method is settled. Mean trade return there
   must also be > 0.
5. **Survives doubled costs.** With trading costs and borrow both doubled,
   mean return per trade remains > 0.
6. **Enough trades to mean anything.** At least 200 trades in the main
   period, and at least 30 in the holdout.

## 7. What this study cannot tell you

The same irreducible limit as the value backtest: prices are only available
for companies that still carry a ticker today, so pairs where a leg was
delisted before today are absent from the universe entirely. Since one
plausible way a pairs trade loses badly is a leg going to zero, this bias
runs **in favour** of the strategy. Any positive result here is an upper
bound.

It also assumes both legs are continuously shortable at the stated borrow
cost, which is optimistic — borrow availability tightens exactly when
spreads widen.

## Change log

Nothing yet. Any change made after the first run against real data must be
recorded here with the reason and the date, and the run that prompted it
named.
