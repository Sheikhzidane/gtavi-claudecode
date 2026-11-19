# GTA Viral Clips Publisher: Privacy Policy

Last updated: 19 November 2026

GTA Viral Clips Publisher (the "app") is a private tool run by the owner of the GTA Viral Clips accounts: YouTube @gtaviralclips6969, Instagram @gtaviralclips69 and TikTok @gta.viral.clips6. It publishes the owner's short gaming videos to those three accounts only. It is not offered to the public, and only the owner signs in to it.

## What the app accesses
The owner connects each account through the platform's official OAuth sign-in (Google for YouTube, TikTok Login Kit, Instagram Login). The platform then issues an access token limited to the permissions shown on its consent screen. The app uses that access only to:
- read the account's basic profile (account name and ID) and follower count, to confirm it is posting to the correct account;
- upload videos with their titles, descriptions and captions, and check their publishing status;
- read statistics for the owner's own videos: views, likes, comments, shares and, where the platform provides them, watch time and retention.

The app does not read direct messages, contacts, other users' data or anyone's password.

## Where data is stored
- **Access tokens** are kept in the Windows Credential Manager (the operating system's credential store) on the owner's computer. They are not written to source code, logs, the local database or the public code repository. Error messages are scrubbed of tokens before they are saved.
- **App credentials** (client IDs and secrets) are kept in that same credential store, or in a local folder that is excluded from the code repository.
- **Publishing records and video statistics** (post IDs, links, status and counts) are kept in a local SQLite database and log files on the owner's computer.

No data is sold, rented or used for advertising, and the app sends no data to any third party other than the platform being posted to. When the owner runs or maintains the app with Claude Code (an AI coding assistant from Anthropic), the files and logs it opens are processed by that service as part of the owner's session.

## Retention and deletion
Publishing records and statistics are kept until the owner deletes them. The owner can disconnect an account at any time:
- `python -m autopilot revoke youtube` revokes the Google token with Google and deletes the stored copy;
- `python -m autopilot revoke tiktok` revokes the TikTok token with TikTok and deletes the stored copy;
- `python -m autopilot revoke instagram` deletes the stored token. To remove the app's access fully, also remove it under Instagram settings → Apps and websites.

Access can also be withdrawn from each platform's own security settings. Deleting the local `data` folder removes all stored records.

## Contact
Questions about this policy: https://github.com/Sheikhzidane/gtavi-claudecode/issues

GTA Viral Clips is an unofficial fan project. It is not affiliated with Rockstar Games, Take-Two Interactive, Google, TikTok or Meta.
