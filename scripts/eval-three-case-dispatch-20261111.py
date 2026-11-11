#!/usr/bin/env python3
"""eval-three-case-dispatch-20261111.py — Eval OS 第二阶段三考试派发驱动（跑在枢纽上）。

一次一个 Case，每案过 Gate 检查再派下一个。一份 verdict 只授权一次派发。
用法：
  python3 scripts/eval-three-case-dispatch-20261111.py --case A [--wait-min 30]
  python3 scripts/eval-three-case-dispatch-20261111.py --case B-dryrun
  python3 scripts/eval-three-case-dispatch-20261111.py --case B
  python3 scripts/eval-three-case-dispatch-20261111.py --case C
退出码：0 = 达到预期终态；1 = 未达到或任一步失败。
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
CASE_DIR = BASE + "/state/eval-os-phase2"
DISPATCH = BASE + "/scripts/pipeline-dispatch-20261106.py"
EVAL_GATE = BASE + "/scripts/eval-gate.py"
PREFLIGHT = BASE + "/scripts/preflight-gate-20261107.py"
PLAN_ID = "23085d4b-d440-4b07-b68d-1a6656dfa9a7"
RUN_ID = "eval-run-20261111-01"
WORKDIR_BASE = "~/code-agent-pilot/workdir"


def _dsn():
    for line in open(BASE + "/state/.cq-dsn"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def _run(cmd, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _plan(tid, body):
    d = os.path.join(BASE, "state", tid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "PLAN-eval.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


def _preflight(tid, q1, q2, q3, q4):
    rc, out = _run(["python3", PREFLIGHT, "--task", tid, "--record",
                    "--q1", q1, "--q2", q2, "--q3", q3, "--q4", q4,
                    "--queue3"], timeout=30)
    if rc != 0:
        print("DISPATCH_FAIL preflight rc=%d %s" % (rc, out[:500]))
        sys.exit(1)
    print("PREFLIGHT_OK task=%s" % tid)


def _sign_verdict(contract_path, eval_id):
    rc, out = _run(["python3", EVAL_GATE, "issue-verdict",
                    "--contract", contract_path,
                    "--eval-id", eval_id, "--eval-run-id", RUN_ID,
                    "--plan-id", PLAN_ID], timeout=120)
    print(out, end="")
    if rc != 0:
        print("DISPATCH_FAIL issue-verdict rc=%d" % rc)
        sys.exit(1)
    print("VERDICT_SIGNED eval=%s run=%s" % (eval_id, RUN_ID))


def _dispatch(contract_path):
    rc, out = _run(["python3", DISPATCH, "--contract", contract_path],
                   timeout=900)
    print(out, end="")
    if rc != 0:
        print("DISPATCH_FAIL pipeline-dispatch rc=%d" % rc)
        sys.exit(1)
    print("DISPATCHED contract=%s" % contract_path)


def _poll(tid, expect, wait_min, fail_states=("QUARANTINED",)):
    import psycopg
    deadline = time.time() + wait_min * 60
    last = None
    with psycopg.connect(_dsn()) as conn:
        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT status, retry_count, artifact_sha, failure_class, reason"
                            " FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
                row = cur.fetchone()
            if row:
                last = row[0]
                print("POLL task=%s status=%s retry=%s sha=%s fclass=%s reason=%s expect=%s"
                      % (tid, row[0], row[1], (row[2] or "")[:12], row[3], (row[4] or "")[:80], expect),
                      flush=True)
                if last in expect:
                    print("POLL_OK task=%s status=%s" % (tid, last))
                    return 0
                if last in fail_states and last not in expect:
                    print("POLL_FAIL task=%s status=%s" % (tid, last))
                    return 1
            else:
                print("POLL task=%s not-in-central_tasks-yet expect=%s" % (tid, expect), flush=True)
            time.sleep(60)
    print("POLL_FAIL timeout task=%s last=%s" % (tid, last))
    return 1


# ---------------------------------------------------------------- Case A
CASE_A_TID = "eval-EVAL-001-20261111-01"
CASE_A_DIR = os.path.join(CASE_DIR, "case-a")
CASE_A_FILES = ["immersion_engine.py", "ladder.py", "simulator.py",
                "test_kids4.py", "test_kids5_1.py"]


def case_a_contract():
    hub = [os.path.join(CASE_A_DIR, f) for f in CASE_A_FILES]
    return {
        "task_id": CASE_A_TID, "task_type": "code",
        "five_part": {
            "goal": "给 kids 儿童软件加 12 个边界测试 test_kids5_1.py，不改源码；pytest 47 passed",
            "constraint": "不改源码；bundle 须为 PULLONCE-BUNDLE-V1；测试须断言真实行为",
            "stop_when": "pytest 47 passed 且 verifier 五门全过、DONE 由 verifier 置",
            "must_not": "no fake DONE; no manual SQL; no manual file delivery; 不改源码",
            "report": "pytest 日志 + verifier verdict + artifact sha"},
        "input": "EVAL-001 正常成功：kids 迭代 4（35 passed）加 test_kids5_1.py（12 个边界测试），不改源码",
        "output_path": os.path.join(CASE_A_DIR, "test_kids5_1.py"),
        "evidence": "hub 预跑 pytest 47 passed 日志：state/eval-os-phase2/case-a/pytest-47passed-20261111.log",
        "pass_line": "pytest 47 passed；bundle 解析 + sha 校验通过；verifier 五门全过",
        "fail_action": "RETRY_WAIT with verbatim reason, no manual DONE flip.",
        "verifier": "c2-verifier",
        "forbidden": ["no fake DONE", "no manual SQL", "no manual file delivery", "不改源码"],
        "plan_id": PLAN_ID,
        "lane": "biz",
        "workdir": WORKDIR_BASE + "/eval-EVAL-001-20261111-01",
        "test_command": ["python3", "-m", "pytest", "-q"],
        "test_files": [os.path.join(CASE_A_DIR, "test_kids4.py"),
                       os.path.join(CASE_A_DIR, "test_kids5_1.py")],
        "test_baseline_seconds": 120,
        "allowed_dependencies": [],
        "estimated_seconds": 600,
        "code_files": hub,
        "evidence_files": [os.path.join(CASE_A_DIR, "pytest-47passed-20261111.log")],
        "outputs": hub,
        "dependencies": [
            {"hub_path": os.path.join(CASE_A_DIR, f),
             "worker_rel": "code-agent-pilot/workdir/eval-EVAL-001-20261111-01/" + f}
            for f in CASE_A_FILES],
        "format": "PULLONCE-BUNDLE-V1",
        "eval_id": "EVAL-001",
        "eval_run_id": RUN_ID,
        "eval": {"eval_id": "EVAL-001", "eval_run_id": RUN_ID, "eval_shape": "code"},
    }


CASE_A_PLAN = """# 背景
Eval OS 第二阶段第一批 Case A（EVAL-001 正常成功）。kids 儿童软件迭代 4 已 DONE（pytest 35 passed），
本次加 1 个边界测试文件 test_kids5_1.py（12 个边界测试），不改源码。hub 已预跑 47 passed。

