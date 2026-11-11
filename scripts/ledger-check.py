#!/usr/bin/env python3
"""执行台账机制校验（枢纽版，AGENTS.md 执行台账机制，2026-11-06 董事长令）。
本地版的枢纽移植：同一校验逻辑，默认台账路径改为枢纽 state/execution-ledger.md。
文档不是机制，超期与非法关单必须由程序算出来。

用法：python3 ledger-check.py [--ledger PATH] [--quiet]
退出码：0=干净；1=有警告（4 小时内到期或超期<=2h）；2=违规（超期>2h、DONE 无证据、非法状态）。
北京时间（Asia/Shanghai）为准。
"""
import argparse
import re
import sys
from datetime import datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    CST = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    CST = timezone(timedelta(hours=8))

VALID_STATUS = {"OPEN", "BLOCKED-董事长", "BLOCKED-我方", "DONE", "DROP"}


def parse_deadline(raw: str):
    raw = raw.strip()
    if raw in ("等董事长", "持续", "—", "") or raw.startswith("随"):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s*夜", raw)
    if m:
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, 23, 59, tzinfo=CST)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?", raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 23
        mm = int(m.group(5)) if m.group(5) else 59
        return datetime(y, mo, d, hh, mm, tzinfo=CST)
    return None


def parse_ledger(path: str):
    rows = []
    in_table = False
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("| 决策/事项 |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 5:
                continue
            rows.append({
                "item": cells[0],
                "status": cells[1],
                "note": cells[2],
                "deadline_raw": cells[3],
                "evidence": cells[4],
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="/home/ubuntu/ai-team/state/execution-ledger.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    now = datetime.now(CST)
    rows = parse_ledger(args.ledger)
    problems = []   # (level, text) level: WARN/VIOLATION
    done_n = 0
    overdue_hours = []

    for r in rows:
        st = r["status"]
        if st not in VALID_STATUS:
            problems.append(("VIOLATION", f"非法状态 [{st}]：{r['item']}"))
            continue
        if st == "DONE":
            done_n += 1
            if r["evidence"] in ("—", "", "无"):
                problems.append(("VIOLATION", f"DONE 无证据：{r['item']}"))
            continue
        if st == "DROP":
            continue
        dl = parse_deadline(r["deadline_raw"])
        if dl is None:
            continue
        delta_h = (now - dl).total_seconds() / 3600
        if delta_h > 0:
            overdue_hours.append(delta_h)
            if delta_h > 6:
                problems.append(("VIOLATION", f"超期 {delta_h:.1f}h（>6h 应报董事长）：{r['item']}，期限 {r['deadline_raw']}"))
            elif delta_h > 2:
                problems.append(("VIOLATION", f"超期 {delta_h:.1f}h（>2h 应置顶）：{r['item']}，期限 {r['deadline_raw']}"))
            else:
                problems.append(("WARN", f"刚超期 {delta_h:.1f}h：{r['item']}，期限 {r['deadline_raw']}"))
        elif delta_h > -4:
            problems.append(("WARN", f"{-delta_h:.1f}h 后到期：{r['item']}，期限 {r['deadline_raw']}"))

    total = len(rows)
    close_rate = (done_n / total * 100) if total else 0
    avg_overdue = (sum(overdue_hours) / len(overdue_hours)) if overdue_hours else 0
    open_n = sum(1 for r in rows if r["status"] == "OPEN")
    blocked_n = sum(1 for r in rows if r["status"].startswith("BLOCKED"))

    if not args.quiet:
        print(f"台账：共 {total} 项，OPEN {open_n}，BLOCKED {blocked_n}，DONE {done_n}，关单率 {close_rate:.0f}%")
        print(f"超期项 {len(overdue_hours)}，平均超期 {avg_overdue:.1f}h")
        for level, text in problems:
            print(f"[{level}] {text}")
        if not problems:
            print("无超期、无违规。")

    if any(l == "VIOLATION" for l, _ in problems):
        return 2
    if problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
