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
单顾问豁免（2026-11-25 董事长单顾问推进令，知识库
knowledge/机制/single-advisor-proceed-20261125.md）：批判通道因故障跑不成时，
--single-advisor 只要求 ChatGPT（发散顾问）确认 + 结构通过，缺失的 Claude
批判评审记为待补项（写入本脚本同目录的 dual-advisor-gate-ledger.jsonl，留痕），
不阻塞；不开启时默认双顾问行为不变。
用法：python3 dual-advisor-gate.py <file.md> [--routine] [--single-advisor]
退出码：0=PASS；1=REFUSE（缺发散/批判/确认其一，或超两轮修订）。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

CHATGPT_MARKERS = ("ChatGPT", "chatgpt", "发散")
CLAUDE_MARKERS = ("Claude", "claude", "批判")
CONFIRM_MARKERS = ("双顾问确认", "双顾问通过", "无否决性意见", "两顾问都同意",
                   "顾问确认", "过审")
ROUTINE_MARKER = "不触发双顾问门"
SINGLE_ADVISOR_LEDGER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dual-advisor-gate-ledger.jsonl")


def record_single_advisor_backlog(path):
    """单顾问豁免通过时留痕：缺失的批判评审记为待补项。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "file": path,
        "mode": "single-advisor",
        "advisor": "ChatGPT",
        "missing": "Claude 批判评审",
        "status": "待补项",
        "result": "PASS",
        "note": "批判通道故障，通道恢复后补做；补做发现实质性问题时允许变更并重新评审。",
    }
    with open(SINGLE_ADVISOR_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return SINGLE_ADVISOR_LEDGER


def check(path, routine=False, single_advisor=False):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return ["文件不可读：%s" % e]
    if routine and ROUTINE_MARKER in text:
        return []
    errors = []
    if not any(m in text for m in CHATGPT_MARKERS):
        errors.append("缺 ChatGPT 发散记录")
    if not single_advisor:
        if not any(m in text for m in CLAUDE_MARKERS):
            errors.append("缺 Claude 批判记录")
    # 单顾问模式下确认标记放宽到单顾问确认（默认双顾问标记集不变）：
    # “有条件确认 / 专家确认 / 确认通过”均为单顾问可出的确认形式。
    confirm_markers = CONFIRM_MARKERS
    if single_advisor:
        confirm_markers = CONFIRM_MARKERS + ("有条件确认", "专家确认", "确认通过")
    if not any(m in text for m in confirm_markers):
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
    ap.add_argument("--single-advisor", action="store_true",
                    help="单顾问豁免（2026-11-25 董事长令）：只要求 ChatGPT 确认，"
                         "缺失的 Claude 批判评审记为待补项，不阻塞")
    args = ap.parse_args()
    errors = check(args.path, args.routine, args.single_advisor)
    if errors:
        print("DUAL-ADVISOR GATE REFUSED: %s" % args.path)
        for e in errors:
            print("  - %s" % e)
        return 1
    if args.single_advisor and not args.routine:
        ledger = record_single_advisor_backlog(args.path)
        print("DUAL-ADVISOR GATE PASS (single-advisor exemption): %s" % args.path)
        print("  - 单顾问（ChatGPT）确认 + 结构通过；缺失的 Claude 批判评审已记为待补项，见 %s" % ledger)
        return 0
    print("DUAL-ADVISOR GATE PASS: %s" % args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
