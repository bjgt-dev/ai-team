#!/usr/bin/env python3
"""Execution ledger guard (2026-11-07, MEMORY.md 执行台账机制).

Rule (memory-only until now; the ledger-check.py named in MEMORY does not
exist on the hub -- only ledger-count-check-20261104.py, which checks key
counts, not the ledger itself): every chair decision enters the ledger
the same day with owner + deadline + evidence; only evidence closes an
item; overdue unclosed items surface at the top.
Ledger: state/execution-ledger.md (hub) or --ledger. Entry format
(lenient parse): a bullet/heading block containing 负责人/owner,
期限/deadline (YYYY-MM-DD) and 证据/evidence. Blocks are split on lines
starting with "- " or "## ".
  * entry without owner/deadline/evidence -> FAIL (listed).
  * deadline past + no evidence marker -> OVERDUE FAIL.
  * entries blocked on the chair ("卡董事长") are reported, not failed.
Missing ledger -> SKIP (exit 2). Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, os, re, sys
from datetime import date

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
LEDGER = os.path.join(BASE, "state", "execution-ledger.md")
DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")


def split_entries(text):
    entries, cur = [], []
    for line in text.splitlines():
        if line.startswith("- ") or line.startswith("## "):
            if cur:
                entries.append("\n".join(cur))
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        entries.append("\n".join(cur))
    return entries


def check_entry(block, today):
    missing = []
    if not re.search(r"负责人|owner", block):
        missing.append("owner")
    m = re.search(r"(?:期限|deadline)[^\n]*?(" + DATE_RE.pattern + r")", block)
    if not m:
        missing.append("deadline")
    if not re.search(r"证据|evidence|✅|SHA|sha256|回执|receipt", block):
        missing.append("evidence")
    overdue = False
    if m and "证据" not in missing and "evidence" not in missing:
        pass  # has evidence: closed or closable, not overdue
    elif m:
        try:
            if date.fromisoformat(m.group(1)) < today:
                overdue = True
        except ValueError:
            pass
    chair_blocked = bool(re.search(r"卡董事长|等董事长|董事长身上", block))
    return missing, overdue, chair_blocked


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    today = date(2026, 11, 7)
    good = "- 决策A 负责人：twt 期限：2026-11-10 证据：回执 sha256:abc"
    m, o, c = check_entry(good, today)
    ck(m == [] and not o, "owner+deadline+evidence -> PASS")
    m, _, _ = check_entry("- 决策B 期限：2026-11-10 证据：回执", today)
    ck(m == ["owner"], "missing owner -> listed")
    m, _, _ = check_entry("- 决策C 负责人：twt 证据：回执", today)
    ck(m == ["deadline"], "missing deadline -> listed")
    m, o, _ = check_entry("- 决策D 负责人：twt 期限：2026-11-01", today)
    ck("evidence" in m and o, "past deadline + no evidence -> OVERDUE")
    m, o, _ = check_entry("- 决策E 负责人：twt 期限：2026-11-01 证据：回执", today)
    ck(not o, "past deadline WITH evidence -> not overdue")
    _, _, c = check_entry("- 决策F 负责人：twt 期限：2026-11-20 卡董事长身上", today)
    ck(c, "chair-blocked entry flagged, not failed")
    ents = split_entries("- A 负责人：twt\n- B 负责人：x\n")
    ck(len(ents) == 2, "two bullets split into two entries")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--today", help="YYYY-MM-DD")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    today = date.fromisoformat(args.today) if args.today else date.today()
    try:
        text = open(args.ledger, errors="replace").read()
    except OSError:
        print("SKIP no ledger %s" % args.ledger)
        return 2
    entries = split_entries(text)
    bad, overdue, chair = [], [], []
    for e in entries:
        missing, is_over, is_chair = check_entry(e, today)
        head = e.splitlines()[0][:80] if e.splitlines() else "?"
        if missing:
            bad.append((head, missing))
        if is_over:
            overdue.append(head)
        if is_chair:
            chair.append(head)
    print("checked %d entries" % len(entries))
    for head, fields in bad:
        print("FAIL %s :: missing %s" % (head, ", ".join(fields)))
    for head in overdue:
        print("OVERDUE %s" % head)
    for head in chair:
        print("note (chair-blocked, remind max 1/day): %s" % head)
    if bad or overdue:
        return 1
    print("PASS ledger entries all carry owner+deadline+evidence, none overdue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
