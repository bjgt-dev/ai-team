#!/usr/bin/env python3
"""goal_driver DRAFT -- TEST ONLY (2026-11-04).

Scope guard (hard):
- TEST database only: sqlite file given by --db, default under the TEST dir
  state/goal-driver-test-20261104/central_queue_test.db on the hub.
- NEVER touches production central_queue, production executor, verifier,
  guard, submitter, or any production cron/timer. This script installs NO
  cron/timer itself. A 2-minute cadence is a PROPOSED cron line printed
  in --help only; installing it needs a separate production approval.
- NEVER reads or copies secrets: no key, OCID, fingerprint, password or
  token value is read, printed or stored. Receipts carry only sha256 of
  deliverable bytes + byte count, never content secrets.
- No Oracle instance is created, no money is spent.

Behaviour (one cycle):
1. Evaluate every criterion of the goal by running its stored check_sql
   (SELECT-only, single statement, else the criterion is marked failed
   with reason 'unsafe-check-sql' and no task is fabricated from it).
2. If all criteria pass -> status='done', write one done-receipt JSON
   (idempotent: a second cycle logs 'already-done', writes nothing new).
3. Else, if no open task exists for the goal, call the butler_decide-style
   generator (deterministic, rules-first, in-process; it does NOT call any
   gateway/model and does NOT write state directly) and validate its
   output with a strict schema (<=5 tasks, every task carries a contract:
   do / deliverable / verify / min_bytes and/or schema_fields). Only
   schema-valid output is written to test_tasks. Invalid output -> zero
   tasks written, cycle logged as 'generator-rejected'.
4. Blocked handling: if the passed-criterion count is unchanged from the
   previous cycle and the goal is still not done, blocked_cycles += 1.
   When blocked_cycles >= --blocked-threshold (default 3) and no report
   has been sent for this blocked episode, write EXACTLY ONE report
   (test reports dir + goal_reports row, hub-first test analogue of the
   alert route: test file first, never production incidents/inbox, TG
   never used here) with a default action. Later cycles in the same
   episode write no further report until progress resets the episode.

Usage (hub, test dir):
  python3 scripts/goal_driver_test_20261104.py --init --db <test.db>
  python3 scripts/goal_driver_test_20261104.py --cycle --db <test.db> --goal <id>
  python3 scripts/goal_driver_test_20261104.py --complete-task --db <test.db> \
      --task <task_id> --receipt-file <path>   # receipt = deliverable file
  PROPOSED (NOT installed) 2-min cadence, only after separate approval:
  */2 * * * * python3 <hub>/scripts/goal_driver_test_20261104.py --cycle \
      --db <hub>/state/goal-driver-test-20261104/central_queue_test.db \
      --goal TEST-TEAM-AUTO-20261104 >> <test-log> 2>&1
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|PRAGMA|ATTACH|REPLACE|VACUUM)\b",
    re.IGNORECASE)
SECRET_PATTERNS = ("-----BEGIN", "ocid1.", "AKIA")


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_secret_leak(text):
    return any(p in text for p in SECRET_PATTERNS)


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  goal_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','done','blocked')),
  blocked_cycles INTEGER NOT NULL DEFAULT 0,
  blocked_reported INTEGER NOT NULL DEFAULT 0,
  last_passed_count INTEGER NOT NULL DEFAULT -1,
  report_count INTEGER NOT NULL DEFAULT 0,
  done_receipt_path TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_criteria (
  goal_id TEXT NOT NULL, criterion_id TEXT NOT NULL,
  description TEXT NOT NULL, check_sql TEXT NOT NULL,
  expected TEXT NOT NULL DEFAULT '1',
  current_value TEXT, passed INTEGER NOT NULL DEFAULT 0,
  checked_at TEXT,
  PRIMARY KEY (goal_id, criterion_id)
);
CREATE TABLE IF NOT EXISTS test_probes (
  probe TEXT PRIMARY KEY, value_num REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_tasks (
  task_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
  criterion_id TEXT NOT NULL, title TEXT NOT NULL,
  contract_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'READY'
    CHECK (status IN ('READY','RUNNING','DONE','FAILED')),
  receipt_path TEXT, receipt_sha256 TEXT, receipt_bytes INTEGER,
  verified_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_driver_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  goal_id TEXT NOT NULL, cycle INTEGER NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS goal_reports (
  report_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
  kind TEXT NOT NULL, default_action TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

SEED_MAIN_GOAL = ("TEST-TEAM-AUTO-20261104", "团队自动化基建完结（测试副本）")
SEED_MAIN_CRITERIA = [
    ("pull_loop", "拉活循环装好",
     "SELECT CASE WHEN value_num>=1 THEN 1 ELSE 0 END FROM test_probes WHERE probe='pull_loop_installed'"),
    ("queue_auto", "队列自动跑",
     "SELECT CASE WHEN value_num>=1 THEN 1 ELSE 0 END FROM test_probes WHERE probe='queue_auto_run_ok'"),
    ("ledger_reconcile", "台账自动对账（差异=0）",
     "SELECT CASE WHEN value_num=0 THEN 1 ELSE 0 END FROM test_probes WHERE probe='ledger_reconcile_diff'"),
    ("model_pool", "模型池资格>=2",
     "SELECT CASE WHEN value_num>=2 THEN 1 ELSE 0 END FROM test_probes WHERE probe='model_pool_qualified'"),
    ("oci_monitor", "OCI监测自己跑",
     "SELECT CASE WHEN value_num>=1 THEN 1 ELSE 0 END FROM test_probes WHERE probe='oci_monitor_selfrun'"),
    ("alert_route", "报警路由先写枢纽再报主聊（非仅TG）",
     "SELECT CASE WHEN (SELECT value_num FROM test_probes WHERE probe='alert_hub_first')>=1 "
     "AND (SELECT value_num FROM test_probes WHERE probe='alert_tg_only')=0 THEN 1 ELSE 0 END"),
]
SEED_MAIN_PROBES = [
    ("pull_loop_installed", 0, "test seed: not installed in test env"),
    ("queue_auto_run_ok", 0, "test seed: no auto run observed in test env"),
    ("ledger_reconcile_diff", 1, "test seed: 1 diff outstanding in test env"),
    ("model_pool_qualified", 0, "test seed: 0 qualified in test env"),
    ("oci_monitor_selfrun", 0, "test seed: not self-running in test env"),
    ("alert_hub_first", 0, "test seed: hub-first not evidenced in test env"),
    ("alert_tg_only", 0, "test seed"),
]

TASK_TEMPLATES = {
    "pull_loop": ("装拉活循环（测试）", "在测试环境装拉活循环并留一次拉取日志"),
    "queue_auto": ("队列自动跑（测试）", "在测试环境跑一次队列自动循环并留运行日志"),
    "ledger_reconcile": ("台账自动对账（测试）", "在测试环境跑一次台账对账并使差异归零"),
    "model_pool": ("模型池资格>=2（测试）", "在测试环境以金丝雀核验至少2个模型池成员"),
    "oci_monitor": ("OCI监测自己跑（测试）", "在测试环境跑一次OCI监测自运行并留数字回执"),
    "alert_route": ("报警路由先写枢纽再报主聊（测试）", "在测试环境走一次先写枢纽文件再汇总的报警"),
}


def init_db(conn, test_dir):
    conn.executescript(SCHEMA)
    now = utcnow()
    conn.execute(
        "INSERT OR IGNORE INTO goals(goal_id,title,status,created_at,updated_at)"
        " VALUES(?,?, 'open',?,?)", (*SEED_MAIN_GOAL, now, now))
    for cid, desc, sql in SEED_MAIN_CRITERIA:
        conn.execute(
            "INSERT OR IGNORE INTO goal_criteria"
            "(goal_id,criterion_id,description,check_sql) VALUES(?,?,?,?)",
            (SEED_MAIN_GOAL[0], cid, desc, sql))
    for probe, val, note in SEED_MAIN_PROBES:
        conn.execute(
            "INSERT OR IGNORE INTO test_probes(probe,value_num,note,observed_at)"
            " VALUES(?,?,?,?)", (probe, val, note, now))
    conn.commit()
    return {"db": "init-ok", "test_dir": test_dir}


def validate_check_sql(sql):
    s = sql.strip()
    if not s.upper().startswith("SELECT"):
        return False, "not SELECT"
    if ";" in s.rstrip(";"):
        return False, "multiple statements"
    if FORBIDDEN_SQL.search(s):
        return False, "forbidden keyword"
    return True, ""


def evaluate(conn, goal_id):
    """Return list of {criterion_id, passed, current_value, reason}."""
    out = []
    rows = conn.execute(
        "SELECT criterion_id, check_sql, expected FROM goal_criteria"
        " WHERE goal_id=? ORDER BY criterion_id", (goal_id,)).fetchall()
    now = utcnow()
    for cid, sql, expected in rows:
        ok, reason = validate_check_sql(sql)
        if not ok:
            val, passed = "unsafe-check-sql", 0
        else:
            try:
                r = conn.execute(sql).fetchone()
                val = "" if r is None else str(r[0])
                passed = 1 if val == expected else 0
            except Exception as e:  # noqa: BLE001 - test check must fail closed
                val, passed, reason = "check-error:%s" % e, 0, "check-error"
        conn.execute(
            "UPDATE goal_criteria SET current_value=?, passed=?, checked_at=?"
            " WHERE goal_id=? AND criterion_id=?", (val, passed, now, goal_id, cid))
        out.append({"criterion_id": cid, "passed": passed,
                    "current_value": val, "reason": "" if ok else reason})
    conn.commit()
    return out


def butler_decide_next_tasks(goal_id, failing, cycle):
    """butler_decide-style generator: rules first, strict JSON out.

    This is the TEST analogue of calling butler_decide: it never calls a
    model/gateway, never writes state itself (the caller validates first,
    then writes), and its output is schema-checked before any write.
    """
    tasks = []
    for crit in failing[:5]:
        cid = crit["criterion_id"]
        title, do = TASK_TEMPLATES.get(
            cid, ("补齐标准 %s（测试）" % cid, "在测试环境补齐该标准并留证据"))
        tasks.append({
            "task_id": "%s-%s-c%d" % (goal_id, cid, cycle),
            "criterion_id": cid, "title": title,
            "contract": {
                "do": do,
                "deliverable": "test evidence file under goal-driver-test-20261104/evidence/",
                "verify": "re-run the criterion check_sql; must return expected value",
                "min_bytes": 200,
                "schema_fields": ["criterion_id", "evidence_path", "sha256", "bytes"],
            }})
    return {"tasks": tasks}


def validate_task_batch(obj, failing_ids):
    """Strict schema check. Returns (ok, reason). Mirrors butler_decide
    strictness: object, bounded list, every contract field present."""
    if not isinstance(obj, dict) or not isinstance(obj.get("tasks"), list):
        return False, "not an object with tasks list"
    tasks = obj["tasks"]
    if not (1 <= len(tasks) <= 5):
        return False, "task count outside 1..5"
    seen = set()
    for t in tasks:
        if not isinstance(t, dict):
            return False, "task not an object"
        if not t.get("task_id") or t["task_id"] in seen:
            return False, "task_id missing or duplicate"
        seen.add(t["task_id"])
        if t.get("criterion_id") not in failing_ids:
            return False, "task criterion_id not in failing set"
        if not t.get("title") or not isinstance(t["title"], str):
            return False, "title missing"
        c = t.get("contract")
        if not isinstance(c, dict):
            return False, "contract missing"
        for f in ("do", "deliverable", "verify"):
            if not c.get(f) or not isinstance(c[f], str):
                return False, "contract.%s missing" % f
        if check_secret_leak(json.dumps(c, ensure_ascii=False)):
            return False, "contract contains secret-like pattern"
        mb = c.get("min_bytes")
        sf = c.get("schema_fields")
        if not ((isinstance(mb, int) and mb >= 1)
                or (isinstance(sf, list) and len(sf) >= 1)):
            return False, "contract needs min_bytes>=1 or schema_fields"
    return True, ""


def log(conn, goal_id, cycle, action, detail=""):
    conn.execute(
        "INSERT INTO goal_driver_log(ts,goal_id,cycle,action,detail)"
        " VALUES(?,?,?,?,?)", (utcnow(), goal_id, cycle, action, detail))
    conn.commit()


def run_cycle(conn, goal_id, test_dir, blocked_threshold, cycle_override=None):
    goal = conn.execute(
        "SELECT status, blocked_cycles, blocked_reported, last_passed_count,"
        " report_count FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
    if goal is None:
        return {"error": "goal not found: %s" % goal_id}
    status, blocked_cycles, blocked_reported, last_passed, report_count = goal
    if status == "done":
        n = conn.execute(
            "SELECT COUNT(*) FROM goal_driver_log WHERE goal_id=?", (goal_id,)).fetchone()[0]
        log(conn, goal_id, n + 1, "already-done", "no new tasks, no new report")
        return {"goal_id": goal_id, "action": "already-done", "passed": "all"}
    cycle = (cycle_override if cycle_override is not None else
             conn.execute("SELECT COUNT(*) FROM goal_driver_log WHERE goal_id=?",
                          (goal_id,)).fetchone()[0] + 1)
    crit = evaluate(conn, goal_id)
    passed = sum(c["passed"] for c in crit)
    failing = [c for c in crit if not c["passed"]]
    # Blocked-episode bookkeeping: progress = passed count grew.
    if last_passed >= 0 and passed == last_passed:
        blocked_cycles += 1
    else:
        blocked_cycles, blocked_reported = 0, 0
    conn.execute(
        "UPDATE goals SET last_passed_count=?, blocked_cycles=?,"
        " blocked_reported=? WHERE goal_id=?",
        (passed, blocked_cycles, blocked_reported, goal_id))
    conn.commit()

    if not failing:
        receipt_path = os.path.join(test_dir, "receipts", goal_id + ".json")
        os.makedirs(os.path.join(test_dir, "receipts"), exist_ok=True)
        receipt = {"goal_id": goal_id, "status": "done", "criteria": crit,
                   "ts": utcnow(), "report_count": report_count}
        canon = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        receipt["receipt_sha256"] = hashlib.sha256(canon.encode()).hexdigest()
        with open(receipt_path, "w") as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=True)
        conn.execute(
            "UPDATE goals SET status='done', done_receipt_path=?, updated_at=?"
            " WHERE goal_id=?", (receipt_path, utcnow(), goal_id))
        conn.commit()
        log(conn, goal_id, cycle, "done",
            "all %d criteria passed; receipt=%s" % (len(crit), receipt_path))
        return {"goal_id": goal_id, "action": "done", "passed": passed,
                "total": len(crit), "receipt": receipt_path,
                "report_count": report_count}

    open_n = conn.execute(
        "SELECT COUNT(*) FROM test_tasks WHERE goal_id=? AND status IN"
        " ('READY','RUNNING')", (goal_id,)).fetchone()[0]
    action_detail = "passed %d/%d; open_tasks=%d; blocked_cycles=%d" % (
        passed, len(crit), open_n, blocked_cycles)
    report = None
    if open_n == 0:
        batch = butler_decide_next_tasks(goal_id, failing, cycle)
        ok, reason = validate_task_batch(batch, {c["criterion_id"] for c in failing})
        if not ok:
            log(conn, goal_id, cycle, "generator-rejected", reason)
            return {"goal_id": goal_id, "action": "generator-rejected",
                    "reason": reason, "passed": passed, "total": len(crit)}
        now = utcnow()
        for t in batch["tasks"]:
            conn.execute(
                "INSERT OR IGNORE INTO test_tasks(task_id,goal_id,criterion_id,"
                "title,contract_json,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'READY',?,?)",
                (t["task_id"], goal_id, t["criterion_id"], t["title"],
                 json.dumps(t["contract"], ensure_ascii=False, sort_keys=True),
                 now, now))
        conn.commit()
        action_detail += "; tasks_created=%d" % len(batch["tasks"])
    # Single-report rule for a blocked episode.
    if blocked_cycles >= blocked_threshold and not blocked_reported:
        os.makedirs(os.path.join(test_dir, "reports"), exist_ok=True)
        rid = "TEST-REPORT-%s-%d" % (goal_id, cycle)
        default_action = ("暂缓并汇总呈报主聊；不擅自执行生产变更；"
                          "默认动作附后，待主聊拍板后再动")
        conn.execute(
            "INSERT OR IGNORE INTO goal_reports(report_id,goal_id,kind,"
            "default_action,created_at) VALUES(?,?,?,?,?)",
            (rid, goal_id, "blocked", default_action, utcnow()))
        with open(os.path.join(test_dir, "reports", rid + ".md"), "w") as f:
            f.write("# 测试报警（单次上报）\n\n- goal: %s\n- kind: blocked\n"
                    "- blocked_cycles: %d (threshold %d)\n"
                    "- default_action: %s\n"
                    "- note: TEST ONLY, not production incidents/inbox, no TG\n"
                    % (goal_id, blocked_cycles, blocked_threshold, default_action))
        conn.execute(
            "UPDATE goals SET blocked_reported=1, report_count=report_count+1,"
            " updated_at=? WHERE goal_id=?", (utcnow(), goal_id))
        conn.commit()
        report = rid
        action_detail += "; report_once=%s" % rid
    log(conn, goal_id, cycle, "cycle", action_detail)
    return {"goal_id": goal_id, "action": "cycle", "passed": passed,
            "total": len(crit), "failing": [c["criterion_id"] for c in failing],
            "blocked_cycles": blocked_cycles, "report": report,
            "report_count": report_count + (1 if report else 0)}


def complete_task(conn, task_id, receipt_file):
    row = conn.execute(
        "SELECT goal_id, contract_json, status FROM test_tasks WHERE task_id=?",
        (task_id,)).fetchone()
    if row is None:
        return {"error": "task not found: %s" % task_id}
    goal_id, contract_json, status = row
    if status == "DONE":
        return {"task_id": task_id, "action": "already-done"}
    contract = json.loads(contract_json)
    if not os.path.isfile(receipt_file):
        return {"error": "receipt file missing: %s" % receipt_file}
    size = os.path.getsize(receipt_file)
    if size < int(contract.get("min_bytes") or 1):
        return {"error": "receipt bytes %d < min_bytes %s" % (size, contract.get("min_bytes"))}
    sha = sha256_file(receipt_file)
    conn.execute(
        "UPDATE test_tasks SET status='DONE', receipt_path=?, receipt_sha256=?,"
        " receipt_bytes=?, verified_by='test-harness-receipt-check',"
        " updated_at=? WHERE task_id=?",
        (receipt_file, sha, size, utcnow(), task_id))
    conn.commit()
    return {"task_id": task_id, "action": "completed", "bytes": size,
            "sha256": sha, "goal_id": goal_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--test-dir", default="")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--complete-task", action="store_true")
    ap.add_argument("--goal", default="")
    ap.add_argument("--task", default="")
    ap.add_argument("--receipt-file", default="")
    ap.add_argument("--blocked-threshold", type=int, default=3)
    ap.add_argument("--cycle-n", type=int, default=0)
    args = ap.parse_args()
    test_dir = args.test_dir or os.path.dirname(os.path.abspath(args.db))
    conn = sqlite3.connect(args.db)
    try:
        if args.init:
            print(json.dumps(init_db(conn, test_dir), ensure_ascii=False))
        elif args.cycle:
            if not args.goal:
                print(json.dumps({"error": "--goal required"})); return 2
            print(json.dumps(run_cycle(conn, args.goal, test_dir,
                                       args.blocked_threshold,
                                       args.cycle_n or None),
                             ensure_ascii=False))
        elif args.complete_task:
            if not args.task or not args.receipt_file:
                print(json.dumps({"error": "--task and --receipt-file required"})); return 2
            print(json.dumps(complete_task(conn, args.task, args.receipt_file),
                             ensure_ascii=False))
        else:
            print(json.dumps({"error": "need --init, --cycle or --complete-task"})); return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
