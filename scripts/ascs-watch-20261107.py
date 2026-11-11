#!/usr/bin/env python3
"""ASCS watch (2026-11-07, MEMORY.md ASCS盯守).

MEMORY claims ascs-30min-watch + renewal reminder 2026-11-24 exist, but
no script, no cron line and no state file existed on the hub -- this file
lands the rule as code. Ticket TKT-407168457, renewal 2026-11-29.
  * every run refreshes state/ascs-watch.json last_check (the 30-min watch
    is the cron that runs this script; staleness = watch not running).
  * on/after 2026-11-24 (reminder date) and <=5 days to renewal: write one
    outbox reminder (once per day), exit 1 DUE-SOON.
  * renewal date passed with ticket still OPEN -> FAIL (exit 1).
State file is the only production-adjacent write (plus outbox reminders);
no ticket API is called -- status comes from the state file, updated by
the operator/cron from the real ticket. Missing state -> SKIP (exit 2).
Exit: 0 PASS, 1 DUE/FAIL, 2 SKIP.
"""
import argparse, json, os, sys
from datetime import date, datetime, timezone

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
STATE_FILE = os.path.join(BASE, "state", "ascs-watch.json")
OUTBOX = os.path.join(BASE, "state", "outbox")
TICKET = "TKT-407168457"
RENEWAL = date(2026, 11, 29)
REMINDER = date(2026, 11, 24)


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check(today, state):
    """Returns (code, messages, actions). code 0/1/2."""
    if state is None:
        return 2, ["SKIP no state file (run --init first)"], []
    if state.get("ticket") != TICKET:
        return 2, ["SKIP state ticket %r != %s" % (state.get("ticket"), TICKET)], []
    days = (RENEWAL - today).days
    msgs, acts = [], []
    if state.get("status") == "CLOSED":
        return 0, ["PASS ticket CLOSED, renewal %s" % RENEWAL], []
    if days < 0:
        return 1, ["FAIL renewal %s passed (%d days ago), ticket still %s"
                   % (RENEWAL, -days, state.get("status"))], ["outbox"]
    if today >= REMINDER:
        key = "reminder_%s" % today.isoformat()
        if key not in state.get("reminders_sent", []):
            acts.append("outbox")
            msgs.append("DUE-SOON renewal in %d day(s) (%s); reminder -> outbox"
                        % (days, RENEWAL))
            return 1, msgs, acts
        msgs.append("reminder already sent today, renewal in %d day(s)" % days)
        return 1, msgs, []
    return 0, ["PASS renewal in %d day(s) (%s), reminder from %s"
               % (days, RENEWAL, REMINDER)], []


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    st = {"ticket": TICKET, "status": "OPEN", "reminders_sent": []}
    code, msgs, acts = check(date(2026, 11, 7), st)
    ck(code == 0, "2026-11-07 far from renewal -> PASS")
    code, msgs, acts = check(date(2026, 11, 24), dict(st))
    ck(code == 1 and acts == ["outbox"], "2026-11-24 reminder date -> DUE-SOON + outbox")
    st2 = dict(st, reminders_sent=["reminder_2026-11-24"])
    code, _, acts = check(date(2026, 11, 24), st2)
    ck(code == 1 and acts == [], "reminder already sent today -> no duplicate outbox")
    code, _, _ = check(date(2026, 11, 30), dict(st))
    ck(code == 1, "past renewal with OPEN ticket -> FAIL")
    code, _, _ = check(date(2026, 11, 7), dict(st, status="CLOSED"))
    ck(code == 0, "CLOSED ticket -> PASS")
    code, _, _ = check(date(2026, 11, 7), None)
    ck(code == 2, "missing state -> SKIP")
    code, _, _ = check(date(2026, 11, 7), {"ticket": "OTHER"})
    ck(code == 2, "wrong ticket in state -> SKIP")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "ascs-watch.json")
        with open(p, "w") as f:
            json.dump(st, f)
        ck(load_state(p)["ticket"] == TICKET, "state file round-trips")
        ck(load_state(os.path.join(td, "nope.json")) is None, "missing file -> None")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--init", action="store_true", help="create state file if missing")
    ap.add_argument("--today", help="YYYY-MM-DD (tests only)")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--outbox", default=OUTBOX)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    today = date.fromisoformat(args.today) if args.today else date.today()
    state = load_state(args.state)
    if state is None and args.init:
        state = {"ticket": TICKET, "status": "OPEN", "renewal_date": str(RENEWAL),
                 "reminder_date": str(REMINDER), "reminders_sent": [],
                 "created": datetime.now(timezone.utc).isoformat()}
        os.makedirs(os.path.dirname(args.state), exist_ok=True)
        with open(args.state, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print("INIT state file created at %s" % args.state)
    code, msgs, acts = check(today, state)
    for m in msgs:
        print(m)
    if "outbox" in acts and state is not None:
        os.makedirs(args.outbox, exist_ok=True)
        fn = os.path.join(args.outbox, "ascs-renewal-reminder-%s.json" % today.isoformat())
        with open(fn, "w") as f:
            json.dump({"kind": "ascs-renewal-reminder", "ticket": TICKET,
                       "renewal_date": str(RENEWAL),
                       "days_left": (RENEWAL - today).days}, f, ensure_ascii=False, indent=2)
        state.setdefault("reminders_sent", []).append("reminder_%s" % today.isoformat())
        print("outbox: %s" % fn)
    if state is not None:
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        with open(args.state, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
