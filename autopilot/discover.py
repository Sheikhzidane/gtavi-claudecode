"""Streamer and footage discovery: live GTA 6 streams (Twitch, YouTube) ranked by real concurrent viewers.

Every creator seen becomes a `candidate` row in the rights DB (never cleared automatically).
Returns only discoveries whose creator is cleared, so the pipeline never touches unlicensed footage.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from .db import conn, rows, log
from . import rights

SRC = {"YouTube": "youtube", "Twitch": "twitch", "Kick": "kick"}


def run(query="gta 6", limit=20):
    import streams  # scripts/streams.py
    found = []
    for fn, args in ((streams.youtube, (query, limit)), (streams.twitch, (query,))):
        try:
            found += fn(*args) or []
        except BaseException as e:  # yt-dlp wrappers sys.exit on failure
            log("discover", f"{fn.__name__} failed: {e}", "warn")
    now = time.time()
    with conn() as c:
        for r in found:
            plat = SRC.get(r["platform"], r["platform"].lower())
            creator = rights.norm(r["channel"])
            c.execute("INSERT INTO rights (creator, source_platform, status, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(creator, source_platform) DO NOTHING", (creator, plat, "candidate", now))
            st = c.execute("SELECT status FROM rights WHERE creator=? AND source_platform=?", (creator, plat)).fetchone()[0]
            c.execute("INSERT INTO discoveries (url, platform, creator, title, metric, metric_kind, live, seen_at, rights_status) "
                      "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET metric=excluded.metric, title=excluded.title, "
                      "seen_at=excluded.seen_at, rights_status=excluded.rights_status",
                      (r["url"], plat, creator, r["title"], r["viewers"] or 0, "concurrent_viewers", 1, now, st))
    cleared = [d for d in rows("SELECT * FROM discoveries WHERE seen_at=? ORDER BY metric DESC", now) if d["rights_status"] == "cleared"]
    log("discover", f"{len(found)} live streams seen, {len(cleared)} from cleared creators")
    return found, cleared


def top(n=15):
    return rows("SELECT platform, creator, metric, rights_status, title, url FROM discoveries "
                "WHERE seen_at > ? ORDER BY metric DESC LIMIT ?", time.time() - 6 * 3600, n)
