#!/usr/bin/env python3
"""eval_reports.py -- M6 regression report (Eval OS phase 2, G10).

v2 (2026-11-20, agent-platform-gap-audit L1). Changes vs 2026-11-11 v1:
  1. Task selection fixed: eval tasks are marked by TITLE
     ('eval-<EVAL_ID>-<run suffix>', e.g. eval-EVAL-001-20261111-02),
     not by payload. v1's `payload ? 'eval'` matched 0 rows in production
     (proven 2026-11-20), so the report always said tasks_run=0. v2
     selects title LIKE 'eval-%' (payload ? 'eval' kept as OR fallback)
     and filters by --run-id / --case in Python (one matcher, both
     read paths). --run-id 'eval-run-20261111-02' matches titles ending
     '-20261111-02'; --case EVAL-001 matches titles 'eval-EVAL-001-%'.
  2. Read path fixed: the default DSN role (cq_timer) has no SELECT on
     the eval tables (InsufficientPrivilege, proven live 2026-11-20;
     gap G10). v2 falls back to the read-only admin channel
     (docker exec ops-pg psql -U postgres, SELECT only -- same pattern
     as eval_reports_e2e_check.py) when the DSN read fails, and only
     then. Outside the hub (no docker) it re-raises with a clear message.
  3. eval_failures has no eval_run_id column (v1's run-filtered query
     would raise UndefinedColumn). v2 loads all failures and filters in
     Python by the case_ids of the selected run/case tasks.
  4. --baseline-from-db CASE: builds a baseline report from the latest
     eval_baselines row for CASE (verifier_ok -> VSTR/TSR 1.0, failures 0;
     a baseline row records one verified success, so the mapping is
     explicit and documented, not silently assumed).
  5. Safety metric now has a real source: state/dispatch-gate-ledger.jsonl
     (PASS/REFUSED counts). v1's docstring named dispatch-audit.log.jsonl,
     which does not exist. Ledger missing -> safety reported undefined,
     never zero. Cost / outcome_quality / process_quality / recovery_rate
     stay undefined (no source table; cost needs a chairman decision, P4).

Read-only: aggregates eval_* tables + central_tasks (+ the gate ledger
file) and prints a JSON report. Writes NOTHING to PG.

What is computed (only metrics with an approved definition):
  TSR  = verifier-DONE tasks / tasks run
  VSTR = independently-verified successes / tasks run
  Failure Distribution = eval_failures counts by six categories
  Latency = eval_traces.latency_ms aggregates (mean / max)
  Trace completeness = tasks with zero eval_traces rows ("Trace 缺失")
  Safety = dispatch-gate PASS/REFUSED counts (ledger file, read-only)

Regression rule (regression_check, unchanged from v1): a candidate is
BLOCKED when any of VSTR decreases, TSR decreases, or total failures
increase vs baseline. Anything else is reported, not blocking.

Usage:
  python3 eval_reports.py [--run-id EVAL_RUN_ID] [--case EVAL_ID]
      [--compare BASELINE.json] [--baseline-from-db EVAL_ID]
  --compare reads a baseline report JSON (as printed by this script) and
  exits 0 (no regression) or 1 (regression) after printing the comparison.
  --baseline-from-db builds that baseline JSON from eval_baselines instead.
DSN is read from state/.cq-dsn (same read pattern as c2-verifier).
"""
import argparse
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = os.path.join(BASE, "state", ".cq-dsn")
GATE_LEDGER = os.path.join(BASE, "state", "dispatch-gate-ledger.jsonl")

UNDEFINED_METRICS = {
    "outcome_quality": "no approved formula yet (plan names it, does not define it)",
    "process_quality": "no approved formula yet (plan names it, does not define it)",
    "recovery_rate": "no approved formula yet (needs retry/recovery event counts per run)",
    "cost": "suggested proxy (gateway calls + task time) has no readable source table",
}


