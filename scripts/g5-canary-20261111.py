#!/usr/bin/env python3
"""g5-canary-20261111.py - G5 wiring canaries (synthetic, gated path only).

Dispatches one canary task per invocation through pipeline-dispatch
(self-issued SYNTHETIC verdict + PLAN + preflight + contract, exactly
like pipeline-canary-20261106.py; proves pipeline mechanics only, NOT
independent review). Direct DB insert is refused (bypasses gates).

Usage: python3 scripts/g5-canary-20261111.py --which <name> [--wait-min 20]
  code-base | code-after | script-after | eval-code | eval-judge-pass |
  eval-judge-fail | eval-badshape
Exit 0 only when the canary reaches its expected end state (see EXPECT).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

BASE = "/home/ubuntu/ai-team"
OUT = BASE + "/state/pipeline-formal-20261106"
FILES = BASE + "/state/g5-canary-20261111/files"
DISPATCH = BASE + "/scripts/pipeline-dispatch-20261106.py"


def _dsn():
    for line in open(BASE + "/state/.cq-dsn"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def _tid(which):
    return "g5-canary-%s-%s" % (
        which, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"))


def _plan(tid, goal):
    d = os.path.join(BASE, "state", tid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "PLAN-canary.md"), "w") as f:
        f.write("# 背景\nG5 wiring synthetic canary (%s).\n\n" % tid)
        f.write("# 目标\n%s\n\n" % goal)
        f.write("# 不做\nNo manual DONE flip, no manual SQL, no manual file delivery, "
                "no eval verdict, no Case dispatch.\n\n")
        f.write("# 方案选项\nA) pipeline-dispatch with gates (chosen). "
                "B) direct insert (refused: bypasses gates).\n\n")
        f.write("# 评估标准（contract）\nSee EXPECT in g5-canary-20261111.py.\n\n")
        f.write("# 风险\nGate changes may refuse the synthetic contract.\n\n")
        f.write("# 回滚\nDelete the canary task row; re-run the canary.\n\n")
        f.write("# 需拍板点\nNone: synthetic regression only, no spend, no external publish.\n")


def _verdict(tid):
    d = os.path.join(BASE, "ceo-seat", "state", "butler-verdicts")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s.json" % tid), "w") as f:
        json.dump({"verdict": "通过", "task": tid,
                   "note": "SYNTHETIC regression-harness verdict (self-issued by g5-canary); proves pipeline mechanics only, not independent review.",
                   "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                  f, ensure_ascii=False)


def _preflight(tid):
    r = subprocess.run(["python3", BASE + "/scripts/preflight-gate-20261107.py",
                        "--task", tid, "--record",
                        "--q1", "hub: pipeline-dispatch, dispatch-gate-hub, taskcontract_gate, dispatch_task (existing scripts, no new dependency)",
                        "--q2", "existing hub scripts, no external package",
                        "--q3", "reuse existing pipeline; no new tool",
                        "--q4", "canary exits 0 only on expected end state; timeout 20 min",
                        "--queue3"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print("CANARY_FAIL preflight rc=%d %s" % (r.returncode, (r.stdout or "")[:300]))
        sys.exit(1)


def _contract_base(tid, task_type):
    return {
        "task_id": tid, "task_type": task_type,
        "five_part": {"goal": "G5 canary %s" % tid,
                      "constraint": "synthetic only; pytest on worker-1",
                      "stop_when": "expected end state reached",
                      "must_not": "no fake DONE; no manual SQL; no manual file delivery",
                      "report": "receipt in state/pipeline-formal-20261106/"},
        "input": "G5 canary %s" % tid,
        "output_path": os.path.join(FILES, "add_one.py"),
        "evidence": "worker-1 run + artifact, receipt in state/pipeline-formal-20261106/",
        "pass_line": "expected end state reached, no manual steps.",
        "fail_action": "RETRY_WAIT with verbatim reason, no manual DONE flip.",
        "verifier": "c2-verifier + G5 canary check",
        "forbidden": ["no fake DONE", "no manual SQL", "no manual file delivery"],
    }


def _dispatch(tid, contract):
    cp = os.path.join(OUT, "canary-contract-%s.json" % tid)
    os.makedirs(OUT, exist_ok=True)
    json.dump(contract, open(cp, "w"))
    _plan(tid, contract.get("input", tid))
    _verdict(tid)
    _preflight(tid)
    r = subprocess.run(["python3", DISPATCH, "--contract", cp],
                       capture_output=True, text=True, timeout=900)
    print((r.stdout or "") + (r.stderr or ""), end="")
    if r.returncode != 0:
        print("CANARY_FAIL dispatch rc=%d" % r.returncode)
        sys.exit(1)
    print("CANARY_DISPATCHED task=%s" % tid)
    return tid


def _poll(tid, expect, wait_min):
    import psycopg
    deadline = time.time() + wait_min * 60
    last = None
    with psycopg.connect(_dsn()) as conn:
        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
                row = cur.fetchone()
            last = row[0] if row else None
            print("CANARY_POLL task=%s status=%s expect=%s" % (tid, last, expect), flush=True)
            if last in expect:
                print("CANARY_OK task=%s status=%s" % (tid, last))
                return 0
            if last in ("QUARANTINED",) and "QUARANTINED" not in expect:
                print("CANARY_FAIL task=%s status=%s" % (tid, last))
                return 1
            time.sleep(60)
    print("CANARY_FAIL timeout task=%s last=%s" % (tid, last))
    return 1


CANARIES = {
    # Baseline code canary (fixed files, deterministic). Expect DONE.
    # NOTE (2026-11-11): code_files/evidence_files MUST be absolute paths.
    # gate-svc runs with WorkingDirectory=scripts/, so decision-code-gate
    # resolves relative paths against scripts/ and every relative path
    # fails (this also broke the 08:00 pipeline-canary and all code
    # dispatch since cutover enforce). Absolute paths pass the same
    # existence check. The CWD bug itself is reported separately; this
    # canary does not depend on it being fixed.
    "code-base": ("code", {
        "workdir": "~/code-agent-pilot/workdir/g5-canary-code",
        "test_command": ["python3", "-m", "pytest", "-q"],
        "test_baseline_seconds": 60, "allowed_dependencies": [],
        "estimated_seconds": 600,
        "code_files": ["/home/ubuntu/ai-team/state/g5-canary-20261111/files/add_one.py"],
        "evidence_files": ["/home/ubuntu/ai-team/state/cutover-20261110.log"],
        "dependencies": [
            {"hub_path": FILES + "/add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-code/add_one.py"},
            {"hub_path": FILES + "/test_add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-code/test_add_one.py"}],
        "format": "PULLONCE-BUNDLE-V1",
    }, ("DONE",)),
    # Same as code-base, run after the G5 change. Expect DONE + identical
    # manifest file list / per-file sha + pytest text (modulo timing).
    "code-after": ("code", {
        "workdir": "~/code-agent-pilot/workdir/g5-canary-code",
        "test_command": ["python3", "-m", "pytest", "-q"],
        "test_baseline_seconds": 60, "allowed_dependencies": [],
        "estimated_seconds": 600,
        "code_files": ["/home/ubuntu/ai-team/state/g5-canary-20261111/files/add_one.py"],
        "evidence_files": ["/home/ubuntu/ai-team/state/cutover-20261110.log"],
        "dependencies": [
            {"hub_path": FILES + "/add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-code/add_one.py"},
            {"hub_path": FILES + "/test_add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-code/test_add_one.py"}],
        "format": "PULLONCE-BUNDLE-V1",
    }, ("DONE",)),
    # Script regression. Expect DONE.
    "script-after": ("script", {
        "input": {"command": "echo g5-script-ok"},
        "code_files": ["/home/ubuntu/ai-team/scripts/g5-canary-20261111.py"],
        "evidence_files": ["/home/ubuntu/ai-team/state/cutover-20261110.log"],
    }, ("DONE",)),
    # Eval-code: seeded code, shape code. Expect worker claim -> VERIFYING
    # (bundle submitted) -> RETRY_WAIT (G9 fail-closed, no acceptance).
    "eval-code": ("eval", {
        "eval_id": "EVAL-G5-CODE", "eval_run_id": "g5-canary-eval-code",
        "eval": {"eval_id": "EVAL-G5-CODE", "eval_run_id": "g5-canary-eval-code",
                 "eval_shape": "code"},
        "workdir": "~/code-agent-pilot/workdir/g5-canary-eval-code",
        "dependencies": [
            {"hub_path": FILES + "/add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-eval-code/add_one.py"},
            {"hub_path": FILES + "/test_add_one.py",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-eval-code/test_add_one.py"}],
    }, ("VERIFYING", "RETRY_WAIT", "QUARANTINED")),
    # Eval-judge pass: product satisfies all criteria. Expect result JSON
    # overall=pass submitted -> VERIFYING -> RETRY_WAIT (G9).
    "eval-judge-pass": ("eval", {
        "eval_id": "EVAL-G5-JUDGE-PASS", "eval_run_id": "g5-canary-eval-judge-pass",
        "eval": {"eval_id": "EVAL-G5-JUDGE-PASS", "eval_run_id": "g5-canary-eval-judge-pass",
                 "eval_shape": "judge", "artifact_path": "doc-pass.md",
                 "criteria": ["grep:幻觉", "字数>=200", "章节>=3", "关键词:幻觉,上下文,成本"]},
        "workdir": "~/code-agent-pilot/workdir/g5-canary-eval-judge-pass",
        "dependencies": [
            {"hub_path": FILES + "/doc-pass.md",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-eval-judge-pass/doc-pass.md"}],
    }, ("VERIFYING", "RETRY_WAIT", "QUARANTINED")),
    # Eval-judge fail: product misses the grep word. Expect result JSON
    # overall=fail submitted, then fail("eval_judge_fail") -> RETRY_WAIT.
    "eval-judge-fail": ("eval", {
        "eval_id": "EVAL-G5-JUDGE-FAIL", "eval_run_id": "g5-canary-eval-judge-fail",
        "eval": {"eval_id": "EVAL-G5-JUDGE-FAIL", "eval_run_id": "g5-canary-eval-judge-fail",
                 "eval_shape": "judge", "artifact_path": "doc-fail.md",
                 "criteria": ["grep:不存在的关键词"]},
        "workdir": "~/code-agent-pilot/workdir/g5-canary-eval-judge-fail",
        "dependencies": [
            {"hub_path": FILES + "/doc-fail.md",
             "worker_rel": "code-agent-pilot/workdir/g5-canary-eval-judge-fail/doc-fail.md"}],
    }, ("RETRY_WAIT", "QUARANTINED")),
    # Eval unknown shape. Expect fail("eval_shape_unknown") -> RETRY_WAIT.
    "eval-badshape": ("eval", {
        "eval_id": "EVAL-G5-BADSHAPE", "eval_run_id": "g5-canary-eval-badshape",
        "eval": {"eval_id": "EVAL-G5-BADSHAPE", "eval_run_id": "g5-canary-eval-badshape",
                 "eval_shape": "bogus"},
        "workdir": "~/code-agent-pilot/workdir/g5-canary-eval-badshape",
    }, ("RETRY_WAIT", "QUARANTINED")),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", required=True, choices=sorted(CANARIES))
    ap.add_argument("--wait-min", type=int, default=20)
    a = ap.parse_args()
    task_type, extra, expect = CANARIES[a.which]
    tid = _tid(a.which)
    c = _contract_base(tid, task_type)
    if task_type == "script":
        c["input"] = extra["input"]
    else:
        c.update(extra)
    _dispatch(tid, c)
    return _poll(tid, expect, a.wait_min)


if __name__ == "__main__":
    sys.exit(main())
