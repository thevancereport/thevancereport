"""Post the day's note to the Facebook Page and Instagram.

Runs at the end of the Daily video job, once the note and the video are
public. One post per platform per screen date: social.json remembers what was
posted, so a re-run never posts twice.

Facebook gets the text and a link to the note. Instagram cannot hold a
clickable link, so it says "link in bio".

In the nightly job the videos are on hand, so:
  --video build/briefing.mp4   Facebook gets the full video, uploaded to the Page
  --reel  build/short.mp4      Instagram gets the vertical short as a Reel
Without them (a run by hand) Facebook gets a link post and Instagram the
YouTube thumbnail as a picture.

Needs the repository secret META_PAGE_TOKEN: a Page token for the Arden K. Vance
Page, made from an extended user token, so it does not expire. Without it the
step says so and does nothing.

  python3 scripts/social_post.py            # post, if not already posted
  python3 scripts/social_post.py --video build/briefing.mp4 --reel build/short.mp4
  python3 scripts/social_post.py --dry-run  # print what would be posted
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://thevancereport.com"
GRAPH = "https://graph.facebook.com/v23.0"
PAGE_ID = os.environ.get("META_PAGE_ID", "942385352288591")          # Arden K. Vance
IG_ID = os.environ.get("META_IG_ID", "17841480104686475")             # @ardenkvance
TAGS = "#stocks #valueinvesting #stockmarket #investing #SEC"
NOT_ADVICE = "Educational research, not investment advice."


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def long_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def compose(t5, dv):
    """The two captions, from the files the site already shows."""
    day = t5["date"]
    url = f"{SITE}/{t5['page']}"
    title = (dv or {}).get("title") or ""
    names = title.split(": ", 1)[1] if ": " in title and (dv or {}).get("date") == day else \
        ", ".join(n["symbol"] for n in t5.get("names", []))
    head = f"The five at the top, {long_date(day)}: {names.rstrip('.')}."
    blurb = (t5.get("blurb") or "").strip()
    fb = "\n\n".join(x for x in [head, blurb, f"The note and the video: {url}", NOT_ADVICE] if x)
    ig = "\n\n".join(x for x in [head, blurb,
                                 "The full note and the video are on thevancereport.com (link in bio).",
                                 NOT_ADVICE, TAGS] if x)
    return fb, ig[:2200], url


def call(method, path, params, token):
    data = urllib.parse.urlencode({**params, "access_token": token}).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise RuntimeError(f"{path}: HTTP {e.code}: {msg[:300]}") from None


def fb_video(path, text, title, token):
    """Upload a video file to the Page. Returns the video id."""
    import requests  # only the nightly job (which has it) posts video
    with open(path, "rb") as fh:
        r = requests.post(f"https://graph-video.facebook.com/v23.0/{PAGE_ID}/videos", timeout=900,
                          data={"description": text, "title": title, "access_token": token},
                          files={"source": (Path(path).name, fh, "video/mp4")})
    if r.status_code != 200:
        raise RuntimeError(f"video upload: HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("id")


def ig_reel(path, caption, token):
    """Upload a vertical video as a Reel: make the container, send the file,
    wait for Instagram to process it, publish. Returns the media id."""
    import requests
    c = call("POST", f"{IG_ID}/media", {"media_type": "REELS", "upload_type": "resumable",
                                         "caption": caption, "share_to_feed": "true"}, token)
    cid = c["id"]
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/v23.0/{cid}", timeout=900, data=fh,
                          headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    if r.status_code != 200:
        raise RuntimeError(f"reel upload: HTTP {r.status_code}: {r.text[:300]}")
    wait_ready(cid, token, tries=60, every=10)
    return call("POST", f"{IG_ID}/media_publish", {"creation_id": cid}, token).get("id")


def ig_reel_url(url, caption, token):
    """Ask Instagram to fetch the short from a public link (the downloads
    release) instead of sending the file; Instagram's direct upload has been
    failing with ProcessingFailedError. Returns the media id."""
    c = call("POST", f"{IG_ID}/media", {"media_type": "REELS", "video_url": url,
                                         "caption": caption, "share_to_feed": "true"}, token)
    wait_ready(c["id"], token, tries=60, every=10)
    return call("POST", f"{IG_ID}/media_publish", {"creation_id": c["id"]}, token).get("id")


def wait_ready(cid, token, tries=20, every=6):
    for _ in range(tries):
        s = call("GET", cid, {"fields": "status_code,status"}, token)
        if s.get("status_code") == "FINISHED":
            return
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process it: {s.get('status') or s.get('status_code')}")
        time.sleep(every)
    raise RuntimeError("Instagram was still processing it after the wait")


def thumb_url(video_id):
    """A JPEG Instagram can fetch. YouTube's largest frame, else the standard one."""
    for name in ("maxresdefault.jpg", "hqdefault.jpg"):
        u = f"https://i.ytimg.com/vi/{video_id}/{name}"
        try:
            with urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=30) as r:
                if r.status == 200:
                    return u
        except Exception:  # noqa: BLE001
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--video", help="the full video, for the Facebook Page")
    ap.add_argument("--reel", help="the vertical short, for Instagram")
    ap.add_argument("--reel-url", help="a public link to the same short; tried before --reel")
    a = ap.parse_args()
    root = Path(a.root)

    t5 = load(root / "top5.json")
    dv = load(root / "daily_video.json", {})
    if not t5 or not t5.get("date") or not t5.get("page"):
        print("no top5.json to post from")
        return 0
    day = t5["date"]
    fb_text, ig_text, url = compose(t5, dv)
    video = dv.get("video_id") if dv.get("date") == day and dv.get("visible") else t5.get("video_id")
    image = thumb_url(video) if video else None

    state_p = root / "social.json"
    state = load(state_p, {}) or {}
    done = state.get(day, {})

    fb_video_file = a.video if a.video and Path(a.video).exists() else None
    reel_file = a.reel if a.reel and Path(a.reel).exists() else None
    print("FACEBOOK (" + ("video " + fb_video_file if fb_video_file else "link post") + "):\n" + fb_text +
          "\n\nINSTAGRAM (" + ("reel " + (a.reel_url or reel_file) if (a.reel_url or reel_file) else "image " + (image or "none")) + "):\n" + ig_text + "\n")
    if a.dry_run:
        return 0

    # The secret may have been pasted with extra text around the token (quotes,
    # a label, the Page ID). Meta tokens are one unbroken run starting "EAA".
    raw = os.environ.get("META_PAGE_TOKEN", "")
    m = re.search(r"EAA[A-Za-z0-9]{20,}", raw)
    token = m.group(0) if m else raw.strip()
    if raw.strip() and m and token != raw.strip():
        print("::notice::META_PAGE_TOKEN had extra text around the token; using the token alone.")
    if not token:
        print("::notice::META_PAGE_TOKEN is not set; nothing posted.")
        return 0

    problems = []
    if not done.get("facebook"):
        if fb_video_file:
            try:
                title = (dv.get("title") if dv.get("date") == day else None) or f"The five at the top, {long_date(day)}"
                text = fb_text.replace("The note and the video: ", "The full note, with every figure: ")
                done["facebook"] = fb_video(fb_video_file, text, title[:250], token)
                done["facebook_kind"] = "video"
                print("Facebook video:", done["facebook"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"Facebook video: {e}; posting the link instead")
        if not done.get("facebook"):
            try:
                r = call("POST", f"{PAGE_ID}/feed", {"message": fb_text, "link": url}, token)
                done["facebook"] = r.get("id")
                print("Facebook post:", done["facebook"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"Facebook: {e}")
    else:
        print("Facebook already posted for", day)

    if not done.get("instagram"):
        if a.reel_url:
            try:
                done["instagram"] = ig_reel_url(a.reel_url, ig_text, token)
                done["instagram_kind"] = "reel"
                print("Instagram reel (from link):", done["instagram"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"Instagram reel from link: {e}" + ("; trying the file upload" if reel_file else ""))
        if reel_file and not done.get("instagram"):
            try:
                done["instagram"] = ig_reel(reel_file, ig_text, token)
                done["instagram_kind"] = "reel"
                print("Instagram reel:", done["instagram"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"Instagram reel: {e}; posting the picture instead")
        if not done.get("instagram") and not image:
            problems.append("Instagram: no video thumbnail yet, so nothing to post (Instagram needs a picture)")
        elif not done.get("instagram"):
            try:
                c = call("POST", f"{IG_ID}/media", {"image_url": image, "caption": ig_text}, token)
                wait_ready(c["id"], token)    # Instagram fetches the picture first
                r = call("POST", f"{IG_ID}/media_publish", {"creation_id": c["id"]}, token)
                done["instagram"] = r.get("id")
                print("Instagram post:", done["instagram"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"Instagram: {e}")
    else:
        print("Instagram already posted for", day)

    if done:
        state[day] = done
        state = dict(sorted(state.items())[-60:])     # keep two or three months
        state_p.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"Social for {day}: Facebook {done.get('facebook') or 'not posted'}, "
                     f"Instagram {done.get('instagram') or 'not posted'}.\n")
    for p in problems:
        print("::warning::" + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