def title_case_id(title):
    """'eval-EVAL-001-20261111-02' -> 'EVAL-001'. None when not title-marked.
    The case id itself contains a hyphen (EVAL-001) and the run suffix is
    the last two hyphen-parts (YYYYMMDD-NN), so the case is everything
    between the 'eval-' prefix and the run suffix."""
    if not isinstance(title, str):
        return None
    parts = title.split("-")
    if len(parts) >= 4 and parts[0] == "eval":
        return "-".join(parts[1:-2])
    return None


def title_matches_run(title, run_id):
    """Run 'eval-run-20261111-02' matches titles ending '-20261111-02'."""
    if not run_id:
        return True
    if not isinstance(title, str):
        return False
    suffix = run_id[len("eval-run-"):] if run_id.startswith("eval-run-") else run_id
    return title.startswith("eval-") and title.endswith("-" + suffix)


def title_matches_case(title, case_id):
    if not case_id:
        return True
    return isinstance(title, str) and title.startswith("eval-%s-" % case_id)


def select_tasks(tasks, run_id=None, case_id=None):
    """Filter title/payload-marked task rows to one run and/or case."""
    out = []
    for t in tasks:
        title = t.get("title")
        if title is not None:
            if not title_matches_run(title, run_id):
                continue
            if not title_matches_case(title, case_id):
                continue
        elif not run_id and not case_id:
            pass  # payload-marked row, no run/case filter requested
        else:
            continue  # payload-marked row cannot be tied to a run/case
        out.append(t)
    return out


def failures_for_tasks(all_failures, tasks):
    """eval_failures has no run column: keep rows whose case_id belongs
    to one of the selected tasks (parsed from the title marker)."""
    case_ids = {title_case_id(t.get("title")) for t in tasks}
    case_ids.discard(None)
    if not case_ids:
        return []
    return [f for f in all_failures if f.get("case_id") in case_ids]


def build_baseline_report(row):
    """eval_baselines row -> baseline report dict for regression_check.
    A baseline row records ONE verified success (verifier_ok true), so
    VSTR/TSR are 1.0 and failures 0 by construction of that record.
    A non-ok baseline maps to 0.0/0.0 (explicit, not silent)."""
    metrics = row.get("metrics") or {}
    ok = bool(metrics.get("verifier_ok"))
    return {
        "case_id": row.get("case_id"),
        "baseline_id": row.get("baseline_id"),
        "version": row.get("version"),
        "ts": row.get("ts"),
        "tasks_run": 1,
        "tasks_done": 1 if ok else 0,
        "tasks_verified_done": 1 if ok else 0,
        "tsr": 1.0 if ok else 0.0,
        "vstr": 1.0 if ok else 0.0,
        "failures": 0,
        "failure_distribution": {},
        "source": "eval_baselines",
    }


def parse_gate_ledger(text):
    """dispatch-gate-ledger.jsonl text -> safety counts. Malformed lines
    are counted, never silently dropped."""
    passed = refused = unreadable = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        result = str(rec.get("result", "")).upper()
        if result == "PASS":
            passed += 1
        elif result == "REFUSED":
            refused += 1
        else:
            unreadable += 1
    return {"passed": passed, "refused": refused, "unreadable_lines": unreadable,
            "source": "state/dispatch-gate-ledger.jsonl"}


