#!/usr/bin/env python3
"""Order14 B6 canary_b tracker: runs gateway canary for agnes + gemini-preview
(standard unchanged: 3/3 items, 2-round logic replaced by tally rule here),
tallies pass3 over rolling 2 days (max 6 runs, 3/day), promotes to tier_b
ONLY at >=5/6 with latest run pass3. Otherwise leaves tier_b unchanged.
Never lowers the canary standard, never promotes on a single run."""
import json, os, subprocess, sys
from datetime import datetime, timedelta
STATE = "/home/ubuntu/ai-team/state"
POOL = os.path.join(STATE, "gateway-pool.json")
CANARY = os.path.join(STATE, "gateway-canary-20261104.jsonl")
TALLY = os.path.join(STATE, "canary-b-tally-20261104.json")
MODELS = ["agnes-3.0-flash", "gemini-3-flash-preview"]

def main():
    r = subprocess.run(["python3", os.path.join("/home/ubuntu/ai-team/scripts", "gateway_canary-20261104.py"),
                        "--models", ",".join(MODELS), "--round", "canary_b"],
                       capture_output=True, text=True, timeout=280)
    cutoff = datetime.now() - timedelta(days=2)
    tally = {}
    if os.path.exists(CANARY):
        rows = []
        for line in open(CANARY, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("round") != "canary_b" or d.get("model") not in MODELS:
                continue
            try:
                ts = datetime.strptime(d.get("ts", ""), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if ts >= cutoff:
                rows.append(d)
        for m in MODELS:
            ms = [d for d in rows if d["model"] == m][-6:]
            _p = sum(1 for d in ms if d.get("pass3"))
            tally[m] = {"runs": len(ms), "passes": _p, "ratio": "%d/%d" % (_p, len(ms)),
                        "latest_pass3": bool(ms[-1].get("pass3")) if ms else False}
    promoted = []
    pool = json.load(open(POOL))
    tier_b = list(pool.get("tier_b") or [])
    for m in MODELS:
        t = tally.get(m, {})
        if t.get("passes", 0) >= 5 and t.get("runs", 0) >= 5 and t.get("latest_pass3") and m not in tier_b:
            tier_b.append(m); promoted.append(m)
    if promoted:
        pool["tier_b"] = tier_b
        pool["tier_x"] = [x for x in (pool.get("tier_x") or []) if x not in promoted]
        json.dump(pool, open(POOL, "w"), ensure_ascii=False, indent=2)
    json.dump({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "canary_rc": r.returncode,
               "tally": tally, "promoted": promoted}, open(TALLY, "w"), ensure_ascii=False, indent=2)
    print(json.dumps({"canary_rc": r.returncode, "tally": tally, "promoted": promoted}, sort_keys=True))
    return 0
if __name__ == "__main__":
    sys.exit(main())
