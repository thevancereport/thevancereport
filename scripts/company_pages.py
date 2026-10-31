"""One static page per company, plus an A-Z index, a sitemap and a StockTwits feed.

Runs after each night's screen (inside the Track record job). Writes:

<symbol>-stock.html   one page per company in the top PAGE_TOP of the ranking,
                      and for every company that already has a page (so a page
                      never goes stale once Google has it). Plain HTML with the
                      numbers in the page itself, so search engines can read it;
                      the interactive profile stays at stock.html?symbol=.
stocks.html           every company page, A to Z, with tonight's rank.
sitemap-stocks.xml    the company pages, for Search Console (listed in robots.txt).
stock_feed.xml        an RSS feed, one item per session: tonight's #1 and top five
                      as cashtags. Zapier reads it and posts it to StockTwits.

The page shell (fonts, colours, menu, footer, disclaimer bar, sign-up script) is
scripts/templates/company.html; this file only fills in the article.
Educational research only, not investment advice.
"""

import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.environ.get("SITE_ROOT", ".")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "company.html")
PAGE_TOP = int(os.environ.get("COMPANY_PAGE_TOP", "200"))
SITE = "https://thevancereport.com/"
FEED_KEEP = 15

PILLARS = [("cheapness", "Cheapness", "40%", "Is it cheap against its earnings, cash flow and sales?"),
           ("quality", "Quality", "30%", "Is it a good business: profitable, with honest earnings?"),
           ("safety", "Safety", "20%", "Could it get through a bad stretch?"),
           ("confirmation", "Confirmation", "10%", "Has the selling stopped?")]

