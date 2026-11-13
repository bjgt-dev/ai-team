#!/usr/bin/env python3
"""dispatch_task.py - PRODUCTION dispatch entry point with TaskContract gate enforced (M3 exit item 1, CHAIR-20261105-18, 2026-11-05).

OpenClaw (dispatch) MUST use this script to insert ANY contract-bearing task
(llm_doc / script / code) into production central_tasks. There is no other
enforced path: queue_api has no insert endpoint, and ad-hoc psql inserts
bypass the gate by construction.

Flow (cutover51 W1 single throat):
  1. Load the contract JSON file.
  2. Wrapper-level check: non-empty task_id (gate itself does not check it).
  3. Kill-switch check (cutover51 W2.2): state/l3-20261105-contracts/GATE-DISABLED
     is NO LONGER a bypass. If the file exists, dispatch is REFUSED (fail
     closed) and the presence is written to the audit log for the kill-switch
     monitor. The only way to disable a gate is a DB gate_overrides row with
     a mandatory expiry, written via /approve.
  4. Enforced mode: run the deterministic gate as a subprocess:
       python3 scripts/taskcontract_gate.py --insert-guard <contract.json>
     exit 0 -> continue; exit 1 -> INSERT REFUSED, pending record, exit 1.
  5. Single throat: call gate-svc POST /gate/dispatch with the contract.
     The service runs taskcontract-strict, decision-code-gate, preflight,
     m5-switch-guard, dispatch-gate-hub and the duration limit, then writes
     a dispatch_receipts row. The DB trigger refuses any code/script INSERT
     into READY without a matching receipt (24h, same contract_hash).
     gate-svc unreachable or refusing -> NO insert, fail closed.
  6. On insert, the row task_id is a fresh UUID generated HERE (the receipt
     is keyed by it); the full contract is embedded in the payload plus
     payload.contract_hash (canonical sha256, same algorithm as gate-svc).
     code tasks also get top-level payload.workdir AND the central_tasks.workdir
     column; the directory itself is created ONLY at claim time (/v1/claim),
     never at dispatch (W2.4/2.4).
  7. Idempotency key (cutover51 W3): "contract:" + sha256(contract_sha256 +
     "|attempt:N")[:32]; re-dispatch with a new attempt number gets a new key,
     terminal rows no longer block new attempts forever.
  8. Every attempt is appended to state/m3-exit-20261105/dispatch-audit.log.jsonl.

Out of scope (documented, NOT gated here - see DISPATCH-RULE-20261105.md):
  timer_submit / patrol_submit cron submitters carry hardcoded minimal
  payloads (type timer / type patrol) with no 8-field contract; forcing
  them through this gate would refuse every timer and patrol row and
  starve the C2 loop. They are fixed system tasks, not OpenClaw dispatch,
  and are left untouched.

Usage:
  python3 scripts/dispatch_task.py --contract <contract.json>
  Exit 0 = inserted READY; 1 = gate refused (pending record written);
  2 = usage / duplicate / DB error (no insert).
DSN only from state/.cq-dsn, no DSN => fail closed, no insert.
Stdlib + psycopg only. No network, no LLM, no spend.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid

BASE = "/home/ubuntu/ai-team"
GATE_SCRIPT = BASE + "/scripts/taskcontract_gate.py"
GATE_SVC_DISPATCH = os.environ.get("GATE_SVC_URL", "http://127.0.0.1:8093") + "/gate/dispatch"
DSN_FILE = BASE + "/state/.cq-dsn"
KILL_SWITCH = BASE + "/state/l3-20261105-contracts/GATE-DISABLED"
PENDING_DIR = BASE + "/state/m3-exit-20261105/pending"
AUDIT_LOG = BASE + "/state/m3-exit-20261105/dispatch-audit.log.jsonl"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ALLOWED_LANES = {"P0", "biz", "infra"}


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _audit(entry):
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_dsn():
    try:
        with open(DSN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def main():
    ap = argparse.ArgumentParser(description="Production dispatch with TaskContract gate (M3 exit 1)")
    ap.add_argument("--contract", required=True, help="contract JSON file (8-field TaskContract + task_id)")
    args = ap.parse_args()
    cpath = args.contract
    try:
        with open(cpath, encoding="utf-8") as f:
            raw = f.read()
        contract = json.loads(raw)
    except Exception as exc:
        print("DISPATCH REFUSED: cannot load contract %s: %s" % (cpath, exc))
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": None,
                "gate": "not_run", "result": "refused_unloadable", "reason": str(exc)[:200]})
        return 1
    if not isinstance(contract, dict):
        print("DISPATCH REFUSED: contract is not a JSON object")
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": None,
                "gate": "not_run", "result": "refused_not_object"})
        return 1
    task_id = contract.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        print("DISPATCH REFUSED: missing/invalid task_id (wrapper requirement; gate does not check task_id)")
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": str(task_id)[:80],
                "gate": "not_run", "result": "refused_no_task_id"})
        return 1
    # 2026-11-13 factory repair: generative llm_doc must not carry a long
    # input.content. content >= 10 chars classifies the task as PRE-GENERATED
    # (queue_api/c2-executor claim it; pull_once writes content verbatim and
    # never writes output_path). Tonight's N1 attempt 1 stuffed a 374-char
    # description into content and was quarantined 3x. A contract that really
    # is pre-generated must declare "pre_generated": true; anything else with
    # content >= 10 chars is refused so the author removes it or marks it.
    _ttype0 = contract.get("task_type")
    _inp0 = contract.get("input") if isinstance(contract.get("input"), dict) else {}
    _content0 = _inp0.get("content")
    if _ttype0 == "llm_doc" and isinstance(_content0, str) and len(_content0) >= 10 and not contract.get("pre_generated"):
        print("DISPATCH REFUSED: llm_doc %s has input.content %d chars but no pre_generated:true; generative tasks must not carry content (it misclassifies as pre-generated and output_path is never written). Remove content or set pre_generated:true." % (task_id, len(_content0)))
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": "not_run", "result": "refused_llm_content", "content_chars": len(_content0)})
        return 1
    # Cutover51 W2.2: GATE-DISABLED is no longer a bypass. Its presence is a
    # kill-switch alarm condition: refuse the dispatch (fail closed) and let
    # the kill-switch monitor (gate-wiring-audit.py) pick it up from the audit log.
    if os.path.exists(KILL_SWITCH):
        print("DISPATCH REFUSED: kill-switch file present %s (cutover51: file bypass removed; remove the file and record a DB gate_overrides row if a gate must be disabled)" % KILL_SWITCH)
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": "kill-switch-file", "result": "refused_kill_switch_file"})
        return 1
    gate_out = ""
    gate_mode = "enforced"
    try:
        r = subprocess.run([sys.executable, GATE_SCRIPT, "--insert-guard", cpath],
                           capture_output=True, text=True, timeout=30)
        gate_out = ((r.stdout or "").strip() + " " + (r.stderr or "").strip()).strip()
    except Exception as exc:
        print("DISPATCH REFUSED: gate could not run: %s" % exc)
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": "error", "result": "refused_gate_error", "reason": str(exc)[:200]})
        return 1
    if r.returncode != 0:
        os.makedirs(PENDING_DIR, exist_ok=True)
        prec = {"task_id": task_id, "status": "pending",
                "gate": "taskcontract_gate.py --insert-guard",
                "gate_exit": r.returncode, "gate_output": gate_out,
                "contract_file": cpath, "ts": _ts(),
                "note": "Refused by production gate; NEVER inserted into central_tasks; fix contract and re-dispatch."}
        with open(os.path.join(PENDING_DIR, task_id + ".json"), "w", encoding="utf-8") as f:
            json.dump(prec, f, ensure_ascii=False, indent=2, sort_keys=True)
        print("DISPATCH REFUSED: %s (task stays pending, no DB write)" % gate_out)
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": "refused", "gate_exit": r.returncode, "gate_output": gate_out,
                "result": "refused_pending"})
        return 1
    # Cutover51 W1 single throat: gate-svc /gate/dispatch issues the receipt
    # the DB trigger requires. Row UUID is generated here so the receipt can
    # be keyed by it before the INSERT.
    def _canonical_hash(obj):
        return hashlib.sha256(
            json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    contract_hash = _canonical_hash(contract)
    row_uuid = str(uuid.uuid4())
    try:
        _body = json.dumps({"task_id": row_uuid, "contract_hash": contract_hash,
                            "contract": contract}).encode()
        _req = urllib.request.Request(GATE_SVC_DISPATCH, data=_body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(_req, timeout=90) as _resp:
            _disp = json.loads(_resp.read().decode())
        if not _disp.get("ok"):
            print("DISPATCH REFUSED: gate-svc /gate/dispatch refused: %s" % json.dumps(_disp, ensure_ascii=False)[:500])
            _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                    "gate": "gate/dispatch", "result": "refused_dispatch_gate",
                    "detail": str(_disp)[:500]})
            return 1
        print("DISPATCH gate-svc: receipt issued mode=%s gates=%s" % (_disp.get("mode"), ",".join(_disp.get("gates_passed") or [])))
    except Exception as exc:
        print("DISPATCH REFUSED: gate-svc /gate/dispatch unreachable/failed: %s (fail closed, no insert)" % exc)
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": "gate/dispatch", "result": "refused_dispatch_unreachable", "reason": str(exc)[:200]})
        return 2
    dsn = get_dsn()
    if not dsn:
        print("DISPATCH REFUSED: no DSN, fail closed (no insert)")
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": gate_mode, "result": "refused_no_dsn"})
        return 2
    task_type = contract.get("task_type")
    payload = {"type": task_type, "task_id": task_id,
               "input": contract.get("input"), "output_path": contract.get("output_path"),
               "evidence": contract.get("evidence"), "pass_line": contract.get("pass_line"),
               "fail_action": contract.get("fail_action"), "verifier": contract.get("verifier"),
               "forbidden": contract.get("forbidden"),
               "contract": contract,
               "five_part": contract.get("five_part"),
               "variants": contract.get("variants"),
               "contract_sha256": hashlib.sha256(raw.encode()).hexdigest(),
               "contract_hash": contract_hash,
               "m3_gate": "enforced-20261105"}
    inp = contract.get("input")
    if task_type == "script" and isinstance(inp, dict) and isinstance(inp.get("command"), str):
        payload["command"] = inp["command"]
    if task_type == "llm_doc":
        if isinstance(inp, dict) and isinstance(inp.get("content"), str):
            payload["content"] = inp["content"]
        elif isinstance(inp, str):
            payload["content"] = inp
    if task_type == "code":
        wd = contract.get("workdir")
        if not wd and isinstance(inp, dict):
            wd = inp.get("workdir")
        payload["workdir"] = wd
    if task_type == "eval":
        # G5 (Eval OS phase 2, 2026-11-11): eval tasks need top-level
        # workdir + eval ids in the payload for /v1/claim (CLAIM_WHERE
        # requires payload->>'workdir' non-empty) and pull_once (reads
        # top-level eval_id / eval_run_id / eval per the G5 plan payload
        # shape sec 2.7). The contract carries them (G4 gate 2 requires
        # non-empty eval_id + eval_run_id on the contract); dispatch
        # copies them to payload top level. Absent workdir -> payload
        # workdir None -> never claimed (fail-closed at claim).
        wd = contract.get("workdir")
        if not wd and isinstance(inp, dict):
            wd = inp.get("workdir")
        payload["workdir"] = wd
        payload["eval_id"] = contract.get("eval_id")
        payload["eval_run_id"] = contract.get("eval_run_id")
        payload["eval"] = contract.get("eval")
    lane = contract.get("lane") if contract.get("lane") in ALLOWED_LANES else "infra"
    # long-task-heartbeat (2026-11-06): default deadline now()+4h; a contract
    # may request 1..48h via deadline_hours (complex tasks: 8h).
    dh = contract.get("deadline_hours")
    deadline_hours = dh if isinstance(dh, int) and 1 <= dh <= 48 else 4
    import psycopg
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Thorsten Ball Rule 7 (2026-11-07): one task, one workdir.
                # A code task whose normalized workdir is already used by
                # another non-terminal task is refused (no DB write).
                if task_type == "code":
                    _wd = contract.get("workdir")
                    if not _wd and isinstance(contract.get("input"), dict):
                        _wd = contract.get("input", {}).get("workdir")
                    if isinstance(_wd, str) and _wd.strip():
                        _wdn = os.path.expanduser(_wd.strip()).rstrip("/")
                        cur.execute(
                            "SELECT payload->>'task_id', status, payload->>'workdir' FROM central_tasks "
                            "WHERE status IN ('READY','CLAIMED','VERIFYING') "
                            "AND payload->>'workdir' IS NOT NULL")
                        _conflict = None
                        for _otid, _s, _owd in cur.fetchall():
                            if _otid == task_id:
                                continue
                            if isinstance(_owd, str) and os.path.expanduser(_owd.strip()).rstrip("/") == _wdn:
                                _conflict = (_otid, _s)
                                break
                        if _conflict:
                            print("DISPATCH REFUSED: workdir %s already used by active task %s status=%s (Rule 7: one task, one workdir)" % (_wd, _conflict[0], _conflict[1]))
                            _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                                    "gate": gate_mode, "result": "refused_workdir_conflict",
                                    "workdir": _wd, "conflict_task": _conflict[0], "conflict_status": _conflict[1]})
                            return 2
                # 2026-11-13 factory repair: duplicate check skips terminal
                # rows (DONE/QUARANTINED). A terminal row must never block a
                # re-dispatch of the same task_id (N1 attempt 1 QUARANTINED
                # forced a payload task_id rename to re-dispatch). Only a live
                # row (READY/CLAIMED/VERIFYING/RETRY_WAIT) blocks.
                cur.execute("SELECT task_id, status FROM central_tasks WHERE payload->>'task_id' = %s AND status NOT IN ('DONE','QUARANTINED') ORDER BY created_at DESC LIMIT 1", (task_id,))
                dup = cur.fetchone()
                if dup:
                    print("DISPATCH REFUSED: duplicate task_id %s already in central_tasks as %s status=%s" % (task_id, dup[0], dup[1]))
                    _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                            "gate": gate_mode, "result": "refused_duplicate", "existing_status": dup[1]})
                    return 2
                # queue-harden step B (2026-11-06) + cutover51 W3: idempotency
                # key is now hash(contract content + attempt). A new attempt
                # gets a new key, so a terminal row can never block re-dispatch.
                _attempt = contract.get("attempt", 1)
                if not isinstance(_attempt, int) or _attempt < 1:
                    _attempt = 1
                idem_key = "contract:" + hashlib.sha256(
                    (hashlib.sha256(raw.encode()).hexdigest() + "|attempt:" + str(_attempt)).encode()
                ).hexdigest()[:32]
                # gate-svc receipt (S6, 2026-11-10): code/script tasks must carry a
                # kb receipt so the DONE trigger can verify it. Fail closed if
                # gate-svc is unreachable.
                receipt_id = None
                if task_type in ("code", "script"):
                    try:
                        _sr = subprocess.run(
                            ["/home/ubuntu/ai-team/scripts/hubctl", "search",
                             "dispatcher", task_id, task_id],
                            capture_output=True, text=True, timeout=60)
                        _sout = json.loads(_sr.stdout or "{}")
                        for _rc in (_sout.get("receipts") or []):
                            if _rc.get("kind") == "kb":
                                receipt_id = _rc.get("receipt_id")
                        if not receipt_id:
                            print("DISPATCH REFUSED: gate-svc /search gave no kb receipt for %s" % task_id)
                            _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                                    "gate": gate_mode, "result": "refused_no_receipt"})
                            return 2
                    except Exception as _e:
                        print("DISPATCH REFUSED: gate-svc /search failed for %s: %s" % (task_id, _e))
                        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                                "gate": gate_mode, "result": "refused_receipt_error"})
                        return 2
                # plan_id (S4): chairman-approved plan, from contract.plan_id if present.
                # Set at INSERT when available; otherwise it must be set (via
                # /approve + UPDATE) before a worker claims, or the lease
                # trigger will refuse the claim (gate-deny).
                plan_id = contract.get("plan_id")
                if plan_id and not isinstance(plan_id, str):
                    plan_id = None
                # Plan A (g5-planid-dispatch-fix-20261111): code/script tasks
                # must carry a plan_id that REALLY EXISTS in plan_approvals.
                # Existence-only (same semantics as the S4 lease trigger):
                # never the 24h TTL / slug rule, or dispatches would be
                # refused after the approval's 24h window. Fail closed, no
                # INSERT, audit-logged. (Prerequisite: GRANT SELECT ON
                # plan_approvals TO cq_timer, 2026-11-11.)
                if task_type in ("code", "script"):
                    if not plan_id:
                        print("DISPATCH REFUSED: code/script task %s has no plan_id (g5-planid-dispatch-fix-20261111)" % task_id)
                        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                                "gate": gate_mode, "result": "refused_no_plan_id"})
                        return 2
                    cur.execute("SELECT 1 FROM plan_approvals WHERE plan_id = %s", (plan_id,))
                    if cur.fetchone() is None:
                        print("DISPATCH REFUSED: plan_id %s not in plan_approvals for %s" % (plan_id, task_id))
                        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                                "gate": gate_mode, "result": "refused_plan_not_approved", "plan_id": plan_id})
                        return 2
                _wd_col = payload.get("workdir") if isinstance(payload.get("workdir"), str) else None
                if receipt_id and plan_id:
                    cur.execute("INSERT INTO central_tasks (task_id, title, payload, lane, min_bytes, status, deadline, idempotency_key, receipt_id, plan_id, workdir) VALUES (%s, %s, %s::jsonb, %s, %s, 'READY', now() + (%s || ' hours')::interval, %s, %s, %s, %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                                (row_uuid, task_id, json.dumps(payload), lane, 10, deadline_hours, idem_key, receipt_id, plan_id, _wd_col))
                elif receipt_id:
                    cur.execute("INSERT INTO central_tasks (task_id, title, payload, lane, min_bytes, status, deadline, idempotency_key, receipt_id, workdir) VALUES (%s, %s, %s::jsonb, %s, %s, 'READY', now() + (%s || ' hours')::interval, %s, %s, %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                                (row_uuid, task_id, json.dumps(payload), lane, 10, deadline_hours, idem_key, receipt_id, _wd_col))
                elif plan_id:
                    cur.execute("INSERT INTO central_tasks (task_id, title, payload, lane, min_bytes, status, deadline, idempotency_key, plan_id, workdir) VALUES (%s, %s, %s::jsonb, %s, %s, 'READY', now() + (%s || ' hours')::interval, %s, %s, %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                                (row_uuid, task_id, json.dumps(payload), lane, 10, deadline_hours, idem_key, plan_id, _wd_col))
                else:
                    cur.execute("INSERT INTO central_tasks (task_id, title, payload, lane, min_bytes, status, deadline, idempotency_key, workdir) VALUES (%s, %s, %s::jsonb, %s, %s, 'READY', now() + (%s || ' hours')::interval, %s, %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                                (row_uuid, task_id, json.dumps(payload), lane, 10, deadline_hours, idem_key, _wd_col))
                got = cur.fetchone()
                if got is None:
                    cur.execute("SELECT task_id, status FROM central_tasks WHERE idempotency_key = %s", (idem_key,))
                    dup = cur.fetchone()
                    print("DISPATCH REFUSED: duplicate task_id %s already in central_tasks as %s status=%s (idempotency key %s)" % (task_id, dup[0], dup[1], idem_key))
                    _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                            "gate": gate_mode, "result": "refused_duplicate", "existing_status": dup[1]})
                    return 2
                row_id = got[0]
                conn.commit()
                # Cutover51 W2.4: workdir is created ONLY at claim time
                # (/v1/claim). Dispatch-time creation removed; the column
                # central_tasks.workdir was written at INSERT above.
    except Exception as exc:
        print("DISPATCH REFUSED: DB error, no insert committed: %s" % exc)
        _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id,
                "gate": gate_mode, "result": "refused_db_error", "reason": str(exc)[:200]})
        return 2
    print("DISPATCH OK: gate=%s; inserted %s status=READY row=%s" % (gate_mode, task_id, row_id))
    _audit({"ts": _ts(), "contract_file": cpath, "task_id": task_id, "row_id": str(row_id),
            "gate": "allowed", "gate_output": gate_out,
            "result": "inserted_ready"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
