# The daily record — specification

**Frozen before the code exists**, 20 September 2026, following the practice of
`VALUE_SPEC.md` and `PAIRS_SPEC.md`. Changes after this date go in §11 with a
date and a reason.

---

## 0. What this is, and what it refuses to be

A record of what happened in the market on the previous session, published every
weekday, built entirely from numbers this site computed itself.

It is a report. It contains no forecast, no explanation and no advice.

That is a build constraint, not a style note. The failure mode of every
automatically generated market note is that it drifts into causation — "stocks
fell on rate worries" — which is a claim about *why*, and nothing in our data
supports a claim about why. We can measure that prices moved. We cannot measure
the reason, and we will not imply one.

## 1. How the page is written

Two layers, and the second can always fall back to the first.

**1.1 The facts layer** is deterministic. A script computes every figure from
the price data and writes them into a structured file, `record/facts/<date>.json`.
Nothing interprets anything. This file is the only input to what follows, and
it is published alongside the page so a reader can check the arithmetic.

**1.2 The prose layer** is generated. A model receives the facts file and
nothing else — no news, no context, no memory of previous days — and writes the
page in the site's voice. It is explicitly instructed that it is describing, not
explaining.

**1.3 The template layer is the floor.** The same facts also render through a
fixed template, the way `narration.py` already does for the video channel. If
the model is unavailable, or its draft fails validation twice, **the template
version publishes instead.** The page is never skipped for want of prose, and
the prose is never published unvalidated.

Each page states which layer produced it.

## 2. The validator

A draft is rejected — and on second rejection the template ships — if any of
the following is true.

**2.1 Banned language.** The draft contains a causal connective (because, amid,
as, on, following, driven by, led by, in response to, after news of), a
forward-looking verb (will, should, expects, poised, set to, likely, anticipate),
an undefined magnitude adjective (sharp, heavy, modest, strong, sell-off, rally,
plunge, surge, tumble), or a reference to sentiment (fear, confidence, optimism,
appetite, nerves).

**2.2 An unsupported number.** *This is the important one.* Every numeric
literal in the draft must appear in the facts file, or be derivable from it by
a stated rounding. A model that invents a figure is a worse problem than one
that writes an awkward sentence, and a banned-word list would never catch it.
The check is exact: extract every number from the prose, match each against the
fact set, fail on any orphan.

**2.3 A name not in the data.** Every ticker or company mentioned must appear in
that day's screen output.

The banned list and the numeric check live in `test_daily_record.py`, not in
the generator, so weakening either requires editing a test — which shows up in
review rather than in a quiet commit.

## 3. What the page reports

All figures computed from the price histories the daily screen already fetches.
Nothing quoted from a third-party market summary.

**3.1 The session.** The trading session covered, not the publication date.

**3.2 Breadth, from our own universe.** Of the companies priced: how many closed
higher, lower and unchanged, the median one-day return, and each as a share of
the universe. This is breadth computed over roughly a thousand names by us, not
a number taken from elsewhere, and it is the most defensible thing on the page.

**3.3 Dispersion.** Interquartile range of one-day returns, and the count of
names moving more than 5% each way. The 5% threshold is fixed here so no day's
report can pick a threshold that makes the day look more dramatic than it was.

**3.4 Index reference.** One-day change for SPY, QQQ and IWM, through
`value_prices.py` like everything else. Arithmetic only.

**3.5 The screen.** How many companies met every condition. **If the answer is
zero, the page says zero.** The premise check found the old screen fired on
roughly one day in two; a page that never admits an empty day is lying by
omission. Where there are names, they are listed with their one-day move and
their rank.

**3.6 Method link.** One line at the foot, never model-written, linking to the
method page, which states what the record measures. One line and not a
paragraph: a caveat restated at length every day for a year stops reading as
candour and starts reading as an apology. The limit is stated properly once,
where someone can read it, rather than repeatedly where nobody will.

## 4. When it does not publish

The daily screen already refuses to commit a thin file — fewer than 200 price
histories or 50 eligible companies and it leaves yesterday's alone. The record
inherits that and adds to it. Nothing is written when:

1. Fewer than 200 companies were priced for the session.
2. Any of SPY, QQQ or IWM failed to return a close.
3. The session date cannot be established from the price data.
4. The file being written is not newer than the previous session's.

On any of these the job exits without writing, logs the reason, and the site
shows the last good record with its own date visible. **A missing day is honest.
A day assembled from partial data is not.**

Weekends and market holidays produce no page, and no page pretends otherwise.

## 5. Where it lives

