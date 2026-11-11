#!/usr/bin/env python3
"""Commercial P0-3 guard (2026-11-07, MEMORY.md P0-3).

Rules landed as code (were memory-only):
  * C1 unfreeze needs >=3 independently verified items.
  * 7-day countdown starts at approval; past deadline -> FAIL.
  * 18:00 rule: if it is past 18:00 local and no commercial action happened
    today -> write one outbox note (once per day).
State: state/commercial-p03.json {approval_date, items_verified:[...],
last_action_date}. Missing state -> SKIP (exit 2), never a false PASS.
--now takes "YYYY-MM-DD HH:MM" (tests / dry runs). Exit 0/1/2.
"""
import argparse, json, os, sys
from datetime import date, datetime, timedelta

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
STATE_FILE = os.path.join(BASE, "state", "commercial-p03.json")
OUTBOX = os.path.join(BASE, "state", "outbox")
C1_MIN_ITEMS = 3
COUNTDOWN_DAYS = 7
REPORT_HOUR = 18


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check(now, state):
    """Returns (code, messages, actions)."""
    if state is None:
        return 2, ["SKIP no state file %s" % STATE_FILE], []
    msgs, acts = [], []
    items = state.get("items_verified") or []
    if len(items) < C1_MIN_ITEMS:
        return 1, ["FAIL C1 unfreeze needs >=%d verified items, have %d"
                   % (C1_MIN_ITEMS, len(items))], []
    ap = state.get("approval_date")
    if not ap:
        return 2, ["SKIP approval_date missing in state"], []
    approval = date.fromisoformat(ap)
    deadline = approval + timedelta(days=COUNTDOWN_DAYS)
    days_left = (deadline - now.date()).days
    if days_left < 0:
        return 1, ["FAIL 7-day countdown expired %d day(s) ago (deadline %s)"
                   % (-days_left, deadline)], []
    msgs.append("C1 ok (%d items); countdown %d day(s) left (deadline %s)"
                % (len(items), days_left, deadline))
    if now.hour >= REPORT_HOUR and state.get("last_action_date") != now.date().isoformat():
        key = "noaction_%s" % now.date().isoformat()
        if key not in state.get("notices_sent", []):
            acts.append("outbox")
            msgs.append("18:00 passed with no commercial action today -> outbox")
            return 1, msgs, acts
    return 0, msgs, []


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    st = {"approval_date": "2026-11-04",
          "items_verified": ["g2-landing", "outreach-template", "delivery-checklist"],
          "last_action_date": "2026-11-07"}
    code, msgs, _ = check(datetime(2026, 11, 7, 10, 0), dict(st))
    ck(code == 0, "3 items + within countdown + action today -> PASS")
    code, _, _ = check(datetime(2026, 11, 7, 10, 0),
                       dict(st, items_verified=["g2-landing"]))
    ck(code == 1, "only 1 verified item -> C1 FAIL")
    code, _, _ = check(datetime(2026, 11, 12, 10, 0), dict(st))
    ck(code == 1, "past 7-day deadline -> FAIL")
    code, msgs, acts = check(datetime(2026, 11, 7, 18, 30),
                             dict(st, last_action_date="2026-11-06"))
    ck(code == 1 and acts == ["outbox"], "18:30 no action today -> outbox")
    code, msgs, acts = check(datetime(2026, 11, 7, 18, 30),
                             dict(st, last_action_date="2026-11-06",
                                  notices_sent=["noaction_2026-11-07"]))
    ck(code == 0 and acts == [], "notice already sent today -> no duplicate")
    code, _, _ = check(datetime(2026, 11, 7, 10, 0), None)
    ck(code == 2, "missing state -> SKIP")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--now", help="YYYY-MM-DD HH:MM")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--outbox", default=OUTBOX)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now()
    state = load_state(args.state)
    code, msgs, acts = check(now, state)
    for m in msgs:
        print(m)
    if "outbox" in acts and state is not None:
        os.makedirs(args.outbox, exist_ok=True)
        fn = os.path.join(args.outbox, "commercial-noaction-%s.json" % now.date().isoformat())
        with open(fn, "w") as f:
            json.dump({"kind": "commercial-noaction-1800",
                       "date": now.date().isoformat(),
                       "rule": "18:00 with no commercial action -> report"}, f,
                      ensure_ascii=False, indent=2)
        state.setdefault("notices_sent", []).append("noaction_%s" % now.date().isoformat())
        with open(args.state, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print("outbox: %s" % fn)
    return code


if __name__ == "__main__":
    sys.exit(main())
