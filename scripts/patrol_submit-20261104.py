#!/usr/bin/env python3
"""ORDER13-A patrol submitter (M5-1 revision, 2026-11-05, CHAIR-20261105-18 residual 1).
Cron only inserts a READY patrol task; C2 executes it.
Idempotent: if a patrol task is already READY/CLAIMED/RUNNING/VERIFYING, skip.
DSN only from state/.cq-dsn, no DSN => fail closed, no task.

M5-1 change (was: hardcoded minimal payload {"type":"patrol"} with no contract;
see state/m3-exit-20261105/1-gate-dispatch-evidence.md §5):
  - patrol now has a FIXED 8-field TaskContract template in
    state/m5-20261105/cron-contracts/patrol.json;
  - before ANY insert, this script runs the deterministic gate as a subprocess:
      python3 scripts/taskcontract_gate.py --insert-guard <template>
    exit != 0 => REFUSE, no DB write of any kind, exit 1;
  - on allow, the full contract is embedded in the payload (payload.contract)
    plus payload.contract_sha256 and payload.m3_gate=enforced-20261105.
  - --dry-run: load template + run gate only, no DB access, no insert.
    exit 0 = gate ALLOWED, 1 = gate REFUSED / template problem.
No network, no LLM, no spend. Stdlib + psycopg only.
"""
import hashlib
import json
import os
import subprocess
import sys

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
GATE_SCRIPT = "/home/ubuntu/ai-team/scripts/taskcontract_gate.py"
CONTRACT_PATH = "/home/ubuntu/ai-team/state/m5-20261105/cron-contracts/patrol.json"


def get_dsn():
    if not os.access(DSN_FILE, os.R_OK):
        return ""
    for line in open(DSN_FILE):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def load_contract():
    """Return (contract_dict, raw_bytes, error_str)."""
    try:
        with open(CONTRACT_PATH, "rb") as f:
            raw = f.read()
        contract = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return None, b"", "cannot load contract %s: %s" % (CONTRACT_PATH, exc)
    if not isinstance(contract, dict):
        return None, b"", "contract %s is not a JSON object" % CONTRACT_PATH
    if contract.get("task_type") != "patrol":
        return None, b"", "contract %s task_type != patrol" % CONTRACT_PATH
    return contract, raw, ""


def gate_check():
    """Run the deterministic gate on the fixed template. Return (allowed, output)."""
    try:
        r = subprocess.run([sys.executable, GATE_SCRIPT, "--insert-guard", CONTRACT_PATH],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return False, "gate could not run: %s" % exc
    out = ((r.stdout or "").strip() + " " + (r.stderr or "").strip()).strip()
    return (r.returncode == 0), out


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    contract, raw, err = load_contract()
    if err:
        print("patrol-submit: REFUSED %s (no DB write)" % err)
        return 1
    allowed, gate_out = gate_check()
    if not allowed:
        print("patrol-submit: REFUSED by gate: %s (no DB write)" % gate_out)
        return 1
    if dry_run:
        print("patrol-submit: DRYRUN ALLOWED gate=%s" % gate_out)
        return 0
    dsn = get_dsn()
    if not dsn:
        print("patrol-submit: no DSN, refusing")
        return 0
    import psycopg
    payload = {"type": "patrol",
               "contract": contract,
               "contract_sha256": hashlib.sha256(raw).hexdigest(),
               "m3_gate": "enforced-20261105"}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id FROM central_tasks WHERE payload->>'type'='patrol' AND status IN ('READY','CLAIMED','RUNNING','VERIFYING') LIMIT 1")
            row = cur.fetchone()
            if row:
                print("patrol-submit: skip, existing " + str(row[0]))
                return 0
            # queue-harden step B (2026-11-06): idempotency key.
            idem_key = "patrol:patrol-c2"
            # cq-scan 2026-11-07 (L1): rotate the static key. Same bug as
            # timer_submit: the DONE row 971354b8 holding
            # "patrol:patrol-c2" made every later INSERT hit ON CONFLICT
            # DO NOTHING forever (36 skips in logs/patrol.log), so no new
            # patrol task could ever be created. Clear the key on terminal
            # rows only, in the same transaction; live-row dedup is kept
            # by the active-row pre-check above.
            cur.execute(
                "UPDATE central_tasks SET idempotency_key=NULL "
                "WHERE idempotency_key=%s AND status IN ('DONE','QUARANTINED')",
                (idem_key,))
            if cur.rowcount:
                print("patrol-submit: rotated idempotency key %s (cleared %d terminal row(s))" % (idem_key, cur.rowcount))
            cur.execute("INSERT INTO central_tasks (title, payload, lane, min_bytes, deadline, idempotency_key) VALUES (%s, %s::jsonb, %s, %s, now() + interval '4 hours', %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                        ("patrol-c2", json.dumps(payload), "infra", 100, idem_key))
            got = cur.fetchone()
            if got is None:
                cur.execute("SELECT task_id FROM central_tasks WHERE idempotency_key = %s", (idem_key,))
                print("patrol-submit: skip, existing " + str(cur.fetchone()[0]) + " (idempotency key " + idem_key + ")")
                return 0
            tid = got[0]
            conn.commit()
            print("patrol-submit: inserted " + str(tid) + " gate=allowed m3_gate=enforced-20261105")
    return 0


if __name__ == "__main__":
    sys.exit(main())
