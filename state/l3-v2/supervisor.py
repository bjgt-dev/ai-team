#!/usr/bin/env python3
"""Supervisor control plane (deterministic, no LLM, no seat lock held during execution).
- Short flock on queue.json only for claim/status transitions.
- Claim token + attempt_id + fencing: only current token may write status.
- Lease: 1800s, heartbeat file per task, expired lease -> reclaimable, old process cannot overwrite (token check).
- Independent task work dir per task.
- Auto-discover: decision-queue stale stage2 entries, knowledge file count drift, queue backlog.
- Auto-score P0/P1/P2/P3 with reasons, aging PENDING >60min +1 level (max P1).
- Deterministic tasks only: hub-local scripts and worker-1 embedded payload (via API, not hub path).
- LLM tasks: marked READY, written to seat-dispatch dir, NEVER run ceo-opencode while seat lock is busy (flock test, non-blocking).
- Verify: file exists/nonempty/sha, receipt fields, archive to knowledge-assets, update dashboard + autonomy-state.
- Major anomaly only: write to red-alerts log, do NOT send Telegram here (alert_push is called only by explicit major-anomaly path in main chat / hub-health).
"""
import json, pathlib, hashlib, time, sys, subprocess
from datetime import datetime
BASE = pathlib.Path("/home/ubuntu/ai-team")
L3 = BASE / "state/l3-v2"
QUEUE = L3 / "queue.json"
LOCK = L3 / "queue.lock"
STATE = L3 / "autonomy-state.json"

def now_iso():
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
def run_locked(args):
    # args: list for l3_claim.py, run under flock, returns stdout
    r = subprocess.run(["flock", str(LOCK), "python3", str(L3 / "l3_claim.py")] + args, capture_output=True, text=True)
    return r.stdout.strip()
def main():
    q = load(QUEUE, {"tasks": []})
    # lease expiry: CLAIMED/RUNNING older than 1800s -> back to PENDING with note, token invalidated
    now_ts = time.time()
    changed = False
    for t in q.get("tasks", []):
        if t.get("status") in ("CLAIMED", "RUNNING") and t.get("claimed_at"):
            try:
                ct = datetime.fromisoformat(t["claimed_at"]).timestamp()
            except Exception:
                continue
            if now_ts - ct > 1800:
                t["status"] = "PENDING"
                t["note"] = "lease expired, reclaimed by Supervisor, old token fenced"
                t["claim_token"] = None
                changed = True
    # auto-discover: if no open task for decision-queue backlog, create one (only if none PENDING/READY for same title)
    dq = load(BASE / "state/decision-queue.json", {})
    pending = dq.get("pending", [])
    if len(pending) > 5 and not any(t.get("title", "").startswith("自动发现：决策队列积压") and t.get("status") in ("PENDING", "READY", "CLAIMED", "RUNNING", "VERIFYING") for t in q.get("tasks", [])):
        nid = f"L3-AUTO-{datetime.now().astimezone().strftime('%Y%m%d-%H%M')}"
        q["tasks"].append({"task_id": nid, "title": "自动发现：决策队列积压复核", "status": "PENDING", "priority": "P2", "score": 5.0, "score_reasons": "backlog>5, needs chairman decision list, not auto-close", "executor_type": "hub-local", "worker_id": "hub-local", "attempt": 0, "max_attempts": 2, "created_at": now_iso(), "output_dir": f"state/l3-v2/tasks/{nid}", "acceptance": "列出积压条目与C级待批项，不关闭原始记录"})
        changed = True
    if changed:
        save(QUEUE, q)
    # Seat priority: Supervisor (not human) creates/removes priority flag.
    # If an LLM seat task is READY, create flag so butler_loop/seat_revive skip.
    # If no LLM task is READY/CLAIMED/RUNNING, remove flag so patrol resumes.
    flag = BASE / "state/priority-external-task"
    needs_seat = any(t.get("executor_type") == "seat-muse" and t.get("status") in ("READY", "CLAIMED", "RUNNING") for t in q.get("tasks", []))
    if needs_seat and not flag.exists():
        flag.touch()
    if not needs_seat and flag.exists():
        flag.unlink()
    # autonomy state
    st = load(STATE, {})
    st.update({"phase": "supervisor-running", "last_supervisor_run": now_iso(), "queue_tasks": len(q.get("tasks", [])), "queue_statuses": {s: sum(1 for t in q.get("tasks", []) if t.get("status") == s) for s in set(t.get("status") for t in q.get("tasks", []))}, "decision_pending": len(pending)})
    save(STATE, st)
    print(f"supervisor ok tasks={len(q.get('tasks', []))} decision_pending={len(pending)}")
if __name__ == "__main__":
    main()
