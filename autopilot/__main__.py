"""GTA VI clip operation control.

python -m autopilot status                         accounts, mode, stop switch, queue, rights
python -m autopilot tick                           one full cycle (what the scheduler runs)
python -m autopilot discover                       live streams -> rights candidates
python -m autopilot rights list | clear | deny | revoke ...
python -m autopilot render <url-or-file> --creator X --source-platform twitch|youtube|own
python -m autopilot review list | approve <id...> | reject <id...>
python -m autopilot set-app tiktok|instagram           type developer-app credentials into hidden prompts (OS keyring)
python -m autopilot connect|revoke youtube|tiktok|instagram ; accounts
python -m autopilot test-upload <platform> <clip_id>   non-public test (private / SELF_ONLY / unpublished container)
python -m autopilot autopilot enable <platforms...> --i-approve-public-autopilot | disable
python -m autopilot stop [reason] | resume          emergency stop switch
python -m autopilot analytics | report
python -m autopilot schedule install [--minutes 60] | remove | show
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path
from .db import ROOT, DATA, rows, one, execute, log
from . import config, rights, tokens

LOCK = DATA / "tick.lock"
INPUT = ROOT / "input"  # drop folder: input/<creator>/<file>.mp4 (creator must be cleared; input/own/ = your footage)
TASK = "GTAVI Autopilot"


def cmd_status(a):
    cfg = config.load()
    print(f"mode: {cfg['mode']}   emergency stop: {'ON (' + config.STOP.read_text() + ')' if config.stopped() else 'off'}   paused: {cfg.get('paused', [])}")
    print("autopilot approvals:", rows("SELECT * FROM approvals") or "none")
    cmd_accounts(a, quiet=True)
    print("\nclip queue:", {r["status"]: r["n"] for r in rows("SELECT status, COUNT(*) n FROM clips GROUP BY status")})
    print("posts:", {f"{r['platform']}/{r['privacy']}/{r['status']}": r["n"] for r in rows("SELECT platform, privacy, status, COUNT(*) n FROM posts GROUP BY 1,2,3")})
    cl = rows("SELECT creator, source_platform, basis, campaign FROM rights WHERE status='cleared'")
    print(f"cleared creators: {cl or 'none'}   candidates: {one('SELECT COUNT(*) n FROM rights WHERE status=?', 'candidate')['n']}")


def cmd_accounts(a, quiet=False):
    from .publish import pub
    for p in ("youtube", "tiktok", "instagram"):
        app = "app creds OK" if tokens.app(p) else f"no app creds (data/secrets/{p}.json)"
        if not tokens.get(p):
            print(f"{p:10} NOT connected  [{app}]"); continue
        try:
            print(f"{p:10} connected as {pub(p).whoami()}  [{app}]")
        except Exception as e:
            print(f"{p:10} token stored but check failed: {tokens.redact(e)}")


def cmd_connect(a):
    from .publish import pub
    print(pub(a.platform).connect()); log("accounts", f"{a.platform} connected")


def cmd_revoke(a):
    from .publish import pub
    pub(a.platform).revoke(); log("accounts", f"{a.platform} revoked and token deleted"); print("revoked")


def cmd_rights(a):
    if a.action == "list":
        for r in rights.table():
            print(f"{r['status']:10} {r['source_platform']:8} {r['creator']:28} {r['basis'] or '':18} {r['campaign'] or '':28} {r['evidence_url'] or ''}")
    elif a.action == "clear":
        rights.clear(a.creator, a.source_platform, a.basis, a.evidence, a.by, campaign=a.campaign, rate_per_1k=a.rate,
                     required_mention=a.mention, ad_label=int(a.ad_label), min_seconds=a.min_seconds, max_seconds=a.max_seconds,
                     source_rule=a.source_rule, expires_at=a.expires, allowed_platforms=a.platforms.split(","),
                     required_tags=a.tags.split() if a.tags else [])
        print("cleared")
    else:
        rights.set_status(a.creator, a.source_platform, {"deny": "denied", "revoke": "revoked"}[a.action], a.note or "")
        if a.action == "revoke":  # pull queued clips of that creator out of the queue
            execute("UPDATE clips SET status='rejected' WHERE creator=? AND status IN ('awaiting_review','approved')", rights.norm(a.creator))


def cmd_render(a):
    from . import render
    print(render.process(a.src, a.creator, a.source_platform, display=a.display, max_clips=a.max, clip_max=a.clip_max))


def cmd_review(a):
    if a.action == "list":
        for c in rows("SELECT id, status, creator, category, hook, end-start dur, path FROM clips ORDER BY id DESC LIMIT 30"):
            print(f"#{c['id']:<4} {c['status']:16} {c['creator']:16} {c['category']:13} {c['dur']:5.1f}s  {c['hook']}\n       {c['path']}")
        return
    st = "approved" if a.action == "approve" else "rejected"
    for i in a.ids:
        execute("UPDATE clips SET status=?, reviewed_at=? WHERE id=? AND status IN ('awaiting_review','approved','rejected')", st, time.time(), i)
        log("review", f"clip {i} {st}")


def cmd_test_upload(a):
    from .publish import publish_clip
    c = one("SELECT * FROM clips WHERE id=?", a.clip_id)
    print(publish_clip(c, a.platform, "private", config.load(), test=True))
    print(one("SELECT platform, status, remote_id, url, verified, error FROM posts WHERE clip_id=? AND platform=? AND privacy='private'", a.clip_id, a.platform))


def cmd_autopilot(a):
    cfg = config.load()
    if a.action == "disable":
        cfg["mode"] = "review"; config.save(cfg); execute("UPDATE approvals SET autopilot=0"); log("autopilot", "disabled -> review mode"); return
    if not a.i_approve_public_autopilot:
        sys.exit("Refused: pass --i-approve-public-autopilot to confirm you authorise public automatic posting.")
    for p in a.platforms:
        test = one("SELECT id FROM posts WHERE platform=? AND verified=1 AND COALESCE(method,'api')='api'", p)
        if not test:
            sys.exit(f"Refused for {p}: no verified API test upload yet (browser tests don't count). Run test-upload first.")
        execute("INSERT INTO approvals VALUES (?,?,?,?) ON CONFLICT(platform) DO UPDATE SET autopilot=1, approved_at=excluded.approved_at",
                p, 1, time.strftime("%Y-%m-%d %H:%M"), "owner approved via CLI")
    cfg["mode"] = "autopilot"; config.save(cfg); log("autopilot", f"ENABLED for {a.platforms}")


def _ingest_drop_folder():
    from . import render
    made = []
    for d in sorted(INPUT.glob("*")) if INPUT.exists() else []:
        if not d.is_dir():
            continue
        plat = "own" if d.name == "own" else "drop"
        for f in sorted(d.glob("*.mp4")) + sorted(d.glob("*.mkv")):
            done = f.with_suffix(f.suffix + ".done")
            if done.exists():
                continue
            if not rights.is_cleared(d.name, plat)[0]:
                log("tick", f"drop folder {d.name}: not cleared, skipped {f.name}", "warn"); continue
            made += render.process(str(f), d.name, plat); done.write_text(time.strftime("%Y-%m-%d %H:%M"))
    return made


def _live_capture(disc, minutes):
    """Record a cleared creator's live stream, cut on chat spikes, then render the spike clips."""
    from . import render
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "live.py"), disc["url"], "--minutes", str(minutes)], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=minutes * 60 + 900,
                       env={**os.environ, "PYTHONUTF8": "1", "CLIP_DESK_HOME": str(ROOT)})
    made = []
    for line in r.stdout.splitlines():
        if line.startswith("recording ") and "->" in line:
            ld = Path(line.split("->")[1].split("(")[0].strip())
            for spike in sorted(ld.glob("spike_*.mp4")):
                made += render.process(str(spike), disc["creator"], disc["platform"], max_clips=1)
    return made


