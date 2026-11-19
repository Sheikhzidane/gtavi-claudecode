"""SQLite store: rights, discoveries, clips, posts, approvals, metrics, events.

One file (data/ops.db). Every state change goes through here so the scheduler can crash and resume.
"""
import json, sqlite3, time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "ops.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rights (
  id INTEGER PRIMARY KEY,
  creator TEXT NOT NULL,              -- channel/login, lower-case
  source_platform TEXT NOT NULL,      -- twitch|youtube|kick|own
  status TEXT NOT NULL DEFAULT 'candidate', -- candidate|cleared|denied|revoked|expired
  basis TEXT,                         -- campaign|written_permission|license|own_footage
  evidence_url TEXT, evidence_note TEXT,
  campaign TEXT, rate_per_1k REAL,
  allowed_platforms TEXT DEFAULT '["youtube","tiktok","instagram"]',
  required_tags TEXT DEFAULT '[]', required_mention TEXT, ad_label INTEGER DEFAULT 0,
  min_seconds REAL, max_seconds REAL, source_rule TEXT,
  expires_at TEXT, cleared_by TEXT, cleared_at TEXT, updated_at REAL,
  UNIQUE(creator, source_platform)
);
CREATE TABLE IF NOT EXISTS discoveries (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, platform TEXT, creator TEXT, title TEXT,
  metric INTEGER, metric_kind TEXT, live INTEGER, seen_at REAL, rights_status TEXT, processed_at REAL
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, source_url TEXT, creator TEXT, rights_id INTEGER,
  start REAL, end REAL, category TEXT, score REAL, hook TEXT, outro TEXT,
  path TEXT, sha256 TEXT UNIQUE, meta TEXT, checks TEXT,
  status TEXT DEFAULT 'rendered',     -- rendered|checked|rejected|awaiting_review|approved|done
  created_at REAL, reviewed_at REAL, UNIQUE(source_url, start)
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY, clip_id INTEGER, platform TEXT, account TEXT,
  privacy TEXT, status TEXT,          -- queued|uploading|processing|published|private_test|manual_needed|failed
  remote_id TEXT, url TEXT, verified INTEGER DEFAULT 0, error TEXT, attempts INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL, UNIQUE(clip_id, platform, privacy)
);
CREATE TABLE IF NOT EXISTS approvals (
  platform TEXT PRIMARY KEY, autopilot INTEGER, approved_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
  post_id INTEGER, ts REAL, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
  watch_seconds REAL, avg_view_pct REAL, raw TEXT
);
CREATE TABLE IF NOT EXISTS events (ts REAL, level TEXT, kind TEXT, msg TEXT);
"""


@contextmanager
def conn():
    DATA.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for col in ("method TEXT DEFAULT 'api'",):  # migrations for older DBs
        try:
            c.execute(f"ALTER TABLE posts ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        yield c
        c.commit()
    finally:
        c.close()


def log(kind, msg, level="info"):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {kind}: {msg}"
    print(line)
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    with open(DATA / "logs" / f"{time.strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    with conn() as c:
        c.execute("INSERT INTO events VALUES (?,?,?,?)", (time.time(), level, kind, msg))


def rows(sql, *args):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, *args):
    r = rows(sql, *args)
    return r[0] if r else None


def execute(sql, *args):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def j(v):
    return json.dumps(v, ensure_ascii=False)
