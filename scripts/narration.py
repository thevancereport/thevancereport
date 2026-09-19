"""
The words the daily briefing says, and the words it puts on screen.

Kept separate from rendering on purpose: this file is the one a person reads
when they want to know what the channel is claiming. Nothing here decides what
to buy. Every figure it speaks is either read straight out of research.json or
is arithmetic on those figures, and the script says which.

Two strings per beat:

  say      what the narrator reads. Numbers are spelled out, because a speech
           engine reads "$1.92" as "dollar one point nine two".
  caption  what appears on screen. Numerals, as the site writes them.
"""

from __future__ import annotations

CASH_GATE = 30.0          # matches scripts/update_research.py
DROP_THRESHOLD = -15.0
DROP_WINDOW_DAYS = 3      # ditto; a fallback, the run's own value wins
MAX_NAMES = 5             # a briefing longer than this stops being watched


def drop_of(row: dict) -> float:
    """The move the verdict was made on.

    drop_pct arrived when the window became configurable. Files written before
    that only carry the five-day figure, and a briefing that reads zero because
    a field was renamed is worse than one that reads the old field.
    """
    value = row.get("drop_pct")
    if value is None:
        value = row.get("five_day_drop_pct")
    return value or 0.0


def window_of(row: dict) -> int:
    """How many sessions that move was measured over.

    A row with no drop_window_days key came out of a run made before the
    window was configurable, and its move is a five-session one. Defaulting to
    the current window there would have the narrator say "three sessions" over
    a five-session figure.
    """
    if "drop_window_days" not in row:
        return 5
    try:
        return int(row["drop_window_days"] or DROP_WINDOW_DAYS)
    except (TypeError, ValueError):
        return DROP_WINDOW_DAYS

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
        "eighty", "ninety"]


