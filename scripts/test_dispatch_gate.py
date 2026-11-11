#!/usr/bin/env python3
"""test_dispatch_gate.py — 协作派发 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_dispatch_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dispatch-gate.py")

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


def fresh_job(tmp, job_id="dispatch-test"):
    wd = os.path.join(tmp, job_id)
    run("init", "--job-id", job_id, "--workdir", wd,
        "--lease-ttl-seconds", "3600", expect=0)
    return wd, os.path.join(wd, "dispatch-sop-state.json")


def touch(wd, name, text="x"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


CONTRACT = ["--contract-goal", "写一篇文章", "--input-version", "v1.0",
            "--output-schema", "article.md", "--budget", "2h",
            "--success-criteria", "复核通过"]


def do_contract(wd, state):
    run("advance", "--state", state, "--stage", "contract", "--actor", "twt",
        "--artifact", touch(wd, "contract.json"), *CONTRACT, expect=0)


def do_claim(wd, state, attempt="att-1", actor="worker-1"):
    run("advance", "--state", state, "--stage", "claim", "--actor", actor,
        "--artifact", touch(wd, "claim.json"), "--attempt-id", attempt, expect=0)


def expire_lease(state):
    """把租约 expires_at 改到过去，模拟超时未心跳。"""
    with open(state, encoding="utf-8") as f:
        st = json.load(f)
    past = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")
    st["lease"]["expires_at"] = past
    with open(state, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程通过（含心跳续租） ---
        wd, state = fresh_job(tmp, "t1")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        run("heartbeat", "--state", state, "--attempt-id", "att-1", expect=0)
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-1",
            "--artifact", touch(wd, "result.md"), "--attempt-id", "att-1", expect=0)
        run("advance", "--state", state, "--stage", "receipt", "--actor", "twt",
            "--artifact", touch(wd, "receipt.json"), "--attempt-id", "att-1", expect=0)
        run("check", "--state", state, expect=0)
        run("status", "--state", state, expect=0)

        # --- 用例 2：跳步（未 contract 先 claim），拦截 ---
        wd, state = fresh_job(tmp, "t2")
        run("advance", "--state", state, "--stage", "claim", "--actor", "worker-1",
            "--artifact", touch(wd, "claim.json"), "--attempt-id", "att-1", expect=1)

        # --- 用例 3：契约缺项（缺预算与成功标准），拦截 ---
        wd, state = fresh_job(tmp, "t3")
        run("advance", "--state", state, "--stage", "contract", "--actor", "twt",
            "--artifact", touch(wd, "contract.json"),
            "--contract-goal", "写一篇文章", "--input-version", "v1.0",
            "--output-schema", "article.md", expect=1)

        # --- 用例 4：租约过期后执行，拦截；重派新 attempt 后可继续 ---
        wd, state = fresh_job(tmp, "t4")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        expire_lease(state)
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-1",
            "--artifact", touch(wd, "result.md"), "--attempt-id", "att-1", expect=1)
        run("heartbeat", "--state", state, "--attempt-id", "att-1", expect=1)
        run("advance", "--state", state, "--stage", "claim", "--actor", "worker-2",
            "--artifact", touch(wd, "claim2.json"), "--attempt-id", "att-2", expect=0)
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-2",
            "--artifact", touch(wd, "result.md"), "--attempt-id", "att-2", expect=0)
        run("advance", "--state", state, "--stage", "receipt", "--actor", "twt",
            "--artifact", touch(wd, "receipt.json"), "--attempt-id", "att-2", expect=0)
        run("check", "--state", state, expect=0)

        # --- 用例 5：重复 attempt ID 认领，拦截 ---
        wd, state = fresh_job(tmp, "t5")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        expire_lease(state)
        run("advance", "--state", state, "--stage", "claim", "--actor", "worker-2",
            "--artifact", touch(wd, "claim2.json"), "--attempt-id", "att-1", expect=1)

        # --- 用例 6：迟到回调（旧 attempt 回执，已重派给新 attempt），拦截 ---
        wd, state = fresh_job(tmp, "t6")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        expire_lease(state)
        run("advance", "--state", state, "--stage", "claim", "--actor", "worker-2",
            "--artifact", touch(wd, "claim2.json"), "--attempt-id", "att-2", expect=0)
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-2",
            "--artifact", touch(wd, "result.md"), "--attempt-id", "att-2", expect=0)
        run("advance", "--state", state, "--stage", "receipt", "--actor", "twt",
            "--artifact", touch(wd, "receipt.json"), "--attempt-id", "att-1", expect=1)

        # --- 用例 7：手工篡改 state（execute attempt 与租约不一致），check 抓获 ---
        wd, state = fresh_job(tmp, "t7")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["stages"]["execute"] = {"status": "done", "actor": "worker-1",
                                   "artifact": "result.md",
                                   "ts": "2026-11-10T00:00:00+00:00",
                                   "attempt_id": "att-OLD"}
        st["stages"]["receipt"] = {"status": "done", "actor": "twt",
                                   "artifact": "receipt.json",
                                   "ts": "2026-11-10T00:00:00+00:00",
                                   "attempt_id": "att-OLD"}
        st["receipt_count"] = 1
        st["receipt_attempt_id"] = "att-OLD"
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        touch(wd, "result.md")
        touch(wd, "receipt.json")
        run("check", "--state", state, expect=1)

        # --- 用例 8：有效租约期内重复认领，拦截 ---
        wd, state = fresh_job(tmp, "t8")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        run("advance", "--state", state, "--stage", "claim", "--actor", "worker-2",
            "--artifact", touch(wd, "claim2.json"), "--attempt-id", "att-2", expect=1)

        # --- 用例 9：重复回执不得重复记账，拦截 ---
        wd, state = fresh_job(tmp, "t9")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-1",
            "--artifact", touch(wd, "result.md"), "--attempt-id", "att-1", expect=0)
        run("advance", "--state", state, "--stage", "receipt", "--actor", "twt",
            "--artifact", touch(wd, "receipt.json"), "--attempt-id", "att-1", expect=0)
        run("advance", "--state", state, "--stage", "receipt", "--actor", "twt",
            "--artifact", touch(wd, "receipt.json"), "--attempt-id", "att-1", expect=1)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["receipt_count"] == 1, "重复回执不得重复记账"
        print("PASS: 用例 9 receipt_count 仍为 1")
        PASS += 1

        # --- 用例 10：缺 attempt ID 执行，拦截 ---
        wd, state = fresh_job(tmp, "t10")
        do_contract(wd, state)
        do_claim(wd, state, "att-1")
        run("advance", "--state", state, "--stage", "execute", "--actor", "worker-1",
            "--artifact", touch(wd, "result.md"), expect=1)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
