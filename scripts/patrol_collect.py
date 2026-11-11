#!/usr/bin/env python3
# 巡航确定性采集层（2026-10-24 Twt 代建）：无模型依赖，永远先落心跳。
# 采集队列/告警/网关/磁盘/脑状态，写 patrol-latest.md 并向 sort-log 追加 auto-collect 行（沿用上轮 top3 并标注）。
import json, os, subprocess, time
BASE = "/home/ubuntu/ai-team"
now = time.strftime("%F %T")
def count_files(d):
    try: return sum(len(fs) for _, _, fs in os.walk(d))
    except Exception: return -1
pend = {w: count_files(f"{BASE}/contracts/pending/{w}") for w in ("cue","guangtou","muse","noesis")}
claim = count_files(f"{BASE}/contracts/claimed")
alerts = []
try:
    qa = json.load(open(f"{BASE}/state/queue_alerts.json"))
    alerts = qa if isinstance(qa, list) else qa.get("alerts", [])
except Exception: pass
def sh(cmd):
    try: return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception: return "?"
gw = sh("sudo -n docker ps --format '{{.Names}} {{.Status}}' | grep -i openclaw-gateway | head -1") or "?"
disk = sh("df -h / | tail -1 | awk '{print $4\"可用(\"$5\"已用)\"}'")
brain = sh(f"tail -1 {BASE}/state/brain-active.log") or "?"
top3 = []
try:
    lines = open(f"{BASE}/state/priority/sort-log.jsonl").read().strip().split("\n")
    for ln in reversed(lines):
        o = json.loads(ln)
        if o.get("top3"): top3 = o["top3"]; break
except Exception: pass
pend_total = sum(v for v in pend.values() if v > 0)
report = [
    f"# 巡航 {now}（auto-collect 心跳层）｜模型轮次不可用时由此层保底落盘",
    f"- 队列：pending 合计 {pend_total}（{pend}）、claimed {claim}；告警 {len(alerts)} 条。",
    f"- 网关：{gw}；磁盘 {disk}。",
    f"- 脑：{brain}",
    f"- 排序：沿用上轮 top3（模型判断待强脑恢复后由模型轮次覆盖）：{json.dumps(top3, ensure_ascii=False)[:200]}",
]
open(f"{BASE}/ceo-seat/out/patrol-latest.md", "w").write("\n".join(report) + "\n")
entry = {"ts": time.strftime("%FT%T+08:00"), "mode": "auto-collect", "top3": top3, "note": "确定性采集层落盘；模型判断轮次待强脑恢复"}
with open(f"{BASE}/state/priority/sort-log.jsonl", "a") as f: f.write(json.dumps(entry, ensure_ascii=False) + "\n")
print("COLLECT_OK", now)
