"""
Slide rendering.

One PNG per spoken beat: the slide's artwork stays put while the caption
underneath changes, so the picture does not flicker every sentence but the
words on screen always match the words being said.

Rendered by headless Chromium from a self-contained HTML string. No network,
no fonts to fetch -- the page names IBM Plex first (installed on the CI runner
via fonts-ibm-plex) and falls back to DejaVu, which every Linux box has.
"""

from __future__ import annotations

import html
import subprocess
from pathlib import Path

W, H = 1920, 1080

INK = "#0c1014"
INK2 = "#11171d"
RULE = "#26303a"
RULE_SOFT = "#1b2229"
PAPER = "#e7e3d8"
DIM = "#a8a294"
FAINT = "#6f7680"
AMBER = "#e0a33c"
PASS = "#4ea373"
FAIL = "#c8553d"

MONO = "'IBM Plex Mono','DejaVu Sans Mono',monospace"
SERIF = "'IBM Plex Serif','DejaVu Serif',Georgia,serif"


def _shell(body: str, caption: str, footer: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:{W}px; height:{H}px; background:{INK}; color:{PAPER};
               font-family:{MONO}; overflow:hidden; }}
  .grid {{ position:absolute; inset:0;
           background-image:
             linear-gradient(to right, rgba(255,255,255,.022) 1px, transparent 1px),
             linear-gradient(to bottom, rgba(255,255,255,.022) 1px, transparent 1px);
           background-size:80px 80px; }}
  .frame {{ position:absolute; inset:0; padding:66px 96px 318px; display:flex;
            flex-direction:column; }}
  .rail {{ display:flex; align-items:center; gap:18px; color:{AMBER};
           font-size:22px; letter-spacing:.34em; text-transform:uppercase; }}
  .rail .mk {{ border:1px solid {AMBER}; padding:3px 9px; font-size:19px;
               letter-spacing:.12em; }}
  .stage {{ flex:1; display:flex; flex-direction:column; justify-content:center;
            padding:34px 0; min-height:0; overflow:hidden; }}
  .cap {{ position:absolute; left:96px; right:96px; bottom:128px;
          border-top:1px solid {RULE}; padding-top:26px; max-height:168px;
          overflow:hidden; font-size:39px; line-height:1.32; color:{PAPER};
          letter-spacing:.005em; }}
  .foot {{ position:absolute; left:96px; right:96px; bottom:52px;
           display:flex; justify-content:space-between; align-items:center;
           color:{FAINT}; font-size:21px; letter-spacing:.16em;
           text-transform:uppercase; }}
  h1 {{ font-family:{SERIF}; font-weight:400; font-size:118px; line-height:.98;
        letter-spacing:-.02em; }}
  h2 {{ font-family:{SERIF}; font-weight:400; font-size:74px; line-height:1.02;
        letter-spacing:-.01em; }}
  .lede {{ color:{DIM}; font-size:34px; line-height:1.5; max-width:38ch;
           margin-top:30px; }}
  .sym {{ font-family:{MONO}; font-weight:600; font-size:134px;
          letter-spacing:.02em; color:{AMBER}; line-height:1; }}
  .co {{ font-size:38px; color:{PAPER}; margin-top:16px; }}
  .sect {{ font-size:24px; color:{FAINT}; letter-spacing:.18em;
           text-transform:uppercase; margin-top:12px; }}
  table.kv {{ border-collapse:collapse; width:100%; }}
  table.kv td {{ padding:11px 0; border-bottom:1px solid {RULE_SOFT};
                 font-size:29px; vertical-align:baseline; }}
  table.kv td.k {{ color:{FAINT}; font-size:22px; letter-spacing:.13em;
                   text-transform:uppercase; }}
  table.kv td.v {{ text-align:right; font-variant-numeric:tabular-nums;
                   font-size:33px; }}
  .amb {{ color:{AMBER}; }} .ok {{ color:{PASS}; }} .no {{ color:{FAIL}; }}
  .cols {{ display:flex; gap:90px; align-items:flex-start; }}
  .cols > * {{ flex:1; min-width:0; }}
  .steps {{ display:flex; flex-direction:column; gap:26px; }}
  .step {{ display:flex; gap:26px; align-items:baseline; font-size:34px;
           line-height:1.35; }}
  .step b {{ color:{AMBER}; font-size:26px; letter-spacing:.14em; flex:0 0 auto; }}
  .big {{ font-size:96px; font-family:{MONO}; font-weight:600;
          font-variant-numeric:tabular-nums; }}
  .note {{ color:{DIM}; font-size:30px; line-height:1.45; max-width:44ch; }}
  .band {{ border:1px solid {RULE}; background:{INK2}; padding:34px 40px; }}
</style></head><body>
<div class="grid"></div>
<div class="frame">
  <div class="rail"><span class="mk">VR</span><span>The Vance Report</span></div>
  <div class="stage">{body}</div>
