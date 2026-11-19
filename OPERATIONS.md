# Autopilot operations

`autopilot/` turns the clip scripts into a scheduled operation:

**discover → rights gate → select → edit → caption → export → check → upload → verify → analyse → repeat**

```
.venv\Scripts\python -m autopilot status          # one-screen health: mode, stop switch, accounts, queue, rights
.venv\Scripts\python -m autopilot stop "reason"   # EMERGENCY STOP (or create the file data\STOP)
.venv\Scripts\python -m autopilot resume
```

## What runs every hour (Windows Task Scheduler, task "GTAVI Autopilot")

1. Skips the whole cycle if the emergency stop is on, or if the previous cycle is still running (lock file).
2. Renders new files in the drop folder `input\<creator>\`. The creator must be cleared; `input\own\` holds your own footage.
3. Discovers live GTA 6 streams on Twitch and YouTube, ranked by the viewer counts the platforms report. Every creator becomes a `candidate`. Nothing is clipped unless the creator is `cleared`.
4. For cleared creators who are live: records 30 min, cuts on chat spikes, then picks the best moments.
5. Renders up to `daily_clip_limit` (3) clips a day: 1080x1920, 30 fps, H.264 + AAC, word-by-word Whisper captions with slurs and swears masked, a hook box, a voiced hook (edge-tts) with the game audio ducked, an editorial outro line, loudness normalised, and the facecam/react layout when a webcam is found.
6. Runs 11 checks on every clip: resolution, length, audio, a full decode (playback), loudness, black/dead sections, captions, hook + outro present, credit in every caption, rights cleared, and no banned terms. Clips that fail are `rejected` and never posted.
7. Publishes eligible clips (see modes): at most 3 per platform per day, at least 120 min apart, and never before the first posting slot of the day. Each post is verified through the platform API afterwards. A clip is never posted twice to the same platform (SHA-256 dedupe).
8. Fetches real stats, re-weights clip categories by real views, and writes `reports\YYYY-MM-DD.md`.

Safety: if a platform error mentions copyright, strike, spam, suspended, banned, violation or restricted, everything stops (`data\STOP`). Three failures in a row pause that platform. Revoking a creator's rights removes their queued clips. Tokens are redacted from every stored error.

Output: `output\<date>\<creator>\<category>\clip_*.mp4`, plus `.json` (the package and its check results) and `.youtube.txt` / `.tiktok.txt` / `.instagram.txt` captions.
When a platform can't post directly, a ready-to-post copy with the blocker text goes to `output\manual\<date>\<platform>\`.

## Modes

- **Review (default).** Clips wait in the queue. `review list`, then `review approve 3 4` or `review reject 5`.
- **Autopilot.** `autopilot enable youtube instagram --i-approve-public-autopilot`. It refuses any platform that has no verified test upload. In autopilot, clips that pass every check publish without asking you. `autopilot disable` goes back to review mode.

## Rights (the gate)

```
python -m autopilot rights list
python -m autopilot rights clear kaicenat twitch --basis campaign --evidence <campaign URL> \
   --campaign "Kai Cenat GTA 6" --rate 1.0 --mention @kaicenat --tags "#kaicenat" --platforms tiktok,youtube \
   --min-seconds 10 --max-seconds 60 --ad-label --expires 2026-12-31
python -m autopilot rights revoke <creator> <platform> --note "campaign ended"
```
Clear a creator only after **you** have joined the campaign or hold written permission, and its rules are copied into the row. Being public, or getting a credit line, is not permission. Campaign leads already found are stored as `candidate` with their campaign links.

## Connect accounts (once each, all free)

Developer-app credentials go in `data\secrets\<platform>.json` (git-ignored). Tokens go to Windows Credential Manager (service `gtavi-autopilot`), never into files, the DB or logs.
Check with `python -m autopilot accounts`, revoke with `python -m autopilot revoke <platform>`.

### YouTube Shorts
1. console.cloud.google.com → new project → enable **YouTube Data API v3** and **YouTube Analytics API**.
2. OAuth consent screen: External, add your Google account as a test user.
3. Credentials → OAuth client ID → **Desktop app** → download the JSON as `data\secrets\youtube.json`.
4. `python -m autopilot connect youtube` (a browser opens; pick the channel).
5. `python -m autopilot test-upload youtube <clip_id>` uploads as **private** and verifies.

Restriction: Google keeps every upload from an unaudited API project private, so even "public" posts stay private (the system reports this as `locked_private` and makes a manual package). Public auto-posting needs the free YouTube API compliance audit: https://support.google.com/youtube/contact/yt_api_form. Quota is 10,000 units a day, about 6 uploads.

### TikTok
1. developers.tiktok.com → Manage apps → create app → add **Login Kit** and **Content Posting API** (turn on Direct Post) → scopes `user.info.basic, video.publish, video.upload, video.list`.
2. Add a redirect URI, for example `https://localhost/callback`.
3. Save `{"client_key":"…","client_secret":"…","redirect_uri":"https://localhost/callback"}` as `data\secrets\tiktok.json`.
4. `python -m autopilot connect tiktok`. Approve in the browser, then paste the address you land on into the hidden prompt.
5. `python -m autopilot test-upload tiktok <clip_id>` posts as **SELF_ONLY** (only you can see it).

