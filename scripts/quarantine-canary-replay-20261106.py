#!/usr/bin/env python3
"""quarantine-canary-replay-20261106.py — item 3 step C (queue-harden-lease-dlq).

Replays ONE quarantined task as a canary: promotes the oldest QUARANTINED
code/script task (the only types worker-1 can execute via queue_api) back to
READY through pipeline-disposition, then polls up to 20 min for a terminal
verdict (DONE, or RETRY_WAIT/QUARANTINED with a reason). Never batch-promotes:
exactly one task per run, guarded by an flock lockfile.

Usage:
  python3 scripts/quarantine-canary-replay-20261106.py [--dry-run] [--task-id <uuid>]
  --dry-run prints the candidate and exits 0 without promoting.
  --task-id replays one explicit QUARANTINED task instead of the oldest.
Receipt: state/quarantine-replay/replay-<task8>-<ts>.json
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
STATE_DIR = BASE + "/state/quarantine-replay"
DISPOSITION = BASE + "/scripts/pipeline-disposition-20261106.py"
LOCK_PATH = STATE_DIR + "/.replay.lock"
POLL_SECONDS = 20 * 60


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _dsn():
    try:
        for line in open(DSN_FILE):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _pick_candidate(cur, task_id=None):
    if task_id:
        cur.execute(
            "SELECT task_id, title, payload->>'type' AS type, retry_count, created_at "
            "FROM central_tasks WHERE task_id::text=%s AND status='QUARANTINED'", (task_id,))
        return cur.fetchone()
    cur.execute(
        "SELECT task_id, title, payload->>'type' AS type, retry_count, created_at "
        "FROM central_tasks WHERE status='QUARANTINED' "
        "AND payload->>'type' IN ('code','script') "
        "ORDER BY created_at ASC LIMIT 1")
    return cur.fetchone()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--task-id", default=None)
    a = ap.parse_args()
    dsn = _dsn()
    if not dsn:
        print("quarantine-replay: no DSN, refusing")
        return 0
    import psycopg
    os.makedirs(STATE_DIR, exist_ok=True)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            row = _pick_candidate(cur, a.task_id)
    if not row:
        print("quarantine-replay: no QUARANTINED code/script candidate")
        return 0
    tid, title, typ, retry, created = row
    tid = str(tid)
    print("quarantine-replay: candidate %s title=%s type=%s retry=%s created=%s"
          % (tid, title, typ, retry, created))
    if a.dry_run:
        print("quarantine-replay: DRY-RUN, not promoting")
        return 0
    lockf = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("quarantine-replay: another replay is running, refusing")
        return 1
    promoted_at = _ts()
    r = subprocess.run(
        ["python3", DISPOSITION, "--task-id", tid,
         "--action", "promote-quarantined",
         "--reason", "quarantine canary replay (item 3 step C): single-task re-entry probe"],
        capture_output=True, text=True, timeout=120)
    print((r.stdout or "") + (r.stderr or ""), end="")
    if r.returncode != 0:
        print("quarantine-replay: DISPOSITION_FAIL rc=%d" % r.returncode)
        return 1
    verdict, final_status = "TIMEOUT", None
    deadline = time.time() + POLL_SECONDS
    with psycopg.connect(dsn) as conn:
        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM central_tasks WHERE task_id::text=%s", (tid,))
                got = cur.fetchone()
            final_status = got[0] if got else None
            if final_status == "DONE":
                verdict = "DONE"
                break
            if final_status in ("RETRY_WAIT", "QUARANTINED"):
                verdict = final_status
                break
            time.sleep(60)
    rec = {"task_id": tid, "title": title, "type": typ, "retry_before": retry,
           "promoted_at": promoted_at, "verdict": verdict,
           "final_status": final_status, "polled_until": _ts()}
    p = os.path.join(STATE_DIR, "replay-%s-%s.json" % (
        tid[:8], promoted_at.replace(":", "").replace("-", "")))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print("QUARANTINE_REPLAY verdict=%s task=%s final_status=%s receipt=%s"
          % (verdict, tid, final_status, p))
    return 0 if verdict == "DONE" else 1


if __name__ == "__main__":
    sys.exit(main())