</div>
<div class="cap">{html.escape(caption)}</div>
<div class="foot"><span>Opinion &middot; not investment advice</span><span>{html.escape(footer)}</span></div>
</body></html>"""


def _kv(rows: list[tuple[str, str, str]]) -> str:
    out = ['<table class="kv">']
    for k, v, cls in rows:
        out.append(f'<tr><td class="k">{html.escape(k)}</td>'
                   f'<td class="v {cls}">{v}</td></tr>')
    out.append("</table>")
    return "".join(out)


def body_for(kind: str, d: dict) -> str:
    if kind == "title":
        n = d["count"]
        return (f'<h1>Daily<br>briefing</h1>'
                f'<p class="lede">{html.escape(d["date"])} &middot; '
                f'{n} name{"s" if n != 1 else ""} cleared both gates</p>')

    if kind == "method":
        return ('<h2>How a name gets here</h2>'
                '<div class="steps" style="margin-top:46px">'
                '<div class="step"><b>01</b><span>Five-day move of &minus;15% or worse</span></div>'
                '<div class="step"><b>02</b><span>(cash &minus; total liabilities) &divide; shares '
                '&divide; price &ge; 30%</span></div>'
                '<div class="step"><b>03</b><span>Runway, dilution and open-market '
                'insider buying</span></div></div>')

    if kind == "stock":
        runway = d.get("runway")
        rows = [
            ("Price", f'${d["price"]:,.2f}', ""),
            ("Five-day move", f'{d["drop"]:+.1f}%', "ok" if d["drop"] <= -15 else "no"),
            ("Net cash / share", f'${d["cps"]:,.2f}', ""),
            ("Net cash cushion", f'{d["cushion"]:.1f}%', "ok" if d["cushion"] >= 30 else "no"),
        ]
        if runway is not None:
            rows.append(("Runway",
                         "cash generative" if runway >= 50 else f"{runway:.1f} yrs",
                         "ok" if runway >= 2 else "no"))
        if d.get("dilution") is not None:
            rows.append(("Share count, 1 yr", f'{d["dilution"]:+.1f}%',
                         "ok" if d["dilution"] <= 25 else "no"))
        rows.append(("Market cap", f'${d["market_cap"]/1e6:,.0f}M', ""))
        rows.append(("Insider buys, 180d", str(d.get("insider_buys") or 0),
                     "amb" if d.get("insider_buys") else ""))
        return ('<div class="cols">'
                f'<div><div class="sym">{html.escape(d["symbol"])}</div>'
                f'<div class="co">{html.escape(d["name"])}</div>'
                f'<div class="sect">{html.escape(d["sector"])}</div></div>'
                f'<div>{_kv(rows)}</div></div>')

    if kind == "levels":
        return ('<div class="cols">'
                f'<div><h2>The screen&rsquo;s<br>own levels</h2>'
                f'<p class="note" style="margin-top:28px">Net cash per share divided '
                f'by the 30% gate. Arithmetic on the threshold &mdash; not a target, '
                f'not a valuation, not a price to pay.</p></div>'
                '<div>' + _kv([
                    ("Net cash / share", f'${d["cps"]:,.2f}', ""),
                    ("Cushion holds below", f'<span class="amb">${d["ceiling"]:,.2f}</span>', ""),
                    ("Price now", f'${d["price"]:,.2f}', ""),
                    ("Cushion now", f'{d["cushion"]:.1f}%', "ok"),
                ]) + '</div></div>')

    if kind == "horizon":
        return ('<h2>Three to six months</h2>'
                '<div class="band" style="margin-top:40px">'
                '<p class="note" style="max-width:52ch">A cash cushion is not a '
                'catalyst. Nothing on a balance sheet says what a price does '
                'tomorrow. These names are looked at on a three to six month view '
                '&mdash; not for day trading, and not for short-term trading of any '
                'kind.</p></div>')

    if kind == "disclaimer":
        return ('<h2>Opinion. Not advice.</h2>'
                '<div class="band" style="margin-top:36px">'
                '<p class="note" style="max-width:56ch">The Vance Report is not a '
                'registered investment adviser and not a broker-dealer. Nothing here '
                'is a recommendation to buy or sell. Screened securities are volatile, '
                'high risk, and may involve total loss of principal. Figures come from '
                'company filings and may be stale or wrong &mdash; verify before acting.'
                '</p></div>')

    return f'<h2>{html.escape(kind)}</h2>'


def render(frames: list[dict], out_dir: Path, chromium: str) -> list[Path]:
    """
    frames: [{kind, data, caption, footer}] -> one PNG each, in order.

    Chromium is launched once per frame. That is slower than driving a single
    instance, but it has no dependency beyond the binary itself, which matters
    for a job that has to keep working unattended.
    """
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / "_chrome-profile"
    profile.mkdir(exist_ok=True)
    paths: list[Path] = []
    for i, f in enumerate(frames):
        page = out_dir / f"f{i:04d}.html"
        png = out_dir / f"f{i:04d}.png"
        page.write_text(_shell(body_for(f["kind"], f["data"]),
                               f["caption"], f["footer"]), encoding="utf-8")
        subprocess.run(
            [chromium, "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             # The page is entirely local. Everything below stops Chromium
             # reaching for the network on launch, which on a locked-down
             # runner means a 30-second stall per frame instead of 1 second.
             "--no-first-run", "--no-default-browser-check",
             "--disable-background-networking", "--disable-component-update",
             "--disable-client-side-phishing-detection", "--disable-sync",
             "--disable-default-apps", "--disable-extensions",
             "--metrics-recording-only", "--safebrowsing-disable-auto-update",
             "--disable-domain-reliability", "--disable-breakpad",
             "--virtual-time-budget=4000",
             f"--user-data-dir={profile}",
             f"--window-size={W},{H}", f"--screenshot={png}", page.as_uri()],
            check=True, capture_output=True, timeout=120,
        )
        if not png.exists():
            raise RuntimeError(f"chromium produced no PNG for frame {i}")
        paths.append(png)
    return paths
