#!/usr/bin/env python3
"""M4 开场 boot_quiz + 红线卡 (2026-11-07 M3/M4/M5 落地扫描补齐, 令19).

缺口: 令19 要求开场 boot_quiz + 红线卡 <=12 条, 但枢纽无任何相关代码
(仅 butler-self-check / eight_layer_review 有另一含义的 REDLINE_KEYS,
与本令无关). 本脚本是开场自检门: 读红线卡文件, 强制 <=12 条, 逐条出题.

红线卡文件: state/m4-self-improve-20261106/redline-cards.md
格式: 每卡一行, 以 "卡N: " 开头, 须含 "问: " 自检问题. 内容只摘自已有
定案 (MECHANISM-20261106.md / SOUL 红线 / M5 定案), 不新增决策.

用法:
  python3 m4-boot-quiz.py --check [--cards PATH]   # 卡数/格式校验, >12 则 exit 1
  python3 m4-boot-quiz.py --quiz  [--cards PATH]   # 开场逐条出题
  python3 m4-boot-quiz.py --self-test              # 13 卡/缺问/12 卡正反例
Stdlib only. 只读.
"""
import argparse
import os
import re
import sys

BASE = "/home/ubuntu/ai-team"
DEFAULT_CARDS = os.path.join(BASE, "state/m4-self-improve-20261106/redline-cards.md")
CARD_RE = re.compile(r"^卡(\d+)[：:]\s*(.+)$")


def load_cards(path):
    cards = []
    problems = []
    if not os.path.exists(path):
        return cards, ["红线卡文件缺失: %s" % path]
    for ln, line in enumerate(open(path, encoding="utf-8"), 1):
        m = CARD_RE.match(line.strip())
        if not m:
            continue
        body = m.group(2)
        if "问:" not in body and "问：" not in body:
            problems.append("第 %d 行卡%s 缺自检问题 (须含 '问: ')" % (ln, m.group(1)))
        cards.append((int(m.group(1)), body))
    if cards:
        nums = [n for n, _ in cards]
        if nums != list(range(1, len(nums) + 1)):
            problems.append("卡号不连续: %s (须从卡1连续编号)" % nums)
    if not cards:
        problems.append("红线卡文件无任何卡片行 (须以 '卡N: ' 开头)")
    if len(cards) > 12:
        problems.append("红线卡 %d 条, 超过上限 12 条" % len(cards))
    return cards, problems


def cmd_check(cards_path):
    cards, problems = load_cards(cards_path)
    print("红线卡: %d 条 (上限 12)" % len(cards))
    if problems:
        for p in problems:
            print("FAIL: " + p)
        return 1
    print("PASS: 红线卡 %d 条, 格式合规" % len(cards))
    return 0


def cmd_quiz(cards_path):
    cards, problems = load_cards(cards_path)
    if problems:
        for p in problems:
            print("FAIL: " + p)
        return 1
    print("开场 boot_quiz: %d 条红线, 逐条自问 (答不出即停手先查机制文件):" % len(cards))
    for n, body in cards:
        print("卡%d: %s" % (n, body))
    return 0


def self_test():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="m4quiz-")
    fails = []

    def chk(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    good = "".join("卡%d: 红线%d 问: 自检%d?\n" % (i, i, i) for i in range(1, 13))
    p1 = os.path.join(tmp, "good.md"); open(p1, "w", encoding="utf-8").write(good)
    chk(cmd_check(p1) == 0, "12 卡通过 (上限内正例)")
    chk(cmd_quiz(p1) == 0, "12 卡开场 quiz 通过")
    bad13 = good + "卡13: 多一条 问: ?\n"
    p2 = os.path.join(tmp, "bad13.md"); open(p2, "w", encoding="utf-8").write(bad13)
    chk(cmd_check(p2) == 1, "13 卡被拒 (超上限反例)")
    badq = "卡1: 无自检问题的红线\n"
    p3 = os.path.join(tmp, "badq.md"); open(p3, "w", encoding="utf-8").write(badq)
    chk(cmd_check(p3) == 1, "缺 '问: ' 的卡被拒 (反例)")
    chk(cmd_check(os.path.join(tmp, "nope.md")) == 1, "缺失卡文件被拒 (反例)")
    print("SELF-TEST: %d failures" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 boot quiz + red-line cards (<=12)")
    ap.add_argument("--cards", default=DEFAULT_CARDS)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiz", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.quiz:
        return cmd_quiz(a.cards)
    return cmd_check(a.cards)


if __name__ == "__main__":
    sys.exit(main())
