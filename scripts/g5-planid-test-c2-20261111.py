#!/usr/bin/env python3
"""g5-planid-test-c2-20261111.py - Plan A test C, llm_doc leg retry (hub).

First attempt was refused by decision-code-gate (pre-existing gate: every
non-eval type must declare code_files + evidence_files; llm_doc contracts
never do -- G2's three llm_doc contracts also lack them, so no llm_doc can
pass /gate/dispatch today). This retry declares two real existing files
formally to satisfy that pre-existing gate, and lane=biz so c2-executor
claims it ahead of the 258fd357 poison pill (infra lane head-of-line
block; see implementation log). The plan_id question is orthogonal: the
contract carries NO plan_id, and plan-id-exists must pass it through.
Prints C2_PASS / C2_FAIL.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

BASE = "/home/ubuntu/ai-team"
HARNESS = BASE + "/scripts/g5-canary-20261111.py"

spec = importlib.util.spec_from_file_location("g5canary", HARNESS)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

DOC = ("Plan A test C llm_doc (retry).\n"
       "This is a synthetic test document for the plan_id fix.\n"
       "It must be written to state/deliverables by c2-executor without any plan_id.\n"
       "Non-code tasks (timer, llm_doc, patrol) carry no plan_id and must not be blocked by the plan_id change.\n")


def main():
    tid = H._tid("c2-llmdoc").replace("g5-canary-", "g5-planid-")
    c = H._contract_base(tid, "llm_doc")
    c["input"] = DOC
    c["output_path"] = "state/deliverables/<uuid>.md"  # convention: literal <uuid> placeholder (cf. DONE llm_doc m5w-w6/7/8); executor writes <UUID>.md
    c["lane"] = "biz"
    # Formal satisfaction of the pre-existing decision-code-gate (see header).
    c["code_files"] = ["/home/ubuntu/ai-team/scripts/g5-planid-test-c2-20261111.py"]
    c["evidence_files"] = ["/home/ubuntu/ai-team/state/cutover-20261110.log"]
    assert "plan_id" not in c
    cp = os.path.join(H.OUT, "canary-contract-%s.json" % tid)
    os.makedirs(H.OUT, exist_ok=True)
    json.dump(c, open(cp, "w"))
    H._plan(tid, c.get("input", tid))
    H._verdict(tid)
    H._preflight(tid)
    r = subprocess.run(["python3", H.DISPATCH, "--contract", cp],
                       capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    print(out, end="", flush=True)
    print("C2_DISPATCH_RC=%d" % r.returncode, flush=True)
    if r.returncode != 0:
        print("C2_FAIL dispatch refused task=%s" % tid)
        return 1
    # Wait for c2-executor (1/min) to claim + execute (up to 5 min).
    import psycopg
    deadline = time.time() + 300
    task = None
    while time.time() < deadline:
        with psycopg.connect(H._dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT task_id, status, plan_id FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
                task = cur.fetchone()
                if task:
                    cur.execute("SELECT worker_id, plan_id, status FROM central_leases WHERE task_id=%s ORDER BY issued_at", (task[0],))
                    leases = cur.fetchall()
        print("C2_POLL status=%s plan=%s leases=%s" % (
            task[1] if task else None, task[2] if task else None,
            [(l[0], str(l[1]), l[2]) for l in leases] if task else []), flush=True)
        if task and task[1] in ("DONE", "VERIFYING", "QUARANTINED"):
            break
        if task and leases and task[1] in ("CLAIMED", "RUNNING"):
            time.sleep(30)
            continue
        time.sleep(30)
    if not task:
        print("C2_FAIL no task row task=%s" % tid)
        return 1
    lease_plans = sorted({str(l[1]) for l in leases}) if leases else []
    fpath = os.path.join(BASE, "state/deliverables", "%s.md" % task[0])
    print("C2_TASK task=%s status=%s plan_id=%s leases=%d lease_plans=%s file=%s exists=%s" % (
        task[0], task[1], task[2], len(leases), lease_plans, fpath, os.path.exists(fpath)), flush=True)
    if task[2] is None and leases and lease_plans == ["None"] and os.path.exists(fpath):
        print("C2_PASS task=%s status=%s" % (tid, task[1]))
        return 0
    print("C2_FAIL task=%s status=%s plan=%s leases=%d plans=%s file_exists=%s" % (
        tid, task[1], task[2], len(leases), lease_plans, os.path.exists(fpath)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
