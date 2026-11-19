"""Upload queue: review mode, autopilot mode, test uploads, dedupe, quotas, retries, pause rules, manual packages."""
import importlib, json, shutil, time
from pathlib import Path
from .db import ROOT, execute, one, rows, log, j
from . import config, tokens, rights

PLATS = ("youtube", "tiktok", "instagram")
FATAL = ("copyright", "strike", "spam", "suspended", "banned", "violat", "blocked", "restricted", "community guidelines")
MANUAL = ROOT / "output" / "manual"


def pub(platform):
    return importlib.import_module(f"autopilot.publishers.{platform}")


def connected(platform):
    return bool(tokens.get(platform))


def autopilot_ok(platform, cfg):
    a = one("SELECT autopilot FROM approvals WHERE platform=?", platform)
    return cfg["mode"] == "autopilot" and bool(a and a["autopilot"]) and platform not in cfg.get("paused", [])


def posted_today(platform):
    t0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return one("SELECT COUNT(*) n FROM posts WHERE platform=? AND privacy='public' AND status IN ('published','processing','uploading') "
               "AND created_at>=?", platform, t0)["n"]


def last_post_age_min(platform):
    r = one("SELECT MAX(created_at) t FROM posts WHERE platform=? AND privacy='public'", platform)
    return (time.time() - r["t"]) / 60 if r and r["t"] else 1e9


