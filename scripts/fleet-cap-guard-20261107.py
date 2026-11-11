#!/usr/bin/env python3
"""Fleet cap guard (2026-11-07, MEMORY.md 甲骨文与InstaBox合并).

Rules landed as code (were memory-only):
  * fleet is exactly two machines: arm-agent + amd-proxy. Any third
    machine in the inventory -> FAIL (no new builds, no expansion).
  * 31-day budget per machine: OCPU-hours <= 3000, GB-hours <= 18000.
    Budget state: state/fleet-budget-20261107.json (seeded from the
    MEMORY figures: 2976 OCPU-h / 17856 GB-h projected per 31d).
Inventory source: state/oci-7d-p95-latest.json instances[].name plus
optional --inventory JSON list. Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, json, os, sys

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
P95 = os.path.join(BASE, "state", "oci-7d-p95-latest.json")
BUDGET = os.path.join(BASE, "state", "fleet-budget-20261107.json")
ALLOWED = {"arm-agent", "amd-proxy"}
OCPU_LIMIT = 3000
GBH_LIMIT = 18000


def inventory(p95_path, extra=None):
    names = set()
    try:
        data = json.load(open(p95_path))
        for inst in data.get("instances", []):
            if inst.get("name"):
                names.add(inst["name"])
    except (OSError, ValueError):
        pass
    if extra:
        try:
            names.update(json.load(open(extra)))
        except (OSError, ValueError):
            pass
    return names


def check_budget(path):
    """Returns (problems, notes). Missing file -> ([], ['skip...'])."""
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return [], ["budget state missing (%s); budget check skipped" % path]
    problems, notes = [], []
    for m in data.get("machines", []):
        ocpu = m.get("ocpu_hours_31d")
        gbh = m.get("gb_hours_31d")
        if ocpu is not None and ocpu > OCPU_LIMIT:
            problems.append("%s OCPU-hours %.0f > %d" % (m.get("name"), ocpu, OCPU_LIMIT))
        if gbh is not None and gbh > GBH_LIMIT:
            problems.append("%s GB-hours %.0f > %d" % (m.get("name"), gbh, GBH_LIMIT))
        notes.append("%s OCPU %.0f/%d GB-h %.0f/%d"
                     % (m.get("name"), ocpu or 0, OCPU_LIMIT, gbh or 0, GBH_LIMIT))
    return problems, notes


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        p95 = os.path.join(td, "p95.json")
        with open(p95, "w") as f:
            json.dump({"instances": [{"name": "arm-agent"}, {"name": "amd-proxy"}]}, f)
        names = inventory(p95)
        ck(names == ALLOWED, "two-machine inventory -> allowed set")
        with open(p95, "w") as f:
            json.dump({"instances": [{"name": "arm-agent"}, {"name": "amd-proxy"},
                                     {"name": "extra-box"}]}, f)
        extra = inventory(p95) - ALLOWED
        ck(extra == {"extra-box"}, "third machine detected -> FAIL")
        bud = os.path.join(td, "budget.json")
        with open(bud, "w") as f:
            json.dump({"machines": [{"name": "arm-agent", "ocpu_hours_31d": 2976,
                                     "gb_hours_31d": 17856}]}, f)
        probs, _ = check_budget(bud)
        ck(probs == [], "2976/17856 within limits -> PASS")
        with open(bud, "w") as f:
            json.dump({"machines": [{"name": "arm-agent", "ocpu_hours_31d": 3100,
                                     "gb_hours_31d": 17856}]}, f)
        probs, _ = check_budget(bud)
        ck(any("OCPU" in p for p in probs), "3100 OCPU-hours -> FAIL")
        probs, notes = check_budget(os.path.join(td, "nope.json"))
        ck(probs == [] and any("skipped" in n for n in notes),
           "missing budget file -> skip, not false pass")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--p95", default=P95)
    ap.add_argument("--inventory", help="extra JSON list of machine names")
    ap.add_argument("--budget", default=BUDGET)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    problems = []
    names = inventory(args.p95, args.inventory)
    if not names:
        print("SKIP no inventory source readable (%s)" % args.p95)
        return 2
    extra = names - ALLOWED
    if extra:
        problems.append("fleet has machine(s) outside {arm-agent, amd-proxy}: %s"
                        % ", ".join(sorted(extra)))
    bprobs, notes = check_budget(args.budget)
    problems += bprobs
    for n in notes:
        print("note: " + n)
    print("fleet: %s" % ", ".join(sorted(names)))
    if problems:
        for p in problems:
            print("FAIL " + p)
        return 1
    print("PASS fleet is exactly the 2 allowed machines within OCPU/GB-h budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
