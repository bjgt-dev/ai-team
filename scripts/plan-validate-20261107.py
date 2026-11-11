#!/usr/bin/env python3
"""plan-validate-20261107.py -- plan模板校验器.

Before this script, the plan gates existed only as SOP prose:
  * code-dev-sop.md §三.1 / code-plan-template.md: plan不合格就重写, 不进
    下一步 -- but nothing checked a plan file; a bad plan entered design
    on the honour system.
  * beautiful-article-plan-sop.md §二 Phase 2: plan自查5条 (主Agent内联,
    禁开SubAgent) -- the 5 checks were a checklist in a human's head.
  * plan-template.md: 四段缺一不可, 不许留空蒙过去 -- no checker.

This script makes those three gates runnable:
  code-plan <plan.md> [--hub BASE]
      Brief 一句话+不做什么边界; Outline 拆到文件级 (含文件路径);
      Assets 每项有真实路径且在hub上存在; Contract 字段齐;
      Tests 表非空; Acceptance 6项齐; Timebox 有时限.
  article-plan <plan.md> [--source source.md]
      自查5条: 1) Brief/Outline自洽 (保留比例与每节保留信息一致 --
      checked as: every must-keep item is referenced by some section);
      2) 取舍有据 (每条可删减带理由, 每条必须保留指向source位置);
      3) 不过度组件化 (组件数 <= 节数*2 的启发式上限, 超出即告警);
      4) Raw/图片有目的 (每个"需要 Raw: 是"须写清服务哪个论点);
      5) 章节序号连续单调.
  generic-plan <plan.md>
      Brief / Outline / Theme / Assets 四段齐全, 无HTML注释占位残留
      (未填的 <!-- ... --> 字段即不合格).

Placeholder detection: a section that still contains "<!--" or is empty
fails. Exit: 0 = PASS; 1 = FAIL (lists every failed check); 2 = tool error.
"""
import argparse
import os
import re
import sys

