#!/usr/bin/env python3
"""eval_reports.py -- M6 regression report (Eval OS phase 2, G10, 2026-11-11).

Read-only: aggregates eval_* tables + central_tasks and prints a JSON report.
Writes NOTHING to PG (no baseline snapshot insert; that needs a writer
decision, same lesson as the eval_cases import, where the cq_timer DSN role
has no INSERT privilege on the new eval tables).

What is computed (only metrics with an approved definition):
  TSR  = verifier-DONE tasks / tasks run (three-case plan 0.4)
  VSTR = independently-verified successes / tasks run (same source)
  Failure Distribution = eval_failures counts by six categories
  Latency = eval_traces.latency_ms aggregates (mean / max, per case_id)
  Trace completeness = tasks with zero eval_traces rows ("Trace 缺失")
What is NOT invented (listed as undefined with the reason):
  Outcome Quality / Process Quality / Recovery Rate / Cost / Safety --
  named in the plan's nine metrics but with no approved formula yet.
  Cost's suggested proxy (gateway calls + task time) has no readable
  source table; Safety's suggested source (gate-svc refusals) lives in
  dispatch-audit.log.jsonl, not in PG. They are reported as undefined,
  never as zero.

Regression rule (regression_check): a candidate is BLOCKED when any of
  VSTR decreases, TSR decreases, or total failures increase vs baseline.
  Anything else is reported, not blocking.

Usage:
  python3 eval_reports.py [--run-id EVAL_RUN_ID] [--compare BASELINE.json]
  --compare reads a baseline report JSON (as printed by this script) and
  exits 0 (no regression) or 1 (regression) after printing the comparison.
DSN is read from state/.cq-dsn (same read pattern as c2-verifier).
"""
import argparse
import json
import os
import sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = os.path.join(BASE, "state", ".cq-dsn")

UNDEFINED_METRICS = {
    "outcome_quality": "no approved formula yet (plan names it, does not define it)",
    "process_quality": "no approved formula yet (plan names it, does not define it)",
    "recovery_rate": "no approved formula yet (needs retry/recovery event counts per run)",
    "cost": "suggested proxy (gateway calls + task time) has no readable source table",
    "safety": "suggested source (gate-svc refusals, canary_fail) lives in logs, not in PG",
}


def aggregate(cases, traces, failures, tasks):
    """Pure aggregation over row dicts. cases/traces/failures are eval_* rows;
    tasks are central_tasks rows carrying the eval marker (payload has 'eval').
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
    return {
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
        "undefined_metrics": UNDEFINED_METRICS,
    }


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


def load_rows(run_id=None):
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
            if run_id:
                cur.execute("SELECT case_id, category FROM public.eval_failures"
                            " WHERE eval_run_id=%s", (run_id,))
            else:
                cur.execute("SELECT case_id, category FROM public.eval_failures")
            failures = [{"case_id": r[0], "category": r[1]} for r in cur.fetchall()]
            if run_id:
                # NOTE: the exact payload shape for the eval marker is not
                # frozen yet (plan P5: payload->'eval' + eval_run_id). Match
                # the three plausible shapes rather than assuming one.
                cur.execute("SELECT task_id, status, verified_by FROM public.central_tasks"
                            " WHERE (payload->'eval'->>'run_id'=%s"
                            " OR payload->'eval'->>'eval_run_id'=%s"
                            " OR payload->>'eval_run_id'=%s)",
                            (run_id, run_id, run_id))
            else:
                cur.execute("SELECT task_id, status, verified_by FROM public.central_tasks"
                            " WHERE payload ? 'eval'")
            tasks = [{"task_id": str(r[0]), "status": r[1],
                      "verified_by": r[2]} for r in cur.fetchall()]
    return cases, traces, failures, tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="")
    ap.add_argument("--compare", default="")
    args = ap.parse_args()
    cases, traces, failures, tasks = load_rows(args.run_id or None)
    metrics = aggregate(cases, traces, failures, tasks)
    metrics["run_id"] = args.run_id or None
    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            baseline = json.load(f)
        ok, reasons = regression_check(baseline, metrics)
        out = {"baseline": baseline, "candidate": metrics,
               "regression": not ok, "reasons": reasons}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
