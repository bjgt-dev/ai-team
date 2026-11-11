#!/usr/bin/env python3
"""plan-package-gate.py -- 方案包门禁（2026-11-10 董事长令，硬规）。

规则：调研完方案，带着背景、需求、业内最佳实践，走专家咨询 SOP，
拿到双顾问确认后才能推给董事长。缺一件即 FAIL，exit 1。

用法：plan-package-gate.py <方案包目录>
方案包目录须含：
  background.md        背景
  requirements.md      需求
  best-practices.md    业内最佳实践（或 industry-survey.md，任一即可）
  design.md            方案
  expert-review.md     专家评审记录
expert-review.md 须含：
  至少两家专家名（ChatGPT / Claude / DeepSeek / Gemini，任选两家）
  确认标记（双顾问确认 / 有条件确认 / 已过外脑团评审 / 专家确认）
  处理标记（采纳 / 不采纳 / 修订 —— 证明意见被消化过，不只贴了原文）
"""
import os
import sys

EXPERTS = ["chatgpt", "claude", "deepseek", "gemini"]
CONFIRM_MARKERS = ["双顾问确认", "有条件确认", "已过外脑团评审", "专家确认"]
DISPOSITION_MARKERS = ["采纳", "不采纳", "修订"]


def check_file(path, label, problems):
    if not os.path.isfile(path):
        problems.append("缺文件：%s（%s）" % (label, os.path.basename(path)))
        return None
    if os.path.getsize(path) == 0:
        problems.append("空文件：%s（%s）" % (label, os.path.basename(path)))
        return None
    return path


def main():
    if len(sys.argv) != 2:
        print("用法：plan-package-gate.py <方案包目录>")
        return 2
    pkg = sys.argv[1]
    problems = []
    if not os.path.isdir(pkg):
        print("FAIL：方案包目录不存在：%s" % pkg)
        return 1

    check_file(os.path.join(pkg, "background.md"), "背景", problems)
    check_file(os.path.join(pkg, "requirements.md"), "需求", problems)
    bp_ok = False
    for name in ("best-practices.md", "industry-survey.md"):
        p = os.path.join(pkg, name)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            bp_ok = True
            break
    if not bp_ok:
        problems.append("缺文件：业内最佳实践（best-practices.md 或 industry-survey.md）")
    check_file(os.path.join(pkg, "design.md"), "方案", problems)
    review = check_file(os.path.join(pkg, "expert-review.md"), "专家评审记录", problems)

    if review:
        text = open(review, encoding="utf-8", errors="ignore").read()
        lowered = text.lower()
        found = [e for e in EXPERTS if e in lowered]
        if len(found) < 2:
            problems.append("专家评审记录只提到 %d 家专家（%s），须至少两家" % (len(found), found))
        if not any(m in text for m in CONFIRM_MARKERS):
            problems.append("专家评审记录缺确认标记（须含：双顾问确认 / 有条件确认 / 已过外脑团评审 / 专家确认）")
        if not any(m in text for m in DISPOSITION_MARKERS):
            problems.append("专家评审记录缺处理标记（须含：采纳 / 不采纳 / 修订，证明意见被消化过）")

    if problems:
        print("FAIL：方案包未过门禁，不许推给董事长确认：")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("PASS：方案包五件齐、专家评审已确认，可以推给董事长。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
