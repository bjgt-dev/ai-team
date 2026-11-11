#!/usr/bin/env python3
"""test_lifecycle_gate.py — 生命周期 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_lifecycle_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lifecycle-gate.py")

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


def fresh_job(tmp, job_id, deps=""):
    wd = os.path.join(tmp, job_id)
    args = ["init", "--agent-id", job_id, "--workdir", wd,
            "--owner", "twt", "--purpose", "test-agent"]
    if deps:
        args += ["--deps", deps]
    run(*args, expect=0)
    return wd, os.path.join(wd, "lifecycle-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def adv(wd, state, stage, actor, artifact):
    return run("advance", "--state", state, "--stage", stage,
               "--actor", actor, "--artifact", artifact, expect=0)


def full_flow_to_monitor(wd, state):
    adv(wd, state, "register", "twt", touch(wd, "register.md"))
    adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
    for f in ["accuracy.md", "safety.md", "readiness.md"]:
        touch(wd, f)
    adv(wd, state, "deploy", "da-guan-jia", touch(wd, "deploy.md"))
    adv(wd, state, "monitor", "twt", touch(wd, "monitor.md"))


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程（含 improve 循环、degrade、rollback、retire）---
        wd, state = fresh_job(tmp, "t1")
        full_flow_to_monitor(wd, state)
        run("degrade", "--state", state, "--actor", "twt",
            "--reason", "准确率抖动", expect=0)
        run("rollback", "--state", state, "--actor", "twt",
            "--to", "v1.0", expect=0)
        adv(wd, state, "improve", "writer-a", touch(wd, "improve.md"))
        # improve 后 review/deploy/monitor 重置重走
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["stages"]["review"]["status"] == "pending", "improve 后 review 应重置"
        assert st["stages"]["monitor"]["status"] == "pending", "improve 后 monitor 应重置"
        print("PASS: improve 后下游重置")
        PASS += 1
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review2.md"))
        for f in ["accuracy.md", "safety.md", "readiness.md"]:
            touch(wd, f)
        adv(wd, state, "deploy", "da-guan-jia", touch(wd, "deploy2.md"))
        adv(wd, state, "monitor", "twt", touch(wd, "monitor2.md"))
        adv(wd, state, "retire", "twt", touch(wd, "retire.md"))
        run("check", "--state", state, expect=0)

        # --- 用例 2：缺发布门证据就上线，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        adv(wd, state, "register", "twt", touch(wd, "register.md"))
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
        touch(wd, "accuracy.md")
        touch(wd, "safety.md")
        # readiness.md 故意不建
        run("advance", "--state", state, "--stage", "deploy",
            "--actor", "da-guan-jia", "--artifact", touch(wd, "deploy.md"), expect=1)

        # --- 用例 3：跳过 review 直发，拦截 ---
        wd, state = fresh_job(tmp, "t3")
        adv(wd, state, "register", "twt", touch(wd, "register.md"))
        for f in ["accuracy.md", "safety.md", "readiness.md"]:
            touch(wd, f)
        run("advance", "--state", state, "--stage", "deploy",
            "--actor", "da-guan-jia", "--artifact", touch(wd, "deploy.md"), expect=1)

        # --- 用例 4：退役前依赖未清空，拦截；清空后放行 ---
        wd, state = fresh_job(tmp, "t4", deps="worker-1,gateway")
        full_flow_to_monitor(wd, state)
        run("advance", "--state", state, "--stage", "retire",
            "--actor", "twt", "--artifact", touch(wd, "retire.md"), expect=1)
        run("undepend", "--state", state, "--dep", "worker-1", expect=0)
        run("undepend", "--state", state, "--dep", "gateway", expect=0)
        adv(wd, state, "retire", "twt", touch(wd, "retire.md"))

        # --- 用例 5：kill 后仍执行，拦截 ---
        wd, state = fresh_job(tmp, "t5")
        full_flow_to_monitor(wd, state)
        run("kill", "--state", state, "--actor", "twt",
            "--reason", "越权调用", expect=0)
        run("advance", "--state", state, "--stage", "improve",
            "--actor", "writer-a", "--artifact", touch(wd, "improve.md"), expect=1)
        run("degrade", "--state", state, "--actor", "twt",
            "--reason", "x", expect=1)

        # --- 用例 6：退役后推进，拦截 ---
        wd, state = fresh_job(tmp, "t6")
        full_flow_to_monitor(wd, state)
        adv(wd, state, "retire", "twt", touch(wd, "retire.md"))
        run("advance", "--state", state, "--stage", "improve",
            "--actor", "writer-a", "--artifact", touch(wd, "improve.md"), expect=1)

        # --- 用例 7：手工篡改 state（deploy done 但发布门缺证据），check 抓获 ---
        wd, state = fresh_job(tmp, "t7")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        for s in ["register", "review", "deploy"]:
            st["stages"][s] = {"status": "done", "actor": "twt",
                               "artifact": f"{s}.md", "ts": "2026-11-10T00:00:00+00:00"}
            touch(wd, f"{s}.md")
        st["release_gate"] = {"accuracy.md": True, "safety.md": False,
                              "readiness.md": True}
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 8：未上线就降级/回滚，拦截 ---
        wd, state = fresh_job(tmp, "t8")
        run("degrade", "--state", state, "--actor", "twt",
            "--reason", "x", expect=1)
        run("rollback", "--state", state, "--actor", "twt",
            "--to", "v1.0", expect=1)

        # --- 用例 9：产出物不存在，拦截 ---
        wd, state = fresh_job(tmp, "t9")
        run("advance", "--state", state, "--stage", "register",
            "--actor", "twt", "--artifact", "不存在.md", expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
