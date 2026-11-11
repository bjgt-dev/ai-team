#!/usr/bin/env python3
"""Auto-dispatch a passed/conditionally-passed butler verdict into the
central queue. Builds a standard code-task contract from the verdict +
intake, then submits via dispatch_task.py (gate enforced). Usage:
  verdict-auto-dispatch.py <verdict.json>
Exit 0 = dispatched READY; 1 = gate refused / cannot build; 2 = usage.
No network, no LLM. Stdlib only (dispatch_task.py does the DB insert)."""
import json, os, re, subprocess, sys

STATE = "/home/ubuntu/ai-team/state"
TASKS_DIR = os.path.join(STATE, "..", "ceo-seat", "tasks")
DISPATCH = "/home/ubuntu/ai-team/scripts/dispatch_task.py"
COND_CHECK = "/home/ubuntu/ai-team/scripts/verdict-conditions-check-20261107.py"

FORBIDDEN = [
    "不伪造 DONE",
    "不下单板子、不花钱、不对外发布、不做不可逆操作",
    "不收集孩子真实语音、不写孩子可识别信息、不把孩子真实语音用于未经批准的用途",
    "不编造真声 ASR 转写率、不冒充发音已复核、不冒充容错表已真声校准",
    "不用 llm_doc 派生成任务",
    "不绕过合同门禁",
    "不直接派活给个人，只走中央队列认领",
]

def slug(task):
    s = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    return s or "task"

def safe_task_id(task, date):
    """dispatch_task.py TASK_ID_RE allows only [A-Za-z0-9_-] (1..128).
    A raw verdict task with spaces or other characters was refused at
    dispatch (exit 1) and the watchdog logged AUTO-DISPATCH FAILED.
    Sanitize (case preserved, so the watchdog's case-sensitive substring
    match still finds the title); the watchdog's normalize_task applies
    the same rule on the matching side."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (task or "").strip()).strip("-")
    s = s or "task"
    task_id = "%s-p0-%s" % (s, date) if date else "%s-p0" % s
    return task_id[:128]

def main():
    if len(sys.argv) != 2:
        print("usage: verdict-auto-dispatch.py <verdict.json>")
        return 2
    vpath = sys.argv[1]
    try:
        v = json.load(open(vpath))
    except Exception as e:
        print("cannot read verdict:", e)
        return 1
    verdict = v.get("verdict", "")
    if verdict not in ("通过", "有条件通过"):
        print("verdict is not pass/conditional pass:", verdict)
        return 1
    task = v.get("task", "") or v.get("task_id", "") or os.path.basename(vpath)[:-5]
    if not task:
        print("verdict has no task")
        return 1
    # find intake for context
    intake_text = ""
    req = v.get("request", "") or ""
    cands = []
    if req:
        cands.append(os.path.join(TASKS_DIR, req))
    for name in sorted(os.listdir(TASKS_DIR)) if os.path.isdir(TASKS_DIR) else []:
        if task in name and name.endswith(".md"):
            cands.append(os.path.join(TASKS_DIR, name))
    for c in cands:
        try:
            intake_text = open(c).read()
            break
        except Exception:
            continue
    conditions = v.get("conditions", []) or []
    cond_text = "\n".join("- %s" % c for c in conditions) if conditions else "- 无"
    date = ""
    m = re.search(r"(\d{8})", os.path.basename(vpath))
    if m:
        date = m.group(1)
    task_id = safe_task_id(task, date)
    s = slug(task)
    req_path = v.get("evidence", [""])[0] if v.get("evidence") else ""
    inp = (
        "工厂执行：%s（大管家 verdict：%s）。\n"
        "需求/方案见：%s。\n"
        "条件（须逐项落实）：\n%s\n"
        "认领前置检查：先查本机有无执行本任务所需配置；无则一次性报缺哪项，不要认领后空转。code 型任务由 worker-1 经 queue_api/pull_once 拉活。"
        % (task, verdict, req_path or (req or "见 intake"), cond_text)
    )
    # Thorsten Ball Rule 3 (2026-11-07): five-part contract (Goal /
    # Constraint / Stop when / Must not / Report). Rule 8: variants list
    # (empty: verdict tasks are well-defined, not fuzzy). Rule 9: evidence
    # rules — "tests pass" alone is not evidence; receipt must carry the
    # raw output and sha256 of the deliverable.
    five_part = {
        "goal": "完成 %s：大管家 verdict 为 %s，条件逐项落实，pytest 全绿。" % (task, verdict),
        "constraint": "不花钱、不对外发布、不做不可逆操作；只改合同内文件。",
        "stop_when": "条件逐项落实；交付物齐全；测试全绿且附原始输出 sha256。",
        "must_not": "不伪造 DONE；不编造成绩；不冒充已复核；不弱化测试。",
        "report": "结论 + 执行动作 + 验收证据（原始输出 + 交付物 sha256），每项按此格式写。",
    }
    contract = {
        "task_id": task_id,
        "task_type": "code",
        "format": "PULLONCE-BUNDLE-V1",
        "lane": "biz",
        "workdir": "~/code-agent-pilot/workdir/%s" % s,
        "input": inp,
        "evidence": "交付物落盘枢纽 state/；回执附原始输出及 sha256；每项按结论→执行动作→验收写。证据规则：只写 tests pass 不算证据，须附失败前测试原文、原始输出与交付物 sha256。",
        "verifier": "c2-verifier + 本席独立验收后再报董事长。",
        "forbidden": FORBIDDEN,
        "pass_line": "条件逐项落实；交付物齐全；测试全绿且附原始输出 sha256；无编造成绩、无已复核冒充。",
        "fail_action": "任一证据缺失则 RETRY_WAIT，不报 DONE；实现不了则一次性报缺项并留 READY。",
        "output_path": "/home/ubuntu/ai-team/state/%s-DELIVERABLES.md" % s,
        "dependencies": [],
        "five_part": five_part,
        "variants": [],
    }
    tmp = "/tmp/verdict-dispatch-%s.json" % s
    with open(tmp, "w") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)
    # cq-scan 2026-11-07 (L5): conditions guard. Before this, conditions
    # were only pasted into the contract input and nothing verified they
    # survived; a dropped condition would dispatch a contract the butler
    # never approved. Refuse (no DB write) when the check fails.
    cc = subprocess.run(
        ["python3", COND_CHECK, "--verdict", vpath, "--contract", tmp],
        capture_output=True, text=True, timeout=60)
    print((cc.stdout or "").strip())
    if cc.returncode != 0:
        print("verdict-auto-dispatch: REFUSED by conditions check, no dispatch")
        return 1
    r = subprocess.run(
        ["python3", DISPATCH, "--contract", tmp],
        capture_output=True, text=True, timeout=120)
    print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip()[:500])
    return 0 if r.returncode == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
