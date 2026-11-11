#!/usr/bin/env python3
# Requeue-on-REJECT (2026-10-20 supervisor item 6): when verify.py returns a
# terminal REJECT, send the contract back to pending for the ORIGINAL worker
# (from the bundle manifest worker_id), bounded by the contract's max_retries
# budget (tracked in the contract's attempts field). NEED_HUMAN/PASS never
# come here. Legacy bundles without worker_id requeue without preference.
# Usage: requeue_rejected.py <task_id>
import json, os, sys

ROOT = "/home/ubuntu/ai-team"
task_id = sys.argv[1]
canonical = os.path.join(ROOT, "contracts", task_id + ".json")
try:
    c = json.load(open(canonical))
except Exception as e:
    print("REQUEUE_SKIP %s no canonical contract (%s)" % (task_id, e)); sys.exit(0)

attempts = int(c.get("attempts", 0))
max_retries = int(c.get("max_retries", 2))
if attempts >= max_retries:
    print("REQUEUE_CAP %s attempts=%d max_retries=%d" % (task_id, attempts, max_retries)); sys.exit(0)

worker = ""
for cand in (os.path.join(ROOT, "quarantine", task_id, "bundle", "manifest.json"),
             os.path.join(ROOT, "inbox", task_id, "manifest.json")):
    try:
        worker = json.load(open(cand)).get("worker_id", "") or ""
        if worker:
            break
    except Exception:
        continue

c["attempts"] = attempts + 1
if worker:
    c["preferred_worker"] = worker
else:
    c.pop("preferred_worker", None)

with open(canonical, "w") as f:
    json.dump(c, f, ensure_ascii=False, indent=1)
pending = os.path.join(ROOT, "contracts", "pending", "muse", task_id + ".json")
with open(pending, "w") as f:
    json.dump(c, f, ensure_ascii=False, indent=1)
print("REQUEUED %s attempts=%d preferred_worker=%s" % (task_id, attempts + 1, worker or "(none)"))
