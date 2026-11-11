#!/usr/bin/env python3
"""l3_claim.py with fencing (batch-1 decision, 2026-11-02).

CLI: l3_claim.py <task_id> <worker_id> <new_status> [claim_token] [artifact] [note] [--legacy]

Rules (checked before any write; rejections never touch queue.json):
1. new_status == "CLAIMED": reject (exit 3) if the task already has a
   claim_token or its status is CLAIMED/RUNNING. Otherwise mint a new token
   with the existing sha256(task_id:attempt:ts)[:16] algorithm.
2. new_status in (RUNNING, DONE, FAILED, VERIFYING): argv[4] must equal the
   task's current claim_token, else reject (exit 4).
3. Legacy tasks (claim_token None): any push in rule 2 is rejected (exit 4)
   unless --legacy is given; --legacy only annotates (legacy_bypass=true,
   artifact/note) and never changes status.
4. Every rejection appends one JSON line to claim-audit.jsonl
   (task_id, old_token, given_token, caller_pid, ts, reason).
5. Writes are atomic (tmp + os.replace). Callers must hold flock around
   the invocation (supervisor.run_locked does; direct CLI users must wrap
   with flock themselves).

Statuses outside the governed set (e.g. PENDING/READY) keep the old
pass-through behaviour: no token is required. This preserves existing
flows that never went through CLAIM; only the five governed transitions
are fenced.

Test support: queue/audit paths may be overridden with L3_CLAIM_QUEUE /
L3_CLAIM_AUDIT env vars (used by sandbox tests). With env vars unset,
production paths are used unchanged.
"""
import hashlib
import json
import os
import pathlib
import sys
import time
from datetime import datetime

BASE = pathlib.Path("/home/ubuntu/ai-team")
QUEUE = pathlib.Path(os.environ.get(
    "L3_CLAIM_QUEUE", str(BASE / "state/l3-v2/queue.json")))
AUDIT = pathlib.Path(os.environ.get(
    "L3_CLAIM_AUDIT", str(BASE / "state/l3-v2/claim-audit.jsonl")))

GOVERNED_PUSH = ("RUNNING", "DONE", "FAILED", "VERIFYING")


def now_iso():
    return datetime.now().astimezone().isoformat()


def audit(task_id, old_token, given_token, reason):
    rec = {
        "task_id": task_id,
        "old_token": old_token,
        "given_token": given_token,
        "caller_pid": os.getpid(),
        "ts": now_iso(),
        "reason": reason,
    }
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main():
    if len(sys.argv) < 4:
        print("usage: l3_claim.py <task_id> <worker_id> <new_status> "
              "[claim_token] [artifact] [note] [--legacy]", file=sys.stderr)
        return 2
    task_id, worker_id, new_status = sys.argv[1], sys.argv[2], sys.argv[3]
    rest = sys.argv[4:]
    legacy = "--legacy" in rest
    rest = [a for a in rest if a != "--legacy"]
    token_given = rest[0] if rest else None
    artifact = rest[1] if len(rest) > 1 else None
    note = rest[2] if len(rest) > 2 else None

    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    task = None
    for t in q["tasks"]:
        if t["task_id"] == task_id:
            task = t
            break
    if task is None:
        print(f"task not found: {task_id}", file=sys.stderr)
        return 2

    cur_token = task.get("claim_token")
    cur_status = task.get("status")

    if new_status == "CLAIMED":
        if cur_token is not None or cur_status in ("CLAIMED", "RUNNING"):
            audit(task_id, cur_token, token_given, "duplicate_claim")
            print(f"rejected: duplicate claim for {task_id} "
                  f"(status={cur_status})", file=sys.stderr)
            return 3
        task["attempt"] = task.get("attempt", 0) + 1
        task["attempt_id"] = f"{task_id}-a{task['attempt']}"
        task["claim_token"] = hashlib.sha256(
            f"{task_id}:{task['attempt']}:{time.time()}".encode()
        ).hexdigest()[:16]
        task["claimed_by"] = worker_id
        task["claimed_at"] = now_iso()
        task["status"] = "CLAIMED"
    elif new_status in GOVERNED_PUSH:
        if cur_token is None:
            if not legacy:
                audit(task_id, None, token_given, "legacy_token_null")
                print(f"rejected: legacy task {task_id} has no claim_token; "
                      f"use --legacy to annotate only", file=sys.stderr)
                return 4
            # --legacy: annotate only, status untouched, no token minted.
            task["legacy_bypass"] = True
            if artifact is not None:
                task["artifact"] = artifact
            if note is not None:
                task["note"] = note
            atomic_write(QUEUE, json.dumps(q, ensure_ascii=False, indent=2))
            print(json.dumps(task, ensure_ascii=False))
            return 0
        if token_given is None or token_given != cur_token:
            audit(task_id, cur_token, token_given, "stale_token_rejected")
            print(f"rejected: stale token for {task_id}", file=sys.stderr)
            return 4
        task["status"] = new_status
    else:
        # Ungoverned status (e.g. PENDING/READY): old pass-through behaviour.
        task["status"] = new_status

    task["updated_at"] = now_iso()
    if artifact is not None:
        task["artifact"] = artifact
    if note is not None:
        task["note"] = note
    atomic_write(QUEUE, json.dumps(q, ensure_ascii=False, indent=2))
    print(json.dumps(task, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