Restriction: until TikTok audits the app, Direct Post allows only SELF_ONLY. For public clips, autopilot uploads to your TikTok inbox, and you tap Post in the app (status `manual_needed`). Apply for the audit in the developer portal to get public Direct Post.

### Instagram Reels
1. Switch the Instagram account to **Professional** (Creator or Business).
2. developers.facebook.com → create app (Business) → add **Instagram** → *API setup with Instagram login* → add your account as an Instagram tester and accept the invite (Instagram → Settings → Apps and websites).
3. Permissions: `instagram_business_basic, instagram_business_content_publish, instagram_business_manage_insights`. Set the redirect URI.
4. Save `{"client_id":"…","client_secret":"…","redirect_uri":"…"}` as `data\secrets\instagram.json`.
5. `python -m autopilot connect instagram`, then `python -m autopilot test-upload instagram <clip_id>`. The test uploads and processes the Reel but does **not** publish it: Instagram has no private Reels.

Development mode is enough to post to your own tester account. Limit: 50 API posts in 24 h, checked before every post.

## Where it runs

The task uses your Windows login (`InteractiveToken`): it runs while this PC is on and you are signed in, including on battery. It catches up after sleep. **It does not run while the PC is off or asleep.** For true 24/7, you'll need an always-on host. Free options:
- **Oracle Cloud Always Free** ARM VM (4 cores, 24 GB): enough for Whisper `small` on CPU. It needs a card for identity checks and charges nothing on the always-free shapes.
- A spare PC or mini PC that stays on.

Paid VPS hosting (about $5–10 a month) is optional and won't be set up without your approval.

## Destination accounts (publishing is locked to these)

| Platform | Account | Owner login |
|---|---|---|
| YouTube Shorts | GTA Viral Clips, @gtaviralclips6969, channel `UCpxIZcZJwslLEJDD04JSkJQ` | The channel owner's Google account (the only one to use; address kept private) |
| Instagram Reels | @gtaviralclips69 | Instagram (personal profile today: must become Creator for the API) |
| TikTok | @gta.viral.clips6 | TikTok |

`account_guard` refuses to upload when the connected API account is not exactly this destination (`data/config.json` → `destinations`).
The Google Cloud project is `gtaviralclips` (number 286612411476), owned by that same Google account.

Public app pages (GitHub Pages, from `/docs` on `feature/gtaviralclips-publishing`), used by the TikTok and Meta developer apps:
- Terms of Service: https://sheikhzidane.github.io/gtavi-claudecode/TERMS.html
- Privacy Policy: https://sheikhzidane.github.io/gtavi-claudecode/PRIVACY.html
- App site: https://sheikhzidane.github.io/gtavi-claudecode/

If this branch is merged or renamed, switch the Pages source (repo Settings → Pages), or these links go down.

## Two publishing methods

1. **Official APIs (primary, unattended).** The scheduler uses them: they are allowed for automation, and posts are verified through the API.
2. **Your signed-in Chrome via Claude in Chrome (fallback, in a Claude session).** For a platform whose API can't post yet: `python -m autopilot browser-queue`, then follow `skills/browser-publish/SKILL.md`, then `python -m autopilot browser-record <post_id> <status> --url <verified URL>`. Manual packages are re-encoded to stay under the 10 MB per-file limit of the Chrome upload tool.
   This method does not run unattended: Claude in Chrome works only while a Claude session is open, and the platforms' terms allow automated posting only through their APIs. Cookies, tokens and passwords are never read or exported.
   Autopilot can only be enabled for a platform after a verified **API** test (browser tests don't count).

Browser tests on 2026-11-19, with clip #3 (synthetic test footage):
- **YouTube:** saved as a private Draft in Studio.
- **TikTok:** posted as "Only me" (https://www.tiktok.com/@gta.viral.clips6/video/7709875753470545174).
- **Instagram:** reached the Share screen, then discarded (Instagram has no private Reels); the profile still shows 0 posts.

## Brand

GTA Viral Clips (set in `data/config.json` → `brand`). No impersonation of Rockstar Games or any streamer.

## Brand ideas from the first proposal (superseded)

| | |
|---|---|
| Name | **Vice Clips Daily** (backups: *Leonida Moments*, *Six Star Clips*) |
| Handle | @viceclipsdaily on TikTok, Instagram and YouTube |
| Bio | "The best GTA 6 moments, every day. Credited clips, hidden secrets, wild chases. Fan page, not affiliated with Rockstar Games." |
| Style | Pink and cyan neon, Arial Black captions, yellow word highlight |
| Hashtags | #GTA6 #GTAVI #ViceCity #gaming + a category tag + any tags the campaign requires |

Brand name and tags are set in `data\config.json` → `brand`.

## Analytics and earnings

Stats come only from the platform APIs (views, likes, comments, shares, watch time, retention where the API provides them). Earnings come only from rows you add to `data\earnings.csv`: `date,campaign,platform,amount,currency,evidence_url`. Nothing is estimated.

## Tests

`.venv\Scripts\python -m unittest tests.test_publishers -v` runs **simulated** tests: the platform APIs are mocked. They check request order, privacy rules, dedupe, the emergency stop and token redaction. They do not prove a real upload.
