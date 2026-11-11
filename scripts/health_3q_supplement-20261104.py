#!/usr/bin/env python3
"""Order14 health_3q supplement (additive, never changes health_3q exit code).
Metrics (all read-only, no secrets printed):
- gateway token synthetic call: GET /v1/models with gateway token read from
  opencode.json internally; report only ok/count, never the token.
- PG connection ratio: max_connections vs pg_stat_activity count.
- container restart increment: total RestartCount now minus baseline file;
  first run sets baseline, increment 0, and does NOT fail on first run.
- inode usage for / and /data.
- last successful backup age <26h: latest date dir in backups-repo/backups,
  age measured from that date 00:00 UTC to now (coarse, reported as hours).
- queue oldest READY age (hours, null if none).
- decision-queue oldest pending age: pending list has no timestamps in the
  current file format, so report pending count and age=null (honest, not
  fabricated). Threshold: chairman-needing pending==0 passes (same
  four-category-gate rule as health_3q Q3, 2026-11-07; raw pending count
  is still reported alongside).
- lease reclaim rate: leases with status != ACTIVE divided by total leases.
Exit 0 if all thresholds pass, 1 otherwise. --dry-run prints without
writing the restart baseline file.
Thresholds: pg_ratio <80%, inode <85%, backup_age_h <26, ready_age_h <24
or none, pending==0, lease_rate >=99%, restart_increment <=2.
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timezone
BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
BASELINE = os.path.join(STATE, "health-supplement-restart-baseline.json")

def chairman_pending_count(pend):
    """Gate-aware pending count (same rule as health_3q Q3, 2026-11-07):
    only pending items that need the chairman count. Items tagged
    needs_chairman=False are self-decidable and do not count; legacy
    items without the tag count (fail closed)."""
    return len([e for e in (pend or [])
                if isinstance(e, dict) and e.get("needs_chairman", True) is not False])

def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except Exception as e:
        return "", str(e)[:200], 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    m = {}
    # gateway token synthetic call
    try:
        cfg = json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
        tok = ""
        for p in (cfg.get("provider") or {}).values():
            opt = p.get("options") or {}
            tok = opt.get("apiKey") or p.get("apiKey") or ""
            if tok:
                break
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:3001/v1/models",
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode() or "{}")
        models = d.get("data") or []
        m["gateway_token_call"] = {"ok": True, "model_count": len(models)}
    except Exception as e:
        m["gateway_token_call"] = {"ok": False, "error": str(e)[:120]}
    # PG connections
    out, _, _ = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres", "-t", "-A",
                     "-c", "SHOW max_connections;", "-c", "SELECT count(*) FROM pg_stat_activity;"])
    try:
        vals = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
        mx, cur = int(vals[0]), int(vals[1])
        m["pg_connections"] = {"max": mx, "used": cur, "ratio": "%d/%d" % (cur, mx), "ratio_pct": round(cur / mx * 100, 1)}
    except Exception as e:
        m["pg_connections"] = {"error": str(e)[:120]}
    # container restarts
    out, _, _ = run(["docker", "ps", "--format", "{{.Names}}"])
    total, per = 0, {}
    for name in [x.strip() for x in out.splitlines() if x.strip()]:
        o2, _, _ = run(["docker", "inspect", "-f", "{{.RestartCount}}", name])
        try:
            n = int(o2.strip()); per[name] = n; total += n
        except Exception:
            pass
    base = None
    if os.path.exists(BASELINE):
        try:
            base = json.load(open(BASELINE)).get("total_restarts")
        except Exception:
            base = None
    inc = 0 if base is None else max(0, total - base)
    m["container_restarts"] = {"total": total, "baseline": base, "increment": inc, "per_container": per}
    if not a.dry_run:
        json.dump({"total_restarts": total, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                  open(BASELINE, "w"), ensure_ascii=False, indent=2)
    # inode
    out, _, _ = run(["df", "-i", "/", "/data"])
    inode = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            inode[parts[5]] = parts[4]
    m["inode"] = inode
    # backup age
    try:
        dates = sorted([d for d in os.listdir("/home/ubuntu/backups-repo/backups")
                        if len(d) == 10 and d[4] == "-" and d[7] == "-"])
        latest = dates[-1] if dates else None
        if latest:
            dt = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_h = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
        else:
            age_h = None
        m["backup"] = {"latest_date": latest, "days_present": len(dates), "age_h": age_h, "dates": dates}
    except Exception as e:
        m["backup"] = {"error": str(e)[:120]}
    # queue oldest READY + decision queue + leases
    out, _, _ = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres", "-d", "central_queue", "-t", "-A",
                     "-c", "SELECT EXTRACT(EPOCH FROM (now() - MIN(created_at)))/3600 FROM central_tasks WHERE status='READY';",
                     "-c", "SELECT count(*) FROM central_leases;",
                     "-c", "SELECT count(*) FROM central_leases WHERE status != 'ACTIVE';"])
    try:
        # Positional lines (2026-11-06 fix): psql prints one line per query
        # and the READY-age line is EMPTY when no READY rows exist. Filtering
        # blanks shifted positions and raised IndexError exactly when the
        # queue was empty (healthy state).
        lines = [x.strip() for x in out.splitlines()]
        while lines and lines[-1] == "":
            lines.pop()
        ready_age = float(lines[0]) if lines and lines[0] not in ("", "null") else None
        lt, lr = int(lines[1]), int(lines[2])
        m["queue_oldest_ready_h"] = round(ready_age, 1) if ready_age is not None else None
        m["lease_reclaim"] = {"total": lt, "reclaimed": lr, "rate": ("%d/%d" % (lr, lt)) if lt else None, "rate_pct": round(lr / lt * 100, 1) if lt else None}
    except Exception as e:
        m["queue_oldest_ready_h"] = None
        m["lease_reclaim"] = {"error": str(e)[:120]}
    try:
        dq = json.load(open(os.path.join(STATE, "decision-queue.json")))
        pend = dq.get("pending", [])
        m["decision_queue"] = {"pending_count": len(pend),
                               "pending_chairman": chairman_pending_count(pend),
                               "oldest_age_h": None,
                               "note": "pending items carry no timestamp in current file format; age not fabricated; pending_chairman counts only needs_chairman items (same rule as health_3q Q3)"}
    except Exception as e:
        m["decision_queue"] = {"error": str(e)[:120]}
    # thresholds
    checks = {
        "gateway_token_ok": bool(m.get("gateway_token_call", {}).get("ok")),
        "pg_ratio_ok": (m.get("pg_connections", {}).get("ratio_pct", 999) < 80),
        "restart_inc_ok": (m["container_restarts"]["increment"] <= 2),
        "inode_ok": all((int(v.rstrip("%")) < 85) for v in inode.values()) if inode else False,
        "backup_age_ok": (m.get("backup", {}).get("age_h") is not None and m["backup"]["age_h"] < 26),
        "ready_age_ok": (m["queue_oldest_ready_h"] is None or m["queue_oldest_ready_h"] < 24),
        "decision_pending_ok": (m.get("decision_queue", {}).get("pending_chairman", m.get("decision_queue", {}).get("pending_count")) == 0),
        "lease_rate_ok": ((m.get("lease_reclaim", {}).get("rate_pct") or 0) >= 99),
    }
    out_doc = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "metrics": m, "checks": checks,
               "all_pass": all(checks.values()), "dry_run": a.dry_run}
    print(json.dumps(out_doc, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1

if __name__ == "__main__":
    sys.exit(main())
