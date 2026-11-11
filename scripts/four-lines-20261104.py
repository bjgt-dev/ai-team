#!/usr/bin/env python3
"""Helper for morning-report.sh: prints two lines, ALERT24=... and FOUR=...
from four-numbers-20261104.py. Falls back to 'unmeasured' lines on error."""
import json, subprocess, sys
try:
    r = subprocess.run(["python3", "/home/ubuntu/ai-team/scripts/four-numbers-20261104.py"],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout)
    print("ALERT24=24小时内 incident 指纹 %s 个（RED_ALERT 行 %s 条，测试指纹除外；自愈/需人拆分未区分，alerts.log 无此字段）"
          % (d.get("incident_fingerprint_count_24h", "?"), d.get("alerts_24h_lines", "?")))
    rec = "未统计" if d.get("recurrence_rate") is None else str(d.get("recurrence_rate"))
    print("FOUR=四数：非 L3 人工介入 %s、复发率 %s、无主项 %s（决策队列 pending；任务表 owner 空 %s 项另列不混入）、日交付产出 %s"
          % (d.get("non_l3_escalations", "?"), rec, d.get("owner_empty_decisions_pending", "?"),
             d.get("central_tasks_owner_null", "?"), d.get("daily_deliverable_output", "?")))
except Exception:
    print("ALERT24=24小时内 incident 未统计")
    print("FOUR=四数：未统计")
