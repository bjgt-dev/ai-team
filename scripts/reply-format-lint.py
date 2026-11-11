#!/usr/bin/env python3
"""reply-format-lint.py — 对话回答输出格式 lint（AGENTS.md，2026-11-07 董事长定）。

对话回答用分级缩进的结构化格式（明显不适用的情况除外；仅限对话回答，
不用于文档等其它内容输出）。除最末一级缩进外，更外层缩进层级的文字均应
能概括下一层级的核心内容，不只给结构性标签；只读外层就能抓住核心。

可机检规则（确定性，不做语义判断）：
  E1 外层裸标签：父级行只有标签无内容——行内容去掉标记后以 ：/：结尾，
     或内容全是标签词（结论/背景/目标/方案/风险/下一步/阻塞/原因/结果 等）
     且长度 <= 6 字。
  E2 父级无子级：非最末缩进层级的行没有任何更深缩进的后续行跟随
     （孤立父级，结构断裂）。
  W1 全篇平铺：正文 >= 8 行且无任何缩进层级（只有明显不适用才豁免，
     本 lint 只报警告，不判错）。
用法：python3 reply-format-lint.py <reply.md|reply.txt> [--strict]
退出码：0=通过（警告不影响）；1=有 E 级违规。stdin 可用 "-"。
"""
import argparse
import re
import sys

LABEL_WORDS = {"结论", "背景", "目标", "方案", "风险", "回滚", "下一步", "阻塞",
               "原因", "结果", "依据", "进展", "问题", "建议", "计划", "执行",
               "核验", "证据", "状态", "详情", "说明", "概述", "要点"}
BULLET_RE = re.compile(r"^(\s*)(?:[-*•\d]+\.|[-*•])\s+(.*)$")


def _indent_of(line):
    m = BULLET_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2)
    if line.strip() == "":
        return None, None
    return len(line) - len(line.lstrip(" ")), line.strip()


def lint(text):
    errors, warnings = [], []
    items = []  # (lineno, indent, content)
    for i, line in enumerate(text.splitlines(), 1):
        ind, content = _indent_of(line)
        if ind is None or content is None or content == "":
            continue
        items.append((i, ind, content))
    if not items:
        return errors, warnings
    if all(ind == items[0][1] for _, ind, _ in items) and len(items) >= 8:
        warnings.append("W1：正文 %d 行全篇平铺、无分级缩进" % len(items))
    for idx, (lineno, ind, content) in enumerate(items):
        body = content.strip()
        # 其后第一个缩进不同的行是否更深：是则本行为父级
        has_child = False
        for j in range(idx + 1, len(items)):
            if items[j][1] > ind:
                has_child = True
                break
            if items[j][1] <= ind:
                break
        is_parent = has_child
        bare = body.endswith(("：", ":")) or (
            len(body) <= 6 and body.rstrip("：:") in LABEL_WORDS)
        if is_parent and bare:
            errors.append("E1 第 %d 行外层裸标签无内容概括：%s" % (lineno, body))
    return errors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="reply file, or - for stdin")
    ap.add_argument("--strict", action="store_true",
                    help="W1 警告也判错")
    args = ap.parse_args()
    text = sys.stdin.read() if args.path == "-" else open(
        args.path, encoding="utf-8").read()
    errors, warnings = lint(text)
    for w in warnings:
        print(w)
    for e in errors:
        print(e)
    if errors or (args.strict and warnings):
        print("REPLY-FORMAT LINT FAIL：%d 错误，%d 警告" % (len(errors), len(warnings)))
        return 1
    print("REPLY-FORMAT LINT PASS：%d 错误，%d 警告" % (len(errors), len(warnings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
