"""Automatic GTA VI creator scout: discovers and ranks streamers and footage with no names given.

Sources (all public, no login):
  twitch_live   top live GTA VI streams by concurrent viewers (Twitch public GQL)
  twitch_clips  most-viewed GTA VI clips of the last day: shows which streamers are going viral right now
  youtube_live  live GTA 6 streams by concurrent viewers
  youtube_new   GTA 6 uploads of the last 48 h ranked by views per hour
  youtube_cc    GTA 6 videos licensed Creative Commons Attribution (reuse allowed), each licence checked on the video
  campaigns     creators with a known clipping campaign (a lead, not permission until the owner joins)

Every creator lands in scout_creators with a score. Licensed videos land in scout_opportunities.
Only CC-BY videos (licence verified per video) or creators the owner cleared are ever rendered.
"""
import json, math, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from .db import conn, rows, one, execute, log, ROOT
from . import rights

GAME = "Grand Theft Auto VI"
MOMENT = re.compile(r"\b(chase|police|cops?|wanted|funny|fail|crash|secret|hidden|easter egg|heist|robbery|wtf|insane|crazy|"
                    r"rare|glitch|lmao|reaction|escape|jump|stunt|military|tank|jet|cruise|strip|alligator|gator)\b", re.I)
NOT_OWN = re.compile(r"\b(compilation|best of|top \d+|reacts?|reaction to|asmongold|streamers? (react|play)|clips? of|"
                     r"news|leak|trailer|woke|podcast|ep\.\s*\d|ranking|funniest moments|moments compilation|tier list|banir|ban)\b", re.I)
# footage the uploader credits to someone else: CC on a re-upload is not the original creator's permission
OTHERS = re.compile(r"(credits? (to|go)|source:|all rights (go|belong|reserved to)|rights belong|footage (by|from)|twitch\.tv/|"
                    r"original video|clip(ped)? from|from the stream of)", re.I)
# repo hard rules: no leaks, early-access, piracy, GTA Online money or AFK exploits
BANNED = re.compile(r"(money glitch|unlimited money|infinite \$|infinite money|money exploit|\bleak|play early|free download|"
                    r"crack|mod menu|afk)", re.I)
# talk / buyer-guide videos without gameplay moments to clip
TALK = re.compile(r"(pre[- ]?order|edition|settings|fps\b|hours away|almost here|are we ready|effect is|who can|should you buy|"
                  r"nba ?2k|patch|price|review\b)", re.I)
CC_QUERIES = ["gta 6 gameplay", "gta 6 funny moments", "gta 6 police chase", "gta 6 secrets", "gta 6 stunts",
              "gta 6 easter egg", "gta 6 mission", "gta 6 free roam"]
CC_FILTER = "&sp=EgIwAQ%253D%253D"  # YouTube search filter: Creative Commons

SCHEMA = """
CREATE TABLE IF NOT EXISTS scout_creators (
  platform TEXT, creator TEXT, display TEXT, url TEXT, followers INTEGER, live_viewers INTEGER,
  clip_views_24h INTEGER, top_clip TEXT, video_vph INTEGER, cc_videos INTEGER, campaign TEXT,
  rights_status TEXT, signals TEXT, score REAL, updated REAL, PRIMARY KEY(platform, creator));
CREATE TABLE IF NOT EXISTS scout_opportunities (
  url TEXT PRIMARY KEY, platform TEXT, creator TEXT, display TEXT, title TEXT, license TEXT, views INTEGER,
  vph INTEGER, duration INTEGER, upload_date TEXT, moments INTEGER, score REAL, status TEXT, reason TEXT, found REAL, rendered REAL);
"""


def _db():
    with conn() as c:
        c.executescript(SCHEMA)


def _yt(url, extra=()):
    from common import ytdlp_json
    try:
        return ytdlp_json(url, list(extra))
    except BaseException:
        return None


