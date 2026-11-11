#!/usr/bin/env python3
"""cq-queue-contract-audit-20261107.py — cq-scan L6: repeatable carpet audit.

The 2026-11-07 carpet scan of the central queue + contract templates was
done by hand (ad-hoc SQL + greps). This script re-runs the same checks
deterministically so the next scan is one command, not a new investigation.
READ-ONLY: SELECTs + subprocess reads (gate, crontab) only. It never writes
to the database; --out only writes its own JSON report file.

Checks (each section reports found/expected + evidence):
  queue: totals by status and by payload type; READY/CLAIMED live rows.
  idempotency: static keys (timer:<name>, patrol:patrol-c2) held by a
    DONE/QUARANTINED row -- each such key blocks that timer/patrol from
    ever firing again until the key is rotated (timer_submit /
    patrol_submit now rotate it at insert; this section proves it).
  retrywait: RETRY_WAIT rows (retry/max/failure_class/age) + whether any
    automatic exit exists (cq_claim cron enabled? retrywait-scan cron?).
  leases: ACTIVE lease count; distinct lease durations (600s executor /
    1200s queue_api -- doc 02 says 600s, the code disagrees; flagged).
  transitions: the three DB triggers (transition guard, DONE immutable,
    audit) present in information_schema.triggers.
  contracts: all 11 cron-contracts pass taskcontract_gate --insert-guard;
    template inventory counts (schema, mandatory template, sop templates).
  verdicts: every butler-verdicts/*.json -- verdict value, conditions
    count, live-match in central_tasks (DONE/QUARANTINED do NOT count,
    same rule as verdict-dispatch-watchdog.is_dispatched).
  decisions: decision-queue.json pending count + resolved type counts.

Usage:
  python3 scripts/cq-queue-contract-audit-20261107.py [--out <report.json>]
Exit 0 = audit completed (findings are data, not failures); 1 = cannot
read DSN / DB unreachable. Stdlib + psycopg only.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
GATE = BASE + "/scripts/taskcontract_gate.py"
CRON_CONTRACTS = BASE + "/state/m5-20261105/cron-contracts"
VERDICT_DIR = BASE + "/state/butler-verdicts"
DQ_FILE = BASE + "/state/decision-queue.json"

sys.path.insert(0, BASE + "/scripts")
try:
    # The watchdog file name contains hyphens, so it cannot be imported
    # by module name; load it from its path instead.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "verdict_dispatch_watchdog", BASE + "/scripts/verdict-dispatch-watchdog.py")
    _wd = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_wd)
except Exception:
    _wd = None


def _dsn():
    try:
        for line in open(DSN_FILE):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _cron_lines():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
        return out.stdout.splitlines()
    except Exception:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only central-queue + contract audit")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    dsn = _dsn()
    if not dsn:
        print("AUDIT FAIL: no DSN, refusing")
        return 1
    import psycopg
    report = {"audit": "cq-queue-contract-audit-20261107", "sections": {}}
    try:
        with psycopg.connect(dsn) as conn:
            cur = conn.cursor()

            def q(sql, args=None):
                cur.execute(sql, args or ())
                return cur.fetchall()

            total = q("SELECT COUNT(*) FROM central_tasks")[0][0]
            by_status = dict(q("SELECT status, COUNT(*) FROM central_tasks GROUP BY 1"))
            by_type = dict(q("SELECT payload->>'type', COUNT(*) FROM central_tasks GROUP BY 1"))
            report["sections"]["queue"] = {
                "total": total, "by_status": by_status,
                "by_type": {str(k): v for k, v in by_type.items()},
            }
            # NOTE: the LIKE pattern is passed as a parameter -- a bare %
            # inside the SQL string is a psycopg placeholder and raises.
            blocked = q(
                "SELECT idempotency_key, title, status FROM central_tasks "
                "WHERE (idempotency_key LIKE %s OR idempotency_key=%s) "
                "AND status IN ('DONE','QUARANTINED') ORDER BY 1",
                ("timer:%", "patrol:patrol-c2"))
            report["sections"]["idempotency"] = {
                "terminal_held_static_keys": [
                    {"key": k, "title": t, "status": s} for k, t, s in blocked],
                "note": "each key here blocks that timer/patrol until rotated at insert (L1 fix)",
            }
            rw = q(
                "SELECT title, retry_count, max_retries, payload->>'failure_class', "
                "EXTRACT(EPOCH FROM (now()-updated_at))/60 FROM central_tasks "
                "WHERE status='RETRY_WAIT' ORDER BY updated_at")
            report["sections"]["retrywait"] = {
                "rows": [{"title": t, "retry": r, "max": m, "failure_class": f,
                          "age_min": round(float(age), 1) if age is not None else None}
                         for t, r, m, f, age in rw],
            }
            trigs = [r[0] for r in q(
                "SELECT trigger_name FROM information_schema.triggers "
                "WHERE event_object_table='central_tasks'")]
            report["sections"]["transitions"] = {
                "triggers": sorted(set(trigs)),
                "expected": ["central_tasks_transition_trg",
                             "central_tasks_done_immutable_trg",
                             "central_tasks_audit_trg"],
            }
            leases = q("SELECT status, COUNT(*) FROM central_leases GROUP BY 1")
            durs = q("SELECT DISTINCT EXTRACT(EPOCH FROM (expires_at-issued_at))::int "
                     "FROM central_leases LIMIT 10")
            report["sections"]["leases"] = {
                "by_status": dict(leases),
                "distinct_durations_s": sorted(int(d[0]) for d in durs),
            }
    except Exception as exc:
        print("AUDIT FAIL: DB error: %s" % exc)
        return 1
    cron = [l for l in _cron_lines() if l.strip() and not l.strip().startswith("#")]
    report["sections"]["retrywait"]["auto_exit"] = {
        "cq_claim_cron_enabled": any("cq-run-20261102.sh claim" in l for l in cron),
        "retrywait_scan_cron": any("retrywait-scan" in l for l in cron),
        "long_task_sweeper_cron": any("long-task-sweeper" in l for l in cron),
        "note": "RETRY_WAIT->READY needs retrywait-scan --apply on cron; "
                "RETRY_WAIT->DISPATCHING needs the claimer (currently disabled 2026-11-07 dec-004)",
    }
    gate_results = {}
    try:
        names = sorted(os.listdir(CRON_CONTRACTS))
    except Exception:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        p = os.path.join(CRON_CONTRACTS, name)
        r = subprocess.run([sys.executable, GATE, "--insert-guard", p],
                           capture_output=True, text=True, timeout=30)
        gate_results[name] = "ALLOWED" if r.returncode == 0 else "REFUSED: " + (r.stdout or "").strip()[:200]
    report["sections"]["contracts"] = {
        "cron_contracts_gate": gate_results,
        "templates": {
            "schema_json": os.path.exists(BASE + "/contracts/contract.schema.json"),
            "mandatory_template_md": os.path.exists(
                BASE + "/state/contract-mandatory-20261105/CONTRACT-TEMPLATE.md"),
            "cron_contract_files": len([n for n in names if n.endswith(".json")]),
            "sop_plan_templates": [n for n in ("plan-template.md", "code-plan-template.md")
                                   if os.path.exists(BASE + "/docs/sops/" + n)],
        },
    }
    verdicts = {}
    live_pairs = []
    try:
        import psycopg as _pg
        with _pg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title, status FROM central_tasks")
                live_pairs = [(t, s) for t, s in cur.fetchall()]
    except Exception:
        live_pairs = []
    try:
        for name in sorted(os.listdir(VERDICT_DIR)):
            if not name.endswith(".json"):
                continue
            try:
                v = json.load(open(os.path.join(VERDICT_DIR, name), encoding="utf-8"))
            except Exception as exc:
                verdicts[name] = {"error": str(exc)[:200]}
                continue
            matched = None
            if _wd is not None:
                matched = _wd.is_dispatched(v.get("task", "") or name[:-5], live_pairs)
            verdicts[name] = {
                "verdict": v.get("verdict"),
                "task": v.get("task"),
                "conditions": len(v.get("conditions") or []),
                "live_matched_in_queue": matched,
            }
    except Exception as exc:
        verdicts["error"] = str(exc)[:200]
    report["sections"]["verdicts"] = verdicts
    try:
        dq = json.load(open(DQ_FILE, encoding="utf-8"))
        pending = dq.get("pending", [])
        resolved = dq.get("resolved", [])
        report["sections"]["decisions"] = {
            "pending": len(pending),
            "resolved": len(resolved),
            "resolved_types": dict(collections.Counter(
                x.get("type") for x in resolved if isinstance(x, dict))),
            "pending_types": dict(collections.Counter(
                x.get("type") for x in pending if isinstance(x, dict))),
        }
    except Exception as exc:
        report["sections"]["decisions"] = {"error": str(exc)[:200]}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("AUDIT REPORT: %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
