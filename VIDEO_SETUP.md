# Daily briefing video — setup

The pipeline is committed and runs on its own. It cannot publish until you give
it a YouTube credential, because only you can create one: it requires signing
in to Google as the channel owner.

Until the secrets exist, the workflow builds the video, attaches it to the run
as an artifact you can download, and fails at the upload step. The site shows
"Today's briefing has not been published yet." rather than an empty player.

---

## 1. What the pipeline does

| File | Job |
| --- | --- |
| `scripts/narration.py` | Every word the video says and shows. Read this one. |
| `scripts/slides.py` | Renders each frame as a 1920×1080 PNG through headless Chromium |
| `scripts/build_video.py` | Narrates with piper, assembles with ffmpeg, writes the SRT and the description |
| `scripts/upload_youtube.py` | Uploads, then writes `video.json` |
| `.github/workflows/daily_video.yml` | Runs the above after the screener finishes |

`video.json` is committed back to `main`. `index.html` reads it and embeds the
player. Nothing large ever enters the repository — the MP4 lives on YouTube and
as a 14-day build artifact.

The job refuses to publish if `research.json` is more than 26 hours old, and
refuses to publish a build that came out without narration. A briefing that
quietly reads yesterday's numbers is worse than no briefing.

---

## 2. The YouTube credential (one time, ~15 minutes)

You do all of this signed in as the channel owner. Nothing in the repository
sees your password, and no one else needs to.

1. Go to <https://console.cloud.google.com/> and create a project, e.g.
   "vance-report-uploader".
2. **APIs & Services → Library →** search "YouTube Data API v3" → **Enable**.
3. **APIs & Services → OAuth consent screen.** User type **External**. Fill in
   the app name and your email. Under **Scopes** add
   `https://www.googleapis.com/auth/youtube.upload`. Under **Test users** add
   your own Google account. Leave it in **Testing** — you do not need
   verification to upload to your own channel.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID.**
   Application type **Desktop app**. Copy the **client ID** and
   **client secret**.
5. Get a refresh token. On your own machine, with python installed:

   ```bash
   pip install google-auth-oauthlib
   python3 - <<'PY'
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_config(
       {"installed": {
           "client_id": "PASTE_CLIENT_ID",
           "client_secret": "PASTE_CLIENT_SECRET",
           "auth_uri": "https://accounts.google.com/o/oauth2/auth",
           "token_uri": "https://oauth2.googleapis.com/token"}},
       scopes=["https://www.googleapis.com/auth/youtube.upload"])
   creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
   print("\nREFRESH TOKEN:\n" + creds.refresh_token)
   PY
   ```

   A browser opens, you approve, and the refresh token prints in your terminal.
   It does not expire while the project stays in Testing and the token is used
   at least every six months — a daily job qualifies.

6. In the repository: **Settings → Secrets and variables → Actions → Secrets**,
   add three:

   | Name | Value |
   | --- | --- |
   | `YT_CLIENT_ID` | from step 4 |
   | `YT_CLIENT_SECRET` | from step 4 |
   | `YT_REFRESH_TOKEN` | from step 5 |

Paste these into GitHub's own secret fields. Do not put them in a file, a
commit, or a chat message.

---

## 3. Going public

Uploads are **private** by default. That is deliberate: a scheduled job that can
publish to a public channel unattended, from day one, is not a thing you want.

- Watch the first few. Run the workflow by hand from the Actions tab
  (**Daily briefing video → Run workflow**) and pick the privacy for that run.
- When you are satisfied, add a repository **variable** (not a secret)
  `YT_PRIVACY` with the value `public`. The workflow reads it on every run.

Quota: an upload costs 1,600 units against a default 10,000 units a day. One
briefing a day uses about a sixth of it.

---

## 4. Things worth knowing before the first public upload

**This is a channel that names securities every day.** The video states the
screen's own levels and says, in the video and in the description, that they are
arithmetic on a threshold rather than a target or a suggested entry. That
wording is the safer end of the range, and it is still a public daily broadcast
about specific stocks. Worth an hour with a securities attorney before the
channel goes public — not because anything here is reckless, but because the
answer depends on where you and your viewers are, and I am not a lawyer.

**YouTube's inauthentic-content policy.** Templated, mass-produced videos with
synthetic narration are the thing that policy targets, and monetisation is where
it bites first. What protects a channel like this is that each video is a
genuine analysis of that day's data rather than a reskin of yesterday's. If you
ever want it further from that line, recording the narration in your own voice
is the single biggest change available.

**The site already shows a "target execution zone"** on `stock.html`, from the
`target_zone` field in `research.json`. The video deliberately does not read it.
If the video's framing is the one you want, that field is the loose end.

---

## 5. Running it yourself

```bash
# visuals only, no narration — quick, and what to use while editing wording
python3 scripts/build_video.py --silent --out build

# the real thing
pip install piper-tts requests
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high
curl -fsSL -o voice.onnx      "$BASE/en_US-ryan-high.onnx"
curl -fsSL -o voice.onnx.json "$BASE/en_US-ryan-high.onnx.json"
PIPER_VOICE=voice.onnx python3 scripts/build_video.py --out build
```

To change a voice, point `PIPER_VOICE` at a different model from the same
collection. To change what is said, edit `scripts/narration.py` — it is the only
file with wording in it.
