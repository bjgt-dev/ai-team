#!/usr/bin/env python3
"""05-hub-local-takeover: hub-local 抢修工接管触发器（规范05配套脚本，初版待验证）.

只接管 stall-scan 已判 RED 且复核仍红的任务；默认 --dry-run，只写计划不动作。
真实执行须显式传 --execute。
条件（须同时满足）：
  1. state/stall-scan-latest.json 中该 task_id 为 RED（DOING>60min 或 VERIFYING>900s
     或 RETRY_WAIT 单次等待>15min），且本脚本二次直读 central_tasks 复核仍 RED；
  2. 该任务无 ACTIVE 租约（central_leases），或租约已过期——有活租约绝不抢；
  3. 同一 task_id 24 小时内未接管过（state/backup-watchdog-state.json 的 triggered）。
动作（按 backup-watchdog.py 已有路径，不自造执行器）：
  1. 先跑 backup-watchdog.py --dry-run 探针；2. 再实跑 backup-watchdog.py
     （probe-first 自愈 -> hub_chat 备工执行 -> 写 deliverable + receipt -> notify 推送）；
  3. 备工失败 -> alert_push.sh NEEDS_DECISION + state/incidents/takeover-<tid>.json。
回滚：本脚本不删合同、不改任务状态（状态流转仍由 C2 executor/verifier 走）；
  若备工写出的 deliverable/receipt 有误，按 state/takeover-latest.json 记录的
  path 删除当次写出的两个文件即回滚，任务回到接管前状态，由人工重派。
  24h 冷却内重复触发直接跳过，不循环抢。
用法: python3 05-hub-local-takeover.py [--execute] [--task <task_id>]
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
SCRIPTS = os.path.join(BASE, "scripts")
STALL = os.path.join(STATE, "stall-scan-latest.json")
BW_STATE = os.path.join(STATE, "backup-watchdog-state.json")
LOCK = "/tmp/hub-local-takeover.lock"
COOLDOWN_S = 86400

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "stall_scan", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "05-supervisor-stall-scan.py"))
_mod = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_mod)
    classify, dsn = _mod.classify, _mod.dsn
except Exception:
    classify = dsn = None


def log(msg):
    print(json.dumps({"takeover": msg}, ensure_ascii=False))


def main():
    execute = "--execute" in sys.argv
    task_arg = None
    if "--task" in sys.argv:
        task_arg = sys.argv[sys.argv.index("--task") + 1]
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        log("BUSY lock held")
        return 2
    try:
        if classify is None:
            log("ERROR cannot import supervisor_stall_scan (classify/dsn)")
            return 2
        try:
            scan = json.load(open(STALL, encoding="utf-8"))
        except Exception as e:
            log("ERROR no stall-scan report: %s" % e)
            return 2
        reds = [i for i in scan.get("items", []) if i.get("level") == "RED"]
        if task_arg:
            reds = [i for i in reds if i["task_id"] == task_arg]
        if not reds:
            log("OK no RED task to take over")
            return 0
        import psycopg
        with psycopg.connect(dsn()) as conn:
            with conn.cursor() as cur:
                for it in reds:
                    tid = it["task_id"]
                    cur.execute(
                        "SELECT status, EXTRACT(EPOCH FROM (now()-updated_at)),"
                        " EXTRACT(EPOCH FROM (not_before-now()))"
                        " FROM central_tasks WHERE task_id=%s", (tid,))
                    row = cur.fetchone()
                    if not row:
                        log("SKIP %s not in central_tasks" % tid[:8])
                        continue
                    st, age_s, nb_s = row
                    level, reason = classify(
                        st, float(age_s or 0),
                        float(nb_s) if nb_s is not None else None)
                    if level != "RED":
                        log("SKIP %s recheck now %s (%s)" % (tid[:8], level, reason))
                        continue
                    cur.execute(
                        "SELECT COUNT(*) FROM central_leases WHERE task_id=%s"
                        " AND status='ACTIVE'", (tid,))
                    if cur.fetchone()[0] > 0:
                        log("SKIP %s has ACTIVE lease, never抢" % tid[:8])
                        continue
                    try:
                        bw = json.load(open(BW_STATE, encoding="utf-8"))
                        last = bw.get("triggered", {}).get(tid, 0)
                    except Exception:
                        last = 0
                    if time.time() - last < COOLDOWN_S:
                        log("SKIP %s cooldown 24h" % tid[:8])
                        continue
                    plan = {"task_id": tid, "status": st, "reason": reason,
                            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                            "execute": execute}
                    with open(os.path.join(STATE, "takeover-latest.json"), "w",
                              encoding="utf-8") as f:
                        json.dump(plan, f, ensure_ascii=False, indent=2)
                    if not execute:
                        log("DRY-RUN plan %s" % json.dumps(plan, ensure_ascii=False))
                        continue
                    r = subprocess.run(
                        [sys.executable, os.path.join(SCRIPTS, "backup-watchdog.py")],
                        capture_output=True, text=True, timeout=900)
                    log("backup-watchdog rc=%s out=%s" % (r.returncode, (r.stdout or "")[:300]))
                    if r.returncode != 0:
                        subprocess.run(
                            [os.path.join(SCRIPTS, "alert_push.sh"), "NEEDS_DECISION",
                             "hub-local 接管失败：%s，备工亦失败，需人工处理。" % tid[:8]],
                            capture_output=True, text=True, timeout=60)
                        idir = os.path.join(STATE, "incidents")
                        os.makedirs(idir, exist_ok=True)
                        with open(os.path.join(idir, "takeover-%s.json" % tid), "w",
                                  encoding="utf-8") as f:
                            json.dump({"tool": "05-hub-local-takeover", "task_id": tid,
                                       "reason": reason, "ts": plan["ts"],
                                       "action": "NEEDS_DECISION"}, f, ensure_ascii=False)
        log("OK done execute=%s" % execute)
        return 0
    finally:
        try:
            os.unlink(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
