#!/usr/bin/env python3
"""Central-queue claimer / sweeper / verifier (TEST-READY, 2026-11-02).

These three components complete switch-list item "install claimer/sweeper/
verifier timers". They are NOT installed as timers yet: installation is a
switch-window action. Until then they run only against ephemeral instances
in tests, or with an explicit CQ_DSN supplied by the operator.

Connection: DSN comes ONLY from the CQ_DSN environment variable. No
credential, host, or password is hardcoded here or in any config file.
Fail closed: any DB error aborts without claiming, expiring, or deciding.

Semantics (matches the DONE-gate patch state machine):
- claimer: one transaction. Picks the highest-priority claimable task
  (lane order P0<biz<infra, then oldest created_at) among READY tasks
  and RETRY_WAIT tasks whose not_before has passed. Moves it
  READY/RETRY_WAIT -> DISPATCHING -> CLAIMED inside the same transaction
  (the transition trigger stays strict: no READY->CLAIMED jump) and
  inserts an ACTIVE lease with fencing = current_fencing_token + 1.
  SELECT ... FOR UPDATE SKIP LOCKED makes concurrent claimers safe
  (exactly one wins per task). Tasks holding an ACTIVE lease are never
  re-claimed (NOT EXISTS guard): a task that still holds a lease is
  owned, even if its status says RETRY_WAIT.
- sweeper: one transaction. Expires ACTIVE leases past expires_at, and
  moves their tasks CLAIMED/RUNNING -> RETRY_WAIT with retry_count + 1
  (re-claimable by the claimer once not_before passes; retry exhaustion
  is the verifier's DEAD decision, not the sweeper's).
- verifier: reuses butler_decide.decide_rules (same DONE evidence gate:
  verifier_pass + token + 64-hex sha + bytes >= min_bytes + verified_by).
  --check prints the verdict without writing; --apply writes the verdict
  to the task (DONE/FAILED/DEAD via the legal transitions VERIFYING->DONE,
  VERIFYING->RETRY_WAIT, and DEAD via QUARANTINED + payload suspect note --
  DEAD is not a task status; dead tasks are quarantined with a dead note,
  matching the queue's terminal handling). Any --apply that moves a task
  out of VERIFYING also releases its ACTIVE lease, so a RETRY_WAIT task
  never holds a live lease.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
import butler_decide as bd  # noqa: E402
try:
    from cq_common import LEASE_SECONDS
except Exception:
    LEASE_SECONDS = 1200  # cutover51 W3: single lease definition is cq_common

LANE_ORDER_SQL = "CASE lane WHEN 'P0' THEN 0 WHEN 'biz' THEN 1 WHEN 'infra' THEN 2 ELSE 9 END"


def connect():
    import psycopg
    dsn = os.environ.get("CQ_DSN", "")
    if not dsn:
        raise RuntimeError("CQ_DSN not set; refusing to connect anywhere by default")
    return psycopg.connect(dsn)


def claim(worker_id):
    """Returns (task_id, fencing_token) or None. Atomic single transaction."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT task_id, current_fencing_token FROM central_tasks
                    WHERE (status = 'READY'
                           OR (status = 'RETRY_WAIT'
                               AND (not_before IS NULL OR not_before <= now())))
                    AND retry_count < COALESCE(max_retries, 3)
                    AND NOT EXISTS (SELECT 1 FROM central_leases l
                                    WHERE l.task_id = central_tasks.task_id
                                    AND l.status = 'ACTIVE')
                    ORDER BY {LANE_ORDER_SQL}, created_at
                    LIMIT 1 FOR UPDATE SKIP LOCKED""")
            row = cur.fetchone()
            if not row:
                return None
            task_id, token = row
            new_token = token + 1
            cur.execute(
                f"""INSERT INTO central_leases (task_id, worker_id, fencing_token, expires_at)
                   VALUES (%s, %s, %s, now() + interval '{LEASE_SECONDS} seconds')""",
                (task_id, worker_id, new_token))
            cur.execute(
                """UPDATE central_tasks SET status = 'DISPATCHING'
                   WHERE task_id = %s""",
                (task_id,))
            cur.execute(
                """UPDATE central_tasks SET status = 'CLAIMED',
                       current_fencing_token = %s, heartbeat_at = now() WHERE task_id = %s""",
                (new_token, task_id))
        conn.commit()
    return (str(task_id), new_token)


def sweep():
    """Expires overdue ACTIVE leases; returns (leases_expired, tasks_moved)."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE central_leases SET status = 'EXPIRED'
                   WHERE status = 'ACTIVE' AND expires_at <= now()
                   RETURNING task_id""")
            expired = [r[0] for r in cur.fetchall()]
            moved = 0
            for task_id in expired:
                # F2 2026-11-10 (failure_id 11): retry>=max is terminal - QUARANTINED, no +1.
                cur.execute(
                    """UPDATE central_tasks
                       SET status = CASE WHEN retry_count >= COALESCE(max_retries, 3) THEN 'QUARANTINED' ELSE 'RETRY_WAIT' END,
                           retry_count = CASE WHEN retry_count >= COALESCE(max_retries, 3) THEN retry_count ELSE retry_count + 1 END
                       WHERE task_id = %s AND status IN ('CLAIMED', 'RUNNING')""",
                    (task_id,))
                moved += cur.rowcount
        conn.commit()
    return (len(expired), moved)


def verify(task_id, receipt, verifier_pass, verified_by, apply=False):
    """Verdict via butler_decide rules. --check only prints; --apply writes."""
    task = {"task_id": task_id, "verifier_pass": verifier_pass,
            "receipt": receipt, "retry_count": 0, "max_attempts": 3}
    verdict, level, conditions, rationale = bd.decide_rules(task)
    result = {"task_id": task_id, "verdict": verdict, "level": level,
              "conditions": conditions, "rationale": rationale}
    if not apply:
        return result
    with connect() as conn:
        with conn.cursor() as cur:
            if verdict == "DONE":
                cur.execute(
                    """UPDATE central_tasks SET status = 'DONE',
                           artifact_sha = %s, artifact_bytes = %s,
                           verified_by = %s, verified_at = now()
                       WHERE task_id = %s AND status = 'VERIFYING'""",
                    (receipt.get("sha"), receipt.get("bytes"), verified_by, task_id))
            elif verdict == "FAILED":
                # F2 2026-11-10 (failure_id 11): retry>=max is terminal - QUARANTINED, no +1.
                cur.execute(
                    """UPDATE central_tasks
                       SET status = CASE WHEN retry_count >= COALESCE(max_retries, 3) THEN 'QUARANTINED' ELSE 'RETRY_WAIT' END,
                           retry_count = CASE WHEN retry_count >= COALESCE(max_retries, 3) THEN retry_count ELSE retry_count + 1 END
                       WHERE task_id = %s AND status = 'VERIFYING'""",
                    (task_id,))
            elif verdict == "DEAD":
                cur.execute(
                    """UPDATE central_tasks SET status = 'QUARANTINED',
                           payload = payload || '{"dead": true}'::jsonb
                       WHERE task_id = %s AND status = 'VERIFYING'""",
                    (task_id,))
            else:  # NO_CHANGE: verifier cannot decide; leave untouched
                result["applied"] = False
                return result
            task_rows = cur.rowcount
            # A task leaving VERIFYING releases its ACTIVE lease, so a
            # RETRY_WAIT task never holds a live lease (which would make
            # the next claim violate the single-active-lease index).
            cur.execute(
                """UPDATE central_leases SET status = 'RELEASED'
                   WHERE task_id = %s AND status = 'ACTIVE'""",
                (task_id,))
            result["applied"] = (task_rows == 1)
        conn.commit()
    return result


def main(argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("claim"); p.add_argument("worker_id")
    sub.add_parser("sweep")
    p = sub.add_parser("verify"); p.add_argument("task_id")
    p.add_argument("--receipt", required=True, help="JSON: token,sha,bytes")
    p.add_argument("--verifier-pass", action="store_true")
    p.add_argument("--verified-by", default="")
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "claim":
        r = claim(a.worker_id)
        print(json.dumps({"claimed": r}, ensure_ascii=False))
    elif a.cmd == "sweep":
        e, m = sweep()
        print(json.dumps({"leases_expired": e, "tasks_moved": m}))
    else:
        receipt = json.loads(a.receipt)
        r = verify(a.task_id, receipt, a.verifier_pass, a.verified_by, a.apply)
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
