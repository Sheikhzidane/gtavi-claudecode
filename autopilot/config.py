"""Operator config (data/config.json) and the emergency stop switch."""
import json
from .db import DATA

DEFAULTS = {
    "mode": "review",                 # review | autopilot  (autopilot also needs a per-platform approval row)
    "daily_clip_limit": 3,            # finished clips per day across all sources
    "daily_posts_per_platform": 3,
    "min_gap_minutes": 120,           # spacing between posts on one platform
    "post_hours_local": [12, 17, 21], # preferred publish slots
    "platforms": ["youtube", "tiktok", "instagram"],
    "youtube_privacy": "public",      # used only in autopilot; tests always go private
    "brand": {"name": "Vice Clips Daily", "handle": "@viceclipsdaily",
              "tags": ["#GTA6", "#GTAVI", "#gaming", "#ViceCity"]},
    "clip_min": 15, "clip_max": 45,
    "voice_hook": True, "voice": "en-US-GuyNeural",
    "pause_on_errors": 3,             # consecutive publish failures on a platform -> pause that platform
}
CFG = DATA / "config.json"
STOP = DATA / "STOP"


def load():
    DATA.mkdir(exist_ok=True)
    cfg = dict(DEFAULTS)
    if CFG.exists():
        cfg.update(json.loads(CFG.read_text(encoding="utf-8")))
    else:
        save(cfg)
    return cfg


def save(cfg):
    DATA.mkdir(exist_ok=True)
    CFG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def stopped():
    return STOP.exists()


def stop(reason="manual"):
    DATA.mkdir(exist_ok=True)
    STOP.write_text(reason, encoding="utf-8")


def resume():
    if STOP.exists():
        STOP.unlink()
