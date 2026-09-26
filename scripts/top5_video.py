#!/usr/bin/env python3
"""
The daily top-five video, built from the screen file and nothing else.

    python3 scripts/top5_video.py build   --out build [--silent]
    python3 scripts/top5_video.py publish --build build
    python3 scripts/top5_video.py site    --build build

build    Reads value_screen.json (tonight's ranking), screen_history.json (the
         session before, for what changed) and the latest top-five-*.html (for
         the company names the notes use). Writes, into --out:
             briefing.mp4      1920x1080, narrated
             briefing.srt      captions
             thumbnail.png     1280x720
             title.txt, description.txt, tags.json, meta.json
publish  Uploads briefing.mp4 to YouTube (secrets YT_CLIENT_ID,
         YT_CLIENT_SECRET, YT_REFRESH_TOKEN; privacy from YT_PRIVACY,
         default public), adds captions and the thumbnail, then checks
         whether YouTube actually lets people watch it. Writes
         build/published.json.
site     Records the video in daily_video.json. If it is watchable, also puts
         it in top5.json (when that file is on tonight's screen) and at the
         top of tonight's note page (when the page exists).

Narration uses piper, the same free voice the earlier briefing used. Without
piper (--silent, or no model) each slide is timed from its word count, which
is for checking the pictures locally and is never uploaded.

Every figure spoken or shown comes from value_screen.json or is arithmetic on
it. Nothing here says what to buy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://thevancereport.com"
CHANNEL = os.environ.get("YT_CHANNEL_ID", "UCplN4kMRgUbAI9pT31DCbAw")
W, H = 1920, 1080
FPS = 30

# ---------------------------------------------------------------- data ----
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
PILLARS = [("cheapness", "Cheapness", "#c98500"), ("quality", "Quality", "#3987e5"),
           ("safety", "Safety", "#199e70"), ("confirmation", "Confirmation", "#9085e9")]
SUFFIX = re.compile(r",?\s+(Inc\.?|Corp\.?|Corporation|Ltd\.?|plc|Co\.?|Holdings?|Group)$", re.I)


def long_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {MONTHS[m - 1]} {y}"


def title_case(name: str) -> str:
    small = {"and", "of", "the", "for", "in", "on", "at", "a", "an", "&"}
    words = re.sub(r"\s+", " ", name.strip()).lower().split(" ")
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w in small) else re.sub(r"(^|[-/(])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w))
    s = " ".join(out)
    s = re.sub(r"\bInc\b\.?", "Inc.", s)
    return re.sub(r"\bCorp\b\.?", "Corp.", s)


def short(name: str) -> str:
    return SUFFIX.sub("", name).strip() or name


def load(root: Path) -> dict:
    vs = json.loads((root / "value_screen.json").read_text(encoding="utf-8"))
    meta = vs["_meta"]
    as_of = meta["as_of"]
    hist = {}
    hp = root / "screen_history.json"
    if hp.exists():
        hist = json.loads(hp.read_text(encoding="utf-8"))
    sessions = [s for s in hist.get("sessions", []) if s.get("session", "") < as_of]
    prev = sessions[-1] if sessions else None
    prev_by = {x["symbol"]: x for x in (prev["top"] if prev else [])}
    prev_five = [x["symbol"] for x in (prev["top"][:5] if prev else [])]

    # Company names as the hand-written notes spell them ("Charles River Labs").
    names: dict[str, str] = {}
    for page in sorted(glob.glob(str(root / "top-five-*.html")))[-3:]:
        txt = Path(page).read_text(encoding="utf-8")
        for sym, nm in re.findall(r'<td class="sym">([A-Z.\-]+)<small>([^<]*?) &middot;', txt):
            names[sym] = html.unescape(nm)

    cos = []
    for r in vs["ranked"][:5]:
        pr = prev_by.get(r["symbol"])
        order = sorted((p[0] for p in PILLARS), key=lambda k: -(r["pillars"].get(k) or 0))
        cos.append({
            "r": r, "symbol": r["symbol"],
            "name": names.get(r["symbol"]) or title_case(r["name"]),
            "prev": pr,
            "move": (r["price"] / pr["price"] - 1) * 100 if pr and pr.get("price") else None,
            "likes": order[:2], "dislike": order[3],
        })
    syms = [c["symbol"] for c in cos]
    entered = [s for s in syms if prev_five and s not in prev_five]
    left = [s for s in prev_five if s not in syms]
    left_names = [short(names.get(s) or title_case(prev_by[s]["name"])) for s in left]
    return {"meta": meta, "as_of": as_of, "cos": cos, "prev": prev,
            "entered": entered, "left": left_names}


# ----------------------------------------------------------- the words ----
def n1(x) -> str:
    return f"{x:.1f}"


def join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def pillar_line(key: str, r: dict, spoken: bool) -> str:
    """One sentence about one of the four measures, from the metrics only."""
    p = round(r["pillars"][key])
    m = r.get("metrics") or {}
    bits = []
    pc = " percent" if spoken else "%"
    if key == "cheapness":
        if m.get("ev_ebit") is not None:
            bits.append(f"enterprise value {n1(m['ev_ebit'])} times operating profit")
        if m.get("ev_fcf") is not None:
            bits.append(f"{n1(m['ev_fcf'])} times free cash flow")
        if (m.get("shareholder_yield_pct") or 0) > 1:
            bits.append(f"a {n1(m['shareholder_yield_pct'])}{pc} trailing shareholder yield")
    elif key == "quality":
        if m.get("roic_pct") is not None:
            bits.append(f"{n1(m['roic_pct'])}{pc} return on invested capital")
        if m.get("accruals") is not None:
            bits.append("profits backed by cash" if m["accruals"] < 0 else "reported profit running ahead of cash")
    elif key == "safety":
        if m.get("net_debt_ebitda") == -1:
            bits.append("net cash")
        elif m.get("net_debt_ebitda") is not None:
            bits.append(f"net debt {n1(m['net_debt_ebitda'])} times earnings before interest, tax and depreciation" if spoken
                        else f"net debt {n1(m['net_debt_ebitda'])}× EBITDA")
        if m.get("interest_cover") == 1000:
            bits.append("no meaningful interest cost")
        elif m.get("interest_cover") is not None:
            bits.append(f"interest covered {n1(m['interest_cover'])} times")
        ni = m.get("net_issuance_pct")
        if ni is not None and abs(ni) >= 0.5:
            bits.append(f"share count {'down' if ni < 0 else 'up'} {n1(abs(ni))}{pc} on the year")
    else:
        mo = m.get("momentum_12_1_pct")
        if mo is not None:
            bits.append(f"{'down' if mo < 0 else 'up'} {n1(abs(mo))}{pc} over twelve months")
        if m.get("price_vs_200d") is not None:
            bits.append(f"at {round(m['price_vs_200d'] * 100)}{pc} of its 200-day average")
    label = dict((k, l) for k, l, _ in PILLARS)[key]
    return f"{label} of {p}" + (": " + join(bits) if bits else "") + "."


def money_spoken(x: float) -> str:
    d = int(x)
    c = round((x - d) * 100)
    if c == 100:
        d, c = d + 1, 0
    return f"{d} dollars" + (f" and {c} cents" if c else "")


def script(data: dict) -> list[dict]:
    """The beats: what is said, and what each slide shows."""
    meta, cos = data["meta"], data["cos"]
    n = f"{int(meta['universe']):,}"
    date = long_date(data["as_of"])
    beats = [{
        "kind": "intro", "chapter": "Intro",
        "say": (f"The five at the top, {date}. Tonight the Vance Value Screener ranked {n} US companies "
                "from their own SEC filings, scoring each against its own sector on four measures: "
                "cheapness, quality, safety and confirmation. These are the five that rank highest, "
                "what the screen likes in each, and what it does not."),
    }]
    top = cos[0]
    lines = []
    if top["prev"]:
        lines.append(f"{short(top['name'])} is first with a score of {n1(top['r']['score'])}"
                     + (", as it was the session before." if top["prev"]["rank"] == 1 else f", up from number {top['prev']['rank']}."))
    else:
        lines.append(f"{short(top['name'])} is first with a score of {n1(top['r']['score'])}.")
    if data["entered"]:
        names = [short(c["name"]) for c in cos if c["symbol"] in data["entered"]]
        lines.append(join(names) + (" joins" if len(names) == 1 else " join") + " the five"
                     + (", and " + join(data["left"]) + (" drops" if len(data["left"]) == 1 else " drop") + " out." if data["left"] else "."))
    elif data["prev"]:
        lines.append("The same five names as the session before.")
    beats.append({"kind": "changes", "chapter": "What changed", "say": " ".join(lines), "lines": lines})
    for c in cos:
        r = c["r"]
        mv = ""
        if c["move"] is not None and abs(c["move"]) >= 0.05:
            mv = f", {'up' if c['move'] > 0 else 'down'} {n1(abs(c['move']))} percent on the session before"
        likes = " ".join(pillar_line(k, r, True) for k in c["likes"])
        dis = pillar_line(c["dislike"], r, True)
        say = (f"Number {r['rank']}: {c['name']}, in {r['sector']}. Score {n1(r['score'])}. "
               f"It closed at {money_spoken(r['price'])}{mv}. "
               f"What the screen likes. {likes} What it does not like. {dis}")
        beats.append({"kind": "company", "chapter": f"{r['rank']}. {short(c['name'])}", "co": c, "say": say,
                      "likes": [pillar_line(k, r, False) for k in c["likes"]], "dislike": pillar_line(c["dislike"], r, False)})
    beats.append({
        "kind": "outro", "chapter": "How to use this",
        "say": ("A high rank is a place to start reading, not a name to buy. In testing, this ranking put the "
                "universe in order, but the top of it performed about in line with the market. The full note, "
                "with every figure, is on the vance report dot com. This is educational research, not investment advice."),
    })
    return beats


# ------------------------------------------------------------ pictures ----
def fonts() -> dict:
    from PIL import ImageFont
    fdir = Path(os.environ.get("FONT_DIR", ROOT / "build-fonts"))
    def pick(names, size):
        for nm in names:
            for p in [fdir / nm, *map(Path, glob.glob(f"/usr/share/fonts/**/{nm}", recursive=True))]:
                if p.exists():
                    return ImageFont.truetype(str(p), size)
        for fb in ["DejaVuSerif.ttf", "DejaVuSans.ttf"]:
            for p in glob.glob(f"/usr/share/fonts/**/{fb}", recursive=True):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()
    serif = ["InstrumentSerif-Regular.ttf", "DejaVuSerif.ttf"]
    italic = ["InstrumentSerif-Italic.ttf", "DejaVuSerif-Italic.ttf"]
    mono = ["IBMPlexMono-Regular.ttf", "DejaVuSansMono.ttf"]
    monob = ["IBMPlexMono-Medium.ttf", "IBMPlexMono-SemiBold.ttf", "DejaVuSansMono-Bold.ttf"]
    return {
        "hero": pick(serif, 132), "heroi": pick(italic, 132), "h1": pick(serif, 84), "h1i": pick(italic, 84),
        "rank": pick(serif, 300), "h2": pick(serif, 56), "body": pick(serif, 40), "bodyi": pick(italic, 40),
        "mono": pick(mono, 26), "monob": pick(monob, 28), "monos": pick(mono, 22), "big": pick(monob, 44),
    }


INK, INK2, RULE = (19, 15, 10), (28, 22, 17), (55, 45, 35)
PAPER, DIM, FAINT = (231, 227, 216), (174, 165, 157), (129, 115, 101)
AMBER = (224, 163, 60)


def hexrgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def canvas(w=W, h=H):
    """Dark warm ground with the light in the upper right, as on the site."""
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w * 0.82) / w) ** 2 + ((yy - h * 0.05) / h) ** 2)
    glow = np.clip(1.0 - d / 0.95, 0, 1) ** 2
    base = np.array(INK, np.float32)
    warm = np.array((52, 38, 22), np.float32)
    img = base + (warm - base) * glow[..., None]
    return Image.fromarray(img.astype("uint8"), "RGB")


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def spaced(draw, xy, text, font, fill, track=3):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + track
    return x


def footer(d, F, data):
    d.line([(120, H - 96), (W - 120, H - 96)], fill=RULE, width=2)
    spaced(d, (120, H - 74), "THE VANCE REPORT", F["monos"], AMBER, 4)
    txt = f"Educational research, not investment advice · Closing prices of {long_date(data['as_of'])}"
    d.text((W - 120 - d.textlength(txt, font=F["monos"]), H - 74), txt, font=F["monos"], fill=FAINT)


def slide(beat: dict, data: dict, F: dict):
    from PIL import ImageDraw
    img = canvas()
    d = ImageDraw.Draw(img)
    k = beat["kind"]
    if k == "intro":
        spaced(d, (120, 150), "DAILY SCREEN · THE VANCE VALUE SCREENER", F["mono"], AMBER, 5)
        d.text((112, 230), "The five at the top", font=F["hero"], fill=PAPER)
        d.text((116, 390), long_date(data["as_of"]) + ".", font=F["heroi"], fill=AMBER)
        y = 640
        spaced(d, (120, y), f"{int(data['meta']['universe']):,} COMPANIES RANKED FROM THEIR OWN SEC FILINGS", F["mono"], DIM, 4)
        x = 120
        for c in data["cos"]:
            t = f"{c['symbol']}  #{c['r']['rank']}"
            tw = d.textlength(t, font=F["monob"])
            d.rectangle([x, y + 70, x + tw + 48, y + 142], outline=RULE, width=2, fill=INK2)
            d.text((x + 24, y + 88), t, font=F["monob"], fill=PAPER)
            x += tw + 72
    elif k == "changes":
        spaced(d, (120, 150), "SINCE THE SESSION BEFORE", F["mono"], AMBER, 5)
        d.text((112, 220), "What changed", font=F["h1"], fill=PAPER)
        y = 400
        for line in beat["lines"]:
            for l in wrap(d, line, F["body"], W - 360):
                d.text((120, y), l, font=F["body"], fill=PAPER)
                y += 58
            y += 30
        y += 20
        for c in data["cos"]:
            pr = c["prev"]
            was = f"was #{pr['rank']}" if pr else "new to the top"
            mv = f"{c['move']:+.1f}%" if c["move"] is not None else ""
            d.text((120, y), f"#{c['r']['rank']}  {c['symbol']:<6}", font=F["monob"], fill=AMBER)
            d.text((420, y), f"{was:<16} {mv}", font=F["mono"], fill=DIM)
            y += 50
    elif k == "company":
        c, r = beat["co"], beat["co"]["r"]
        d.text((96, 70), str(r["rank"]), font=F["rank"], fill=AMBER)
        x0 = 380
        spaced(d, (x0, 150), f"{r['symbol']} · {r['sector'].upper()}", F["mono"], AMBER, 4)
        d.text((x0 - 6, 195), c["name"], font=F["h1"], fill=PAPER)
        m = r.get("metrics") or {}
        facts = [("PRICE", f"${r['price']:,.2f}"), ("SCORE", n1(r["score"]))]
        if m.get("ev_ebit") is not None: facts.append(("EV/EBIT", f"{n1(m['ev_ebit'])}×"))
        if m.get("ev_fcf") is not None: facts.append(("EV/FCF", f"{n1(m['ev_fcf'])}×"))
        if m.get("roic_pct") is not None: facts.append(("ROIC", f"{n1(m['roic_pct'])}%"))
        if m.get("momentum_12_1_pct") is not None: facts.append(("12-MONTH", f"{m['momentum_12_1_pct']:+.1f}%"))
        x = x0
        for lab, val in facts:
            spaced(d, (x, 318), lab, F["monos"], FAINT, 3)
            d.text((x, 348), val, font=F["big"], fill=PAPER)
            x += max(d.textlength(val, font=F["big"]), 120) + 64
        # four bars
        y = 460
        weights = data["meta"].get("weights") or {}
        for key, lab, col in PILLARS:
            v = float(r["pillars"].get(key) or 0)
            d.text((120, y), lab, font=F["mono"], fill=PAPER)
            wt = f"{round((weights.get(key) or 0) * 100)}%"
            d.text((120 + d.textlength(lab + " ", font=F["mono"]), y), wt, font=F["mono"], fill=FAINT)
            bx0, bx1 = 440, 1080
            d.rectangle([bx0, y + 6, bx1, y + 30], fill=(40, 32, 25))
            d.rectangle([bx0, y + 6, bx0 + (bx1 - bx0) * v / 100, y + 30], fill=hexrgb(col))
            d.text((bx1 + 24, y), str(round(v)), font=F["monob"], fill=PAPER)
            y += 64
        # likes / doesn't
        bx, bw = 1230, W - 120 - 1230
        d.line([(bx - 40, 460), (bx - 40, 460 + 4 * 64 - 20)], fill=RULE, width=2)
        spaced(d, (bx, 452), "WHAT THE SCREEN LIKES", F["monos"], (97, 168, 135), 3)
        yy = 490
        for l in wrap(d, " ".join(beat["likes"]), F["monos"], bw)[:6]:
            d.text((bx, yy), l, font=F["monos"], fill=PAPER); yy += 32
        yy += 20
        spaced(d, (bx, yy), "WHAT IT DOESN'T", F["monos"], (204, 98, 80), 3)
        yy += 38
        for l in wrap(d, beat["dislike"], F["monos"], bw)[:4]:
            d.text((bx, yy), l, font=F["monos"], fill=PAPER); yy += 32
        pr = c["prev"]
        note = (f"The session before: #{pr['rank']} at ${pr['price']:,.2f}, score {n1(pr['score'])}" if pr else "New to the top of the board this session")
        d.text((120, 780), note, font=F["bodyi"], fill=DIM)
    else:
        spaced(d, (120, 150), "HOW TO USE THIS", F["mono"], AMBER, 5)
        y = 240
        for l in wrap(d, "A high rank is a place to start reading, not a name to buy.", F["h1"], W - 240):
            d.text((112, y), l, font=F["h1"], fill=PAPER); y += 100
        y += 30
        for l in wrap(d, "In testing, this ranking ordered the universe, but the top of it performed about in line with the market.", F["body"], W - 480):
            d.text((120, y), l, font=F["body"], fill=DIM); y += 56
        y += 60
        spaced(d, (120, y), "THE FULL NOTE, WITH EVERY FIGURE", F["mono"], FAINT, 4)
        d.text((120, y + 44), "thevancereport.com", font=F["h2"], fill=AMBER)
    footer(d, F, data)
    return img


def thumbnail(data, F):
    from PIL import ImageDraw
    img = canvas(1280, 720)
    d = ImageDraw.Draw(img)
    spaced(d, (72, 70), "THE VANCE REPORT · DAILY SCREEN", F["monos"], AMBER, 4)
    d.text((64, 120), "The five", font=F["hero"], fill=PAPER)
    d.text((64, 262), "at the top", font=F["heroi"], fill=AMBER)
    d.text((72, 440), long_date(data["as_of"]), font=F["h2"], fill=PAPER)
    x = 72
    for c in data["cos"]:
        t = c["symbol"]
        tw = d.textlength(t, font=F["monob"])
        d.rectangle([x, 560, x + tw + 40, 626], outline=RULE, width=2, fill=INK2)
        d.text((x + 20, 576), t, font=F["monob"], fill=PAPER)
        x += tw + 60
    return img


# --------------------------------------------------------------- voice ----
def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / float(w.getframerate())


def silent_wav(p: Path, seconds: float, rate=22050):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


_PIPER = None


def piper_command(model: str):
    """Find a working way to call piper (its flags changed between versions)."""
    global _PIPER
    if _PIPER is not None:
        return _PIPER or None
    cands = []
    for launcher in ([sys.executable, "-m", "piper"], ["piper"]):
        for flag in ("--output-file", "--output_file", "-f"):
            cands.append(launcher + ["--model", model, flag])
    with tempfile.TemporaryDirectory() as td:
        for c in cands:
            out = Path(td) / "t.wav"
            try:
                subprocess.run(c + [str(out)], input=b"Testing.", capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if out.exists() and out.stat().st_size > 2000:
                _PIPER = c
                print("piper:", " ".join(c[:-1]))
                return c
    _PIPER = False
    return None


def speak(text: str, out: Path, model: str | None) -> bool:
    cmd = piper_command(model) if model else None
    if cmd:
        r = subprocess.run(cmd + [str(out)], input=text.encode("utf-8"), capture_output=True, timeout=600)
        if out.exists() and out.stat().st_size > 2000:
            return True
        print("piper failed:", r.stderr.decode()[-400:])
    silent_wav(out, max(3.0, len(text.split()) / 2.6))
    return False


# --------------------------------------------------------------- build ----
def fmt_ts(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def srt_ts(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        sys.exit(f"command failed: {' '.join(map(str, cmd[:6]))} ...\n{r.stderr.decode()[-1500:]}")


def build(args) -> int:
    root = Path(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = load(root)
    beats = script(data)
    F = fonts()
    model = None if args.silent else os.environ.get("PIPER_VOICE")
    if model and not Path(model).exists():
        print(f"voice model {model} missing; building silent")
        model = None
    work = out / "work"
    work.mkdir(exist_ok=True)
    narrated = True
    PAD_HEAD, PAD_TAIL = 0.35, 0.75
    t = 0.0
    for i, b in enumerate(beats):
        img = slide(b, data, F)
        b["png"] = work / f"slide{i:02d}.png"
        img.save(b["png"])
        wav = work / f"voice{i:02d}.wav"
        ok = speak(b["say"], wav, model)
        narrated = narrated and ok
        b["voice"] = wav
        b["dur"] = PAD_HEAD + wav_seconds(wav) + PAD_TAIL
        b["start"] = t
        t += b["dur"]
    total = t

    # audio: each beat padded, joined, levelled for speech
    parts = []
    for i, b in enumerate(beats):
        p = work / f"pad{i:02d}.wav"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(b["voice"]), "-af",
             f"aresample=48000,adelay={int(PAD_HEAD * 1000)}:all=1,apad=pad_dur={PAD_TAIL}",
             "-ac", "1", "-ar", "48000", str(p)])
        parts.append(p)
    lst = work / "audio.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    voice = work / "voice.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" if narrated else "anull", "-ar", "48000", str(voice)])
    total = wav_seconds(voice)

    # video: each slide held for its beat
    vl = work / "video.txt"
    body = ""
    for b in beats:
        body += f"file '{b['png'].resolve()}'\nduration {b['dur']:.3f}\n"
    body += f"file '{beats[-1]['png'].resolve()}'\n"
    vl.write_text(body)
    mp4 = out / "briefing.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(vl), "-i", str(voice),
         "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
         "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(mp4)])

    # captions: each beat's sentences share its spoken time by length
    srt, k = [], 1
    for b in beats:
        sents = [s for s in re.split(r"(?<=[.:])\s+", b["say"]) if s.strip()]
        spoken = b["dur"] - PAD_HEAD - PAD_TAIL
        tot = sum(len(s) for s in sents) or 1
        s0 = b["start"] + PAD_HEAD
        for s in sents:
            s1 = s0 + spoken * len(s) / tot
            srt.append(f"{k}\n{srt_ts(s0)} --> {srt_ts(s1)}\n{s}\n")
            k += 1
            s0 = s1
    (out / "briefing.srt").write_text("\n".join(srt), encoding="utf-8")

    thumbnail(data, F).save(out / "thumbnail.png")

    date = long_date(data["as_of"])
    names = [short(c["name"]) for c in data["cos"]]
    title = f"The five at the top, {date}: {join(names)}"
    if len(title) > 100:
        title = f"The five at the top, {date}: " + ", ".join(c["symbol"] for c in data["cos"])
    chapters, merged = [], []
    for b in beats:  # YouTube ignores chapter lists with any chapter under 10s
        if merged and b["dur"] < 10:
            continue
        merged.append(b)
    chapters = [{"title": b["chapter"], "start": round(b["start"], 2)} for b in merged]
    head = (f"The five highest-ranked US companies on the Vance Value Screener on the close of {date}: "
            f"{join(names)}. {int(data['meta']['universe']):,} companies ranked from their own SEC filings, "
            "each scored against its own sector on cheapness, quality, safety and confirmation.")
    desc = (head + "\n\n" + "\n".join(f"{fmt_ts(c['start'])} {c['title']}" for c in chapters) +
            f"\n\nToday's ranking and the full notes: {SITE}/\nHow the screener works: {SITE}/vance-value-screener.html\n\n"
            "A high rank is a place to start reading, not a name to buy. Educational research only, not investment advice. "
            "The Vance Report is not a registered investment adviser or broker-dealer. Prices are the session's official closes; "
            "everything else comes from each company's most recent SEC filings, which can lag the news.\n\n"
            "Narrated with a synthetic voice from the screen's own figures.\n\n"
            + " ".join("#" + c["symbol"] for c in data["cos"]) + " #valueinvesting #stockscreener")
    (out / "title.txt").write_text(title + "\n", encoding="utf-8")
    (out / "description.txt").write_text(desc + "\n", encoding="utf-8")
    tags = ["Vance Value Screener", "stock screener", "value investing", "SEC filings", "sector percentile",
            "daily stock screen"] + [c["symbol"] for c in data["cos"]] + [f"{short(c['name'])} stock" for c in data["cos"][:3]]
    (out / "tags.json").write_text(json.dumps(tags[:15]), encoding="utf-8")
    meta = {"as_of": data["as_of"], "run_date": date, "generated_at": data["meta"].get("generated_at"),
            "symbols": [c["symbol"] for c in data["cos"]], "duration_seconds": round(total, 2),
            "duration": fmt_ts(total), "narrated": narrated, "chapters": chapters, "title": title,
            "thumbnail": "thumbnail.png", "description_head": head}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("as_of", "duration", "narrated", "title")}, indent=1))
    return 0


# ------------------------------------------------------------- publish ----
def publish(args) -> int:
    import requests
    b = Path(args.build)
    meta = json.loads((b / "meta.json").read_text(encoding="utf-8"))
    if not meta.get("narrated"):
        sys.exit("refusing to upload a video built without narration")
    need = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(k)]
    if need:
        sys.exit("missing secret(s): " + ", ".join(need))
    tok = requests.post("https://oauth2.googleapis.com/token", timeout=60, data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    if tok.status_code != 200:
        sys.exit(f"YouTube sign-in failed ({tok.status_code}): {tok.text[:300]}. The refresh token may have expired; see VIDEO_SETUP.md.")
    auth = {"Authorization": "Bearer " + tok.json()["access_token"]}
    privacy = os.environ.get("YT_PRIVACY") or "public"
    body = {"snippet": {"title": (b / "title.txt").read_text(encoding="utf-8").strip()[:100],
                        "description": (b / "description.txt").read_text(encoding="utf-8")[:4950],
                        "tags": json.loads((b / "tags.json").read_text()), "categoryId": os.environ.get("YT_CATEGORY", "27"),
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
                       "containsSyntheticMedia": True}}
    mp4 = b / "briefing.mp4"
    size = mp4.stat().st_size
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                         timeout=120, data=json.dumps(body).encode(),
                         headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
    if init.status_code not in (200, 201):
        sys.exit(f"upload refused ({init.status_code}): {init.text[:400]}")
    url, sent, vid, res = init.headers["Location"], 0, None, None
    with mp4.open("rb") as fh:
        while sent < size:
            chunk = fh.read(8 * 1024 * 1024)
            r = requests.put(url, data=chunk, timeout=600, headers={**auth, "Content-Length": str(len(chunk)),
                             "Content-Range": f"bytes {sent}-{sent + len(chunk) - 1}/{size}"})
            if r.status_code in (200, 201):
                res = r.json(); vid = res["id"]; break
            if r.status_code == 308:
                rng = r.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else sent + len(chunk)
                fh.seek(sent)
                continue
            sys.exit(f"upload failed ({r.status_code}): {r.text[:400]}")
    if not vid:
        sys.exit("upload ended without a video id")
    landed = (res.get("snippet") or {}).get("channelId")
    if CHANNEL and landed and landed != CHANNEL:
        sys.exit(f"The video went to channel {landed}, not The Vance Report ({CHANNEL}). Delete https://youtu.be/{vid} "
                 "and reissue the YouTube sign-in as the channel owner.")
    status = (res.get("status") or {})
    print(f"uploaded {vid}: requested {privacy}, YouTube says {status.get('privacyStatus')} {status.get('uploadStatus', '')}")
    # captions and thumbnail are nice-to-have; never fail the run over them
    try:
        cap = requests.post("https://www.googleapis.com/upload/youtube/v3/captions?uploadType=multipart&part=snippet",
                            timeout=180, headers=auth, files={
                                "metadata": ("m.json", json.dumps({"snippet": {"videoId": vid, "language": "en", "name": "English", "isDraft": False}}), "application/json"),
                                "file": ("c.srt", (b / "briefing.srt").read_bytes(), "application/octet-stream")})
        print("captions:", cap.status_code)
    except Exception as e:  # noqa: BLE001
        print("captions skipped:", e)
    try:
        th = requests.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": vid},
                           timeout=180, headers={**auth, "Content-Type": "image/png"}, data=(b / "thumbnail.png").read_bytes())
        print("thumbnail:", th.status_code, "" if th.status_code < 300 else "(custom thumbnails need a verified channel)")
    except Exception as e:  # noqa: BLE001
        print("thumbnail skipped:", e)
    # Is it actually watchable? Videos from an API project that YouTube has not
    # audited are locked private whatever we asked for. oEmbed answers only for
    # videos anyone can watch.
    visible = False
    if privacy in ("public", "unlisted"):
        for _ in range(10):
            o = requests.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}, timeout=30)
            if o.status_code == 200:
                visible = True
                break
            time.sleep(30)
    pub = {"date": meta["as_of"], "video_id": vid, "duration": meta["duration"], "title": meta["title"],
           "requested": privacy, "visible": visible, "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (b / "published.json").write_text(json.dumps(pub, indent=2) + "\n")
    print(json.dumps(pub, indent=1))
    if privacy == "public" and not visible:
        print("::warning::YouTube uploaded the video but is keeping it private. New YouTube API projects are held private "
              "until Google audits them (see VIDEO_SETUP.md). Until then, make it public by hand in YouTube Studio.")
    return 0


# ---------------------------------------------------------------- site ----
FIG_RE = re.compile(r'<figure class="note-video" data-control="note-video">[\s\S]*?</figure>\n?')


def note_figure(vid: str, duration: str) -> str:
    art = "an" if re.match(r"^(8|11|18)\b", duration) else "a"
    return ('<figure class="note-video" data-control="note-video">\n'
            f'<button type="button" class="note-video-thumb" data-yt="{vid}" aria-label="Play the video of this note">'
            f'<img src="https://i.ytimg.com/vi/{vid}/hqdefault.jpg" alt="" loading="lazy" decoding="async" />'
            '<span class="note-video-play" aria-hidden="true"></span></button>\n'
            f'<figcaption>This note as {art} {duration} video, read aloud. '
            f'<a href="https://www.youtube.com/watch?v={vid}">Watch on YouTube</a>.</figcaption>\n</figure>\n')


def site(args) -> int:
    root = Path(args.root)
    pub_p = Path(args.build) / "published.json"
    if not pub_p.exists():
        print("nothing published; site unchanged")
        return 0
    pub = json.loads(pub_p.read_text())
    (root / "daily_video.json").write_text(json.dumps({
        "_about": "Tonight's automatic video. The control room's note builder puts it on the note when visible is true.",
        **pub}, indent=2) + "\n", encoding="utf-8")
    changed = ["daily_video.json"]
    if pub.get("visible"):
        t5p = root / "top5.json"
        t5 = json.loads(t5p.read_text(encoding="utf-8"))
        if t5.get("date") == pub["date"] and not t5.get("video_id"):
            t5["video_id"], t5["duration"] = pub["video_id"], pub["duration"]
            t5.pop("orientation", None)
            t5p.write_text(json.dumps(t5, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            changed.append("top5.json")
        page = root / f"top-five-{pub['date']}.html"
        if page.exists():
            txt = page.read_text(encoding="utf-8")
            if not FIG_RE.search(txt) and 'class="t5-video"' not in txt and '<div class="prose">' in txt and 'id="note-video-css"' in txt:
                at = txt.index('<div class="prose">') + len('<div class="prose">')
                page.write_text(txt[:at] + "\n\n" + note_figure(pub["video_id"], pub["duration"]) + txt[at:].lstrip("\n"), encoding="utf-8")
                changed.append(page.name)
    print("changed:", " ".join(changed))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--out", default="build"); b.add_argument("--silent", action="store_true")
    b.add_argument("--keep-work", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("--build", default="build")
    s = sub.add_parser("site"); s.add_argument("--build", default="build")
    for x in (b, p, s):
        x.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    return {"build": build, "publish": publish, "site": site}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
