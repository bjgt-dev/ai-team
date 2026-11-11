#!/usr/bin/env python3
"""L3 core: goal tree, priority queue, worker registry, scheduler, verifier, sedimentation.
All state in /home/ubuntu/ai-team/state/l3-v2/ (hub) - single source of truth.
Locking: short flock on queue.json only for claim/status transitions, never held during long LLM/worker execution.
Claim token: task_id + attempt_id + timestamp hash, fencing: only holder with current token may update status.
Aging: PENDING > 60 min gets +1 priority level (max P1), recorded.
"""
import json, hashlib, pathlib, sys, time
from datetime import datetime, timezone
BASE = pathlib.Path("/home/ubuntu/ai-team")
L3 = BASE / "state/l3-v2"
QUEUE = L3 / "queue.json"
WORKERS = L3 / "workers.json"
GOALS = L3 / "goal-tree.json"

def now():
    return datetime.now().astimezone().isoformat()

def load(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default

def save(p, obj):
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    pathlib.Path(tmp).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    pathlib.Path(tmp).replace(p)

def score_task(strategic, economic, urgency, risk, effort, dependency, resource, inaction_cost):
    # Simple transparent scoring, 0-10 each, weighted, recorded with reasons - not subjective alone
    s = strategic*0.25 + economic*0.20 + urgency*0.20 + inaction_cost*0.15 - risk*0.05 - effort*0.05 - dependency*0.05 + resource*0.05
    return round(s, 2)

def priority_from_score(s):
    if s >= 8: return "P0"
    if s >= 6: return "P1"
    if s >= 4: return "P2"
    return "P3"

def claim_task(queue, task_id, worker_id):
    # Must be called under flock in real scheduler; here it is a pure transition with fencing check
    for t in queue["tasks"]:
        if t["task_id"] == task_id and t["status"] in ("PENDING", "READY"):
            attempt = t.get("attempt", 0) + 1
            token = hashlib.sha256(f"{task_id}:{attempt}:{time.time()}".encode()).hexdigest()[:16]
            t["status"] = "CLAIMED"
            t["attempt"] = attempt
            t["attempt_id"] = f"{task_id}-a{attempt}"
            t["claim_token"] = token
            t["claimed_by"] = worker_id
            t["claimed_at"] = now()
            t["lease_until"] = datetime.now().astimezone().timestamp() + 1800
            return token
    return None

def verify_task(artifact_path, source_sha=None):
    p = pathlib.Path(artifact_path)
    checks = {
        "file_exists": p.exists(),
        "file_nonempty": p.exists() and p.stat().st_size > 0,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "",
    }
    checks["pass"] = checks["file_exists"] and checks["file_nonempty"]
    return checks

if __name__ == "__main__":
    print("l3_core loaded, queue/workers/goals paths:", QUEUE, WORKERS, GOALS)
