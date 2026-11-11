#!/usr/bin/env python3
"""Retry loop."""
import json, os
from pathlib import Path
from datetime import datetime, timezone
TEAM_ROOT = Path(os.environ.get("TEAM_ROOT", "/home/ubuntu/ai-team"))
QUARANTINE = TEAM_ROOT / "quarantine"
PENDING_CUE = TEAM_ROOT / "contracts" / "pending" / "cue"
PENDING_MUSE = TEAM_ROOT / "contracts" / "pending" / "muse"
LOG = TEAM_ROOT / "logs" / "retry_loop.log"
MAX_RETRIES = 2
def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line)
    print(line.strip())
def get_retry_count(task_id):
    count = 0
    for pending_dir in [PENDING_CUE, PENDING_MUSE]:
        if not pending_dir.exists():
            continue
        for p in pending_dir.glob(f"{task_id}-retry-*.json"):
            count += 1
    return count
def extract_failure_reason(verdict_path):
    try:
        with open(verdict_path) as f:
            v = json.load(f)
    except Exception as e:
        return f"read error: {e}"
    reasons = []
    for fc in v.get("failed_criteria", []):
        if isinstance(fc, dict):
            crit = fc.get("criterion", {})
            reasons.append(f"FAIL [{crit.get('type')}]: {crit.get('target')}")
        else:
            reasons.append(str(fc))
    for re in v.get("receipt_errors", []):
        reasons.append(f"receipt: {re}")
    for r in v.get("reasons", []):
        reasons.append(f"review: {r}")
    if not reasons:
        reasons.append(f"verdict={v.get('verdict','?')}")
    return "; ".join(reasons)
def load_original_contract(task_id):
    canonical = TEAM_ROOT / "contracts" / f"{task_id}.json"
    if canonical.exists():
        with open(canonical) as f:
            return json.load(f)
    for pd in [PENDING_CUE, PENDING_MUSE, TEAM_ROOT / "contracts" / "pending"]:
        if not pd.exists():
            continue
        p = pd / f"{task_id}.json"
        if p.exists():
            with open(p) as f:
                return json.load(f)
    return None
def generate_fix_contract(task_id, failure_reason, retry_num, original):
    now = datetime.now(timezone.utc).isoformat()
    contract = {
        "task_id": f"{task_id}-retry-{retry_num}",
        "original_task_id": task_id,
        "retry_number": retry_num,
        "created_at": now,
        "objective": f"RETRY #{retry_num} fix. Original: {task_id}. Failure: {failure_reason}. Redo ensuring all criteria pass.",
        "acceptance_criteria": original.get("acceptance_criteria", []) if original else [],
        "executor": "cue",
        "max_retries_remaining": MAX_RETRIES - retry_num,
    }
    return contract
def process_quarantine():
    if not QUARANTINE.exists():
        return
    for task_dir in QUARANTINE.iterdir():
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id.startswith("orphan-"):
            continue
        if (task_dir / ".retry_processed").exists():
            continue
        verdict_path = task_dir / "verdict.json"
        if not verdict_path.exists():
            rp = task_dir / "review.json"
            if rp.exists():
                verdict_path = rp
            else:
                continue
        rc = get_retry_count(task_id)
        if rc >= MAX_RETRIES:
            log(f"ALERT {task_id}: {MAX_RETRIES} retries failed, need human")
            (task_dir / ".retry_processed").touch()
            continue
        fr = extract_failure_reason(verdict_path)
        orig = load_original_contract(task_id)
        rn = rc + 1
        contract = generate_fix_contract(task_id, fr, rn, orig)
        PENDING_CUE.mkdir(parents=True, exist_ok=True)
        cp = PENDING_CUE / f"{contract['task_id']}.json"
        with open(cp, "w") as f:
            json.dump(contract, f, indent=2, ensure_ascii=False)
        log(f"RETRY {task_id}: created {contract['task_id']} (#{rn}/{MAX_RETRIES})")
        (task_dir / ".retry_processed").touch()
if __name__ == "__main__":
    process_quarantine()
