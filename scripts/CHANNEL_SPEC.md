# Channel study — pre-registration

**Written 20 September 2026, before any result existed.** Same discipline as
`VALUE_SPEC.md` and `PAIRS_SPEC.md`: the rules and the conditions for
declaring failure are written down first, so the study cannot be quietly
adjusted into a pass afterwards.

## 0. What is being asked, and what the answer is likely to be

The proposal was a scanner for "channeling" stocks, built from a linear
regression channel plus ADX. Two problems have to be settled before any of
it can run, because as written the scanner tests for the opposite of what it
is looking for.

**Problem one: R² measures the wrong thing.** A high R² on a regression of
price against time means the price has moved in a straight line — that is a
*clean trend*, not a range. A stock oscillating between support and
resistance has a nearly flat fitted line and a low R². Ranking by high R²
selects trending stocks and calls them channels.

**Problem two: ADX points the wrong way too.** ADX measures trend strength
without regard to direction. A range-bound stock has a *low* ADX. Requiring
a high one selects, again, trends.

Both mistakes point the same way, which is what made them hard to see: the
scanner is a competent trend scanner wearing a range scanner's label. This
study takes the stated intent — range-bound, mean-reverting stocks — and
tests that.

The published record on the intent is not encouraging. Lo and MacKinlay
(1988) found negative serial correlation in individual US equity returns,
and attributed a large part of it to nonsynchronous trading and bid–ask
bounce rather than to anything tradeable. Lehmann (1990) found weekly
reversal profits that largely disappeared once realistic spreads were
charged. Avellaneda and Lee (2010) found statistical arbitrage returns in US
equities declining sharply after 2002. **The expected answer is no edge
after costs.** We run it because the machinery exists, because the question
is worth settling, and because a negative answer arrived at properly is
worth more than a guess.

**Nothing from this study goes on the site unless every check in §7 passes.**

## 1. Definition

A stock is **channeling** over a window if its log price is *trend
stationary* over that window: it moves around a level that is flat or
slowly drifting, and departures from that level are pulled back rather than
persisting.

That is a testable statement, which "it looks like it is in a channel" is
not.

## 2. Prerequisite: the price module does not carry high and low

`value_prices.fetch_prices` returns dates, closes and volumes. ADX is built
from True Range and directional movement, both of which need the session
high and low, so **ADX cannot be computed from today's data at all.**

Stooq and Nasdaq both already return high and low in the responses the
module parses and then discards. The fix is an additive `fetch_ohlc`
alongside `fetch_prices`, leaving the existing function's return shape
untouched — `fetch_prices` is on the daily screen's path, and this study
must not be able to break the thing that runs every night.

If that work is not done, §5's ADX filter is dropped and the study runs
without it. The tests in §4 do not depend on it.

## 3. Universe

Formed once per formation window, from the same SEC filer/ticker map the
value screen uses.

| Rule | Value | Reason |
|---|---|---|
| Median daily dollar volume | ≥ $5m | half the pairs floor. The primary strategy here is long-only and needs no borrow, so the constraint is spread, not shortability |
| Minimum price | ≥ $5 | avoids the widest relative spreads, and §4's bid–ask concern is worst in cheap names |
| Price history | ≥ 378 sessions at formation | 252 formation + 126 trading |
| Excluded | SIC 6000–6799 | as the other two studies: banks, insurers, REITs |

## 4. The test for range-boundedness

Two statistics, computed on the formation window only. **Both must pass.**
Two statistics that are allowed to disagree are a check; one is an opinion.

**(a) Variance ratio, Lo–MacKinlay, heteroskedasticity-robust.** If log
price is a random walk, the variance of a q-period return is q times the
variance of a one-period return, so VR(q) = 1. VR(q) < 1 is mean reversion.
The robust z-statistic is used, not the homoskedastic one; equity volatility
clusters, and the homoskedastic version over-rejects when it does.

Computed at q = 5 and q = 10, on a **weekly** (5-session, non-overlapping)
base return.

