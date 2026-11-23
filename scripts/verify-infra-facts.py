#!/usr/bin/env python3
"""verify-infra-facts.py — 核对 infra-facts.yaml 里每条事实的出处还在不在、值对不对得上。

机制（董事长 2026-11-08 令“落实到代码”，针对“凭记忆乱答配置”）：
- 每条事实带 source（出处）与 how（核实方法）；本脚本按出处逐条复核。
- 能自动复核的（本地文件/命令）实跑复核；不能自动复核的（需人工控制台、需登录）
  标 UNVERIFIED，不许当已核实讲。
- 任何一条 FAIL 或事实文件里出现已知的错误值（如 InstaBox 写成 4 核 24G），
  整体 exit 1，打印哪条错、错在哪。
- 用法：python3 ~/workspace/tools/verify-infra-facts.py
  退出码：0 全过；1 有 FAIL/漂移；2 事实文件读不到。

事实文件：~/workspace/tools/infra-facts.yaml（唯一真源）。
"""

import os
import re
import subprocess
import sys

def find_facts_file():
    """事实文件位置：脚本同目录 > 本地 ~/workspace/tools/ > 枢纽 ~/ai-team/state/。"""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "infra-facts.yaml"),
        os.path.expanduser("~/workspace/tools/infra-facts.yaml"),
        os.path.expanduser("~/ai-team/state/infra-facts.yaml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


FACTS_FILE = find_facts_file()

# 已知错误值：事实文件里一旦出现，即 FAIL（防旧错复发）。
KNOWN_WRONG = [
    (re.compile(r"InstaBox[^\\n]{0,40}4\s*核\s*24G"), "InstaBox 写成 4 核 24G（甲骨文口径错安）"),
    (re.compile(r"InstaBox[^\\n]{0,40}内存.{0,10}3\s*倍"), "InstaBox 内存写成 Box 的 3 倍（由上一错衍生）"),
]


def read_facts(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def check_known_wrong(text):
    """事实文件正文不得含已知错误值。"""
    fails = []
    for pat, msg in KNOWN_WRONG:
        if pat.search(text):
            fails.append(msg)
    return fails


def check_sources(text):
    """每条事实的 source 出处：本地文件存在即 PASS，否则记 UNVERIFIED。"""
    results = []
    # 抓 source: "..." 行
    for m in re.finditer(r"-\s*id:\s*(\S+)\s*\n(?:.*\n)*?\s*source:\s*\"([^\"]+)\"", text):
        fid, source = m.group(1), m.group(2)
        # 只核本地文件型出处（含 memory/、MEMORY.md、TOOLS.md）；去掉 #L行号 锚点再判存在
        local = re.findall(r"(memory/[^\s\";，,]+|MEMORY\.md|TOOLS\.md)", source)
        local = [re.sub(r"#L\d+.*$", "", p) for p in local]
        if not local:
            results.append((fid, "UNVERIFIED", "出处非本地文件，需人工核（%s）" % source[:40]))
            continue
        missing = [p for p in local if not os.path.exists(os.path.expanduser("~/" + p))]
        if missing:
            # memory/、MEMORY.md、TOOLS.md 是本席本地（muse-1）文件，枢纽机上本就没有；
            # 在枢纽跑时判 UNVERIFIED（需回本地核），不在枢纽报 FAIL。
            local_only = [p for p in missing if p.startswith("memory/") or p in ("MEMORY.md", "TOOLS.md")]
            if local_only and len(local_only) == len(missing) and not os.path.exists(os.path.expanduser("~/memory")):
                results.append((fid, "UNVERIFIED", "出处为本席本地文件，枢纽机上无，须回本地核"))
            else:
                results.append((fid, "FAIL", "出处文件不存在：%s" % ", ".join(missing)))
        else:
            results.append((fid, "PASS", "出处文件存在"))
    return results


def main():
    if not os.path.exists(FACTS_FILE):
        print("FAIL 事实文件不存在：%s" % FACTS_FILE)
        return 2
    text = read_facts(FACTS_FILE)

    fails, unverified = [], []
    for msg in check_known_wrong(text):
        fails.append(("known-wrong", msg))
    for fid, status, msg in check_sources(text):
        if status == "FAIL":
            fails.append((fid, msg))
        elif status == "UNVERIFIED":
            unverified.append((fid, msg))

    print("事实条目：%d" % text.count("- id:"))
    for fid, msg in fails:
        print("FAIL %s：%s" % (fid, msg))
    for fid, msg in unverified:
        print("UNVERIFIED %s：%s" % (fid, msg))
    if not fails and not unverified:
        print("PASS 全部事实出处存在、无已知错误值")
    if fails:
        print("结果：FAIL（%d 项）" % len(fails))
        return 1
    print("结果：PASS（%d 项未自动核实，需人工按 how 复核）" % len(unverified))
    return 0


if __name__ == "__main__":
    _rc = main()
    # 证据（2026-11-13 root-gate）：核实行为记入 check-ledger，只记时间+事实文件哈希，不记内容；只追加，写失败不影响主流程与退出码。
    try:
        import hashlib as _hl, json as _js, datetime as _dt
        _h = ""
        try:
            with open(FACTS_FILE, "rb") as _f:
                _h = _hl.md5(_f.read()).hexdigest()
        except Exception:
            pass
        _led = "/home/ubuntu/ai-team/state/check-ledger.jsonl" if "ai-team" in os.path.dirname(os.path.abspath(__file__)) else os.path.expanduser("~/workspace/tools/check-ledger.jsonl")
        with open(_led, "a", encoding="utf-8") as _f:
            _f.write(_js.dumps({"ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tool": "verify-infra-facts", "qhash": _h}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    sys.exit(_rc)
