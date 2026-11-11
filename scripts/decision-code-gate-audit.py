#!/usr/bin/env python3
"""decision-code-gate-audit.py — 定案必须落地到代码·台账审计（AGENTS.md，2026-11-07 董事长令）。

decision-code-gate.py 已存在，但全枢纽零调用点——定案标 DONE 时无人跑它。
本审计把它的原则接进执行台账：扫 execution-ledger.md 表中全部 DONE 行，
每行证据须同时满足两点，否则违规（只写文档、只写 SOP 不算定案完成）：
  1. 引用至少一个代码文件（路径含 scripts/ 或以 .py/.sh 结尾）；
  2. 引用测试/验收证据（测试结果、回执、receipt、sha、TEST-RESULTS、日志）。
只满足其一、或证据为"—"/空，即违规。DROP 行不审计。
用法：python3 decision-code-gate-audit.py [--ledger PATH]
退出码：0=全部 DONE 行合规；1=有违规；2=台账不可读。
"""
import argparse
import re
import sys

DEFAULT_LEDGER = "/home/ubuntu/ai-team/state/execution-ledger.md"
CODE_RE = re.compile(r"scripts/|\.py\b|\.sh\b")
EVIDENCE_RE = re.compile(r"测试|回执|receipt|sha|TEST-RESULTS|日志|验收|PASS|FAIL|py_compile|全过|通过")


def parse_rows(path):
    rows = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError as e:
        raise
    in_table = False
    for line in lines:
        if line.startswith("| 决策/事项 |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5:
                continue
            rows.append({"item": cells[0], "status": cells[1],
                         "note": cells[2], "deadline": cells[3],
                         "evidence": cells[4]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    args = ap.parse_args()
    try:
        rows = parse_rows(args.ledger)
    except OSError as e:
        print("台账不可读：%s" % e)
        return 2
    done = [r for r in rows if r["status"] == "DONE"]
    problems = []
    for r in done:
        ev = r["evidence"]
        if ev in ("—", "", "无"):
            problems.append("DONE 无证据：%s" % r["item"])
            continue
        if not CODE_RE.search(ev):
            problems.append("DONE 证据无代码文件引用（定案未落地到代码）：%s" % r["item"])
        if not EVIDENCE_RE.search(ev):
            problems.append("DONE 证据无测试/验收证据：%s" % r["item"])
    print("定案代码门禁审计：DONE %d 项，违规 %d 条" % (len(done), len(problems)))
    for p in problems:
        print("  - %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
