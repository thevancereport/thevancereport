#!/usr/bin/env python3
"""
Build a special-report video from a script file in specials/.

    python3 scripts/special_video.py specials/iran-war-housing.json --out build-special

Each slide in the file has a "kind" (title, stat, points, flow, bars, grid,
company, table, steps, outro), what it shows, and "say", the narration. The
build writes, into --out, the same files the daily video's publisher reads:
briefing.mp4 (1920x1080, narrated with piper), briefing.srt, thumbnail.png,
title.txt, description.txt, tags.json and meta.json. So the upload is simply

    python3 scripts/top5_video.py publish --build build-special

Every figure on the slides comes from the script file, which takes them from
the report page and its sources.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import top5_video as tv  # noqa: E402

W, H = tv.W, tv.H
FPS = 30
GREEN, RED = (97, 168, 135), (204, 98, 80)


def fit(d, text, font, width):
    """The font, made smaller if needed so text fits in width."""
    size = font.size
    while d.textlength(text, font=font) > width and size > 14:
        size -= 2
        font = font.font_variant(size=size)
    return font


def footer(d, F, date_label):
    d.line([(120, H - 96), (W - 120, H - 96)], fill=tv.RULE, width=2)
    tv.spaced(d, (120, H - 74), "THE VANCE REPORT · SPECIAL REPORT", F["monos"], tv.AMBER, 4)
    txt = f"Educational research, not investment advice · {date_label}"
    d.text((W - 120 - d.textlength(txt, font=F["monos"]), H - 74), txt, font=F["monos"], fill=tv.FAINT)


def head(d, F, s, y=150):
    tv.spaced(d, (120, y), s.get("kicker", ""), F["mono"], tv.AMBER, 5)
    yy = y + 60
    for l in tv.wrap(d, s.get("head", ""), F["h1"], W - 240)[:2]:
        d.text((112, yy), l, font=F["h1"], fill=tv.PAPER)
        yy += 100
    return yy + 30


def slide(s: dict, F: dict, date_label: str):
    from PIL import ImageDraw
    img = tv.canvas()
    d = ImageDraw.Draw(img)
    k = s["kind"]
    if k == "title":
        tv.spaced(d, (120, 170), s["kicker"], F["mono"], tv.AMBER, 5)
        d.text((112, 250), s["title"], font=fit(d, s["title"], F["hero"], W - 240), fill=tv.PAPER)
        d.text((116, 410), s["title2"], font=fit(d, s["title2"], F["heroi"], W - 240), fill=tv.AMBER)
        d.text((120, 640), s["sub"], font=F["h2"], fill=tv.PAPER)
        d.text((120, 730), date_label, font=F["body"], fill=tv.DIM)
    elif k == "stat":
        tv.spaced(d, (120, 170), s["kicker"], F["mono"], tv.AMBER, 5)
        d.text((112, 250), s["big"], font=fit(d, s["big"], F["hero"], W - 240), fill=tv.AMBER)
        d.text((120, 440), s["label"], font=F["h2"], fill=tv.PAPER)
        y = 580
        for line in s.get("lines", []):
            for l in tv.wrap(d, line, F["body"], W - 260):
                d.text((120, y), l, font=F["body"], fill=tv.DIM)
                y += 60
            y += 14
    elif k == "points":
        y = head(d, F, s)
        for lab, txt in s["points"]:
            d.text((120, y), lab, font=F["monob"], fill=tv.AMBER)
            lines = tv.wrap(d, txt, F["body"], W - 520)
            for l in lines[:2]:
                d.text((440, y - 6), l, font=F["body"], fill=tv.PAPER)
                y += 56
            y += 24
    elif k == "flow":
        y = head(d, F, s) + 40
        n = len(s["steps"])
        gap = 50
        bw = (W - 240 - gap * (n - 1)) / n
        for i, (st, note) in enumerate(zip(s["steps"], s["notes"])):
            x0 = 120 + i * (bw + gap)
            d.rectangle([x0, y, x0 + bw, y + 200], outline=tv.AMBER if i in (0, n - 1) else tv.RULE, width=3, fill=tv.INK2)
            ty = y + 40
            for l in tv.wrap(d, st, F["h2"], bw - 40)[:2]:
                d.text((x0 + 22, ty), l, font=F["h2"], fill=tv.PAPER)
                ty += 64
            ny = y + 240
            for l in tv.wrap(d, note, F["mono"], bw - 20)[:3]:
                d.text((x0 + 4, ny), l, font=F["mono"], fill=tv.DIM)
                ny += 40
            if i < n - 1:
                ax = x0 + bw + 8
                d.polygon([(ax, y + 85), (ax + gap - 16, y + 100), (ax, y + 115)], fill=tv.AMBER)
    elif k == "bars":
        y0 = head(d, F, s)
        lo, hi = float(s["min"]), float(s["max"])
        base_y, top_y = H - 190, y0 + 60
        n = len(s["bars"])
        slot = (W - 240) / n
        bw = slot * 0.56
        for i, (lab, v, note) in enumerate(s["bars"]):
            x0 = 120 + i * slot + (slot - bw) / 2
            hgt = (base_y - top_y) * (v - lo) / (hi - lo)
            col = tv.AMBER if i in (0, n - 1) else (120, 98, 70)
            d.rectangle([x0, base_y - hgt, x0 + bw, base_y], fill=col)
            val = f"{v:.2f}{s.get('unit', '')}"
            d.text((x0 + (bw - d.textlength(val, font=F["monob"])) / 2, base_y - hgt - 48), val, font=F["monob"], fill=tv.PAPER)
            d.text((x0 + (bw - d.textlength(lab, font=F["mono"])) / 2, base_y + 14), lab, font=F["mono"], fill=tv.DIM)
            if note:
                d.text((x0 + (bw - d.textlength(note, font=F["monos"])) / 2, base_y - hgt - 84), note, font=F["monos"], fill=tv.FAINT)
        d.line([(120, base_y), (W - 120, base_y)], fill=tv.RULE, width=2)
    elif k == "grid":
        y = head(d, F, s)
        cw, ch = (W - 240 - 80) / 3, 230
        for i, (big, txt) in enumerate(s["cells"]):
            r, c = divmod(i, 3)
            x0, y0 = 120 + c * (cw + 40), y + r * (ch + 30)
            d.rectangle([x0, y0, x0 + cw, y0 + ch], outline=tv.RULE, width=2, fill=tv.INK2)
            d.text((x0 + 28, y0 + 26), big, font=F["big"], fill=tv.AMBER)
            ty = y0 + 104
            for l in tv.wrap(d, txt, F["mono"], cw - 56)[:3]:
                d.text((x0 + 28, ty), l, font=F["mono"], fill=tv.PAPER)
                ty += 36
    elif k == "company":
        tv.spaced(d, (120, 150), s["kicker"], F["mono"], tv.AMBER, 5)
        d.text((112, 200), s["name"], font=F["h1"], fill=tv.PAPER)
        tv.spaced(d, (120, 310), s["line"].upper(), F["mono"], tv.DIM, 3)
        y = 390
        ncol = 3 if len(s["facts"]) > 4 else 2
        cw = (W - 240 - 30 * (ncol - 1)) / ncol
        for i, (lab, val) in enumerate(s["facts"]):
            r, c = divmod(i, ncol)
            x0, y0 = 120 + c * (cw + 30), y + r * 150
            tv.spaced(d, (x0, y0), lab.upper(), F["monos"], tv.FAINT, 3)
            d.text((x0, y0 + 34), val, font=fit(d, val, F["big"], cw - 30), fill=tv.PAPER)
        qy = y + ((len(s["facts"]) + ncol - 1) // ncol) * 150 + 20
        d.line([(120, qy), (W - 120, qy)], fill=tv.RULE, width=2)
        for l in tv.wrap(d, s["quote"], F["bodyi"], W - 240)[:3]:
            qy += 20
            d.text((120, qy), l, font=F["bodyi"], fill=tv.DIM)
            qy += 40
    elif k == "table":
        y = head(d, F, s)
        xs = [120, 1000, 1330, 1580]
        for x, c in zip(xs, s["cols"]):
            tv.spaced(d, (x, y), c.upper(), F["monos"], tv.FAINT, 3)
        y += 50
        for row in s["rows"]:
            d.line([(120, y), (W - 120, y)], fill=tv.RULE, width=1)
            y += 18
            for j, (x, cell) in enumerate(zip(xs, row)):
                col = RED if cell.startswith("−") else tv.PAPER
                d.text((x, y), cell, font=F["body"] if j == 0 else F["monob"], fill=col)
            y += 66
    elif k == "steps":
        y = head(d, F, s, y=130)
        for i, (when, what, note) in enumerate(s["steps"]):
            d.ellipse([120, y + 6, 168, y + 54], outline=tv.AMBER, width=3)
            num = str(i + 1)
            d.text((144 - d.textlength(num, font=F["monob"]) / 2, y + 12), num, font=F["monob"], fill=tv.AMBER)
            tv.spaced(d, (200, y + 16), when.upper(), F["mono"], tv.AMBER, 3)
            d.text((420, y), what, font=F["h2"], fill=tv.PAPER)
            d.text((1100, y + 16), note, font=F["mono"], fill=tv.DIM) if d.textlength(note, font=F["mono"]) < W - 1220 else \
                d.text((420, y + 66), note, font=F["mono"], fill=tv.DIM)
            y += 112 if d.textlength(note, font=F["mono"]) >= W - 1220 else 96
    else:  # outro
        tv.spaced(d, (120, 200), s["kicker"], F["mono"], tv.AMBER, 5)
        d.text((112, 280), s["head"], font=F["hero"], fill=tv.AMBER)
        y = 520
        for line in s.get("lines", []):
            d.text((120, y), line, font=F["h2"], fill=tv.PAPER)
            y += 90
    footer(d, F, date_label)
    return img


def thumbnail(spec: dict, F: dict):
    from PIL import ImageDraw
    img = tv.canvas(1280, 720)
    d = ImageDraw.Draw(img)
    tv.spaced(d, (72, 64), "THE VANCE REPORT · SPECIAL REPORT", F["monos"], tv.AMBER, 4)
    t = spec["thumb"] if "thumb" in spec else ["The Iran war", "and your mortgage", "5.98% → 7.28%"]
    d.text((64, 120), t[0], font=F["hero"], fill=tv.PAPER)
    d.text((64, 262), t[1], font=F["heroi"], fill=tv.AMBER)
    d.text((72, 470), t[2], font=F["big"], fill=tv.PAPER)
    d.text((72, 560), "Opendoor · builders · what recovery looks like", font=F["mono"], fill=tv.DIM)
    return img


def build(args) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    F = tv.fonts()
    model = None if args.silent else os.environ.get("PIPER_VOICE")
    if model and not Path(model).exists():
        print(f"voice model {model} missing; building silent")
        model = None
    y, m, dd = (int(x) for x in spec["date"].split("-"))
    date_label = f"{tv.MONTHS[m - 1]} {dd}, {y}"
    work = out / "work"
    work.mkdir(exist_ok=True)
    narrated, PAD_HEAD, PAD_TAIL = True, 0.4, 0.9
    t = 0.0
    slides = spec["slides"]
    for i, s in enumerate(slides):
        s["png"] = work / f"slide{i:02d}.png"
        slide(s, F, date_label).save(s["png"])
        wav = work / f"voice{i:02d}.wav"
        narrated = tv.speak(s["say"], wav, model) and narrated
        s["voice"] = wav
        s["dur"] = PAD_HEAD + tv.wav_seconds(wav) + PAD_TAIL
        s["start"] = t
        t += s["dur"]
    parts = []
    for i, s in enumerate(slides):
        p = work / f"pad{i:02d}.wav"
        tv.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(s["voice"]), "-af",
                f"aresample=48000,adelay={int(PAD_HEAD * 1000)}:all=1,apad=pad_dur={PAD_TAIL}",
                "-ac", "1", "-ar", "48000", str(p)])
        parts.append(p)
    (work / "audio.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    voice = work / "voice.wav"
    tv.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "audio.txt"),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" if narrated else "anull", "-ar", "48000", str(voice)])
    body = "".join(f"file '{s['png'].resolve()}'\nduration {s['dur']:.3f}\n" for s in slides)
    body += f"file '{slides[-1]['png'].resolve()}'\n"
    (work / "video.txt").write_text(body)
    tv.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(work / "video.txt"),
            "-i", str(voice), "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium",
            "-tune", "stillimage", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-shortest", str(out / "briefing.mp4")])
    total = tv.wav_seconds(voice)
    srt, k = [], 1
    for s in slides:
        sents = [x for x in re.split(r"(?<=[.:?])\s+", s["say"]) if x.strip()]
        spoken = s["dur"] - PAD_HEAD - PAD_TAIL
        tot = sum(len(x) for x in sents) or 1
        s0 = s["start"] + PAD_HEAD
        for x in sents:
            s1 = s0 + spoken * len(x) / tot
            srt.append(f"{k}\n{tv.srt_ts(s0)} --> {tv.srt_ts(s1)}\n{x}\n")
            k += 1
            s0 = s1
    (out / "briefing.srt").write_text("\n".join(srt), encoding="utf-8")
    thumbnail(spec, F).save(out / "thumbnail.png")
    chapters = [{"title": s["chapter"], "start": round(s["start"], 2)} for s in slides if s["dur"] >= 10 or s is slides[0]]
    desc = (spec["description"].split("\n\n", 1)[0] + "\n\n" +
            "\n".join(f"{tv.fmt_ts(c['start'])} {c['title']}" for c in chapters) + "\n\n" +
            spec["description"].split("\n\n", 1)[1])
    (out / "title.txt").write_text(spec["title"][:100] + "\n", encoding="utf-8")
    (out / "description.txt").write_text(desc + "\n", encoding="utf-8")
    (out / "tags.json").write_text(json.dumps(spec["tags"][:15]), encoding="utf-8")
    meta = {"as_of": spec["date"], "slug": spec["slug"], "page": spec["page"], "duration_seconds": round(total, 2),
            "duration": tv.fmt_ts(total), "narrated": narrated, "chapters": chapters, "title": spec["title"]}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("duration", "narrated", "title")}))
    return 0


def page(args) -> int:
    """Put the uploaded video at the top of the report page."""
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    pub = json.loads((Path(args.out) / "published.json").read_text())
    if not pub.get("visible"):
        print("video not watchable yet; page unchanged")
        return 0
    p = tv.ROOT / spec["page"]
    txt = p.read_text(encoding="utf-8")
    vid, dur = pub["video_id"], pub["duration"]
    fig = ('<figure class="note-video">\n'
           f'                <button type="button" class="note-video-thumb" data-yt="{vid}"\n'
           '                  aria-label="Play the video version of this report">\n'
           f'                  <img src="https://i.ytimg.com/vi/{vid}/hqdefault.jpg" alt=""\n'
           '                    loading="lazy" width="1280" height="720" />\n'
           '                  <span class="note-video-play" aria-hidden="true"></span>\n'
           '                </button>\n'
           '                <figcaption>\n'
           f'                  This report as a {dur} video, read aloud.\n'
           f'                  <a href="https://www.youtube.com/watch?v={vid}">Watch on YouTube</a>\n'
           '                </figcaption>\n'
           '              </figure>')
    if "<!--SPECIAL-VIDEO-->" not in txt:
        print("no video placeholder on the page; unchanged")
        return 0
    p.write_text(txt.replace("<!--SPECIAL-VIDEO-->", fig), encoding="utf-8")
    print("video added to", p.name)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--out", default="build-special")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--page", action="store_true", help="after publishing: put the video on the report page")
    a = ap.parse_args()
    return page(a) if a.page else build(a)


if __name__ == "__main__":
    raise SystemExit(main())
