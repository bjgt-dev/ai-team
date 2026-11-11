#!/usr/bin/env python3
"""Order15 morning-report four numbers + 24h alert fingerprints (2026-11-04).
Sources are existing tables/files only. Anything without a structured field is
reported as 'unmeasured' with the reason -- never invented.
- non-L3 escalations: butler ledgers order13/order14, escalated_to_chairman=true
  AND level != L3. Ledger records carry level; none escalated in either file.
- recurrence: needs closed-then-reappeared fingerprint records; central_events
  has no SELECT permission for this reader and incidents/ has no close records,
  so recurrence is unmeasured (reason stated, not zero).
- owner-empty: decision-queue.json pending (0). central_tasks owner NULL is
  reported separately because the task pipeline does not fill owner for most
  timer/patrol tasks; it is NOT counted as an owner-empty incident/decision.
- daily output: central_tasks DONE + verified_by non-empty + payload type in
  (llm_doc, script) + verified_at in today (Asia/Shanghai). Timer/patrol DONE
  are excluded from 'deliverable' output and shown separately.
- alerts 24h: alerts.log RED_ALERT lines, fingerprint = token after RED_ALERT
  (plus model for CEO_DOWNGRADE). REDPOINT_TEST is a push test, excluded from
  incident fingerprints. alerts.log carries NO self-healed / needs-human flag,
  so the M/K split is unmeasured, not split by guesswork.
Output: JSON to stdout.
"""
import json, os, re
from collections import Counter
from datetime import datetime, timedelta

S = "/home/ubuntu/ai-team/state"
out = {"date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# 1. non-L3 escalations
esc = 0
for fn in ("butler-order13-ledger.jsonl", "butler-order14-ledger.jsonl"):
    try:
        for line in open(os.path.join(S, fn), encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("escalated_to_chairman") is True and d.get("level") != "L3":
                esc += 1
    except FileNotFoundError:
        pass
out["non_l3_escalations"] = esc

# 2. recurrence: unmeasured
out["recurrence_rate"] = None
out["recurrence_reason"] = "central_events no SELECT permission; incidents/ has no closed-fingerprint records"

# 3. owner-empty decisions/incidents
try:
    dq = json.load(open(os.path.join(S, "decision-queue.json")))
    out["owner_empty_decisions_pending"] = len(dq.get("pending", []))
except Exception as e:
    out["owner_empty_decisions_pending"] = None
    out["owner_empty_reason"] = str(e)[:120]
out["central_tasks_owner_null_note"] = "see PG count separately; not counted here (pipeline does not fill owner for timer/patrol tasks)"

# 4. daily output via PG
try:
    import psycopg
    dsn = ""
    for line in open(os.path.join(S, ".cq-dsn")):
        if line.strip().startswith("CQ_DSN="):
            dsn = line.strip()[7:].strip("\"'")
    with psycopg.connect(dsn) as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM central_tasks WHERE owner IS NULL")
        out["central_tasks_owner_null"] = cur.fetchone()[0]
        cur.execute("SELECT payload->>'type', count(*) FROM central_tasks WHERE status='DONE' AND verified_by IS NOT NULL AND verified_by <> '' AND DATE(verified_at AT TIME ZONE 'Asia/Shanghai') = CURRENT_DATE GROUP BY 1")
        by_type = dict(cur.fetchall())
        out["today_done_verified_by_type"] = by_type
        out["daily_deliverable_output"] = by_type.get("llm_doc", 0) + by_type.get("script", 0)
except Exception as e:
    out["daily_deliverable_output"] = None
    out["daily_output_reason"] = str(e)[:200]

# alerts 24h fingerprints
cut = datetime.now() - timedelta(hours=24)
lines = 0
fps = Counter()
for line in open(os.path.join(S, "alerts.log"), encoding="utf-8", errors="ignore"):
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if not m:
        continue
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except Exception:
        continue
    if ts < cut or "RED_ALERT" not in line:
        continue
    lines += 1
    mm = re.search(r"RED_ALERT\s+(\S+)", line)
    fp = mm.group(1) if mm else "UNKNOWN"
    if fp == "CEO_DOWNGRADE":
        mm2 = re.search(r"my-gateway/([\w\-.]+)", line)
        if mm2:
            fp = "CEO_DOWNGRADE:" + mm2.group(1)
    fps[fp] += 1
incident_fps = {k: v for k, v in fps.items() if k != "REDPOINT_TEST"}
out["alerts_24h_lines"] = lines
out["alert_fingerprints_24h"] = dict(fps)
out["incident_fingerprints_24h"] = incident_fps
out["incident_fingerprint_count_24h"] = len(incident_fps)
out["alert_selfhealed_vs_needs_human"] = None
out["alert_split_reason"] = "alerts.log has no self-healed / needs-human field; split not invented"
print(json.dumps(out, ensure_ascii=False, indent=2))
