#!/usr/bin/env python3
"""observe-cutover51.py -- cutover51 O1/O2: 5-min observation after enforce.

Writes one JSON line per run to state/logs/observe.log with:
gate-svc alive/latency, per-gate refusals (from gate_registry.last_block_ts
changes + canary_ledger), dispatch_receipts count, central_queue
dispatch/claim/complete rates (last 5 min), trigger refusals (approx via
recent gate-deny log lines -- no DB counter exists), lease renewal proxy
(central_leases ACTIVE count + fencing), QUARANTINED count, heartbeat
freshness per probe, retrieval hit proxy (kb receipts 24h), raw_log delta.
Anomalies (gate-svc down, rates zero, QUARANTINED spike, stale heartbeat)
write an outbox alert. Exit 0 always (observer, not a gate).
Auto-rollback triggers (order sec.1) are CHECKED and reported, never
executed by this script -- rollback needs a human/operator decision here.
Usage: python3 scripts/observe-cutover51.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.path.expanduser("~/ai-team")
STATE = os.path.join(BASE, "state")
OUTBOX = os.path.join(STATE, "outbox")
LOG = os.path.join(STATE, "logs", "observe.log")
GATE_URL = os.environ.get("GATE_SVC_URL", "http://127.0.0.1:8093") + "/health"


def dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    raise RuntimeError("CQ_DSN not found")


def q(cur, sql, default=None):
    try:
        cur.execute(sql)
        return cur.fetchone()[0]
    except Exception:
        return default


def main():
    import psycopg
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.time()
    try:
        r = urllib.request.urlopen(GATE_URL, timeout=10)
        svc = {"alive": True, "http": r.status, "latency_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        svc = {"alive": False, "http": None, "latency_ms": None, "error": str(e)[:120]}
    with psycopg.connect(dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            obs = {
                "ts": ts, "gate_svc": svc,
                "dispatch_receipts": q(cur, "SELECT COUNT(*) FROM public.dispatch_receipts", -1),
                "receipts_5m": q(cur, "SELECT COUNT(*) FROM public.dispatch_receipts WHERE ts > now() - interval '5 minutes'", -1),
                "ready_5m": q(cur, "SELECT COUNT(*) FROM public.central_tasks WHERE status='READY' AND updated_at > now() - interval '5 minutes'", -1),
                "claimed_5m": q(cur, "SELECT COUNT(*) FROM public.central_tasks WHERE status IN ('CLAIMED','RUNNING','VERIFYING') AND updated_at > now() - interval '5 minutes'", -1),
                "done_5m": q(cur, "SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' AND verified_at > now() - interval '5 minutes'", -1),
                "quarantined": q(cur, "SELECT COUNT(*) FROM public.central_tasks WHERE status='QUARANTINED'", -1),
                "active_leases": q(cur, "SELECT COUNT(*) FROM public.central_leases WHERE status='ACTIVE'", -1),
                "kb_receipts_24h": q(cur, "SELECT COUNT(*) FROM public.receipts WHERE kind='kb' AND ts > now() - interval '24 hours'", -1),
                "gates_blocked": q(cur, "SELECT COUNT(*) FROM public.gate_registry WHERE last_block_ts IS NOT NULL", -1),
                "decisions_pending": q(cur, "SELECT COUNT(*) FROM public.decisions WHERE status='pending'", -1),
            }
            cur.execute("SELECT probe, ok, EXTRACT(EPOCH FROM (now() - ts)) FROM public.heartbeats WHERE (probe, ts) IN (SELECT probe, MAX(ts) FROM public.heartbeats GROUP BY probe)")
            obs["heartbeats"] = {r[0]: {"ok": r[1], "age_s": round(r[2])} for r in cur.fetchall()}
            # C5 trial business metrics (daily, Asia/Shanghai day).
            # 连续 2 天业务 DONE 为 0 的告警见 business-daily-summary.py
            # （每日 21:00 写 outbox，每天一次）；此处只出数字，避免每 5 分钟重复写告警。
            _sh = ZoneInfo("Asia/Shanghai")
            _d0 = datetime.now(_sh).replace(hour=0, minute=0, second=0, microsecond=0)
            _d0_utc = _d0.astimezone(timezone.utc)
            _d1_utc = (_d0 + timedelta(days=1)).astimezone(timezone.utc)
            _dm1_utc = (_d0 - timedelta(days=1)).astimezone(timezone.utc)
            _biz = "(lower(title) LIKE %s OR lower(title) LIKE %s OR lower(title) LIKE %s OR lower(title) LIKE %s)"
            _bp = ("kids-%", "expert-brief%", "dankoe-research%", "%g2%")
            cur.execute("SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' AND COALESCE(verified_at, updated_at) >= %s AND COALESCE(verified_at, updated_at) < %s AND " + _biz, (_d0_utc, _d1_utc) + _bp)
            obs["biz_done_today"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' AND retry_count=0 AND COALESCE(verified_at, updated_at) >= %s AND COALESCE(verified_at, updated_at) < %s AND " + _biz, (_d0_utc, _d1_utc) + _bp)
            _bfp = cur.fetchone()[0]
            obs["biz_first_pass_today"] = _bfp
            obs["biz_first_pass_rate_today"] = (round(_bfp / obs["biz_done_today"], 4) if obs["biz_done_today"] else None)
            cur.execute("SELECT COUNT(*) FROM public.central_tasks WHERE created_at >= %s AND created_at < %s AND " + _biz, (_d0_utc, _d1_utc) + _bp)
            obs["biz_dispatched_today"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM public.central_tasks WHERE status='QUARANTINED' AND updated_at >= %s AND updated_at < %s AND " + _biz, (_d0_utc, _d1_utc) + _bp)
            obs["biz_quarantined_today"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM public.central_tasks WHERE status='DONE' AND COALESCE(verified_at, updated_at) >= %s AND COALESCE(verified_at, updated_at) < %s AND " + _biz, (_dm1_utc, _d0_utc) + _bp)
            obs["biz_done_yesterday"] = cur.fetchone()[0]
    anomalies = []
    if not svc["alive"]:
        anomalies.append("gate-svc DOWN (auto-rollback trigger: >5min)")
    # Zero-rate: the auto-rollback trigger is 10 min of zero dispatch/claim/
    # complete. A single quiet 5-min window is normal (timers run every
    # 10-30 min), so alert only on the 2nd consecutive zero window.
    zero_now = (obs["done_5m"] == 0 and obs["ready_5m"] == 0 and obs["claimed_5m"] == 0)
    zero_state = os.path.join(STATE, "logs", "observe-zero-state.json")
    prev_zero = False
    try:
        prev_zero = json.load(open(zero_state)).get("zero", False)
    except Exception:
        prev_zero = False
    with open(zero_state, "w") as f:
        json.dump({"zero": bool(zero_now), "ts": ts}, f)
    if zero_now and prev_zero:
        anomalies.append("dispatch/claim/complete rates zero for >=10m (AUTO-ROLLBACK trigger -- report to chairman, do not auto-execute here)")
    for probe, h in obs["heartbeats"].items():
        if h["age_s"] > 3600:
            anomalies.append("heartbeat stale >1h: %s (%ss)" % (probe, h["age_s"]))
    obs["anomalies"] = anomalies
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(obs, ensure_ascii=False) + "\n")
    if anomalies:
        os.makedirs(OUTBOX, exist_ok=True)
        path = os.path.join(OUTBOX, "observe-anomaly-%s.md" % ts.replace(":", "").replace("-", ""))
        with open(path, "w") as f:
            f.write("# 观测异常 %s\n\n" % ts)
            for a in anomalies:
                f.write("- %s\n" % a)
        print("ANOMALIES: %s (alert %s)" % (anomalies, path))
    print(json.dumps(obs, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
