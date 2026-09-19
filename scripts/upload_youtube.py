#!/usr/bin/env python3
"""
Upload the daily briefing to YouTube and record the video id for the site.

    python3 scripts/upload_youtube.py --build build --out video.json

Reads three secrets from the environment. They are issued by Google to a
project you own; nothing here creates them and nothing here sees a password:

    YT_CLIENT_ID       OAuth client id
    YT_CLIENT_SECRET   OAuth client secret
    YT_REFRESH_TOKEN   refresh token for the channel, scope
                       https://www.googleapis.com/auth/youtube.upload

Optional:

    YT_PRIVACY         private (default) | unlisted | public
    YT_CATEGORY        default 22, People & Blogs

The privacy default is deliberate. Until you have watched a few of these end
to end, a scheduled job should not be able to publish to a public channel on
its own. Change it to public in the workflow when you are satisfied.

VIDEO_SETUP.md has the one-time steps for obtaining the refresh token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests is required: pip install requests")

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")
CAPTION_URL = ("https://www.googleapis.com/upload/youtube/v3/captions"
               "?uploadType=multipart&part=snippet")
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK = 8 * 1024 * 1024

# The Vance Report's channel. A channel id is public, not a secret -- it is on
# the channel page. It is here as a guard: if the refresh token ever belongs to
# a different account, the upload lands somewhere unintended and silently. The
# check runs after the upload, because the youtube.upload scope cannot list
# channels; a loud failure with the video id beats no check at all.
EXPECTED_CHANNEL = os.environ.get(
    "YT_CHANNEL_ID", "UCplN4kMRgUbAI9pT31DCbAw")


def access_token() -> str:
    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
               if not os.environ.get(k)]
    if missing:
        sys.exit("missing secret(s): " + ", ".join(missing))
    r = requests.post(TOKEN_URL, timeout=60, data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        sys.exit(f"token refresh failed {r.status_code}: {r.text[:400]}")
    return r.json()["access_token"]


def upload(token: str, mp4: Path, title: str, description: str,
           tags: list[str]) -> str:
    privacy = os.environ.get("YT_PRIVACY", "private")
    if privacy not in ("private", "unlisted", "public"):
        sys.exit(f"YT_PRIVACY must be private, unlisted or public; got {privacy!r}")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4950],
            "tags": tags[:15],
            "categoryId": os.environ.get("YT_CATEGORY", "22"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }

    size = mp4.stat().st_size
    start = requests.post(
        UPLOAD_URL, timeout=120,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size),
                 "X-Upload-Content-Type": "video/mp4"},
        data=json.dumps(body).encode("utf-8"))
    if start.status_code not in (200, 201):
        sys.exit(f"upload init failed {start.status_code}: {start.text[:400]}")
    session = start.headers["Location"]

    # Resumable rather than one shot: a daily job on a shared runner should
    # survive a dropped connection without re-sending the whole file.
    sent = 0
    with mp4.open("rb") as fh:
        while sent < size:
            chunk = fh.read(CHUNK)
            last = sent + len(chunk) - 1
            r = requests.put(
                session, data=chunk, timeout=600,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Length": str(len(chunk)),
                         "Content-Range": f"bytes {sent}-{last}/{size}"})
            if r.status_code in (200, 201):
                resource = r.json()
                vid = resource["id"]
                landed = (resource.get("snippet") or {}).get("channelId")
                print(f"uploaded {vid} ({privacy}) to channel {landed}")
                if EXPECTED_CHANNEL and landed and landed != EXPECTED_CHANNEL:
                    sys.exit(
                        f"Refusing to continue: the video uploaded to channel "
                        f"{landed}, not {EXPECTED_CHANNEL}. The refresh token "
                        f"belongs to a different account. The video exists at "
                        f"https://www.youtube.com/watch?v={vid} and is "
                        f"{privacy} -- delete it there, then reissue the token "
                        f"as the right channel owner.")
                return vid
            if r.status_code == 308:
                rng = r.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else sent + len(chunk)
                fh.seek(sent)
                print(f"  {sent * 100 // size}%")
                continue
            sys.exit(f"upload failed {r.status_code}: {r.text[:400]}")
    sys.exit("upload ended without an id")


def add_captions(token: str, video_id: str, srt: Path) -> None:
    """Best effort. A missing caption track is not worth failing the run over."""
    meta = {"snippet": {"videoId": video_id, "language": "en",
                        "name": "English", "isDraft": False}}
    try:
        r = requests.post(
            CAPTION_URL, timeout=180,
            headers={"Authorization": f"Bearer {token}"},
            files={"metadata": ("metadata.json", json.dumps(meta),
                                "application/json"),
                   "file": (srt.name, srt.read_bytes(), "application/octet-stream")})
        print("captions" if r.status_code in (200, 201)
              else f"captions skipped ({r.status_code}: {r.text[:160]})")
    except Exception as exc:                     # noqa: BLE001
        print(f"captions skipped ({exc})")


def set_thumbnail(token: str, video_id: str, png: Path) -> None:
    """
    Best effort. Custom thumbnails need a verified channel; if yours is not,
    YouTube answers 403 and keeps its auto-generated frame, which is a worse
    thumbnail but not a failed run.
    """
    try:
        r = requests.post(
            THUMB_URL, params={"videoId": video_id}, timeout=180,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "image/png"},
            data=png.read_bytes())
        if r.status_code in (200, 201):
            print("thumbnail set")
        elif r.status_code == 403:
            print("thumbnail skipped: channel not verified for custom "
                  "thumbnails (youtube.com/verify)")
        else:
            print(f"thumbnail skipped ({r.status_code}: {r.text[:160]})")
    except Exception as exc:                     # noqa: BLE001
        print(f"thumbnail skipped ({exc})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default="build")
    ap.add_argument("--out", default="video.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="write video.json from an existing id without uploading")
    ap.add_argument("--video-id", default="")
    args = ap.parse_args()

    build = Path(args.build)
    mp4 = build / "briefing.mp4"
    meta = json.loads((build / "meta.json").read_text(encoding="utf-8"))
    title = (build / "title.txt").read_text(encoding="utf-8").strip()
    description = (build / "description.txt").read_text(encoding="utf-8")

    if args.dry_run:
        video_id = args.video_id or "DRYRUN"
    else:
        if not mp4.exists():
            sys.exit(f"no video at {mp4}")
        token = access_token()
        # Tags count for little on their own, but they do help YouTube
        # disambiguate a two-letter ticker like EQ from the English word.
        tags = ["net cash stocks", "stock screener", "deep value investing",
                "trading below cash", "balance sheet analysis",
                "value investing", "SEC filings", "stock analysis",
                "cash cushion"]
        tags += [s for s in meta.get("symbols", [])]
        tags += [f"{s} stock" for s in meta.get("symbols", [])[:3]]
        video_id = upload(token, mp4, title, description, tags)
        srt = build / "briefing.srt"
        if srt.exists():
            add_captions(token, video_id, srt)
        thumb = build / (meta.get("thumbnail") or "thumbnail.png")
        if thumb.exists():
            set_thumbnail(token, video_id, thumb)

    secs = int(meta.get("duration_seconds") or 0)
    out = {
        "video_id": video_id,
        "title": title,
        "run_date": meta.get("run_date"),
        "generated_at": meta.get("generated_at"),
        "symbols": meta.get("symbols", []),
        "duration_seconds": meta.get("duration_seconds"),
        # ISO 8601, for the VideoObject markup on the site.
        "duration_iso": f"PT{secs // 60}M{secs % 60}S",
        "description": meta.get("description_head", ""),
        "chapters": meta.get("chapters", []),
        "channel": f"https://www.youtube.com/channel/{EXPECTED_CHANNEL}",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "embed": f"https://www.youtube-nocookie.com/embed/{video_id}",
        # i.ytimg.com serves this for any public video without an API call.
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
    }
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {video_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
