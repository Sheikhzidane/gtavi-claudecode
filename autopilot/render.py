"""Render pipeline: cleared source -> best moments -> finished, validated 1080x1920 MP4 packages.

output/<YYYY-MM-DD>/<creator>/<category>/clip_<id>.mp4  (+ .json package with per-platform text)
"""
import hashlib, json, re, subprocess, sys, time
from pathlib import Path
from .db import ROOT, execute, one, rows, j, log
from . import rights, content, config

PY = sys.executable
SCRIPTS = ROOT / "scripts"
OUT = ROOT / "output"
sys.path.insert(0, str(SCRIPTS))


def _run(args, timeout=3600):
    r = subprocess.run([PY, *map(str, args)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env={**__import__("os").environ, "PYTHONUTF8": "1",
                                                               "CLIP_DESK_HOME": str(ROOT)})
    return r.returncode, r.stdout + r.stderr


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def clips_today():
    t0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return one("SELECT COUNT(*) n FROM clips WHERE created_at>=? AND status!='rejected'", t0)["n"]


def voice_hook(text, out, voice):
    try:
        import asyncio, edge_tts
        asyncio.run(edge_tts.Communicate(text, voice, rate="+8%").save(str(out)))
        return out if out.exists() and out.stat().st_size > 1000 else None
    except Exception as e:
        log("render", f"voice hook skipped ({str(e)[:120]})", "warn")
        return None


def validate(mp4, meta, rights_row, cfg):
    """Technical + editorial + rights checks. Returns (ok, list of (name, ok, detail))."""
    from common import ffprobe, tool
    res = []
    p = ffprobe(mp4); st = p.get("streams", [])
    v = next((s for s in st if s.get("codec_type") == "video"), {}); a = next((s for s in st if s.get("codec_type") == "audio"), None)
    dur = float(p["format"].get("duration", 0))
    res.append(("1080x1920 9:16", (v.get("width"), v.get("height")) == (1080, 1920), f"{v.get('width')}x{v.get('height')}"))
    lo = (rights_row or {}).get("min_seconds") or cfg["clip_min"]; hi = (rights_row or {}).get("max_seconds") or cfg["clip_max"]
    res.append((f"length {lo:g}-{hi:g}s", lo - 0.5 <= dur <= hi + 0.5, f"{dur:.1f}s"))
    res.append(("audio track", a is not None, ""))
    dec = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(mp4), "-f", "null", "-"], capture_output=True, text=True)
    res.append(("decodes end to end (playback)", dec.returncode == 0 and not dec.stderr.strip(), dec.stderr.strip()[:120]))
    if a is not None:
        ln = subprocess.run([tool("ffmpeg"), "-hide_banner", "-i", str(mp4), "-af", "ebur128", "-f", "null", "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", ln)
        lufs = float(m[-1]) if m else -99
        res.append(("loudness -18..-10 LUFS", -18 <= lufs <= -10, f"{lufs:.1f} LUFS"))
    bd = subprocess.run([tool("ffmpeg"), "-hide_banner", "-i", str(mp4), "-vf", "blackdetect=d=1.5:pix_th=0.08", "-an", "-f", "null", "-"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    res.append(("no long black/dead sections", "black_start" not in bd, ""))
    res.append(("captions present", meta.get("caption_words", 0) >= 5, f"{meta.get('caption_words', 0)} words"))
    res.append(("original hook + editorial outro", bool(meta.get("hook")) and bool(meta.get("outro")), ""))
    res.append(("creator credit in every caption", all(meta["credit"] in meta[k][f] for k, f in
                                                       (("youtube", "description"), ("tiktok", "caption"), ("instagram", "caption"))), meta["credit"]))
    ok_r = bool(rights_row) and rights_row.get("status") == "cleared"
    res.append(("rights cleared", ok_r, f"{(rights_row or {}).get('basis')} {(rights_row or {}).get('evidence_url') or ''}".strip()))
    from check import banned_in
    hits = banned_in(" ".join([meta["hook"], meta["outro"], meta["tiktok"]["caption"]]))
    res.append(("no banned terms (leaks/piracy/etc.)", not hits, ", ".join(hits)))
    return all(r[1] for r in res), res


def job_from_output(out):
    m = re.search(r"job: (.+)", out)
    return Path(m.group(1).strip()) if m else None


def process(src, creator, source_platform, display=None, max_clips=None, layouts=("react", "blur"), clip_max=None):
    cfg = config.load()
    if clip_max:
        cfg["clip_max"] = clip_max
    ok, rr = rights.is_cleared(creator, source_platform)
    if not ok:
        log("render", f"refused {src}: {creator}@{source_platform} not cleared ({(rr or {}).get('status', 'unknown')})", "warn")
        return []
    budget = (max_clips or cfg["daily_clip_limit"]) - clips_today() if max_clips is None else max_clips
    if budget <= 0:
        log("render", "daily clip limit reached"); return []
    code, out = _run([SCRIPTS / "moments.py", src, "--fresh", "--top", 8, "--min", cfg["clip_min"], "--max", cfg["clip_max"]], timeout=7200)
    job = job_from_output(out)
    if code != 0 or not job:
        log("render", f"moments failed for {src}: {out[-400:]}", "error"); return []
    cands = json.loads((job / "candidates.json").read_text(encoding="utf-8"))
    words = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
    info = cands.get("info", {}); src_url = info.get("webpage_url") if str(info.get("webpage_url", "")).startswith("http") else ""
    display = display or info.get("channel") or info.get("uploader") or creator
    made = []
    wts = cfg.get("category_weights", {})  # learned from real views (analytics.learn)
    for cand in sorted(cands["candidates"], key=lambda c: -c["score"] * wts.get(content.classify(c["text"])[0], 1.0)):
        if len(made) >= budget:
            break
        if one("SELECT id FROM clips WHERE source_url=? AND ABS(start-?)<5", src_url or str(src), cand["start"]):
            continue  # duplicate moment
        seed = f"{src}:{cand['start']}"
        pkg = content.write(cand["text"], display, src_url, rr, cfg["brand"], seed)
        day = time.strftime("%Y-%m-%d")
        d = OUT / day / re.sub(r"[^\w-]+", "_", rights.norm(creator)) / pkg["category"]
        d.mkdir(parents=True, exist_ok=True)
        stem = f"clip_{int(time.time())}_{cand['n']}"
        mp4 = d / f"{stem}.mp4"
        voice = voice_hook(pkg["hook"], d / f"{stem}_hook.mp3", cfg["voice"]) if cfg.get("voice_hook") else None
        done = False
        for lay in layouts:
            args = [SCRIPTS / "cut.py", job, "--pick", cand["n"], "--layout", lay, "--hook", pkg["hook"],
                    "--outro", pkg["outro"], "--out", mp4]
            if lay == "react":
                args += ["--title", pkg["hook"], "--title-secs", 3]
            if voice:
                args += ["--voice", voice]
            code, cout = _run(args)
            if code == 0 and mp4.exists():
                done = True; pkg["layout"] = lay; break
            log("render", f"layout {lay} not used for #{cand['n']}: {cout.strip().splitlines()[-1][:160] if cout.strip() else code}")
        if not done:
            continue
        pkg.update({"source": src_url or str(src), "creator": rights.norm(creator), "start": cand["start"], "end": cand["end"],
                    "score": cand["score"], "why": cand["why"], "voice_hook": bool(voice),
                    "caption_words": len([w for w in words if cand["start"] <= w["s"] < cand["end"]])})
        okv, checks = validate(mp4, pkg, rr, cfg)
        digest = sha256(mp4)
        if one("SELECT id FROM clips WHERE sha256=?", digest):
            continue
        pkg["checks"] = [{"check": n, "pass": p, "detail": dt} for n, p, dt in checks]
        mp4.with_suffix(".json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
        for plat, key in (("youtube", "description"), ("tiktok", "caption"), ("instagram", "caption")):
            extra = f"TITLE: {pkg['youtube']['title']}\n\n" if plat == "youtube" else ""
            (d / f"{stem}.{plat}.txt").write_text(extra + pkg[plat][key], encoding="utf-8")
        status = "awaiting_review" if okv else "rejected"
        cid = execute("INSERT INTO clips (source_url, creator, rights_id, start, end, category, score, hook, outro, path, sha256, meta, checks, status, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pkg["source"], pkg["creator"], rr["id"], cand["start"], cand["end"],
                      pkg["category"], cand["score"], pkg["hook"], pkg["outro"], str(mp4), digest, j(pkg), j(pkg["checks"]), status, time.time())
        failed = [c["check"] for c in pkg["checks"] if not c["pass"]]
        log("render", f"clip {cid} {status}: {mp4.relative_to(ROOT)}" + (f" failed: {failed}" if failed else ""),
            "info" if okv else "warn")
        if okv:
            made.append(cid)
    return made
