#!/usr/bin/env python3
"""code-review-gate-20261107.py -- code-dev-sop §三.6/§五-2 评审接进流水线.

SOP gap (2026-11-06, still open): "评审未接进流水线 -- code_review 在令19
链里有名, 但 formal pipeline 的八步表里没有评审这一步; 现在评审靠人工
触发, 不走自动门. 补法: 把 §三(6) 接成生成后、验证前的硬门, 评审不通过
不进 VERIFYING."

This script is the gate itself (read-only check, no DB writes):
  given a task_id, it looks for the two-axis review record and requires
  BOTH axes PASS with no hard violation:
    * Standards axis: state/code-review-20261107/review-<task>-standards.json
    * Spec axis:      state/code-review-20261107/review-<task>-spec.json
    (legacy location state/m5-20261105/review/review-<task>.json is also
    accepted when it carries {"axes": {"standards": "PASS", "spec": "PASS"}})
  A record with verdict FAIL / any hard violation / missing axis -> exit 1
  (REVIEW-BLOCKED, with reasons). No record at all -> exit 1 (NO-REVIEW).
  Both axes PASS -> exit 0 (REVIEW-PASS).

Wiring note (deliberate): this gate is NOT yet wired into the live
submit/verify path in this change. The transition to VERIFYING happens in
queue_api on worker submit, and blocking DONE in the verifier for tasks
already in flight without review records would stall them. Wiring point
is queue_api submit (refuse VERIFYING without REVIEW-PASS) -- that needs
a butler decision + a canary run, recorded in the scan receipt. This
script lands the check so the wiring is a one-line call.
Exit: 0 PASS / 1 BLOCKED or NO-REVIEW / 2 tool error.
"""
import argparse
import json
import os
import sys

REVIEW_DIR = "/home/ubuntu/ai-team/state/code-review-20261107"
LEGACY_DIR = "/home/ubuntu/ai-team/state/m5-20261105/review"


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(task_id, review_dir=None, legacy_dir=None):
    review_dir = review_dir or REVIEW_DIR
    legacy_dir = legacy_dir or LEGACY_DIR
    axes = {}
    problems = []
    for axis in ("standards", "spec"):
        path = os.path.join(review_dir, "review-%s-%s.json" % (task_id, axis))
        if os.path.exists(path):
            try:
                rec = _load_json(path)
            except Exception as exc:
                problems.append("%s: unreadable: %s" % (axis, exc))
                continue
            axes[axis] = rec
            continue
        legacy = os.path.join(legacy_dir, "review-%s.json" % task_id)
        if os.path.exists(legacy):
            try:
                rec = _load_json(legacy)
            except Exception as exc:
                problems.append("%s: legacy unreadable: %s" % (axis, exc))
                continue
            sub = (rec.get("axes") or {}).get(axis)
            axes[axis] = {"verdict": sub, "reasons": rec.get("reasons", [])}
    for axis in ("standards", "spec"):
        if axis not in axes:
            problems.append("%s: no review record" % axis)
            continue
        rec = axes[axis]
        verdict = rec.get("verdict")
        if verdict != "PASS":
            problems.append("%s: verdict=%s reasons=%s" % (
                axis, verdict, "; ".join(map(str, rec.get("reasons") or rec.get("violations") or [])) or "n/a"))
        for v in (rec.get("violations") or []):
            if isinstance(v, dict) and v.get("hard"):
                problems.append("%s: hard violation: %s" % (axis, v))
            elif isinstance(v, str):
                problems.append("%s: violation: %s" % (axis, v))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="code review gate (both axes must PASS)")
    ap.add_argument("task_id")
    ap.add_argument("--review-dir", default=None)
    ap.add_argument("--legacy-dir", default=None)
    args = ap.parse_args(argv)
    if not args.task_id.strip():
        print("TOOL-ERROR: empty task_id")
        return 2
    problems = check(args.task_id.strip(), args.review_dir, args.legacy_dir)
    if problems:
        print("REVIEW-BLOCKED %s:" % args.task_id)
        for p in problems:
            print("  - %s" % p)
        return 1
    print("REVIEW-PASS %s (standards + spec both PASS)" % args.task_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
