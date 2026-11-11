#!/usr/bin/env python3
"""Order14 daily 5% DONE independent review sampler (audit record only).
- Selects ceil(5%) of DONE tasks verified in the last 24h, seeded by date
  so the sample is reproducible for the day.
- Writes state/review-sample-YYYYMMDD.json with task_ids, model placeholder
  (different model/session required at execution time), and a JSON verdict
  field per task left as PENDING_REVIEW until a different model actually
  runs it. This script NEVER marks a task DONE/FAILED by itself.
- Review output is one verifier input only; it cannot override a
  deterministic verifier failure (see rework_review-20261104.py).
- --dry-run prints the sample without writing the file.
"""
import argparse, json, math, os, random, subprocess, sys, time
STATE = "/home/ubuntu/ai-team/state"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = subprocess.run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
                        "-d", "central_queue", "-t", "-A",
                        "-c", "SELECT task_id FROM central_tasks WHERE status='DONE' AND verified_at > now() - interval '24 hours' ORDER BY verified_at;"],
                       capture_output=True, text=True, timeout=60)
    ids = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    n = math.ceil(len(ids) * 0.05) if ids else 0
    seed = time.strftime("%Y%m%d")
    rnd = random.Random(seed)
    sample = rnd.sample(ids, n) if n and len(ids) >= n else ids
    doc = {"date": seed, "done_24h": len(ids), "sample_n": len(sample),
           "sample_task_ids": sample,
           "review_model_rule": "different model/session from executor; JSON verdict per task",
           "verdict_status": "PENDING_REVIEW",
           "non_override_note": "review cannot override deterministic verifier failure to DONE"}
    if a.dry_run:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0
    path = os.path.join(STATE, "review-sample-" + seed + ".json")
    json.dump(doc, open(path, "w"), ensure_ascii=False, indent=2)
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
