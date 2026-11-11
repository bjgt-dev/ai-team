#!/usr/bin/env python3
"""全面监管巡检（2026-10-22 董事长令）：磁盘/网关/模型网关/ds2api/keeper/工人心跳/专家通道/常驻席巡航。
只写状态板 state/supervisor-latest.md；新故障才往 handoff.md 追加 SUPERVISOR_ALERT 行（同一持续故障不重复，恢复后再故障才新推）。"""
import json, os, subprocess, time, urllib.request

HUB = "/home/ubuntu/ai-team"
now = time.time()
rows, alerts = [], []

def check(name, ok, detail):
    status = "OK" if ok else "FAULT"
    rows.append(f"| {name} | {status} | {detail} |")
    if not ok:
        alerts.append(f"{name}: {detail}")

def sh(cmd, t=25):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t).stdout.strip()

# 磁盘
df = sh("df / | tail -1").split()
pct = int(df[4].rstrip("%")) if len(df) > 4 else -1
check("磁盘", pct < 90, f"根分区 {pct}%（告警线 90%）")

# 容器健康（关键三件）+ ds2api 赠品容器只记状态不告警（2026-10-22 拍板：赠品死活不报）
ps = sh("sudo -n docker ps --format '{{.Names}} {{.Status}}'")
for c in ("openclaw", "ai-stack-new-api-1", "vaultwarden"):
    line = next((l for l in ps.splitlines() if l.startswith(c)), "")
    check(f"容器/{c}", "Up" in line and "unhealthy" not in line, line or "不存在")
_line = next((l for l in ps.splitlines() if l.startswith("ds2api-smoke")), "")
_ok = "Up" in _line and "unhealthy" not in _line
rows.append(f"| 容器/ds2api-smoke | {'OK' if _ok else 'ABSENT'} | {_line or '不存在（赠品通道，不告警）'} |")

