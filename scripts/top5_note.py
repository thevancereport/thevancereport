#!/usr/bin/env python3
"""
Write tonight's top-five note straight from the screen file.

    python3 scripts/top5_note.py [--root .] [--force]

Creates top-five-<as_of>.html (in the design of the newest earlier note, whose
<head> styles, nav and footer it reuses), points top5.json at it, adds it to
the top of the Field Notes list in blog.html and to sitemap.xml. It does
nothing if tonight's note already exists (unless --force), so a note written
or edited by hand is never overwritten.

Every figure in the note comes from value_screen.json, or from
screen_history.json for "the session before". Nothing here says what to buy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from top5_video import PILLARS, join, long_date, load, n1, pillar_line, short  # noqa: E402

CT = dt.timezone(dt.timedelta(hours=-5))  # the date only matters to the day; CDT/CST both land the same evening
SITE = "https://thevancereport.com"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def attr(s) -> str:
    return html.escape(str(s), quote=True)


def money(x: float) -> str:
    return f"${x:,.2f}"


def pct(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(x):.1f}%"


def fmt_long(iso: str) -> str:    # September 25, 2026
    y, m, d = (int(v) for v in iso.split("-"))
    return f"{MONTHS[m - 1]} {d}, {y}"


def fmt_short(iso: str) -> str:   # 25 Sep 2026
    y, m, d = (int(v) for v in iso.split("-"))
    return f"{d} {MONTHS[m - 1][:3]} {y}"


def weekday(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%A")


# ------------------------------------------------------------- the words ----
def texts(data: dict) -> dict:
    meta, cos = data["meta"], data["cos"]
    n = f"{int(meta['universe']):,}"
    day = long_date(data["as_of"])
    top = cos[0]
    tn = short(top["name"])
    if top["prev"]:
        top_bit = f"{tn} is first with a score of {n1(top['r']['score'])}" + (
            ", as it was the session before." if top["prev"]["rank"] == 1 else f", up from #{top['prev']['rank']} the session before.")
    else:
        top_bit = f"{tn} is first with a score of {n1(top['r']['score'])}."
    if data["entered"]:
        ins = [short(c["name"]) for c in cos if c["symbol"] in data["entered"]]
        change = join(ins) + (" joins" if len(ins) == 1 else " join") + " the five" + (
            ", and " + join(data["left"]) + (" drops" if len(data["left"]) == 1 else " drop") + " out." if data["left"] else ".")
    elif data["prev"]:
        swaps = [c for c in cos if c["prev"] and c["prev"]["rank"] != c["r"]["rank"]]
        change = "The same five names as the session before" + (
            ", with " + join([short(c["name"]) for c in swaps]) + " changing places." if 0 < len(swaps) <= 3 else ".")
    else:
        change = ""
    moved = [c for c in cos if c["move"] is not None]
    big = max(moved, key=lambda c: abs(c["move"])) if moved else None
    move_bit = (f" The biggest move among them: {short(big['name'])}, {'up' if big['move'] > 0 else 'down'} "
                f"{abs(big['move']):.1f}% on the day.") if big and abs(big["move"]) >= 2 else ""
    return {
        "headline": f"The five highest-ranked US companies on {day}",
        "h1": f"The five at the top, <em>{esc(day)}.</em>",
        "dek": (f"{n} companies read from their own SEC filings and put in order on the close of "
                f"{day.rsplit(' ', 1)[0]}. What the screen likes in each of the five, and what the same arithmetic does not like."),
        "lead": re.sub(r"\s+", " ", f"{n} US companies were ranked on the close of {weekday(data['as_of'])}, {day}. {top_bit} {change}{move_bit}").strip(),
        "blurb": re.sub(r"\s+", " ", f"{top_bit} {change} What the screen likes in each of the five, and what it doesn't.").strip(),
        "list_dek": re.sub(r"\s+", " ", f"{n} companies ranked on the close of {day.rsplit(' ', 1)[0]}. {top_bit} {change}").strip(),
    }


def missing_inputs(r: dict) -> list[str]:
    m = r.get("metrics") or {}
    names = {"ev_ebit": "EV/EBIT", "ev_fcf": "EV/free cash flow", "ev_sales": "EV/revenue",
             "shareholder_yield_pct": "shareholder yield", "gross_profitability": "gross profitability",
             "roic_pct": "return on capital", "accruals": "accruals", "net_debt_ebitda": "net debt/EBITDA",
             "interest_cover": "interest cover", "net_issuance_pct": "share issuance",
             "momentum_12_1_pct": "12-month momentum", "price_vs_200d": "200-day average"}
    return [v for k, v in names.items() if k in m and m[k] is None]


def facts_row(r: dict) -> str:
    m = r.get("metrics") or {}
    f = [f"<span>Price <b>{money(r['price'])}</b></span>", f"<span>Score <b>{n1(r['score'])}</b></span>"]
    if m.get("ev_ebit") is not None: f.append(f"<span>EV/EBIT <b>{n1(m['ev_ebit'])}&times;</b></span>")
    if m.get("ev_fcf") is not None: f.append(f"<span>EV/FCF <b>{n1(m['ev_fcf'])}&times;</b></span>")
    if m.get("roic_pct") is not None: f.append(f"<span>ROIC <b>{n1(m['roic_pct'])}%</b></span>")
    if m.get("net_debt_ebitda") == -1: f.append("<span>Net cash</span>")
    elif m.get("net_debt_ebitda") is not None: f.append(f"<span>Net debt/EBITDA <b>{n1(m['net_debt_ebitda'])}&times;</b></span>")
    if m.get("momentum_12_1_pct") is not None: f.append(f"<span>12-month <b>{pct(m['momentum_12_1_pct']).replace('−', '&minus;')}</b></span>")
    return '<div class="t5-facts">' + "".join(f) + "</div>"


def bars_row(r: dict, w: dict) -> str:
    out = []
    for key, label, col in PILLARS:
        v = float(r["pillars"].get(key) or 0)
        out.append(f'<div class="r"><span>{label} <span style="opacity:.55">{round((w.get(key) or 0) * 100)}%</span></span>'
                   f'<span class="t"><i style="width:{v:g}%;background:{col}"></i></span><span class="v">{round(v)}</span></div>')
    return '<div class="t5-bars">' + "".join(out) + "</div>"


def main_html(data: dict, t: dict) -> tuple[str, int]:
    meta, cos = data["meta"], data["cos"]
    w = meta.get("weights") or {"cheapness": .4, "quality": .3, "safety": .2, "confirmation": .1}
    fl = meta.get("floors") or {}
    wp = lambda k: f"{round((w.get(k) or 0) * 100)}%"
    n = f"{int(meta['universe']):,}"
    rows = []
    for c in cos:
        r = c["r"]
        cells = "".join(f'<td class="p{" lo" if (r["pillars"].get(k) or 0) < 40 else ""}">{round(r["pillars"].get(k) or 0)}</td>' for k, _, _ in PILLARS)
        rows.append(f'<tr><td class="rk">{r["rank"]}</td><td class="sym">{esc(r["symbol"])}<small>{esc(c["name"])} &middot; {esc(r["sector"])}</small></td>'
                    f'<td class="num">{money(r["price"])}</td><td class="num">{n1(r["score"])}</td>{cells}</tr>')
    blocks = []
    for c in cos:
        r = c["r"]
        if c["prev"]:
            yest = f"The session before it was #{c['prev']['rank']} at {money(c['prev']['price'])} with a score of {n1(c['prev']['score'])}" + (
                f"; today's close is {pct(c['move'])} on that." if c["move"] is not None else ".")
        else:
            yest = "New to the top of the board this session."
        notes = []
        miss = missing_inputs(r)
        if miss:
            notes.append(f"The filings did not supply {join(miss)}, so the score leans on fewer measures than usual.")
        if ((r.get("metrics") or {}).get("shareholder_yield_pct") or 0) > 15:
            notes.append("The shareholder yield is a trailing twelve-month figure, divided by today's market value: a lower price raises it without a dollar more being returned.")
        likes = " ".join(pillar_line(k, r, False) for k in c["likes"])
        blocks.append(
            '<div class="t5-co">\n'
            f'<h2>{r["rank"]} &middot; {esc(c["name"])} &mdash; {esc(r["sector"])}</h2>\n'
            f"{facts_row(r)}\n{bars_row(r, w)}\n"
            '<div class="t5-take">\n'
            f'<div class="up"><b>What the screen likes</b><p>{esc(likes)}</p></div>\n'
            f'<div class="dn"><b>What it doesn&rsquo;t</b><p>{esc(pillar_line(c["dislike"], r, False))}</p></div>\n</div>\n'
            + (f'<div class="t5-note"><p>{esc(" ".join(notes))}</p></div>\n' if notes else "")
            + f"<p>\n{esc(yest)}\n</p>\n</div>")
    words = len(t["lead"].split()) + 1100
    minutes = max(3, round(words / 230))
    unranked = f" and {meta['unranked']} were left out for want of data" if meta.get("unranked") else ""
    body = f"""<main>