def _flat(url, n):
    import subprocess
    from common import ytdlp_base
    r = subprocess.run(ytdlp_base() + ["--flat-playlist", "-J", "--playlist-end", str(n), url], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        return json.loads(r.stdout).get("entries") or []
    except Exception:
        return []


def twitch_live(n=30):
    import twitch
    try:
        return [{"platform": "twitch", "creator": r["login"].lower(), "display": r["login"], "url": r["url"],
                 "live_viewers": r["viewers"], "title": r["title"]} for r in twitch.top_streams(GAME, n)]
    except Exception as e:
        log("scout", f"twitch live failed: {e}", "warn"); return []


def twitch_clips(n=50):
    import twitch
    q = """query($name:String!,$n:Int!){ game(name:$name){ clips(first:$n, criteria:{period:LAST_DAY, sort:VIEWS_DESC}){ edges{ node{
     title viewCount url durationSeconds broadcaster{ login displayName followers{ totalCount } } } } } } }"""
    try:
        r = twitch.gql(q, {"name": GAME, "n": n})
        edges = (((r.get("data") or {}).get("game") or {}).get("clips") or {}).get("edges") or []
    except Exception as e:
        log("scout", f"twitch clips failed: {e}", "warn"); return []
    out = []
    for e in edges:
        x = e["node"]; b = x.get("broadcaster") or {}
        if b.get("login"):
            out.append({"platform": "twitch", "creator": b["login"].lower(), "display": b.get("displayName") or b["login"],
                        "url": f"https://twitch.tv/{b['login']}", "followers": (b.get("followers") or {}).get("totalCount"),
                        "clip_views": x["viewCount"], "clip": f"{x['title'][:60]} ({x['viewCount']:,} views) {x['url']}", "title": x["title"]})
    return out


def _yt_api():
    from . import tokens
    return bool(tokens.get("youtube"))


def youtube_live_api(n=25):
    from .publishers import youtube as Y
    ids = Y.api_search("gta 6", n, event="live")
    meta, live = Y.api_videos(ids), Y.api_live_viewers(ids)
    return [{"platform": "youtube", "creator": rights.norm(m["channel"]), "display": m["channel"],
             "url": f"https://www.youtube.com/channel/{m['channel_id']}", "live_viewers": live.get(v, 0), "title": m["title"]}
            for v, m in meta.items()]


def youtube_new_api(n=25, hours=48):
    import datetime as dt
    from .publishers import youtube as Y
    ids = Y.api_search("gta 6", n, hours=hours)
    meta = Y.api_videos(ids)
    subs = Y.api_channels(list({m["channel_id"] for m in meta.values()}))
    out, now = [], time.time()
    for v, m in meta.items():
        t = dt.datetime.fromisoformat(m["published"].replace("Z", "+00:00")).timestamp()
        out.append({"platform": "youtube", "creator": rights.norm(m["channel"]), "display": m["channel"],
                    "url": f"https://www.youtube.com/channel/{m['channel_id']}", "followers": subs.get(m["channel_id"]),
                    "vph": int(m["views"] / max(0.25, (now - t) / 3600)), "title": m["title"], "license": m["license"],
                    "video": f"https://www.youtube.com/watch?v={v}"})
    return out


def youtube_live(n=15):
    if _yt_api():
        try:
            return youtube_live_api()
        except Exception as e:
            log("scout", f"YouTube API live search failed: {str(e)[:120]}", "warn")
    import streams
    try:
        return [{"platform": "youtube", "creator": rights.norm(r["channel"]), "display": r["channel"], "url": r["url"],
                 "live_viewers": r["viewers"] or 0, "title": r["title"]} for r in streams.youtube("gta 6", n)]
    except BaseException as e:
        log("scout", f"youtube live failed: {e}", "warn"); return []


def youtube_new(n=25, hours=48):
    if _yt_api():
        try:
            return youtube_new_api(n, hours)
        except Exception as e:
            log("scout", f"YouTube API new-video search failed: {str(e)[:120]}", "warn")
    ents = _flat("https://www.youtube.com/results?search_query=gta+6&sp=CAI%253D", n)
    with ThreadPoolExecutor(6) as ex:
        infos = [i for i in ex.map(lambda e: _yt(e["url"], ["--skip-download"]) if e.get("url") and "/shorts/" not in e["url"] else None, ents) if i]
    out, now = [], time.time()
    for i in infos:
        t = i.get("timestamp") or i.get("release_timestamp")
        if not t or i.get("is_live") or (now - t) / 3600 > hours:
            continue
        vph = int((i.get("view_count") or 0) / max(0.25, (now - t) / 3600))
        out.append({"platform": "youtube", "creator": rights.norm(i.get("channel")), "display": i.get("channel"),
                    "url": f"https://www.youtube.com/channel/{i.get('channel_id')}", "followers": i.get("channel_follower_count"),
                    "vph": vph, "title": i.get("title") or "", "license": i.get("license"), "video": i.get("webpage_url")})
    return out


CLIP_CHANNEL = re.compile(r"(clips|compilation|highlights|best moments|shorts? ?(hub|daily))", re.I)


def youtube_cc_api(queries=4):
    """Official route: YouTube Data API search with videoLicense=creativeCommon, licence read per video."""
    import datetime as dt
    from .publishers import youtube as Y
    ids = []
    for q in CC_QUERIES[:queries]:
        ids += [v for v in Y.api_search_cc(q, 25) if v not in ids]
    meta = Y.api_videos(ids)
    subs = Y.api_channels(list({m["channel_id"] for m in meta.values()}))
    infos = [{"title": m["title"], "license": m["license"],
              "timestamp": dt.datetime.fromisoformat(m["published"].replace("Z", "+00:00")).timestamp(),
              "view_count": m["views"], "duration": m["duration"], "channel": m["channel"], "channel_id": m["channel_id"],
              "channel_follower_count": subs.get(m["channel_id"]), "description": m["description"],
              "webpage_url": f"https://www.youtube.com/watch?v={m['id']}", "upload_date": m["published"][:10].replace("-", "")}
             for m in meta.values() if m.get("live") == "none"]
    return _classify_cc(infos)


def youtube_cc(per_query=15):
    from . import tokens
    if tokens.get("youtube"):
        try:
            return youtube_cc_api()
        except Exception as e:
            log("scout", f"YouTube API CC search failed ({str(e)[:120]}), falling back to public pages", "warn")
    ids = {}
    for q in CC_QUERIES:
        for e in _flat(f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}{CC_FILTER}", per_query):
            if e.get("id") and (e.get("duration") or 0) >= 20:
                ids.setdefault(e["id"], e)
    with ThreadPoolExecutor(3) as ex:
        infos = [i for i in ex.map(lambda v: _yt(f"https://www.youtube.com/watch?v={v}", ["--skip-download"]), list(ids)) if i]
    return _classify_cc(infos)


def _classify_cc(infos):
    out, now = [], time.time()
    for i in infos:
        title = i.get("title") or ""
        lic = i.get("license") or ""
        t = i.get("timestamp") or now
        vph = int((i.get("view_count") or 0) / max(0.25, (now - t) / 3600))
        gta6 = bool(re.search(r"gta\s*(6|vi)\b|grand theft auto (6|vi)\b", title, re.I))
        if "Creative Commons" not in lic:
            status, why = "no_license", "licence on the video is not Creative Commons"
        elif not gta6:
            status, why = "skip", "not clearly GTA VI"
        elif CLIP_CHANNEL.search(i.get("channel") or ""):
            status, why = "skip", "clip/compilation channel: footage likely re-uploaded from others"
        elif OTHERS.search(i.get("description") or ""):
            status, why = "skip", "description credits another source: footage may not be the uploader's own"
        elif NOT_OWN.search(title):
            status, why = "skip", "looks like a compilation, reaction or news video: may contain other people's footage"
        elif BANNED.search(title):
            status, why = "skip", "banned topic (money glitch / leak / exploit)"
        elif TALK.search(title):
            status, why = "skip", "talk or buyer-guide video, no gameplay moment to clip"
        elif (i.get("duration") or 0) < 90:
            status, why = "skip", "shorter than 90 s: re-cutting it would be close to a re-upload"
        elif (i.get("duration") or 0) > 3 * 3600:
            status, why = "skip", "longer than 3 h"
        else:
            status, why = "licensed", "CC BY on the video itself: reuse allowed with attribution"
        out.append({"platform": "youtube", "creator": rights.norm(i.get("channel")), "display": i.get("channel"),
                    "url": f"https://www.youtube.com/channel/{i.get('channel_id')}", "followers": i.get("channel_follower_count"),
                    "video": i.get("webpage_url"), "title": title, "license": lic, "views": i.get("view_count") or 0, "vph": vph,
                    "duration": i.get("duration"), "upload_date": i.get("upload_date"), "status": status, "reason": why})
    return out


def _score(c):
    s = (0.9 * math.log10((c.get("live_viewers") or 0) + 1) + 0.8 * math.log10((c.get("clip_views_24h") or 0) + 1)
         + 0.7 * math.log10((c.get("video_vph") or 0) + 1) + 0.4 * math.log10((c.get("followers") or 0) + 1))
    s += min(1.0, 0.25 * len(set(m.lower() for m in MOMENT.findall(c.get("signals") or ""))))
    if c.get("cc_videos"):
        s += 2.0   # footage we can legally clip today
    if c.get("rights_status") == "cleared":
        s += 2.0
    elif c.get("campaign"):
        s += 1.0   # paid clipping campaign exists: join it to unlock
    return round(s, 2)


def run(youtube=True):
    _db()
    t0 = time.time()
    res = {"yl": [], "yn": [], "cc": []}
    with ThreadPoolExecutor(2) as ex:  # Twitch in parallel; YouTube sources one after another (it throttles bursts)
        tl, tc = ex.submit(twitch_live), ex.submit(twitch_clips)
        for k, fn in ((("yl", youtube_live), ("yn", youtube_new), ("cc", youtube_cc)) if youtube else ()):
            for attempt in range(2):
                try:
                    res[k] = fn()
                except Exception as e:
                    log("scout", f"{fn.__name__} error: {e}", "warn"); res[k] = []
                if res[k]:
                    break
                time.sleep(20)
        res["tl"], res["tc"] = tl.result(), tc.result()
    agg = {}

    def get(r):
        k = (r["platform"], r["creator"])
        if k not in agg:
            agg[k] = {"platform": r["platform"], "creator": r["creator"], "display": r.get("display"), "url": r.get("url"),
                      "followers": None, "live_viewers": 0, "clip_views_24h": 0, "top_clip": None, "video_vph": 0, "cc_videos": 0, "signals": ""}
        return agg[k]

    for r in res["tl"] + res["yl"]:
        a = get(r); a["live_viewers"] = max(a["live_viewers"], r.get("live_viewers") or 0); a["signals"] += " " + r.get("title", "")
    for r in res["tc"]:
        a = get(r); a["clip_views_24h"] += r["clip_views"]; a["followers"] = a["followers"] or r.get("followers")
        a["top_clip"] = a["top_clip"] or r["clip"]; a["signals"] += " " + r["title"]
    for r in res["yn"]:
        a = get(r); a["video_vph"] = max(a["video_vph"], r["vph"]); a["followers"] = a["followers"] or r.get("followers"); a["signals"] += " " + r["title"]
    for r in res["cc"]:
        if r["status"] == "licensed":
            a = get(r); a["cc_videos"] += 1; a["followers"] = a["followers"] or r.get("followers"); a["signals"] += " " + r["title"]
            a["video_vph"] = max(a["video_vph"], r["vph"])
    leads = {(x["source_platform"], x["creator"]): x for x in rows("SELECT * FROM rights WHERE campaign IS NOT NULL OR status='cleared'")}
    now = time.time()
    with conn() as c:
        for (plat, cr), a in agg.items():
            rr = leads.get((plat, cr)) or c.execute("SELECT * FROM rights WHERE creator=? AND source_platform=?", (cr, plat)).fetchone()
            rr = dict(rr) if rr else {}
            a["campaign"] = rr.get("campaign"); a["rights_status"] = rr.get("status") or "candidate"
            if not rr:
                c.execute("INSERT OR IGNORE INTO rights (creator, source_platform, status, updated_at) VALUES (?,?,?,?)", (cr, plat, "candidate", now))
            a["score"] = _score(a)
            c.execute("INSERT OR REPLACE INTO scout_creators VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (plat, cr, a["display"], a["url"], a["followers"], a["live_viewers"], a["clip_views_24h"], a["top_clip"],
                       a["video_vph"], a["cc_videos"], a["campaign"], a["rights_status"], a["signals"][-600:], a["score"], now))
        for r in res["cc"]:
            moments = len(set(m.lower() for m in MOMENT.findall(r["title"])))
            sc = round(math.log10(r["views"] + 1) + 0.8 * math.log10(r["vph"] + 1) + 0.4 * moments, 2)
            ex_ = c.execute("SELECT rendered FROM scout_opportunities WHERE url=?", (r["video"],)).fetchone()
            c.execute("INSERT OR REPLACE INTO scout_opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["video"], "youtube", r["creator"], r["display"], r["title"], r["license"], r["views"], r["vph"], r["duration"],
                       r["upload_date"], moments, sc, r["status"], r["reason"], now, ex_[0] if ex_ else None))
    with conn() as c:  # re-apply the content rules to stored opportunities (rules may have tightened)
        for u, t in c.execute("SELECT url, title FROM scout_opportunities WHERE status='licensed'").fetchall():
            for rx, why in ((NOT_OWN, "compilation, reaction or news video"), (BANNED, "banned topic (money glitch / leak / exploit)"),
                            (TALK, "talk or buyer-guide video, no gameplay moment to clip")):
                if rx.search(t or ""):
                    c.execute("UPDATE scout_opportunities SET status='skip', reason=? WHERE url=?", (why, u)); break
    log("scout", f"{len(agg)} creators ranked in {time.time() - t0:.0f}s: twitch live {len(res['tl'])}, twitch clips {len(res['tc'])}, "
                 f"youtube live {len(res['yl'])}, youtube new {len(res['yn'])}, CC videos checked {len(res['cc'])} "
                 f"({sum(1 for r in res['cc'] if r['status'] == 'licensed')} licensed)")
    return report()


