#!/usr/bin/env python3
"""
The daily top-five short: a vertical (9:16) countdown for Instagram Reels and
Facebook, built from the same screen file as the long video.

    python3 scripts/top5_short.py --out build [--silent]

Writes build/short.mp4 (1080x1920, narrated, about a minute) and
build/short.json. It reuses the long video's data, words, voice and colours
(scripts/top5_video.py), so the two never disagree.

Layout keeps everything that matters between y=240 and y=1530: Instagram
draws its own buttons and caption over the top and bottom of a Reel.

Every figure shown or spoken comes from value_screen.json. Nothing here says
what to buy.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import top5_video as tv  # noqa: E402

W, H = 1080, 1920
FPS = 30
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def fonts() -> dict:
    from PIL import ImageFont
    import glob
    fdir = Path(os.environ.get("FONT_DIR", tv.ROOT / "build-fonts"))

    def pick(names, size):
        for nm in names:
            for p in [fdir / nm, *map(Path, glob.glob(f"/usr/share/fonts/**/{nm}", recursive=True))]:
                if p.exists():
                    return ImageFont.truetype(str(p), size)
        return ImageFont.load_default()
    serif = ["InstrumentSerif-Regular.ttf", "DejaVuSerif.ttf"]
    italic = ["InstrumentSerif-Italic.ttf", "DejaVuSerif-Italic.ttf"]
    mono = ["IBMPlexMono-Regular.ttf", "DejaVuSansMono.ttf"]
    monob = ["IBMPlexMono-Medium.ttf", "DejaVuSansMono-Bold.ttf"]
    return {"hero": pick(serif, 150), "heroi": pick(italic, 150), "rank": pick(serif, 380),
            "h1": pick(serif, 92), "h2": pick(serif, 64), "h2i": pick(italic, 64),
            "body": pick(serif, 50), "bodyi": pick(italic, 50),
            "mono": pick(mono, 31), "monob": pick(monob, 36), "big": pick(monob, 68)}


def one_reason(key: str, r: dict, spoken: bool = True) -> str:
    """One plain reason behind one measure, short enough for a phone screen."""
    m = r.get("metrics") or {}
    p = round(r["pillars"][key])
    pc = " percent" if spoken else "%"
    why = None
    if key == "cheapness":
        if m.get("ev_ebit") is not None:
            why = f"valued at {tv.n1(m['ev_ebit'])} times operating profit"
        elif m.get("ev_fcf") is not None:
            why = f"valued at {tv.n1(m['ev_fcf'])} times free cash flow"
    elif key == "quality":
        if m.get("roic_pct") is not None:
            why = f"{tv.n1(m['roic_pct'])}{pc} return on invested capital"
    elif key == "safety":
        nd, ic = m.get("net_debt_ebitda"), m.get("interest_cover")
        if nd == -1:
            why = "more cash than debt"
        elif nd is not None:
            why = (f"net debt {tv.n1(nd)} times yearly earnings before interest, tax and depreciation" if spoken
                   else f"net debt {tv.n1(nd)}× EBITDA")
        elif ic is not None and ic < 1000:
            why = f"interest covered {tv.n1(ic)} times"
    else:
        mo = m.get("momentum_12_1_pct")
        if mo is not None:
            why = f"{'down' if mo < 0 else 'up'} {tv.n1(abs(mo))}{pc} over twelve months"
    label = dict((k, l) for k, l, _ in tv.PILLARS)[key]
    return f"{label} of {p}" + (f": {why}." if why else ".")


def name(c: dict) -> str:
    """"Consolidated Water Co. Ltd." -> "Consolidated Water"."""
    n = c["name"]
    for _ in range(3):
        n = tv.short(n)
    return n


def beats(data: dict) -> list[dict]:
    n = f"{int(data['meta']['universe']):,}"
    out = [{"kind": "intro",
            "say": (f"The five at the top of the Vance Value Screener tonight, out of {n} US companies "
                    "ranked from their own SEC filings. Counting down.")}]
    for c in reversed(data["cos"]):
        r = c["r"]
        like = c["likes"][0]
        out.append({"kind": "company", "co": c, "why": one_reason(like, r, False),
                    "say": (f"Number {WORDS.get(r['rank'], r['rank'])}. {name(c)}. "
                            f"Score {tv.n1(r['score'])}. {one_reason(like, r, True)}")})
    out.append({"kind": "outro",
                "say": ("The full note is free, with every figure and what the screen does not like, on "
                        "the vance report dot com. Educational research, not investment advice.")})
    return out


def centre(d, y, text, font, fill):
    d.text(((W - d.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def footer(d, F, data):
    d.line([(90, 1530), (W - 90, 1530)], fill=tv.RULE, width=2)
    tv.spaced(d, (90, 1552), "THE VANCE REPORT", F["mono"], tv.AMBER, 4)
    t = f"Closes of {tv.long_date(data['as_of'])}"
    d.text((W - 90 - d.textlength(t, font=F["mono"]), 1552), t, font=F["mono"], fill=tv.FAINT)
    t = "Educational research, not investment advice"
    d.text((90, 1598), t, font=F["mono"], fill=tv.FAINT)


def slide(b: dict, data: dict, F: dict):
    from PIL import ImageDraw
    img = tv.canvas(W, H)
    d = ImageDraw.Draw(img)
    if b["kind"] == "intro":
        tv.spaced(d, (90, 250), "DAILY SCREEN", F["mono"], tv.AMBER, 6)
        d.text((80, 300), "The five", font=F["hero"], fill=tv.PAPER)
        d.text((84, 460), "at the top", font=F["heroi"], fill=tv.AMBER)
        d.text((90, 660), tv.long_date(data["as_of"]), font=F["h2"], fill=tv.PAPER)
        y = 790
        for l in tv.wrap(d, f"{int(data['meta']['universe']):,} US companies ranked from their own SEC filings.",
                         F["body"], W - 180):
            d.text((90, y), l, font=F["body"], fill=tv.DIM)
            y += 60
        y += 50
        for c in data["cos"]:
            r = c["r"]
            d.rectangle([90, y, W - 90, y + 96], outline=tv.RULE, width=2, fill=tv.INK2)
            d.text((124, y + 26), f"#{r['rank']}", font=F["monob"], fill=tv.AMBER)
            d.text((230, y + 26), r["symbol"], font=F["monob"], fill=tv.PAPER)
            nm = name(c)
            while d.textlength(nm, font=F["mono"]) > W - 90 - 420 - 30 and len(nm) > 4:
                nm = nm[:-2].rstrip() + "…" if not nm.endswith("…") else nm[:-2].rstrip() + "…"
            d.text((420, y + 30), nm, font=F["mono"], fill=tv.DIM)
            y += 116
    elif b["kind"] == "company":
        c, r = b["co"], b["co"]["r"]
        tv.spaced(d, (90, 250), f"NUMBER {WORDS.get(r['rank'], r['rank']).upper()} OF {int(data['meta']['universe']):,}",
                  F["mono"], tv.AMBER, 5)
        d.text((70, 250), str(r["rank"]), font=F["rank"], fill=tv.AMBER)
        y = 680
        for l in tv.wrap(d, name(c), F["h1"], W - 180)[:2]:
            d.text((86, y), l, font=F["h1"], fill=tv.PAPER)
            y += 104
        tv.spaced(d, (90, y + 10), f"{r['symbol']} · {r['sector'].upper()}", F["mono"], tv.DIM, 4)
        y += 80
        # score and price
        tv.spaced(d, (90, y), "SCORE", F["mono"], tv.FAINT, 3)
        tv.spaced(d, (520, y), "CLOSE", F["mono"], tv.FAINT, 3)
        d.text((90, y + 36), tv.n1(r["score"]), font=F["big"], fill=tv.PAPER)
        d.text((520, y + 36), f"${r['price']:,.2f}", font=F["big"], fill=tv.PAPER)
        y += 140
        for key, lab, col in tv.PILLARS:
            v = float(r["pillars"].get(key) or 0)
            d.text((90, y), lab, font=F["mono"], fill=tv.PAPER)
            bx0, bx1 = 390, W - 190
            d.rectangle([bx0, y + 8, bx1, y + 34], fill=(40, 32, 25))
            d.rectangle([bx0, y + 8, bx0 + (bx1 - bx0) * v / 100, y + 34], fill=tv.hexrgb(col))
            t = str(round(v))
            d.text((W - 90 - d.textlength(t, font=F["monob"]), y), t, font=F["monob"], fill=tv.PAPER)
            y += 64
        y += 20
        tv.spaced(d, (90, y), "WHAT THE SCREEN LIKES MOST", F["mono"], (97, 168, 135), 3)
        y += 46
        for l in tv.wrap(d, b["why"], F["bodyi"], W - 180)[:2]:
            d.text((90, y), l, font=F["bodyi"], fill=tv.PAPER)
            y += 60
    else:
        tv.spaced(d, (90, 250), "A NEW NOTE EVERY WEEKNIGHT", F["mono"], tv.AMBER, 5)
        y = 330
        for l in tv.wrap(d, "Every figure, and what the screen doesn't like, in the free note:", F["h1"], W - 180):
            d.text((86, y), l, font=F["h1"], fill=tv.PAPER)
            y += 106
        y += 30
        d.text((90, y), "thevancereport.com", font=F["h2"], fill=tv.AMBER)
        y += 150
        for l in tv.wrap(d, "A high rank is a place to start reading, not a name to buy.", F["bodyi"], W - 180):
            d.text((90, y), l, font=F["bodyi"], fill=tv.DIM)
            y += 60
    footer(d, F, data)
    return img


def build(args) -> int:
    root, out = Path(args.root), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = tv.load(root)
    bs = beats(data)
    F = fonts()
    model = None if args.silent else os.environ.get("PIPER_VOICE")
    if model and not Path(model).exists():
        print(f"voice model {model} missing; building silent")
        model = None
    work = out / "short-work"
    work.mkdir(exist_ok=True)
    narrated, PAD_HEAD, PAD_TAIL = True, 0.25, 0.45
    for i, b in enumerate(bs):
        b["png"] = work / f"s{i:02d}.png"
        slide(b, data, F).save(b["png"])
        wav = work / f"v{i:02d}.wav"
        narrated = tv.speak(b["say"], wav, model) and narrated
        b["voice"] = wav
        b["dur"] = PAD_HEAD + tv.wav_seconds(wav) + PAD_TAIL
    parts = []
    for i, b in enumerate(bs):
        p = work / f"p{i:02d}.wav"
        tv.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(b["voice"]), "-af",
                f"aresample=48000,adelay={int(PAD_HEAD * 1000)}:all=1,apad=pad_dur={PAD_TAIL}",
                "-ac", "1", "-ar", "48000", str(p)])
        parts.append(p)
    (work / "a.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    voice = work / "voice.wav"
    tv.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "a.txt"),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" if narrated else "anull", "-ar", "48000", str(voice)])
    body = "".join(f"file '{b['png'].resolve()}'\nduration {b['dur']:.3f}\n" for b in bs)
    body += f"file '{bs[-1]['png'].resolve()}'\n"
    (work / "v.txt").write_text(body)
    mp4 = out / "short.mp4"
    tv.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "v.txt"),
            "-i", str(voice), "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium",
            "-tune", "stillimage", "-crf", "20", "-profile:v", "high", "-c:a", "aac", "-b:a", "128k",
            "-ac", "2", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(mp4)])
    total = tv.wav_seconds(voice)
    meta = {"as_of": data["as_of"], "file": "short.mp4", "width": W, "height": H,
            "duration_seconds": round(total, 2), "narrated": narrated,
            "symbols": [c["symbol"] for c in data["cos"]]}
    (out / "short.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if args.keep_work:
        print("slides kept in", work)
    else:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(meta))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build")
    ap.add_argument("--root", default=str(tv.ROOT))
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--keep-work", action="store_true")
    return build(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
