#!/usr/bin/env python3
"""U-18 tests (2026-11-10): Q1 mtime + canary freshness, reporter 变色才报.

Run on hub: python3 scripts/test-u18-health3q-20261110.py
Exit 0 = all pass. Uses only temp files except importing the real modules.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

BASE = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(BASE, "scripts")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    h3q = load("health_3q", os.path.join(SCRIPTS, "health_3q-20261104.py"))
    rep = load("health_3q_reporter", os.path.join(SCRIPTS, "health_3q_reporter.py"))
    fails = []

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)

    tmp = tempfile.mkdtemp(prefix="u18test-")
    # --- pool mtime: 20h-old file must fail, fresh file must pass ---
    old_pool = os.path.join(tmp, "pool-old.json")
    with open(old_pool, "w") as f:
        json.dump({"tier_a": ["a", "b"], "tier_b": []}, f)
    os.utime(old_pool, (time.time() - 20 * 3600, time.time() - 20 * 3600))
    age = h3q.pool_mtime_age_s(old_pool)
    check("old pool (20h) age>7200: age=%s" % (None if age is None else round(age)), age is not None and age > 7200)
    check("old pool pool_fresh=False", h3q.pool_fresh(old_pool) is False)
    fresh_pool = os.path.join(tmp, "pool-fresh.json")
    with open(fresh_pool, "w") as f:
        json.dump({"tier_a": ["a", "b"], "tier_b": []}, f)
    check("fresh pool pool_fresh=True", h3q.pool_fresh(fresh_pool) is True)
    check("missing pool pool_fresh=False", h3q.pool_fresh(os.path.join(tmp, "nope.json")) is False)

    # --- canary freshness: latest pass3 within 30min passes, 20h-old fails ---
    from datetime import datetime, timedelta, timezone
    cst = timezone(timedelta(hours=8))
    now = datetime.now(cst)
    old_cj = os.path.join(tmp, "canary-old.jsonl")
    with open(old_cj, "w") as f:
        f.write(json.dumps({"ts": (now - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": "glm-4-flash-cc", "pass3": True}) + "\n")
    check("20h-old canary canary_fresh=False", h3q.canary_fresh(old_cj) is False)
    fresh_cj = os.path.join(tmp, "canary-fresh.jsonl")
    with open(fresh_cj, "w") as f:
        f.write(json.dumps({"ts": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": "glm-4-flash-cc", "pass3": True}) + "\n")
        f.write(json.dumps({"ts": (now - timedelta(minutes=4)).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": "qwen3.8-flash", "pass3": True}) + "\n")
    check("fresh canary canary_fresh=True", h3q.canary_fresh(fresh_cj) is True)
    stale_cj = os.path.join(tmp, "canary-stale.jsonl")
    with open(stale_cj, "w") as f:
        f.write(json.dumps({"ts": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": "glm-4-flash-cc", "pass3": False}) + "\n")
    check("recent but pass3=False canary_fresh=False", h3q.canary_fresh(stale_cj) is False)

    # --- reporter: 变色才报 ---
    rep.LAST = os.path.join(tmp, "last_exit.json")
    rep.OUTBOX = os.path.join(tmp, "outbox")
    detail = {"q1_usable": True, "q2_working": True, "q3_correct": True,
              "q1": [["pool_mtime_fresh", True]], "q2": [], "q3": [], "notes": []}
    a1, _ = rep.maybe_alert(0, detail)
    check("reporter first run exit=0 silent (baseline)", a1 is False)
    a2, _ = rep.maybe_alert(0, detail)
    check("reporter same exit=0 again silent", a2 is False)
    a3, _ = rep.maybe_alert(3, dict(detail, q1_usable=False))
    files1 = os.listdir(rep.OUTBOX) if os.path.exists(rep.OUTBOX) else []
    check("reporter exit 0->3 writes exactly 1 outbox file", a3 is True and len(files1) == 1)
    a4, _ = rep.maybe_alert(3, dict(detail, q1_usable=False))
    files2 = os.listdir(rep.OUTBOX)
    check("reporter same exit=3 again silent (no new file)", a4 is False and len(files2) == 1)
    a5, _ = rep.maybe_alert(0, detail)
    files3 = os.listdir(rep.OUTBOX)
    check("reporter exit 3->0 writes 2nd outbox file", a5 is True and len(files3) == 2)

    print("U18-TEST %s (%d failures)" % ("PASS" if not fails else "FAIL", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
