#!/usr/bin/env python3
"""hub-status.py — read-only status board (Thorsten Ball Rule 4/5, 2026-11-07).
One command shows the full pipeline: queue depth, gateway health, last 5
DONE tasks, and (with --task) this task's contract + verdict.
READ-ONLY: only SELECTs and file reads; never writes production state.
Usage: python3 scripts/hub-status.py [--task <task_id>]"""
import argparse, glob, json, os, sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
VERDICT_DIR = BASE + "/state/butler-verdicts"
RESULTS_DIR = BASE + "/state/c2-task-results"

def get_dsn():
    try:
        with open(DSN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

def gateway_health():
    """(process alive?, latest gateway_canary result). Best-effort."""
    alive = False
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(os.path.join("/proc", pid, "cmdline"), "rb") as f:
                    if b"openclaw-gateway" in f.read():
                        alive = True
                        break
            except Exception:
                continue
    except Exception:
        pass
    canary = "unknown (no DONE gateway_canary row)"
    dsn = get_dsn()
    if dsn:
        try:
            import psycopg
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT task_id, updated_at FROM central_tasks WHERE title='timer-gateway_canary' AND status='DONE' ORDER BY updated_at DESC LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        tid, ts = row
                        cands = glob.glob(os.path.join(RESULTS_DIR, "%s*" % tid))
                        if cands:
                            txt = open(cands[0], encoding="utf-8", errors="ignore").read()
                            ok = '"pass3": true' in txt
                            canary = "PASS 3/3" if ok else "FAIL (see %s)" % cands[0]
                            canary += " at %s" % ts
                        else:
                            canary = "DONE %s but no result file" % ts
        except Exception as e:
            canary = "unknown (canary query failed: %s)" % e
    return alive, canary

def main():
    ap = argparse.ArgumentParser(description="Read-only hub status board")
    ap.add_argument("--task", help="task_id to show contract + verdict for")
    args = ap.parse_args()
    print("=== hub status (read-only) ===")
    dsn = get_dsn()
    if not dsn:
        print("queue: NO DSN (fail closed, cannot read)")
    else:
        try:
            import psycopg
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT status, COUNT(*) FROM central_tasks GROUP BY status ORDER BY status")
                    print("queue depth:")
                    total = 0
                    for st, n in cur.fetchall():
                        print("  %-12s %d" % (st, n))
                        total += n
                    print("  %-12s %d" % ("TOTAL", total))
                    cur.execute("SELECT title, payload->>'task_id', updated_at FROM central_tasks WHERE status='DONE' ORDER BY updated_at DESC LIMIT 5")
                    print("last 5 DONE:")
                    for title, tid, ts in cur.fetchall():
                        print("  %s  %s" % (tid or title, ts))
                    if args.task:
                        cur.execute("SELECT status, payload FROM central_tasks WHERE payload->>'task_id' = %s ORDER BY updated_at DESC LIMIT 1", (args.task,))
                        row = cur.fetchone()
                        if not row:
                            print("task %s: not found in central_tasks" % args.task)
                        else:
                            st, payload = row
                            print("task %s: status=%s" % (args.task, st))
                            c = payload.get("contract") if isinstance(payload, dict) else None
                            if isinstance(c, dict):
                                print("  contract: task_type=%s workdir=%s" % (c.get("task_type"), c.get("workdir")))
                                fp = c.get("five_part") or {}
                                print("  five_part: %s" % (", ".join(sorted(fp.keys())) if isinstance(fp, dict) else "missing"))
                            else:
                                print("  contract: none (non-contract task)")
        except Exception as e:
            print("queue: query failed: %s" % e)
    alive, canary = gateway_health()
    print("gateway: process alive=%s; canary=%s" % (alive, canary))
    if args.task:
        # verdict lookup by task slug
        found = None
        try:
            for name in sorted(os.listdir(VERDICT_DIR)):
                if args.task.split("-p0-")[0] in name and name.endswith(".json"):
                    found = os.path.join(VERDICT_DIR, name)
                    break
        except Exception:
            pass
        if found:
            try:
                v = json.load(open(found, encoding="utf-8"))
                print("verdict: %s (%s) conditions=%d" % (v.get("verdict"), found, len(v.get("conditions") or [])))
            except Exception as e:
                print("verdict: unreadable %s (%s)" % (found, e))
        else:
            print("verdict: none found for %s" % args.task)
    return 0

if __name__ == "__main__":
    sys.exit(main())
