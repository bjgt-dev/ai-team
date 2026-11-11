#!/usr/bin/env python3
"""test_cost_gate.py — 成本与额度 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_cost_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile
import time

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cost-gate.py")

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


def fresh_job(tmp, job_id, budget_day=1000, budget_month=10000,
              bucket_capacity=100000, bucket_rate=10000,
              breaker_failures=3, cooldown=60, fallback="backup-model"):
    wd = os.path.join(tmp, job_id)
    args = ["init", "--agent-id", job_id, "--workdir", wd,
            "--budget-day", str(budget_day),
            "--budget-month", str(budget_month),
            "--bucket-capacity", str(bucket_capacity),
            "--bucket-rate", str(bucket_rate),
            "--breaker-failures", str(breaker_failures),
            "--cooldown", str(cooldown)]
    if fallback:
        args += ["--fallback", fallback]
    run(*args, expect=0)
    return wd, os.path.join(wd, "cost-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def charge(state, actor, amount, model="m1", extra=None):
    args = ["charge", "--state", state, "--actor", actor,
            "--amount", str(amount), "--model", model]
    if extra:
        args += extra
    return run(*args, expect=0)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程（70% 告警、85% 降级、100% 拦截、备用切换）---
        wd, state = fresh_job(tmp, "t1")
        run("advance", "--state", state, "--stage", "budget",
            "--actor", "twt", "--artifact", touch(wd, "budget.md"), expect=0)
        charge(state, "worker-1", 700)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert len(st["alerts"]) == 1, "70% 应记告警"
        print("PASS: 70% 告警已记")
        PASS += 1
        charge(state, "worker-1", 150)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert len(st["degrades"]) == 1, "85% 应记降级"
        print("PASS: 85% 降级已记")
        PASS += 1
        charge(state, "worker-1", 100)
        run("charge", "--state", state, "--actor", "worker-1",
            "--amount", "51", "--model", "m1", expect=1)  # 100% 硬拦截
        run("fallback", "--state", state, "--actor", "twt",
            "--to", "backup-model", expect=0)
        run("usage", "--state", state, expect=0)
        run("check", "--state", state, expect=0)

        # --- 用例 2：无预算就调用，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        run("charge", "--state", state, "--actor", "worker-1",
            "--amount", "10", "--model", "m1", expect=1)

        # --- 用例 3：桶空仍调用，拦截 ---
        wd, state = fresh_job(tmp, "t3", bucket_capacity=100, bucket_rate=0)
        run("advance", "--state", state, "--stage", "budget",
            "--actor", "twt", "--artifact", touch(wd, "budget.md"), expect=0)
        run("charge", "--state", state, "--actor", "worker-1",
            "--amount", "150", "--model", "m1", expect=1)

        # --- 用例 4：熔断后冷却期内调用，拦截；冷却过后恢复 ---
        wd, state = fresh_job(tmp, "t4", cooldown=1)
        run("advance", "--state", state, "--stage", "budget",
            "--actor", "twt", "--artifact", touch(wd, "budget.md"), expect=0)
        for _ in range(3):
            run("charge", "--state", state, "--actor", "worker-1",
                "--amount", "10", "--model", "m1", "--failed", expect=0)
        run("charge", "--state", state, "--actor", "worker-1",
            "--amount", "10", "--model", "m1", expect=1)  # 冷却期内必拒
        time.sleep(2)
        charge(state, "worker-1", 10)  # 冷却过后恢复
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["consecutive_failures"] == 0, "成功后熔断计数应复位"
        print("PASS: 熔断冷却后恢复，计数复位")
        PASS += 1

        # --- 用例 5：高风险任务自动降级，拦截并记转人工 ---
        wd, state = fresh_job(tmp, "t5")
        run("advance", "--state", state, "--stage", "budget",
            "--actor", "twt", "--artifact", touch(wd, "budget.md"), expect=0)
        charge(state, "worker-1", 800)
        run("charge", "--state", state, "--actor", "worker-1",
            "--amount", "100", "--model", "m1", "--high-risk", expect=1)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert len(st["manual_reviews"]) == 1, "高风险降级须记转人工"
        print("PASS: 高风险任务转人工已记")
        PASS += 1

        # --- 用例 6：备用通道未登记就切换，拦截 ---
        wd, state = fresh_job(tmp, "t6", fallback="")
        run("fallback", "--state", state, "--actor", "twt",
            "--to", "backup-model", expect=1)

        # --- 用例 7：用量与记账不一致，check 抓获 ---
        wd, state = fresh_job(tmp, "t7")
        run("advance", "--state", state, "--stage", "budget",
            "--actor", "twt", "--artifact", touch(wd, "budget.md"), expect=0)
        charge(state, "worker-1", 100)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["used_day"] = 999  # 篡改
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 8：手工篡改 state（用量超预算），check 抓获 ---
        wd, state = fresh_job(tmp, "t8")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["stages"]["budget"] = {"status": "done", "actor": "twt",
                                  "artifact": "budget.md", "ts": "2026-11-10T00:00:00+00:00"}
        st["used_day"] = 1500
        st["charges"] = [{"actor": "w", "amount": 1500, "model": "m1",
                          "high_risk": False, "failed": False,
                          "channel": "primary", "ts": "2026-11-10T00:00:00+00:00"}]
        touch(wd, "budget.md")
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
