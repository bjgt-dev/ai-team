#!/usr/bin/env python3
"""business-weekly-report.py — C5 一周单业务试跑 · 第 7 天周报。

内容：通过率、失败按四类的数量与占比、占比最高类别的单点修复建议（只提 1 项）。
失败类别口径与 business-failure-log 一致：
  1 任务过大或模糊；2 模型能力不足；3 需求或验收标准不清；4 基建故障。
单点修复建议按最高占比类别取一则（映射见 FIX_SUGGESTION）；本周零失败则写明
无需修复项，不硬凑建议。

窗口：--start/--end 为北京时间自然日（含两端）。试跑第 1 天 2026-11-10、
第 7 天 2026-11-16；cron 定在 2026-11-16 21:00；若试跑起始日后移，
改 cron 里的 --start/--end 即可，不用改代码。
输出：state/business-weekly-YYYYMMDD-YYYYMMDD.md。
只读 central_tasks + business_failure_log；不改任务状态、不删数据。
用法：python3 scripts/business-weekly-report.py --start YYYY-MM-DD --end YYYY-MM-DD [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.path.expanduser("~/ai-team")
STATE = os.path.join(BASE, "state")
SH = ZoneInfo("Asia/Shanghai")

BIZ = ("(lower(title) LIKE %s OR lower(title) LIKE %s "
       "OR lower(title) LIKE %s OR lower(title) LIKE %s)")
BIZ_PATTERNS = ("kids-%", "expert-brief%", "dankoe-research%", "%g2%")

CAT_NAMES = {
    1: "任务过大或模糊",
    2: "模型能力不足",
    3: "需求或验收标准不清",
    4: "基建故障",
}
FIX_SUGGESTION = {
    1: "把任务拆到更小：单任务只含一个可独立验收的改动，合同写死输入/输出/测试命令，"
       "估时仍超 20 分钟就再拆一刀。",
    2: "降难度或换更强模型跑该类任务：先在枢纽复现最小失败用例，确认是能力缺口再升级模型，"
       "未定位前不盲目重试。",
    3: "补验收标准：合同 pass_line 写成机器可判的数字（pytest 数/文件 sha/输出行数），"
       "模糊描述先冻结不派。",
    4: "修执行腿本身：先定位认领/执行/回传三段中哪一段断，再重试；未定位前不重试。",
}


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


def window_bounds(start_str, end_str):
    s = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=SH)
    e = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=SH)
    return s.astimezone(timezone.utc), (e + timedelta(days=1)).astimezone(timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    start, end = window_bounds(a.start, a.end)
    import psycopg
    with psycopg.connect(dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM public.central_tasks WHERE created_at >= %s "
                "AND created_at < %s AND " + BIZ, (start, end) + BIZ_PATTERNS)
            dispatched = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' "
                "AND COALESCE(verified_at, updated_at) >= %s "
                "AND COALESCE(verified_at, updated_at) < %s AND " + BIZ, (start, end) + BIZ_PATTERNS)
            done = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' "
                "AND retry_count=0 AND COALESCE(verified_at, updated_at) >= %s "
                "AND COALESCE(verified_at, updated_at) < %s AND " + BIZ, (start, end) + BIZ_PATTERNS)
            first_pass = cur.fetchone()[0]
    fail_cats = fail_cats_via_docker(start, end, business="code-kids")
    total_fail = sum(fail_cats.values())
    pass_rate = (done / dispatched) if dispatched else None
    fp_rate = (first_pass / done) if done else None

    lines = []
    lines.append("# 业务周报 %s 至 %s（C5 试跑 · code-kids）" % (a.start, a.end))
    lines.append("")
    lines.append("窗口：北京时间自然日，含两端；业务口径：C3。")
    lines.append("")
    lines.append("## 通过率")
    lines.append("")
    lines.append("- 本周派发：%d" % dispatched)
    lines.append("- 本周通过（DONE）：%d" % done)
    lines.append("- 其中首次通过（retry_count=0）：%d" % first_pass)
    lines.append("- 通过率（DONE/派发）：%s" % (
        "%.1f%%" % (pass_rate * 100) if pass_rate is not None else "无（本周派发为 0）"))
    lines.append("- 首次通过率（首次通过/DONE）：%s" % (
        "%.1f%%" % (fp_rate * 100) if fp_rate is not None else "无（本周通过为 0）"))
    lines.append("")
    lines.append("## 失败按四类（占比）")
    lines.append("")
    if not total_fail:
        lines.append("- 本周失败账本零记录。")
    for cat in (1, 2, 3, 4):
        n = fail_cats.get(cat, 0)
        share = (n / total_fail * 100) if total_fail else 0.0
        lines.append("- 类%d（%s）：%d 条，占失败 %.1f%%" % (cat, CAT_NAMES[cat], n, share))
    lines.append("- 合计失败：%d 条" % total_fail)
    lines.append("")
    lines.append("## 单点修复建议（只提 1 项）")
    lines.append("")
    if not total_fail:
        lines.append("- 本周零失败，无需修复项。")
    else:
        top = max(fail_cats.items(), key=lambda kv: kv[1])[0]
        lines.append("- 占比最高类别：类%d（%s），%d 条，占失败 %.1f%%。" % (
            top, CAT_NAMES[top], fail_cats[top], fail_cats[top] / total_fail * 100))
        lines.append("- 建议：%s" % FIX_SUGGESTION[top])
    lines.append("")
    text = "\n".join(lines)

    if a.dry_run:
        print(text)
        print("DRY-RUN: 未写文件")
        return 0
    out_path = os.path.join(STATE, "business-weekly-%s-%s.md" % (
        a.start.replace("-", ""), a.end.replace("-", "")))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("WROTE %s dispatched=%d done=%d first_pass=%d failures=%d" % (
        out_path, dispatched, done, first_pass, total_fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
