---
name: browser-publish
description: Post queued GTA Viral Clips videos through the YouTube Studio, TikTok Studio and Instagram websites using Claude in Chrome, when official API publishing is unavailable for a platform. Use when the owner asks to work through the browser queue, or to make a private/draft browser test upload.
---

# Browser publishing (Claude in Chrome fallback)

The official APIs are the primary route: they run unattended from the scheduler. This skill is the fallback for a platform whose API can't post yet. It only runs inside a Claude session with Claude in Chrome connected. It is not an unattended bot: the platforms' terms allow automated posting only through their APIs.

## Rules
- Use the owner's existing signed-in Chrome sessions. Never read, copy or export cookies, tokens or passwords.
- If a login page, CAPTCHA, 2FA or security check appears: stop and ask the owner to complete it.
- Check the signed-in account first: YouTube channel `UCpxIZcZJwslLEJDD04JSkJQ` (GTA Viral Clips), Instagram `gtaviralclips69`, TikTok `gta.viral.clips6`. Wrong account: stop.
- **Public** posting only when `public_allowed` is true in `python -m autopilot browser-queue` (autopilot approved for that platform). Otherwise only private, draft or "Only me" tests.
- Never accept terms, switch account types or change account settings without asking the owner.
- Record every outcome: `python -m autopilot browser-record <post_id> published --url <verified URL>`, or `private_test` / `failed --note "..."`. Never record `published` without opening the live URL.

## Steps
1. `python -m autopilot browser-queue` lists each item's file (already under 10 MB for the upload tool), caption and whether public posting is allowed.
2. **YouTube Studio**: open `studio.youtube.com/channel/UCpxIZcZJwslLEJDD04JSkJQ/videos/upload?d=ud`, use `find` to get the hidden `file-input`, then `file_upload`. The title is a contenteditable DIV, so `form_input` fails: click it, press ctrl+a then Delete, and type. Type `#shorts` last: the hashtag autocomplete swallows the keys that follow. Click the description box by its ref before typing. Set "No, it's not made for kids", then click Next through to Visibility and pick Private or Public. Pressing Escape saves the video as a private **Draft**. Verify on the Shorts tab: the Visibility column.
3. **TikTok Studio**: open `tiktok.com/tiktokstudio/upload` and `file_upload` to the hidden `file-input`. Dismiss the "automatic content checks" prompt with Cancel unless the owner has chosen to turn it on. Set "Who can see this post" to **Only you** (test) or **Everyone** (only when public is allowed). Type the caption, then click Post. Verify in the Posts list and read the `/video/<id>` link with `javascript_tool` (`a[href*="/video/"]`).
4. **Instagram**: click Create, then `file_upload` to the `file-input`. Click OK on the reels notice, then Next (crop), then Next (edit), then the caption screen, then Share. Instagram has **no private or draft Reel** on the web: a test stops at the Share screen, then close and Discard. Verify a real post on the profile and record its `/reel/<id>/` permalink.
5. Close any tabs you opened.
