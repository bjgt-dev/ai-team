#!/usr/bin/env python3
"""dispatch-gate-hub.py — 枢纽侧派发门禁（AGENTS.md：规划与执行分离 + 预检门）。

本地 dispatch-gate.sh 只查 PLAN 文件存在、合同文件存在、大管家 verdict 存在，
且只在本地跑；枢纽 pipeline-dispatch-20261106.py 本身不做这四项校验。
本门禁是枢纽侧唯一入口，派发前四项缺一即拒绝（exit 2），结果写进
state/dispatch-gate-ledger.jsonl：
  1. 方案：state/<task>/PLAN-*.md 存在且八段齐全（调 plan-first-gate.py）。
  2. 合同：state/pipeline-formal-20261106/ 下 <task> 合同 JSON 通过
     taskcontract_gate.py --validate（八字段 + 五段式）。
  3. 大管家评审结论：state/butler-verdicts/<task>.json verdict 为 通过/有条件通过。
  4. 预检：state/preflight-ledger.jsonl 有该任务记录（调 preflight-check.py check）。
用法：python3 dispatch-gate-hub.py <task_id> [--base /home/ubuntu/ai-team]
退出码：0=放行；2=拒绝。只读 + 写门禁台账，不改生产数据。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

BASE_DEFAULT = "/home/ubuntu/ai-team"
SCRIPTS = "scripts"


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def _candidates(task):
    """查哪用的名字：队列任务号常带 -p0-YYYYMMDD 后缀
    （如 kb-retrieval-20261106-p0-20261106），而 PLAN 目录 /
    verdict 文件 / 合同 task_id 一律用 base 名（kb-retrieval-20261106）。
    先试全名，再试 base 名。2026-11-07 修复：此前只试全名，
    对真实任务四项全 MISS，门禁等于永远拒收。"""
    cands = [task]
    m = re.search(r"-p\d+-\d{8}$", task)
    if m and task[:m.start()] not in cands:
        cands.append(task[:m.start()])
    return cands


def _canonical_hash(obj):
    """合同 canonical hash，与 dispatch_task.py _canonical_hash 一字一致。"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _log_ledger(base, rec):
    ledger = os.path.join(base, "state", "dispatch-gate-ledger.jsonl")
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _verdict_ok(base, task, contract_eval_id=None, contract_eval_run_id=None,
                contract_hash=None):
    # verdict 现行写 ceo-seat/state/butler-verdicts/，旧的在全局
    # state/butler-verdicts/；两处都查，现行优先（2026-11-10 修）。
    dirs = [os.path.join(base, "ceo-seat", "state", "butler-verdicts"),
            os.path.join(base, "state", "butler-verdicts")]
    for cand in _candidates(task):
        for d in dirs:
            p = os.path.join(d, "%s.json" % cand)
            try:
                dd = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # eval 分支（模板 §6.2）：插在大管家分支之前，大管家分支一字不动。
            # 全部条件须同时满足，任一不满足即拒绝，不得落入大管家分支。
            # 合同三项取不到时（None）比对恒假，即 fail-closed。
            if dd.get("eval") is True:
                if dd.get("verdict") == "eval-通过" \
                        and dd.get("eval_id") in ("EVAL-001", "EVAL-003", "EVAL-005") \
                        and isinstance(dd.get("eval_run_id"), str) and dd.get("eval_run_id") \
                        and dd.get("conditions") == [] \
                        and dd.get("eval_run_id") == contract_eval_run_id \
                        and dd.get("eval_id") == contract_eval_id \
                        and dd.get("contract_hash") == contract_hash:
                    return True, "eval-verdict=eval-通过 (%s, %s)" % (dd.get("eval_id"), dd.get("eval_run_id"))
                # 语义冒充单列：eval=true 却写大管家取值，须拒收并记冒充日志
                if dd.get("verdict") in ("通过", "有条件通过"):
                    _log_ledger(base, {"ts": datetime.now().isoformat(timespec="seconds"),
                                       "task": task, "event": "eval-verdict-impersonation",
                                       "result": "REFUSED",
                                       "detail": "eval-verdict-impersonation: %s" % p})
                return False, "eval verdict 不合格（须 eval-通过 + 范围内 eval_id + 一致的 eval_run_id/contract_hash）"
            v = dd.get("verdict")
            if v in ("通过", "有条件通过"):
                return True, "verdict=%s (%s)" % (v, cand)
            return False, "verdict=%s（须 通过/有条件通过）" % v
    return False, "无 verdict 文件：%s" % os.path.join(
        dirs[0], "%s.json" % task)


