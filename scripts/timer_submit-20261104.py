#!/usr/bin/env python3
"""ORDER13-6 timer submitter (M5-1 revision, 2026-11-05, CHAIR-20261105-18 residual 1).
Cron only inserts a READY timer task; C2 executes it. Idempotent per timer:
if a task with the same timer is READY/CLAIMED/RUNNING/VERIFYING, skip.
DSN only from state/.cq-dsn, no DSN => fail closed.

M5-1 change (was: hardcoded minimal payload {"type":"timer","timer":name}
with no contract; forcing that through the gate would refuse every timer row
and starve the C2 loop -- see state/m3-exit-20261105/1-gate-dispatch-evidence.md §5):
  - each timer name now has a FIXED 8-field TaskContract template in
    state/m5-20261105/cron-contracts/timer-<name>.json;
  - before ANY insert, this script runs the deterministic gate as a subprocess:
      python3 scripts/taskcontract_gate.py --insert-guard <template>
    exit != 0 => REFUSE, no DB write of any kind, exit 1;
  - on allow, the full contract is embedded in the payload (payload.contract)
    plus payload.contract_sha256 and payload.m3_gate=enforced-20261105,
    so the verifier and the claim predicate see the same fields the gate checked.
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
CONTRACT_DIR = "/home/ubuntu/ai-team/state/m5-20261105/cron-contracts"
ALLOWED = {"gateway_canary", "audit_gateway", "config_commit", "backup_restore_drill", "health_3q", "storage_scan", "storage_confirm", "storage_release", "storage_audit", "canary_b"}


def get_dsn():
    if not os.access(DSN_FILE, os.R_OK):
        return ""
    for line in open(DSN_FILE):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def load_contract(tname):
    """Return (contract_dict, raw_bytes, error_str)."""
    path = os.path.join(CONTRACT_DIR, "timer-%s.json" % tname)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        contract = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return None, b"", "cannot load contract %s: %s" % (path, exc)
    if not isinstance(contract, dict):
        return None, b"", "contract %s is not a JSON object" % path
    if contract.get("task_type") != "timer":
        return None, b"", "contract %s task_type != timer" % path
    inp = contract.get("input")
    if not isinstance(inp, dict) or inp.get("timer") != tname:
        return None, b"", "contract %s input.timer != %s" % (path, tname)
    return contract, raw, ""


def gate_check(tname):
    """Run the deterministic gate on the fixed template. Return (allowed, output)."""
    path = os.path.join(CONTRACT_DIR, "timer-%s.json" % tname)
    try:
        r = subprocess.run([sys.executable, GATE_SCRIPT, "--insert-guard", path],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return False, "gate could not run: %s" % exc
    out = ((r.stdout or "").strip() + " " + (r.stderr or "").strip()).strip()
    return (r.returncode == 0), out


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]
    if len(args) < 1 or args[0] not in ALLOWED:
        print("timer-submit: unknown timer")
        return 2
    tname = args[0]
    contract, raw, err = load_contract(tname)
    if err:
        print("timer-submit: REFUSED %s (no DB write)" % err)
        return 1
    allowed, gate_out = gate_check(tname)
    if not allowed:
        print("timer-submit: REFUSED by gate: %s (no DB write)" % gate_out)
        return 1
    if dry_run:
        print("timer-submit: DRYRUN ALLOWED timer=%s gate=%s" % (tname, gate_out))
        return 0
    dsn = get_dsn()
    if not dsn:
        print("timer-submit: no DSN, refusing")
        return 0
    import psycopg
    payload = {"type": "timer", "timer": tname,
               "contract": contract,
               "contract_sha256": hashlib.sha256(raw).hexdigest(),
               "m3_gate": "enforced-20261105"}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id FROM central_tasks WHERE payload->>'type'='timer' AND payload->>'timer'=%s AND status IN ('READY','CLAIMED','RUNNING','VERIFYING') LIMIT 1", (tname,))
            row = cur.fetchone()
            if row:
                print("timer-submit: skip, existing " + str(row[0]))
                return 0
            # queue-harden step B (2026-11-06): idempotency key "timer:<name>".
            idem_key = "timer:" + tname
            # cq-scan 2026-11-07 (L1): rotate the static key. A terminal row
            # (DONE/QUARANTINED) holding "timer:<name>" made every later
            # INSERT hit ON CONFLICT DO NOTHING forever: 94 skips in
            # state/logs/timer-submit.log, same 3 task_ids
            # (storage_confirm d229deab, gateway_canary 6546817c,
            # health_3q c72c88da), so those timers could never fire again.
            # Clearing the key on terminal rows only, in the same
            # transaction as the INSERT, keeps dedup for live rows (the
            # active-row pre-check above already returned for those)
            # while letting the next cron slot re-fire.
            cur.execute(
                "UPDATE central_tasks SET idempotency_key=NULL "
                "WHERE idempotency_key=%s AND status IN ('DONE','QUARANTINED')",
                (idem_key,))
            if cur.rowcount:
                print("timer-submit: rotated idempotency key %s (cleared %d terminal row(s))" % (idem_key, cur.rowcount))
            # Cutover51 W3.6: expired non-terminal rows. A non-terminal row
            # (READY/CLAIMED/RUNNING/VERIFYING/RETRY_WAIT) past deadline still
            # holding this timer's key blocks every re-fire via ON CONFLICT.
            # Clear the key AND move the row to QUARANTINED (legal per the
            # transition guard for all five states) with a payload note.
            cur.execute(
                "UPDATE central_tasks SET idempotency_key=NULL, status='QUARANTINED', "
                "payload = payload || %s::jsonb "
                "WHERE idempotency_key=%s AND deadline IS NOT NULL AND deadline < now() "
                "AND status IN ('READY','CLAIMED','RUNNING','VERIFYING','RETRY_WAIT')",
                (json.dumps({"deadline_passed_nonterminal": True,
                             "quarantined_by": "timer_submit-20261104.py (cutover51 W3.6)"}), idem_key))
            if cur.rowcount:
                print("timer-submit: quarantined %d expired non-terminal row(s) for %s" % (cur.rowcount, idem_key))
            cur.execute("INSERT INTO central_tasks (title, payload, lane, min_bytes, deadline, idempotency_key) VALUES (%s, %s::jsonb, %s, %s, now() + interval '4 hours', %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                        ("timer-" + tname, json.dumps(payload), "infra", 10, idem_key))
            got = cur.fetchone()
            if got is None:
                cur.execute("SELECT task_id FROM central_tasks WHERE idempotency_key = %s", (idem_key,))
                print("timer-submit: skip, existing " + str(cur.fetchone()[0]) + " (idempotency key " + idem_key + ")")
                return 0
            tid = got[0]
            conn.commit()
            print("timer-submit: inserted " + str(tid) + " timer=" + tname + " gate=allowed m3_gate=enforced-20261105")
    return 0


if __name__ == "__main__":
    sys.exit(main())
