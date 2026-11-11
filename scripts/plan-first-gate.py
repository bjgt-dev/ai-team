#!/usr/bin/env python3
"""plan-first-gate.py — 规划与执行分离门禁（AGENTS.md，2026-11-05 董事长定）。

默认第一轮只给分析计划，不做图、不写代码、不部署、不改生产。计划须一次给全
八段：背景、目标、不做、方案选项、评估标准（contract）、风险、回滚、需拍板点。
董事长确认后再进执行轮；执行中偏离计划先停、先报。
例外只有三类：董事长明确说直接做、已批执行令、已批的紧急可逆小修复。

用法：
  python3 plan-first-gate.py check-plan <plan.md> [--exception direct|approved-order|emergency-reversible]
      校验计划文件含八段；无例外时缺一段即 REFUSE。
  python3 plan-first-gate.py check-task <state/<task>/> [--exception ...]
      任务目录须有 PLAN-*.md 且八段齐全；若目录已含执行产物（代码/部署/交付文件）
      而无 approval 标记（APPROVED 或 butler-verdicts/<task>.json 为通过/有条件通过）
      即 REFUSE（上来就干）。
退出码：0=PASS，1=REFUSE。只读，不改生产数据。
"""
import argparse
import json
import os
import re
import sys

SECTIONS = [
    ("背景", ["背景"]),
    ("目标", ["目标"]),
    ("不做", ["不做", "不做事项", "范围外"]),
    ("方案选项", ["方案选项", "方案", "选项"]),
    ("评估标准", ["评估标准", "contract", "验收标准", "核验标准"]),
    ("风险", ["风险"]),
    ("回滚", ["回滚"]),
    ("需拍板点", ["需拍板点", "拍板点", "需确认", "待拍板"]),
]
EXCEPTIONS = {"direct", "approved-order", "emergency-reversible"}
EXECUTION_SUFFIXES = (".py", ".js", ".sh", ".ts")
EXECUTION_NAMES = ("DELIVERABLE", "OUTPUT", "deploy", "DEPLOY")


def _norm(text):
    return re.sub(r"\s+", "", text)


def check_plan(path):
    errors = []
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return ["计划文件不可读：%s" % e]
    norm = _norm(text)
    for label, keys in SECTIONS:
        if not any(_norm(k) in norm for k in keys):
            errors.append("计划缺段：%s" % label)
    return errors


def _has_approval(task_dir, task):
    if os.path.exists(os.path.join(task_dir, "APPROVED")):
        return True
    verdict = "/home/ubuntu/ai-team/state/butler-verdicts/%s.json" % task
    try:
        d = json.load(open(verdict, encoding="utf-8"))
        return d.get("verdict") in ("通过", "有条件通过")
    except (OSError, ValueError):
        return False


def check_task(task_dir, exception=None):
    task = os.path.basename(os.path.normpath(task_dir))
    errors = []
    plans = sorted(f for f in os.listdir(task_dir)
                   if f.startswith("PLAN-") and f.endswith(".md")) \
        if os.path.isdir(task_dir) else []
    if not plans:
        return ["任务目录无 PLAN-*.md：先出计划再执行"]
    for p in plans:
        errors += check_plan(os.path.join(task_dir, p))
    if exception:
        return errors  # 例外三类：计划段仍须齐，但允许未批先行
    produced = []
    for root, _dirs, files in os.walk(task_dir):
        for f in files:
            if f.startswith("PLAN-"):
                continue
            if f.endswith(EXECUTION_SUFFIXES) or any(k in f for k in EXECUTION_NAMES):
                produced.append(os.path.join(root, f))
    if produced and not _has_approval(task_dir, task):
        errors.append("已有执行产物但无董事长确认/APPROVED/大管家通过结论：%s"
                      % "; ".join(sorted(produced)[:5]))
    return errors


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("check-plan")
    p1.add_argument("plan")
    p1.add_argument("--exception", choices=sorted(EXCEPTIONS))
    p2 = sub.add_parser("check-task")
    p2.add_argument("task_dir")
    p2.add_argument("--exception", choices=sorted(EXCEPTIONS))
    args = ap.parse_args()
    if args.cmd == "check-plan":
        errors = check_plan(args.plan)
    else:
        errors = check_task(args.task_dir, args.exception)
    if errors:
        print("PLAN-FIRST GATE REFUSED")
        for e in errors:
            print("  - %s" % e)
        return 1
    print("PLAN-FIRST GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
