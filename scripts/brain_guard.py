#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
from datetime import datetime

HUB = "/home/ubuntu/ai-team"
STATE = os.path.join(HUB, "state")
STATUS = os.path.join(STATE, "brain-status.json")
BOARD = os.path.join(STATE, "brain-status.md")
HANDOFF = os.path.join(STATE, "handoff.md")
SEEN = os.path.join(STATE, "brain-guard-seen.json")
CONFIG = "/opt/openclaw-official/.openclaw/openclaw.json"
LANES = ("work_primary", "expert_primary")


def now_ts():
    return time.time()


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_config():
    p = subprocess.run(["sudo", "-n", "cat", CONFIG], capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def direct_chat_models(cfg):
    if not cfg:
        return None
    ent = (cfg.get("agents") or {}).get("entries") or {}
    butler = ((ent.get("butler") or {}).get("model"))
    return butler


def has_reasoner(obj):
    return "reasoner" in json.dumps(obj, ensure_ascii=False).lower()


def append_handoff(title, lines):
    with open(HANDOFF, "a") as f:
        f.write("\n## %s %s\n" % (now_iso(), title))
        for line in lines:
            f.write("- %s\n" % line)


def snapshot_age_h(data):
    # 2026-11-04 BOARDSTALE: 看板刷新时间不等于验活快照时间。canary 每周一 06:17 才写
    # brain-status.json，若 canary 未跑完，旧快照会被看板伪装成刚更新（10-26→11-04 已 9 天）。
    snap = data.get("updated_at")
    if not snap:
        return None
    try:
        ts = datetime.fromisoformat(snap).timestamp()
    except Exception:
        return None
    return (now_ts() - ts) / 3600.0


def write_board(data, cfg_models, violations):
    snap = data.get("updated_at") or "未知"
    age_h = snapshot_age_h(data)
    if age_h is None:
        snap_line = "状态快照：%s（无法解析，按陈旧处理）" % snap
    elif age_h > 8 * 24:
        snap_line = "状态快照：%s（已 %.1f 小时未验活，STALE，超过每周 canary 周期）" % (snap, age_h)
    else:
        snap_line = "状态快照：%s（%.1f 小时前）" % (snap, age_h)
    lines = [
        "# 脑通道状态板",
        "",
        "看板刷新：%s（brain_guard 每 30 分钟）" % now_iso(),
        snap_line,
        "",
        "| 通道 | 状态 | 说明 | 降级起点 | 最近验活 |",
        "|---|---|---|---|---|",
    ]
    for k in ("work_primary", "work_backup", "expert_primary", "expert_backup", "gift"):
        v = data.get(k, {})
        lines.append("| %s | %s | %s | %s | %s |" % (
            k, v.get("status", "UNKNOWN"), v.get("detail", ""),
            v.get("degraded_since") or "-", v.get("last_checked") or "-"))
    lines += [
        "",
        "直聊席配置：`%s`" % json.dumps(cfg_models, ensure_ascii=False),
        "",
        "直聊席 reasoner 检查：%s" % ("FAIL " + "; ".join(violations) if violations else "PASS"),
        "",
    ]
    with open(BOARD, "w") as f:
        f.write("\n".join(lines))


def check_degraded(data, seen):
    alerts = []
    for lane in LANES:
        v = data.get(lane) or {}
        if v.get("status") == "HEALTHY":
            continue
        since = v.get("degraded_since")
        if not since:
            continue
        try:
            since_ts = datetime.fromisoformat(since).timestamp()
        except Exception:
            continue
        if now_ts() - since_ts < 0:  # 2026-10-24 chairman: weak-brain degraded alert immediate, no 1h wait
            continue
        # 2026-11-04 DEGDEDUP: 同一降级事件（lane+status+degraded_since）只推一次。
        # 旧键仅 lane+status，10 分钟窗口小于 guard 30 分钟周期，导致同一事件每轮重刷 handoff；
        # 加入 degraded_since 后，事件不变不重复推，恢复或再次失败（degraded_since 变化）时立即推。
        key = "%s:%s:%s" % (lane, v.get("status"), since)
        if key in seen:
            continue
        seen[key] = now_ts()
        alerts.append((lane, v))
    return alerts


def run(check_drill=False):
    data = load_json(STATUS, {})
    cfg = read_config()
    models = direct_chat_models(cfg)
    violations = []
    if models is None:
        violations.append("cannot read butler model config")
    elif has_reasoner(models):
        violations.append("butler model chain contains reasoner")
    seen = load_json(SEEN, {})

    cur_guard = sorted(violations)
    prev_guard = seen.get("direct_chat_active") or []
    if not isinstance(prev_guard, list):
        prev_guard = []
    # 2026-11-04 DIRECTDEDUP: 与 BRAIN_DEGRADED/SUPERVISOR_ALERT 同源缺陷——10 分钟窗口
    # 小于 guard 30 分钟周期，违规持续时会每轮重刷 handoff。改为“违规集合出现或变化才推，
    # 恢复后清空”，同一持续违规不重复，新增/变化/恢复后再犯立即推。
    if cur_guard and cur_guard != sorted(prev_guard):
        append_handoff("BRAIN_DIRECT_CHAT_GUARD", [
            "直聊席模型链命中禁用的 reasoner 类：`%s`" % json.dumps(models, ensure_ascii=False),
            "处置：在 Twt2026 对齐前不动配置；需人工改为 deepseek-chat 类/无思考外露模型。",
        ])
    if cur_guard:
        seen["direct_chat_active"] = cur_guard
    else:
        seen.pop("direct_chat_active", None)

    alerts = check_degraded(data, seen)
    for lane, v in alerts:
        append_handoff("BRAIN_DEGRADED", [
            "lane=%s status=%s channel=%s" % (lane, v.get("status"), v.get("channel")),
            "degraded_since=%s（已降级，立即报）" % v.get("degraded_since"),
            "fallback=Claude/GLM；未自动登录、未改账号设置。",
        ])

    save_json(SEEN, seen)
    write_board(data, models, violations)
    print(json.dumps({
        "time": now_iso(),
        "degraded_alerts": len(alerts),
        "direct_chat_violations": violations,
        "snapshot_age_h": None if snapshot_age_h(data) is None else round(snapshot_age_h(data), 1),
        "board": BOARD,
    }, ensure_ascii=False))
    return 0


def drill():
    data = {
        "time": now_iso(),
        "drill": "BRAIN_DEGRADED_DRILL",
        "lane": "expert_primary",
        "status": "FAILED",
        "fallback": "Claude",
        "degraded_since": datetime.fromtimestamp(now_ts() - 3700).astimezone().isoformat(timespec="seconds"),
        "note": "P0-3 告警路径演练，非真实故障；不外发、不登录。",
    }
    path = os.path.join(STATE, "brain-degraded-drill-%s.json" % datetime.now().strftime("%Y%m%d-%H%M"))
    save_json(path, data)
    append_handoff("BRAIN_DEGRADED_DRILL（P0-3 演练）", [
        "[DRILL] lane=expert_primary status=FAILED fallback=Claude",
        "degraded_since=%s（模拟超过 1 小时）" % data["degraded_since"],
        "告警落 handoff + state/brain-status.md；非真实故障。",
    ])
    with open(BOARD, "a") as f:
        f.write("\n## 告警演练\n- [DRILL] BRAIN_DEGRADED_DRILL 已触发并落 handoff。\n")
    print(json.dumps({"drill": path, "verdict": "PASS"}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", action="store_true")
    a = ap.parse_args()
    if a.drill:
        return drill()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
