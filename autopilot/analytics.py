"""Real analytics only: pulls platform stats for verified posts, writes daily reports, learns category weights.

Earnings are never estimated. Record verified payouts in data/earnings.csv (date,campaign,platform,amount,currency,evidence_url).
"""
import csv, json, time
from collections import defaultdict
from .db import ROOT, DATA, execute, rows, log
from . import config, tokens
from .publish import pub

REPORTS = ROOT / "reports"


def fetch():
    n = 0
    for plat in ("youtube", "tiktok", "instagram"):
        if not tokens.get(plat):
            continue
        posts = rows("SELECT id, remote_id FROM posts WHERE platform=? AND verified=1 AND remote_id IS NOT NULL", plat)
        ids = [p["remote_id"] for p in posts]
        if not ids:
            continue
        try:
            st = pub(plat).stats(ids)
        except Exception as e:
            log("analytics", f"{plat} stats failed: {tokens.redact(e)}", "warn"); continue
        for p in posts:
            s = st.get(p["remote_id"])
            if s:
                execute("INSERT INTO metrics VALUES (?,?,?,?,?,?,?,?,?)", p["id"], time.time(), s.get("views"), s.get("likes"),
                        s.get("comments"), s.get("shares"), s.get("watch_seconds"), s.get("avg_view_pct"), json.dumps(s))
                n += 1
    log("analytics", f"{n} metric snapshots stored")
    return n


def latest():
    return rows("""SELECT p.platform, p.url, p.privacy, c.creator, c.category, c.hook, c.end-c.start dur,
                  m.views, m.likes, m.comments, m.shares, m.watch_seconds, m.avg_view_pct
                  FROM posts p JOIN clips c ON c.id=p.clip_id
                  LEFT JOIN metrics m ON m.post_id=p.id AND m.ts=(SELECT MAX(ts) FROM metrics WHERE post_id=p.id)
                  WHERE p.verified=1""")


def learn():
    """Category weight = category avg views / overall avg (needs >=3 public posts per category), clipped 0.5..2."""
    data = [r for r in latest() if r["privacy"] == "public" and r["views"] is not None]
    if len(data) < 6:
        return {}
    allavg = sum(r["views"] for r in data) / len(data) or 1
    by = defaultdict(list)
    for r in data:
        by[r["category"]].append(r["views"])
    w = {c: round(min(2, max(0.5, (sum(v) / len(v)) / allavg)), 2) for c, v in by.items() if len(v) >= 3}
    cfg = config.load(); cfg["category_weights"] = w; config.save(cfg)
    log("learn", f"category weights {w}")
    return w


def earnings():
    p = DATA / "earnings.csv"
    if not p.exists():
        return []
    return list(csv.DictReader(p.open(encoding="utf-8")))


def report():
    REPORTS.mkdir(exist_ok=True)
    day = time.strftime("%Y-%m-%d"); t0 = time.mktime(time.strptime(day, "%Y-%m-%d"))
    clips = rows("SELECT id, status, creator, category, path FROM clips WHERE created_at>=?", t0)
    posts = rows("SELECT p.*, c.creator FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.updated_at>=?", t0)
    lat = latest(); cfg = config.load()
    by_creator, by_cat = defaultdict(int), defaultdict(int)
    for r in lat:
        if r["views"]:
            by_creator[r["creator"]] += r["views"]; by_cat[r["category"]] += r["views"]
    md = [f"# Daily report {day}", "", f"Mode: **{cfg['mode']}** · Emergency stop: **{'ON' if config.stopped() else 'off'}** · "
          f"Paused: {', '.join(cfg.get('paused', [])) or 'none'}", "",
          f"## Clips rendered today: {len(clips)}", *[f"- #{c['id']} {c['status']} {c['creator']}/{c['category']} `{c['path']}`" for c in clips], "",
          f"## Uploads touched today: {len(posts)}",
          *[f"- {p['platform']} clip #{p['clip_id']} {p['privacy']} **{p['status']}** verified={bool(p['verified'])} {p['url'] or ''} {('ERR ' + p['error']) if p['error'] else ''}" for p in posts], "",
          "## Verified post stats (latest snapshot)", "| platform | creator | category | views | likes | comments | shares | url |", "|---|---|---|---|---|---|---|---|",
          *[f"| {r['platform']} | {r['creator']} | {r['category']} | {r['views']} | {r['likes']} | {r['comments']} | {r['shares']} | {r['url'] or ''} |" for r in lat], "",
          "## Top creators by views", *[f"- {k}: {v}" for k, v in sorted(by_creator.items(), key=lambda x: -x[1])[:5]], "",
          "## Top categories by views", *[f"- {k}: {v}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])[:5]], "",
          f"## Failures today: {sum(1 for p in posts if p['status'] == 'failed')}", "",
          "## Verified earnings (from data/earnings.csv only)", *([f"- {e['date']} {e['campaign']} {e['platform']} {e['amount']} {e['currency']} {e['evidence_url']}" for e in earnings()] or ["- none recorded"])]
    out = REPORTS / f"{day}.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return out
