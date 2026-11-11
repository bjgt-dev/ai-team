#!/usr/bin/env python3
"""four-category-audit.py — 四类检查审计（AGENTS.md，2026-11-07 董事长骂后立）。

four-category-gate.py 已接入决策队列写入点（ceo-seat/work/notify.py：写入时打
needs_chairman/category），但历史条目与旁路写入可能缺标签。本审计扫
state/decision-queue.json 全表：
  - 每条 pending 须带 needs_chairman（bool）与 category/reason；
  - 用 four-category-gate.py 重算：门禁判需董事长而条目打 False 即违规；
    门禁判自决而条目打 True 不判违规（宁严勿松）。
用法：python3 four-category-audit.py [--queue PATH]
退出码：0=全表干净；1=有缺标签/误标（>0 即违规）；2=队列不可读。
"""
import argparse
import json
import os
import sys

import importlib.util
_gate = None
try:
    _spec = importlib.util.spec_from_file_location(
        "fcg", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "four-category-gate.py"))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _gate = _mod.needs_chairman
except Exception:  # noqa: BLE001 — 门禁缺失时审计只查标签，不崩
    _gate = None

DEFAULT_QUEUE = "/home/ubuntu/ai-team/state/decision-queue.json"


def audit(queue_path):
    try:
        d = json.load(open(queue_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("决策队列不可读：%s" % e)
        return 2, []
    pending = d.get("pending", []) if isinstance(d, dict) else []
    problems = []
    for i, item in enumerate(pending):
        tag = item.get("id", item.get("task_id", "?"))
        if "needs_chairman" not in item:
            problems.append("pending[%d] %s：缺 needs_chairman 标签" % (i, tag))
            continue
        if not isinstance(item.get("needs_chairman"), bool):
            problems.append("pending[%d] %s：needs_chairman 非 bool" % (i, tag))
            continue
        if _gate is not None:
            need, reason = _gate(item)
            if need and item.get("needs_chairman") is False:
                problems.append("pending[%d] %s：误标自决，门禁判需董事长（%s）"
                                % (i, tag, reason))
        if item.get("needs_chairman") is True and not item.get("category"):
            problems.append("pending[%d] %s：需董事长但缺 category" % (i, tag))
    return (1 if problems else 0), problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default=DEFAULT_QUEUE)
    args = ap.parse_args()
    rc, problems = audit(args.queue)
    print("决策队列审计：pending 项，违规 %d 条" % len(problems))
    for p in problems:
        print("  - %s" % p)
    return rc


if __name__ == "__main__":
    sys.exit(main())
