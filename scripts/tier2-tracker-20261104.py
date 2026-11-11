#!/usr/bin/env python3
"""Order15 Tier-2 tracker: runs T2 canary for given models, adds pass2 models
to gateway-pool.json tier_2 (never removes, never touches tier_a/tier_b or
Tier-1 standard). Writes state/tier2-tally-20261104.json.
Tier-2 output rule (documented, enforced by callers, not here): all Tier-2
output is verifier-sampled; a failure retries with the next Tier-2 model, max 3.
"""
import json, os, subprocess, sys
from datetime import datetime
STATE = "/home/ubuntu/ai-team/state"
POOL = os.path.join(STATE, "gateway-pool.json")
TALLY = os.path.join(STATE, "tier2-tally-20261104.json")

def main():
    models = sys.argv[1].split(",") if len(sys.argv) > 1 else ["agnes-3.0-flash", "gemini-3-flash-preview"]
    r = subprocess.run(["python3", "/home/ubuntu/ai-team/scripts/gateway_canary_tier2-20261104.py",
                        "--models", ",".join(models), "--round", "tier2"],
                       capture_output=True, text=True, timeout=900)
    added = []
    pool = json.load(open(POOL))
    t2 = list(pool.get("tier_2") or [])
    # read latest T2 records for these models
    latest = {}
    try:
        for line in open(os.path.join(STATE, "gateway-canary-tier2-20261104.jsonl"), encoding="utf-8"):
            d = json.loads(line)
            if d.get("model") in models:
                latest[d["model"]] = d
    except FileNotFoundError:
        pass
    scores = {}
    for m in models:
        rec = latest.get(m, {})
        rounds = rec.get("rounds") or []
        _pp = sum(1 for r in rounds if r.get("pass"))
        scores[m] = "%d/%d" % (_pp, len(rounds)) if rounds else "0/0 (no rounds)"
    for m in models:
        rec = latest.get(m, {})
        if rec.get("pass2") and m not in t2:
            t2.append(m)
            added.append(m)
    if added:
        pool["tier_2"] = t2
        pool["tier2_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        json.dump(pool, open(POOL, "w"), ensure_ascii=False, indent=2)
    json.dump({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "canary_rc": r.returncode, "models": models,
               "latest": latest, "scores": scores, "added": added, "tier_2": t2},
              open(TALLY, "w"), ensure_ascii=False, indent=2)
    print(json.dumps({"canary_rc": r.returncode, "added": added, "tier_2": t2}, ensure_ascii=False))

if __name__ == "__main__":
    main()
