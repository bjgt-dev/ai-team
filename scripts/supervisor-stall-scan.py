#!/usr/bin/env python3
"""Supervisor stall scan (2026-11-06, live PG scan).

Real scan of central_tasks (read-only SELECT, DSN from state/.cq-dsn only,
same pattern as scripts/health_3q-20261104.py, psycopg):
  PENDING (READY)      age >30min yellow, >60min red
  DOING (CLAIMED/RUNNING) age >30min yellow, >60min red (age = now - updated_at)
  VERIFYING            age >900s red
  RETRY_WAIT           single stall (updated_at >15min) red;
                       cumulative (created_at >2h) incident
Yellow = log only. Red/incident = one aggregated TG push per run via
scripts/alert_push.sh key "stall-scan-red" (alert_push dedups 60min/key).
Exit: 0 = no yellow/red, 1 = yellow only, 2 = red/incident present.
--dry-run: prints thresholds + DB counts, no TG, no report write.
Live run writes state/supervisor-stall-scan.json (machine report) and
appends to state/supervisor-stall-scan.log.
Usage: python3 scripts/supervisor-stall-scan.py [--dry-run]
Cron: * * * * * /home/ubuntu/ai-team/scripts/supervisor-stall-scan.py >> /home/ubuntu/ai-team/state/supervisor-stall-scan.log 2>&1
"""
import json, os, subprocess, sys, time
BASE = "/home/ubuntu/ai-team"
DSN_FILE = os.path.join(BASE, "state/.cq-dsn")
REPORT_FILE = os.path.join(BASE, "state/supervisor-stall-scan.json")
LOG_FILE = os.path.join(BASE, "state/supervisor-stall-scan.log")
ALERT_PUSH = os.path.join(BASE, "scripts/alert_push.sh")

# thresholds (seconds)
YELLOW_S = 30 * 60
RED_S = 60 * 60
VERIFYING_RED_S = 900
RETRY_SINGLE_RED_S = 15 * 60
RETRY_CUMULATIVE_S = 2 * 3600


def dsn():
    for line in open(DSN_FILE):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def q(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def main():
    dry = "--dry-run" in sys.argv
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print("supervisor-stall-scan ts=%s DRY_RUN=%s thresholds: PENDING/DOING 30m yellow/60m red, VERIFYING 900s red, RETRY_WAIT 15m/2h" % (ts, dry))
    if not os.path.exists(DSN_FILE):
        print("STALL_SCAN_FAIL: no DSN file, fail closed")
        return 3
    print("DSN file exists=True")
    import psycopg
    try:
        conn = psycopg.connect(dsn())
    except Exception as e:
        print("STALL_SCAN_FAIL: db connect failed: %s" % str(e)[:200])
        return 3
    buckets = {}
    try:
        # READY = PENDING, CLAIMED/RUNNING = DOING; age measured on updated_at.
        rows = q(conn, "SELECT task_id::text, title, status, "
                        "EXTRACT(EPOCH FROM (now() - updated_at)) AS age_s, "
                        "EXTRACT(EPOCH FROM (now() - created_at)) AS cage_s "
                        "FROM central_tasks WHERE status IN ('READY','CLAIMED','RUNNING','VERIFYING','RETRY_WAIT') "
                        "ORDER BY updated_at ASC")
    except Exception as e:
        print("STALL_SCAN_FAIL: db query failed: %s" % str(e)[:200])
        return 3
    finally:
        conn.close()

    def bucket(status, age_s, cage_s):
        if status == "READY":
            if age_s > RED_S:
                return "red"
            if age_s > YELLOW_S:
                return "yellow"
            return "ok"
        if status in ("CLAIMED", "RUNNING"):
            if age_s > RED_S:
                return "red"
            if age_s > YELLOW_S:
                return "yellow"
            return "ok"
        if status == "VERIFYING":
            return "red" if age_s > VERIFYING_RED_S else "ok"
        if status == "RETRY_WAIT":
            if cage_s > RETRY_CUMULATIVE_S:
                return "incident"
            if age_s > RETRY_SINGLE_RED_S:
                return "red"
            return "ok"
        return "ok"

    for tid, title, status, age_s, cage_s in rows:
        b = bucket(status, age_s or 0, cage_s or 0)
        key = "%s_%s" % (status, b)
        buckets.setdefault(key, {"count": 0, "oldest": []})
        buckets[key]["count"] += 1
        if len(buckets[key]["oldest"]) < 5:
            buckets[key]["oldest"].append({"task_id": tid[:8], "title": (title or "")[:40],
                                           "age_min": round(float(age_s or 0) / 60, 1)})
    counts = {k: v["count"] for k, v in sorted(buckets.items())}
    n_yellow = sum(v["count"] for k, v in buckets.items() if k.endswith("_yellow"))
    n_red = sum(v["count"] for k, v in buckets.items() if k.endswith("_red"))
    n_incident = sum(v["count"] for k, v in buckets.items() if k.endswith("_incident"))
    print("STALL_SCAN buckets=%s yellow=%d red=%d incident=%d" % (json.dumps(counts), n_yellow, n_red, n_incident))
    for k in sorted(buckets):
        if k.endswith(("_yellow", "_red", "_incident")):
            print("  %s: %s" % (k, json.dumps(buckets[k]["oldest"], ensure_ascii=False)))
    if dry:
        print("STALL_SCAN_DRY_RUN_OK: live query ran, no TG sent, no report written")
        return 0 if (n_red == 0 and n_incident == 0) else 2
    report = {"ts": ts, "buckets": buckets, "yellow": n_yellow, "red": n_red, "incident": n_incident}
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("%s STALL_SCAN yellow=%d red=%d incident=%d buckets=%s\n" % (ts, n_yellow, n_red, n_incident, json.dumps(counts)))
    if n_red > 0 or n_incident > 0:
        msg = ("supervisor stall scan %s: red=%d incident=%d buckets=%s" % (ts, n_red, n_incident, json.dumps(counts)))
        r = subprocess.run([ALERT_PUSH, "stall-scan-red", msg], capture_output=True, text=True, timeout=60)
        print("TG_PUSH stall-scan-red exit=%s out=%s" % (r.returncode, (r.stdout or "").strip()[:200]))
    else:
        print("STALL_SCAN_OK: no red/incident, no TG sent")
    if n_incident > 0 or n_red > 0:
        return 2
    if n_yellow > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