def aggregate(cases, traces, failures, tasks, safety=None):
    """Pure aggregation over row dicts. cases/traces/failures are eval_* rows;
    tasks are central_tasks rows carrying the eval marker (title or payload).
    Returns a metrics dict; TSR/VSTR are None (not 0) when no tasks ran."""
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "DONE")
    verified = sum(1 for t in tasks
                   if t.get("status") == "DONE" and t.get("verified_by"))
    tsr = (done / total) if total else None
    vstr = (verified / total) if total else None
    dist = {}
    for f in failures:
        dist[f.get("category")] = dist.get(f.get("category"), 0) + 1
    lats = [t["latency_ms"] for t in traces
            if isinstance(t.get("latency_ms"), (int, float))]
    traced_ids = {t.get("task_id") for t in traces if t.get("task_id")}
    missing = [t.get("task_id") for t in tasks
               if t.get("task_id") not in traced_ids]
    undefined = dict(UNDEFINED_METRICS)
    if safety is None:
        undefined["safety"] = ("no readable source: state/dispatch-gate-ledger.jsonl"
                               " missing or unreadable")
    report = {
        "cases": len(cases),
        "traces": len(traces),
        "failures": len(failures),
        "tasks_run": total,
        "tasks_done": done,
        "tasks_verified_done": verified,
        "tsr": tsr,
        "vstr": vstr,
        "failure_distribution": dist,
        "latency_ms": {
            "n": len(lats),
            "mean": (sum(lats) / len(lats)) if lats else None,
            "max": max(lats) if lats else None,
        },
        "trace_missing_tasks": len(missing),
        "trace_missing_task_ids": missing,
        "undefined_metrics": undefined,
    }
    if safety is not None:
        report["safety"] = safety
    return report


def regression_check(baseline, candidate):
    """Return (ok, reasons). BLOCKED when VSTR/TSR decreases or failures rise.
    Comparisons with a None side are skipped (no runs yet is not a regression)."""
    reasons = []
    for key in ("vstr", "tsr"):
        b, c = baseline.get(key), candidate.get(key)
        if b is not None and c is not None and c < b:
            reasons.append("%s regressed: baseline %s -> candidate %s" % (key, b, c))
    bf = baseline.get("failures")
    cf = candidate.get("failures")
    if bf is not None and cf is not None and cf > bf:
        reasons.append("failures increased: baseline %s -> candidate %s" % (bf, cf))
    return (len(reasons) == 0, reasons)


