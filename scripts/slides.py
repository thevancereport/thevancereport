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

# Chromium's --window-size is the WINDOW, and the viewport it gives you is
# about 87px shorter. New headless paints nothing below the viewport fold, so
# a window of exactly W x H loses the bottom ~90px of every frame -- the
# footer and the caption rule sat in that band and simply never rendered.
#
# Ask for a taller window so the whole frame is painted, then trim the slack
# off the bottom. The trim happens once, in the single ffmpeg encode for the
# video and in one call for the thumbnail, rather than per frame.
VIEWPORT_SLACK = 220


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
  /* Everything positions inside .page, never against the viewport.
     Chromium's --window-size is the WINDOW, and the viewport it gives you
     is ~100px shorter; anchoring to `inset:0` silently laid every frame out
     in 1920x980 and left a dead band along the bottom of the picture. */
  .page {{ position:relative; width:{W}px; height:{H}px; overflow:hidden; }}
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
<div class="page">
<div class="grid"></div>
<div class="frame">
  <div class="rail"><span class="mk">VR</span><span>The Vance Report</span></div>
  <div class="stage">{body}</div>
</div>
<div class="cap">{html.escape(caption)}</div>
<div class="foot"><span>Opinion &middot; not investment advice</span><span>{html.escape(footer)}</span></div>
</div>
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


def thumbnail(out_path: Path, chromium: str, symbols: list[str],
              run_date: str, headline: str) -> Path:
    """
    A 1280x720 thumbnail.

    Legible at the size a phone actually renders it: the tickers are the
    picture, everything else is small. YouTube search results are roughly
    360px wide, so anything below about 40px here is decoration.
    """
    syms = " ".join(symbols[:3]) if symbols else "NO NAMES"
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:1280px; height:720px; background:{INK}; color:{PAPER};
               font-family:{MONO}; overflow:hidden; }}
  .page {{ position:relative; width:1280px; height:720px; overflow:hidden; }}
  .g {{ position:absolute; inset:0;
        background-image:
          linear-gradient(to right, rgba(255,255,255,.03) 1px, transparent 1px),
          linear-gradient(to bottom, rgba(255,255,255,.03) 1px, transparent 1px);
        background-size:64px 64px; }}
  .w {{ position:absolute; inset:0; padding:56px 64px; display:flex;
        flex-direction:column; justify-content:space-between; }}
  .top {{ display:flex; align-items:center; gap:14px; color:{AMBER};
          font-size:23px; letter-spacing:.3em; text-transform:uppercase; }}
  .top .mk {{ border:1px solid {AMBER}; padding:3px 9px; font-size:20px;
              letter-spacing:.12em; }}
  .syms {{ font-size:{150 if len(syms) <= 8 else 112}px; font-weight:600;
           color:{AMBER}; letter-spacing:.02em; line-height:1; }}
  .head {{ font-family:{SERIF}; font-size:62px; font-weight:400;
           line-height:1.04; margin-top:16px; max-width:20ch; }}
  .bot {{ display:flex; justify-content:space-between; align-items:flex-end;
          color:{FAINT}; font-size:22px; letter-spacing:.14em;
          text-transform:uppercase; }}
  .bar {{ position:absolute; left:0; right:0; bottom:0; height:9px;
          background:{AMBER}; }}

</style></head><body>
<div class="page">
<div class="g"></div>
<div class="w">
  <div class="top"><span class="mk">VR</span><span>The Vance Report</span></div>
  <div>
    <div class="syms">{html.escape(syms)}</div>
    <div class="head">{html.escape(headline)}</div>
  </div>
  <div class="bot"><span>{html.escape(run_date)}</span><span>Opinion &middot; not advice</span></div>
