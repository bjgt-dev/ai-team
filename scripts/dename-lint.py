#!/usr/bin/env python3
"""dename-lint.py — 输出一律脱名 lint（AGENTS.md，2026-11-05 董事长纠正）。

聊天、文档、梳理输出里只称"董事长"，绝不写真实姓名；文件正文用角色词，
不写死人名。另合并且落地"对外脱敏"（2026-10-05）：对外沟通、跨平台操作
不带真实姓名、地址、完整账号、密码 token 等隐私。

默认档：扫描文件是否出现董事长真实姓名（由环境变量 CHAIRMAN_REAL_NAME 指定）——出现即 FAIL。
--strict 档（对外发布/对外沟通前用）：另查完整个人邮箱、手机号、身份证号、
  形如 password/token/secret 的明文赋值。
注意：本脚本不含任何真实凭据，只做模式匹配；姓名是董事长已公开给本席的
称呼对照，不是凭据。
用法：python3 dename-lint.py <file...> [--strict]
退出码：0=通过；1=命中脱名/脱敏违规。
"""
import os
import argparse
import re
import sys

REAL_NAME = os.environ.get("CHAIRMAN_REAL_NAME", "")
STRICT_PATTERNS = [
    ("完整个人邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("明文凭据赋值", re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*['\"]?\S{4,}")),
]


def lint_file(path, strict=False):
    hits = []
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError as e:
        return ["文件不可读：%s" % e]
    for i, line in enumerate(text.splitlines(), 1):
        if REAL_NAME and REAL_NAME in line:
            hits.append("%s 第 %d 行出现真实姓名：%s"
                        % (path, i, line.strip()[:80]))
    if strict:
        for label, pat in STRICT_PATTERNS:
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    hits.append("%s 第 %d 行疑似%s：%s"
                                % (path, i, label, line.strip()[:80]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    hits = []
    for p in args.paths:
        hits += lint_file(p, args.strict)
    for h in hits:
        print("  - %s" % h)
    if hits:
        print("DENAME LINT FAIL：%d 处违规（只称董事长，不写真实姓名）" % len(hits))
        return 1
    print("DENAME LINT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