def top(n=25):
    return rows("SELECT * FROM scout_creators WHERE updated > ? ORDER BY score DESC LIMIT ?", time.time() - 24 * 3600, n)


def opportunities(n=15):
    return rows("SELECT * FROM scout_opportunities WHERE status='licensed' AND rendered IS NULL ORDER BY score DESC LIMIT ?", n)


def report():
    out = ROOT / "reports"; out.mkdir(exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    t = top(30); o = rows("SELECT * FROM scout_opportunities WHERE status='licensed' ORDER BY score DESC LIMIT 20")
    f = lambda x: "" if x in (None, 0) else f"{x:,}"
    md = [f"# GTA VI scout {day} {time.strftime('%H:%M')}", "",
          "Ranked automatically from public Twitch and YouTube data. **Rights:** `cleared` = may be clipped; `campaign` = a paid clipping "
          "campaign exists (join it to unlock); `candidate` = discovery only, never downloaded or reposted.", "",
          "## Top creators", "| # | score | platform | creator | followers | live now | clip views 24h | best video views/h | CC videos | rights | link |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, c in enumerate(t, 1):
        r = "cleared" if c["rights_status"] == "cleared" else ("CC licence" if c["cc_videos"] else ("campaign" if c["campaign"] else "candidate"))
        md.append(f"| {k} | {c['score']} | {c['platform']} | {c['display']} | {f(c['followers'])} | {f(c['live_viewers'])} | "
                  f"{f(c['clip_views_24h'])} | {f(c['video_vph'])} | {c['cc_videos'] or ''} | {r} | {c['url']} |")
    md += ["", "## Viral right now (top Twitch clip per creator)"]
    md += [f"- **{c['display']}**: {c['top_clip']}" for c in sorted(t, key=lambda x: -(x["clip_views_24h"] or 0))[:10] if c["top_clip"]]
    md += ["", "## Authorised clipping opportunities (licence checked on each video)",
           "| score | creator | video | views | views/h | length | licence | rendered |", "|---|---|---|---|---|---|---|---|"]
    for x in o:
        md.append(f"| {x['score']} | {x['display']} | [{x['title'][:60]}]({x['url']}) | {f(x['views'])} | {f(x['vph'])} | "
                  f"{(x['duration'] or 0) // 60}m{(x['duration'] or 0) % 60:02d}s | CC BY | {'yes' if x['rendered'] else ''} |")
    camp = rows("SELECT creator, source_platform, campaign, rate_per_1k, evidence_url FROM rights WHERE campaign IS NOT NULL AND status!='cleared'")
    md += ["", "## Clipping campaigns found (join to unlock: not permission yet)"]
    md += [f"- {c['creator']} ({c['source_platform']}): {c['campaign']}, ${c['rate_per_1k'] or '?'}/1K views, {c['evidence_url']}" for c in camp]
    p = out / f"scout-{day}.md"
    p.write_text("\n".join(md) + "\n", encoding="utf-8")
    return p
