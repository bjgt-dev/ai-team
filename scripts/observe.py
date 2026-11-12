#!/usr/bin/env python3
# observe.py — 切运后观测 O1-O5（2026-11-10，enforce 后启动）
# 每 5 分钟跑一次（首 2 小时），之后每 30 分钟；写 ~/ai-team/state/observe.log。
# O1 gate-svc 存活/延迟/5xx；O2 /search 调用量与命中率与 0 结果率；
# O3 /act 通过与拒绝数与拒绝原因分布；O4 central_queue 派单/领取/完成速率与触发器拒绝数；
# O5 raw_log 各席位增量、extractor 每轮抽取条数与错误。
# 异常（回滚触发条件）即写 observe.log 标 ALERT。
import json, os, subprocess, sys, time, urllib.request

LOG = "/home/ubuntu/ai-team/state/observe.log"
GATE = "http://127.0.0.1:8093"
STATE = "/home/ubuntu/ai-team/state/gate/observe-state.json"

def psql(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "ops-pg", "psql", "-U", "postgres",
         "-d", "central_queue", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-c", sql],
        capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()

def http_get(path, timeout=10):
    t0 = time.time()
    try:
        with urllib.request.urlopen(GATE + path, timeout=timeout) as resp:
            return resp.status, time.time() - t0, resp.read()[:500]
    except Exception as e:
        return None, time.time() - t0, str(e)[:200]

def main():
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = []
    alerts = []
    # O1 gate-svc
    st, lat, body = http_get("/health")
    o1 = "gate-svc health=%s latency=%.2fs" % (st, lat)
    if st != 200:
        alerts.append("gate-svc DOWN (health=%s)" % st)
    lines.append("O1 %s" % o1)
    # O2 receipts/search volume (last 5 min)
    rc, out, _ = psql(
        "SELECT COUNT(*) FROM receipts WHERE ts > now() - interval '5 minutes';")
    n5 = out if rc == 0 else "?"
    rc, out, _ = psql(
        "SELECT COUNT(*) FROM receipts WHERE ts > now() - interval '1 hour';")
    n1h = out if rc == 0 else "?"
    lines.append("O2 receipts last5m=%s last1h=%s" % (n5, n1h))
    # O3 act ledger (gate-act-ledger.jsonl is gatesvc-owned; count via sudo cat if readable)
    act_total = act_ok = act_deny = "?"
    try:
        r = subprocess.run(["sudo", "cat", "/home/ubuntu/ai-team/state/gate-act-ledger.jsonl"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            rows = [json.loads(l) for l in r.stdout.strip().split("\n") if l.strip()]
            act_total = len(rows)
            act_ok = sum(1 for x in rows if str(x.get("result", "")).lower() in ("ok", "pass", "allowed", "executed"))
            act_deny = act_total - act_ok
    except Exception:
        pass
    lines.append("O3 act total=%s ok=%s deny=%s" % (act_total, act_ok, act_deny))
    # O4 central_queue rates (last 5 min: READY created, DONE)
    rc, out, _ = psql(
        "SELECT COUNT(*) FROM central_tasks WHERE created_at > now() - interval '5 minutes';")
    created5 = out if rc == 0 else "?"
    rc, out, _ = psql(
        "SELECT COUNT(*) FROM central_tasks WHERE status='DONE' AND verified_at > now() - interval '5 minutes';")
    done5 = out if rc == 0 else "?"
    rc, out, _ = psql(
        "SELECT COUNT(*) FROM central_tasks WHERE status='QUARANTINED' AND updated_at > now() - interval '5 minutes';")
    q5 = out if rc == 0 else "?"
    lines.append("O4 queue created5m=%s done5m=%s quarantined5m=%s" % (created5, done5, q5))
    # O5 raw_log increments + extractor
    rc, out, _ = psql(
        "SELECT seat, COUNT(*) FROM raw_log WHERE ts > now() - interval '5 minutes' GROUP BY seat;")
    raw5 = out.replace("\n", ",") if rc == 0 and out else "none"
    lines.append("O5 raw_log5m: %s" % raw5)
    # canary ledger (should stay all ok)
    rc, out, _ = psql("SELECT COUNT(*) FROM canary_ledger WHERE ok=false;")
    bad = out if rc == 0 else "?"
    if bad not in ("0", "?"):
        alerts.append("canary_ledger has %s failing rows" % bad)
    lines.append("O5 canary_fail=%s" % bad)
    # write
    entry = "[%s] %s" % (ts, " | ".join(lines))
    with open(LOG, "a") as f:
        f.write(entry + "\n")
        for a in alerts:
            f.write("[%s] ALERT %s\n" % (ts, a))
    print(entry)
    if alerts:
        print("ALERTS: " + "; ".join(alerts))
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