# newapi + ds2api 冒烟（团队 key，chat 单发）
try:
    key = open("/home/ubuntu/shared/worker-kit/newapi.key").read().strip()
    req = urllib.request.Request("http://127.0.0.1:3001/v1/chat/completions",
        data=json.dumps({"model": "deepseek-chat", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=40))
    ct = d.get("usage", {}).get("completion_tokens", 0)
    if ct > 0:
        rows.append(f"| 网关渠道9(DeepSeek·赠品) | OK | completion_tokens={ct} |")
    else:
        rows.append(f"| 网关渠道9(DeepSeek·赠品) | DOWN | completion_tokens={ct}（2026-10-22 董事长令降级赠品：不告警、不催登录） |")
except Exception as e:
    rows.append(f"| 网关渠道9(DeepSeek·赠品) | DOWN | {type(e).__name__}（赠品通道，静默） |")

# keeper 新鲜度：2026-11-03 B5B4 已退场（crontab 注释），不再按 FAULT 告警
kf = os.path.join(HUB, "logs/ds-token-keeper.log")
age = (now - os.path.getmtime(kf)) / 60 if os.path.exists(kf) else 9999
rows.append(f"| ds_token_keeper | RETIRED | 2026-11-03 B5B4 退场；末次日志 {age:.0f} 分钟前（仅记录） |")

# 工人心跳：原注释即“仅记录”，不应每 10 分钟刷 SUPERVISOR_ALERT
hb = os.path.join(HUB, "state/heartbeats/guangtou.json")
age = (now - os.path.getmtime(hb)) / 60 if os.path.exists(hb) else 9999
rows.append(f"| 工人/光头心跳 | OFFLINE | {age:.0f} 分钟前（仅记录，掉线走改派兜底机制） |")

# 专家通道（读最近一次验活结果文件）
for p in ("deepseek", "chatgpt", "claude", "gemini"):
    f = os.path.join(HUB, f"state/expert-ping-{p}.txt")
    if not os.path.exists(f):
        rows.append(f"| 专家/{p} | UNKNOWN | 未验活 |")
        continue
    try:
        d = json.load(open(f))
        ok = d.get("status") == "ok"
        ageh = (now - os.path.getmtime(f)) / 3600
        if ageh > 2:
            rows.append(f"| 专家/{p} | UNKNOWN | 验活 {ageh:.1f} 小时前 status={d.get('status')}，超过 2h 不判健康 |")
            continue
        check(f"专家/{p}", ok, f"验活 {ageh:.1f} 小时前 status={d.get('status')}")
    except Exception:
        rows.append(f"| 专家/{p} | UNKNOWN | 结果文件解析失败 |")

# 脑通道承重状态（brain_canary 写入，brain_guard 守护）
bf = os.path.join(HUB, "state/brain-status.json")
if os.path.exists(bf):
    try:
        bd = json.load(open(bf))
        _ageh = (now - os.path.getmtime(bf)) / 3600
        for k, label in (("work_primary", "Zen 干活"), ("expert_primary", "DeepSeek#1 专家"),
                         ("expert_backup", "Claude 专家备")):
            v = bd.get(k) or {}
            _st = v.get('status', 'UNKNOWN')
            _dt = v.get('detail', '')
            if _ageh > 192:
                _dt += f"；快照 {bd.get('updated_at') or '未知'} 已 {_ageh:.1f}h 未验活（STALE）"
                if _st == 'HEALTHY':
                    _st = 'STALE'
            rows.append(f"| 脑/{label} | {_st} | {_dt}；降级起点 {v.get('degraded_since') or '-'} |")
    except Exception:
        rows.append("| 脑/承重 | UNKNOWN | brain-status.json 解析失败 |")

# 常驻席巡航新鲜度
pf = os.path.join(HUB, "ceo-seat/out/patrol-latest.md")
age = (now - os.path.getmtime(pf)) / 60 if os.path.exists(pf) else 9999
# 2026-11-05 PATROLNEG: supervisor 与 patrol 同分钟起跑时 patrol mtime 可能晚于 now，
# age 为负会显示“patrol -0 分钟前”；新鲜度只表达“已经过去多久”，负数按 0 处理。
age = max(0.0, age)
check("常驻席巡航", age < 50, f"patrol {age:.0f} 分钟前（周期 30 分钟）")

ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
board = "# 全面监管状态板\n\n更新：" + ts + "（每 30 分钟）\n\n| 项目 | 状态 | 详情 |\n|---|---|---|\n" + "\n".join(rows) + "\n"
open(os.path.join(HUB, "state/supervisor-latest.md"), "w").write(board)

# 新故障才告警（2026-11-04 SUPDEDUP：按故障名做“出现→恢复→再出现”事件去重）
# 旧逻辑 10 分钟窗口 < cron 30 分钟周期，同一持续故障每轮重复追加 SUPERVISOR_ALERT；
# 现改为只在故障首次出现时推一次，seen 中保留当前故障集，恢复后键消失，再次出现即新推。
seenf = os.path.join(HUB, "state/supervisor-seen.json")
try:
    raw = json.load(open(seenf))
except Exception:
    raw = {}
# 旧格式 {故障名: 时间戳} 没有恢复记录，无法区分持续与已恢复；迁移首轮按空集处理，
# 避免“新发生但历史上见过”的故障被旧键永久吞掉。新格式带版本标记。
if isinstance(raw, dict) and raw.get("__dedup__") == 2:
    seen = set(raw.get("active") or [])
else:
    seen = set()
current = {}
for a in alerts:
    current[a.split(":")[0]] = a
new = [a for k, a in current.items() if k not in seen]
json.dump({"__dedup__": 2, "active": sorted(current)}, open(seenf, "w"))
if new:
    with open(os.path.join(HUB, "state/handoff.md"), "a") as f:
        for a in new:
            f.write(f"\n## {ts} SUPERVISOR_ALERT（全面监管自动）\n- {a}\n")
print(f"checks={len(rows)} faults={len(alerts)} new_alerts={len(new)}")
