#!/usr/bin/env python3
"""long-task-sweeper-20261106.py — items 1 + 3 (slugs long-task-heartbeat,
queue-harden-lease-dlq step A).

Three jobs, one run (default --dry-run prints what it WOULD do; --live acts):
  1. STALE scan: tasks in CLAIMED/RUNNING with no heartbeat_at update AND
     no ACTIVE-lease heartbeat in the last 30 min -> stale.json + outbox
     alert (only on NEW stale tasks, to avoid spam). STALE is a computed
     flag, NOT a task status (the transition guard enum is unchanged).
  2. Timeout stop: tasks in CLAIMED/RUNNING whose deadline has passed ->
     QUARANTINED (legal per guard) with a payload timeout note
     (timeout_at, stopped_next_step), ACTIVE lease released, one outbox
     record per task. Cutover51 W3.6: READY/RETRY_WAIT past deadline are
     now ALSO auto-isolated (was: report only) — a waiting task past
     deadline is quarantined with a payload note + one batch outbox record.
  3. Retry-exhaustion isolation (item 3 step A): RETRY_WAIT tasks whose
     retry_count >= max_retries (default 3) -> QUARANTINED with a payload
     retry_exhausted note + one batch outbox record per run. This is what
     breaks the claim -> sweep -> reclaim infinite loop: the claimers now
     refuse retry_count >= max_retries, so exhausted tasks can only land
     here, never back in CLAIMED.

Heartbeat sources (all stamp central_tasks.heartbeat_at):
  - queue_api /v1/heartbeat and /v1/checkpoint (worker-1),
  - claim (queue_api /v1/claim and cq-csv claim) stamps it at claim time.
Deadline is set at insert by timer_submit / dispatch_task / patrol_submit
(default now()+4h; dispatch_task honours contract deadline_hours 1..48).

DSN only from state/.cq-dsn, fail closed. Stdlib + psycopg only.
Usage:
  python3 scripts/long-task-sweeper-20261106.py [--live] [--stale-minutes N]
Cron (installed by item 1, append-only):
  */5 * * * * .../cron_flock.sh longtask_sweep python3 .../long-task-sweeper-20261106.py --live
"""
import argparse
import json
import os
import sys
import time

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
STATE_DIR = BASE + "/state/long-task-heartbeat"
OUTBOX = BASE + "/state/outbox"
STALE_MINUTES_DEFAULT = 30


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


