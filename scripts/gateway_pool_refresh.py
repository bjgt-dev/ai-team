#!/usr/bin/env python3
"""gateway pool refresh (U-18, 2026-11-10): pool re-validation stamp.

Problem: state/gateway-pool.json is only rewritten when canary_b_tracker
promotes a model (rare), so its mtime goes stale for days while health_3q
Q1 (after the U-18 fix) fails any pool older than 2h. A bare `touch` would
fake freshness, so this script rewrites the pool ONLY when the last-30-min
gateway_canary rounds prove every current tier_a+tier_b model still passes
3/3 (pass3). Membership is NEVER changed here -- entry/exit still follows
the Q1 3-consecutive-rounds rule via canary_b_tracker / model-health.
What changes: last_validated_at / validated_by / validated_models, plus
the file mtime (which then truthfully means "pool re-validated <2h ago").

Exit 0 = re-validated and rewritten; exit 1 = NOT rewritten (reason in
stdout JSON; a stale canary or a failing pool model must NOT refresh mtime).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
POOL = os.path.join(STATE, "gateway-pool.json")
CANARY = os.path.join(STATE, "gateway-canary-20261104.jsonl")
CST = timezone(timedelta(hours=8))
WINDOW_S = 1800


def parse_ts(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s).strip(), fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def main():
    now = datetime.now(CST)
    try:
        pool = json.load(open(POOL))
    except Exception as e:
        print(json.dumps({"ok": False, "reason": "pool_unreadable: %s" % e}))
        return 1
    models = list(pool.get("tier_a") or []) + list(pool.get("tier_b") or [])
    if len(models) < 2:
        print(json.dumps({"ok": False, "reason": "pool_models<2: %s" % models}))
        return 1
    latest_pass = {}
    try:
        for line in open(CANARY, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(d.get("ts"))
            if ts is None or (now - ts).total_seconds() > WINDOW_S:
                continue
            if d.get("pass3"):
                latest_pass[d.get("model")] = d.get("ts")
    except OSError as e:
        print(json.dumps({"ok": False, "reason": "canary_unreadable: %s" % e}))
        return 1
    missing = [m for m in models if m not in latest_pass]
    if missing:
        print(json.dumps({"ok": False,
                          "reason": "no pass3 in last 30min for: %s" % missing,
                          "validated": sorted(latest_pass)}))
        return 1
    pool["last_validated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    pool["validated_by"] = "gateway_canary"
    pool["validated_models"] = sorted(latest_pass)
    tmp = POOL + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    os.replace(tmp, POOL)
    print(json.dumps({"ok": True, "validated_at": pool["last_validated_at"],
                      "models": sorted(latest_pass)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
