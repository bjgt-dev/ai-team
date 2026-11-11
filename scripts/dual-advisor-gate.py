#!/usr/bin/env python3
"""dual-advisor-gate.py — 三方协作双顾问门禁（AGENTS.md，2026-09-27 用户确认）。

任何有实质内容的产出（代码、文章、视频脚本、推广方案）都必须经过双顾问流程，
不得跳过。流程：ChatGPT 发散 → 我收敛成方案 → Claude 批判 → 我修订 → 双顾问确认。
"同意"定义：无否决性意见；修订最多两轮；两轮后仍不一致 → 汇总交董事长拍板。

本门禁校验产出说明文件（方案/计划/复盘文档）是否留有双顾问过审记录：
  须同时出现 ChatGPT 发散记录与 Claude 批判记录，以及双顾问确认（无否决性意见），
  且修订轮次 <= 2（出现"第 3 轮修订/修订 3 轮"即 REFUSE，应转交董事长拍板）。
触发门之外（日常执行）不适用：--routine 可豁免，但须在文件中写明"日常执行，
不触发双顾问门"。
用法：python3 dual-advisor-gate.py <file.md> [--routine]
退出码：0=PASS；1=REFUSE（缺发散/批判/确认其一，或超两轮修订）。
"""
import argparse
import re
import sys

CHATGPT_MARKERS = ("ChatGPT", "chatgpt", "发散")
CLAUDE_MARKERS = ("Claude", "claude", "批判")
CONFIRM_MARKERS = ("双顾问确认", "双顾问通过", "无否决性意见", "两顾问都同意",
                   "顾问确认", "过审")
ROUTINE_MARKER = "不触发双顾问门"


def check(path, routine=False):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return ["文件不可读：%s" % e]
    if routine and ROUTINE_MARKER in text:
        return []
    errors = []
    if not any(m in text for m in CHATGPT_MARKERS):
        errors.append("缺 ChatGPT 发散记录")
    if not any(m in text for m in CLAUDE_MARKERS):
        errors.append("缺 Claude 批判记录")
    if not any(m in text for m in CONFIRM_MARKERS):
        errors.append("缺双顾问确认（无否决性意见/双顾问通过）")
    m = re.search(r"(?:第\s*([3-9])\s*轮修订|修订\s*([3-9])\s*轮)", text)
    if m:
        errors.append("修订已达 %s 轮（>2 轮）：应汇总双方意见交董事长拍板，不得再修订"
                      % (m.group(1) or m.group(2)))
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--routine", action="store_true")
    args = ap.parse_args()
    errors = check(args.path, args.routine)
    if errors:
        print("DUAL-ADVISOR GATE REFUSED: %s" % args.path)
        for e in errors:
            print("  - %s" % e)
        return 1
    print("DUAL-ADVISOR GATE PASS: %s" % args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
