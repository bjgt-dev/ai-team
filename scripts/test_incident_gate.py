#!/usr/bin/env python3
"""test_incident_gate.py — 事故与交接 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_incident_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "incident-gate.py")

PASS = 0
FAIL = 0


def run(*args, expect=0):
    global PASS, FAIL
    r = subprocess.run([sys.executable, GATE, *args],
                       capture_output=True, text=True)
    ok = (r.returncode == expect)
    name = " ".join(args)
    if ok:
        PASS += 1
        print(f"PASS: {name} -> {r.returncode}")
    else:
        FAIL += 1
        print(f"FAIL: {name} -> {r.returncode}（期望 {expect}）\n  stdout={r.stdout.strip()}\n  stderr={r.stderr.strip()}")
    return r


def fresh_incident(tmp, job_id, sev=2, age_days=None):
    wd = os.path.join(tmp, job_id)
    args = ["init", "--incident-id", job_id, "--workdir", wd,
            "--sev", str(sev)]
    if age_days is not None:
        args += ["--age-days", str(age_days)]
    run(*args, expect=0)
    return wd, os.path.join(wd, "incident-sop-state.json")


def touch(wd, name, text="记录内容"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def adv(wd, state, stage, actor, artifact):
    return run("advance", "--state", state, "--stage", stage,
               "--actor", actor, "--artifact", artifact, expect=0)


def full_flow(wd, state):
    adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV2 服务降级，影响转发"))
    adv(wd, state, "triage", "twt",
        touch(wd, "triage.md", "IC: twt\nscribe: guangtou\ncomms: noesis"))
    adv(wd, state, "mitigate", "twt", touch(wd, "mitigate.md", "已切备用通道，服务恢复"))
    adv(wd, state, "diagnose", "twt", touch(wd, "diagnose.md", "根因：宿主机故障，证据见日志"))
    adv(wd, state, "resolve", "twt", touch(wd, "resolve.md", "已验证恢复"))
    adv(wd, state, "postmortem", "twt",
        touch(wd, "postmortem.md", "改进项：加熔断器 负责人：twt 期限：2026-11-20"))
    adv(wd, state, "runbook", "twt", touch(wd, "runbook.md", "runbook 已更新：熔断步骤"))


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程 + 交接 ---
        wd, state = fresh_incident(tmp, "t1", sev=2)
        full_flow(wd, state)
        run("handover", "--state", state, "--actor", "twt",
            "--artifact", touch(wd, "handover.md",
                                "未关告警：无\n进行中变更：熔断器上线\n已知脆弱点：单点网关\n待跟进项：复盘改进项"),
            "--to", "guangtou", expect=0)
        run("check", "--state", state, expect=0)

        # --- 用例 2：宣告无级别，拦截 ---
        wd, state = fresh_incident(tmp, "t2", sev=2)
        run("init", "--incident-id", "t2b", "--workdir", os.path.join(tmp, "t2b"),
            "--sev", "5", expect=2)

        # --- 用例 3：未分工就缓解，拦截 ---
        wd, state = fresh_incident(tmp, "t3", sev=2)
        adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV2"))
        run("advance", "--state", state, "--stage", "mitigate",
            "--actor", "twt", "--artifact", touch(wd, "mitigate.md", "已止血"), expect=1)

        # --- 用例 4：未缓解就诊断，拦截 ---
        wd, state = fresh_incident(tmp, "t4", sev=2)
        adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV2"))
        adv(wd, state, "triage", "twt",
            touch(wd, "triage.md", "IC: twt\nscribe: guangtou\ncomms: noesis"))
        run("advance", "--state", state, "--stage", "diagnose",
            "--actor", "twt", "--artifact", touch(wd, "diagnose.md", "根因"), expect=1)

        # --- 用例 5：triage 缺角色，拦截 ---
        wd, state = fresh_incident(tmp, "t5", sev=2)
        adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV2"))
        run("advance", "--state", state, "--stage", "triage",
            "--actor", "twt", "--artifact",
            touch(wd, "triage.md", "IC: twt\nscribe: guangtou"), expect=1)

        # --- 用例 6：复盘改进项缺负责人/到期日，拦截 ---
        wd, state = fresh_incident(tmp, "t6", sev=2)
        adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV2"))
        adv(wd, state, "triage", "twt",
            touch(wd, "triage.md", "IC: twt\nscribe: guangtou\ncomms: noesis"))
        adv(wd, state, "mitigate", "twt", touch(wd, "mitigate.md", "已止血"))
        adv(wd, state, "diagnose", "twt", touch(wd, "diagnose.md", "根因"))
        adv(wd, state, "resolve", "twt", touch(wd, "resolve.md", "已验证"))
        run("advance", "--state", state, "--stage", "postmortem",
            "--actor", "twt", "--artifact",
            touch(wd, "postmortem.md", "改进项：加熔断器"), expect=1)

        # --- 用例 7：P0/P1 复盘超 5 个工作日，拦截 ---
        wd, state = fresh_incident(tmp, "t7", sev=1, age_days=10)
        adv(wd, state, "declare", "twt", touch(wd, "declare.md", "SEV1"))
        adv(wd, state, "triage", "twt",
            touch(wd, "triage.md", "IC: twt\nscribe: guangtou\ncomms: noesis"))
        adv(wd, state, "mitigate", "twt", touch(wd, "mitigate.md", "已止血"))
        adv(wd, state, "diagnose", "twt", touch(wd, "diagnose.md", "根因"))
        adv(wd, state, "resolve", "twt", touch(wd, "resolve.md", "已验证"))
        run("advance", "--state", state, "--stage", "postmortem",
            "--actor", "twt", "--artifact",
            touch(wd, "postmortem.md", "改进项：加熔断器 负责人：twt 期限：2026-11-20"),
            expect=1)

        # --- 用例 8：交接清单缺项，拦截 ---
        wd, state = fresh_incident(tmp, "t8", sev=3)
        run("handover", "--state", state, "--actor", "twt",
            "--artifact", touch(wd, "handover.md", "未关告警：无\n进行中变更：无"),
            "--to", "guangtou", expect=1)

        # --- 用例 9：手工篡改 state（runbook done 但复盘未完成），check 抓获 ---
        wd, state = fresh_incident(tmp, "t9", sev=3)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        for s in ["declare", "triage", "mitigate", "diagnose", "resolve", "runbook"]:
            st["stages"][s] = {"status": "done", "actor": "twt",
                               "artifact": f"{s}.md", "ts": "2026-11-10T00:00:00+00:00"}
            touch(wd, f"{s}.md", "IC: twt\nscribe: guangtou\ncomms: noesis")
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