def _record(clip_id, platform, privacy, **kw):
    now = time.time()
    ex = one("SELECT id, attempts FROM posts WHERE clip_id=? AND platform=? AND privacy=?", clip_id, platform, privacy)
    if ex:
        sets = ", ".join(f"{k}=?" for k in kw)
        execute(f"UPDATE posts SET {sets}, updated_at=? WHERE id=?", *kw.values(), now, ex["id"])
        return ex["id"]
    cols = ["clip_id", "platform", "privacy", "created_at", "updated_at", *kw]
    return execute(f"INSERT INTO posts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   clip_id, platform, privacy, now, now, *kw.values())


def _fail(clip_id, platform, privacy, err, cfg):
    err = tokens.redact(err)[:500]
    p = one("SELECT attempts FROM posts WHERE clip_id=? AND platform=? AND privacy=?", clip_id, platform, privacy)
    _record(clip_id, platform, privacy, status="failed", error=err, attempts=(p["attempts"] if p else 0) + 1)
    log("publish", f"{platform} clip {clip_id} failed: {err}", "error")
    low = err.lower()
    if any(w in low for w in FATAL):
        config.stop(f"auto-stop: {platform} reported '{err[:120]}'")
        log("safety", f"EMERGENCY STOP engaged: possible copyright/account problem on {platform}", "error")
    recent = rows("SELECT status FROM posts WHERE platform=? ORDER BY updated_at DESC LIMIT ?", platform, cfg["pause_on_errors"])
    if len(recent) >= cfg["pause_on_errors"] and all(r["status"] == "failed" for r in recent):
        cfg.setdefault("paused", []).append(platform); cfg["paused"] = sorted(set(cfg["paused"])); config.save(cfg)
        log("safety", f"{platform} paused after {cfg['pause_on_errors']} consecutive failures", "error")


def publish_clip(clip, platform, privacy, cfg, test=False):
    """privacy: 'public' or 'private' (test). Returns post row status."""
    meta = json.loads(clip["meta"])
    if not test:
        ok, rr = rights.is_cleared(clip["creator"], (one("SELECT source_platform FROM rights WHERE id=?", clip["rights_id"]) or {}).get("source_platform", ""), platform)
        if not ok:
            log("publish", f"blocked clip {clip['id']} on {platform}: rights not cleared for this platform", "warn"); return "blocked"
    if one("SELECT p.id FROM posts p JOIN clips c ON c.id=p.clip_id WHERE c.sha256=? AND p.platform=? AND p.privacy=? "
           "AND p.status IN ('published','private_test','processing','manual_needed') AND COALESCE(p.error,'') NOT LIKE '%not connected%'", clip["sha256"], platform, privacy):
        return "duplicate"
    _record(clip["id"], platform, privacy, status="uploading", account=(tokens.get(platform) or {}).get("user_id"))
    try:
        P = pub(platform)
        if platform == "youtube":
            y = meta["youtube"]
            r = P.upload(clip["path"], y["title"], y["description"], y["tags"], privacy="private" if test else cfg["youtube_privacy"])
            state, detail = P.verify(r["remote_id"], "private" if test else cfg["youtube_privacy"])
        elif platform == "tiktok":
            want = "SELF_ONLY"
            if not test:
                opts = P.creator_info().get("privacy_level_options", [])
                want = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else None
            if want:
                r = P.upload(clip["path"], meta["tiktok"]["caption"], privacy=want)
            else:  # unaudited app: inbox upload, owner taps Post in TikTok
                r = P.upload(clip["path"], meta["tiktok"]["caption"], mode="inbox")
            state, detail = P.verify(r["remote_id"], want or "INBOX")
        else:
            if not test:
                used, total = P.quota()
                if used >= total - 1:
                    log("publish", f"instagram publishing quota {used}/{total} reached", "warn"); return "quota"
            r = P.upload(clip["path"], meta["instagram"]["caption"], publish=not test)
            state, detail = P.verify(r["remote_id"], container_only=r.get("container_only", False))
            if state == "published":
                r["url"] = detail
    except PermissionError as e:
        _record(clip["id"], platform, privacy, status="manual_needed", error=str(e)); manual_package(clip, platform, str(e)); return "manual_needed"
    except Exception as e:
        _fail(clip["id"], platform, privacy, f"{type(e).__name__}: {e}", cfg); return "failed"
    if state == "failed":
        _fail(clip["id"], platform, privacy, detail, cfg); return "failed"
    verified = int(state in ("published", "private_test"))
    _record(clip["id"], platform, privacy, status=state, remote_id=r["remote_id"], url=r.get("url"), verified=verified, error=None if verified else detail)
    log("publish", f"{platform} clip {clip['id']} -> {state} ({detail}) {r.get('url') or ''}")
    if state in ("manual_needed", "locked_private"):
        manual_package(clip, platform, detail)
    return state


def manual_package(clip, platform, blocker):
    meta = json.loads(clip["meta"])
    d = MANUAL / time.strftime("%Y-%m-%d") / platform
    d.mkdir(parents=True, exist_ok=True)
    dst = d / Path(clip["path"]).name
    if not dst.exists():
        shutil.copy2(clip["path"], dst)
    txt = meta["youtube"]["title"] + "\n\n" + meta["youtube"]["description"] if platform == "youtube" else meta[platform]["caption"]
    (d / (dst.stem + ".txt")).write_text(f"BLOCKER: {blocker}\n\nPost this file manually: {dst}\n\n--- caption ---\n{txt}\n", encoding="utf-8")


def eligible_clips(cfg):
    """Review mode: only clips the owner approved. Autopilot: approved + checked clips awaiting review."""
    st = ("approved", "awaiting_review") if cfg["mode"] == "autopilot" else ("approved",)
    return rows(f"SELECT * FROM clips WHERE status IN ({','.join('?' * len(st))}) ORDER BY score DESC", *st)


def due(cfg=None):
    cfg = cfg or config.load()
    if config.stopped():
        log("publish", "emergency stop is ON, nothing published"); return []
    hour = int(time.strftime("%H"))
    done = []
    for clip in eligible_clips(cfg):
        handled = 0
        for plat in cfg["platforms"]:
            if one("SELECT id FROM posts WHERE clip_id=? AND platform=? AND privacy='public' AND status!='failed' AND COALESCE(error,'') NOT LIKE '%not connected%'", clip["id"], plat):
                handled += 1; continue
            p = one("SELECT attempts FROM posts WHERE clip_id=? AND platform=? AND privacy='public'", clip["id"], plat)
            if p and p["attempts"] >= 3:
                handled += 1; continue
            if not connected(plat):
                if not one("SELECT id FROM posts WHERE clip_id=? AND platform=? AND privacy='public'", clip["id"], plat):
                    _record(clip["id"], plat, "public", status="manual_needed", error="account not connected")
                    manual_package(clip, plat, "account not connected (python -m autopilot connect " + plat + ")")
                continue  # stays queued; posts automatically once the account is connected
            if clip["status"] == "awaiting_review" and not autopilot_ok(plat, cfg):
                continue
            if plat in cfg.get("paused", []):
                continue
            if posted_today(plat) >= cfg["daily_posts_per_platform"] or last_post_age_min(plat) < cfg["min_gap_minutes"]:
                continue
            if cfg.get("post_hours_local") and hour < min(cfg["post_hours_local"]):
                continue
            s = publish_clip(clip, plat, "public", cfg)
            done.append((clip["id"], plat, s))
            if s in ("published", "manual_needed", "locked_private", "duplicate", "blocked"):
                handled += 1
            if config.stopped():
                return done
        if handled == len(cfg["platforms"]):
            execute("UPDATE clips SET status='done' WHERE id=?", clip["id"])
    return done