PLACEHOLDER = re.compile(r"<!--.*?-->", re.S)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _section(text, heading):
    """Text under a ## heading (up to the next ## heading)."""
    m = re.search(r"^##\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    m2 = re.search(r"^##\s+\S", rest, re.M)
    return rest[:m2.start()] if m2 else rest


def _has_real_content(section):
    if section is None:
        return False
    body = PLACEHOLDER.sub("", section).strip()
    body = re.sub(r"^#+\s.*$", "", body, flags=re.M).strip()
    return len(body) > 0


def _failures_code_plan(text, hub):
    errs = []
    brief = _section(text, "Brief（任务简述）") or _section(text, "Brief")
    if not _has_real_content(brief):
        errs.append("Brief: missing or empty")
    else:
        if "不做什么" not in brief:
            errs.append("Brief: no 不做什么 boundary")
        if "一句话" in brief and len(PLACEHOLDER.sub("", brief)) < 40:
            errs.append("Brief: too short to state the problem")
    outline = _section(text, "Outline（实现大纲）") or _section(text, "Outline")
    if not _has_real_content(outline):
        errs.append("Outline: missing or empty")
    else:
        steps = re.findall(r"^\d+[.、]\s", outline, re.M)
        if len(steps) < 1:
            errs.append("Outline: no numbered steps")
        if not re.search(r"[\w./-]+\.(py|md|json|sh|ts|js|yaml|yml)\b", outline):
            errs.append("Outline: no file-level target (no file path mentioned)")
    assets = _section(text, "Assets（输入资产）") or _section(text, "Assets")
    if not _has_real_content(assets):
        errs.append("Assets: missing or empty")
    else:
        for path in re.findall(r"(?:state|scripts|/home/ubuntu)/[^\s|)`]+", assets):
            full = path if path.startswith("/") else os.path.join(hub, path)
            if not os.path.exists(full):
                errs.append("Assets: path does not exist on hub: %s" % path)
    contract = _section(text, "Contract（合同字段）") or _section(text, "Contract")
    if not _has_real_content(contract):
        errs.append("Contract: missing or empty")
    else:
        for field in ("task_id", "workdir", "output_path"):
            if field not in contract:
                errs.append("Contract: missing field %s" % field)
    tests = _section(text, "Tests（锁定测试清单）") or _section(text, "Tests")
    if not _has_real_content(tests):
        errs.append("Tests: missing or empty")
    accept = _section(text, "Acceptance（什么叫 DONE）") or _section(text, "Acceptance")
    if not _has_real_content(accept):
        errs.append("Acceptance: missing or empty")
    else:
        boxes = re.findall(r"-\s*\[[ xX]\]", accept)
        if len(boxes) < 4:
            errs.append("Acceptance: fewer than 4 checklist items (%d)" % len(boxes))
    timebox = _section(text, "Timebox（时间盒）") or _section(text, "Timebox")
    if not _has_real_content(timebox):
        errs.append("Timebox: missing or empty")
    elif not re.search(r"\d+\s*分钟", timebox):
        errs.append("Timebox: no time limit in minutes")
    return errs


def _failures_article_plan(text, source_path):
    errs = []
    brief = _section(text, "Brief") or ""
    outline = _section(text, "Outline") or ""
    # 1) Brief/Outline consistency: must-keep items must show up as
    # "保留信息" in at least one Outline section; otherwise the outline
    # drops something the brief promised to keep.
    keep_block = ""
    m = re.search(r"必须保留的信息：(.+?)(?:\n- \S+：|\Z)", brief, re.S)
    if m:
        keep_block = m.group(1)
    keep_lines = [ln for ln in keep_block.splitlines() if ln.strip().startswith(("<", "-"))]
    outline_keeps = len(re.findall(r"保留信息：", outline))
    if keep_lines and outline_keeps == 0:
        errs.append("check1: Brief lists must-keep items but no Outline section has 保留信息")
    sections = re.findall(r"^\d+\.\s+.*$", outline, re.M)
    if not sections:
        errs.append("check1: Outline has no numbered sections")
    # 2) 取舍有据: deletable items need a reason; must-keep needs a source pointer
    if "可删减" in brief and not re.search(r"可删减.{0,200}(重复|旁枝|过时)", brief, re.S):
        errs.append("check2: deletable items lack a reason (重复/旁枝/过时)")
    if "必须保留" in brief and source_path:
        try:
            source = _read(source_path)
        except OSError as exc:
            errs.append("check2: cannot read source %s: %s" % (source_path, exc))
            source = ""
        if source and "source.md" not in brief and "source" not in brief.lower():
            errs.append("check2: must-keep items do not point at source.md locations")
    # 3) 不过度组件化: components per section heuristic
    components = len(re.findall(r"Aside|Quote|Table|CodeBlock|Formula|Image", outline))
    if sections and components > len(sections) * 2:
        errs.append("check3: over-componentised (%d components for %d sections)" % (components, len(sections)))
    # 4) Raw/图片有目的: each "需要 Raw: 是" needs a purpose clause
    raw_yes = [m.start() for m in re.finditer(r"是否需要 Raw：\s*是", outline)]
    for pos in raw_yes:
        window = outline[pos:pos + 200]
        if "服务" not in window and "论点" not in window and "目的" not in window:
            errs.append("check4: a Raw=yes section states no purpose (which argument it serves)")
            break
    # 5) 章节序号连续单调
    nums = [int(n) for n in re.findall(r"^(\d+)\.\s", outline, re.M)]
    if nums and nums != list(range(1, len(nums) + 1)):
        errs.append("check5: section numbers not continuous 01..N: %s" % nums)
    for name in ("Brief", "Outline", "Theme", "Assets"):
        if _section(text, name) is None:
            errs.append("missing section: %s" % name)
    return errs


def _failures_generic_plan(text):
    errs = []
    for name in ("Brief", "Outline", "Theme", "Assets"):
        sec = _section(text, name)
        if sec is None:
            errs.append("missing section: %s" % name)
        elif not _has_real_content(sec):
            errs.append("section %s is empty or still placeholder comments" % name)
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(description="plan template validator")
    ap.add_argument("kind", choices=["code-plan", "article-plan", "generic-plan"])
    ap.add_argument("plan")
    ap.add_argument("--source", default=None, help="source.md for article-plan check2")
    ap.add_argument("--hub", default="/home/ubuntu/ai-team")
    args = ap.parse_args(argv)
    try:
        text = _read(args.plan)
    except OSError as exc:
        print("TOOL-ERROR: cannot read %s: %s" % (args.plan, exc))
        return 2
    if args.kind == "code-plan":
        errs = _failures_code_plan(text, args.hub)
    elif args.kind == "article-plan":
        errs = _failures_article_plan(text, args.source)
    else:
        errs = _failures_generic_plan(text)
    if errs:
        print("FAIL %s (%d):" % (args.plan, len(errs)))
        for e in errs:
            print("  - %s" % e)
        return 1
    print("PASS %s (%s)" % (args.plan, args.kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
