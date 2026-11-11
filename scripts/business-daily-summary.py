#!/usr/bin/env python3
"""business-daily-summary.py — C5 一周单业务试跑 · 每日 21:00 汇总。

汇总当日（北京时间自然日）业务产出：今日派发、通过、失败、交付物清单。
业务口径与 C3 盘点一致：标题前缀 kids-/expert-brief/dankoe-research，
或标题含 G2；其余一律不算业务。

输出：state/business-daily-YYYY-MM-DD.md（固定位置，按日期命名）。
告警：业务类 DONE 连续 2 天为 0 时写 outbox 告警
state/outbox/business-zero-2d-YYYY-MM-DD.md（每天只写一次），并在
stdout 打印 ALERT 行（cron 日志可见）。计数状态在
state/logs/business-zero-days.json。

只读 central_tasks + business_failure_log；唯一写操作是汇总文件、
计数状态与 outbox 告警；不改任务状态、不删数据。
用法：python3 scripts/business-daily-summary.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.path.expanduser("~/ai-team")
STATE = os.path.join(BASE, "state")
OUTBOX = os.path.join(STATE, "outbox")
LOGDIR = os.path.join(STATE, "logs")
SH = ZoneInfo("Asia/Shanghai")

BIZ = ("(lower(title) LIKE %s OR lower(title) LIKE %s "
       "OR lower(title) LIKE %s OR lower(title) LIKE %s)")
BIZ_PATTERNS = ("kids-%", "expert-brief%", "dankoe-research%", "%g2%")


def dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    raise RuntimeError("CQ_DSN not found")


def fail_cats_via_docker(start, end, business=None):
    """业务失败账本读数：DSN 账号无 business_failure_log 读权限，
    走既定通道 docker exec -u postgres 只读 SELECT（与
    business-failure-log-append.sh 同口径）。"""
    import subprocess
    sql = ("SELECT category, COUNT(*) FROM public.business_failure_log "
           "WHERE ts >= '%s' AND ts < '%s'" % (
               start.strftime("%Y-%m-%dT%H:%M:%SZ"),
               end.strftime("%Y-%m-%dT%H:%M:%SZ")))
    if business:
        sql += " AND business='%s'" % business.replace("'", "''")
    sql += " GROUP BY category ORDER BY category"
    r = subprocess.run(["docker", "exec", "-u", "postgres", "ops-pg",
                        "psql", "-d", "central_queue", "-t", "-A", "-c", sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("failure-log read failed: %s" % (r.stderr or "")[:200])
    out = {}
    for line in (r.stdout or "").strip().splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            out[int(k)] = int(v)
    return out



def biz_bypass_today(date_str):
    """当日旁路交付数：hub 本地直调网关的交付记入 state/business-bypass.jsonl，
    每行一个 JSON，含 date（北京时间 YYYY-MM-DD）或 ts；文件不存在即 0。
    旁路交付不计入业务通过率，单独计数。"""
    import json as _json
    path = os.path.join(STATE, "business-bypass.jsonl")
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except ValueError:
                    continue
                if d.get("date") == date_str:
                    n += 1
                elif isinstance(d.get("ts"), str) and d["ts"][:10] == date_str:
                    n += 1
    except OSError:
        return 0
    return n

def day_bounds(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=SH)
    start = d.astimezone(timezone.utc)
    end = (d + timedelta(days=1)).astimezone(timezone.utc)
    return start, end


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="汇总日期 YYYY-MM-DD（默认今天，北京时间）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只计算打印，不写文件")
    a = ap.parse_args()
    date_str = a.date or datetime.now(SH).strftime("%Y-%m-%d")
    start, end = day_bounds(date_str)
    import psycopg
    with psycopg.connect(dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT task_id, title, status, retry_count, "
                "COALESCE(artifact_sha,'') AS sha, COALESCE(artifact_bytes,0) AS n, "
                "COALESCE(payload->'contract'->>'output_path','') AS op "
                "FROM public.central_tasks "
                "WHERE created_at >= %s AND created_at < %s AND " + BIZ +
                " ORDER BY created_at", (start, end) + BIZ_PATTERNS)
            dispatched = cur.fetchall()
            cur.execute(
                "SELECT task_id, title, status, retry_count, "
                "COALESCE(artifact_sha,'') AS sha, COALESCE(artifact_bytes,0) AS n, "
                "COALESCE(payload->'contract'->>'output_path','') AS op "
                "FROM public.central_tasks "
                "WHERE status='DONE' AND COALESCE(verified_at, updated_at) >= %s "
                "AND COALESCE(verified_at, updated_at) < %s AND " + BIZ +
                " ORDER BY COALESCE(verified_at, updated_at)", (start, end) + BIZ_PATTERNS)
            passed = cur.fetchall()
            cur.execute(
                "SELECT task_id, title, retry_count FROM public.central_tasks "
                "WHERE status='QUARANTINED' AND updated_at >= %s AND updated_at < %s "
                "AND " + BIZ + " ORDER BY updated_at", (start, end) + BIZ_PATTERNS)
            failed = cur.fetchall()
    fail_cats = fail_cats_via_docker(start, end)
    first_pass = [r for r in passed if r[3] == 0]
    biz_done = len(passed)
    rate = (len(first_pass) / biz_done) if biz_done else None

    lines = []
    lines.append("# 业务日报 %s（C5 试跑 · code-kids）" % date_str)
    lines.append("")
    lines.append("汇总时区：北京时间；业务口径：C3（kids-/expert-brief/dankoe-research 前缀或标题含 G2）。")
    lines.append("")
    lines.append("## 当日数字")
    lines.append("")
    lines.append("- 今日派发：%d" % len(dispatched))
    lines.append("- 今日通过（DONE）：%d" % biz_done)
    lines.append("- 其中首次通过（retry_count=0）：%d" % len(first_pass))
    lines.append("- 今日首次通过率：%s" % (
        "%.1f%%" % (rate * 100) if rate is not None else "无（当日通过为 0）"))
    lines.append("- 今日失败（QUARANTINED）：%d" % len(failed))
    lines.append("- 今日旁路（hub 本地直调网关）交付：%d（不计入业务通过率）" % biz_bypass_today(date_str))
    if fail_cats:
        lines.append("- 今日失败账本按类：%s" % ", ".join(
            "类%d=%d" % (k, v) for k, v in sorted(fail_cats.items())))
    lines.append("")
    lines.append("## 交付物清单（当日通过）")
    lines.append("")
    if not passed:
        lines.append("- 无")
    for task_id, title, _st, _rc, sha, n, op in passed:
        lines.append("- %s | %s | sha=%s bytes=%d | 合同输出=%s" % (
            title, task_id, (sha or "")[:12], n, op or "（未声明）"))
    lines.append("")
    lines.append("## 当日派发清单")
    lines.append("")
    if not dispatched:
        lines.append("- 无")
    for task_id, title, status, _rc, _sha, _n, _op in dispatched:
        lines.append("- %s | %s | 状态=%s" % (title, task_id, status))
    lines.append("")
    lines.append("## 当日失败清单")
    lines.append("")
    if not failed:
        lines.append("- 无")
    for task_id, title, rc in failed:
        lines.append("- %s | %s | retry=%d（原因见 business_failure_log）" % (
            title, task_id, rc))
    lines.append("")
    text = "\n".join(lines)

    if a.dry_run:
        print(text)
        print("DRY-RUN: 未写文件")
        return 0

    out_path = os.path.join(STATE, "business-daily-%s.md" % date_str)
    os.makedirs(STATE, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("WROTE %s dispatched=%d passed=%d first_pass=%d failed=%d" % (
        out_path, len(dispatched), biz_done, len(first_pass), len(failed)))

    # --- 连续 2 天业务 DONE 为 0 即告警（每天只写一次）---
    state_path = os.path.join(LOGDIR, "business-zero-days.json")
    try:
        st = json.load(open(state_path))
    except Exception:
        st = {"consecutive_zero": 0, "last_date": ""}
    if st.get("last_date") != date_str:
        st["consecutive_zero"] = (st.get("consecutive_zero", 0) + 1) if biz_done == 0 else 0
        st["last_date"] = date_str
        os.makedirs(LOGDIR, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(st, f)
    if st["consecutive_zero"] >= 2:
        alert_path = os.path.join(
            OUTBOX, "business-zero-2d-%s.md" % date_str)
        if not os.path.exists(alert_path):
            os.makedirs(OUTBOX, exist_ok=True)
            with open(alert_path, "w", encoding="utf-8") as f:
                f.write("# 告警：业务类 DONE 连续 2 天为 0（C5 试跑）\n\n")
                f.write("日期：%s；连续零产出天数：%d。\n\n" % (date_str, st["consecutive_zero"]))
                f.write("试跑业务：code-kids；请董事长知悉。\n")
            print("ALERT business-zero-2d days=%d alert=%s" % (
                st["consecutive_zero"], alert_path))
        else:
            print("ALERT already filed today: %s" % alert_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