<header class="article-head">
<div class="wrap head-grid">
<div>
<p class="eyebrow">Daily Screen &middot; The Five At The Top</p>
<h1>{t['h1']}</h1>
<p class="dek">
{esc(t['dek'])}
</p>
</div>
<dl class="story-meta">
<div><dt>Screen date</dt><dd>{fmt_long(data['as_of'])}</dd></div>
<div><dt>Companies ranked</dt><dd>{n}</dd></div>
<div><dt>Reading time</dt><dd>{minutes} minutes</dd></div>
</dl>
</div>
</header>
<article>
<div class="wrap article-grid">
<aside class="rail" aria-label="Article classification">
<strong>Daily screen</strong>
<div class="rail-line"></div>
{"<br />".join(esc(c["symbol"]) for c in cos)}
</aside>
<div class="prose">

<p class="lead">
{esc(t['lead'])}
</p>

<h2>How to read the four numbers</h2>
<p>
Every company gets four scores, and each is a percentile against other companies <em>in its own sector</em>, on the same day. Cheapness 98 means the company looks cheaper than 98% of its sector on enterprise value against operating profit, free cash flow and sales, plus what it returns to shareholders. It does not mean cheap against the market.
</p>
<p>
The four combine into one composite: cheapness {wp('cheapness')}, quality {wp('quality')}, safety {wp('safety')}, confirmation {wp('confirmation')}. To be ranked at all a company must trade above ${fl.get('price', 3)}, be worth ${round((fl.get('market_cap') or 3e8) / 1e6)} million, and turn over ${round((fl.get('dollar_volume') or 2e6) / 1e6)} million of stock a day. Banks, insurers and property trusts are excluded, because enterprise-value measures do not describe those balance sheets honestly. This session {n} companies were ranked{unranked}.
</p>