The weekly base is not a detail. Bid–ask bounce induces negative serial
correlation in daily closing prices *mechanically*, with no mean reversion
present: a close that alternates between bid and ask looks like a stock that
keeps reverting. On daily returns this pushes VR below 1 for reasons that
cannot be traded. Sampling weekly does not remove the effect but shrinks its
share of the measured variance by roughly the ratio of the intervals. The
daily figures are computed too, and reported beside the weekly ones, so the
size of the gap between them is visible rather than assumed away.

**(b) Augmented Dickey–Fuller on log price, trend-stationary variant.**
`adfuller(log_px, regression="ct")`, rejecting the unit root at 5%.

A note on why this is the right test here when it was the wrong one in the
pairs study: there, the series tested was a *residual* whose hedge ratio had
been estimated from the same data, which biases the plain ADF toward
rejection and is why `coint` and MacKinnon's critical values were used
instead. Here the series is the observed log price itself. Nothing has been
estimated out of it, so the standard critical values are the correct ones.

**(c) A cap on the fitted slope.** Added 20 September 2026, before any run,
because (b) as first written let back in exactly what §0 says is the error.

`regression="ct"` tests the unit root against a *trend-stationary*
alternative. A stock climbing in a clean, tight line is trend stationary.
It rejects the unit root, it passes (b), and it is the high-R² trending
stock this whole document exists to exclude. The variance ratio catches the
strong cases — a deterministic trend plus noise has its long-horizon
variance dominated by the trend, so VR comes out above 1 — but a mild trend
with mean-reverting residuals passes both tests and is not a range.

So the fitted centre line must also be close to flat:

> |slope| × 252 ≤ 1.0 × the residual standard deviation

In words: over the whole formation window the centre line may not move by
more than one band half-width. A series that drifts further than that is
trending, whatever the ADF says about its residuals.

The distribution of |slope| × 252 / σ is reported across all candidates, so
the cut can be seen rather than taken on faith, and the count that (b)
passes but (c) rejects is reported separately — that number is the size of
the loophole.

**Not used: the Hurst exponent.** It is the obvious third candidate and it
is left out deliberately. Rescaled-range estimates on 252 observations carry
severe small-sample bias and no usable sampling distribution at that length;
a Hurst of 0.45 on a year of daily data is not evidence of anything. Adding
it would add the appearance of rigour and none of the substance.

## 5. Channel geometry and the ADX filter

Fitted on the formation window, applied to the trading window, never
refitted on data a trade can see.

- Centre line: OLS of log price on session index over the formation window,
  giving intercept α and slope β.
- Half-width: k × the standard deviation of the residuals, k = 2.
- **Through the trading window the line is projected, not refitted.** The
  session index keeps counting, so the centre on trading day t is
  α + β·(252 + t) and the bands stay at ±2σ around it, with α, β and σ all
  frozen at formation. Refitting on the trading window would be look-ahead;
  holding the line flat instead would be a different and unstated model.
  §4(c) is what keeps this extrapolation honest: with the slope capped, the
  centre can move at most about half a band-width across the 126 sessions,
  so the projection is bounded by construction rather than by hope.
- Everything above is in log space, including the ±2σ bands, so an entry
  test compares ln(close) against the band and never a raw price against a
  log-space bound.
- ADX(14), **Wilder smoothing** — the recursive
  `prev - prev/n + current` form, not `.rolling(n).mean()`, which is a
  different and shorter-memoried filter that will not agree with any chart
  the reader compares it against. Wilder's form is recursive, so it needs a
  warm-up before it means anything: prices are pulled from **60 sessions
  before** the formation window starts and the ADX reading used is the one
  at the formation window's end, by which point the recursion has long
  since forgotten its seed. Pre-registered as a *filter*, not a test:
  a candidate is dropped if ADX at the end of the formation window exceeds
  25. No p-value is claimed for it, because it does not have one.

## 6. Trading rules

Formation 252 sessions, trading the 126 immediately after, non-overlapping —
the same shape as the pairs study, so the two are comparable.

**Primary, long only, no borrow:**

- Enter when the close crosses below the lower band.
- Exit at the centre line, or at the end of the trading window.
- Stop out if the close falls a further 1 residual sd below the lower band.
  That is the channel breaking, and a strategy that will not admit a broken
  channel is not a channel strategy.
