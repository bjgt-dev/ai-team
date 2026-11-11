#!/usr/bin/env python3
"""Intel terminal-state guard (2026-11-07, MEMORY.md 情报扫须走到采用/拒绝终态并入库).

Rule (memory-only until now): every intel registry entry must end in a
terminal state -- adopted / verifying-with-deadline / rejected-with-reason.
Morning reports count adoptions, not raw sweep counts. "Only registered,
never landed" is a violation.
Registry: state/intel-adoption-registry.jsonl, one JSON object per line:
  {id, source, status, deadline?, reason?, skill_file?, evidence?}
  * adopted  -> needs skill_file + evidence (skill file + test evidence
    + registry entry = the three入库 items).
  * verifying -> needs a deadline (<=7 days out); past deadline -> FAIL.
  * rejected -> needs a reason.
  * anything else -> FAIL (no terminal state).
Missing registry -> SKIP (exit 2), never a vacuous PASS.
Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, json, os, sys
from datetime import date, timedelta

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
REGISTRY = os.path.join(BASE, "state", "intel-adoption-registry.jsonl")
VERIFY_MAX_DAYS = 7


def load(path):
    try:
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
    except OSError:
        return None
    except ValueError as e:
        return {"error": str(e)}


def check(entries, today):
    problems = []
    if isinstance(entries, dict):
        return ["registry is not valid JSONL: %s" % entries.get("error")]
    for e in entries:
        eid = e.get("id", "?")
        st = e.get("status")
        if st == "adopted":
            if not e.get("skill_file"):
                problems.append("%s adopted without skill_file" % eid)
            if not e.get("evidence"):
                problems.append("%s adopted without evidence" % eid)
        elif st == "verifying":
            dl = e.get("deadline")
            if not dl:
                problems.append("%s verifying without deadline" % eid)
            else:
                try:
                    d = date.fromisoformat(dl)
                    if d < today:
                        problems.append("%s verifying deadline %s passed" % (eid, dl))
                    elif (d - today).days > VERIFY_MAX_DAYS:
                        problems.append("%s verifying deadline %s > %d days out"
                                        % (eid, dl, VERIFY_MAX_DAYS))
                except ValueError:
                    problems.append("%s bad deadline %r" % (eid, dl))
        elif st == "rejected":
            if not str(e.get("reason", "")).strip():
                problems.append("%s rejected without reason" % eid)
        else:
            problems.append("%s has no terminal state (status=%r)" % (eid, st))
    return problems


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    today = date(2026, 11, 7)
    good = [{"id": "a", "status": "adopted", "skill_file": "s.md", "evidence": "t.log"},
            {"id": "b", "status": "verifying", "deadline": "2026-11-10"},
            {"id": "c", "status": "rejected", "reason": "42 stars, too small"}]
    ck(check(good, today) == [], "adopted+verifying+rejected all well-formed -> PASS")
    ck(any("without skill_file" in p for p in
           check([{"id": "a", "status": "adopted", "evidence": "t"}], today)),
       "adopted without skill_file -> FAIL")
    ck(any("deadline" in p for p in
           check([{"id": "b", "status": "verifying"}], today)),
       "verifying without deadline -> FAIL")
    ck(any("passed" in p for p in
           check([{"id": "b", "status": "verifying", "deadline": "2026-11-06"}], today)),
       "verifying past deadline -> FAIL")
    ck(any("without reason" in p for p in
           check([{"id": "c", "status": "rejected"}], today)),
       "rejected without reason -> FAIL")
    ck(any("no terminal state" in p for p in
           check([{"id": "d", "status": "seen"}], today)),
       "bare 'seen' entry has no terminal state -> FAIL")
    ck(load("/nonexistent/x.jsonl") is None, "missing registry -> None (SKIP)")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--today", help="YYYY-MM-DD")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    today = date.fromisoformat(args.today) if args.today else date.today()
    entries = load(args.registry)
    if entries is None:
        print("SKIP no registry %s" % args.registry)
        return 2
    problems = check(entries, today)
    if problems:
        for p in problems:
            print("FAIL " + p)
        return 1
    print("PASS %d intel entries all in terminal states" % len(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