def cmd_browser_record(a):
    """Record the verified outcome of a website post made in a Claude-in-Chrome session."""
    p = one("SELECT * FROM posts WHERE id=?", a.post_id)
    if a.status == "published" and not a.url:
        sys.exit("a published post needs its verified --url")
    execute("UPDATE posts SET status=?, url=?, verified=?, method='browser', error=?, updated_at=? WHERE id=?",
            a.status, a.url or None, int(a.status != "failed"), a.note or None, time.time(), a.post_id)
    log("publish", f"{p['platform']} clip {p['clip_id']} -> {a.status} via BROWSER {a.url} {a.note}")


def cmd_tick(a):
    if config.stopped():
        log("tick", "emergency stop ON: skipping cycle"); return
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 6 * 3600:
        log("tick", "previous cycle still running, skipping"); return
    LOCK.write_text(str(os.getpid()))
    try:
        from . import discover, render, publish, analytics
        cfg = config.load()
        _ingest_drop_folder()
        _, cleared = discover.run()
        for d in cleared[:2]:
            if render.clips_today() >= cfg["daily_clip_limit"] or config.stopped():
                break
            execute("UPDATE discoveries SET processed_at=? WHERE url=?", time.time(), d["url"])
            _live_capture(d, cfg.get("live_minutes", 30))
        for r in publish.due(cfg):
            log("tick", f"publish result {r}")
        analytics.fetch(); analytics.learn()
        print(analytics.report())
    except Exception as e:
        log("tick", f"cycle error: {tokens.redact(e)}", "error"); raise
    finally:
        LOCK.unlink(missing_ok=True)


