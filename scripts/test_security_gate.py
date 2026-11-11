#!/usr/bin/env python3
"""test_security_gate.py — 安全权限 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
注意：本测试只用 token/审批单占位 ID，不产生、不输出任何真实密钥/token 值。
跑法：python3 test_security_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "security-gate.py")

PASS = 0
FAIL = 0
FUTURE = "2030-01-01T00:00:00Z"
PAST = "2020-01-01T00:00:00Z"


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


def fresh_job(tmp, job_id="sec-test"):
    wd = os.path.join(tmp, job_id)
    run("init", "--job-id", job_id, "--workdir", wd, expect=0)
    return wd, os.path.join(wd, "security-sop-state.json")


def audit_log_path(wd):
    return os.path.join(wd, "audit.log")


def write_audit(wd, agent, tool, token, result="ok"):
    p = audit_log_path(wd)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-11-10T00:00:00+00:00", "agent": agent,
                            "tool": tool, "token": token, "result": result},
                           ensure_ascii=False) + "\n")


def register(wd, state, agent="worker-1"):
    run("advance", "--state", state, "--stage", "register", "--agent", agent,
        "--owner", "twt", "--purpose", "测试任务", "--expires-at", FUTURE, expect=0)


def grant(wd, state, agent, tool, token):
    run("advance", "--state", state, "--stage", "grant", "--agent", agent,
        "--tool", tool, "--token", token, "--expires-at", FUTURE, expect=0)


def execute(wd, state, agent, tool, token, approval=None, high=False):
    args = ["advance", "--state", state, "--stage", "execute", "--agent", agent,
            "--tool", tool, "--token", token, "--audit-log", audit_log_path(wd)]
    if approval:
        args += ["--approval", approval]
    if high:
        args.append("--high-impact")
    return run(*args, expect=0)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程通过（register -> grant -> execute -> audit -> revoke）---
        wd, state = fresh_job(tmp, "t1")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        grant(wd, state, "worker-1", "deploy", "tok-002")
        run("approve", "--state", state, "--approval-id", "appr-001",
            "--agent", "worker-1", "--action", "deploy", expect=0)
        write_audit(wd, "worker-1", "read_file", "tok-001")
        execute(wd, state, "worker-1", "read_file", "tok-001")
        write_audit(wd, "worker-1", "deploy", "tok-002")
        execute(wd, state, "worker-1", "deploy", "tok-002",
                approval="appr-001", high=True)
        run("advance", "--state", state, "--stage", "audit", "--agent", "worker-1",
            "--audit-log", audit_log_path(wd), expect=0)
        run("advance", "--state", state, "--stage", "revoke", "--agent", "worker-1", expect=0)
        run("check", "--state", state, expect=0)
        run("status", "--state", state, expect=0)

        # --- 用例 2：未登记身份执行，拦截 ---
        wd, state = fresh_job(tmp, "t2")
        write_audit(wd, "ghost-1", "read_file", "tok-999")
        run("advance", "--state", state, "--stage", "execute", "--agent", "ghost-1",
            "--tool", "read_file", "--token", "tok-999",
            "--audit-log", audit_log_path(wd), expect=1)

        # --- 用例 3：越权工具调用（只授 read_file，却调 write_file），拦截 ---
        wd, state = fresh_job(tmp, "t3")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        write_audit(wd, "worker-1", "write_file", "tok-001")
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "write_file", "--token", "tok-001",
            "--audit-log", audit_log_path(wd), expect=1)

        # --- 用例 4：过期 token 调用，拦截（grant 有效 token 后把 state 里有效期改成过去）---
        wd, state = fresh_job(tmp, "t4")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["tokens"]["tok-001"]["expires_at"] = PAST
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        write_audit(wd, "worker-1", "read_file", "tok-001")
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "read_file", "--token", "tok-001",
            "--audit-log", audit_log_path(wd), expect=1)

        # --- 用例 5：重放已消费审批单，拦截 ---
        wd, state = fresh_job(tmp, "t5")
        register(wd, state)
        grant(wd, state, "worker-1", "deploy", "tok-002")
        run("approve", "--state", state, "--approval-id", "appr-001",
            "--agent", "worker-1", "--action", "deploy", expect=0)
        write_audit(wd, "worker-1", "deploy", "tok-002")
        execute(wd, state, "worker-1", "deploy", "tok-002",
                approval="appr-001", high=True)
        write_audit(wd, "worker-1", "deploy", "tok-002")
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "deploy", "--token", "tok-002",
            "--audit-log", audit_log_path(wd),
            "--approval", "appr-001", "--high-impact", expect=1)

        # --- 用例 6：无审批做高影响动作，拦截 ---
        wd, state = fresh_job(tmp, "t6")
        register(wd, state)
        grant(wd, state, "worker-1", "deploy", "tok-002")
        write_audit(wd, "worker-1", "deploy", "tok-002")
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "deploy", "--token", "tok-002",
            "--audit-log", audit_log_path(wd), "--high-impact", expect=1)

        # --- 用例 7：手工篡改 state（伪造一条无授权的执行记录），check 抓获 ---
        wd, state = fresh_job(tmp, "t7")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        write_audit(wd, "worker-1", "read_file", "tok-001")
        execute(wd, state, "worker-1", "read_file", "tok-001")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["executions"].append({"agent": "worker-1", "tool": "write_file",
                                 "token": "tok-001", "approval": None,
                                 "high_impact": False, "ts": "2026-11-10T00:00:00+00:00",
                                 "audit_log": audit_log_path(wd)})
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 8：撤销后调用必拒 ---
        wd, state = fresh_job(tmp, "t8")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        write_audit(wd, "worker-1", "read_file", "tok-001")
        execute(wd, state, "worker-1", "read_file", "tok-001")
        run("advance", "--state", state, "--stage", "audit", "--agent", "worker-1",
            "--audit-log", audit_log_path(wd), expect=0)
        run("advance", "--state", state, "--stage", "revoke", "--agent", "worker-1", expect=0)
        write_audit(wd, "worker-1", "read_file", "tok-001")
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "read_file", "--token", "tok-001",
            "--audit-log", audit_log_path(wd), expect=1)

        # --- 用例 9：审计日志缺条目，执行拦截 ---
        wd, state = fresh_job(tmp, "t9")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        open(audit_log_path(wd), "w", encoding="utf-8").close()  # 空日志，无条目
        run("advance", "--state", state, "--stage", "execute", "--agent", "worker-1",
            "--tool", "read_file", "--token", "tok-001",
            "--audit-log", audit_log_path(wd), expect=1)

        # --- 用例 10：跳步（未 register 就 grant），拦截 ---
        wd, state = fresh_job(tmp, "t10")
        run("advance", "--state", state, "--stage", "grant", "--agent", "worker-1",
            "--tool", "read_file", "--token", "tok-001",
            "--expires-at", FUTURE, expect=1)

        # --- 用例 11：白名单外工具签发，拦截 ---
        wd, state = fresh_job(tmp, "t11")
        register(wd, state)
        run("advance", "--state", state, "--stage", "grant", "--agent", "worker-1",
            "--tool", "rm_rf", "--token", "tok-001",
            "--expires-at", FUTURE, expect=1)

        # --- 用例 12：revoke-drill 终态同样走通（演练路径）---
        wd, state = fresh_job(tmp, "t12")
        register(wd, state)
        grant(wd, state, "worker-1", "read_file", "tok-001")
        write_audit(wd, "worker-1", "read_file", "tok-001")
        execute(wd, state, "worker-1", "read_file", "tok-001")
        run("advance", "--state", state, "--stage", "audit", "--agent", "worker-1",
            "--audit-log", audit_log_path(wd), expect=0)
        run("advance", "--state", state, "--stage", "revoke-drill",
            "--agent", "worker-1", expect=0)
        run("check", "--state", state, expect=0)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
