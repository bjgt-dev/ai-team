#!/usr/bin/env python3
"""test_promptops_gate.py — 提示词与配置管理 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_promptops_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "promptops-gate.py")

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


def fresh_job(tmp, job_id, model="deepseek-v4.1-flash-free"):
    wd = os.path.join(tmp, job_id)
    args = ["init", "--prompt-id", job_id, "--workdir", wd]
    if model:
        args += ["--model", model]
    run(*args, expect=0)
    return wd, os.path.join(wd, "promptops-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def adv(wd, state, stage, actor, artifact, extra=None):
    args = ["advance", "--state", state, "--stage", stage,
            "--actor", actor, "--artifact", artifact]
    if extra:
        args += extra
    return run(*args, expect=0)


def full_flow(wd, state, version="1.2.0"):
    adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
        ["--version", version])
    adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
    adv(wd, state, "ci", "checker-a", touch(wd, "ci-result.md", "ALL PASS"))
    for tier in [1, 10, 50, 100]:
        adv(wd, state, "canary", "twt", touch(wd, f"canary-{tier}.md"),
            ["--tier", str(tier)])
    adv(wd, state, "production", "twt", touch(wd, "production.md"))


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程 + 回滚后 production 回到上一版 ---
        wd, state = fresh_job(tmp, "t1")
        full_flow(wd, state)
        run("check", "--state", state, expect=0)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["production_version"] == "1.2.0", "production 应为 1.2.0"
        assert st["production_model"] == "deepseek-v4.1-flash-free", "模型版本须钉死"
        print("PASS: production 双版本钉死")
        PASS += 1
        run("rollback", "--state", state, "--actor", "twt",
            "--to-version", "1.1.0", "--reason", "1.2.0 退化", expect=0)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["production_version"] == "1.1.0", "回滚后 production 应为 1.1.0"
        print("PASS: 回滚后 production 标签回到上一版")
        PASS += 1

        # --- 用例 2：版本号不合规，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        run("advance", "--state", state, "--stage", "version",
            "--actor", "writer-a", "--artifact", touch(wd, "prompt-x.md"),
            "--version", "1.2", expect=1)
        run("advance", "--state", state, "--stage", "version",
            "--actor", "writer-a", "--artifact", touch(wd, "prompt-x.md"),
            "--version", "v1.2.0", expect=1)

        # --- 用例 3：未评审就进 CI，拦截 ---
        wd, state = fresh_job(tmp, "t3")
        adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
            ["--version", "1.2.0"])
        run("advance", "--state", state, "--stage", "ci",
            "--actor", "checker-a", "--artifact",
            touch(wd, "ci-result.md", "ALL PASS"), expect=1)

        # --- 用例 4：CI 未全过就灰度，拦截 ---
        wd, state = fresh_job(tmp, "t4")
        adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
            ["--version", "1.2.0"])
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
        run("advance", "--state", state, "--stage", "ci",
            "--actor", "checker-a", "--artifact",
            touch(wd, "ci-result.md", "2 PASS 1 FAIL"), expect=1)

        # --- 用例 5：灰度跳档，拦截 ---
        wd, state = fresh_job(tmp, "t5")
        adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
            ["--version", "1.2.0"])
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
        adv(wd, state, "ci", "checker-a", touch(wd, "ci-result.md", "ALL PASS"))
        run("advance", "--state", state, "--stage", "canary",
            "--actor", "twt", "--artifact", touch(wd, "canary-50.md"),
            "--tier", "50", expect=1)

        # --- 用例 6：无观测记录就进下一档，拦截 ---
        wd, state = fresh_job(tmp, "t6")
        adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
            ["--version", "1.2.0"])
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
        adv(wd, state, "ci", "checker-a", touch(wd, "ci-result.md", "ALL PASS"))
        run("advance", "--state", state, "--stage", "canary",
            "--actor", "twt", "--artifact", "canary-1-不存在.md",
            "--tier", "1", expect=1)

        # --- 用例 7：production 标签缺模型版本，拦截 ---
        wd, state = fresh_job(tmp, "t7", model=None)
        adv(wd, state, "version", "writer-a", touch(wd, "prompt-v1.2.0.md"),
            ["--version", "1.2.0"])
        adv(wd, state, "review", "da-guan-jia", touch(wd, "review.md"))
        adv(wd, state, "ci", "checker-a", touch(wd, "ci-result.md", "ALL PASS"))
        for tier in [1, 10, 50, 100]:
            adv(wd, state, "canary", "twt", touch(wd, f"canary-{tier}.md"),
                ["--tier", str(tier)])
        run("advance", "--state", state, "--stage", "production",
            "--actor", "twt", "--artifact", touch(wd, "production.md"), expect=1)

        # --- 用例 8：手工篡改 state（canary done 但档位不完整），check 抓获 ---
        wd, state = fresh_job(tmp, "t8")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        for s in ["version", "review", "ci", "canary"]:
            st["stages"][s] = {"status": "done", "actor": "twt",
                               "artifact": f"{s}.md", "ts": "2026-11-10T00:00:00+00:00"}
            touch(wd, f"{s}.md")
        st["version"] = "1.2.0"
        st["ci_passed"] = True
        st["canary_observed"] = [1, 10]  # 缺 50/100
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 9：尚未发布就回滚，拦截 ---
        wd, state = fresh_job(tmp, "t9")
        run("rollback", "--state", state, "--actor", "twt",
            "--to-version", "1.1.0", "--reason", "x", expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