</div>
<div class="bar"></div>
</div>
</body></html>"""
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = out_path.with_suffix(".html")
    src.write_text(page, encoding="utf-8")
    subprocess.run(
        [chromium, "--headless", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--force-device-scale-factor=1",
         "--no-first-run", "--no-default-browser-check",
         "--disable-background-networking", "--disable-component-update",
         "--disable-client-side-phishing-detection", "--disable-sync",
         "--disable-default-apps", "--disable-extensions",
         "--metrics-recording-only", "--safebrowsing-disable-auto-update",
         "--disable-domain-reliability", "--disable-breakpad",
         "--virtual-time-budget=4000",
         f"--user-data-dir={out_path.parent / '_chrome-profile'}",
         f"--window-size=1280,{720 + VIEWPORT_SLACK}",
         f"--screenshot={out_path}", src.as_uri()],
        check=True, capture_output=True, timeout=120)
    trimmed = out_path.with_name(out_path.stem + "-1280x720.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_path),
                    "-vf", "crop=1280:720:0:0", str(trimmed)],
                   check=True, capture_output=True, timeout=120)
    trimmed.replace(out_path)
    return out_path


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
             f"--window-size={W},{H + VIEWPORT_SLACK}",
             f"--screenshot={png}", page.as_uri()],
            check=True, capture_output=True, timeout=120,
        )
        if not png.exists():
            raise RuntimeError(f"chromium produced no PNG for frame {i}")
        paths.append(png)
    return paths


def social_card(out_path: Path, chromium: str, symbols: list[str]) -> Path:
    """
    The 1200x630 card that link previews use (og:image / twitter:image).

    Rebuilt on every run so the site's preview names today's tickers rather
    than a fixed slogan. The page it advertises is the screen, so the card
    says what the screen does, in the words someone would search for.
    """
    W2, H2 = 1200, 630
    line = (", ".join(symbols[:4]) + (" and more" if len(symbols) > 4 else "")
            if symbols else "No name cleared the screen today")
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:{W2}px; height:{H2}px; background:{INK}; color:{PAPER};
               font-family:{MONO}; overflow:hidden; }}
  .page {{ position:relative; width:{W2}px; height:{H2}px; overflow:hidden; }}
  .g {{ position:absolute; inset:0;
        background-image:
          linear-gradient(to right, rgba(255,255,255,.03) 1px, transparent 1px),
          linear-gradient(to bottom, rgba(255,255,255,.03) 1px, transparent 1px);
        background-size:60px 60px; }}
  .w {{ position:absolute; inset:0; padding:60px 72px; display:flex;
        flex-direction:column; justify-content:space-between; }}
  .top {{ display:flex; align-items:center; gap:14px; color:{AMBER};
          font-size:21px; letter-spacing:.3em; text-transform:uppercase; }}
  .mk {{ border:1px solid {AMBER}; padding:3px 9px; font-size:18px;
         letter-spacing:.12em; }}
  .rule {{ height:4px; background:{AMBER}; width:110px; margin-bottom:24px; }}
  h1 {{ font-family:{SERIF}; font-weight:400; font-size:76px; line-height:1.0;
        letter-spacing:-.02em; max-width:15ch; }}
  .sub {{ color:{DIM}; font-size:25px; line-height:1.45; margin-top:20px;
          max-width:44ch; }}
  .bot {{ display:flex; justify-content:space-between; align-items:flex-end;
          color:{FAINT}; font-size:19px; letter-spacing:.14em;
          text-transform:uppercase; }}
</style></head><body>
<div class="page">
  <div class="g"></div>
  <div class="w">
    <div class="top"><span class="mk">VR</span><span>The Vance Report</span></div>
    <div>
      <div class="rule"></div>
      <h1>Find the signal inside the selloff</h1>
      <p class="sub">A daily screen for US stocks that fell 15% in five days and
      still hold net cash worth 30% or more of the share price. Today:
      {html.escape(line)}.</p>
    </div>
    <div class="bot"><span>thevancereport.com</span><span>Opinion &middot; not investment advice</span></div>
  </div>
</div>
</body></html>"""
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = out_path.with_suffix(".html")
    src.write_text(page, encoding="utf-8")
    subprocess.run(
        [chromium, "--headless", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", "--force-device-scale-factor=1",
         "--no-first-run", "--no-default-browser-check",
         "--disable-background-networking", "--disable-component-update",
         "--disable-client-side-phishing-detection", "--disable-sync",
         "--disable-default-apps", "--disable-extensions",
         "--metrics-recording-only", "--safebrowsing-disable-auto-update",
         "--disable-domain-reliability", "--disable-breakpad",
         "--virtual-time-budget=4000",
         f"--user-data-dir={out_path.parent / '_chrome-profile'}",
         f"--window-size={W2},{H2 + VIEWPORT_SLACK}",
         f"--screenshot={out_path}", src.as_uri()],
        check=True, capture_output=True, timeout=120)
    trimmed = out_path.with_name(out_path.stem + "-trim.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_path),
                    "-vf", f"crop={W2}:{H2}:0:0", str(trimmed)],
                   check=True, capture_output=True, timeout=120)
    trimmed.replace(out_path)
    return out_path
