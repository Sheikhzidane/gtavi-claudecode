"""TikTok via the official Content Posting API (Login Kit OAuth v2 + PKCE).

Setup (free): developers.tiktok.com -> create app -> add products "Login Kit" and "Content Posting API"
-> enable Direct Post -> scopes user.info.basic, video.publish, video.upload, video.list
-> redirect URI (e.g. https://localhost/callback) -> save {"client_key","client_secret","redirect_uri"}
to data/secrets/tiktok.json.

Restriction (TikTok policy, cannot be bypassed): until TikTok audits the app, Direct Post only allows
privacy_level SELF_ONLY (visible to the owner only) and at most 5 posting users/24 h. Public autopilot on
TikTok needs the audit. Fallback while unaudited: upload to the creator's TikTok inbox (video.upload)
so a public post is one tap in the app, or post SELF_ONLY and switch visibility in the app.
"""
import base64, hashlib, math, os, secrets, time, urllib.parse, webbrowser
import requests
from .. import tokens

PLAT = "tiktok"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.publish,video.upload,video.list"


def _app():
    a = tokens.app(PLAT)
    if not a:
        raise SystemExit(f"Missing {tokens.SECRETS / 'tiktok.json'} (client_key, client_secret, redirect_uri).")
    return a


def connect(redirected_url=None):
    a = _app()
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok desktop PKCE uses hex SHA256
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": a["client_key"], "scope": SCOPES, "response_type": "code", "redirect_uri": a["redirect_uri"],
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    ru = urllib.parse.urlparse(a["redirect_uri"])
    if not redirected_url and ru.hostname in ("localhost", "127.0.0.1"):
        back = _capture_local(url, ru)  # desktop redirect: catch it on this PC, nothing to paste
    else:
        print("Opening TikTok authorisation in your browser. After approving, copy the FULL address you are sent to.")
        webbrowser.open(url)
        import getpass
        back = redirected_url or getpass.getpass("Paste the redirected URL (hidden): ")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    if q.get("state", [""])[0] != state:
        raise SystemExit("state mismatch: authorisation aborted")
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": a["client_key"], "client_secret": a["client_secret"], "code": q["code"][0],
        "grant_type": "authorization_code", "redirect_uri": a["redirect_uri"], "code_verifier": verifier}, timeout=30).json()
    if "access_token" not in r:
        raise SystemExit(f"token exchange failed: {r.get('error')} {r.get('error_description')}")
    r["obtained_at"] = time.time()
    tokens.put(PLAT, r)
    return whoami()


