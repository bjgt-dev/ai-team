#!/usr/bin/env python3
"""test_eval_gate.py — 评测验收 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_eval_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "eval-gate.py")

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


def fresh_job(tmp, job_id="eval-test"):
    wd = os.path.join(tmp, job_id)
    run("init", "--job-id", job_id, "--workdir", wd, expect=0)
    return wd, os.path.join(wd, "eval-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def evalset(wd, name="evalset.json", version="v1.0", extra_tasks=None):
    tasks = [
        {"id": "t1", "expected": "正确回答", "scoring": "assertion"},
        {"id": "t2", "expected": "流畅对话", "scoring": "llm_judge"},
        {"id": "t3", "expected": "人工满意", "scoring": "human"},
    ]
    if extra_tasks:
        tasks += extra_tasks
    return touch(wd, name, json.dumps({"version": version, "tasks": tasks}, ensure_ascii=False))


def scores(wd, name, dims, version="v1.0"):
    return touch(wd, name, json.dumps(
        {"evalset_version": version, "dimensions": dims}, ensure_ascii=False))


def adv(wd, state, stage, actor, artifact, extra=None):
    args = ["advance", "--state", state, "--stage", stage,
            "--actor", actor, "--artifact", artifact]
    if extra:
        args += extra
    return run(*args, expect=0)


def happy_path(wd, state, dims=None, reg_dims=None):
    """走通 evalset->baseline->change->calibrate->regression->compare->publish(smoke)。"""
    dims = dims or {"accuracy": 0.90, "fluency": 0.85}
    reg_dims = reg_dims if reg_dims is not None else dims
    adv(wd, state, "evalset", "twt", evalset(wd))
    adv(wd, state, "baseline", "twt", scores(wd, "baseline.json", dims))
    adv(wd, state, "change", "twt", touch(wd, "change.md", "改提示词"),
        ["--change-type", "prompt"])
    run("calibrate", "--state", state, "--artifact",
        touch(wd, "calibration.md", "人工抽查 20 条一致率 95%"), expect=0)
    adv(wd, state, "regression", "twt", scores(wd, "regression.json", reg_dims))
    adv(wd, state, "compare", "twt", touch(wd, "compare.md", "逐维度对比：全部不低于基线"))
    adv(wd, state, "publish", "twt", touch(wd, "release-smoke.md", "smoke 发布"),
        ["--tier", "smoke"])
    run("check", "--state", state, expect=0)
    run("status", "--state", state, expect=0)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程 + smoke->regression->full 三档顺序发布 ---
        wd, state = fresh_job(tmp, "t1")
        happy_path(wd, state)
        adv(wd, state, "publish", "twt", touch(wd, "release-reg.md", "regression 发布"),
            ["--tier", "regression"])
        adv(wd, state, "publish", "twt", touch(wd, "release-full.md", "full 发布"),
            ["--tier", "full"])
        run("check", "--state", state, expect=0)

        # --- 用例 2（违规 1）：无评测集就跑回归，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        run("advance", "--state", state, "--stage", "regression",
            "--actor", "twt", "--artifact", touch(wd, "regression.json", "{}"), expect=1)

        # --- 用例 3（违规 2）：未校准就回归，拦截 ---
        wd, state = fresh_job(tmp, "t3")
        adv(wd, state, "evalset", "twt", evalset(wd))
        adv(wd, state, "baseline", "twt", scores(wd, "baseline.json", {"accuracy": 0.90}))
        adv(wd, state, "change", "twt", touch(wd, "change.md", "换模型"),
            ["--change-type", "model"])
        run("advance", "--state", state, "--stage", "regression",
            "--actor", "twt", "--artifact",
            scores(wd, "regression.json", {"accuracy": 0.91}), expect=1)

        # --- 用例 4（违规 3）：某维度退化仍发布，compare 与 publish 均拦截 ---
        wd, state = fresh_job(tmp, "t4")
        adv(wd, state, "evalset", "twt", evalset(wd))
        adv(wd, state, "baseline", "twt", scores(wd, "baseline.json",
                                                  {"accuracy": 0.90, "fluency": 0.85}))
        adv(wd, state, "change", "twt", touch(wd, "change.md", "改工作流"),
            ["--change-type", "workflow"])
        run("calibrate", "--state", state, "--artifact",
            touch(wd, "calibration.md", "校准记录"), expect=0)
        adv(wd, state, "regression", "twt", scores(wd, "regression.json",
                                                    {"accuracy": 0.91, "fluency": 0.80}))
        run("advance", "--state", state, "--stage", "compare",
            "--actor", "twt", "--artifact", touch(wd, "compare.md", "对比"), expect=1)
        run("advance", "--state", state, "--stage", "publish",
            "--actor", "twt", "--artifact", touch(wd, "release.md", "发布"),
            "--tier", "smoke", expect=1)

        # --- 用例 5（违规 4）：跳过 regression 直发 full，拦截（顺序 + 档位双重） ---
        wd, state = fresh_job(tmp, "t5")
        adv(wd, state, "evalset", "twt", evalset(wd))
        adv(wd, state, "baseline", "twt", scores(wd, "baseline.json", {"accuracy": 0.90}))
        adv(wd, state, "change", "twt", touch(wd, "change.md", "改提示词"),
            ["--change-type", "prompt"])
        run("advance", "--state", state, "--stage", "publish",
            "--actor", "twt", "--artifact", touch(wd, "release-full.md", "full"),
            "--tier", "full", expect=1)
        # 即使跑完回归，smoke 未发也不许直发 full
        wd, state = fresh_job(tmp, "t5b")
        happy_path(wd, state)
        run("advance", "--state", state, "--stage", "publish",
            "--actor", "twt", "--artifact", touch(wd, "release-full.md", "full"),
            "--tier", "full", expect=1)

        # --- 用例 6（违规 5）：失败案例未登记就关单/发布，拦截；登记后放行 ---
        wd, state = fresh_job(tmp, "t6")
        adv(wd, state, "evalset", "twt", evalset(wd))
        adv(wd, state, "baseline", "twt", scores(wd, "baseline.json", {"accuracy": 0.90}))
        adv(wd, state, "change", "twt", touch(wd, "change.md", "改提示词"),
            ["--change-type", "prompt"])
        run("calibrate", "--state", state, "--artifact",
            touch(wd, "calibration.md", "校准记录"), expect=0)
        adv(wd, state, "regression", "twt", scores(wd, "regression.json", {"accuracy": 0.90}))
        adv(wd, state, "compare", "twt", touch(wd, "compare.md", "对比"))
        run("record-failure", "--state", state, "--case-id", "prod-fail-001", expect=0)
        run("advance", "--state", state, "--stage", "publish",
            "--actor", "twt", "--artifact", touch(wd, "release.md", "发布"),
            "--tier", "smoke", expect=1)
        # 未写进评测集文件，register 也拦截
        run("register-failure", "--state", state, "--case-id", "prod-fail-001", expect=1)
        # 写进评测集再登记，放行
        evalset(wd, "evalset.json", extra_tasks=[
            {"id": "prod-fail-001", "expected": "不再复现", "scoring": "assertion"}])
        run("register-failure", "--state", state, "--case-id", "prod-fail-001", expect=0)
        adv(wd, state, "publish", "twt", touch(wd, "release.md", "发布"),
            ["--tier", "smoke"])
        run("check", "--state", state, expect=0)

        # --- 用例 7（违规 6）：手工篡改 state（回归分数改低 / publish 凭空 done），check 抓获 ---
        wd, state = fresh_job(tmp, "t7")
        happy_path(wd, state)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        with open(os.path.join(wd, "regression.json"), "w", encoding="utf-8") as f:
            json.dump({"evalset_version": "v1.0",
                       "dimensions": {"accuracy": 0.50, "fluency": 0.85}}, f)
        run("check", "--state", state, expect=1)
        # 恢复回归文件，再篡改 state：publish done 但 compare 未 done
        scores(wd, "regression.json", {"accuracy": 0.90, "fluency": 0.85})
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["stages"]["compare"]["status"] = "pending"
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 8：评测集无版本号，拦截 ---
        wd, state = fresh_job(tmp, "t8")
        bad = touch(wd, "evalset.json", json.dumps(
            {"tasks": [{"id": "t1", "expected": "x", "scoring": "assertion"}]}))
        run("advance", "--state", state, "--stage", "evalset",
            "--actor", "twt", "--artifact", bad, expect=1)

        # --- 用例 9：基线版本与评测集版本不一致，拦截 ---
        wd, state = fresh_job(tmp, "t9")
        adv(wd, state, "evalset", "twt", evalset(wd, version="v1.0"))
        run("advance", "--state", state, "--stage", "baseline",
            "--actor", "twt", "--artifact",
            scores(wd, "baseline.json", {"accuracy": 0.90}, version="v9.9"), expect=1)

        # --- 用例 10：change 不声明改动类型，拦截 ---
        wd, state = fresh_job(tmp, "t10")
        adv(wd, state, "evalset", "twt", evalset(wd))
        adv(wd, state, "baseline", "twt", scores(wd, "baseline.json", {"accuracy": 0.90}))
        run("advance", "--state", state, "--stage", "change",
            "--actor", "twt", "--artifact", touch(wd, "change.md", "改了点东西"), expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