<h2>The five</h2>
<div class="t5-wrap">
<table class="t5">
<thead>
<tr><th>#</th><th>Company</th><th>Price</th><th>Score</th><th>Cheap</th><th>Qual</th><th>Safe</th><th>Conf</th></tr>
</thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</div>
<p>
Everything except the price comes from each company&rsquo;s most recent SEC filings, which can lag a quarter behind the news.
</p>

{chr(10).join(blocks)}

<p>
The standing limit: in testing, this ranking ordered the universe &mdash; the bottom decile performed consistently worse than the top &mdash; but the top decile itself performed about in line with the market. A high rank buys a shorter reading list. It does not buy a return.
</p>
<p>
Sources: The Vance Report&rsquo;s own screen file for {long_date(data['as_of'])}, built from SEC company facts and Nasdaq closing prices; the method is at
<a href="vance-value-screener.html">how the Vance Value Screener works</a>. Educational research, not investment advice.
</p>
<p>
<a class="back" href="blog.html"
><span aria-hidden="true">←</span> All field notes</a
>
</p>
<div class="endmark"></div>
</div>
</div>
</article>
</main>"""
    return body, words


def head_block(data: dict, t: dict, page: str, published: str, words: int) -> str:
    url = f"{SITE}/{page}"
    desc = t["dek"]
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "Article", "headline": t["headline"], "description": desc, "datePublished": published,
         "dateModified": published, "inLanguage": "en-US", "articleSection": "Daily screen",
         "keywords": "Vance Value Screener, daily stock screen, " + ", ".join(c["symbol"] for c in data["cos"]) + ", value investing, SEC filings, sector percentile",
         "wordCount": words,
         "author": {"@type": "Organization", "name": "The Vance Report", "url": f"{SITE}/"},
         "publisher": {"@type": "Organization", "name": "The Vance Report", "url": f"{SITE}/",
                       "logo": {"@type": "ImageObject", "url": f"{SITE}/apple-touch-icon.png"}},
         "mainEntityOfPage": {"@type": "WebPage", "@id": url}, "image": f"{SITE}/og-card.png", "isAccessibleForFree": True,
         "about": [{"@type": "Corporation", "name": c["name"], "tickerSymbol": c["symbol"]} for c in data["cos"]]},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Field Notes", "item": f"{SITE}/blog.html"},
            {"@type": "ListItem", "position": 3, "name": f"The five at the top, {long_date(data['as_of'])}", "item": url}]}]}
    return (f"<title>{esc(t['headline'])} — The Vance Report</title>\n"
            f'<meta\nname="description"\ncontent="{attr(desc)}"\n/>\n<link rel="canonical" href="{url}" />\n'
            '<meta name="robots" content="index, follow, max-image-preview:large" />\n'
            '<meta property="og:type" content="article" />\n<meta property="og:site_name" content="The Vance Report" />\n'
            f'<meta property="og:title" content="{attr(t["headline"])}" />\n'
            f'<meta\nproperty="og:description"\ncontent="{attr(desc)}"\n/>\n<meta property="og:url" content="{url}" />\n'
            f'<meta property="og:image" content="{SITE}/og-card.png" />\n'
            f'<meta property="article:published_time" content="{published}" />\n<meta property="article:section" content="Daily screen" />\n'
            '<meta name="twitter:card" content="summary_large_image" />\n'
            f'<meta name="twitter:title" content="{attr(t["headline"])}" />\n'
            f'<meta\nname="twitter:description"\ncontent="{attr(desc)}"\n/>\n<meta name="twitter:image" content="{SITE}/og-card.png" />\n'
            '<script type="application/ld+json">\n' + json.dumps(ld, indent=2, ensure_ascii=False).replace("</", "<\\/") + "\n</script>\n")


def assemble(template: str, head: str, main: str) -> str:
    a = template.index("<title>")
    b = template.index('<link rel="preconnect"', a)
    m1, m2 = template.index("<main"), template.index("</main>")
    out = template[:a] + head + template[b:m1] + main + template[m2 + len("</main>"):]
    return re.sub(r'<figure class="(t5-video|note-video)"[\s\S]*?</figure>\n?', "", out)


def add_to_blog(blog: str, page: str, published: str, t: dict, minutes: int) -> str:
    if f'href="{page}"' in blog:
        return blog
    a = blog.index('<ul class="post-list">')
    b = blog.index("</ul>", a)
    ls = blog.rfind("\n", 0, a) + 1
    ind = blog[ls:a] if not blog[ls:a].strip() else ""
    I = lambda k: ind + "  " * k
    tags = ["Daily screen", "Top five", "Sector percentiles", f"{minutes} min"]
    li = "\n".join([
        I(1) + "<li>",
        I(2) + f'<a class="post-item" href="{attr(page)}">',
        I(3) + f'<span class="post-index">{fmt_short(published)}</span>',
        I(3) + "<span>",
        I(4) + f'<span class="post-title">{esc(t["headline"])}</span>',
        I(4) + f'<span class="post-dek">{esc(t["list_dek"])}</span>',
        I(4) + '<span class="post-tags">' + "".join(f"<span>{esc(x)}</span>" for x in tags) + "</span>",
        I(3) + "</span>",
        I(2) + "</a>",
        I(1) + "</li>"])
    start = a + len('<ul class="post-list">')
    out = blog[:start] + "\n" + li + blog[start:]
    count = out[a:out.index("</ul>", a)].count("<li")
    out = re.sub(r"(<dt>\s*Published\s*</dt>\s*<dd>)[^<]*(</dd>)", rf"\g<1>{count} {'note' if count == 1 else 'notes'}\g<2>", out, count=1)
    out = re.sub(r"(<dt>\s*Updated\s*</dt>\s*<dd>)[^<]*(</dd>)", rf"\g<1>{fmt_long(published)}\g<2>", out, count=1)
    return out


def add_to_sitemap(xml: str, page: str, published: str) -> str:
    if f"/{page}</loc>" not in xml:
        xml = xml.replace("</urlset>", f"  <url>\n    <loc>{SITE}/{page}</loc>\n    <lastmod>{published}</lastmod>\n"
                                       "    <changefreq>never</changefreq>\n    <priority>0.6</priority>\n  </url>\n</urlset>")
    return re.sub(r"(<loc>https://thevancereport\.com/blog\.html</loc>\s*<lastmod>)[^<]*(</lastmod>)", rf"\g<1>{published}\g<2>", xml)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--published", default="", help="publication date (YYYY-MM-DD); default today in Central time")
    a = ap.parse_args()
    root = Path(a.root)
    data = load(root)
    page = f"top-five-{data['as_of']}.html"
    if (root / page).exists() and not a.force:
        print(f"{page} already exists; leaving it alone")
        return 0
    earlier = sorted(p for p in glob.glob(str(root / "top-five-*.html")) if Path(p).name < page)
    if not earlier:
        sys.exit("no earlier top-five note to use as the page template")
    template = Path(earlier[-1]).read_text(encoding="utf-8")
    published = a.published or dt.datetime.now(CT).date().isoformat()
    t = texts(data)
    main_part, words = main_html(data, t)
    minutes = max(3, round(words / 230))
    (root / page).write_text(assemble(template, head_block(data, t, page, published, words), main_part), encoding="utf-8")

    t5p = root / "top5.json"
    old = json.loads(t5p.read_text(encoding="utf-8")) if t5p.exists() else {}
    t5 = {"_about": old.get("_about", "The day's note and the video of it."), "date": data["as_of"], "published": published,
          "page": page, "universe": data["meta"]["universe"], "blurb": t["blurb"], "video_id": None,
          "names": [{"symbol": c["symbol"], "rank": c["r"]["rank"], "score": c["r"]["score"], "price": c["r"]["price"]} for c in data["cos"]]}
    t5p.write_text(json.dumps(t5, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    blog = root / "blog.html"
    if blog.exists():
        blog.write_text(add_to_blog(blog.read_text(encoding="utf-8"), page, published, t, minutes), encoding="utf-8")
    sm = root / "sitemap.xml"
    if sm.exists():
        sm.write_text(add_to_sitemap(sm.read_text(encoding="utf-8"), page, published), encoding="utf-8")
    print(f"wrote {page}: {t['lead']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
