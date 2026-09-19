#!/usr/bin/env python3
"""
Build the daily briefing video from research.json.

    python3 scripts/build_video.py --out build

Produces, in --out:

    briefing.mp4        1920x1080, H.264 + AAC
    briefing.srt        caption track
    title.txt           the YouTube title
    description.txt     the YouTube description, including the whole script
    meta.json           what the uploader and the site need to know

Narration uses piper (https://github.com/OHF-Voice/piper1-gpl). If the binary
or the voice model is missing the build still runs with --silent, which times
each frame from its word count instead. That is for previewing the visuals
locally; it is not what ships.

Nothing here decides what to buy. Every figure it speaks comes out of
research.json or is arithmetic on those figures, and scripts/narration.py is
where the wording lives.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import narration  # noqa: E402
import slides  # noqa: E402

SAMPLE_RATE = 22050
GAP_SECONDS = 0.34          # breath between beats
WORDS_PER_SECOND = 2.6      # only used by --silent
MIN_SECONDS = 1.8


def find_chromium() -> str:
    for cand in (os.environ.get("CHROMIUM_PATH"),
                 shutil.which("chromium"), shutil.which("chromium-browser"),
                 shutil.which("google-chrome"),
                 "/opt/pw-browsers/chromium/chrome-linux/chrome",
                 "/opt/pw-browsers/chromium/chrome"):
        if cand and Path(cand).exists():
            return cand
    for base in Path("/opt/pw-browsers").glob("chromium*/**/chrome"):
        return str(base)
    raise SystemExit("No chromium binary found. Set CHROMIUM_PATH.")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def silence(path: Path, seconds: float, like: Path | None = None) -> None:
    """
    A silent wav. When `like` is given the format is copied from it.

    That matters: the gap clips are concatenated with the narration by stream
    copy, which silently produces garbage if the sample rate or channel count
    differ. Piper voices are not all 22.05kHz, so the gap follows the voice
    rather than a constant.
    """
    channels, width, rate = 1, 2, SAMPLE_RATE
    if like and like.exists():
        with wave.open(str(like), "rb") as src:
            channels, width, rate = (src.getnchannels(), src.getsampwidth(),
                                     src.getframerate())
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (width * channels * int(rate * seconds)))


def piper_command(model: str) -> list[str] | None:
    """
    Work out how to call piper on this machine, once.

    The rewritten piper (pip install piper-tts) takes --output-file and is
    importable as a module; the older standalone binary takes --output_file.
    Both read the line to speak on stdin. Rather than pin a spelling that will
    rot, try each on a throwaway phrase and keep whichever produced a wav.
    """
    candidates: list[list[str]] = []
    for launcher in ([sys.executable, "-m", "piper"],
                     [os.environ.get("PIPER_BIN") or "piper"]):
        if launcher[0] and (len(launcher) > 1 or shutil.which(launcher[0])):
            for flag in ("--output-file", "--output_file"):
                candidates.append(launcher + ["--model", model, flag])

    import tempfile
    for cmd in candidates:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.wav"
            try:
                subprocess.run(cmd + [str(probe)], input="test one two",
                               text=True, check=True, capture_output=True,
                               timeout=240)
            except Exception:                       # noqa: BLE001
                continue
            if probe.exists() and probe.stat().st_size > 1024:
                print("piper: " + " ".join(cmd[:4]) + " " + cmd[-1])
                return cmd
    return None


def speak(text: str, out: Path, cmd: list[str] | None) -> float:
    """Render one beat to a wav. Returns its duration in seconds."""
    if cmd:
        subprocess.run(cmd + [str(out)], input=text, text=True, check=True,
                       capture_output=True, timeout=240)
        return probe_duration(out)
    seconds = max(MIN_SECONDS, len(text.split()) / WORDS_PER_SECOND)
    silence(out, seconds)
    return seconds


def srt_stamp(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--research", default="research.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--piper", default=os.environ.get("PIPER_BIN", "piper"))
    ap.add_argument("--voice", default=os.environ.get("PIPER_VOICE", ""))
    ap.add_argument("--silent", action="store_true",
                    help="skip speech; time frames from word count (preview only)")
    args = ap.parse_args()

    out = Path(args.out)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    research = json.loads(Path(args.research).read_text(encoding="utf-8"))
    generated = (research.get("_meta") or {}).get("generated_at")
    try:
        run_dt = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except Exception:
        run_dt = datetime.now(timezone.utc)
    run_date = run_dt.strftime("%B %-d, %Y")

    voice = args.voice if (args.voice and Path(args.voice).exists()) else None
    tts = None
    if not args.silent:
        if not voice:
            print(f"voice model not found at {args.voice!r}", file=sys.stderr)
        else:
            tts = piper_command(voice)
        if tts is None:
            print("piper unavailable -- falling back to --silent. The visuals "
                  "are right; there is no narration. The workflow refuses to "
                  "publish a build in this state.", file=sys.stderr)

    deck = narration.build(research, run_date)

    # ---- flatten to one frame per beat -------------------------------------
    frames: list[dict] = []
    for slide in deck:
        for b in slide["beats"]:
            frames.append({"kind": slide["kind"], "data": slide["data"],
                           "caption": b["caption"], "say": b["say"],
                           "footer": run_date})
    if not frames:
        print("nothing to say", file=sys.stderr)
        return 1

    # ---- narration ---------------------------------------------------------
    wavs: list[Path] = []
    durations: list[float] = []
    gap = work / "gap.wav"
    for i, f in enumerate(frames):
        w = work / f"a{i:04d}.wav"
        d = speak(f["say"], w, tts)
        if i == 0:
            silence(gap, GAP_SECONDS, like=w)   # match the voice's wav format
        wavs += [w, gap]
        durations.append(d + GAP_SECONDS)
        print(f"  beat {i+1}/{len(frames)}  {d:5.2f}s  {f['caption'][:58]}")

    concat_a = work / "audio.txt"
    concat_a.write_text("".join(f"file '{w.resolve()}'\n" for w in wavs),
                        encoding="utf-8")
    audio = work / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat_a), "-c", "copy", str(audio)], check=True)

    # ---- slides ------------------------------------------------------------
    print("rendering slides")
    pngs = slides.render(frames, work / "frames", find_chromium())

    concat_v = work / "video.txt"
    lines = []
    for png, d in zip(pngs, durations):
        lines.append(f"file '{png.resolve()}'\nduration {d:.3f}\n")
    lines.append(f"file '{pngs[-1].resolve()}'\n")   # concat demuxer needs the tail
    concat_v.write_text("".join(lines), encoding="utf-8")

    mp4 = out / "briefing.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "concat", "-safe", "0", "-i", str(concat_v),
         "-i", str(audio),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-r", "30",
         "-c:a", "aac", "-b:a", "160k", "-shortest",
         "-movflags", "+faststart", str(mp4)], check=True)

    # ---- captions ----------------------------------------------------------
    srt, t = [], 0.0
    for i, (f, d) in enumerate(zip(frames, durations), start=1):
        srt.append(f"{i}\n{srt_stamp(t)} --> {srt_stamp(t + d - 0.05)}\n"
                   f"{f['caption']}\n")
        t += d
    (out / "briefing.srt").write_text("\n".join(srt), encoding="utf-8")

    # ---- text that travels with the video ----------------------------------
    passers = [s for s in deck if s["kind"] == "stock"]
    syms = [s["data"]["symbol"] for s in passers]
    title = (f"Daily cash-cushion screen — {run_date}"
             + (f" — {', '.join(syms)}" if syms else " — no names cleared"))
    (out / "title.txt").write_text(title[:100], encoding="utf-8")

    description = "\n".join([
        f"The Vance Report daily briefing for {run_date}.",
        "",
        "Every trading day a screen runs across every listed US common stock "
        "looking for two things at once: a five-day fall of 15% or more, and "
        "net cash — cash after total liabilities — worth at least 30% of the "
        "share price. Runway, dilution and open-market insider buying are "
        "checked behind those two.",
        "",
        ("Names in this briefing: " + ", ".join(syms)) if syms
        else "No name cleared both gates today.",
        "",
        "HORIZON",
        "These names are looked at on a three to six month view. They are not "
        "intended for day trading or short-term trading of any kind.",
        "",
        "DISCLAIMER",
        "This video is opinion and education. It is not investment advice. The "
        "Vance Report is not a registered investment adviser and not a "
        "broker-dealer, and nothing here is a recommendation to buy or sell any "
        "security. Screened securities are volatile, high risk, and may involve "
        "total loss of principal. Figures are taken from company SEC filings and "
        "may be delayed or inaccurate — verify them before acting. Any price "
        "level mentioned is arithmetic on the screen's own 30% threshold, not a "
        "target and not a suggested entry.",
        "",
        "Full screen and full disclaimer: https://thevancereport.com",
        "",
        "— TRANSCRIPT —",
        "",
        narration.plain_script(deck),
    ])
    (out / "description.txt").write_text(description[:4900], encoding="utf-8")

    meta = {
        "run_date": run_date,
        "generated_at": run_dt.isoformat(),
        "symbols": syms,
        "beats": len(frames),
        "duration_seconds": round(sum(durations), 2),
        "narrated": bool(tts),
        "title": title[:100],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\n{mp4}  ({meta['duration_seconds']:.0f}s, {len(frames)} beats, "
          f"narrated={meta['narrated']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
