#!/usr/bin/env python3
"""Alert-route recorder (DRAFT 2026-11-04, NOT wired into production).

Writes ONE incident to BOTH hub places, atomically in intent:
  state/incidents/<id>.json  (machine-readable source of truth)
  shared/twt2026-inbox/<id>.md  (twt inbox, visible on twt online)
If either write fails, the caller must treat the alert as NOT delivered.
TG is backup-only and is NOT sent by this script.

Forbidden: secrets. Any value containing '-----BEGIN' or 'ocid1.' is
rejected (exit 2) so keys/OCIDs are never copied into an alert file.
Usage:
  python3 scripts/alert-route-record-20261104.py --id INC-20261104-001 \
    --type incident --level P1 --title "..." --default-action "..." \
    [--needs-chairman | --no-chairman-approval] [--source ...]
"""
import argparse, json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TYPES = ("incident", "l3", "hard-constraint", "rollback")
LEVELS = ("P0", "P1", "P2", "info")
FORBIDDEN = ("-----BEGIN", "ocid1.")

def base_dir():
    return Path(__file__).resolve().parent.parent

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--type", required=True, choices=TYPES)
    ap.add_argument("--level", required=True, choices=LEVELS)
    ap.add_argument("--title", required=True)
    ap.add_argument("--default-action", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--needs-chairman", action="store_true")
    g.add_argument("--no-chairman-approval", action="store_true")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--base", default=None)
    args = ap.parse_args()
    for v in (args.id, args.title, args.default_action, args.source):
        for bad in FORBIDDEN:
            if bad in v:
                print(f"REJECT: value contains forbidden secret-like pattern {bad!r}", file=sys.stderr)
                return 2
    if "/" in args.id or ".." in args.id:
        print("REJECT: id must be a plain filename", file=sys.stderr)
        return 2
    base = Path(args.base) if args.base else base_dir()
    inc_dir = base / "state" / "incidents"
    inbox_dir = base / "shared" / "twt2026-inbox"
    inc_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    jpath = inc_dir / f"{args.id}.json"
    mpath = inbox_dir / f"{args.id}.md"
    if jpath.exists() or mpath.exists():
        print(f"REJECT: {args.id} already exists; use a new id", file=sys.stderr)
        return 2
    now_local = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)
    rec = {
        "id": args.id,
        "created_at": now_local.isoformat(timespec="seconds"),
        "created_at_utc": now_utc.isoformat(timespec="seconds"),
        "type": args.type,
        "level": args.level,
        "title": args.title,
        "default_action": args.default_action,
        "needs_chairman_approval": bool(args.needs_chairman),
        "status": "pending",
        "tg_role": "backup-only",
        "source": args.source,
    }
    need = "是" if rec["needs_chairman_approval"] else "否"
    md = (
        f"# {args.id} — {args.title}\n\n"
        f"- 时间：{rec['created_at']}\n- 类型：{args.type}\n- 级别：{args.level}\n"
        f"- 默认动作：{args.default_action}\n- 需董事长拍板：{need}\n- 状态：pending\n"
        f"- TG 角色：仅备用，不作唯一报警路\n\n"
        f"twt 上线处理：按默认动作处理；需拍板项只汇总呈报、不擅自执行；"
        f"处理完用汇总脚本 --mark-closed 标 closed。\n"
    )
    jpath.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        mpath.write_text(md, encoding="utf-8")
    except Exception:
        jpath.unlink(missing_ok=True)
        raise
    print(f"RECORDED {args.id}")
    print(f"  incident: {jpath}")
    print(f"  inbox:    {mpath}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
