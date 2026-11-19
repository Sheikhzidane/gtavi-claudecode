"""Rights and permissions database.

Rule: nothing is rendered for publishing or published unless the creator has a `cleared` row with a basis
and evidence. Public footage or a credit line is NOT permission. Discovery only creates `candidate` rows.
"""
import json, time
from datetime import date
from .db import execute, one, rows, j, log

BASES = {"campaign", "written_permission", "license", "own_footage"}


def norm(creator):
    return (creator or "").strip().lstrip("@").lower()


def ensure_candidate(creator, source_platform, note=""):
    c = norm(creator)
    if not one("SELECT id FROM rights WHERE creator=? AND source_platform=?", c, source_platform):
        execute("INSERT INTO rights (creator, source_platform, status, evidence_note, updated_at) VALUES (?,?,?,?,?)",
                c, source_platform, "candidate", note, time.time())


def clear(creator, source_platform, basis, evidence_url, cleared_by, **kw):
    if basis not in BASES:
        raise SystemExit(f"basis must be one of {sorted(BASES)}")
    if basis != "own_footage" and not evidence_url:
        raise SystemExit("evidence_url is required (campaign page, signed permission, licence)")
    c = norm(creator)
    ensure_candidate(c, source_platform)
    fields = {"status": "cleared", "basis": basis, "evidence_url": evidence_url, "cleared_by": cleared_by,
              "cleared_at": time.strftime("%Y-%m-%d %H:%M"), "updated_at": time.time()}
    for k in ("campaign", "rate_per_1k", "required_mention", "ad_label", "min_seconds", "max_seconds",
              "source_rule", "expires_at", "evidence_note"):
        if kw.get(k) is not None:
            fields[k] = kw[k]
    for k in ("allowed_platforms", "required_tags"):
        if kw.get(k) is not None:
            fields[k] = j(kw[k])
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE rights SET {sets} WHERE creator=? AND source_platform=?", *fields.values(), c, source_platform)
    log("rights", f"cleared {c}@{source_platform} basis={basis} by={cleared_by} evidence={evidence_url}")


def set_status(creator, source_platform, status, note=""):
    execute("UPDATE rights SET status=?, evidence_note=?, updated_at=? WHERE creator=? AND source_platform=?",
            status, note, time.time(), norm(creator), source_platform)
    log("rights", f"{norm(creator)}@{source_platform} -> {status} {note}", "warn" if status in ("revoked", "denied") else "info")


def lookup(creator, source_platform):
    r = one("SELECT * FROM rights WHERE creator=? AND source_platform=?", norm(creator), source_platform)
    if r and r["status"] == "cleared" and r.get("expires_at") and r["expires_at"] < date.today().isoformat():
        set_status(creator, source_platform, "expired", f"expired {r['expires_at']}")
        r = one("SELECT * FROM rights WHERE creator=? AND source_platform=?", norm(creator), source_platform)
    if r:
        r["allowed_platforms"] = json.loads(r.get("allowed_platforms") or "[]")
        r["required_tags"] = json.loads(r.get("required_tags") or "[]")
    return r


def is_cleared(creator, source_platform, target_platform=None):
    r = lookup(creator, source_platform)
    if not r or r["status"] != "cleared":
        return False, r
    if target_platform and target_platform not in r["allowed_platforms"]:
        return False, r
    return True, r


def table():
    return rows("SELECT creator, source_platform, status, basis, campaign, rate_per_1k, evidence_url, cleared_at FROM rights "
                "ORDER BY status='cleared' DESC, creator")


def lead(creator, source_platform, campaign, evidence_url, rate=None, note=""):
    """Record a clipping campaign as a lead. Stays `candidate` until the owner joins and clears it."""
    ensure_candidate(creator, source_platform)
    execute("UPDATE rights SET campaign=?, evidence_url=?, rate_per_1k=?, evidence_note=?, updated_at=? "
            "WHERE creator=? AND source_platform=? AND status!='cleared'",
            campaign, evidence_url, rate, note, time.time(), norm(creator), source_platform)
