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
    p = SECRETS / f"{platform}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def redact(s):
    s = str(s)
    for plat in ("youtube", "tiktok", "instagram"):
        t = get(plat) or {}
        for k in ("access_token", "refresh_token", "token"):
            if t.get(k) and len(t[k]) > 8:
                s = s.replace(t[k], "***")
    return s
