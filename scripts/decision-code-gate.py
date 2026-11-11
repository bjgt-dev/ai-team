#!/usr/bin/env python3
"""Decision code gate (2026-11-07 董事长令：定案必须落地到代码)。

任何定案在标记 DONE 之前，必须通过本门禁：
- 必须引用至少一个代码文件（脚本/门禁/监控），且该文件存在。
- 必须有测试证据（测试输出、验收日志、sha），证明代码跑过。
- 只有文档、只有 SOP、只有口头承诺，一律拒收。

用法：
  python3 decision-code-gate.py --decision <decision-id> --code-files <path1,path2> --evidence <path1,path2>
  返回 0 = 通过，1 = 拒收（无代码或无证据）。
"""
import argparse
import os
import sys

def main():
    p = argparse.ArgumentParser(description="Decision code gate")
    p.add_argument("--decision", required=True, help="Decision ID (e.g. dec-20261107-004)")
    p.add_argument("--code-files", required=True, help="Comma-separated code file paths (must exist)")
    p.add_argument("--evidence", required=True, help="Comma-separated evidence paths (test output, logs, sha)")
    args = p.parse_args()

    code_files = [f.strip() for f in args.code_files.split(",") if f.strip()]
    evidence = [f.strip() for f in args.evidence.split(",") if f.strip()]

    errors = []
    if not code_files:
        errors.append("no code files: decision must reference at least one code file")
    for f in code_files:
        if not os.path.exists(f):
            errors.append(f"code file missing: {f}")
    if not evidence:
        errors.append("no evidence: decision must have test/acceptance evidence")
    for f in evidence:
        if not os.path.exists(f):
            errors.append(f"evidence missing: {f}")

    if errors:
        print(f"DECISION CODE GATE REFUSED: {args.decision}")
        for e in errors:
            print(f"  - {e}")
        print("  Rule: 定案必须落地到代码，文档不是机制。")
        return 1

    print(f"DECISION CODE GATE PASS: {args.decision}")
    print(f"  code files: {len(code_files)}")
    print(f"  evidence: {len(evidence)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
