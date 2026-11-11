#!/usr/bin/env python3
"""gate-wiring-audit-db.py -- cutover51 A2: DB-based gate wiring audit.

Reconciles three sources, all from the DB or the live code tree:
  1. gate_registry (DB): every gate must have canary_id + last_block_ts.
     Empty last_block_ts = NOT-WIRED (the gate has never blocked in enforce).
  2. canary_ledger (DB): each gate's canary_id must have at least one row
     (refusal or allow evidence) -- the canary must exist, not just be named.
  3. Code tree: each gate_id must have a code anchor -- a gate script file,
     a gate-svc dispatch entry, or a named gate function -- found by grep.
     Registry gates with no code anchor, or code gates with no registry row,
     are reported as drift.
Output: stdout summary + JSON to state/logs/gate-wiring-audit-db.log.
Exit 0 only when every registry gate is wired (canary row + last_block_ts)
and registry count == code gate count. (A3: this script only READS the
ledger/registry; it never writes canary_ledger or last_block_ts.)
Usage: python3 scripts/gate-wiring-audit-db.py [--outbox]
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

BASE = os.path.expanduser("~/ai-team")
SCRIPTS = os.path.join(BASE, "scripts")
STATE = os.path.join(BASE, "state")
OUTBOX = os.path.join(STATE, "outbox")
LOG = os.path.join(STATE, "logs", "gate-wiring-audit-db.log")

# gate_id -> code anchor(s): (kind, pattern) searched in the code tree.
CODE_ANCHORS = {
    "cron-flock-perjob": [("file", "cron_flock.sh")],
    "cron-inventory-strict": [("file", "cron-inventory-check-20261107.py")],
    "decision-code-gate": [("file", "decision-code-gate.py")],
    "decision-queue-api": [("file", "gate-svc.py"), ("grep", "/decision")],
    "dispatch-gate-hub": [("file", "dispatch-gate-hub.py")],
    "dispatch-receipt": [("grep", "dispatch_receipt_trg"), ("grep", "/gate/dispatch")],
    "duration-limit": [("grep", "duration-limit"), ("grep", "estimated_seconds.*1200")],
    "health-heartbeat": [("file", "health_3q-20261104.py"), ("grep", "heartbeats")],
    "idempotency-rotation": [("file", "timer_submit-20261104.py"), ("grep", "idempotency_key")],
    "kill-switch-monitor": [("grep", "kill_switch_check")],
    "lease-fence": [("file", "cq_common.py"), ("grep", "lease_epoch")],
    "m4-guards": [("file", "m4-antirecurrence-guard-20261107.py")],
    "m5-switch-guard": [("file", "m5-switch-guard.py"), ("file", "check-m5-switch.sh")],
    "plan-id-exists": [("grep", "plan-id-exists")],
    "preflight-gate": [("file", "preflight-gate-20261107.py")],
    "source-of-truth": [("file", "sop-mirror-check.py"), ("grep", "source-of-truth")],
    "taskcontract-strict": [("file", "taskcontract_gate.py")],
    "verifier-rerun": [("file", "c2-verifier-20261104.py"), ("grep", "_gate3_rerun")],
    "watchdog-registry": [("file", "orphan-disposition-20261110.py"), ("grep", "watchdog_registry")],
}


def dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    raise RuntimeError("CQ_DSN not found")


def code_anchor_ok(anchors):
    for kind, pat in anchors:
        if kind == "file":
            if os.path.isfile(os.path.join(SCRIPTS, pat)):
                return True, pat
        else:
            r = subprocess.run(["grep", "-rl", "--include=*.py", "--include=*.sh",
                                "--exclude-dir=__pycache__", pat, SCRIPTS],
                               capture_output=True, text=True, timeout=60)
            if r.stdout.strip():
                return True, pat + " in " + os.path.basename(r.stdout.splitlines()[0])
    return False, ""


def main():
    outbox = "--outbox" in sys.argv
    import psycopg
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT gate_id, owner, call_site, mode, canary_id, last_block_ts FROM public.gate_registry ORDER BY gate_id")
            regs = cur.fetchall()
            cur.execute("SELECT DISTINCT gate FROM public.canary_ledger")
            canary_gates = {r[0] for r in cur.fetchall()}
    results = []
    for gate_id, owner, call_site, mode, canary_id, last_block in regs:
        anchor_ok, anchor = code_anchor_ok(CODE_ANCHORS.get(gate_id, []))
        canary_row = bool(canary_id) and canary_id in canary_gates
        wired = bool(last_block)
        if not anchor_ok:
            verdict = "NOCODE"
        elif not canary_id or not canary_row:
            verdict = "NOCANARY"
        elif not wired:
            verdict = "NOT-WIRED"
        else:
            verdict = "WIRED"
        results.append({"gate_id": gate_id, "mode": mode, "canary_id": canary_id,
                        "canary_row": canary_row, "last_block_ts": last_block.isoformat() if last_block else None,
                        "code_anchor": anchor, "verdict": verdict})
    code_gate_count = len(CODE_ANCHORS)
    report = {"ts": datetime.now(timezone.utc).isoformat(),
              "registry_gates": len(regs), "code_gates": code_gate_count,
              "wired": sum(1 for r in results if r["verdict"] == "WIRED"),
              "not_wired": sum(1 for r in results if r["verdict"] == "NOT-WIRED"),
              "no_canary": sum(1 for r in results if r["verdict"] == "NOCANARY"),
              "no_code": sum(1 for r in results if r["verdict"] == "NOCODE"),
              "results": results}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    print("gate-wiring-audit-db: registry=%d code=%d WIRED=%d NOT-WIRED=%d NOCANARY=%d NOCODE=%d" % (
        report["registry_gates"], code_gate_count, report["wired"],
        report["not_wired"], report["no_canary"], report["no_code"]))
    for r in results:
        print("  [%s] %-22s mode=%-7s canary=%s row=%s last_block=%s anchor=%s" % (
            r["verdict"], r["gate_id"], r["mode"], r["canary_id"], r["canary_row"],
            r["last_block_ts"] or "-", r["code_anchor"] or "-"))
    if outbox and (report["not_wired"] or report["no_canary"] or report["no_code"]):
        os.makedirs(OUTBOX, exist_ok=True)
        path = os.path.join(OUTBOX, "gate-wiring-audit-db-%s.md" % datetime.now(timezone.utc).strftime("%Y%m%d-%H%M"))
        with open(path, "w") as f:
            f.write("# gate-wiring-audit-db (DB 审计)\n\n")
            for r in results:
                if r["verdict"] != "WIRED":
                    f.write("- [%s] %s mode=%s canary=%s last_block=%s\n" % (
                        r["verdict"], r["gate_id"], r["mode"], r["canary_id"], r["last_block_ts"] or "-"))
        print("alert written: " + path)
    return 0 if (report["wired"] == len(regs) and len(regs) == code_gate_count) else 1


if __name__ == "__main__":
    sys.exit(main())
