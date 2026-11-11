#!/usr/bin/env python3
"""Preflight gate (2026-11-07, MEMORY.md 开工前强制预检门 + 机制流程).

Two memory-only gates landed as code:
  * preflight 4 questions (before any new work): what was searched,
    what was found (name + real star count + license), build-or-reuse,
    verify/acceptance plan. Low-star (<1000) counts as "does not exist".
  * central-queue 3 questions (before creating a queue task): not bound
    to one person; not waiting on an unreachable session; executor
    checks credentials first.
Ledger: state/preflight-ledger.jsonl (JSONL, one entry per task).
  --record TASK --q1 .. --q4 .. [--queue3] appends an entry.
  --check TASK verifies the 4 answers are present and non-empty.
  --check-queue3 TASK verifies the 3 queue answers are all true.
Exit 0 PASS, 1 FAIL, 2 SKIP (no ledger / no entry).
"""
import argparse, json, os, sys
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
LEDGER = os.path.join(BASE, "state", "preflight-ledger.jsonl")
FOUR = ("q1_searched", "q2_found", "q3_build_or_reuse", "q4_verify_plan")
THREE = ("not_bound_to_person", "not_waiting_unreachable", "executor_checked_creds")


def load_entries(path):
    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return None
    return entries


def find(entries, task):
    for e in reversed(entries or []):
        if e.get("task") == task:
            return e
    return None


def check_four(entry):
    if entry is None:
        return ["no preflight entry for this task"]
    return ["%s missing/empty" % q for q in FOUR if not str(entry.get(q, "")).strip()]


def check_queue3(entry):
    if entry is None:
        return ["no preflight entry for this task"]
    q3 = entry.get("queue3") or {}
    return ["queue3 %s not true" % k for k in THREE if q3.get(k) is not True]


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        led = os.path.join(td, "ledger.jsonl")
        ck(load_entries(led) is None, "missing ledger -> None (SKIP)")
        ck(find([], "t") is None, "empty ledger -> no entry")
        good = {"task": "t1", "q1_searched": "github X", "q2_found": "repo 5k stars MIT",
                "q3_build_or_reuse": "reuse", "q4_verify_plan": "tests",
                "queue3": {"not_bound_to_person": True,
                           "not_waiting_unreachable": True,
                           "executor_checked_creds": True}}
        with open(led, "w") as f:
            f.write(json.dumps(good) + "\n")
        entries = load_entries(led)
        ck(check_four(find(entries, "t1")) == [], "complete 4 answers -> PASS")
        ck(check_queue3(find(entries, "t1")) == [], "queue3 all true -> PASS")
        ck(check_four(find(entries, "nope")) != [], "unknown task -> FAIL")
        bad = dict(good, q2_found="")
        with open(led, "a") as f:
            f.write(json.dumps(bad) + "\n")
        ck(check_four(find(entries + [bad], "t1")) != [],
           "newest entry with empty q2 -> FAIL (no stale pass)")
        bad3 = dict(good, queue3={"not_bound_to_person": True,
                                  "not_waiting_unreachable": False,
                                  "executor_checked_creds": True})
        ck(check_queue3(bad3) != [], "queue3 one false -> FAIL")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--task")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--check-queue3", action="store_true")
    ap.add_argument("--q1", default="")
    ap.add_argument("--q2", default="")
    ap.add_argument("--q3", default="")
    ap.add_argument("--q4", default="")
    ap.add_argument("--queue3", action="store_true",
                    help="assert all three queue questions are true")
    ap.add_argument("--ledger", default=LEDGER)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.task:
        print("usage: --task NAME with --record / --check / --check-queue3")
        return 2
    if args.record:
        entry = {"task": args.task, "ts": datetime.now(timezone.utc).isoformat(),
                 "q1_searched": args.q1, "q2_found": args.q2,
                 "q3_build_or_reuse": args.q3, "q4_verify_plan": args.q4}
        if args.queue3:
            entry["queue3"] = {k: True for k in THREE}
        os.makedirs(os.path.dirname(args.ledger), exist_ok=True)
        with open(args.ledger, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        missing = check_four(entry)
        if missing:
            print("RECORDED but incomplete: %s" % "; ".join(missing))
            return 1
        print("RECORDED preflight for %s" % args.task)
        return 0
    entries = load_entries(args.ledger)
    if entries is None:
        print("SKIP no ledger %s" % args.ledger)
        return 2
    entry = find(entries, args.task)
    problems = []
    if args.check:
        problems += check_four(entry)
    if args.check_queue3:
        problems += check_queue3(entry)
    if not args.check and not args.check_queue3:
        problems = check_four(entry) + check_queue3(entry)
    if problems:
        print("FAIL %s: %s" % (args.task, "; ".join(problems)))
        return 1
    print("PASS preflight gate for %s" % args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
