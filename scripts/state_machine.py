#!/usr/bin/env python3
"""六态状态机：pending -> claimed -> executing -> verified -> evaluated -> delivered | quarantined
规则：只许按顺序前进；超时（executing 超过 budget 秒）-> quarantined；重试超预算（>2 次）-> quarantined；
重试必须带 new_constraint，否则拒绝并进 quarantined；任何越序转移 -> 拒绝。
状态持久化到 JSON，含历史与重试计数。
"""
import json, sys, pathlib, time
STATES = ["pending","claimed","executing","verified","evaluated","delivered","quarantined"]
def load(p):
    return json.load(open(p)) if pathlib.Path(p).exists() else {"state":"pending","retries":0,"history":["pending"],"started":None}
def save(p, s):
    json.dump(s, open(p,"w"), ensure_ascii=False, indent=2)
def transition(state_file, target, new_constraint=None, now=None):
    s = load(state_file)
    cur = s["state"]
    now = now or time.time()
    if cur in ("delivered","quarantined"):
        print(f"REJECT: terminal state {cur} cannot move to {target}"); return 2
    if target == "quarantined":
        s["state"]="quarantined"; s["history"].append("quarantined"); save(state_file,s); print("QUARANTINED"); return 0
    order = STATES.index(cur); torder = STATES.index(target) if target in STATES else -1
    if torder != order + 1:
        print(f"REJECT: out-of-order {cur} -> {target}"); return 2
    if target == "executing":
        s["started"]=now
    if target == "claimed" and s.get("retries",0) > 0 and not new_constraint:
        print("REJECT: retry without new_constraint -> quarantined"); s["state"]="quarantined"; s["history"].append("quarantined"); save(state_file,s); return 2
    s["state"]=target; s["history"].append(target); save(state_file,s); print(f"OK {cur} -> {target}"); return 0
def retry(state_file, new_constraint=None):
    s = load(state_file)
    if s.get("retries",0) >= 2:
        print("REJECT: retry budget exceeded -> quarantined"); s["state"]="quarantined"; s["history"].append("quarantined"); save(state_file,s); return 2
    if not new_constraint:
        print("REJECT: retry without new_constraint"); return 2
    s["retries"]+=1; s["state"]="pending"; s["history"].append(f"retry#{s['retries']}:{new_constraint}"); save(state_file,s); print(f"RETRY allowed #{s['retries']} with constraint"); return 0
if __name__ == "__main__":
    cmd = sys.argv[1]; f = sys.argv[2]
    if cmd == "go":
        sys.exit(transition(f, sys.argv[3], sys.argv[4] if len(sys.argv)>4 else None))
    if cmd == "retry":
        sys.exit(retry(f, sys.argv[3] if len(sys.argv)>3 else None))
    if cmd == "timeout_check":
        s = load(f)
        if s["state"]=="executing" and s.get("started") and time.time()-s["started"]>float(sys.argv[3]):
            s["state"]="quarantined"; s["history"].append("quarantined_timeout"); save(f,s); print("QUARANTINED_TIMEOUT"); sys.exit(0)
        print("NOT_TIMEOUT state="+s["state"]); sys.exit(0)
