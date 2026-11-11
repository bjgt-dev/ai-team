#!/usr/bin/env python3
"""check-quarantine-stuck.py — QUARANTINED 任务久置告警（2026-11-08 董事长骂后立）。

根因：kids-iteration4 在 QUARANTINED 停了一天没人发现——三问体检只把
QUARANTINED 当“已知隔离”计数，不告警；retry 耗尽后无任何机制再盯它。
机制：本脚本查 central_tasks 里 QUARANTINED 超过 STUCK_HOURS（默认 6h）
的任务，逐条列出（标题、隔离时长、retry），超期即 exit 1；
无久置项 exit 0。由 cron 每 30 分钟跑，exit 1 写 outbox 告警。
用法：python3 check-quarantine-stuck.py [--hours N]
"""
import os
import subprocess
import sys

STUCK_HOURS = int(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 6


def run_psql(sql):
    env = dict(os.environ)
    dsn_file = os.path.expanduser("~/ai-team/state/.cq-dsn")
    with open(dsn_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("CQ_DSN="):
                env["CQ_DSN"] = line.split("=", 1)[1].strip('"')
    out = subprocess.run(
        ["psql", env["CQ_DSN"], "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, env=env, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError("psql failed: " + out.stderr.strip()[:200])
    return out.stdout.strip()


def write_outbox(stuck_lines):
    """FAIL 时写一份 outbox 告警（每天一份，覆盖当日旧的，不刷屏）。"""
    from datetime import date
    outbox = os.path.expanduser("~/ai-team/state/outbox")
    os.makedirs(outbox, exist_ok=True)
    path = os.path.join(outbox, "quarantine-stuck-%s.md" % date.today().isoformat())
    with open(path, "w", encoding="utf-8") as f:
        f.write("# QUARANTINED 久置告警（%s）\n\n" % date.today().isoformat())
        f.write("以下任务在 QUARANTINED 停超 %d 小时，无人处理：\n\n" % STUCK_HOURS)
        for line in stuck_lines:
            parts = line.split("|")
            f.write("- %s | retry %s | 停了 %.1fh\n" % (parts[0], parts[2], float(parts[3])))
        f.write("\n处理：查根因、修好后重置 READY；修不好的写清原因关单。\n")
    return path


def main():
    sql = (
        "SELECT title, status, retry_count, "
        "EXTRACT(EPOCH FROM (NOW() - updated_at))/3600 AS stuck_h "
        "FROM central_tasks WHERE status='QUARANTINED' "
        "AND updated_at < NOW() - INTERVAL '%d hours' ORDER BY updated_at;" % STUCK_HOURS
    )
    try:
        rows = run_psql(sql)
    except Exception as e:
        print("FAIL 查不到队列：%s" % e)
        return 2
    if not rows:
        print("PASS 无 QUARANTINED 超 %dh 的任务" % STUCK_HOURS)
        return 0
    print("FAIL 以下 QUARANTINED 任务已停超 %dh：" % STUCK_HOURS)
    for line in rows.splitlines():
        parts = line.split("|")
        print("  - %s | retry %s | 停了 %.1fh" % (parts[0], parts[2], float(parts[3])))
    path = write_outbox(rows.splitlines())
    print("告警已写：%s" % path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
