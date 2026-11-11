#!/usr/bin/env python3
"""Main-chat pending digest (DRAFT 2026-11-04, NOT wired into production).
Run when twt opens the main chat: lists every pending item once, so the
chairman sees it without relying on any TG push. TG is backup-only.
Sources: state/incidents/*.json with status pending, plus
state/decision-queue.json pending[] (legacy L3 queue, if present).
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ORDER = {"P0": 0, "P1": 1, "P2": 2, "info": 3}

def base_dir():
    return Path(__file__).resolve().parent.parent

def load_pending(base):
    inc_dir = base / "state" / "incidents"
    if not inc_dir.is_dir():
        return None
    items = []
    for p in sorted(inc_dir.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print("WARN: unreadable %s: %s" % (p.name, e), file=sys.stderr)
            continue
        if r.get("status") == "pending":
            items.append(r)
    dq = base / "state" / "decision-queue.json"
    if dq.is_file():
        try:
            data = json.loads(dq.read_text(encoding="utf-8"))
            for d in data.get("pending", []):
                items.append({
                    "id": d.get("id", "?"), "created_at": d.get("created_at", "?"),
                    "type": d.get("type", "l3"), "level": "P1",
                    "title": d.get("summary", d.get("title", "?")),
                    "default_action": "汇总呈报，等董事长拍板",
                    "needs_chairman_approval": True,
                })
        except Exception as e:
            print("WARN: unreadable decision-queue.json: %s" % e, file=sys.stderr)
    items.sort(key=lambda r: (ORDER.get(r.get("level"), 9), str(r.get("created_at", ""))))
    return items

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--mark-closed", default=None)
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    base = Path(args.base) if args.base else base_dir()
    if args.mark_closed:
        jpath = base / "state" / "incidents" / (args.mark_closed + ".json")
        mpath = base / "shared" / "twt2026-inbox" / (args.mark_closed + ".md")
        if not jpath.is_file():
            print("REJECT: no incident %s" % args.mark_closed, file=sys.stderr)
            return 2
        r = json.loads(jpath.read_text(encoding="utf-8"))
        r["status"] = "closed"
        r["closed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        r["closed_note"] = args.note
        jpath.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if mpath.is_file():
            txt = mpath.read_text(encoding="utf-8")
            txt = txt.replace("状态：pending", "状态：closed")
            mpath.write_text(txt + "\nClosed: " + r["closed_at"] + " (" + args.note + ")\n", encoding="utf-8")
        print("CLOSED %s" % args.mark_closed)
        return 0
    items = load_pending(base)
    if items is None:
        print("REJECT: state/incidents/ missing on hub", file=sys.stderr)
        return 2
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    print("主聊待批汇总 %s（不依赖 TG 推送；TG 仅备用）" % today)
    if not items:
        print("今日无待批项（TG 没看也不影响）。")
        return 0
    counts = {}
    for it in items:
        counts[it.get("level", "?")] = counts.get(it.get("level", "?"), 0) + 1
    print("共 %d 项 pending：%s" % (len(items), ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    for i, it in enumerate(items, 1):
        need = "是" if it.get("needs_chairman_approval") else "否"
        print("%d. [%s][%s] %s -- %s — 默认动作：%s — 需董事长拍板：%s" % (
            i, it.get("level"), it.get("type"), it.get("title"),
            it.get("created_at"), it.get("default_action"), need))
    print("需拍板项只汇总、不擅自执行；非拍板项 twt 按默认动作处理后标 closed。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
