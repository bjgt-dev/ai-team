#!/usr/bin/env python3
"""test_memory_gate.py — 记忆与知识库 SOP 门禁测试。

正向全流程必须通过；每个违规用例必须被拦截（exit 1）。
跑法：python3 test_memory_gate.py [gate 路径]
"""
import json
import os
import subprocess
import sys
import tempfile

GATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "memory-gate.py")

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


def fresh_kb(tmp, kb_id):
    wd = os.path.join(tmp, kb_id)
    run("init", "--kb-id", kb_id, "--workdir", wd, expect=0)
    return wd, os.path.join(wd, "memory-sop-state.json")


def touch(wd, name, text="这是一条值得记录的决策事实内容"):
    p = os.path.join(wd, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return name


def propose(state, eid, op, actor, content, tier, evidence, key="", age_days=None):
    args = ["propose", "--state", state, "--entry-id", eid,
            "--operation", op, "--actor", actor, "--content", content,
            "--tier", tier, "--evidence", evidence]
    if key:
        args += ["--key", key]
    if age_days is not None:
        args += ["--age-days", str(age_days)]
    return run(*args, expect=0)


def main():
    global PASS
    with tempfile.TemporaryDirectory() as tmp:
        # --- 用例 1：正向全流程（ADD → 五检查 → 应用 → 整合）---
        wd, state = fresh_kb(tmp, "t1")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "agreement", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=0)
        run("consolidate", "--state", state, "--actor", "twt",
            "--artifact", touch(wd, "consolidate.md", "整合记录：无过期条目"), expect=0)
        run("check", "--state", state, expect=0)

        # --- 用例 2：内容含敏感信息，脱敏检查拦截 ---
        wd, state = fresh_kb(tmp, "t2")
        propose(state, "e1", "ADD", "twt",
                touch(wd, "e1.md", "网关密钥在这里 api_key = abcdefgh12345678 请勿外传"),
                "agreement", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e1", expect=1)

        # --- 用例 3：重复 key 写入，拦截 ---
        wd, state = fresh_kb(tmp, "t3")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "agreement", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=0)
        propose(state, "e2", "ADD", "twt", touch(wd, "e2.md"),
                "agreement", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e2", expect=1)

        # --- 用例 4：新证据弱于旧证据，keep_new 拦截；keep_both_pending 放行 ---
        wd, state = fresh_kb(tmp, "t4")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "architecture", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=0)
        propose(state, "e2", "SUPERSEDE", "twt", touch(wd, "e2.md"),
                "architecture", "low", key="k1")
        run("check5", "--state", state, "--entry-id", "e2", expect=1)
        run("resolve", "--state", state, "--entry-id", "e2",
            "--decision", "keep_new", expect=1)
        run("resolve", "--state", state, "--entry-id", "e2",
            "--decision", "keep_both_pending", expect=0)
        run("apply", "--state", state, "--entry-id", "e2",
            "--reviewer", "da-guan-jia", expect=0)

        # --- 用例 5：未过五检查就应用，拦截 ---
        wd, state = fresh_kb(tmp, "t5")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "agreement", "high", key="k1")
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=1)

        # --- 用例 6：审核人就是提议人，拦截 ---
        wd, state = fresh_kb(tmp, "t6")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "agreement", "high", key="k1")
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "twt", expect=1)

        # --- 用例 7：已过期条目应用，拦截 ---
        wd, state = fresh_kb(tmp, "t7")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "session", "high", key="k1", age_days=5)  # 会话层 TTL 1 天，已过期
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=1)

        # --- 用例 8：SUPERSEDE 无所指旧条目，拦截 ---
        wd, state = fresh_kb(tmp, "t8")
        run("propose", "--state", state, "--entry-id", "e1",
            "--operation", "SUPERSEDE", "--actor", "twt",
            "--content", touch(wd, "e1.md"), "--tier", "agreement",
            "--evidence", "high", "--key", "不存在的key", expect=1)

        # --- 用例 9：手工篡改 state（已应用但审核人=提议人），check 抓获 ---
        wd, state = fresh_kb(tmp, "t9")
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        st["entries"]["e1"] = {
            "operation": "ADD", "proposer": "twt", "reviewer": "twt",
            "content": "e1.md", "tier": "agreement", "ttl_days": 90,
            "evidence": "high", "key": "k1", "target": None,
            "created_ts": 1790000000.0, "status": "applied",
            "checks": {}, "conflict": None, "resolution": None}
        st["keys"]["k1"] = "e1"
        touch(wd, "e1.md")
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("check", "--state", state, expect=1)

        # --- 用例 10：整合使过期条目失效 ---
        wd, state = fresh_kb(tmp, "t10")
        propose(state, "e1", "ADD", "twt", touch(wd, "e1.md"),
                "session", "high", key="k1", age_days=0)
        run("check5", "--state", state, "--entry-id", "e1", expect=0)
        run("apply", "--state", state, "--entry-id", "e1",
            "--reviewer", "da-guan-jia", expect=0)
        # 把创建时间改到 2 天前（会话层 TTL 1 天），整合应使其过期
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        import time as _time
        st["entries"]["e1"]["created_ts"] = _time.time() - 2 * 86400
        with open(state, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        run("consolidate", "--state", state, "--actor", "twt",
            "--artifact", touch(wd, "consolidate.md", "整合记录：e1 到期"), expect=0)
        with open(state, encoding="utf-8") as f:
            st = json.load(f)
        assert st["entries"]["e1"]["status"] == "expired", "整合后 e1 应过期"
        print("PASS: 整合使过期条目失效")
        PASS += 1
        run("check", "--state", state, expect=0)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
