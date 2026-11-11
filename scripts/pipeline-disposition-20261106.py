#!/usr/bin/env python3
"""pipeline-disposition-20261106.py — scripted human disposition (formal pipeline, DESIGN §3).

Replaces ad-hoc SQL. Only transitions the live guard allows for a human:
QUARANTINED->READY and RETRY_WAIT->READY, in ONE transaction, retry_count=0,
deadline reset to now()+4h (2026-11-07 fix: re-queued tasks kept their expired
deadline and the sweeper re-quarantined them on claim).
Never touches current_fencing_token (NOT NULL; new claim bumps it), never
touches DONE rows, never sets any other status.

quarantine-ready (2026-11-11, Plan A g5-planid-dispatch-fix-20261111):
READY->QUARANTINED, the missing entry point the sweeper never had (it only
reports READY past deadline, never auto-quarantines). Guards: task must be
READY; --reason >= 10 chars; a code/script task with a non-NULL plan_id is
REFUSED (it can be claimed; this path is only for unclaimable tasks, e.g. a
poison-pill canary with plan_id NULL that blocks its whole lane's claims).
Sets status='QUARANTINED' + reason=<reason> only (no plan_id touch, no
fencing-token touch, no row delete). Reversible via promote-quarantined.
Every run writes an outbox record (audit trail).

Usage:
  python3 scripts/pipeline-disposition-20261106.py --task-id <uuid>
      --action promote-quarantined|promote-retrywait|quarantine-ready --reason "<text>"
Exit 0 = rowcount 1 and outbox record written; 1 = anything else.
"""
import argparse, os, sys, time

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
OUTBOX = BASE + "/state/outbox"


def _dsn():
    for line in open(DSN_FILE):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def _quarantine_ready(a):
    """READY->QUARANTINED (2026-11-11, Plan A). Guards: task must be READY;
    --reason >= 10 chars; a code/script task with a non-NULL plan_id is
    refused (it can be claimed). Sets status + reason only. Reversible via
    promote-quarantined. Writes an outbox audit record."""
    import psycopg
    if not isinstance(a.reason, str) or len(a.reason.strip()) < 10:
        print("DISPOSITION_FAIL: --reason must be >= 10 chars"); return 1
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, retry_count, current_fencing_token, "
                "payload->>'type', plan_id FROM central_tasks "
                "WHERE task_id::text=%s", (a.task_id,))
            row = cur.fetchone()
            if not row:
                print("DISPOSITION_FAIL: task not found"); return 1
            old_status, old_retry, token, ttype, plan_id = row
            if old_status != "READY":
                print("DISPOSITION_FAIL: status=%s, expected READY" % old_status); return 1
            if ttype in ("code", "script") and plan_id is not None:
                print("DISPOSITION_FAIL: %s task has plan_id=%s and can be claimed; "
                      "quarantine-ready is only for unclaimable tasks" % (ttype, plan_id)); return 1
            cur.execute(
                "UPDATE central_tasks SET status='QUARANTINED', reason=%s "
                "WHERE task_id::text=%s AND status='READY'", (a.reason, a.task_id))
            rc = cur.rowcount
        conn.commit()
    if rc != 1:
        print("DISPOSITION_FAIL: rowcount=%d" % rc); return 1
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec = ("# Disposition %s\n\n- task_id: %s\n- action: quarantine-ready\n- old_status: READY -> new_status: QUARANTINED\n"
           "- type: %s\n- plan_id: %s\n- retry_count: %s (unchanged)\n- current_fencing_token: %s (unchanged)\n"
           "- reason: %s\n- operator: pipeline-disposition-20261106.py\n- ts: %s\n- rollback: promote-quarantined --task-id %s --reason \"<text>\"\n"
           % (a.task_id[:8], a.task_id, ttype, plan_id, old_retry, token, a.reason, ts, a.task_id))
    os.makedirs(OUTBOX, exist_ok=True)
    p = os.path.join(OUTBOX, "disposition-%s-%s.md" % (a.task_id[:8], ts.replace(":", "").replace("-", "")))
    open(p, "w").write(rec)
    print("DISPOSITION_OK task=%s READY->QUARANTINED type=%s plan_id=%s record=%s" % (a.task_id[:8], ttype, plan_id, p))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--action", required=True,
                    choices=["promote-quarantined", "promote-retrywait", "quarantine-ready"])
    ap.add_argument("--reason", required=True)
    a = ap.parse_args()
    if a.action == "quarantine-ready":
        return _quarantine_ready(a)
    want_old = "QUARANTINED" if a.action == "promote-quarantined" else "RETRY_WAIT"
    import psycopg
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, retry_count, current_fencing_token FROM central_tasks "
                "WHERE task_id::text=%s", (a.task_id,))
            row = cur.fetchone()
            if not row:
                print("DISPOSITION_FAIL: task not found"); return 1
            old_status, old_retry, token = row
            if old_status != want_old:
                print("DISPOSITION_FAIL: status=%s, expected %s" % (old_status, want_old)); return 1
            cur.execute(
                "UPDATE central_tasks SET status='READY', retry_count=0, "
                "deadline = now() + interval '4 hours' "
                "WHERE task_id::text=%s AND status=%s", (a.task_id, want_old))
            rc = cur.rowcount
        conn.commit()
    if rc != 1:
        print("DISPOSITION_FAIL: rowcount=%d" % rc); return 1
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec = ("# Disposition %s\n\n- task_id: %s\n- action: %s\n- old_status: %s -> new_status: READY\n"
           "- retry_count: %s -> 0\n- current_fencing_token: %s (unchanged, new claim bumps it)\n"
           "- reason: %s\n- operator: pipeline-disposition-20261106.py\n- ts: %s\n"
           % (a.task_id[:8], a.task_id, a.action, old_status, old_retry, token, a.reason, ts))
    os.makedirs(OUTBOX, exist_ok=True)
    p = os.path.join(OUTBOX, "disposition-%s-%s.md" % (a.task_id[:8], ts.replace(":", "").replace("-", "")))
    open(p, "w").write(rec)
    print("DISPOSITION_OK task=%s %s->READY retry=%s->0 token=%s record=%s" % (a.task_id[:8], old_status, old_retry, token, p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
