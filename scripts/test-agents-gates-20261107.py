#!/usr/bin/env python3
"""test-agents-gates-20261107.py — AGENTS.md 硬规落代码：正反测试总跑。

在枢纽上跑：python3 scripts/test-agents-gates-20261107.py
夹具全在 /tmp/agents-gates-test-20261107/，不碰生产文件、不写生产台账。
每项 gate 各跑一个正例（应 PASS）与一个反例（应 REFUSE/FAIL）；
正例被拒或反例放行都算测试失败。退出码 0=全部通过，1=有失败。
"""
import json
import os
import shutil
import subprocess
import sys
import time

S = "/home/ubuntu/ai-team/scripts"
T = "/tmp/agents-gates-test-20261107"
RESULTS = []


def run(cmd, expect):
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == expect
    return ok, r.returncode, (r.stdout + r.stderr).strip()[:400]


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(T, exist_ok=True)
    py = sys.executable

    # ---- 1. wake-check-hub：正例（五项全绿）/ 反例（五步链 stale）----
    good = os.path.join(T, "hub-good")
    os.makedirs(os.path.join(good, "state"), exist_ok=True)
    now = time.time()
    w(os.path.join(good, "state", "brain-active.log"),
      "2026-11-07 17:50:00 brain=my-gateway/agnes-3.0-flash\n")
    w(os.path.join(good, "state", "brain-health.md"),
      "- Zen 通道 opencode/deepseek-v4.1-flash-free：OK\n")
    w(os.path.join(good, "state", "decision-queue.json"),
      json.dumps({"pending": [], "resolved": [], "auto_decisions": []}))
    w(os.path.join(good, "state", "five-step-executor.log"), "step ok\n")
    for p in ("brain-active.log", "brain-health.md", "decision-queue.json",
              "five-step-executor.log"):
        os.utime(os.path.join(good, "state", p), (now, now))
    ok, rc, out = run([py, os.path.join(S, "wake-check-hub.py"), "--base", good], 0)
    check("wake-check-hub 正例全绿", ok, "rc=%d" % rc)
    stale = os.path.join(T, "hub-stale")
    shutil.copytree(good, stale)
    old = now - 6 * 86400
    os.utime(os.path.join(stale, "state", "five-step-executor.log"), (old, old))
    ok, rc, out = run([py, os.path.join(S, "wake-check-hub.py"), "--base", stale], 1)
    check("wake-check-hub 反例五步链 stale 判 RED", ok and "五步链" in out, "rc=%d" % rc)
    badq = os.path.join(T, "hub-badq")
    shutil.copytree(good, badq)
    w(os.path.join(badq, "state", "decision-queue.json"),
      json.dumps({"pending": [{"id": "x%d" % i} for i in range(7)]}))
    ok, rc, out = run([py, os.path.join(S, "wake-check-hub.py"), "--base", badq], 1)
    check("wake-check-hub 反例队列 pending>5 判 RED", ok and "pending=7" in out, "rc=%d" % rc)

    # ---- 2. plan-first-gate：正例八段齐 / 反例缺段 + 无批先干 ----
    plan_ok = os.path.join(T, "plan-ok.md")
    w(plan_ok, "# 计划\n## 背景\n## 目标\n## 不做\n## 方案选项\n## 评估标准 contract\n## 风险\n## 回滚\n## 需拍板点\n")
    ok, rc, out = run([py, os.path.join(S, "plan-first-gate.py"), "check-plan", plan_ok], 0)
    check("plan-first-gate 正例八段齐", ok, "rc=%d" % rc)
    plan_bad = os.path.join(T, "plan-bad.md")
    w(plan_bad, "# 计划\n## 背景\n## 目标\n")
    ok, rc, out = run([py, os.path.join(S, "plan-first-gate.py"), "check-plan", plan_bad], 1)
    check("plan-first-gate 反例缺六段拒收", ok and "缺段" in out, "rc=%d" % rc)
    taskdir = os.path.join(T, "task-x")
    os.makedirs(taskdir, exist_ok=True)
    w(os.path.join(taskdir, "PLAN-20261107.md"), open(plan_ok).read())
    w(os.path.join(taskdir, "impl.py"), "print(1)\n")
    ok, rc, out = run([py, os.path.join(S, "plan-first-gate.py"), "check-task", taskdir], 1)
    check("plan-first-gate 反例无批先写代码拒收", ok and "无董事长确认" in out, "rc=%d" % rc)

    # ---- 3. preflight-check：正例 / 反例（无 GitHub、低星当有、决定词缺失）----
    led = os.path.join(T, "preflight.jsonl")
    ok, rc, out = run([py, os.path.join(S, "preflight-check.py"), "--ledger", led,
                       "log", "--task", "t1", "--kind", "code",
                       "--searched", "GitHub: verify.sh determinism, 关键词 verify gates",
                       "--found", "quant-mind 3125★ MIT",
                       "--decision", "用：借 verify.sh 固定顺序模式"], 0)
    check("preflight-check 正例记账", ok, "rc=%d" % rc)
    ok, rc, out = run([py, os.path.join(S, "preflight-check.py"), "--ledger", led,
                       "log", "--task", "t2", "--kind", "code",
                       "--searched", "随便搜了搜",
                       "--found", "agentbrain 42★",
                       "--decision", "用：看着不错"], 1)
    check("preflight-check 反例无GitHub+低星拒记", ok and "GitHub" in out, "rc=%d" % rc)
    ok, rc, out = run([py, os.path.join(S, "preflight-check.py"), "--ledger", led,
                       "log", "--task", "t3", "--kind", "model",
                       "--searched", "GitHub: 找模型",
                       "--found", "某模型 5000 下载",
                       "--decision", "看看再说"], 1)
    check("preflight-check 反例模型类无HF+决定词缺失拒记", ok and "Hugging Face" in out, "rc=%d" % rc)
    ok, rc, out = run([py, os.path.join(S, "preflight-check.py"), "--ledger", led,
                       "check", "--task", "t1"], 0)
    check("preflight-check 正例 check 查到记录", ok, "rc=%d" % rc)
    ok, rc, out = run([py, os.path.join(S, "preflight-check.py"), "--ledger", led,
                       "check", "--task", "no-such-task"], 1)
    check("preflight-check 反例 check 无记录", ok, "rc=%d" % rc)

    # ---- 4. reply-format-lint：正例分级概括 / 反例外层裸标签 ----
    good_reply = os.path.join(T, "reply-good.md")
    w(good_reply, "- 结论：三项已补、两项已落地，缺口在派发接入\n"
                  "  - 已补：唤醒体检五步链新鲜度判 RED、预检 GitHub/HF 强制\n"
                  "  - 已落地：四类门禁已接 notify.py、定案门禁已有脚本\n"
                  "- 阻塞：定案门禁零调用点，需接进 DONE 流程\n"
                  "  - 影响：DONE 可能只凭文档关单\n")
    ok, rc, out = run([py, os.path.join(S, "reply-format-lint.py"), good_reply], 0)
    check("reply-format-lint 正例外层概括内容", ok, "rc=%d" % rc)
    bad_reply = os.path.join(T, "reply-bad.md")
    w(bad_reply, "- 结论：\n  - 三项已补\n- 阻塞：\n  - 定案门禁零调用\n")
    ok, rc, out = run([py, os.path.join(S, "reply-format-lint.py"), bad_reply], 1)
    check("reply-format-lint 反例外层裸标签", ok and "E1" in out, "rc=%d" % rc)

    # ---- 5. dename-lint：正例只称董事长 / 反例出现真实姓名 ----
    good_doc = os.path.join(T, "doc-good.md")
    w(good_doc, "董事长确认后执行，董事长只看主聊。\n")
    ok, rc, out = run([py, os.path.join(S, "dename-lint.py"), good_doc], 0)
    check("dename-lint 正例脱名通过", ok, "rc=%d" % rc)
    bad_doc = os.path.join(T, "doc-bad.md")
    w(bad_doc, "董事长确认后执行。\n")
    ok, rc, out = run([py, os.path.join(S, "dename-lint.py"), bad_doc], 1)
    check("dename-lint 反例真实姓名命中", ok and "真实姓名" in out, "rc=%d" % rc)

    # ---- 6. dual-advisor-gate：正例双顾问齐 / 反例缺批判 ----
    good_adv = os.path.join(T, "adv-good.md")
    w(good_adv, "ChatGPT 发散：三个方向；Claude 批判：两处失败点；"
                 "修订 1 轮；双顾问确认，无否决性意见。\n")
    ok, rc, out = run([py, os.path.join(S, "dual-advisor-gate.py"), good_adv], 0)
    check("dual-advisor-gate 正例双顾问齐", ok, "rc=%d" % rc)
    bad_adv = os.path.join(T, "adv-bad.md")
    w(bad_adv, "ChatGPT 发散：三个方向；直接做了。\n")
    ok, rc, out = run([py, os.path.join(S, "dual-advisor-gate.py"), bad_adv], 1)
    check("dual-advisor-gate 反例缺批判与确认", ok and "Claude" in out, "rc=%d" % rc)
    over_adv = os.path.join(T, "adv-over.md")
    w(over_adv, "ChatGPT 发散；Claude 批判；修订 3 轮；双顾问确认。\n")
    ok, rc, out = run([py, os.path.join(S, "dual-advisor-gate.py"), over_adv], 1)
    check("dual-advisor-gate 反例超两轮修订", ok and "3 轮" in out, "rc=%d" % rc)

    # ---- 7. four-category-audit：正例全带标签 / 反例缺标签与误标 ----
    q_good = os.path.join(T, "q-good.json")
    w(q_good, json.dumps({"pending": [
        {"id": "a", "summary": "买服务器", "needs_chairman": True, "category": "money"},
        {"id": "b", "summary": "重启定时器", "needs_chairman": False}],
        "resolved": [], "auto_decisions": []}, ensure_ascii=False))
    ok, rc, out = run([py, os.path.join(S, "four-category-audit.py"), "--queue", q_good], 0)
    check("four-category-audit 正例标签齐全", ok, "rc=%d" % rc)
    q_bad = os.path.join(T, "q-bad.json")
    w(q_bad, json.dumps({"pending": [
        {"id": "a", "summary": "删除数据库"},
        {"id": "b", "summary": "购买订阅", "needs_chairman": False}],
        "resolved": [], "auto_decisions": []}, ensure_ascii=False))
    ok, rc, out = run([py, os.path.join(S, "four-category-audit.py"), "--queue", q_bad], 1)
    check("four-category-audit 反例缺标签+误标自决", ok and "缺 needs_chairman" in out and "误标自决" in out, "rc=%d" % rc)

    # ---- 8. decision-code-gate-audit：正例 DONE 带代码+证据 / 反例只写文档 ----
    led_good = os.path.join(T, "ledger-good.md")
    w(led_good, "# 执行台账\n| 决策/事项 | 状态 | 卡在哪 / 下一步 | 期限 | 证据 |\n|---|----|----|----|----|\n"
                  "| 定案A | DONE | 已验 | 2026-11-07 | scripts/four-category-gate.py、测试结果 PASS |\n")
    ok, rc, out = run([py, os.path.join(S, "decision-code-gate-audit.py"),
                       "--ledger", led_good], 0)
    check("decision-code-gate-audit 正例 DONE 带代码+证据", ok, "rc=%d" % rc)
    led_bad = os.path.join(T, "ledger-bad.md")
    w(led_bad, "# 执行台账\n| 决策/事项 | 状态 | 卡在哪 / 下一步 | 期限 | 证据 |\n|---|----|----|----|----|\n"
                "| 定案B | DONE | 已验 | 2026-11-07 | 只写了 SOP 文档 |\n")
    ok, rc, out = run([py, os.path.join(S, "decision-code-gate-audit.py"),
                       "--ledger", led_bad], 1)
    check("decision-code-gate-audit 反例 DONE 只写文档", ok and "无代码文件引用" in out, "rc=%d" % rc)

    # ---- 9. ledger-check（枢纽版）：正例干净 / 反例 DONE 无证据 ----
    ok, rc, out = run([py, os.path.join(S, "ledger-check.py"), "--ledger", led_good], 0)
    check("ledger-check 正例干净台账", ok, "rc=%d" % rc)
    led_bad2 = os.path.join(T, "ledger-bad2.md")
    w(led_bad2, "# 执行台账\n| 决策/事项 | 状态 | 卡在哪 / 下一步 | 期限 | 证据 |\n|---|----|----|----|----|\n"
                 "| 定案B | DONE | 已验 | 2026-11-07 | — |\n")
    ok, rc, out = run([py, os.path.join(S, "ledger-check.py"), "--ledger", led_bad2], 2)
    check("ledger-check 反例 DONE 无证据判违规", ok, "rc=%d" % rc)

    # ---- 10. dispatch-gate-hub：正例四项齐 / 反例缺预检 ----
    hub = os.path.join(T, "hub-dispatch")
    os.makedirs(os.path.join(hub, "state", "t9"), exist_ok=True)
    os.makedirs(os.path.join(hub, "state", "pipeline-formal-20261106"), exist_ok=True)
    os.makedirs(os.path.join(hub, "state", "butler-verdicts"), exist_ok=True)
    os.makedirs(os.path.join(hub, "scripts"), exist_ok=True)
    w(os.path.join(hub, "state", "t9", "PLAN-20261107.md"), open(plan_ok).read())
    contract = {"task_type": "script", "input": "x", "output_path": "/tmp/o",
                "evidence": "test", "pass_line": "ok", "fail_action": "stop",
                "verifier": "v", "forbidden": "none",
                "five_part": {"goal": "g", "constraint": "c", "stop_when": "s",
                              "must_not": "m", "report": "r"}}
    w(os.path.join(hub, "state", "pipeline-formal-20261106", "t9-contract-20261107.json"),
      json.dumps(contract))
    w(os.path.join(hub, "state", "butler-verdicts", "t9.json"),
      json.dumps({"verdict": "通过"}, ensure_ascii=False))
    for f in ("plan-first-gate.py", "taskcontract_gate.py", "preflight-check.py"):
        shutil.copy(os.path.join(S, f), os.path.join(hub, "scripts", f))
    led2 = os.path.join(hub, "state", "preflight-ledger.jsonl")
    with open(led2, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"task": "t9", "searched": "GitHub x",
                             "found": "y 2000★", "decision": "用：z",
                             "kind": "code"}, ensure_ascii=False) + "\n")
    ok, rc, out = run([py, os.path.join(S, "dispatch-gate-hub.py"), "t9",
                       "--base", hub], 0)
    check("dispatch-gate-hub 正例四项齐放行", ok, "rc=%d %s" % (rc, out[:200]))
    os.remove(led2)
    ok, rc, out = run([py, os.path.join(S, "dispatch-gate-hub.py"), "t9",
                       "--base", hub], 2)
    check("dispatch-gate-hub 反例缺预检拒绝", ok and "PREFLIGHT" in out, "rc=%d" % rc)

    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print("=" * 60)
    print("TOTAL %d，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - n_fail, n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
