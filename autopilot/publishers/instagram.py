"""Instagram Reels via the official Instagram API with Instagram Login (graph.instagram.com).

Setup (free): Instagram account switched to Professional (Creator or Business) -> developers.facebook.com
-> create app (type Business) -> add "Instagram" product -> "API setup with Instagram login" -> add your
account as an Instagram tester and accept the invite -> scopes instagram_business_basic,
instagram_business_content_publish, instagram_business_manage_insights -> set a redirect URI
-> save {"client_id","client_secret","redirect_uri"} to data/secrets/instagram.json.
Development mode works for your own tester account; no App Review needed to post to your own account.

Upload uses the resumable rupload endpoint, so no public file hosting is needed.
Instagram has no private/unlisted Reel: a "test" stops after the container reaches FINISHED, unpublished.
Limit: 50 API-published posts per 24 h (checked before each post).
"""
import os, secrets, time, urllib.parse, webbrowser
import requests
from .. import tokens

PLAT = "instagram"
V = "v23.0"
G = f"https://graph.instagram.com/{V}"
SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights"


def _app():
    a = tokens.app(PLAT)
    if not a:
        raise SystemExit(f"Missing {tokens.SECRETS / 'instagram.json'} (client_id, client_secret, redirect_uri).")
    return a


def connect(redirected_url=None):
    a = _app(); state = secrets.token_urlsafe(16)
    url = "https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": a["client_id"], "redirect_uri": a["redirect_uri"], "scope": SCOPES,
        "response_type": "code", "state": state, "enable_fb_login": 0, "force_reauth": "true"})
    print("Opening Instagram authorisation in your browser. After approving, copy the FULL address you are sent to.")
    webbrowser.open(url)
    import getpass
    back = redirected_url or getpass.getpass("Paste the redirected URL (hidden): ")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    if q.get("state", [""])[0] != state:
        raise SystemExit("state mismatch: authorisation aborted")
    code = q["code"][0].split("#")[0]
    s = requests.post("https://api.instagram.com/oauth/access_token", data={
        "client_id": a["client_id"], "client_secret": a["client_secret"], "grant_type": "authorization_code",
        "redirect_uri": a["redirect_uri"], "code": code}, timeout=30).json()
    if "access_token" not in s:
        raise SystemExit(f"token exchange failed: {s.get('error_message') or s}")
    l = requests.get("https://graph.instagram.com/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": a["client_secret"], "access_token": s["access_token"]}, timeout=30).json()
    tokens.put(PLAT, {"access_token": l["access_token"], "user_id": str(s["user_id"]), "obtained_at": time.time(),
                      "expires_in": l.get("expires_in", 5184000)})
    return whoami()


def _tok():
    t = tokens.get(PLAT)
    if not t:
        raise PermissionError("instagram not connected: run `python -m autopilot connect instagram`")
    if time.time() > t["obtained_at"] + 7 * 86400:  # refresh weekly; long-lived tokens last 60 days
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": t["access_token"]}, timeout=30).json()
        if "access_token" in r:
            t.update(access_token=r["access_token"], obtained_at=time.time(), expires_in=r.get("expires_in"))
            tokens.put(PLAT, t)
    return t


def _req(method, path, **kw):
    t = _tok()
    params = dict(kw.pop("params", {}), access_token=t["access_token"])
    r = requests.request(method, f"{G}/{path}", params=params, timeout=60, **kw)
    js = r.json()
    if "error" in js:
        e = js["error"]
        raise RuntimeError(f"instagram {path}: {e.get('code')}/{e.get('error_subcode')} {e.get('message')}")
    return js


def whoami():
    t = _tok()
    me = _req("GET", "me", params={"fields": "user_id,username,account_type,followers_count,media_count"})
    return {"platform": PLAT, "account": me.get("username"), "id": me.get("user_id") or t["user_id"],
            "account_type": me.get("account_type"), "followers": me.get("followers_count")}


def revoke():
    tokens.delete(PLAT)  # Meta has no token-revoke endpoint for IG Login; also remove the app in Instagram settings


def quota():
    t = _tok()
    d = _req("GET", f"{t['user_id']}/content_publishing_limit", params={"fields": "quota_usage,config"})
    x = (d.get("data") or [{}])[0]
    return x.get("quota_usage", 0), (x.get("config") or {}).get("quota_total", 50)


def upload(path, caption, publish=True):
    t = _tok()
    c = _req("POST", f"{t['user_id']}/media", params={"media_type": "REELS", "upload_type": "resumable",
                                                     "caption": caption[:2200], "share_to_feed": "true"})
    cid = c["id"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{V}/{cid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {t['access_token']}", "offset": "0", "file_size": str(size)})
    if r.status_code != 200 or not r.json().get("success"):
        raise RuntimeError(f"instagram rupload HTTP {r.status_code}: {r.text[:200]}")
    for _ in range(60):
        s = _req("GET", cid, params={"fields": "status_code,status"})
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"instagram processing {s.get('status_code')}: {s.get('status')}")
        time.sleep(10)
    else:
        raise RuntimeError("instagram container did not finish processing in 10 min")
    if not publish:
        return {"remote_id": cid, "url": None, "container_only": True}
    m = _req("POST", f"{t['user_id']}/media_publish", params={"creation_id": cid})
    link = _req("GET", m["id"], params={"fields": "permalink"}).get("permalink")
    return {"remote_id": m["id"], "url": link}


def verify(remote_id, container_only=False):
    if container_only:
        s = _req("GET", remote_id, params={"fields": "status_code"})
        return ("private_test" if s.get("status_code") == "FINISHED" else "failed"), f"container {s.get('status_code')} (not published)"
    m = _req("GET", remote_id, params={"fields": "permalink,media_product_type"})
    return ("published", m["permalink"]) if m.get("permalink") else ("failed", "no permalink")


def stats(media_ids):
    out = {}
    for mid in media_ids:
        try:
            d = _req("GET", f"{mid}/insights", params={"metric": "views,likes,comments,shares,ig_reels_avg_watch_time"})
            vals = {x["name"]: x["values"][0]["value"] for x in d.get("data", [])}
            out[mid] = {"views": vals.get("views"), "likes": vals.get("likes"), "comments": vals.get("comments"),
                        "shares": vals.get("shares"), "watch_seconds": (vals.get("ig_reels_avg_watch_time") or 0) / 1000}
        except Exception:
            continue
    return out
