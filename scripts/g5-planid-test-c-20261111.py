#!/usr/bin/env python3
"""g5-planid-test-c-20261111.py - Plan A test C (run on the hub).

Non-code tasks must NOT be blocked by the plan_id change:
- timer canary_b via timer_submit (sanctioned submitter, no plan_id).
- llm_doc via pipeline-dispatch (full contract, no plan_id).
- patrol via patrol_submit (regression: pre-cutover claim behaviour).
Claim path for these types is c2-executor (direct DB lease, every minute),
NOT /v1/claim (which only serves script/code per CLAIM_WHERE) -- the
decision package §11 wording "claim 200" is corrected here to the real
claim path; the intent (lease created, plan_id stays NULL) is unchanged.
Prints C_PASS / C_FAIL per item with evidence.
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

DOC = ("Plan A test C llm_doc.\n"
       "This is a synthetic test document for the plan_id fix.\n"
       "It must be written to state/deliverables by c2-executor without any plan_id.\n"
       "Non-code tasks (timer, llm_doc, patrol) carry no plan_id and must not be blocked.\n")


def _run(cmd, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    print("CMD rc=%d: %s" % (r.returncode, " ".join(cmd)), flush=True)
    print((r.stdout or "") + (r.stderr or ""), end="", flush=True)
    return r


def _row(tid):
    import psycopg
    with psycopg.connect(H._dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id, title, status, plan_id FROM central_tasks WHERE payload->>'task_id'=%s OR title=%s ORDER BY created_at DESC LIMIT 1", (tid, tid))
            t = cur.fetchone()
            if not t:
                return None, []
            cur.execute("SELECT worker_id, plan_id, status FROM central_leases WHERE task_id=%s ORDER BY issued_at", (t[0],))
            return t, cur.fetchall()


def main():
    results = {}
    # 1. timer canary_b
    r1 = _run(["python3", BASE + "/scripts/timer_submit-20261104.py", "canary_b"])
    results["timer_submit_rc"] = r1.returncode
    # 2. patrol
    r2 = _run(["python3", BASE + "/scripts/patrol_submit-20261104.py"])
    results["patrol_submit_rc"] = r2.returncode
    # 3. llm_doc via pipeline-dispatch
    tid = H._tid("c-llmdoc").replace("g5-canary-", "g5-planid-")
    c = H._contract_base(tid, "llm_doc")
    c["input"] = DOC
    c["output_path"] = "/home/ubuntu/ai-team/state/deliverables/%s.md" % tid
    assert "plan_id" not in c
    cp = os.path.join(H.OUT, "canary-contract-%s.json" % tid)
    os.makedirs(H.OUT, exist_ok=True)
    json.dump(c, open(cp, "w"))
    H._plan(tid, c.get("input", tid))
    H._verdict(tid)
    H._preflight(tid)
    r3 = _run(["python3", H.DISPATCH, "--contract", cp], timeout=900)
    results["llmdoc_dispatch_rc"] = r3.returncode
    results["llmdoc_tid"] = tid
    print("C_SUBMITTED llmdoc_tid=%s timer_rc=%d patrol_rc=%d llmdoc_rc=%d" % (
        tid, r1.returncode, r2.returncode, r3.returncode), flush=True)
    # 4. wait for c2-executor (1/min) to claim all three; poll up to 5 min.
    import psycopg
    deadline = time.time() + 300
    seen = {}
    while time.time() < deadline:
        with psycopg.connect(H._dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, payload->>'type', payload->>'timer', status, plan_id "
                    "FROM central_tasks WHERE (payload->>'type'='timer' AND payload->>'timer'='canary_b' "
                    "AND created_at > now() - interval '10 minutes') "
                    "OR (payload->>'type'='patrol' AND created_at > now() - interval '10 minutes') "
                    "OR payload->>'task_id'=%s ORDER BY created_at DESC", (tid,))
                rows = cur.fetchall()
        for row in rows:
            key = (row[1], row[2] or row[0])
            seen[key] = row
        print("C_POLL " + "; ".join("%s/%s=%s plan=%s" % (r[1], (r[2] or str(r[0])[:8]), r[3], r[4]) for r in rows), flush=True)
        if len(seen) >= 3:
            break
        time.sleep(30)
    # 5. verdicts
    ok = True
    for (typ, _), row in sorted(seen.items()):
        task_id = row[0]
        with psycopg.connect(H._dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT worker_id, plan_id, status FROM central_leases WHERE task_id=%s ORDER BY issued_at", (task_id,))
                leases = cur.fetchall()
        lease_plans = sorted({str(l[1]) for l in leases})
        lease_ok = len(leases) > 0 and lease_plans == ["None"]
        print("C_ITEM type=%s task=%s status=%s plan_id=%s leases=%d lease_plans=%s lease_ok=%s" % (
            typ, task_id, row[3], row[4], len(leases), lease_plans, lease_ok), flush=True)
        if row[4] is not None or not lease_ok:
            ok = False
    if r1.returncode != 0 or r2.returncode != 0 or r3.returncode != 0:
        ok = False
        print("C_FAIL submit rc timer=%d patrol=%d llmdoc=%d" % (r1.returncode, r2.returncode, r3.returncode))
    # llm_doc deliverable file (c2-executor writes <UUID>.md)
    t, _ = _row(tid)
    if t:
        p = os.path.join(BASE, "state/deliverables", "%s.md" % t[0])
        print("C_LLMDOC_FILE %s exists=%s" % (p, os.path.exists(p)), flush=True)
    print("C_%s" % ("PASS" if ok and len(seen) >= 3 else "FAIL"))
    return 0 if (ok and len(seen) >= 3) else 1


if __name__ == "__main__":
    sys.exit(main())