def cmd_schedule(a):
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if a.action == "install":
        runner = DATA / "run_tick.cmd"
        runner.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nset PYTHONUTF8=1\r\n"{py}" -m autopilot tick >> "{DATA / "logs" / "scheduler.log"}" 2>&1\r\n', newline="")
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        # XML definition: runs on battery, catches up missed runs after sleep/boot, never overlaps itself,
        # 6 h hard time limit. A PC that is switched off does not run tasks; see OPERATIONS.md for hosting.
        start = time.strftime("%Y-%m-%dT%H:%M:00", time.localtime(time.time() + 120))
        xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>GTA VI clip operation: discover, render, check, publish, analytics</Description></RegistrationInfo>
  <Triggers><TimeTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
    <Repetition><Interval>PT{a.minutes}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit><Enabled>true</Enabled>
    <RestartOnFailure><Interval>PT10M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>{runner}</Command><WorkingDirectory>{ROOT}</WorkingDirectory></Exec></Actions>
</Task>"""
        xp = DATA / "task.xml"
        xp.write_text(xml, encoding="utf-16")
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK, "/XML", str(xp)], capture_output=True, text=True)
        print(r.stdout or r.stderr)
    elif a.action == "remove":
        print(subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True, text=True).stdout)
    print(subprocess.run(["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"], capture_output=True, text=True).stdout or "no scheduled task")


def main():
    ap = argparse.ArgumentParser(prog="autopilot"); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status").set_defaults(f=cmd_status)
    sp.add_parser("accounts").set_defaults(f=cmd_accounts)
    sp.add_parser("tick").set_defaults(f=cmd_tick)
    sp.add_parser("discover").set_defaults(f=lambda a: [print(r) for r in __import__("autopilot.discover", fromlist=["x"]).run() and
                                                        __import__("autopilot.discover", fromlist=["x"]).top()])
    for name, f in (("connect", cmd_connect), ("revoke", cmd_revoke)):
        p = sp.add_parser(name); p.add_argument("platform", choices=["youtube", "tiktok", "instagram"]); p.set_defaults(f=f)
    sp.add_parser("browser-queue").set_defaults(f=lambda a: print(json.dumps(__import__("autopilot.publish", fromlist=["x"]).browser_queue(), indent=1, ensure_ascii=False)))
    p = sp.add_parser("browser-record"); p.add_argument("post_id", type=int); p.add_argument("status", choices=["published", "private_test", "failed"])
    p.add_argument("--url", default=""); p.add_argument("--note", default="")
    p.set_defaults(f=cmd_browser_record)
    p = sp.add_parser("set-app"); p.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    p.set_defaults(f=lambda a: print(tokens.set_app(a.platform)))
    p = sp.add_parser("rights"); p.add_argument("action", choices=["list", "clear", "deny", "revoke"])
    p.add_argument("creator", nargs="?"); p.add_argument("source_platform", nargs="?")
    p.add_argument("--basis"); p.add_argument("--evidence", default=""); p.add_argument("--by", default="owner")
    p.add_argument("--campaign"); p.add_argument("--rate", type=float); p.add_argument("--mention"); p.add_argument("--tags", default="")
    p.add_argument("--ad-label", action="store_true"); p.add_argument("--min-seconds", type=float); p.add_argument("--max-seconds", type=float)
    p.add_argument("--source-rule"); p.add_argument("--expires"); p.add_argument("--platforms", default="youtube,tiktok,instagram")
    p.add_argument("--note"); p.set_defaults(f=cmd_rights)
    p = sp.add_parser("render"); p.add_argument("src"); p.add_argument("--creator", required=True)
    p.add_argument("--source-platform", required=True); p.add_argument("--display"); p.add_argument("--max", type=int); p.add_argument("--clip-max", type=float); p.set_defaults(f=cmd_render)
    p = sp.add_parser("review"); p.add_argument("action", choices=["list", "approve", "reject"]); p.add_argument("ids", nargs="*", type=int); p.set_defaults(f=cmd_review)
    p = sp.add_parser("test-upload"); p.add_argument("platform", choices=["youtube", "tiktok", "instagram"]); p.add_argument("clip_id", type=int); p.set_defaults(f=cmd_test_upload)
    p = sp.add_parser("autopilot"); p.add_argument("action", choices=["enable", "disable"]); p.add_argument("platforms", nargs="*")
    p.add_argument("--i-approve-public-autopilot", action="store_true"); p.set_defaults(f=cmd_autopilot)
    p = sp.add_parser("stop"); p.add_argument("reason", nargs="?", default="manual"); p.set_defaults(f=lambda a: (config.stop(a.reason), log("safety", f"EMERGENCY STOP: {a.reason}", "warn")))
    sp.add_parser("resume").set_defaults(f=lambda a: (config.resume(), log("safety", "emergency stop cleared")))
    sp.add_parser("analytics").set_defaults(f=lambda a: __import__("autopilot.analytics", fromlist=["x"]).fetch())
    sp.add_parser("report").set_defaults(f=lambda a: print(__import__("autopilot.analytics", fromlist=["x"]).report()))
    p = sp.add_parser("schedule"); p.add_argument("action", choices=["install", "remove", "show"]); p.add_argument("--minutes", type=int, default=60)
    p.set_defaults(f=cmd_schedule)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