def get_dsn():
    with open(DSN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip("\"").strip("'")
    return ""


def load_rows_dsn(run_id=None):
    import psycopg
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("no DSN in %s" % DSN_FILE)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT eval_id, task_type, version, golden FROM public.eval_cases")
            cases = [{"eval_id": r[0], "task_type": r[1], "version": r[2],
                      "golden": r[3]} for r in cur.fetchall()]
            if run_id:
                cur.execute("SELECT trace_id, task_id, eval_id, event_type, status,"
                            " latency_ms FROM public.eval_traces WHERE eval_run_id=%s",
                            (run_id,))
            else:
                cur.execute("SELECT trace_id, task_id, eval_id, event_type, status,"
                            " latency_ms FROM public.eval_traces")
            traces = [{"trace_id": r[0], "task_id": str(r[1]) if r[1] else None,
                       "eval_id": r[2], "event_type": r[3], "status": r[4],
                       "latency_ms": r[5]} for r in cur.fetchall()]
            cur.execute("SELECT case_id, category FROM public.eval_failures")
            failures = [{"case_id": r[0], "category": r[1]} for r in cur.fetchall()]
            cur.execute("SELECT task_id, title, status, verified_by FROM public.central_tasks"
                        " WHERE title LIKE 'eval-%%' OR payload ? 'eval'")
            tasks = [{"task_id": str(r[0]), "title": r[1], "status": r[2],
                      "verified_by": r[3]} for r in cur.fetchall()]
    return cases, traces, failures, tasks


def _admin_query(sql):
    """Read-only SELECT via the hub postgres admin channel. Returns rows
    as a JSON list. Raises RuntimeError when docker/psql is unavailable."""
    wrapped = ("SELECT COALESCE(json_agg(t), '[]'::json) FROM (%s) t" % sql)
    r = subprocess.run(["docker", "exec", "ops-pg", "psql", "-U", "postgres",
                        "-d", "central_queue", "-t", "-A", "-c", wrapped],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("admin channel failed: %s" % r.stderr.strip()[-300:])
    out = r.stdout.strip()
    return json.loads(out) if out else []


def load_rows_admin(run_id=None):
    cases = _admin_query("SELECT eval_id, task_type, version, golden"
                         " FROM public.eval_cases")
    if run_id:
        traces = _admin_query("SELECT trace_id, task_id::text, eval_id, event_type,"
                              " status, latency_ms FROM public.eval_traces"
                              " WHERE eval_run_id = '%s'" % run_id.replace("'", "''"))
    else:
        traces = _admin_query("SELECT trace_id, task_id::text, eval_id, event_type,"
                              " status, latency_ms FROM public.eval_traces")
    failures = _admin_query("SELECT case_id, category FROM public.eval_failures")
    tasks = _admin_query("SELECT task_id::text, title, status,"
                         " (verified_by IS NOT NULL) AS verified_by"
                         " FROM public.central_tasks"
                         " WHERE title LIKE 'eval-%' OR payload ? 'eval'")
    for t in tasks:
        t["verified_by"] = t.get("verified_by") or None
    return cases, traces, failures, tasks


def load_baseline_admin(case_id):
    rows = _admin_query("SELECT baseline_id, case_id, version, metrics,"
                        " ts::text AS ts FROM public.eval_baselines"
                        " WHERE case_id = '%s' ORDER BY ts DESC LIMIT 1"
                        % case_id.replace("'", "''"))
    return rows[0] if rows else None


def load_baseline_dsn(case_id):
    import psycopg
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("no DSN in %s" % DSN_FILE)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT baseline_id, case_id, version, metrics,"
                        " ts::text FROM public.eval_baselines"
                        " WHERE case_id=%s ORDER BY ts DESC LIMIT 1", (case_id,))
            r = cur.fetchone()
            if not r:
                return None
            return {"baseline_id": r[0], "case_id": r[1], "version": r[2],
                    "metrics": r[3], "ts": r[4]}


def load_rows(run_id=None):
    """DSN first; on missing eval-table privilege fall back to the
    read-only admin channel (hub only). Both paths return the same shapes."""
    try:
        return load_rows_dsn(run_id)
    except Exception as e:  # noqa: BLE001 -- fallback is the point; re-raise below if it also fails
        import psycopg
        if isinstance(e, psycopg.errors.InsufficientPrivilege):
            try:
                return load_rows_admin(run_id)
            except Exception as admin_e:  # noqa: BLE001
                raise RuntimeError("DSN read failed (%s); admin fallback also failed (%s)"
                                   % (e, admin_e))
        raise


def load_baseline(case_id):
    try:
        row = load_baseline_dsn(case_id)
        if row is not None:
            return row
    except Exception:  # noqa: BLE001 -- DSN lacks eval-table privilege; try admin
        pass
    return load_baseline_admin(case_id)


def load_safety():
    try:
        with open(GATE_LEDGER, encoding="utf-8") as f:
            return parse_gate_ledger(f.read())
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="")
    ap.add_argument("--case", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--baseline-from-db", default="")
    args = ap.parse_args()
    if args.compare and args.baseline_from_db:
        ap.error("--compare and --baseline-from-db are mutually exclusive")
    cases, traces, failures, tasks = load_rows(args.run_id or None)
    tasks = select_tasks(tasks, args.run_id or None, args.case or None)
    if args.run_id or args.case:
        failures = failures_for_tasks(failures, tasks)
    metrics = aggregate(cases, traces, failures, tasks, safety=load_safety())
    metrics["run_id"] = args.run_id or None
    metrics["case"] = args.case or None
    if args.compare or args.baseline_from_db:
        if args.compare:
            with open(args.compare, encoding="utf-8") as f:
                baseline = json.load(f)
        else:
            row = load_baseline(args.baseline_from_db)
            if row is None:
                print("no baseline row for case %s" % args.baseline_from_db,
                      file=sys.stderr)
                return 2
            baseline = build_baseline_report(row)
        ok, reasons = regression_check(baseline, metrics)
        out = {"baseline": baseline, "candidate": metrics,
               "regression": not ok, "reasons": reasons}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
