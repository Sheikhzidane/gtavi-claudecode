"""Batch clipping for campaign-cleared Twitch creators, plus the ready_to_post export.

For each cleared Twitch creator: their most-viewed GTA VI clips (public Twitch data) mark the best moments
in their authorised replays. Only a short section of the replay around each moment is downloaded, and the
cut is pinned to that moment so the hook (the clip's own title) matches what is shown.
"""
import json, re, shutil, subprocess, sys, time
from pathlib import Path
from .db import ROOT, rows, one, execute, log
from . import rights, render, config

sys.path.insert(0, str(ROOT / "scripts"))
SRC = ROOT / "input" / "campaign_src"
READY = ROOT / "output" / "ready_to_post"
GAME = "Grand Theft Auto VI"
PAD_BEFORE, PAD_AFTER = 20, 40     # seconds of replay downloaded around a clip
CUT_BEFORE, CUT_LEN = 3, 38        # final cut: from 3 s before the clip starts, ~38 s long


def top_moments(login, n=50):
    import twitch
    q = """query($login:String!,$n:Int!){ user(login:$login){ clips(first:$n, criteria:{period:LAST_WEEK, sort:VIEWS_DESC}){ edges{ node{
     slug title viewCount durationSeconds videoOffsetSeconds game{ name } video{ id } } } } } }"""
    r = twitch.gql(q, {"login": login, "n": n})
    out = []
    for e in (((r.get("data") or {}).get("user") or {}).get("clips") or {}).get("edges") or []:
        x = e["node"]
        if (x.get("game") or {}).get("name") == GAME and (x.get("video") or {}).get("id") and x.get("videoOffsetSeconds") is not None:
            out.append({"vod": x["video"]["id"], "offset": int(x["videoOffsetSeconds"]), "dur": int(x["durationSeconds"]),
                        "title": x["title"].strip(), "views": x["viewCount"], "slug": x["slug"]})
    picked = []  # drop moments overlapping an already picked one (same replay, within 60 s)
    for m in sorted(out, key=lambda m: -m["views"]):
        if not any(p["vod"] == m["vod"] and abs(p["offset"] - m["offset"]) < 60 for p in picked):
            picked.append(m)
    return picked


def _safe(s):
    return re.sub(r'[<>:"/\\|?*]+', "", s).strip()[:70] or "moment"


BAD = re.compile(r"(sex|predator|porn|nude|horny|n[i1]gg|f[a@]g|retard)", re.I)
NEUTRAL = ["{who} in GTA 6: wait for it", "{who}'s GTA 6 reaction", "This happened on {who}'s GTA 6 stream"]


def _hook_title(t):
    """The clip's own title when it is a real, clean description; '' means use a neutral hook."""
    words = re.findall(r"[A-Za-z]{2,}", t)
    if BAD.search(t) or len(words) < 3 or len(t) < 12:
        return ""
    t = re.sub(r"([!?.])+", r"", t).strip()
    return t[:1].upper() + t[1:]


def clip_creator(login, display, max_clips=10):
    ok, rr = rights.is_cleared(login, "twitch")
    if not ok:
        log("batch", f"{login}: not cleared, skipped (discovery only)"); return []
    made = []
    for m in top_moments(login):
        if len(made) >= max_clips or config.stopped():
            break
        key = f"twitch:{m['vod']}@{m['offset']}"
        if one("SELECT id FROM clips WHERE source_url=?", key):
            continue  # already produced
        a, b = max(0, m["offset"] - PAD_BEFORE), m["offset"] + m["dur"] + PAD_AFTER
        name = f"{m['vod']}_{m['offset']}"
        hook = _hook_title(m["title"]) or NEUTRAL[m["offset"] % len(NEUTRAL)].format(who=display)
        f = SRC / login / f"{name}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            from common import tool
            ff = str(Path(tool("ffmpeg")).parent)
            r = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", "--compat-options", "no-certifi",
                                "--ffmpeg-location", ff, "-f", "best[height<=1080]/best", "--download-sections", f"*{a}-{b}",
                                "-o", str(f), f"https://www.twitch.tv/videos/{m['vod']}"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            if r.returncode != 0 or not f.exists():
                log("batch", f"{login} {key}: download failed {r.stderr[-160:]}", "warn"); continue
        w0 = m["offset"] - a - CUT_BEFORE
        ids = render.process(str(f), login, "twitch", display=display, max_clips=1, window=(max(0, w0), max(0, w0) + CUT_LEN),
                             source_key=key, source_link=f"https://www.twitch.tv/{login}/clip/{m['slug']}",
                             hook=hook)
        if ids:
            made += ids
            log("batch", f"{login}: clip {ids[0]} from '{m['title']}' ({m['views']} views) {key}")
    return made


