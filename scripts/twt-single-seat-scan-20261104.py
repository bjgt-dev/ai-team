#!/usr/bin/env python3
"""Single-seat queue scanner (2026-11-04, chairman order).
Scans ONLY status READY/CLAIMED in production central_tasks.
If a task is bound to muse / muse-2 / Noesis as a person/session
(binding fields, or a ws2-storage-muse* title), it is re-annotated to
the main-worker pool, owned by twt/muse-1:
  payload.worker -> any-reachable-main-worker (only if it was a person binding)
  payload.executor_pool -> any-reachable-main-worker
  payload.convergence_owner -> twt/muse-1
  payload.single_person_binding -> false
  payload.convergence_note_20261104 -> explanation
NEVER touched: status, current_fencing_token, retry_count, lane, owner column,
artifact fields, and any task in DONE / QUARANTINED / other statuses
(those are excluded by the SQL WHERE clause, not by code alone).
DSN only from state/.cq-dsn, no DSN => fail closed, no write.
Log: state/twt-single-seat-scan-20261104.log (append, with before/after worker).
Usage: --dry-run (default-safe preview) | --apply (real re-annotation)
"""
import json, os, sys
from datetime import datetime, timezone

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
LOG_FILE = "/home/ubuntu/ai-team/state/twt-single-seat-scan-20261104.log"
BINDING_FIELDS = ("worker", "executor", "executor_pool", "assignee", "owner",
                  "target_session", "worker_id", "session")
ALLOWED_POOLS = {"twt", "muse-1", "twt/muse-1", "any-reachable-main-worker",
                 "instabox-main-workers", "main-worker-pool", "hub-timer", "c2-executor"}

def get_dsn():
    if not os.access(DSN_FILE, os.R_OK):
        return ""
    for line in open(DSN_FILE, encoding="utf-8"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""

def _norm(v):
    return str(v).strip().lower() if v is not None else ""

def _is_person_binding(value):
    v = _norm(value)
    if not v or v in ALLOWED_POOLS:
        return False
    if v in ("muse-1", "twt-muse-1") or v.startswith("muse-1-"):
        return False
    if "muse-2" in v or "noesis" in v:
        return True
    if v == "muse" or v.startswith("muse-") or v.startswith("muse_"):
        return True
    return False

def find_bound(title, payload):
    hits = []
    if isinstance(payload, dict):
        for f in BINDING_FIELDS:
            if f in payload and _is_person_binding(payload.get(f)):
                hits.append("%s=%r" % (f, payload.get(f)))
    t = _norm(title)
    if t.startswith("ws2-storage-muse") and "muse-1" not in t:
        hits.append("title=%r" % (title,))
    return hits

def main(argv):
    dry = "--dry-run" in argv
    apply = "--apply" in argv
    if dry == apply:
        print("scan: pass exactly one of --dry-run / --apply", file=sys.stderr)
        return 2
    dsn = get_dsn()
    if not dsn:
        print("scan: no DSN, refusing"); return 0
    import psycopg
    now = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM central_tasks GROUP BY status ORDER BY status")
            counts_before = dict(cur.fetchall())
            cur.execute(
                "SELECT task_id, title, status, payload, current_fencing_token, retry_count "
                "FROM central_tasks WHERE status IN ('READY','CLAIMED') ORDER BY created_at")
            rows = cur.fetchall()
            bound = []
            for task_id, title, status, payload, fence, retry in rows:
                hits = find_bound(title, payload)
                if hits:
                    bound.append((task_id, title, status, payload, fence, retry, hits))
            if apply and bound:
                for task_id, title, status, payload, fence, retry, hits in bound:
                    newp = dict(payload) if isinstance(payload, dict) else {}
                    old_worker = newp.get("worker")
                    if _is_person_binding(old_worker):
                        newp["worker"] = "any-reachable-main-worker"
                    newp["executor_pool"] = "any-reachable-main-worker"
                    newp["convergence_owner"] = "twt/muse-1"
                    newp["single_person_binding"] = False
                    newp["convergence_note_20261104"] = (
                        "Single-seat convergence 2026-11-04: chairman only interfaces twt/muse-1; "
                        "muse/muse-2 are external disks only; execution only via InstaBox 12 main workers. "
                        "Status/fence/retry untouched by this scan.")
                    cur.execute("UPDATE central_tasks SET payload=%s::jsonb WHERE task_id=%s AND status IN ('READY','CLAIMED')",
                                (json.dumps(newp, ensure_ascii=False), task_id))
                conn.commit()
            cur.execute("SELECT status, count(*) FROM central_tasks GROUP BY status ORDER BY status")
            counts_after = dict(cur.fetchall())
    line = "%s mode=%s scanned_ready_claimed=%d bound=%d changed=%d counts_before=%s counts_after=%s bound_tasks=%s\n" % (
        now, "dry-run" if dry else "apply", len(rows), len(bound),
        0 if dry else len(bound), counts_before, counts_after,
        [(str(t), ti, s, h) for (t, ti, s, _p, _f, _r, h) in bound])
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.strip())
    for task_id, title, status, payload, fence, retry, hits in bound:
        print("BOUND %s %s %s fence=%s retry=%s hits=%s old_worker=%r" % (task_id, title, status, fence, retry, hits, payload.get("worker") if isinstance(payload, dict) else None))
    if counts_before != counts_after:
        print("scan: WARNING counts changed, investigate before trusting this run")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
