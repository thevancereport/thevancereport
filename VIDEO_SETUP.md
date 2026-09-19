# Daily briefing video

The pipeline builds a finished, narrated video from `research.json` every day
and attaches it to the workflow run for you to download and post yourself.

That is the intended way to run it. Posting by hand takes about two minutes and
keeps a person between the screen and a public post, which for a channel that
names specific securities every day is worth more than the automation would be.

Automatic publishing is still supported and switched off. See the last section.

---

## What comes out

Each run attaches an artifact called `briefing-<number>` containing:

| File | What to do with it |
| --- | --- |
| `briefing.mp4` | 1920×1080, H.264 + AAC. This is the video. |
| `title.txt` | Paste into YouTube's title box. Already under 100 characters. |
| `description.txt` | Paste into the description. Chapters are already timestamped. |
| `briefing.srt` | Upload under Subtitles → English. |
| `thumbnail.png` | 1280×720. Upload as the custom thumbnail. |
| `og-card.png` | Not for YouTube — the site's link-preview card, committed automatically. |

Nothing large enters the repository. The MP4 lives in the run artifact for 14
days and then on YouTube.

---

## Posting one

1. **Actions** tab → **Daily briefing video** → **Run workflow**. It also runs
   on its own after the screener finishes each weekday.
2. Wait about five minutes. Download the artifact from the bottom of the run
   page and unzip it.
3. YouTube → **Create → Upload video** → drop in `briefing.mp4`.
4. Paste `title.txt` and `description.txt`. Leave the chapters as they are —
   YouTube turns the timestamps into a chapter list by itself.
5. Set the thumbnail to `thumbnail.png`. (Custom thumbnails need a verified
   channel: youtube.com/verify, one-time, takes a minute.)
6. Add `briefing.srt` under Subtitles. This matters more than it looks —
   captions are indexed, so it is the cheapest search win available.
7. Publish, then send the link to Claude, or edit `video.json` yourself:

   ```json
   {
     "video_id": "THE_ID_FROM_THE_URL",
     "embed": "https://www.youtube-nocookie.com/embed/THE_ID_FROM_THE_URL",
     "url": "https://www.youtube.com/watch?v=THE_ID_FROM_THE_URL",
     "title": "…", "run_date": "…", "symbols": ["…"],
     "generated_at": "…", "duration_seconds": 0, "duration_iso": "PT0M0S",
     "description": "…", "chapters": [], "thumbnail": "…"
   }
   ```

   The home page reads that file and embeds the player. Until it exists the
   page says the briefing has not been published yet rather than showing an
   empty box.

---

## What the job refuses to do

- Publish if `research.json` is more than 26 hours old
- Publish if the build came out without narration
- Run at all if the screener workflow failed

A briefing that reads yesterday's numbers in a confident voice is worse than no
briefing, and a silent video is worse than a missing one.

---

## Where the words come from

`scripts/narration.py` holds every word the video says and every word it puts
on screen, and nothing else. If you want to change the wording, that is the
only file to open.

Every figure spoken is read out of `research.json` or is arithmetic on those
figures. The one derived number is the price at which a name stops clearing the
30% gate — net cash per share divided by 0.30 — and the video says, in those
words, that it is arithmetic on the screen's own threshold and not a target,
not a valuation and not a price to pay.

---

## Two things worth an outside opinion

**A daily public video naming specific securities is a different posture from a
screener with a disclaimer.** The wording here is the careful end of the range
and it is still a broadcast about particular stocks. Worth an hour with a
securities attorney before the channel gets an audience. I am not a lawyer.

**YouTube's inauthentic-content policy** targets templated, mass-produced video
with synthetic narration, and it bites at monetisation first. What protects a
channel like this is that each video is a real analysis of that day's data
rather than a reskin of yesterday's. Recording the narration in your own voice
is the single biggest step further from that line, and the script is already
written for you each morning.

**`stock.html` still shows a "target execution zone"** from the `target_zone`
field in `research.json`. The video deliberately does not read it. If the
video's framing is the one you want, that field is the loose end.

---

## Running it locally

```bash
# visuals only, no narration — fast, and what to use while editing wording
python3 scripts/build_video.py --silent --out build

# the real thing
pip install piper-tts
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high
curl -fsSL -o voice.onnx      "$BASE/en_US-ryan-high.onnx"
curl -fsSL -o voice.onnx.json "$BASE/en_US-ryan-high.onnx.json"
PIPER_VOICE=voice.onnx python3 scripts/build_video.py --out build
```

Needs `ffmpeg` and a Chromium or Chrome binary. Point `CHROMIUM_PATH` at it if
it is somewhere unusual. To change voice, point `PIPER_VOICE` at a different
model from the same collection.

---

## If you ever want it to publish on its own

`scripts/upload_youtube.py` is in the repository and does nothing until three
secrets exist. It uploads as private by default, checks which channel the video
landed on and refuses to continue if it is not
`UCplN4kMRgUbAI9pT31DCbAw`, and sets the thumbnail and captions.

To switch it on you would create a Google Cloud project, enable **YouTube Data
API v3**, configure an OAuth consent screen with the scope
`https://www.googleapis.com/auth/youtube.upload`, create an OAuth client, and
exchange it for a refresh token. Then add `YT_CLIENT_ID`, `YT_CLIENT_SECRET`
and `YT_REFRESH_TOKEN` under **Settings → Secrets and variables → Actions**,
and a repository *variable* `YT_PRIVACY` set to `public` when ready.

Two traps if you go this way:

- **Publish the consent screen.** An app left in *Testing* issues refresh
  tokens that expire after **seven days**, so a daily job works for a week and
  then starts failing. Publishing shows an "unverified app" warning at
  authorisation, which you click through; verification is only needed to remove
  that warning for other people.
- **An API key is not a credential for this.** Keys starting `AIza` authorise
  public read-only calls. Uploading acts as you, so it needs OAuth. There is no
  API-key path to `videos.insert`.

Secrets go straight into GitHub's secret fields. Never into a file, a commit,
or a message.
