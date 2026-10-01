#!/usr/bin/env python3
"""
Lay a music bed under a finished, narrated video, in place.

    python3 scripts/music.py build/briefing.mp4 --date 2026-10-01
    python3 scripts/music.py build/short.mp4    --date 2026-10-01 --slot 1

The tracks are the files in assets/music/ (mp3, m4a, wav, ogg). Only put music
there that is licensed for use in monetised videos with no attribution, such
as YouTube Audio Library tracks marked "Attribution not required", or Content
ID will claim the videos. The track rotates by date, and --slot offsets it,
so the full video (slot 0) and the short (slot 1) use different tracks and
swap them each night.

The bed is levelled to sit well under the voice, ducks further whenever the
voice speaks, fades in and out, and loops if the video is longer than the
track. The picture is copied untouched. With no tracks in the folder this does
nothing, so the videos simply go out without music.
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}
BED_LUFS = -27        # the voice is levelled to -16, so the bed sits ~11 dB under it


def seconds(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def pick(tracks: list[Path], date: str, slot: int = 0) -> Path:
    n = dt.date.fromisoformat(date).toordinal() + slot
    return tracks[n % len(tracks)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--dir", default=str(ROOT / "assets" / "music"))
    ap.add_argument("--slot", type=int, default=0, help="0 for the full video, 1 for the short")
    a = ap.parse_args()
    video = Path(a.video)
    if not video.exists():
        print(f"{video} not found; nothing to do")
        return 0
    folder = Path(a.dir)
    tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in EXTS) if folder.is_dir() else []
    if not tracks:
        print(f"no music in {folder}; {video.name} goes out without a bed")
        return 0
    track = pick(tracks, a.date, a.slot)
    total = seconds(video)
    if total <= 0:
        sys.exit(f"could not read the length of {video}")
    fade_out = max(total - 3.0, 0.0)
    graph = (
        f"[1:a]aresample=48000,loudnorm=I={BED_LUFS}:TP=-4:LRA=11,"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out:.2f}:d=3,atrim=0:{total:.2f}[bed];"
        "[0:a]aresample=48000,asplit=2[voice][key];"
        "[bed][key]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[ducked];"
        "[voice][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[out]"
    )
    tmp = video.with_suffix(".music.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-stream_loop", "-1", "-i", str(track),
                        "-filter_complex", graph, "-map", "0:v", "-map", "[out]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart", "-shortest", str(tmp)], capture_output=True, text=True)
    if r.returncode or not tmp.exists() or seconds(tmp) < total - 1.0:
        tmp.unlink(missing_ok=True)
        sys.exit(f"mixing failed, {video.name} left as it was:\n{r.stderr[-800:]}")
    shutil.move(str(tmp), str(video))
    print(f"{video.name}: music bed from {track.name} ({total:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