# (key in metrics, label, pillar, formatter)
METRICS = [
    ("ev_ebit", "EV / EBIT", "Cheapness", lambda v: f"{v:.1f}×"),
    ("ev_fcf", "EV / free cash flow", "Cheapness", lambda v: f"{v:.1f}×"),
    ("ev_sales", "EV / revenue", "Cheapness", lambda v: f"{v:.2f}×"),
    ("shareholder_yield_pct", "Shareholder yield", "Cheapness", lambda v: f"{v:.1f}%"),
    ("gross_profitability", "Gross profit / assets", "Quality", lambda v: f"{v * 100:.0f}%"),
    ("roic_pct", "Return on invested capital", "Quality", lambda v: f"{v:.1f}%"),
    ("accruals", "Accruals / assets", "Quality", lambda v: f"{v * 100:.1f}%"),
    ("net_debt_ebitda", "Net debt / EBITDA", "Safety", lambda v: "net cash" if v <= 0 else f"{v:.1f}×"),
    ("interest_cover", "Interest coverage", "Safety", lambda v: "no interest cost" if v >= 1000 else f"{v:.1f}×"),
    ("net_issuance_pct", "Net share issuance (a year)", "Safety", lambda v: f"{v:+.1f}%"),
    ("momentum_12_1_pct", "12-1 month momentum", "Confirmation", lambda v: f"{v:+.1f}%"),
    ("price_vs_200d", "Price vs 200-day average", "Confirmation", lambda v: f"{(v - 1) * 100:+.1f}%"),
]

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def long_date(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def nice_name(name):
    """'FLEXSTEEL INDUSTRIES INC' -> 'Flexsteel Industries'."""
    n = str(name or "").strip()
    for _ in range(2):
        n = re.sub(r"[,.]?\s+(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|HOLDINGS|N\.V|S\.A|LP|L\.P|/DE|/MD)\.?$", "", n, flags=re.I)
    n = re.sub(r"[,.]\s*$", "", n)
    small = {"and", "of", "the", "for", "in", "on"}
    words = [w.lower() if i and w.lower() in small else (w if not w.isupper() or '&' in w else w.capitalize())
             for i, w in enumerate(n.split())]
    return " ".join(words) or str(name or "")


def slug(sym):
    return re.sub(r"[^a-z0-9]+", "-", sym.lower()).strip("-") + "-stock.html"


def load(name, default=None):
    try:
        with open(os.path.join(ROOT, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- one page

def rank_history(sym, history):
    """Sessions this company ranked in the published top N, as (session, rank)."""
    out = []
    for s in (history or {}).get("sessions", []):
        for r in s.get("top", []):
            if r.get("symbol") == sym:
                out.append((s["session"], r["rank"]))
                break
    return out, len((history or {}).get("sessions", [])), (history or {}).get("top_n", 25)


def history_svg(points, sessions_total):
    """A small rank line: rank 1 at the top. Points = [(session, rank)]."""
    if len(points) < 2:
        return ""
    w, h, pad = 640, 140, 28
    worst = max(r for _, r in points)
    worst = max(worst, 10)
    xs = [pad + i * (w - 2 * pad) / (len(points) - 1) for i in range(len(points))]
    y = lambda r: pad + (r - 1) / (worst - 1) * (h - 2 * pad)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y(r):.1f}" for i, (x, (_, r)) in enumerate(zip(xs, points)))
    first, last = points[0], points[-1]
    return (f"<svg class='cp-hist' viewBox='0 0 {w} {h}' role='img' aria-label='Rank history'>"
            f"<line x1='{pad}' x2='{w - pad}' y1='{y(1):.1f}' y2='{y(1):.1f}' class='g'/>"
            f"<text x='{pad}' y='{y(1) - 8:.1f}' class='t'>#1</text>"
            f"<path d='{path}' class='l'/>"
            f"<circle cx='{xs[-1]:.1f}' cy='{y(last[1]):.1f}' r='4' class='d'/>"
            f"<text x='{pad}' y='{h - 4}' class='t'>{esc(long_date(first[0]))}</text>"
            f"<text x='{w - pad}' y='{h - 4}' class='t' text-anchor='end'>{esc(long_date(last[0]))}: #{last[1]}</text>"
            f"</svg>")


def page_body(r, meta, history, scorecard, leaps, ranked_n):
    sym, name = r["symbol"], nice_name(r.get("name"))
    asof = meta["as_of"]
    p = r.get("pillars") or {}
    m = r.get("metrics") or {}
    pts, n_sessions, top_n = rank_history(sym, history)
    best = min((rk for _, rk in pts), default=None)
    sc = next((c for c in (scorecard or {}).get("companies", []) if c.get("symbol") == sym), None)
    lp = next((x for x in (leaps or {}).get("stock_replacement", []) if x.get("symbol") == sym), None)

    bars = "".join(
        f"<div class='cp-bar'><span class='n'>{lab} <small>{wt}</small></span>"
        f"<span class='t'><i style='width:{max(0, min(100, p.get(k) or 0)):.0f}%'></i></span>"
        f"<b>{(round(p[k]) if isnum(p.get(k)) else '—')}</b><small class='q'>{q}</small></div>"
        for k, lab, wt, q in PILLARS)

    rows = "".join(
        f"<tr><td>{lab}</td><td class='num'>{fmt(m[k]) if isnum(m.get(k)) else 'not reported'}</td><td>{pil}</td></tr>"
        for k, lab, pil, fmt in METRICS)

    hist = ""
    if pts:
        hist = (f"<h2>Rank history</h2><p>{esc(name)} has been in the screen&rsquo;s published top {top_n} on "
                f"{len(pts)} of {n_sessions} weeknight sessions since {esc(long_date(history['sessions'][0]['session']))}"
                f"{f', with a best rank of #{best}' if best else ''}.</p>{history_svg(pts, n_sessions)}")

    record = ""
    if sc and isnum(sc.get("return_pct")):
        record = (f"<h2>Track record</h2><p>{esc(name)} first reached the top ten on {esc(long_date(sc['entered']))} at "
                  f"#{sc['entry_rank']}. Measured from the next close, it has moved "
                  f"<b>{sc['return_pct']:+.1f}%</b>, against {sc.get('spy_pct', 0):+.1f}% for the S&amp;P 500 over the same days. "
                  f"Every company that has reached the top ten is on the <a href='scorecard.html'>track record</a>, "
                  f"winners and losers alike.</p>")

    options = ""
    if lp:
        options = (f"<h2>On the options screen</h2><p>Tonight {esc(sym)} passes {lp['passes']} of the five LEAPS filters"
                   f"{' and is one of the few that pass them all' if lp.get('all_pass') else ''}. "
                   f"See the <a href='leaps-stock-replacement.html'>stock replacement page</a> for the contract, its cost and the risks.</p>")

    cap = r.get("market_cap")
    cap_s = f"${cap / 1e9:.1f}bn" if isnum(cap) and cap >= 1e9 else (f"${cap / 1e6:.0f}m" if isnum(cap) else "—")
    lead = (f"On the close of {esc(long_date(asof))}, {esc(name)} ({esc(sym)}) ranked <b>#{r['rank']} of {ranked_n:,}</b> "
            f"US companies on the Vance value screen, with a score of {r['score']:.1f} out of 100"
            f"{', in the top tenth' if r.get('decile') == 1 else ''}. "
            f"Each figure below is a percentile against other {esc(r.get('sector') or '')} companies on the same night, "
            f"where 100 is the best reading.")

    return f"""<header class="article-head">
<div class="wrap head-grid">
<div>
<p class="eyebrow">Vance value screen · {esc(r.get('sector') or 'Unclassified')}</p>
<h1>{esc(name)} <em>({esc(sym)})</em></h1>
<p class="dek">Rank, score and the filing figures behind them, updated every weeknight after the close.</p>
</div>
<dl class="story-meta">
<div><dt>Tonight&rsquo;s rank</dt><dd>#{r['rank']} of {ranked_n:,}</dd></div>
<div><dt>Score</dt><dd>{r['score']:.1f} / 100</dd></div>
<div><dt>Price · size</dt><dd>${r['price']:.2f} · {cap_s}</dd></div>
<div><dt>As of</dt><dd>{esc(long_date(asof))}</dd></div>
</dl>
</div>
</header>
<article>
<div class="wrap article-grid">
<aside class="rail" aria-label="Article classification">
<strong>Company page</strong>
<div class="rail-line"></div>
Updated nightly<br>From SEC filings<br>Educational only
</aside>
<div class="prose">
<p class="lead">{lead}</p>
<h2>The four questions</h2>
<div class="cp-bars">{bars}</div>
<h2>The figures behind the score</h2>
<div class="t5-wrap sc-wrap"><table class="t5 sc cp-tab"><thead><tr><th>Measure</th><th>{esc(sym)}</th><th>Counts towards</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="sc-note">From {esc(name)}&rsquo;s most recent SEC filings and the closing price of {esc(long_date(asof))}. Trailing twelve months where it applies. How each measure is scored: <a href="vance-value-screener.html">how it works</a>.</p>
{hist}
{record}
{options}
<p class="cp-links"><a class="back" href="stock.html?symbol={esc(sym)}">Interactive profile &rarr;</a> <a class="back" href="stock-screener.html">Full ranking &rarr;</a> <a class="back" href="stocks.html">All company pages &rarr;</a></p>
<div class="sc-cta" id="sc-cta">
<p><strong>Get the screen&rsquo;s top ten by email after each close.</strong> Free. One email to confirm; unsubscribe any time.</p>
<form id="scForm" novalidate="">
<p hidden="" aria-hidden="true"><label>Do not fill this out: <input name="bot-field" tabindex="-1"></label></p>
<input type="email" name="email" autocomplete="email" placeholder="name@example.com" required="">
<button type="submit">Sign up</button>
</form>
<p class="sc-form-note" id="scNote" hidden=""></p>
</div>
<p class="sc-note">A high rank is a reason to look, not a reason to buy. Educational research only, not investment advice.</p>
</div>
</div>
</article>"""


def page_head(r, meta, ranked_n):
    sym, name = r["symbol"], nice_name(r.get("name"))
    title = f"{sym} stock: {name} ranks #{r['rank']} of {ranked_n:,} on the Vance value screen"
    desc = (f"{name} ({sym}) value analysis: rank #{r['rank']} of {ranked_n:,} US companies on "
            f"{long_date(meta['as_of'])}, score {r['score']:.0f}/100, with cheapness, quality, safety and the "
            f"SEC-filing figures behind them. Updated every weeknight.")
    return title, desc


def render(template, r, meta, history, scorecard, leaps, ranked_n):
    title, desc = page_head(r, meta, ranked_n)
    url = SITE + slug(r["symbol"])
    out = template.replace("{{TITLE}}", esc(title)).replace("{{DESC}}", esc(desc)).replace("{{URL}}", url)
    return out.replace("{{BODY}}", page_body(r, meta, history, scorecard, leaps, ranked_n))


def gone_body(sym, meta):
    return f"""<header class="article-head"><div class="wrap head-grid"><div>
<p class="eyebrow">Vance value screen</p><h1>{esc(sym)}</h1>
<p class="dek">Not ranked on the screen of {esc(long_date(meta['as_of']))}.</p></div></div></header>
<article><div class="wrap article-grid"><aside class="rail"><strong>Company page</strong></aside><div class="prose">
<p class="lead">{esc(sym)} was not in the ranked universe tonight. Companies leave it when they fall below the size, price or trading
floors, when their filings are out of date, or when they are delisted. The page will fill in again if it returns.</p>
<p class="cp-links"><a class="back" href="stock-screener.html">Full ranking &rarr;</a> <a class="back" href="stocks.html">All company pages &rarr;</a></p>
<div class="sc-cta" id="sc-cta"><form id="scForm" novalidate="" hidden=""></form><p class="sc-form-note" id="scNote" hidden=""></p></div>
</div></div></article>"""


# ---------------------------------------------------------------- index, sitemap, feed

def index_page(template, pages, meta, ranked_n):
    items = "".join(
        f"<tr><td class='sym'><a href='{slug(r['symbol'])}'>{esc(r['symbol'])}</a><small>{esc(nice_name(r.get('name')))}</small></td>"
        f"<td class='num' data-l='Rank'>#{r['rank']}</td><td class='num' data-l='Score'>{r['score']:.1f}</td>"
        f"<td data-l='Sector'>{esc(r.get('sector') or '')}</td></tr>"
        for r in sorted(pages, key=lambda r: r["symbol"]))
    body = f"""<header class="article-head"><div class="wrap head-grid"><div>
<p class="eyebrow">Vance value screen · company pages</p><h1>Every company page, <em>A to Z.</em></h1>
<p class="dek">One page for each of the {len(pages)} companies with a page tonight, with its rank on the screen of {esc(long_date(meta['as_of']))}.</p></div></div></header>
<article><div class="wrap article-grid"><aside class="rail"><strong>Company pages</strong><div class="rail-line"></div>Updated nightly</aside><div class="prose">
<div class="t5-wrap sc-wrap"><table class="t5 sc"><thead><tr><th>Company</th><th>Rank of {ranked_n:,}</th><th>Score</th><th>Sector</th></tr></thead><tbody>{items}</tbody></table></div>
<div class="sc-cta" id="sc-cta"><form id="scForm" novalidate="" hidden=""></form><p class="sc-form-note" id="scNote" hidden=""></p></div>
</div></div></article>"""
    out = template.replace("{{TITLE}}", "Company pages A to Z: the Vance value screen")
    out = out.replace("{{DESC}}", f"Value-screen pages for {len(pages)} US companies: rank, score and SEC-filing figures, updated every weeknight.")
    return out.replace("{{URL}}", SITE + "stocks.html").replace("{{BODY}}", body)


def sitemap(syms, asof):
    urls = [SITE + "stocks.html"] + [SITE + slug(s) for s in sorted(syms)]
    body = "".join(f"  <url>\n    <loc>{u}</loc>\n    <lastmod>{asof}</lastmod>\n    <changefreq>daily</changefreq>\n  </url>\n" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}</urlset>\n'


def feed_item(meta, ranked):
    top = ranked[:5]
    first = top[0]
    link = SITE + slug(first["symbol"])
    # StockTwits (via Zapier) cuts posts at 140 characters, and the Zap posts "title link".
    # Pick the fullest wording that fits; no apostrophes (they arrive HTML-escaped).
    budget = 140 - len(link) - 1
    lead = f"${first['symbol']} is #1 of {len(ranked):,} on the Vance value screen"
    nxt = " ".join("$" + r["symbol"] for r in top[1:])
    nxt3 = " ".join("$" + r["symbol"] for r in top[1:4])
    candidates = [
        f"{lead} ({first['score']:.0f}/100). Next: {nxt}. Not advice.",
        f"{lead} ({first['score']:.0f}/100). Next: {nxt}.",
        f"{lead}. Next: {nxt}.",
        f"{lead}. Next: {nxt3}.",
        f"{lead}.",
    ]
    text = next((c for c in candidates if len(c) <= budget), candidates[-1])
    return {"guid": f"vance-screen-{meta['as_of']}", "title": text, "link": link,
            "date": meta["as_of"]}


def write_feed(path, item):
    old = []
    try:
        with open(path) as f:
            for m in re.finditer(r"<item><title>(.*?)</title><link>(.*?)</link><guid isPermaLink=\"false\">(.*?)</guid><pubDate>(.*?)</pubDate></item>", f.read(), re.S):
                old.append({"title": html.unescape(m.group(1)), "link": m.group(2), "guid": m.group(3), "pub": m.group(4)})
    except FileNotFoundError:
        pass
    old = [o for o in old if o["guid"] != item["guid"]]
    pub = datetime.strptime(item["date"], "%Y-%m-%d").replace(hour=22, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    items = [{"title": item["title"], "link": item["link"], "guid": item["guid"], "pub": pub}] + old
    items = items[:FEED_KEEP]
    body = "".join(f"<item><title>{esc(i['title'])}</title><link>{i['link']}</link><guid isPermaLink=\"false\">{i['guid']}</guid><pubDate>{i['pub']}</pubDate></item>\n" for i in items)
    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
                "<title>The Vance Report: tonight's value screen</title>\n"
                f"<link>{SITE}</link>\n<description>One line per weeknight: the top of the Vance value screen, as cashtags.</description>\n"
                f"{body}</channel></rss>\n")


# ---------------------------------------------------------------- main

def main():
    screen = load("value_screen.json")
    if not screen:
        sys.exit("value_screen.json missing")
    meta, ranked = screen["_meta"], screen["ranked"]
    history, scorecard, leaps = load("screen_history.json", {}), load("scorecard.json", {}), load("leaps_screen.json", {})
    with open(TEMPLATE) as f:
        template = f.read()
    by_sym = {r["symbol"]: r for r in ranked}
    existing = {os.path.basename(p)[:-len("-stock.html")].upper()
                for p in glob.glob(os.path.join(ROOT, "*-stock.html"))}
    want = {r["symbol"] for r in ranked[:PAGE_TOP]} | existing
    written = []
    for sym in sorted(want):
        r = by_sym.get(sym)
        if r:
            out = render(template, r, meta, history, scorecard, leaps, len(ranked))
            written.append(r)
        else:
            out = (template.replace("{{TITLE}}", esc(f"{sym} stock: not ranked tonight"))
                   .replace("{{DESC}}", esc(f"{sym} is not on the Vance value screen of {long_date(meta['as_of'])}."))
                   .replace("{{URL}}", SITE + slug(sym)).replace("{{BODY}}", gone_body(sym, meta)))
        with open(os.path.join(ROOT, slug(sym)), "w") as f:
            f.write(out)
    with open(os.path.join(ROOT, "stocks.html"), "w") as f:
        f.write(index_page(template, written, meta, len(ranked)))
    with open(os.path.join(ROOT, "sitemap-stocks.xml"), "w") as f:
        f.write(sitemap(want, meta["as_of"]))
    write_feed(os.path.join(ROOT, "stock_feed.xml"), feed_item(meta, ranked))
    print(f"{len(written)} company pages, {len(want) - len(written)} not ranked tonight; index, sitemap and feed written.")


if __name__ == "__main__":
    main()
