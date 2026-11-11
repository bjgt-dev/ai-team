#!/usr/bin/env python3
"""g5-planid-test-b1-20261111.py - Plan A test B1 (run on the hub).

Dispatches a code contract WITHOUT plan_id. Expected: pipeline-dispatch
refuses (taskcontract_gate change-1: "missing field: plan_id"), exit != 0,
and NO task row is created. Prints B1_PASS / B1_FAIL with evidence.
"""
import importlib.util
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
HARNESS = BASE + "/scripts/g5-canary-20261111.py"

spec = importlib.util.spec_from_file_location("g5canary", HARNESS)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def main():
    tid = H._tid("b1-noplan").replace("g5-canary-", "g5-planid-")
    c = H._contract_base(tid, "code")
    # code-base shape, NO plan_id, fresh workdir (Rule 7).
    c.update({
        "workdir": "~/code-agent-pilot/workdir/g5-planid-b1-code",
        "test_command": ["python3", "-m", "pytest", "-q"],
        "test_files": ["/home/ubuntu/ai-team/state/g5-canary-20261111/files/test_add_one.py"],
        "test_baseline_seconds": 60, "allowed_dependencies": [],
        "estimated_seconds": 600,
        "code_files": ["/home/ubuntu/ai-team/state/g5-canary-20261111/files/add_one.py"],
        "evidence_files": ["/home/ubuntu/ai-team/state/cutover-20261110.log"],
        "dependencies": [
            {"hub_path": H.FILES + "/add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-planid-b1-code/add_one.py"},
            {"hub_path": H.FILES + "/test_add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-planid-b1-code/test_add_one.py"}],
        "format": "PULLONCE-BUNDLE-V1",
    })
    assert "plan_id" not in c, "B1 contract must NOT carry plan_id"
    cp = os.path.join(H.OUT, "canary-contract-%s.json" % tid)
    os.makedirs(H.OUT, exist_ok=True)
    json.dump(c, open(cp, "w"))
    H._plan(tid, c.get("input", tid))
    H._verdict(tid)
    H._preflight(tid)
    r = subprocess.run(["python3", H.DISPATCH, "--contract", cp],
                       capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    print(out, end="")
    print("B1_DISPATCH_RC=%d" % r.returncode)
    # No row may be created.
    import psycopg
    with psycopg.connect(H._dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
            n = cur.fetchone()[0]
    print("B1_ROWS_WITH_TID=%d" % n)
    refused = ("missing field: plan_id" in out)
    print("B1_REFUSAL_IS_PLAN_ID=%s" % refused)
    if r.returncode != 0 and refused and n == 0:
        print("B1_PASS task=%s" % tid)
        return 0
    print("B1_FAIL task=%s rc=%d refused_plan_id=%s rows=%d" % (tid, r.returncode, refused, n))
    return 1


if __name__ == "__main__":
    sys.exit(main())
