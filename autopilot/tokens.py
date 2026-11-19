"""Protected token storage: OS keyring (Windows Credential Manager / macOS Keychain / Secret Service).

Tokens never touch the repo, the DB or the logs. App client credentials (not passwords) live in
data/secrets/<platform>.json, which is git-ignored.
"""
import json
import keyring
from .db import DATA

SERVICE = "gtavi-autopilot"
SECRETS = DATA / "secrets"


def get(platform):
    v = keyring.get_password(SERVICE, platform)
    return json.loads(v) if v else None


def put(platform, data):
    keyring.set_password(SERVICE, platform, json.dumps(data))


def delete(platform):
    try:
        keyring.delete_password(SERVICE, platform)
    except keyring.errors.PasswordDeleteError:
        pass


def app(platform):
    """Developer-app credentials the owner created on the platform's developer site."""
    v = keyring.get_password(SERVICE, f"{platform}-app")
    if v:
        return json.loads(v)
    p = SECRETS / f"{platform}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


APP_FIELDS = {"tiktok": ("client_key", "client_secret", "redirect_uri"),
              "instagram": ("client_id", "client_secret", "redirect_uri")}


def set_app(platform):
    """Owner types app credentials into hidden prompts; they go straight to the OS keyring."""
    import getpass
    if platform == "youtube":
        raise SystemExit("YouTube uses the downloaded OAuth client file: save it as data/secrets/youtube.json")
    data = {}
    for f in APP_FIELDS[platform]:
        ask = input if f == "redirect_uri" else getpass.getpass
        data[f] = ask(f"{platform} {f}{' (hidden)' if ask is not input else ''}: ").strip()
        if not data[f]:
            raise SystemExit(f"{f} is required")
    keyring.set_password(SERVICE, f"{platform}-app", json.dumps(data))
    return "stored in OS keyring"


def redact(s):
    s = str(s)
    for plat in ("youtube", "tiktok", "instagram"):
        t = get(plat) or {}
        t = {**(app(plat) or {}), **t}
        for k in ("access_token", "refresh_token", "token", "client_secret"):
            if t.get(k) and len(str(t[k])) > 8:
                s = s.replace(str(t[k]), "***")
    return s
