#!/usr/bin/env python3
"""codegen-canary-20261110.py — end-to-end canary for the hub-codegen path.

Dispatches a fixed small code task through pipeline-dispatch with
ENABLE_HUB_CODEGEN=1, waits up to 20 min for DONE, writes a receipt,
exits 0 only on DONE. Uses --allow-no-gate (synthetic canary, no PLAN /
verdict by design, same exemption as pipeline-canary-20261106.py).
"""
import datetime
import json, os, subprocess, sys, time

BASE = "/home/ubuntu/ai-team"
DISPATCH = BASE + "/scripts/pipeline-dispatch-20261106.py"
OUT = BASE + "/state/pipeline-formal-20261106"


def _dsn():
    for line in open(BASE + "/state/.cq-dsn"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def main():
    tid = "codegen-canary-20261110-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
    contract = {"task_id": tid, "task_type": "code",
                "format": "PULLONCE-BUNDLE-V1",
                "five_part": {"goal": "Codegen canary: add_one utility + pytest, generated on hub via ceo-opencode.sh.",
                              "constraint": "stdlib only; pytest on worker-1; verifier DONE",
                              "stop_when": "pytest green on worker-1, verifier DONE",
                              "must_not": "no fake DONE; no manual SQL; no manual file delivery",
                              "report": "receipt in state/pipeline-formal-20261106/"},
                "input": "Write add_one.py with a function add_one(n) returning n+1, and test_add_one.py with pytest tests for 0, 1, -5. Use only the Python standard library.",
                # output_path must be non-empty (taskcontract_gate REQUIRED_FIELDS).
                # It is the deterministic hub path of the file codegen will write;
                # it exists by INSERT time because codegen runs before INSERT.
                "output_path": os.path.join(BASE, "state/codegen-workdir/codegen-canary-20261110/add_one.py"),
                "evidence": "pytest on worker-1 + verifier DONE, receipt in state/pipeline-formal-20261106/",
                "pass_line": "pytest green on worker-1, verifier DONE, no manual steps.",
                "fail_action": "RETRY_WAIT with verbatim reason, no manual DONE flip.",
                "verifier": "c2-verifier + pipeline contract test",
                "forbidden": ["no fake DONE", "no manual SQL", "no manual file delivery"],
                "workdir": "~/code-agent-pilot/workdir/codegen-canary-20261110"}
    cp = os.path.join(OUT, "codegen-canary-contract-%s.json" % tid)
    os.makedirs(OUT, exist_ok=True)
    json.dump(contract, open(cp, "w"))
    env = dict(os.environ)
    env["ENABLE_HUB_CODEGEN"] = "1"
    r = subprocess.run(["python3", DISPATCH, "--contract", cp, "--allow-no-gate"],
                       capture_output=True, text=True, timeout=900, env=env)
    print((r.stdout or "") + (r.stderr or ""), end="", flush=True)
    if r.returncode != 0:
        print("CANARY_FAIL dispatch rc=%d" % r.returncode)
        # Propagate rc 3 (codegen SKIP / seat busy) so a retry loop can
        # distinguish "deferred, retry later" from a real failure.
        return r.returncode
    import psycopg
    deadline = time.time() + 20 * 60
    with psycopg.connect(_dsn()) as conn:
        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT status, retry_count FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
                row = cur.fetchone()
            if row:
                print("CANARY_POLL status=%s retry=%s t=%.0fs" % (row[0], row[1], time.time()), flush=True)
                if row[0] == "DONE":
                    print("CANARY_OK task=%s DONE" % tid)
                    return 0
                if row[0] == "QUARANTINED":
                    print("CANARY_FAIL task=%s QUARANTINED" % tid)
                    return 1
            time.sleep(30)
    print("CANARY_FAIL timeout 20min task=%s" % tid)
    return 1


if __name__ == "__main__":
    sys.exit(main())
