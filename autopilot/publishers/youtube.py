"""YouTube Shorts via the official YouTube Data API v3 (OAuth 2.0 installed-app flow).

Setup (free): Google Cloud project -> enable "YouTube Data API v3" -> OAuth client type "Desktop app"
-> download JSON to data/secrets/youtube.json. Unverified apps work for the owner's own channel
(add yourself as a test user on the consent screen). Videos uploaded by unverified API projects created
after 2020-07-28 are locked to private until the project passes Google's API audit; we detect that.
Quota: 10,000 units/day by default, an upload costs ~1,600 -> max ~6 uploads/day.
"""
import json, time
from .. import tokens

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
PLAT = "youtube"


def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    t = tokens.get(PLAT)
    if not t:
        raise PermissionError("youtube not connected: run `python -m autopilot connect youtube`")
    c = Credentials.from_authorized_user_info(t, SCOPES)
    if not c.valid and c.refresh_token:
        from google.auth.exceptions import RefreshError
        try:
            c.refresh(Request())
        except RefreshError as e:  # revoked, or 7-day expiry while the consent screen is in "Testing"
            raise PermissionError(f"youtube re-authorisation needed ({str(e)[:80]}): run `python -m autopilot connect youtube`")
        tokens.put(PLAT, json.loads(c.to_json()))
    return c


def _svc(name="youtube", ver="v3"):
    from googleapiclient.discovery import build
    return build(name, ver, credentials=_creds(), cache_discovery=False)


def connect():
    from google_auth_oauthlib.flow import InstalledAppFlow
    p = tokens.SECRETS / "youtube.json"
    if not p.exists():
        raise SystemExit(f"Missing {p}. Create a Desktop OAuth client in Google Cloud and save its JSON there.")
    flow = InstalledAppFlow.from_client_secrets_file(str(p), SCOPES)
    import os
    show = os.environ.get("AUTOPILOT_NO_BROWSER") != "1"  # set to 1 to just print the link (open it in the right browser)
    c = flow.run_local_server(port=8766, prompt="consent", access_type="offline", open_browser=show,
                              authorization_prompt_message="Open this Google sign-in link: {url}",
                              success_message="YouTube connected. You can close this tab.")
    tokens.put(PLAT, json.loads(c.to_json()))
    me = whoami()
    from .. import config
    want = config.load().get("destinations", {}).get(PLAT, {}).get("channel_id")
    if want and me.get("id") != want:  # signed in with the wrong Google account or channel: keep nothing
        revoke()
        raise SystemExit(f"Connected channel {me.get('account')} ({me.get('id')}) is not the destination {want}. "
                         "Token revoked; reconnect choosing the GTA Viral Clips channel.")
    return me


def whoami():
    r = _svc().channels().list(part="snippet,statistics", mine=True).execute()
    it = (r.get("items") or [{}])[0]
    return {"platform": PLAT, "account": it.get("snippet", {}).get("title"), "id": it.get("id"),
            "subscribers": it.get("statistics", {}).get("subscriberCount")}


def revoke():
    import requests
    t = tokens.get(PLAT) or {}
    for k in ("refresh_token", "token"):
        if t.get(k):
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": t[k]}, timeout=15)
    tokens.delete(PLAT)


def upload(path, title, description, tags, privacy="private", publish_at=None):
    from googleapiclient.http import MediaFileUpload
    body = {"snippet": {"title": title[:100], "description": description[:5000], "tags": tags[:30], "categoryId": "20"},
            "status": {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": False}}
    if publish_at:
        body["status"]["publishAt"] = publish_at  # RFC3339, video must be private until then
    req = _svc().videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 << 20, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    return {"remote_id": vid, "url": f"https://www.youtube.com/shorts/{vid}"}


def verify(remote_id, want_privacy):
    """Ask the API what really happened. Returns (state, detail)."""
    for _ in range(20):
        it = (_svc().videos().list(part="status,processingDetails", id=remote_id).execute().get("items") or [None])[0]
        if not it:
            return "failed", "video not found after upload"
        st = it["status"]
        if st.get("uploadStatus") in ("rejected", "failed"):
            return "failed", f"{st.get('uploadStatus')}: {st.get('rejectionReason') or st.get('failureReason')}"
        if st.get("uploadStatus") == "processed" or st.get("uploadStatus") == "uploaded":
            if want_privacy != "private" and st.get("privacyStatus") == "private" and not st.get("publishAt"):
                return "locked_private", "API project unaudited: Google keeps uploads private until audit"
            return ("published" if st.get("privacyStatus") == "public" else "private_test"), st.get("privacyStatus")
        time.sleep(15)
    return "processing", "still processing"


def stats(remote_ids):
    out = {}
    for i in range(0, len(remote_ids), 50):
        r = _svc().videos().list(part="statistics", id=",".join(remote_ids[i:i + 50])).execute()
        for it in r.get("items", []):
            s = it["statistics"]
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    try:  # watch time / retention from YouTube Analytics (owner only)
        a = _svc("youtubeAnalytics", "v2").reports().query(
            ids="channel==MINE", startDate="2026-01-01", endDate=time.strftime("%Y-%m-%d"),
            metrics="estimatedMinutesWatched,averageViewPercentage,shares", dimensions="video",
            filters="video==" + ",".join(remote_ids[:200])).execute()
        for vid, mins, pct, shares in a.get("rows", []):
            out.setdefault(vid, {}).update({"watch_seconds": mins * 60, "avg_view_pct": pct, "shares": shares})
    except Exception:
        pass
    return out