# 目标
派发 code 任务到 worker-1：pytest 47 passed，bundle 为 PULLONCE-BUNDLE-V1 且 sha 与库内一致，
verifier 五门全过，DONE 由 verifier 置（非自声明）。

# 不做
不改 kids 源码；不手工改任务状态；不手工写 verdict（由 eval-gate issue-verdict 签发）；
不碰触发器/queue_api/timer/patrol；不混入失败 14/15。

# 方案选项
A) pipeline-dispatch 走 worker-1 code 路 + eval verdict（已选）。
B) 直接入库（拒绝：绕过门禁）。

# 评估标准（contract）
合同全文见 state/pipeline-formal-20261106/eval-EVAL-001-20261111-01-contract.json；
pytest 47 passed；bundle 解析 + sha 校验通过；verifier 五门全过。

# 风险
worker 隧道断连（失败 14/15 已单独入账，不混入本 Case）；verifier gate 3 要求 bundle 内测试文件
与 reference_dir（合同 output_path 所在目录）同名文件 sha 一致——provision 下发同一份 hub 文件可保证。

# 回滚
任务 QUARANTINED 后按失败归因处理，不手工改状态；verdict 一次性，过期重签须重走 P1–P6。

# 需拍板点
无：已批执行令，二阶段批准 23085d4b-d440-4b07-b68d-1a6656dfa9a7，2026-11-12 06:48 CST 过期。
"""


def case_a(wait_min):
    tid = CASE_A_TID
    contract = case_a_contract()
    cp = os.path.join(OUT, "eval-EVAL-001-20261111-01-contract.json")
    os.makedirs(OUT, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)
    rc, out = _run(["python3", os.path.join(BASE, "scripts", "taskcontract_gate.py"),
                    "--insert-guard", cp], timeout=60)
    print(out, end="")
    if rc != 0:
        print("DISPATCH_FAIL contract insert-guard rc=%d" % rc)
        return 1
    _plan(tid, CASE_A_PLAN)
    _preflight(tid,
               "hub: pipeline-dispatch, dispatch-gate-hub, taskcontract_gate, dispatch_task, eval-gate issue-verdict (existing scripts, no new dependency)",
               "existing hub scripts + kids-iteration4 files (hub pre-run 47 passed), no external package",
               "reuse existing pipeline; no new tool",
               "dispatch exits 0 only on gate PASS; poll exits 0 only on DONE; timeout 30 min")
    _sign_verdict(cp, "EVAL-001")
    _dispatch(cp)
    return _poll(tid, ("DONE",), wait_min)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=["A", "B-dryrun", "B", "C"])
    ap.add_argument("--wait-min", type=int, default=30)
    a = ap.parse_args()
    if a.case == "A":
        return case_a(a.wait_min)
    print("case %s 尚未实现（按顺序一次一个）" % a.case)
    return 1


if __name__ == "__main__":
    sys.exit(main())
