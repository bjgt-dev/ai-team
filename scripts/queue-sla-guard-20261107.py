#!/usr/bin/env python3
"""Queue SLA guard (2026-11-07, MEMORY.md P0-1 + pace compression).

Rules landed as code (were memory-only):
  * claimer concurrency cap: at most 3 tasks in CLAIMED status at once.
  * pace: deadline_hours must be 4 (normal) or 8 (complex); 1..48 free choice
    in dispatch_task.py is NOT the rule -- the chair's rule is 4/8 only.
  * overdue +2h past deadline (CLAIMED/RUNNING) -> escalate TOP (reported).
  * overdue +6h past deadline -> report chair (outbox file).
  * no progress: heartbeat older than 30 min on CLAIMED/RUNNING -> FLAG.
Live DB: psycopg3, DSN only from state/.cq-dsn (CQ_DSN=...), same as
pipeline-disposition-20261106.py. No DSN / no psycopg -> SKIP (exit 2),
never a false PASS. Pure check functions are unit-tested via --self-test.
Exit: 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone

MAX_CONCURRENT_CLAIMS = 3
SLA_HOURS = (4, 8)
OVERDUE_TOP_H = 2
OVERDUE_CHAIR_H = 6
NO_PROGRESS_MIN = 30
ACTIVE = ("CLAIMED", "RUNNING")
BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
DSN_FILE = os.path.join(BASE, "state", ".cq-dsn")
OUTBOX = os.path.join(BASE, "state", "outbox")


def now_utc():
    return datetime.now(timezone.utc)


def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_claims(statuses):
    """statuses: iterable of status strings. Returns list of violations."""
    n = sum(1 for s in statuses if s == "CLAIMED")
    if n > MAX_CONCURRENT_CLAIMS:
        return ["claimer concurrency %d > cap %d" % (n, MAX_CONCURRENT_CLAIMS)]
    return []


def check_sla(tasks, now):
    """tasks: dicts with status, deadline_hours, deadline, heartbeat_at.
    Returns (top_list, chair_list, flag_list, bad_sla_list) of task_ids."""
    top, chair, flag, bad = [], [], [], []
    for t in tasks:
        tid = t.get("task_id", "?")
        dh = t.get("deadline_hours")
        if dh is not None and dh not in SLA_HOURS:
            bad.append(tid)
        if t.get("status") not in ACTIVE:
            continue
        dl = _parse_ts(t.get("deadline"))
        if dl is not None:
            if now > dl + timedelta(hours=OVERDUE_CHAIR_H):
                chair.append(tid)
            elif now > dl + timedelta(hours=OVERDUE_TOP_H):
                top.append(tid)
        hb = _parse_ts(t.get("heartbeat_at"))
        if hb is not None and now - hb > timedelta(minutes=NO_PROGRESS_MIN):
            flag.append(tid)
    return top, chair, flag, bad


def _dsn():
    try:
        for line in open(DSN_FILE):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip("\"").strip("'")
    except OSError:
        pass
    return ""


def live_rows():
    import psycopg
    dsn = _dsn()
    if not dsn:
        return None, "no CQ_DSN in %s" % DSN_FILE
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM central_tasks")
            statuses = [r[0] for r in cur.fetchall()]
            try:
                cur.execute("SELECT task_id,status,deadline,heartbeat_at,deadline_hours "
                            "FROM central_tasks WHERE status IN ('CLAIMED','RUNNING')")
                cols = ["task_id", "status", "deadline", "heartbeat_at", "deadline_hours"]
            except Exception:
                conn.rollback()
                cur.execute("SELECT task_id,status,deadline,heartbeat_at "
                            "FROM central_tasks WHERE status IN ('CLAIMED','RUNNING')")
                cols = ["task_id", "status", "deadline", "heartbeat_at"]
            tasks = [dict(zip(cols, r)) for r in cur.fetchall()]
    return (statuses, tasks), ""


def write_outbox(name, payload):
    os.makedirs(OUTBOX, exist_ok=True)
    fn = os.path.join(OUTBOX, name)
    with open(fn, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return fn


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    # positive: 3 claims OK, 4 claims FAIL
    ck(check_claims(["CLAIMED"] * 3) == [], "3 claims within cap")
    ck(len(check_claims(["CLAIMED"] * 4)) == 1, "4 claims violates cap")
    ck(check_claims(["READY", "DONE"]) == [], "no claims OK")
    t0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    base = {"task_id": "t1", "status": "CLAIMED",
            "deadline": "2026-11-07T08:00:00+00:00",
            "heartbeat_at": "2026-11-07T11:45:00+00:00", "deadline_hours": 4}
    top, chair, flag, bad = check_sla([dict(base)], t0)
    ck(top == ["t1"] and chair == [] and flag == [], "2h+ overdue -> TOP only")
    b2 = dict(base, deadline="2026-11-07T04:00:00+00:00")
    top, chair, flag, bad = check_sla([dict(b2)], t0)
    ck(chair == ["t1"], "6h+ overdue -> CHAIR")
    b3 = dict(base, deadline="2026-11-07T16:00:00+00:00",
              heartbeat_at="2026-11-07T11:00:00+00:00")
    top, chair, flag, bad = check_sla([dict(b3)], t0)
    ck(flag == ["t1"] and top == [], "30min no heartbeat -> FLAG")
    b4 = dict(base, deadline_hours=12)
    _, _, _, bad = check_sla([dict(b4)], t0)
    ck(bad == ["t1"], "deadline_hours=12 violates 4/8 rule")
    b5 = dict(base, deadline_hours=8, deadline="2026-11-07T16:00:00+00:00",
              heartbeat_at="2026-11-07T11:50:00+00:00")
    top, chair, flag, bad = check_sla([dict(b5)], t0)
    ck(top == chair == flag == bad == [], "healthy 8h task clean")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        got, why = live_rows()
    except Exception as e:  # noqa: BLE001 - fail closed, report cause
        print("SKIP live DB unavailable: %s" % e)
        return 2
    if got is None:
        print("SKIP %s" % why)
        return 2
    statuses, tasks = got
    violations = check_claims(statuses)
    top, chair, flag, bad = check_sla(tasks, now_utc())
    violations += ["task %s deadline_hours not in (4,8)" % t for t in bad]
    violations += ["task %s overdue +2h (TOP)" % t for t in top]
    violations += ["task %s no progress 30min" % t for t in flag]
    if chair:
        fn = write_outbox("queue-sla-chair-%s.json" % now_utc().strftime("%Y%m%d"),
                          {"kind": "queue-sla-chair", "tasks": chair,
                           "rule": "overdue +6h past deadline -> report chair"})
        violations += ["task %s overdue +6h (CHAIR, outbox %s)" % (t, fn) for t in chair]
    if violations:
        print("FAIL " + "; ".join(violations))
        return 1
    print("PASS claims<=3, sla 4/8h, no +2h/+6h overdue, no 30min-stale heartbeat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