def export_ready(day=None):
    """Copy finished, validated clips into output/ready_to_post/<day>/<Creator>/ with captions and info."""
    day = day or time.strftime("%Y-%m-%d")
    t0 = time.mktime(time.strptime(day, "%Y-%m-%d"))
    clips = rows("SELECT * FROM clips WHERE created_at>=? AND created_at<? AND status NOT IN ('test') ORDER BY id", t0, t0 + 86400)
    base = READY / day
    base.mkdir(parents=True, exist_ok=True)
    n, ready, failed, per = 0, [], [], {}
    for c in clips:
        meta = json.loads(c["meta"])
        rr = one("SELECT * FROM rights WHERE id=?", c["rights_id"]) or {}
        who = re.sub(r"[^\w-]+", "_", (meta.get("display") or c["creator"]).split(" (")[0]).strip("_") or c["creator"]
        n += 1
        tag = f"clip_{n:03d}"
        d = base / who; d.mkdir(exist_ok=True)
        okv = c["status"] != "rejected"
        if okv:
            shutil.copy2(c["path"], d / f"{tag}.mp4")
        y, t, ig = meta["youtube"], meta["tiktok"]["caption"], meta["instagram"]["caption"]
        (d / f"{tag}_caption.txt").write_text(
            f"=== INSTAGRAM REELS ===\n{ig}\n\n=== TIKTOK ===\n{t}\n\n=== YOUTUBE SHORTS ===\nTitle: {y['title']}\n\n{y['description']}\n",
            encoding="utf-8")
        checks = "\n".join(f"  {'PASS' if x['pass'] else 'FAIL'}  {x['check']}  {x['detail']}" for x in meta.get("checks", []))
        (d / f"{tag}_info.txt").write_text(
            f"Suggested title: {y['title']}\nHook: {meta['hook']}\nCreator: {meta.get('display') or c['creator']}\n"
            f"Original source: {meta.get('source_link') or meta.get('source')}\nSource section: {c['source_url']} "
            f"(cut {c['start']:.1f}-{c['end']:.1f} s)\nCampaign: {rr.get('campaign') or rr.get('basis')}\n"
            f"Permission evidence: {rr.get('evidence_url')}\nRules: {rr.get('evidence_note')}\n"
            f"Hashtags: {' '.join(w for w in t.split() if w.startswith('#'))}\n"
            f"Status: {'READY TO POST' if okv else 'FAILED VALIDATION, not copied'}\nChecks:\n{checks}\n", encoding="utf-8")
        (ready if okv else failed).append((who, tag, meta["hook"]))
        per[who] = per.get(who, 0) + 1
    return base, ready, failed, per


def daily_report(base, ready, failed, per, render_secs, blockers):
    camps = rows("SELECT DISTINCT campaign FROM rights WHERE status='cleared' AND campaign IS NOT NULL")
    md = [f"# DAILY REPORT {base.name}", "",
          f"- Videos produced: **{len(ready) + len(failed)}** (ready to post: **{len(ready)}**, failed validation: {len(failed)})",
          f"- Eligible creators covered: {len(per)} ({', '.join(per)})",
          f"- Campaigns used: {', '.join(c['campaign'] for c in camps) or 'none'}",
          f"- Rendering time this run: {render_secs / 60:.1f} min", "", "## Clips per creator"]
    md += [f"- {k}: {v}" for k, v in per.items()]
    md += ["", "## Ready to post"] + [f"- {w}/{t}.mp4: {h}" for w, t, h in ready]
    if failed:
        md += ["", "## Failed validation"] + [f"- {w}/{t}: {h}" for w, t, h in failed]
    md += ["", "## Rights / download blockers"] + [f"- {b}" for b in blockers]
    p = base / "DAILY_REPORT.md"
    p.write_text("\n".join(md) + "\n", encoding="utf-8")
    return p


def run(per_creator=10):
    t0 = time.time()
    made = []
    for r in rows("SELECT creator FROM rights WHERE status='cleared' AND source_platform='twitch'"):
        made += clip_creator(r["creator"], r["creator"].capitalize() if r["creator"] != "kreekcraft" else "KreekCraft", per_creator)
    blockers = ["YouTube downloads are off (YouTube bot check; no cookies or bypass), so CC BY YouTube sources are not clipped."]
    leads = rows("SELECT creator, campaign FROM rights WHERE campaign IS NOT NULL AND status!='cleared'")
    if leads:
        blockers.append("Campaigns not joined (footage not usable until you join and the rules are recorded): "
                        + "; ".join(f"{x['creator']} ({x['campaign']})" for x in leads))
    base, ready, failed, per = export_ready()
    return made, daily_report(base, ready, failed, per, time.time() - t0, blockers)
