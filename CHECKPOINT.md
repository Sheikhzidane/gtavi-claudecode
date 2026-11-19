# GTA Viral Clips: checkpoint (19 Nov 2026)

Branch `feature/gtaviralclips-publishing`. Pipeline: `python -m autopilot tick` (Windows task "GTAVI Autopilot", hourly).

## Platforms (priority: Instagram, TikTok, YouTube)
| Platform | State | Blocker |
|---|---|---|
| Instagram @gtaviralclips69 | Creator account; Meta app "GTA Viral Clips Publisher" (1176800934906126) | Meta dashboard: "API setup with Instagram login is not available for newer apps"; the only route is Unified login, which needs the business portfolio linked to the app (a portfolio, business_id 29876135501992227, already exists) |
| TikTok @gta.viral.clips6 | App 7709860990352214036, sandbox "autopilot-test", Login Kit + Content Posting (Direct Post on), redirect http://localhost:8765/callback/ | Owner: add @gta.viral.clips6 as sandbox target user; run `set-app tiktok`; then `connect tiktok`. Public Direct Post needs TikTok's audit; until then SELF_ONLY or inbox drafts |
| YouTube @gtaviralclips6969 | Connected (OAuth, sheikhzidane8 account); private API test `juuxCDBl_co` verified; public uploads allowed (2 Nov 2026 API revision); autopilot on | none for publishing |

## Footage
- No genuine GTA VI clip exists yet. YouTube downloads are turned off (`youtube_downloads: false`): yt-dlp hit YouTube's bot check, and we don't use cookies or bypasses.
- Licensed (CC BY) YouTube opportunities are listed in `reports/scout-*.md`, but can't be fetched while YouTube downloads are off.
- Best campaign to join: KreekCraft GTA 6 (ClipLaunch, Content Rewards): $1/1K, min $3, max $300 per clip, $6,824 budget, TikTok/IG/YT, must include his overlay; source = his Twitch streams (Twitch downloads work). Full terms and 5 reference files unlock only after joining.
- Next step after joining: `python -m autopilot rights clear kreekcraft twitch --basis campaign --evidence <campaign URL> --campaign "KreekCraft GTA 6" --rate 1 --platforms tiktok,instagram,youtube` with the unlocked rules, then render from his VOD.

## Safety
Rights gate, destination lock, emergency stop (`python -m autopilot stop`), dedupe, synthetic test clips quarantined (status `test`).