Its own section at `/record/`, with its own index, separate from Field Notes.
Field Notes holds research; this holds daily arithmetic. Mixing them would bury
five distinctive pieces under two hundred and fifty short ones a year.

Each day is permanent at `/record/YYYY-MM-DD.html`. The index lists the most
recent sixty, with an archive beyond.

## 6. What the record measures

One session: what the universe did, and what the screen produced that day. It
is a measurement, not a performance history, because a day is not a horizon.

The screen's measured performance is a separate question with a separate
answer, and `VALUE_SPEC.md` is where it is answered. The record points there
and does not relitigate it.

Stated once, on the method page. Not restated daily. Publishing a factual daily
record of your own universe is an unusual thing to do; presenting it as a
confession would misdescribe it.

## 7. Cadence, cost and access

Runs weekdays after `value_daily.yml`, which finishes around 22:10 UTC. It reads
that job's output rather than fetching prices twice.

The prose layer needs a model API key held as a GitHub encrypted secret. It is
set in GitHub's secret field directly and appears in no file, commit or message.
Roughly 250 runs a year at a few thousand tokens each; the cost is small but it
is not zero, and it is a new external dependency the site did not have before.

This is the only credential the record introduces. It reads a model and writes
a file. It cannot reach any public account — see §8.5.

`permissions: contents: write`, scoped to the record directory and its index.

## 8. Syndication to social platforms

Social posts carry a snippet and a link. The full record is never reproduced
off-site: the permanent page at `/record/YYYY-MM-DD.html` is the only complete
version, and every post links to it as its canonical source.

**8.1 The honesty rule is stricter here, not looser.** A post is a fragment
that travels without its context. It gets screenshotted, quoted and reshared
with the link stripped off. So the test for a snippet is not "is this true" —
everything on the page is true — but **"is this still honest with the context
removed."**

Two examples of the difference, because it is the whole point of this section:

- *"Three of today's screened names closed up more than 8%."* True. Also a
  performance claim the moment it is separated from the page explaining that
  one day is not a result and that the backtest found a median six-month excess
  of +0.24pp. **Not permitted.**
- *"Of 1,014 companies priced today, 388 closed higher. Full record: <link>"*
  True, and still true standing alone. **Permitted.**

**8.2 What a snippet may contain.** Breadth, dispersion, index arithmetic, the
count of names meeting the screen, and the date. Plus the link.

**8.3 What a snippet may not contain.** The one-day move of any individual
screened name, or any figure about how screened names performed. Those stay on
the page, where the surrounding context is attached to them. A reader who wants
them follows the link, which is the purpose of the link.

**8.4 The §2 validator applies unchanged** — banned language, and every numeric
literal matched against the facts file. A snippet that fails is not posted, and
failure to post is never a reason to publish an unvalidated one.

**8.5 Nothing posts automatically.** The build generates the snippet; a person
reviews it and posts it. No platform API is called, no posting credential
exists, and there is no path from a scheduled job to a public account. This is
a deliberate limit, not a stage on the way to automation: the snippet rules in
8.1–8.3 are judgement calls at the edges, and a human reading each one before
it goes out is a better check than any test.

It also means the frequency question answers itself. A person posting by hand
will not post a near-identical factual message every weekday, which is the
behaviour that reads as automation to readers and as spam signal to platforms.

**8.6 Where the snippet appears.** In `record/facts/<date>.json` as a `snippet`
field, and rendered at the foot of that day's record page. It is published
rather than held privately, for the same reason the arithmetic is: a reader can
check that what was posted elsewhere matches what the page says.

**8.7 Platform notes, for whoever is posting.** X and Facebook take the snippet
as written. Instagram's publishing path takes only images and video, so a text
snippet has nowhere to go there. Substack has no publishing API and never did;
anything there is written by hand regardless.

## 9. What would make this worth stopping

Decided in advance, so it is not decided in the moment:

- A causal or forward-looking claim reaches the page and §2 did not catch it —
  the job is disabled until the test is fixed, and the page is corrected rather
  than quietly edited.
- A number reaches the page that is not in the facts file — same.
- The record is used in marketing to imply predictive skill — it comes down.
- A snippet is generated carrying an individual name's move — generation is
  disabled until §8.3 is enforced in code rather than by intention.
- A posting credential for any platform is added to this repository. §8.5 says
  there is no path from a scheduled job to a public account; a key in the
  secrets list is that path existing, whatever the intention behind it.

## 10. What passing all of this would establish

That the site publishes a factual daily record of its own universe, from its own
data, which does not overstate what it knows.

It would establish nothing about whether the screen works. That question belongs
to `VALUE_SPEC.md`, and the answer so far is weak.

## 11. Change log

_(empty at freeze)_
