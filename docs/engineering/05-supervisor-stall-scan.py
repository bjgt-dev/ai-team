#!/usr/bin/env python3
"""05-supervisor-stall-scan: central_tasks 滞留扫描（规范05配套脚本，初版待验证）.

只读 central_tasks，永不改库。分类阈值（规范05第3节）：
  READY/PENDING、CLAIMED/RUNNING（DOING）: >30min 黄，>60min 红（推大管家）
  VERIFYING: >900s 红
  RETRY_WAIT: 单次 not_before 距 now >15min 违规；累计滞留（updated_at 起）>2h 写 incident
输出: state/stall-scan-latest.json；黄只写日志不推 TG；红调 scripts/alert_push.sh。
用法: python3 05-supervisor-stall-scan.py [--selftest] [--dry-run]
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
ALERT_PUSH = os.path.join(BASE, "scripts", "alert_push.sh")
OUT = os.path.join(STATE, "stall-scan-latest.json")
LOG = os.path.join(STATE, "alerts.log")

YELLOW_S = 30 * 60
RED_S = 60 * 60
VERIFYING_RED_S = 900
RETRY_SINGLE_MAX_S = 15 * 60
RETRY_CUMUL_MAX_S = 2 * 60 * 60

# 任务书口径 -> central_tasks 实际 status 的映射（实查 2026-11-06：库里只有
# READY/CLAIMED/RETRY_WAIT/DONE/QUARANTINED 等，无 PENDING/DOING/BLOCKED 行）
DOING = {"CLAIMED", "RUNNING"}
PENDING = {"READY", "PENDING", "DISPATCHING"}


def classify(status, age_s, retry_wait_not_before_s=None):
    """返回 (level, reason)：level in OK/YELLOW/RED/INCIDENT。"""
    if status in PENDING or status in DOING:
        if age_s > RED_S:
            return ("RED", "%s滞留%.0f分钟>60" % (status, age_s / 60))
        if age_s > YELLOW_S:
            return ("YELLOW", "%s滞留%.0f分钟>30" % (status, age_s / 60))
        return ("OK", "")
    if status == "VERIFYING":
        if age_s > VERIFYING_RED_S:
            return ("RED", "VERIFYING滞留%.0fs>900" % age_s)
        return ("OK", "")
    if status == "RETRY_WAIT":
        if retry_wait_not_before_s is not None and retry_wait_not_before_s > RETRY_SINGLE_MAX_S:
            return ("RED", "RETRY_WAIT单次等待%.0f分钟>15" % (retry_wait_not_before_s / 60))
        if age_s > RETRY_CUMUL_MAX_S:
            return ("INCIDENT", "RETRY_WAIT累计滞留%.1f小时>2" % (age_s / 3600))
        return ("OK", "")
    return ("OK", "")


def selftest():
    cases = [
        ("READY", 31 * 60, None, "YELLOW"),
        ("CLAIMED", 61 * 60, None, "RED"),
        ("RUNNING", 20 * 60, None, "OK"),
        ("VERIFYING", 901, None, "RED"),
        ("VERIFYING", 600, None, "OK"),
        ("RETRY_WAIT", 60 * 60, 16 * 60, "RED"),
        ("RETRY_WAIT", 3 * 3600, 5 * 60, "INCIDENT"),
        ("DONE", 999999, None, "OK"),
    ]
    bad = 0
    for st, age, nb, want in cases:
        got, reason = classify(st, age, nb)
        ok = got == want
        bad += 0 if ok else 1
        print("%s %-11s age=%s nb=%s -> %s (%s) want=%s" % (
            "PASS" if ok else "FAIL", st, age, nb, got, reason, want))
    print("SELFTEST %s (%d/%d)" % ("OK" if bad == 0 else "FAIL", len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


def dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def main():
    if "--selftest" in sys.argv:
        return selftest()
    dry_run = "--dry-run" in sys.argv
    import psycopg
    now = time.time()
    rows = []
    try:
        with psycopg.connect(dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, status, retry_count, max_retries,"
                    " EXTRACT(EPOCH FROM (now()-updated_at)),"
                    " EXTRACT(EPOCH FROM (not_before-now()))"
                    " FROM central_tasks"
                    " WHERE status NOT IN ('DONE','QUARANTINED')"
                    " ORDER BY updated_at")
                rows = cur.fetchall()
    except Exception as e:
        print(json.dumps({"stall_scan": "DB_ERROR", "err": str(e)[:200]}, ensure_ascii=False))
        return 2
    items, reds, incidents = [], [], []
    for tid, st, rc, mr, age_s, nb_s in rows:
        age_s = float(age_s or 0)
        nb_s = float(nb_s) if nb_s is not None else None
        level, reason = classify(st, age_s, nb_s)
        item = {"task_id": str(tid), "status": st, "retry_count": rc,
                "max_retries": mr, "age_s": int(age_s),
                "not_before_in_s": int(nb_s) if nb_s is not None else None,
                "level": level, "reason": reason}
        items.append(item)
        if level == "RED":
            reds.append(item)
        if level == "INCIDENT":
            incidents.append(item)
    out = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
           "scanned": len(items),
           "red": len(reds), "incident": len(incidents),
           "levels": {lv: sum(1 for i in items if i["level"] == lv)
                      for lv in ("OK", "YELLOW", "RED", "INCIDENT")},
           "items": [i for i in items if i["level"] != "OK"]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s stall-scan scanned=%d yellow=%d red=%d incident=%d\n" % (
            out["ts"], out["scanned"], out["levels"]["YELLOW"],
            out["levels"]["RED"], out["levels"]["INCIDENT"]))
    if dry_run:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    for it in reds:
        subprocess.run([ALERT_PUSH, "STALL_RED",
                        "任务滞留红警：%s %s，%s，请大管家立即处理。" % (
                            it["task_id"][:8], it["status"], it["reason"])],
                       capture_output=True, text=True, timeout=60)
    for it in incidents:
        subprocess.run([ALERT_PUSH, "STALL_INCIDENT",
                        "任务滞留 incident：%s %s，%s，已写 incident 草稿，需人工定案。" % (
                            it["task_id"][:8], it["status"], it["reason"])],
                       capture_output=True, text=True, timeout=60)
        idir = os.path.join(STATE, "incidents")
        os.makedirs(idir, exist_ok=True)
        with open(os.path.join(idir, "stall-%s.json" % it["task_id"]), "w",
                  encoding="utf-8") as f:
            json.dump({"tool": "05-supervisor-stall-scan", "kind": "RETRY_WAIT_OVER_2H",
                       "task_id": it["task_id"], "status": it["status"],
                       "retry_count": it["retry_count"], "max_retries": it["max_retries"],
                       "age_s": it["age_s"], "ts": out["ts"],
                       "action": "NEEDS_DECISION: BLOCKED/TERMINAL_FAILED 尚未在代码中落地，"
                                 "本稿只登记不改库，待人工定案"},
                      f, ensure_ascii=False, indent=2)
    print(json.dumps({"stall_scan": "OK", "scanned": out["scanned"],
                      "red": len(reds), "incident": len(incidents)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