def _write_outbox(name, body):
    os.makedirs(OUTBOX, exist_ok=True)
    p = os.path.join(OUTBOX, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


# F1 2026-11-10 (failure_id 12): diagnostic event payload. The audit
# trigger writes central_events payload '{}'; stamp the just-created
# STATUS_CHANGE row (same txn) so the reason survives without chasing
# worker logs. Diagnostic only: nothing reads event payload for decisions.
def _last_failure(cur, tid):
    """Latest stamped RETRY_WAIT event (failure_class, reason) or (None, None)."""
    cur.execute(
        "SELECT payload->>'failure_class', payload->>'reason' FROM central_events"
        " WHERE task_id=%s AND new_status='RETRY_WAIT' AND payload <> '{}'::jsonb"
        " ORDER BY created_at DESC LIMIT 1", (tid,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _stamp_event(cur, tid, new_status, fclass, reason):
    cur.execute(
        "UPDATE central_events SET payload = jsonb_build_object('failure_class', %s, 'reason', %s)"
        " WHERE event_id = (SELECT event_id FROM central_events"
        " WHERE task_id=%s AND new_status=%s AND payload='{}'::jsonb"
        " ORDER BY created_at DESC LIMIT 1)",
        (fclass, reason[:300], tid, new_status))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="act; without it, dry-run only")
    ap.add_argument("--stale-minutes", type=int, default=STALE_MINUTES_DEFAULT)
    a = ap.parse_args()
    dsn = _dsn()
    if not dsn:
        print("long-task-sweeper: no DSN, refusing")
        return 0
    import psycopg
    now = _ts()
    os.makedirs(STATE_DIR, exist_ok=True)
    stale_path = os.path.join(STATE_DIR, "stale.json")
    prev_stale = set()
    try:
        prev_stale = set(json.load(open(stale_path)).get("stale_task_ids", []))
    except Exception:
        pass
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # --- STALE scan: in-flight, silent for >= threshold, no fresh lease heartbeat.
            cur.execute(
                """SELECT t.task_id, t.title, t.status, t.heartbeat_at, t.retry_count,
                          l.last_heartbeat_at AS lease_hb
                   FROM central_tasks t
                   LEFT JOIN central_leases l
                     ON l.task_id = t.task_id AND l.status = 'ACTIVE'
                   WHERE t.status IN ('CLAIMED','RUNNING')
                     AND (t.heartbeat_at IS NULL
                          OR t.heartbeat_at < now() - (%s || ' minutes')::interval)
                     AND (l.last_heartbeat_at IS NULL
                          OR l.last_heartbeat_at < now() - (%s || ' minutes')::interval)
                   ORDER BY t.heartbeat_at NULLS FIRST""",
                (a.stale_minutes, a.stale_minutes))
            stale_rows = cur.fetchall()
            # --- Timeout scan: in-flight past deadline.
            cur.execute(
                """SELECT task_id, title, status, deadline, next_step, retry_count
                   FROM central_tasks
                   WHERE deadline IS NOT NULL AND deadline < now()
                     AND status IN ('CLAIMED','RUNNING')
                   ORDER BY deadline""")
            timeout_rows = cur.fetchall()
            # --- Waiting past deadline: report only, never auto-quarantine.
            cur.execute(
                """SELECT task_id, title, status, deadline FROM central_tasks
                   WHERE deadline IS NOT NULL AND deadline < now()
                     AND status IN ('READY','RETRY_WAIT')
                   ORDER BY deadline""")
            waiting_rows = cur.fetchall()
            # --- Retry-exhaustion scan: RETRY_WAIT past its retry budget.
            cur.execute(
                """SELECT task_id, title, retry_count, max_retries
                   FROM central_tasks
                   WHERE status = 'RETRY_WAIT'
                     AND retry_count >= COALESCE(max_retries, 3)
                   ORDER BY retry_count DESC""")
            exhausted_rows = cur.fetchall()
            stale_ids = [str(r[0]) for r in stale_rows]
            result = {
                "ts": now, "mode": "live" if a.live else "dry-run",
                "stale_minutes": a.stale_minutes,
                "stale": [{"task_id": str(r[0]), "title": r[1], "status": r[2],
                           "heartbeat_at": str(r[3]) if r[3] else None,
                           "retry_count": r[4]} for r in stale_rows],
                "timeout": [{"task_id": str(r[0]), "title": r[1], "status": r[2],
                             "deadline": str(r[3]), "next_step": r[4]} for r in timeout_rows],
                "waiting_past_deadline": [{"task_id": str(r[0]), "title": r[1],
                                           "status": r[2], "deadline": str(r[3])}
                                          for r in waiting_rows],
                "retry_exhausted": [{"task_id": str(r[0]), "title": r[1],
                                     "retry_count": r[2], "max_retries": r[3]}
                                    for r in exhausted_rows],
            }
            if a.live:
                # Retry-exhaustion isolation: RETRY_WAIT -> QUARANTINED
                # (legal per guard) with payload note + one batch outbox.
                isolated = []
                for r in exhausted_rows:
                    tid, title, retry, maxr = r
                    note = {"retry_exhausted": True, "exhausted_at": now,
                            "retry_count": retry,
                            "max_retries": maxr if maxr is not None else 3}
                    cur.execute(
                        """UPDATE central_tasks SET status='QUARANTINED',
                               payload = payload || %s::jsonb
                           WHERE task_id=%s AND status='RETRY_WAIT'""",
                        (json.dumps(note), tid))
                    if cur.rowcount == 1:
                        # F1 2026-11-10 (failure_id 12): inherit the last
                        # RETRY_WAIT failure class/reason into the
                        # QUARANTINED event payload (diagnostic only).
                        _pc, _pr = _last_failure(cur, tid)
                        _rs = ("retry-exhausted: retry_count=%s>=max_retries=%s"
                               % (retry, maxr if maxr is not None else 3))
                        if _pr:
                            _rs += "; last failure: " + _pr
                        _stamp_event(cur, tid, "QUARANTINED", _pc or "F-UNDIAG", _rs)
                        isolated.append(str(tid))
                if isolated:
                    body = ("# Retry-exhausted isolation %s\n\n"
                            "RETRY_WAIT tasks past their retry budget "
                            "(retry_count >= max_retries) moved to QUARANTINED:\n\n" % now)
                    for r in exhausted_rows:
                        if str(r[0]) in isolated:
                            body += "- %s %s retry=%s max_retries=%s\n" % (r[0], r[1], r[2], r[3])
                    body += ("\nOperator: long-task-sweeper-20261106.py --live\n"
                             "Re-entry: pipeline-disposition only, never manual SQL.\n")
                    _write_outbox("retry-exhausted-%s.md" % now.replace(":", "").replace("-", ""), body)
                    result["retry_exhausted_isolated"] = isolated
                # Timeout stop: QUARANTINED + payload note + release ACTIVE lease.
                for r in timeout_rows:
                    tid, title, status, deadline, next_step, retry = r
                    note = {"timeout": True, "timeout_at": now,
                            "stopped_next_step": next_step,
                            "stopped_from_status": status,
                            "deadline": str(deadline)}
                    cur.execute(
                        """UPDATE central_tasks SET status='QUARANTINED',
                               payload = payload || %s::jsonb
                           WHERE task_id=%s AND status IN ('CLAIMED','RUNNING')""",
                        (json.dumps(note), tid))
                    if cur.rowcount == 1:
                        # F1 2026-11-10 (failure_id 12): stamp the
                        # QUARANTINED event payload (diagnostic only).
                        _stamp_event(cur, tid, "QUARANTINED", "F-TIMEOUT",
                                     "deadline %s passed in %s at step %s"
                                     % (deadline, status, next_step))
                        cur.execute(
                            "UPDATE central_leases SET status='RELEASED' "
                            "WHERE task_id=%s AND status='ACTIVE'", (tid,))
                        rec = ("# Timeout stop %s\n\n- task_id: %s\n- title: %s\n"
                               "- %s -> QUARANTINED (deadline %s passed)\n"
                               "- stopped_next_step: %s\n- retry_count: %s\n"
                               "- operator: long-task-sweeper-20261106.py --live\n- ts: %s\n"
                               % (str(tid)[:8], tid, title, status, deadline,
                                  next_step, retry, now))
                        _write_outbox("timeout-stop-%s-%s.md" % (
                            str(tid)[:8], now.replace(":", "").replace("-", "")), rec)
                        result.setdefault("timeout_stopped", []).append(str(tid))
                # Cutover51 W3.6: waiting past deadline auto-isolation.
                # READY/RETRY_WAIT -> QUARANTINED with payload note + batch outbox.
                waiting_isolated = []
                for r in waiting_rows:
                    tid, title, status, deadline = r
                    note = {"deadline_passed_waiting": True, "isolated_at": now,
                            "from_status": status, "deadline": str(deadline)}
                    cur.execute(
                        """UPDATE central_tasks SET status='QUARANTINED',
                               payload = payload || %s::jsonb
                           WHERE task_id=%s AND status IN ('READY','RETRY_WAIT')""",
                        (json.dumps(note), tid))
                    if cur.rowcount == 1:
                        # F1 2026-11-10 (failure_id 12): stamp the
                        # QUARANTINED event payload; RETRY_WAIT rows inherit
                        # the last failure class/reason (diagnostic only).
                        if status == "RETRY_WAIT":
                            _pc, _pr = _last_failure(cur, tid)
                            _rs = ("deadline-passed-waiting: deadline %s passed"
                                   % deadline)
                            if _pr:
                                _rs += "; last failure: " + _pr
                            _stamp_event(cur, tid, "QUARANTINED",
                                         _pc or "F-UNDIAG", _rs)
                        else:
                            _stamp_event(cur, tid, "QUARANTINED", "F-TIMEOUT",
                                         "deadline %s passed while %s"
                                         % (deadline, status))
                        waiting_isolated.append(str(tid))
                if waiting_isolated:
                    body = ("# Waiting-past-deadline isolation %s\n\n"
                            "READY/RETRY_WAIT tasks past deadline moved to QUARANTINED:\n\n" % now)
                    for r in waiting_rows:
                        if str(r[0]) in waiting_isolated:
                            body += "- %s %s status=%s deadline=%s\n" % (r[0], r[1], r[2], r[3])
                    body += ("\nOperator: long-task-sweeper-20261106.py --live (cutover51 W3.6)\n"
                             "Re-entry: pipeline-disposition only, never manual SQL.\n")
                    _write_outbox("waiting-deadline-%s.md" % now.replace(":", "").replace("-", ""), body)
                    result["waiting_past_deadline_isolated"] = waiting_isolated
                # STALE state file + alert only on NEW stale tasks.
                with open(stale_path, "w", encoding="utf-8") as f:
                    json.dump({"ts": now, "stale_minutes": a.stale_minutes,
                               "stale_task_ids": stale_ids}, f, ensure_ascii=False, indent=2)
                new_stale = [s for s in stale_ids if s not in prev_stale]
                if new_stale:
                    body = ("# STALE alert %s\n\nTasks in-flight with no heartbeat "
                            "for >= %d minutes (task heartbeat_at AND ACTIVE-lease "
                            "heartbeat both stale):\n\n" % (now, a.stale_minutes))
                    for r in stale_rows:
                        if str(r[0]) in new_stale:
                            body += ("- %s %s status=%s heartbeat_at=%s retry=%s\n"
                                     % (r[0], r[1], r[2], r[3], r[4]))
                    body += ("\nOperator: long-task-sweeper-20261106.py --live\n"
                             "Note: STALE is a computed flag, not a status change.\n")
                    _write_outbox("stale-alert-%s.md" % now.replace(":", "").replace("-", ""), body)
                    result["stale_alerted_new"] = new_stale
        conn.commit()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
