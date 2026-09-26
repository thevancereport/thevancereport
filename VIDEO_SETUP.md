# Daily video

Every weeknight, straight after the daily screen publishes, the **Daily video**
job builds a narrated video of the five highest-ranked companies, uploads it to
YouTube and puts it on the site. Nothing in it is written by hand: every figure
it speaks or shows comes from `value_screen.json`, and what changed since the
session before comes from `screen_history.json`.

| Piece | What it is |
| --- | --- |
| `scripts/top5_video.py` | Builds the video (`build`), uploads it (`publish`), and updates the site (`site`). All the wording is in this file. |
| `.github/workflows/daily_video.yml` | Runs it after each successful **Daily value screen** run, or by hand from the Actions tab. |
| `daily_video.json` | Tonight's video: date, YouTube id, length, and whether people can watch it. The control room reads it. |

The screen's schedule tries up to five times a night, and a try that stops
early still counts as a success. So the job checks for itself: it only builds
when the screen file is less than 20 hours old and tonight's video does not
exist yet. **Run workflow** with *Force* ticked makes it again.

## What it does with the video

1. Builds a 1920×1080 MP4 with captions, a thumbnail, a title, a description
   with chapters, and tags. Narration is the free **piper** voice "Ryan".
2. Attaches all of that to the run for 14 days (**Artifacts** on the run page),
   whether or not YouTube is connected.
3. If YouTube is connected, uploads it, adds the captions and thumbnail, and
   declares it as synthetic media (YouTube asks for this for AI voices).
4. Checks whether people can actually watch it. Only then does it put the video
   in the home-page box (`top5.json`) and at the top of tonight's note page, if
   that page already exists. If the note is created later in the control room,
   the note builder offers tonight's video with a tick box.

It refuses to upload a video built without narration, and it stops if the
video would land on any channel other than The Vance Report
(`UCplN4kMRgUbAI9pT31DCbAw`).

## Connecting YouTube (one time, about 20 minutes)

1. **Google Cloud Console** → create a project, e.g. *Vance Report video*.
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **OAuth consent screen** → External. Add the scopes
   `https://www.googleapis.com/auth/youtube.upload` and
   `https://www.googleapis.com/auth/youtube.force-ssl` (the second is for
   captions). Then **Publish app**. Left in *Testing*, the sign-in expires
   after seven days and the nightly job starts failing a week later.
4. **Credentials → Create credentials → OAuth client ID** → *Web application*.
   Add the authorised redirect URI
   `https://developers.google.com/oauthplayground`.
5. Open the **OAuth 2.0 Playground**. Under the gear icon tick *Use your own
   OAuth credentials* and paste the client ID and secret. Enter the two scopes
   above, click **Authorize APIs**, sign in **as the Google account that owns
   The Vance Report channel** (click through the "unverified app" warning;
   it's your own app), then **Exchange authorization code for tokens**. Copy
   the **refresh token**.
6. GitHub → the repo → **Settings → Secrets and variables → Actions** →
   **New repository secret**, three times: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`. Secrets go only into those boxes: never into a file, a
   commit or a chat.
7. Optional: a repository *variable* `YT_PRIVACY` set to `public`, `unlisted`
   or `private`. Unset means public.

## Why the first videos stay private

YouTube keeps every video uploaded through the API by a new, unaudited project
**private**, whatever privacy was asked for. To lift that, submit the
**YouTube API Services audit** form from the project's YouTube Data API page and
describe the use: one automated upload a day to your own channel. Google
reviews it; allow a few weeks.

Until then, each night's video uploads as private and the site is left alone.
The run page says so. To publish one by hand: YouTube Studio → the video →
Visibility → Public, then in the control room go to **Videos** → paste its link
→ **Publish**, or tick the video box when you create the note.

## Two things worth an outside opinion

**A daily public video naming specific securities** is a different posture from
a screener with a disclaimer. The wording is at the careful end, with the
disclaimer spoken and on every slide, and it is still a broadcast about
particular stocks. Worth an hour with a securities attorney. I am not a lawyer.

**YouTube's policy on mass-produced, templated videos** with synthetic narration
bites at monetisation first. What protects this channel is that each video is a
different analysis of that day's data. Recording the narration yourself would
move it further from that line.

## Running it locally

```bash
# pictures only, silent: for checking wording and layout
python3 scripts/top5_video.py build --silent --out build

# with the voice
pip install piper-tts pillow numpy
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high
curl -fsSL -o voice.onnx "$BASE/en_US-ryan-high.onnx"
curl -fsSL -o voice.onnx.json "$BASE/en_US-ryan-high.onnx.json"
PIPER_VOICE=voice.onnx python3 scripts/top5_video.py build --out build
```

Needs `ffmpeg`. Fonts: put InstrumentSerif and IBMPlexMono `.ttf` files in
`build-fonts/` (or set `FONT_DIR`), otherwise it falls back to DejaVu. To change
the voice, point `PIPER_VOICE` at another model from the same collection.

The earlier briefing (`scripts/build_video.py`, `narration.py`, `slides.py`,
`upload_youtube.py`) was built for the retired cash-plus-drop screen. It is no
longer run and is kept for reference.