- One position per symbol per trading window. No pyramiding, no re-entry.
- Costs: 20bp round trip. No borrow, because nothing is shorted.

**Secondary, short the upper band:** identical rules mirrored, plus 100bp
annualised borrow, reported separately and never pooled with the primary.
A dollar-volume floor of $10m applies to the short side alone.

## 7. Falsification — all must pass

Applied mechanically by the script. Any failure means the screen is not
supported and does not go on the site.

**1. It beats a matched control.** This is the check that matters, and it is
the one this kind of scanner usually lacks.

"Buy a stock after a two-sigma drop and hold twenty sessions" makes money in
a rising market whether or not the stock was in a channel. So the question is
never "did these trades make money". It is whether the channel test *added*
anything.

For every trade taken on a stock that passed §4, a control trade is taken
under the identical entry rule, in the same formation window and the same
sector, on a stock that **failed** §4. Mean return on the passing set minus
mean return on the control set must be positive, with a 95% date-block
bootstrap interval, resampling whole formation windows, that excludes zero.

If the difference is zero, the channel test is decoration and the screen is
a dressed-up dip buyer.

**2. Positive after costs on its own terms.** Mean return per trade > 0.

**3. The bid–ask check.** The result must survive with the weekly-sampled
variance ratio, not only the daily one. If it holds on daily and fails on
weekly, the finding is microstructure and is reported as such.

**4. Not one period.** Dropping the single best formation window leaves the
control-adjusted difference positive, and the median formation window's
contribution is positive.

**5. Holds out of sample.** Formation windows from 2023-01-01 onward are a
holdout, untouched while the method is settled. The control-adjusted
difference there must also be positive.

**6. Not one sector.** No single sector may account for more than 40% of
trades, and the control-adjusted difference must stay positive with the
largest-contributing sector removed.

**7. Enough trades, and not a lottery.** At least 200 trades in the main
period and 30 in the holdout, and a hit rate above 50% — so the result is
not three enormous winners carrying a set of losses.

## 8. Multiple comparisons

Every formation window tests every eligible symbol. Benjamini–Hochberg
false-discovery-rate control at q = 0.05 is applied to the ADF p-values
within each formation window; only symbols surviving it are eligible.

The uncorrected count is reported alongside, so the size of the correction
is visible rather than implied.

## 9. What this study cannot tell you

Prices exist only for companies that still carry a ticker today. A stock
that broke down through its channel and was delisted before today is absent
from the universe entirely — and a strategy whose entry rule is "buy after a
fall" would have been hurt by precisely those. This bias runs **in favour**
of the strategy, as it does in the other two studies. Any positive result
here is an upper bound.

Closing prices also cannot say whether an entry was fillable. A close below
the lower band is assumed to be tradeable at that close; in a fast move it
would not have been.

## 10. What passing every check would and would not establish

Worth stating plainly, because the natural reading of §7 is "seven hurdles,
clear them all and the thing is proven", and that is not what this can do.

Clearing §7 would mean: on companies that still carry a ticker today,
priced at the close, over this sample, the range test added return beyond a
matched dip-buying control after the costs assumed in §6.

It would not mean the strategy works. §9 gives two reasons standing in the
way, and neither is fixed by adding more checks. The universe cannot include
a company that broke down and was delisted, and those are disproportionately
the trades that would have hurt, so a positive number is an upper bound, not
an estimate. And a close below the band is assumed fillable at that close,
which in a fast move it was not.

A pass here earns a second study on better data. It does not earn money.

## Change log

**20 September 2026 — before the first run.** Three changes, all prompted by
reading the spec again rather than by any result, since none exists yet.

1. Added §4(c), the slope cap. `regression="ct"` admits trend-stationary
   series, and a clean uptrend is trend stationary — so the ADF test as
   first written let through the exact failure §0 was written about. The
   variance ratio catches the strong cases but not mild ones.
2. §5 now says the centre line is *projected* through the trading window as
   α + β·t rather than leaving it ambiguous between projecting and holding
   flat, and states that the bands live in log space.
3. §5 now records the 60-session warm-up ADX needs before Wilder's
   recursion means anything.

Any change made after the first run against real data must be recorded here
with the reason, the date, and the run that prompted it.