def _capture_local(auth_url, ru, timeout=600):
    """Serve the registered http://localhost redirect once and return the full redirected URL."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0].rstrip("/") == (ru.path or "/").rstrip("/"):
                got["url"] = f"http://{ru.netloc}{self.path}"
                body = b"TikTok connected. You can close this tab."
            else:
                body = b"waiting for TikTok"
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):  # keep the code out of logs
            pass

    srv = HTTPServer(("127.0.0.1", ru.port or 80), H)
    srv.timeout = 5
    print(f"Opening TikTok authorisation in your browser; waiting for the redirect on {ru.netloc} ...")
    webbrowser.open(auth_url)
    end = time.time() + timeout
    while "url" not in got and time.time() < end:
        srv.handle_request()
    srv.server_close()
    if "url" not in got:
        raise SystemExit("timed out waiting for TikTok authorisation")
    return got["url"]


def _token():
    t = tokens.get(PLAT)
    if not t:
        raise PermissionError("tiktok not connected: run `python -m autopilot connect tiktok`")
    if time.time() > t["obtained_at"] + t.get("expires_in", 86400) - 300:
        a = _app()
        r = requests.post(f"{API}/oauth/token/", data={"client_key": a["client_key"], "client_secret": a["client_secret"],
                                                       "grant_type": "refresh_token", "refresh_token": t["refresh_token"]}, timeout=30).json()
        if "access_token" not in r:
            raise PermissionError(f"tiktok refresh failed: {r.get('error')}")
        r["obtained_at"] = time.time(); tokens.put(PLAT, r); t = r
    return t["access_token"]


def _post(path, body):
    r = requests.post(f"{API}{path}", json=body, timeout=60,
                      headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json; charset=UTF-8"})
    js = r.json()
    err = js.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"tiktok {path}: {err.get('code')} {err.get('message')}")
    return js.get("data", {})


def whoami():
    r = requests.get(f"{API}/user/info/", params={"fields": "open_id,display_name,username,follower_count"},
                     headers={"Authorization": f"Bearer {_token()}"}, timeout=30).json()
    u = r.get("data", {}).get("user", {})
    return {"platform": PLAT, "account": u.get("username") or u.get("display_name"), "id": u.get("open_id"),
            "followers": u.get("follower_count")}


def revoke():
    t = tokens.get(PLAT); a = tokens.app(PLAT)
    if t and a:
        requests.post(f"{API}/oauth/revoke/", data={"client_key": a["client_key"], "client_secret": a["client_secret"],
                                                    "token": t["access_token"]}, timeout=30)
    tokens.delete(PLAT)


def creator_info():
    return _post("/post/publish/creator_info/query/", {})


def _upload(upload_url, path):
    size = os.path.getsize(path)
    chunk = size if size <= 64 << 20 else 10 << 20
    n = 1 if size <= 64 << 20 else math.floor(size / chunk)
    with open(path, "rb") as f:
        for i in range(n):
            start = i * chunk; end = size - 1 if i == n - 1 else start + chunk - 1
            f.seek(start); data = f.read(end - start + 1)
            r = requests.put(upload_url, data=data, timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)), "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"tiktok chunk {i} upload HTTP {r.status_code}")
    return size, chunk, n


def _source(path):
    size = os.path.getsize(path)
    chunk = size if size <= 64 << 20 else 10 << 20
    n = 1 if size <= 64 << 20 else math.floor(size / chunk)
    return {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": n}


def upload(path, caption, privacy="SELF_ONLY", mode="direct"):
    """mode=direct: Direct Post (needs video.publish). mode=inbox: send to the creator's TikTok inbox drafts."""
    if mode == "inbox":
        d = _post("/post/publish/inbox/video/init/", {"source_info": _source(path)})
    else:
        ci = creator_info()
        allowed = ci.get("privacy_level_options", [])
        if privacy not in allowed:
            raise RuntimeError(f"privacy {privacy} not allowed for this app/account (allowed: {allowed}). "
                               "Unaudited apps can only post SELF_ONLY.")
        d = _post("/post/publish/video/init/", {
            "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_comment": False,
                          "disable_duet": False, "disable_stitch": False, "brand_content_toggle": False,
                          "is_aigc": False},
            "source_info": _source(path)})
    _upload(d["upload_url"], path)
    return {"remote_id": d["publish_id"], "url": None}


def verify(publish_id, want_privacy):
    for _ in range(40):
        d = _post("/post/publish/status/fetch/", {"publish_id": publish_id})
        st = d.get("status")
        if st == "FAILED":
            return "failed", d.get("fail_reason")
        if st == "SEND_TO_USER_INBOX":
            return "manual_needed", "in TikTok inbox: open TikTok > notifications to finish the post"
        if st == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
            if want_privacy == "SELF_ONLY" or not ids:
                return "private_test", f"posted as {want_privacy}"
            return "published", str(ids[0])
        time.sleep(10)
    return "processing", "still processing"


def stats(video_ids):
    if not video_ids:
        return {}
    r = requests.post(f"{API}/video/query/", params={"fields": "id,view_count,like_count,comment_count,share_count,share_url"},
                      json={"filters": {"video_ids": video_ids[:20]}}, headers={"Authorization": f"Bearer {_token()}"}, timeout=30).json()
    return {v["id"]: {"views": v.get("view_count"), "likes": v.get("like_count"), "comments": v.get("comment_count"),
                      "shares": v.get("share_count"), "url": v.get("share_url")} for v in r.get("data", {}).get("videos", [])}
