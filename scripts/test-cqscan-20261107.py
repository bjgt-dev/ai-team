#!/usr/bin/env python3
"""test-cqscan-20261107.py — tests for the 2026-11-07 cq-scan landings (L1-L6).

Runs on the hub (paths /home/ubuntu/ai-team/scripts). Stdlib only.
NO production writes:
  - L1 timer/patrol tests run the real submit scripts against a FAKE
    psycopg (in-memory rows) injected into sys.modules; the real gate
    subprocess still runs on the real cron-contract templates (read-only).
  - L2/L3/L4/L5 are pure-function tests + CLI tests on temp files.
  - L6 runs the real audit script (read-only SELECTs) and checks its
    report structure.
  - The verdict-auto-dispatch wiring test only exercises REFUSAL paths
    (a 打回 verdict, and a conditional pass whose condition is too short
    to anchor) -- both return before any dispatch/DB write.

Usage: python3 scripts/test-cqscan-20261107.py
Exit 0 = all PASS, 1 = any FAIL (prints which).
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import uuid

SCRIPTS = "/home/ubuntu/ai-team/scripts"
sys.path.insert(0, SCRIPTS)

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print("PASS %s" % name)
    else:
        FAILED.append(name)
        print("FAIL %s %s" % (name, detail))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- fake psycopg
class FakeCursor:
    """In-memory central_tasks: rows are dicts with keys task_id, kind
    ('timer'/'patrol'), timer (name or None), status, idem_key."""

    def __init__(self, rows):
        self.rows = rows
        self._result = []
        self.rowcount = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        self.statements.append(sql)
        if "payload->>'type'='timer'" in sql and "status IN ('READY'" in sql:
            tname = params[0]
            hit = [r for r in self.rows if r.get("timer") == tname
                   and r["status"] in ("READY", "CLAIMED", "RUNNING", "VERIFYING")]
            self._result = [(hit[0]["task_id"],)] if hit else []
        elif "payload->>'type'='patrol'" in sql and "status IN ('READY'" in sql:
            hit = [r for r in self.rows if r.get("kind") == "patrol"
                   and r["status"] in ("READY", "CLAIMED", "RUNNING", "VERIFYING")]
            self._result = [(hit[0]["task_id"],)] if hit else []
        elif "SET idempotency_key=NULL" in sql:
            key = params[0]
            n = 0
            for r in self.rows:
                if r.get("idem_key") == key and r["status"] in ("DONE", "QUARANTINED"):
                    r["idem_key"] = None
                    n += 1
            self.rowcount = n
            self._result = []
        elif sql.strip().startswith("INSERT INTO central_tasks"):
            key = params[-1]
            if any(r.get("idem_key") == key for r in self.rows):
                self._result = []  # ON CONFLICT DO NOTHING
            else:
                tid = str(uuid.uuid4())
                title = params[0]
                kind = "timer" if title.startswith("timer-") else "patrol"
                timer = title[len("timer-"):] if kind == "timer" else None
                self.rows.append({"task_id": tid, "kind": kind, "timer": timer,
                                  "status": "READY", "idem_key": key})
                self._result = [(tid,)]
            self.rowcount = 1 if self._result else 0
        elif "WHERE idempotency_key = %s" in sql:
            key = params[0]
            hit = [r for r in self.rows if r.get("idem_key") == key]
            self._result = [(hit[0]["task_id"], hit[0]["status"])] if hit else []
        else:
            self._result = []
        return None

    def fetchone(self):
        return self._result[0] if self._result else None


class FakeConn:
    def __init__(self, rows):
        self._cur = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cur

    def commit(self):
        return None


# ---------------------------------------------------------------- L1
def test_l1():
    timer = load("timer_submit_test", SCRIPTS + "/timer_submit-20261104.py")
    patrol = load("patrol_submit_test", SCRIPTS + "/patrol_submit-20261104.py")

    # POS 1: DONE row holds timer:gateway_canary -> rotation clears it, insert succeeds.
    seed = [{"task_id": "6546817c-1596-434f-b808-97362ac19e41", "kind": "timer",
             "timer": "gateway_canary", "status": "DONE", "idem_key": "timer:gateway_canary"}]
    fake = types.ModuleType("psycopg")
    conn = FakeConn([dict(r) for r in seed])
    fake.connect = lambda dsn: conn
    old_psycopg, old_argv = sys.modules.get("psycopg"), sys.argv
    sys.modules["psycopg"] = fake
    sys.argv = ["timer_submit-20261104.py", "gateway_canary"]
    try:
        rc = timer.main()
    finally:
        sys.argv = old_argv
        if old_psycopg is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = old_psycopg
    cur = conn._cur
    check("L1 timer DONE-key rotates + inserts", rc == 0 and len(conn._cur.rows) == 2
          and conn._cur.rows[0]["idem_key"] is None
          and conn._cur.rows[1]["idem_key"] == "timer:gateway_canary",
          "rc=%s rows=%s" % (rc, conn._cur.rows))
    check("L1 timer rotation UPDATE only touches terminal rows",
          any("status IN ('DONE','QUARANTINED')" in s for s in cur.statements),
          str(cur.statements))

    # POS 2: QUARANTINED row holds timer:health_3q -> same rotation.
    conn2 = FakeConn([{"task_id": "c72c88da-f119-4dd1-9050-7699ddbf80fc", "kind": "timer",
                       "timer": "health_3q", "status": "QUARANTINED",
                       "idem_key": "timer:health_3q"}])
    fake.connect = lambda dsn: conn2
    sys.modules["psycopg"] = fake
    sys.argv = ["timer_submit-20261104.py", "health_3q"]
    try:
        rc2 = timer.main()
    finally:
        sys.argv = old_argv
        sys.modules.pop("psycopg", None)
    check("L1 timer QUARANTINED-key rotates + inserts",
          rc2 == 0 and len(conn2._cur.rows) == 2
          and conn2._cur.rows[0]["idem_key"] is None,
          "rc=%s rows=%s" % (rc2, conn2._cur.rows))

    # NEG 1: live READY row holds the key -> skip, no UPDATE, no INSERT.
    conn3 = FakeConn([{"task_id": "aaaaaaaa-1111-2222-3333-444444444444", "kind": "timer",
                       "timer": "gateway_canary", "status": "READY",
                       "idem_key": "timer:gateway_canary"}])
    fake.connect = lambda dsn: conn3
    sys.modules["psycopg"] = fake
    sys.argv = ["timer_submit-20261104.py", "gateway_canary"]
    try:
        rc3 = timer.main()
    finally:
        sys.argv = old_argv
        sys.modules.pop("psycopg", None)
    check("L1 timer live READY row still dedups (no insert)",
          rc3 == 0 and len(conn3._cur.rows) == 1
          and not any("SET idempotency_key=NULL" in s for s in conn3._cur.statements)
          and not any(s.strip().startswith("INSERT") for s in conn3._cur.statements),
          "rc=%s rows=%s stmts=%s" % (rc3, conn3._cur.rows, conn3._cur.statements))

    # POS 3: patrol DONE row holds patrol:patrol-c2 -> rotation + insert.
    conn4 = FakeConn([{"task_id": "971354b8-a9b6-4365-a4c7-1d64a84fc5e3", "kind": "patrol",
                       "timer": None, "status": "DONE", "idem_key": "patrol:patrol-c2"}])
    fake.connect = lambda dsn: conn4
    sys.modules["psycopg"] = fake
    sys.argv = ["patrol_submit-20261104.py"]
    try:
        rc4 = patrol.main()
    finally:
        sys.argv = old_argv
        sys.modules.pop("psycopg", None)
    check("L1 patrol DONE-key rotates + inserts",
          rc4 == 0 and len(conn4._cur.rows) == 2
          and conn4._cur.rows[0]["idem_key"] is None,
          "rc=%s rows=%s" % (rc4, conn4._cur.rows))

    # NEG 2: patrol --dry-run touches no DB (gate only).
    r = subprocess.run([sys.executable, SCRIPTS + "/patrol_submit-20261104.py", "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    check("L1 patrol --dry-run ALLOWED, no DB", r.returncode == 0 and "ALLOWED" in r.stdout, r.stdout[:200])
    r = subprocess.run([sys.executable, SCRIPTS + "/timer_submit-20261104.py", "gateway_canary", "--dry-run"],
                       capture_output=True, text=True, timeout=60)
    check("L1 timer --dry-run ALLOWED, no DB", r.returncode == 0 and "ALLOWED" in r.stdout, r.stdout[:200])


# ---------------------------------------------------------------- L2
def test_l2():
    rw = load("retrywait_scan_test", SCRIPTS + "/retrywait-scan-20261105.py")
    now = datetime.datetime.now(datetime.timezone.utc)

    def row(retry, maxr, age_min, fc=None, nb=""):
        upd = (now - datetime.timedelta(minutes=age_min)).isoformat()
        payload = json.dumps({"failure_class": fc} if fc else {})
        return ("t1", "title", "RETRY_WAIT", str(retry), str(maxr),
                now.isoformat(), upd, nb, payload)

    check("L2 decide PROMOTE (F-UNDIAG, retry 1/3, due)",
          rw.decide_action(row(1, 3, 30), now) == "PROMOTE")
    check("L2 decide BLOCKED (age > 2h)",
          rw.decide_action(row(1, 3, 200), now) == "BLOCKED")
    check("L2 decide REWORK (F-CONTRACT never auto-promotes)",
          rw.decide_action(row(1, 3, 30, fc="F-CONTRACT"), now) == "REWORK")
    check("L2 decide TERMINAL_FAILED (retry >= max)",
          rw.decide_action(row(3, 3, 10), now) == "TERMINAL_FAILED")
    check("L2 decide FALLBACK (F-503, retry spent)",
          rw.decide_action(row(1, 3, 30, fc="F-503"), now) == "FALLBACK")
    # WAIT: F-429 backoff 30s not yet due AND not_before in the future.
    # (not_before NULL alone means due -- the backoff only gates when a
    # not_before is set -- so the not-due case needs a future not_before.)
    _nb_future = (now + datetime.timedelta(minutes=10)).isoformat()
    check("L2 decide WAIT (F-429, backoff not due, not_before future)",
          rw.decide_action(row(0, 3, 0, fc="F-429", nb=_nb_future), now) is None)
    sql = rw.terminal_quarantine_sql("abc-123", "BLOCKED-overdue-2h", now.isoformat())
    check("L2 terminal SQL moves RETRY_WAIT->QUARANTINED with guard",
          "QUARANTINED" in sql and "status='RETRY_WAIT'" in sql
          and "retrywait_terminal" in sql and "abc-123" in sql, sql)
    check("L2 terminal SQL never touches DONE rows",
          "DONE" not in sql, sql)
    check("L2b claimer check ignores commented-out cq_claim line",
          rw.claimer_status("# 2026-11-07 dec-004: disable cq_claim cq-timer\n"
                            "* * * * * /home/ubuntu/ai-team/scripts/cq-run-20261102.sh sweep\n")
          == (False, False))
    check("L2b claimer check finds a live claim line",
          rw.claimer_status("* * * * * /home/ubuntu/ai-team/scripts/cq-run-20261102.sh claim cq-timer\n")
          == (True, False))
    check("L2b claimer check flags PAUSED",
          rw.claimer_status("* * * * * PAUSED /home/ubuntu/ai-team/scripts/cq-run-20261102.sh claim cq-timer\n")
          == (True, True))
    # argparse --help does not print defaults; check the declared default
    # in the source instead (a relative default was the cron bug).
    _src = open(SCRIPTS + "/retrywait-scan-20261105.py", encoding="utf-8").read()
    check("L2 --out default is absolute (cron-safe)",
          "default='/home/ubuntu/ai-team/state/retrywait-scan-20261105/last-run.json'" in _src
          and "default='state/retrywait-scan" not in _src)


# ---------------------------------------------------------------- L3
def test_l3():
    wd = load("verdict_watchdog_test", SCRIPTS + "/verdict-dispatch-watchdog.py")
    check("L3 normalize iteration==iter",
          wd.normalize_task("kids-iteration4-20261107") == wd.normalize_task("kids-iter4-20261107"))
    check("L3 QUARANTINED row does NOT count as dispatched (kb bug)",
          wd.is_dispatched("kb-retrieval-20261106",
                           [("kb-retrieval-20261106", "QUARANTINED")]) is False)
    check("L3 DONE row does NOT count as dispatched",
          wd.is_dispatched("kb-retrieval-20261106",
                           [("kb-retrieval-20261106", "DONE")]) is False)
    check("L3 RETRY_WAIT row DOES count as dispatched",
          wd.is_dispatched("kids-iteration4-p0-20261107",
                           [("kids-iteration4-p0-20261107", "RETRY_WAIT")]) is True)
    check("L3 READY row DOES count as dispatched",
          wd.is_dispatched("some-task",
                           [("some-task-p0-20261107", "READY")]) is True)
    check("L3 no row at all -> not dispatched",
          wd.is_dispatched("some-task", []) is False)


# ---------------------------------------------------------------- L4
def test_l4():
    vc = load("validate_contract_test", SCRIPTS + "/validate_contract.py")
    five = {"goal": "g", "constraint": "c", "stop_when": "s", "must_not": "m", "report": "r"}
    good_code = {"task_id": "test-code-1", "task_type": "code",
                 "workdir": "~/code-agent-pilot/workdir/x", "input": "do x",
                 "output_path": "state/x.md", "evidence": "pytest 1/1 + sha256",
                 "pass_line": "tests green", "fail_action": "RETRY_WAIT",
                 "verifier": "c2-verifier", "forbidden": ["no spend"],
                 "five_part": five, "variants": []}
    check("L4 current code contract PASSES", vc.check(good_code) == [], str(vc.check(good_code)))
    bad_code = dict(good_code)
    del bad_code["workdir"]
    check("L4 current code without workdir FAILS",
          any("workdir" in e for e in vc.check(bad_code)), str(vc.check(bad_code)))
    good_timer = {"task_id": "timer-x", "task_type": "timer", "input": {"timer": "x"},
                  "output_path": "state/x.md", "evidence": "sha256",
                  "pass_line": "ok", "fail_action": "RETRY_WAIT",
                  "verifier": "c2-verifier", "forbidden": ["no spend"]}
    check("L4 current timer contract PASSES (no five_part needed)",
          vc.check(good_timer) == [], str(vc.check(good_timer)))
    legacy = {"task_id": "canary-e2e-contract-1", "task_type": "llm_generate",
              "source": "ceo_canary_e2e", "objective": "output one line",
              "output_file": "artifacts/canary-contract.md",
              "acceptance_criteria": [{"type": "file_exists", "target": "artifacts/canary-contract.md"}],
              "timeout_s": 120, "max_retries": 0, "idempotency_key": "canary-e2e-contract-1",
              "evidence_required": ["receipts.jsonl", "manifest.json", "acceptance_self_check.json"],
              "created_at": "2026-11-07T10:00:00+08:00",
              "negative_constraints": [{"id": "NC-01", "source_text": "s", "rule": "r",
                                        "acceptance_check": "a"}]}
    check("L4 legacy llm_generate contract still PASSES", vc.check(legacy) == [], str(vc.check(legacy)))
    bad_legacy = dict(legacy)
    del bad_legacy["negative_constraints"]
    check("L4 legacy missing negative_constraints FAILS",
          any("negative_constraints" in e for e in vc.check(bad_legacy)), str(vc.check(bad_legacy)))
    check("L4 unknown task_type FAILS",
          len(vc.check({"task_id": "x", "task_type": "wizard"})) > 0)
    with tempfile.TemporaryDirectory() as td:
        good_p = os.path.join(td, "good.json")
        bad_p = os.path.join(td, "bad.json")
        json.dump(good_code, open(good_p, "w"))
        json.dump(bad_code, open(bad_p, "w"))
        r = subprocess.run([sys.executable, SCRIPTS + "/validate_contract.py", good_p],
                           capture_output=True, text=True, timeout=30)
        check("L4 CLI current contract exit 0", r.returncode == 0 and "PASS" in r.stdout, r.stdout[:200])
        r = subprocess.run([sys.executable, SCRIPTS + "/validate_contract.py", bad_p],
                           capture_output=True, text=True, timeout=30)
        check("L4 CLI bad contract exit 1", r.returncode == 1 and "FAIL" in r.stdout, r.stdout[:200])


# ---------------------------------------------------------------- L5
def test_l5():
    cc = load("verdict_conditions_test", SCRIPTS + "/verdict-conditions-check-20261107.py")
    conds = ["条件一：把 A 改成 B，并附测试输出原文。",
             "条件二：交付物须列全 retrieve.py、测试与索引。"]
    verdict = {"verdict": "有条件通过", "task": "kb-retrieval-20261106", "conditions": conds}
    contract_ok = {"task_id": "kb-x", "task_type": "code", "input": "执行。条件：\n- " + "\n- ".join(conds),
                   "pass_line": "条件逐项落实", "evidence": "sha256",
                   "forbidden": ["no spend"],
                   "five_part": {"goal": "g", "constraint": "c", "stop_when": "s",
                                 "must_not": "m", "report": "r"}}
    check("L5 all conditions present PASSES", cc.check(verdict, contract_ok) == [],
          str(cc.check(verdict, contract_ok)))
    contract_drop = dict(contract_ok)
    contract_drop["input"] = "执行。条件：\n- " + conds[0]
    errs = cc.check(verdict, contract_drop)
    check("L5 dropped condition FAILS (names condition[1])",
          len(errs) == 1 and "condition[1]" in errs[0], str(errs))
    check("L5 conditional pass with no conditions FAILS",
          len(cc.check({"verdict": "有条件通过", "conditions": []}, contract_ok)) > 0)
    check("L5 plain pass with no conditions PASSES",
          cc.check({"verdict": "通过", "conditions": []}, contract_ok) == [])
    check("L5 打回 verdict FAILS",
          len(cc.check({"verdict": "打回", "conditions": []}, contract_ok)) > 0)
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "v.json")
        cp = os.path.join(td, "c.json")
        json.dump(verdict, open(vp, "w"), ensure_ascii=False)
        json.dump(contract_ok, open(cp, "w"), ensure_ascii=False)
        r = subprocess.run([sys.executable, SCRIPTS + "/verdict-conditions-check-20261107.py",
                            "--verdict", vp, "--contract", cp],
                           capture_output=True, text=True, timeout=30)
        check("L5 CLI exit 0 on pass", r.returncode == 0 and "CHECK PASS" in r.stdout, r.stdout[:200])
        json.dump(contract_drop, open(cp, "w"), ensure_ascii=False)
        r = subprocess.run([sys.executable, SCRIPTS + "/verdict-conditions-check-20261107.py",
                            "--verdict", vp, "--contract", cp],
                           capture_output=True, text=True, timeout=30)
        check("L5 CLI exit 1 on dropped condition", r.returncode == 1 and "CHECK FAIL" in r.stdout, r.stdout[:200])
    # Wiring: auto-dispatch must REFUSE before any DB write when the
    # conditions check fails. A one-character condition cannot anchor.
    with tempfile.TemporaryDirectory() as td:
        vp = os.path.join(td, "v.json")
        json.dump({"verdict": "有条件通过", "task": "wire-test-20261107",
                   "conditions": ["好"], "request": "", "evidence": []},
                  open(vp, "w"), ensure_ascii=False)
        r = subprocess.run([sys.executable, SCRIPTS + "/verdict-auto-dispatch.py", vp],
                           capture_output=True, text=True, timeout=120)
        check("L5 auto-dispatch refuses unanchorable condition (no DB write)",
              r.returncode == 1 and "conditions check" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:300])
        vp2 = os.path.join(td, "v2.json")
        json.dump({"verdict": "打回", "task": "wire-test-20261107", "conditions": []},
                  open(vp2, "w"), ensure_ascii=False)
        r = subprocess.run([sys.executable, SCRIPTS + "/verdict-auto-dispatch.py", vp2],
                           capture_output=True, text=True, timeout=60)
        check("L5 auto-dispatch refuses 打回 verdict",
              r.returncode == 1 and "not pass" in (r.stdout + r.stderr), (r.stdout + r.stderr)[:200])


# ---------------------------------------------------------------- L6
def test_l6():
    audit = load("cq_audit_test", SCRIPTS + "/cq-queue-contract-audit-20261107.py")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "audit.json")
        rc = audit.main(["--out", out])
        rep = json.load(open(out))
        secs = rep.get("sections", {})
        check("L6 audit exit 0 + report written", rc == 0 and os.path.exists(out))
        check("L6 audit queue section present", secs.get("queue", {}).get("total", 0) > 600,
              str(secs.get("queue"))[:200])
        check("L6 audit transition triggers present",
              set(secs.get("transitions", {}).get("expected", [])) <= set(secs.get("transitions", {}).get("triggers", [])),
              str(secs.get("transitions")))
        gates = secs.get("contracts", {}).get("cron_contracts_gate", {})
        check("L6 audit all 11 cron-contracts gate ALLOWED",
              len(gates) == 11 and all(v == "ALLOWED" for v in gates.values()), str(gates))
        check("L6 audit verdicts section present", len(secs.get("verdicts", {})) >= 5,
              str(secs.get("verdicts"))[:200])
        check("L6 audit decisions section present", "pending" in secs.get("decisions", {}),
              str(secs.get("decisions")))


def main():
    test_l1()
    test_l2()
    test_l3()
    test_l4()
    test_l5()
    test_l6()
    print("\n%d passed, %d failed: %s" % (len(PASSED), len(FAILED), FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
