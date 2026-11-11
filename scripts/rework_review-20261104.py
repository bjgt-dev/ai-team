#!/usr/bin/env python3
"""Order14 rework + review mechanism (code + dry-run, no live task mutation).
rework: a NEW task carrying parent_task_id + verifier failure reason.
  - max 2 rework attempts per parent; 3rd failure -> DEAD + incident draft.
  - original task fence (current_fencing_token) is NEVER modified.
  - rework_count = number of existing tasks whose payload.parent_task_id
    equals the parent (read from live DB in --count mode, read-only).
review: task_type=review, different model/session from the executor,
  output JSON {verdict, rationale, checks}. Review output is ONE verifier
  input only: it can suggest RETRY/QUARANTINED, it can NEVER override a
  deterministic verifier failure (fencing/sha/bytes) to DONE.
Modes:
  --dry-run parent=<uuid> reason=<text> : simulate rework creation, print
    the would-be payload, do NOT insert into central_tasks.
  --count parent=<uuid> : read-only live count of existing rework children.
  --review-sample : handled by review_sample-20261104.py (daily 5% DONE).
  --test : built-in logic tests (max-2, dead, review-non-override).
"""
import argparse, json, subprocess, sys

def rework_decision(count, reason):
    if count >= 2:
        return {"action": "DEAD", "incident_draft": {
            "parent_task_id": None, "verifier_failure_reason": reason,
            "rework_attempts": count, "note": "rework exhausted (max 2); dead + incident, original fence untouched"}}
    return {"action": "CREATE_REWORK",
            "payload": {"type": "code", "parent_task_id": None,
                        "verifier_failure_reason": reason,
                        "rework_attempt": count + 1,
                        "note": "new task; original task fence untouched"}}

def review_merge(deterministic_pass, review_verdict):
    # Review can never turn a deterministic failure into DONE.
    if not deterministic_pass:
        return "NOT_DONE"
    if review_verdict == "PASS":
        return "PASS_WITH_REVIEW"
    return "REVIEW_SUGGESTS_RETRY"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("kv", nargs="*")
    a = ap.parse_args()
    kv = {}
    for item in a.kv:
        if "=" in item:
            k, v = item.split("=", 1); kv[k] = v
    if a.test:
        tests = []
        tests.append(("rework_0_create", rework_decision(0, "sha mismatch")["action"] == "CREATE_REWORK"))
        tests.append(("rework_1_create", rework_decision(1, "sha mismatch")["action"] == "CREATE_REWORK"))
        tests.append(("rework_2_dead", rework_decision(2, "sha mismatch")["action"] == "DEAD"))
        tests.append(("review_cannot_override_fail", review_merge(False, "PASS") == "NOT_DONE"))
        tests.append(("review_pass_with_det_pass", review_merge(True, "PASS") == "PASS_WITH_REVIEW"))
        tests.append(("review_retry_suggestion", review_merge(True, "FAIL") == "REVIEW_SUGGESTS_RETRY"))
        ok = all(v for _, v in tests)
        print(json.dumps({"ok": ok, "tests": tests}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if a.count:
        parent = kv.get("parent", "")
        r = subprocess.run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
                            "-d", "central_queue", "-t", "-A", "-c",
                            "SELECT count(*) FROM central_tasks WHERE payload->>'parent_task_id' = '" + parent + "';"],
                           capture_output=True, text=True, timeout=60)
        print(json.dumps({"parent": parent, "rework_children": r.stdout.strip(), "live_read_only": True}))
        return 0
    if a.dry_run:
        parent, reason = kv.get("parent", ""), kv.get("reason", "")
        # count is 0 in dry-run simulation unless --count was run separately;
        # caller must pass count=N explicitly to simulate the 3rd failure.
        count = int(kv.get("count", "0"))
        d = rework_decision(count, reason)
        if d["action"] == "CREATE_REWORK":
            d["payload"]["parent_task_id"] = parent
        else:
            d["incident_draft"]["parent_task_id"] = parent
        print(json.dumps({"dry_run": True, "parent": parent, "count": count, "decision": d}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"error": "specify --dry-run, --count, or --test"}))
    return 2

if __name__ == "__main__":
    sys.exit(main())