def _under_thousand(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        t, r = divmod(n, 10)
        return TENS[t] + ("-" + ONES[r] if r else "")
    h, r = divmod(n, 100)
    return ONES[h] + " hundred" + (" " + _under_thousand(r) if r else "")


def words(n: int) -> str:
    """Whole number to words. Big enough for share counts and market caps."""
    n = int(n)
    if n < 0:
        return "minus " + words(-n)
    if n < 1000:
        return _under_thousand(n)
    for scale, name in ((1_000_000_000, "billion"), (1_000_000, "million"),
                        (1_000, "thousand")):
        if n >= scale:
            head, rest = divmod(n, scale)
            out = words(head) + " " + name
            return out + (" " + words(rest) if rest else "")
    return str(n)


def money(value: float) -> str:
    """1.92 -> 'one dollar and ninety-two cents'."""
    neg = value < 0
    value = abs(value)
    dollars = int(value)
    cents = int(round((value - dollars) * 100))
    if cents == 100:
        dollars, cents = dollars + 1, 0
    if dollars and cents:
        s = f"{words(dollars)} dollar{'s' if dollars != 1 else ''} and {words(cents)} cents"
    elif dollars:
        s = f"{words(dollars)} dollar{'s' if dollars != 1 else ''}"
    else:
        s = f"{words(cents)} cent{'s' if cents != 1 else ''}"
    return ("minus " + s) if neg else s


def pct(value: float, places: int = 1) -> str:
    """44.8 -> 'forty-four point eight percent'."""
    neg = value < 0
    value = abs(value)
    whole = int(value)
    frac = round(value - whole, places)
    out = words(whole)
    if places and frac > 0:
        digits = f"{frac:.{places}f}".split(".")[1].rstrip("0")
        if digits:
            out += " point " + " ".join(ONES[int(d)] for d in digits)
    out += " percent"
    return ("down " + out) if neg else out


def big_money(value: float) -> str:
    """121743517 -> 'a hundred and twenty-two million dollars'."""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{words(round(value / 1_000_000_000))} billion dollars"
    if value >= 1_000_000:
        return f"{words(round(value / 1_000_000))} million dollars"
    return f"{words(round(value))} dollars"


def beat(say: str, caption: str) -> dict:
    return {"say": say, "caption": caption}


# ---------------------------------------------------------------------------


def cushion_ceiling(cps: float) -> float:
    """
    The price above which this name stops clearing the cash gate.

    The gate is net cash per share divided by price, at least 30%. Hold the
    cash constant and that is a price: cps / 0.30. It is arithmetic on the
    screen's own threshold, not a target and not a valuation.
    """
    return cps / (CASH_GATE / 100.0)


def build(research: dict, run_date: str) -> list[dict]:
    """
    Returns a list of slides. Each slide carries what to draw and the beats
    spoken over it, so one slide can hold several captions in turn.
    """
    names = [(sym, row) for sym, row in research.items()
             if not sym.startswith("_") and row.get("gate") == "PASS"]
    names.sort(key=lambda kv: kv[1].get("cash_cushion_pct") or 0, reverse=True)
    names = names[:MAX_NAMES]

    slides: list[dict] = []

    # ---- open ----
    slides.append({
        "kind": "title",
        "chapter": "Intro",
        "data": {"date": run_date, "count": len(names)},
        "beats": [
            beat("This is the Vance Report daily briefing for " + run_date + ".",
                 "The Vance Report — daily briefing"),
            beat(
                "Every trading day a screen runs across every listed U.S. common "
                "stock and looks for two things at once: a sharp recent fall, and "
                "a balance sheet holding more cash than the fall would suggest.",
                "One screen. Two conditions. Every listed US common stock."),
            (beat(
                f"Today {words(len(names))} name{'s' if len(names) != 1 else ''} "
                "cleared both conditions. Here is what the formula found, and what "
                "it did not.",
                f"{len(names)} cleared both gates today")
             if names else
             beat("Today no name cleared both conditions. That is a normal "
                  "outcome and the screen publishes nothing rather than lowering "
                  "the bar.",
                  "No name cleared both gates today")),
        ],
    })

    # ---- how the screen works ----
    win = window_of(names[0][1]) if names else DROP_WINDOW_DAYS
    slides.append({
        "kind": "method",
        "chapter": "How the screen works",
        "data": {"gate": CASH_GATE, "drop": DROP_THRESHOLD, "window": win},
        "beats": [
            beat(
                "The first condition is price. A stock has to have fallen fifteen "
                f"percent or more over {words(win)} sessions. That is the "
                "dislocation the screen is looking for.",
                f"1 — {win}-day move of −15% or worse"),
            beat(
                "The second is cash. The screen takes the cash on the company's "
                "most recent balance sheet, subtracts total liabilities, divides "
                "what is left by the share count, and asks whether that figure is "
                "at least thirty percent of the share price.",
                "2 — (cash − total liabilities) ÷ shares ÷ price ≥ 30%"),
            beat(
                "Liabilities are subtracted before the division. Cash on its own "
                "flatters a company that has borrowed to hold it.",
                "Liabilities come out first. Gross cash flatters debt."),
            beat(
                "Three further checks run behind those two: how long the cash "
                "lasts against the operating burn, how much the share count has "
                "grown in a year, and whether insiders have bought in the open "
                "market.",
                "Runway · dilution · insider buying"),
            # Said here rather than only in the disclaimer at the end, because
            # by the time the disclaimer runs the viewer has already heard five
            # tickers and made up their mind about them.
            beat(
                "And one thing to be clear about before any name is read out. "
                "Clearing this screen is not a recommendation to buy. It means "
                "two arithmetic tests came back clean and the name has earned a "
                "closer look. Nothing in it says the price is right, the business "
                "is sound, or the fall is finished. The screen finds candidates. "
                "The work after that is yours.",
                "Clearing is a reason to look, not a reason to buy"),
        ],
    })

    # ---- one block per name ----
    for rank, (sym, row) in enumerate(names, start=1):
        name = row.get("name") or sym
        price = row.get("price") or 0.0
        cps = row.get("cps") or 0.0
        cushion = row.get("cash_cushion_pct") or 0.0
        drop = drop_of(row)
        win = window_of(row)
        sector = row.get("sector") or "—"
        runway = row.get("runway_years")
        dilution = row.get("dilution_pct")
        gross = row.get("gross_cash_per_share") or 0.0
        liab = row.get("liabilities_per_share") or 0.0
        cap = row.get("market_cap") or 0
        buys = row.get("insider_buys") or 0
        buy_value = row.get("insider_value") or 0
        ceiling = cushion_ceiling(cps)

        why = [
            beat(
                f"Name {words(rank)}. {name}, trading as {spell(sym)}, "
                f"in {sector}, at {money(price)}.",
                f"{sym} — {name}"),
            beat(
                f"It qualified on price because it fell {pct(abs(drop))} over the "
                f"last {words(win)} sessions, against a threshold of fifteen.",
                f"{win}-day move {drop:+.1f}%  ·  threshold −15.0%"),
            beat(
                f"On cash: {money(gross)} a share of cash, less {money(liab)} a "
                f"share of total liabilities, leaves {money(cps)} of net cash "
                f"against a {money(price)} price.",
                f"${gross:,.2f} cash − ${liab:,.2f} liabilities = ${cps:,.2f} net"),
            beat(
                f"That is a net cash cushion of {pct(cushion)}, against a gate of "
                f"thirty. That single figure is why it is in this briefing.",
                f"Net cash cushion {cushion:.1f}%  ·  gate 30.0%"),
        ]

        if runway is not None:
            why.append(beat(
                (f"Its cash covers about {pct(runway, 1).replace(' percent', '')} "
                 f"years of the current operating burn."
                 if runway < 50 else
                 "It is cash generative on the most recent annual figures, so the "
                 "runway test does not bind."),
                (f"Runway {runway:.1f} years" if runway < 50 else "Cash generative")))

        if dilution is not None:
            why.append(beat(
                f"The share count is {pct(abs(dilution))} "
                f"{'higher' if dilution >= 0 else 'lower'} than a year ago, "
                f"against a ceiling of twenty-five.",
                f"Share count {dilution:+.1f}% year on year  ·  ceiling 25%"))

        if buys:
            why.append(beat(
                f"Insiders bought in the open market {words(buys)} "
                f"time{'s' if buys != 1 else ''} in the last six months, "
                f"{big_money(buy_value)} in total. Open-market purchases only — "
                "grants and option exercises are not counted.",
                f"{buys} open-market insider purchases · ${buy_value:,.0f}"))
        else:
            why.append(beat(
                "No open-market insider purchases were filed in the last six "
                "months. That is not a mark against it; it is simply absent "
                "evidence.",
                "No open-market insider purchases filed"))

        slides.append({
            "kind": "stock",
            "chapter": f"{sym} - why it cleared",
            "data": {
                "rank": rank, "symbol": sym, "name": name, "sector": sector,
                "price": price, "cps": cps, "cushion": cushion, "drop": drop,
                "runway": runway, "dilution": dilution, "market_cap": cap,
                "insider_buys": buys,
            },
            "beats": why,
        })

        # ---- the screen's own levels ----
        slides.append({
            "kind": "levels",
            "chapter": f"{sym} - the screen's levels",
            "data": {"symbol": sym, "price": price, "cps": cps,
                     "ceiling": ceiling, "cushion": cushion},
            "beats": [
                beat(
                    f"The levels the screen itself works from, for {spell(sym)}.",
                    f"{sym} — the screen's own levels"),
                beat(
                    f"{money(cps)} a share is the net cash. That is the figure "
                    "the whole test rests on, and it is the company's own number "
                    "from its own filing.",
                    f"Net cash per share  ${cps:,.2f}"),
                beat(
                    f"Divide that by the thirty percent gate and you get "
                    f"{money(ceiling)}. Above {money(ceiling)}, holding the cash "
                    f"constant, this name stops clearing the screen. At "
                    f"{money(price)} it sits below that line.",
                    f"Cushion holds below  ${ceiling:,.2f}   ·   now ${price:,.2f}"),
                beat(
                    "That is arithmetic on the screen's own threshold. It is not "
                    "a target, not a valuation, and not a price anyone is being "
                    "told to pay.",
                    "Arithmetic, not a target"),
            ],
        })

    # ---- horizon ----
    slides.append({
        "kind": "horizon",
        "chapter": "Horizon: 3 to 6 months",
        "data": {},
        "beats": [
            beat(
                "One thing about the horizon, because it decides whether any of "
                "this is useful to you.",
                "On horizon"),
            beat(
                "A cash cushion is not a catalyst. Nothing about a balance sheet "
                "tells you what a stock does tomorrow, or this week.",
                "A cash cushion is not a catalyst"),
            beat(
                "These names are looked at on a three to six month view. They are "
                "not intended for day trading and not intended for short-term "
                "trading of any kind.",
                "Three to six months. Not for day or short-term trading."),
        ],
    })

    # ---- disclaimer ----
    slides.append({
        "kind": "disclaimer",
        "chapter": "Disclaimer",
        "data": {},
        "beats": [
            beat(
                "And the part that matters most. This is opinion and education. "
                "It is not investment advice.",
                "Opinion and education. Not investment advice."),
            beat(
                "The Vance Report is not a registered investment adviser and not "
                "a broker-dealer. Nothing here is a recommendation to buy or sell "
                "anything. A name clearing the screen is a name to look at, not "
                "a name to buy.",
                "Not advice. A name to look at, not a name to buy."),
            beat(
                "The stocks this screen surfaces have fallen hard for reasons the "
                "screen does not evaluate. They are volatile, they are high risk, "
                "and they can go to zero.",
                "High risk · volatile · total loss of principal is possible"),
            beat(
                "Figures come from company filings and can be stale or wrong. "
                "Check them yourself before acting on anything in this video.",
                "Verify every figure before acting"),
            beat(
                "The full screen and the full disclaimer are at the vance report "
                "dot com.",
                "thevancereport.com"),
        ],
    })

    return slides


def spell(symbol: str) -> str:
    """A ticker read as letters: 'EQ' -> 'E Q'."""
    return " ".join(symbol.upper())


def plain_script(slides: list[dict]) -> str:
    """The whole narration as prose, for the video description."""
    return "\n\n".join(b["say"] for s in slides for b in s["beats"])
