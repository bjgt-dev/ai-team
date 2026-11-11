#!/usr/bin/env python3
"""wake-check-hub.py — 枢纽侧唤醒体检（AGENTS.md 唤醒第一动作，2026-11-01 董事长强制令）。

体检五项（AGENTS.md L7-11），任一异常即 RED（exit 1），全绿才许说"系统健康"：
  1. 席位实选脑：brain-active.log 最后一行非巡航行须有实选脑名，且日志新鲜
     （60 分钟内有写入）。不硬编码模型名——旧本地脚本写死 deepseek-v4.1-flash-free，
     而 2026-11-07 实选已是 my-gateway/agnes-3.0-flash，写死即天天误报 RED。
  2. Zen 健康：网关金丝雀 timer-gateway_canary 最近 60 分钟内须有 DONE 行
     （2026-11-10 修：旧源 brain-health.md 的写者 brain_watch.sh 已于 2026-11-04
     按 order13 停用，探活改走 30 分钟网关金丝雀；再读旧文件即天天误报 RED）。
  3. 决策队列 pending 数：ceo-seat/state/decision-queue.json pending <= 5（2026-11-10 修：旧全局文件 stale，pending 恒 0 误报 GREEN）。
  4. 根盘/数据盘：/ 与 /data 使用率 < 85%。
  5. 五步链：已退役，不计分（2026-11-10 修：执行器 cron 于 2026-11-03 B4B3
     退役，日志停在 2026-11-02；活队列是 PG central_tasks，见决策队列项）。

用法：python3 scripts/wake-check-hub.py [--base /home/ubuntu/ai-team]
退出码：0=GREEN 全绿；1=RED 任一异常。只读，不改任何生产数据。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
BRAIN_LOG = "state/brain-active.log"
DSN_FILE = "state/.cq-dsn"
QUEUE_FILE = "ceo-seat/state/decision-queue.json"
BRAIN_FRESH_MIN = 60
CANARY_MAX_MIN = 60
QUEUE_MAX_PENDING = 5
DISK_MAX_PCT = 85


def _age_hours(path):
    try:
        return (datetime.now(CST) - datetime.fromtimestamp(
            os.path.getmtime(path), tz=CST)).total_seconds() / 3600
    except OSError:
        return None


def check_brain(base):
    p = os.path.join(base, BRAIN_LOG)
    age = _age_hours(p)
    if age is None:
        return False, "brain-active.log 缺失"
    if age > BRAIN_FRESH_MIN / 60:
        return False, "实选脑日志超 %.0f 分钟未更新（%.1fh 前）" % (BRAIN_FRESH_MIN, age)
    try:
        lines = open(p, encoding="utf-8", errors="ignore").read().splitlines()
    except OSError as e:
        return False, "实选脑日志不可读：%s" % e
    real = [l for l in lines if "patrol brain=" not in l and "brain=" in l]
    if not real:
        return False, "实选脑日志无实选行（只剩巡航行）"
    last = real[-1].strip()
    if "brain=" not in last or last.split("brain=", 1)[1].strip() == "":
        return False, "实选脑行无脑名：%s" % last
    return True, "实选脑：%s" % last


def _read_dsn(base):
    try:
        for line in open(os.path.join(base, DSN_FILE), encoding="utf-8"):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip("\"'")
    except OSError:
        return None
    return None


def check_zen(base):
    # 活信号是 30 分钟一次的网关金丝雀（order13 接替 11-04 停用的 brain probe）。
    dsn = _read_dsn(base)
    if not dsn:
        return False, "金丝雀 DSN 不可读（state/.cq-dsn 缺失）"
    sql = ("SELECT extract(epoch FROM (now() - max(updated_at)))/60 "
           "FROM central_tasks WHERE title='timer-gateway_canary' AND status='DONE'")
    try:
        r = subprocess.run(["psql", dsn, "-t", "-A", "-c", sql],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001 — 体检本身不许崩
        return False, "金丝雀查询失败：%s" % e
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        return False, "金丝雀查询无结果：%s" % (r.stderr or "").strip()[:120]
    try:
        mins = float(out)
    except ValueError:
        return False, "金丝雀查询结果异常：%s" % out[:120]
    if mins > CANARY_MAX_MIN:
        return False, "网关金丝雀超 %d 分钟未 DONE（%.0f 分钟前）" % (CANARY_MAX_MIN, mins)
    return True, "网关金丝雀 %.0f 分钟前 DONE（30 分钟一次）" % mins


def check_queue(base):
    p = os.path.join(base, QUEUE_FILE)
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, "决策队列不可读：%s" % e
    n = len(d.get("pending", [])) if isinstance(d, dict) else -1
    if n < 0:
        return False, "决策队列结构异常（无 pending 列表）"
    if n > QUEUE_MAX_PENDING:
        return False, "决策队列 pending=%d（>%d）" % (n, QUEUE_MAX_PENDING)
    return True, "决策队列 pending=%d" % n


def check_disk():
    bad = []
    ok = []
    for mount in ("/", "/data"):
        try:
            u = shutil.disk_usage(mount)
            pct = 100.0 * u.used / u.total
        except OSError:
            bad.append("%s 不可读" % mount)
            continue
        if pct >= DISK_MAX_PCT:
            bad.append("%s 使用率 %.0f%%（>=%d%%）" % (mount, pct, DISK_MAX_PCT))
        else:
            ok.append("%s %.0f%%" % (mount, pct))
    if bad:
        return False, "；".join(bad)
    return True, "磁盘：" + "、".join(ok)


def check_five_step(base):
    # 2026-11-03 B4B3：五步链执行器 cron 已退役，日志停在 2026-11-02。
    # 活队列是 PG central_tasks（见决策队列项 + supervisor-stall-scan）。
    # 本项只留作记录，不计入 RED/GREEN。
    return "skip", "五步链已退役 2026-11-03（B4B3），活队列见 PG central_tasks"


CHECKS = [
    ("席位实选脑", check_brain),
    ("Zen健康", check_zen),
    ("决策队列", check_queue),
    ("磁盘", lambda base: check_disk()),
    ("五步链", check_five_step),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/home/ubuntu/ai-team")
    args = ap.parse_args()
    red = False
    for name, fn in CHECKS:
        try:
            ok, detail = fn(args.base)
        except Exception as e:  # noqa: BLE001 — 体检本身不许崩
            ok, detail = False, "检查异常：%s" % e
        if ok == "skip":
            print("SKIP：%s — %s" % (name, detail))
            continue
        print("%s：%s — %s" % ("OK " if ok else "RED", name, detail))
        red = red or not ok
    print("RESULT: %s" % ("RED" if red else "GREEN"))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