def _find_contract(base, task):
    """合同查找：先按文件名 glob（兼容旧命名），再按文件内 task_id 匹配。
    文件名如 kb-retrieval-contract-20261106.json 会把 base 名拆成两段，
    glob *<base>*contract* 永远命中不了，必须读 task_id 才找得到。"""
    cdir = os.path.join(base, "state", "pipeline-formal-20261106")
    for cand in _candidates(task):
        hits = sorted(glob.glob(os.path.join(cdir, "*%s*contract*.json" % cand)))
        if hits:
            return hits[0]
    for f in sorted(glob.glob(os.path.join(cdir, "*contract*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("task_id") in _candidates(task):
            return f
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--base", default=BASE_DEFAULT)
    args = ap.parse_args()
    base, task = args.base, args.task
    checks = []

    plans = []
    for cand in _candidates(task):
        plans = sorted(glob.glob(os.path.join(
            base, "state", cand, "PLAN-*.md")))
        if plans:
            break
    if not plans:
        checks.append(("PLAN", False, "无 PLAN-*.md"))
    else:
        rc, out = _run(["python3", os.path.join(base, SCRIPTS, "plan-first-gate.py"),
                        "check-plan", plans[0]])
        checks.append(("PLAN", rc == 0, out.splitlines()[-1] if out else ""))

    contract = _find_contract(base, task)
    if not contract:
        checks.append(("CONTRACT", False, "无合同 JSON"))
    else:
        rc, out = _run(["python3", os.path.join(base, SCRIPTS, "taskcontract_gate.py"),
                        "--validate", contract])
        checks.append(("CONTRACT", rc == 0, out.splitlines()[-1] if out else ""))

    # G2：从合同取 eval 绑定（eval_id / eval_run_id / canonical contract_hash）。
    # 合同读不到或缺字段时三项为 None，_verdict_ok 的 eval 分支比对恒假（fail-closed）。
    contract_eval_id = contract_eval_run_id = contract_hash = None
    if contract:
        try:
            cdata = json.load(open(contract, encoding="utf-8"))
            contract_eval_id = cdata.get("eval_id")
            contract_eval_run_id = cdata.get("eval_run_id")
            contract_hash = _canonical_hash(cdata)
        except (OSError, ValueError):
            pass
    ok, detail = _verdict_ok(base, task, contract_eval_id, contract_eval_run_id,
                             contract_hash)
    checks.append(("VERDICT", ok, detail))

    pre_ok, pre_detail = False, ""
    for cand in _candidates(task):
        rc, out = _run(["python3", os.path.join(base, SCRIPTS, "preflight-check.py"),
                        "--ledger", os.path.join(base, "state", "preflight-ledger.jsonl"),
                        "check", "--task", cand])
        pre_detail = out.splitlines()[-1] if out else ""
        if rc == 0:
            pre_ok = True
            break
    checks.append(("PREFLIGHT", pre_ok, pre_detail))

    passed = all(ok for _, ok, _ in checks)
    for name, ok, detail in checks:
        print("%s %s：%s" % ("OK " if ok else "MISS", name, detail))
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "task": task,
           "result": "PASS" if passed else "REFUSED",
           "detail": "; ".join("%s=%s" % (n, "OK" if o else "MISS")
                               for n, o, _ in checks)}
    ledger = os.path.join(base, "state", "dispatch-gate-ledger.jsonl")
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("GATE_%s task=%s" % ("PASS" if passed else "REFUSED", task))
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
