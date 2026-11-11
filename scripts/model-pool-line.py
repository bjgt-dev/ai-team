#!/usr/bin/env python3
"""Order15 morning pool line: 'T1 N / T2 M' first, then detail.
T1 = len(tier_a) + len(tier_b); T2 = len(tier_2). Tier-1 standard unchanged.
"""
import json, os
from datetime import datetime, timedelta
S = "/home/ubuntu/ai-team/state"
try:
    pool = json.load(open(os.path.join(S, "gateway-pool.json")))
    a = pool.get("tier_a") or []
    b = pool.get("tier_b") or []
    t2 = pool.get("tier_2") or []
    primary = pool.get("primary", "?")
    min2 = pool.get("pool_minimum_2_met")
    health = "A" + str(len(a)) + "/B" + str(len(b)) + "、下限2供应商" + ("已满足" if min2 else "未满足")
    t1n, t2m = len(a) + len(b), len(t2)
except Exception:
    primary = "?"; health = "池未读到"; a = []; b = []; t2 = []; t1n, t2m = 0, 0
try:
    tally = json.load(open(os.path.join(S, "canary-b-tally-20261104.json")))["tally"]
    parts = []
    for m, v in tally.items():
        parts.append(m + " " + str(v.get("passes", 0)) + "/" + str(v.get("runs", 0)))
    cb = "、".join(parts) if parts else "无"
except Exception:
    cb = "无"
try:
    cutoff = datetime.now() - timedelta(hours=24)
    live = []
    for line in open(os.path.join(S, "gateway-canary-20261104.jsonl"), encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        try:
            ts = datetime.strptime(d.get("ts", ""), "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if ts >= cutoff and d.get("pass3") and d.get("model") not in live:
            live.append(d["model"])
    actual = "、".join(live) if live else "近24h金丝雀全过0个"
except Exception:
    actual = "未统计"
try:
    brain = open(os.path.join(S, "brain-active.log"), encoding="utf-8").read().strip().splitlines()[-1].strip()
except Exception:
    brain = "名义选用未读到"
print(f"T1 {t1n} / T2 {t2m}；名义选用 {brain} / 主力 {primary}；通道健康 {health}；canary_b {cb}；实际出活 {actual}")
