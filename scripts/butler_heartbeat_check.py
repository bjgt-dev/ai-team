#!/usr/bin/env python3
"""Independent heartbeat checker for the butler seat. READ-ONLY except for
its own verdict file. It never writes ACK, patrol, a receipt, or B state,
and never clears a B episode. It is not seat_revive and not patrol."""
import json, os, re, time
ACK = "/home/ubuntu/ai-team/state/dispatch-ack.log"
PATROL = "/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"
OUT = "/home/ubuntu/ai-team/state/butler-seat-revive/heartbeat-verdict.json"
def mt(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None
def backlog(patrol):
    try:
        text = open(patrol, errors="ignore").read()
    except OSError:
        return True
    mp = re.search(r"pending[^0-9]*?(\d+)", text)
    mc = re.search(r"claimed[^0-9]*?(\d+)", text)
    if mp is None and mc is None:
        return True
    return (int(mp.group(1)) if mp else 0) > 0 or (int(mc.group(1)) if mc else 0) > 0
now = time.time()
a_mt, p_mt = mt(ACK), mt(PATROL)
a_age = None if a_mt is None else now - a_mt
p_age = None if p_mt is None else now - p_mt
bl = backlog(PATROL)
alert = (a_age is not None and a_age > 1800 and bl) or (p_age is not None and p_age > 300 and a_age is not None and a_age > 1800)
verdict = {
    "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
    "ack_age_s": a_age,
    "patrol_age_s": p_age,
    "backlog": bl,
    "verdict": "ALERT" if alert else "OK",
    "note": "Independent check only. ALERT means the next seat_revive cycle (already every 5 min) should trigger; this script does not run the runner, write ACK/patrol/receipt, or clear B.",
}
with open(OUT, "w") as f:
    json.dump(verdict, f, ensure_ascii=False, indent=2)
print(json.dumps(verdict, ensure_ascii=False))
