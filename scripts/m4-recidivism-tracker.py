#!/usr/bin/env python3
"""M4 防复犯追踪器 (2026-11-07 M3/M4/M5 落地扫描补齐, 董事长令 2026-11-06 + 令19).

缺口: 以下六条只存在于 MECHANISM-20261106.md / 令19 文档里, 无任何代码:
  1. 每次犯错当天回写 MEMORY.md 原条目 + MECHANISM-20261106.md —— 无代码检查
  2. 连续 3 次同类任务零再犯才算改 —— 无代码追踪
  3. 同指纹第 2 次工具收紧、第 3 次报董事长 —— 无代码
  4. 认错不算数, 同回合须给 mechanism_change_ref 否则登记"未设防" —— 无代码
  5. 晨报复犯/未设防/汇报违规三指标目标 0 —— 无代码计算
  6. 错因归 9 类的写入 —— 见 m4-failure-classify.py, 本脚本消费其输出

台账: state/m4-self-improve-20261106/recidivism-ledger.jsonl (只追加).
升级文件: state/outbox/m4-escalation-<指纹>-<日期>.md (第 3 次才写, 供晨报/推送).

用法:
  record --fingerprint FP --class F-ROUTING [--mechanism-ref REF] [--note ...]
  clean  --task-type T [--fingerprint FP]     # 一次同类任务零再犯
  morning-metrics [--days 30]                 # 三指标, 任一 >0 则 exit 1
  check-writeback --fingerprint FP [--date YYYY-MM-DD]
  --self-test   # 临时台账跑全流程, 零生产污染
Stdlib only. 不改生产数据 (只追加自有台账 + 第 3 次才写 outbox 升级件).
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = "/home/ubuntu/ai-team"
MECH = os.path.join(BASE, "state/m4-self-improve-20261106/MECHANISM-20261106.md")
DEFAULT_LEDGER = os.path.join(BASE, "state/m4-self-improve-20261106/recidivism-ledger.jsonl")
OUTBOX = os.path.join(BASE, "state/outbox")


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _ledger_path(args):
    return getattr(args, "ledger", None) or os.environ.get("M4_LEDGER") or DEFAULT_LEDGER


def _read(ledger):
    rows = []
    if os.path.exists(ledger):
        with open(ledger, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    return rows


def _append(ledger, row):
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _count_fp(rows, fp):
    return sum(1 for r in rows if r.get("fingerprint") == fp
               and r.get("kind") in ("admission", "recurrence"))


def cmd_record(args):
    ledger = _ledger_path(args)
    rows = _read(ledger)
    prior = _count_fp(rows, args.fingerprint)
    n = prior + 1
    guarded = bool(args.mechanism_ref)
    _append(ledger, {"ts": _ts(), "kind": "recurrence" if prior else "admission",
                     "fingerprint": args.fingerprint,
                     "failure_class": args.failure_class,
                     "mechanism_change_ref": args.mechanism_ref or None,
                     "guarded": guarded,
                     "note": args.note or ""})
    if not guarded:
        print("未设防: 本次认错未给 mechanism_change_ref, 已登记为未设防")
    if n == 1:
        print("首次登记: 指纹 %s (第 1 次)" % args.fingerprint)
    elif n == 2:
        print("第 2 次同指纹: 工具收紧 TOOL_TIGHTEN (指纹 %s)" % args.fingerprint)
    else:
        outbox = os.environ.get("M4_OUTBOX") or OUTBOX
        esc = os.path.join(outbox, "m4-escalation-%s-%s.md" % (
            "".join(c for c in args.fingerprint if c.isalnum() or c in "-_")[:40], _today()))
        os.makedirs(outbox, exist_ok=True)
        with open(esc, "w", encoding="utf-8") as f:
            f.write("# M4 复犯升级: 指纹 %s 第 %d 次\n\n- 失败分类: %s\n- 设防: %s\n- 台账: %s\n- 动作: 请董事长定夺 (同指纹第 3 次按令19上报)\n"
                    % (args.fingerprint, n, args.failure_class,
                       "有 mechanism_change_ref" if guarded else "未设防",
                       ledger))
        print("第 %d 次同指纹: 上报董事长 ESCALATE -> %s" % (n, esc))
    return 0


def cmd_clean(args):
    ledger = _ledger_path(args)
    rows = _read(ledger)
    key = args.task_type
    streak = 0
    for r in reversed(rows):
        if r.get("task_type") != key:
            continue
        if r.get("kind") == "clean":
            streak += 1
        else:
            break
    streak += 1
    _append(ledger, {"ts": _ts(), "kind": "clean", "task_type": key,
                     "fingerprint": args.fingerprint or None,
                     "failure_class": None, "mechanism_change_ref": None,
                     "guarded": True, "note": ""})
    if streak >= 3:
        print("FIXED: %s 连续 %d 次零再犯, 该错已改" % (key, streak))
    else:
        print("CLEAN %d/3: %s 零再犯累计 %d 次 (满 3 次才算改)" % (streak, key, streak))
    return 0


def morning_metrics(rows, days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [r for r in rows if r.get("ts", "") >= cutoff[:10]]
    fps = {}
    for r in recent:
        if r.get("kind") in ("admission", "recurrence") and r.get("fingerprint"):
            fps[r["fingerprint"]] = fps.get(r["fingerprint"], 0) + 1
    recidivism = sum(1 for v in fps.values() if v >= 2)
    unguarded = sum(1 for r in recent
                    if r.get("kind") in ("admission", "recurrence") and not r.get("guarded"))
    report_violations = sum(1 for r in recent
                            if r.get("kind") in ("admission", "recurrence")
                            and r.get("failure_class") == "F-FORMAT")
    return recidivism, unguarded, report_violations


def cmd_morning_metrics(args):
    rows = _read(_ledger_path(args))
    r, u, v = morning_metrics(rows, args.days)
    print("晨报 M4 三指标 (近 %d 天, 目标 0/0/0): 复犯 %d / 未设防 %d / 汇报违规 %d"
          % (args.days, r, u, v))
    return 1 if (r or u or v) else 0


def cmd_check_writeback(args):
    mech_path = args.mechanism or MECH
    date = args.date or _today()
    problems = []
    try:
        text = open(mech_path, encoding="utf-8").read()
    except OSError as exc:
        print("FAIL: 读不到机制文件 %s (%s)" % (mech_path, exc))
        return 1
    if args.fingerprint not in text:
        problems.append("机制文件未登记指纹 %s" % args.fingerprint)
    if date not in text:
        problems.append("机制文件未见当日日期 %s (须当天回写)" % date)
    # MEMORY.md 不在枢纽 (本地文件): 只报口径, 不谎称已查.
    print("NOTE: MEMORY.md 为本地文件, 枢纽无副本, 须人工核对原条目已回写")
    if problems:
        for p in problems:
            print("FAIL: " + p)
        return 1
    print("PASS: 机制文件已登记指纹 %s 且含当日日期 %s" % (args.fingerprint, date))
    return 0


def self_test():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="m4track-")
    os.environ["M4_OUTBOX"] = os.path.join(tmp, "outbox")  # 自测升级件只写临时目录
    ledger = os.path.join(tmp, "ledger.jsonl")
    mech = os.path.join(tmp, "MECHANISM.md")
    open(mech, "w", encoding="utf-8").write("2026-11-07 错 FP-TEST-1 -> 改 ...\n")

    class A:
        pass

    def rec(fp, cls, ref=None):
        a = A(); a.ledger = ledger; a.fingerprint = fp; a.failure_class = cls
        a.mechanism_ref = ref; a.note = "self-test"
        return cmd_record(a)

    def clean(t):
        a = A(); a.ledger = ledger; a.task_type = t; a.fingerprint = None
        return cmd_clean(a)

    fails = []

    def chk(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    rec("FP-TEST-1", "F-ROUTING")            # 1st, unguarded
    rec("FP-TEST-1", "F-ROUTING", "scripts/x.py")  # 2nd -> tighten
    a = A(); a.ledger = ledger; a.fingerprint = "FP-TEST-1"
    a.failure_class = "F-ROUTING"; a.mechanism_ref = "scripts/x.py"; a.note = ""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_record(a)  # 3rd -> escalate (M4_OUTBOX 已指到临时目录)
    chk("ESCALATE" in buf.getvalue(), "第3次同指纹触发上报董事长")
    rows = _read(ledger)
    chk(len(rows) == 3, "台账 3 条")
    r, u, v = morning_metrics(rows, 30)
    chk((r, u, v) == (1, 1, 0), "三指标 复犯1/未设防1/汇报违规0 (got %d/%d/%d)" % (r, u, v))
    clean("task-type-A"); clean("task-type-A")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        clean("task-type-A")
    chk("FIXED" in buf.getvalue(), "连续3次 clean 判 FIXED")
    a = A(); a.mechanism = mech; a.fingerprint = "FP-TEST-1"; a.date = "2026-11-07"
    chk(cmd_check_writeback(a) == 0, "当天回写检查通过 (指纹+日期均在机制文件)")
    a.fingerprint = "FP-NOPE"
    chk(cmd_check_writeback(a) == 1, "未登记指纹回写检查 FAIL (反例)")
    print("SELF-TEST: %d failures" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 recidivism tracker (令19)")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("record"); p.add_argument("--fingerprint", required=True)
    p.add_argument("--class", dest="failure_class", required=True)
    p.add_argument("--mechanism-ref", default=None); p.add_argument("--note", default="")
    p = sub.add_parser("clean"); p.add_argument("--task-type", required=True)
    p.add_argument("--fingerprint", default=None)
    p = sub.add_parser("morning-metrics"); p.add_argument("--days", type=int, default=30)
    p = sub.add_parser("check-writeback"); p.add_argument("--fingerprint", required=True)
    p.add_argument("--date", default=None); p.add_argument("--mechanism", default=None)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.cmd == "record":
        return cmd_record(a)
    if a.cmd == "clean":
        return cmd_clean(a)
    if a.cmd == "morning-metrics":
        return cmd_morning_metrics(a)
    if a.cmd == "check-writeback":
        return cmd_check_writeback(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
