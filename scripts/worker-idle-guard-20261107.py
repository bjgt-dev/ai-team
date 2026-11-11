#!/usr/bin/env python3
"""Worker idle guard (2026-11-07, MEMORY.md 工人按需唤醒).

Rules landed as code (were memory-only; wake_scheduler has no 30/480
constants in code): InstaBox workers are pull-only, never resident.
  * idle > 30 min -> PAUSE-DUE (decision written to outbox; this guard
    never pauses a machine itself).
  * runtime > 480 min -> STOP-DUE.
  * a single pull run > 20 min -> FLAG (pull_once must stay <= 20 min).
State: state/worker-idle-state.json {machines: [{name, last_active_iso,
started_iso, pull_started_iso}]}. Missing state -> SKIP (exit 2).
--now ISO timestamp for tests. Exit 0 PASS, 1 DUE/FLAG, 2 SKIP.
"""
import argparse, json, os, sys
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
STATE_FILE = os.path.join(BASE, "state", "worker-idle-state.json")
OUTBOX = os.path.join(BASE, "state", "outbox")
IDLE_PAUSE_MIN = 30
MAX_RUNTIME_MIN = 480
PULL_MAX_MIN = 20


def _parse(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def check(machines, now):
    """Returns (due_list, flag_list) of (name, reason)."""
    due, flags = [], []
    for m in machines:
        name = m.get("name", "?")
        last = _parse(m.get("last_active_iso"))
        if last is not None and (now - last).total_seconds() > IDLE_PAUSE_MIN * 60:
            due.append((name, "idle %.0f min > %d -> PAUSE-DUE"
                        % ((now - last).total_seconds() / 60, IDLE_PAUSE_MIN)))
        start = _parse(m.get("started_iso"))
        if start is not None and (now - start).total_seconds() > MAX_RUNTIME_MIN * 60:
            due.append((name, "runtime %.0f min > %d -> STOP-DUE"
                        % ((now - start).total_seconds() / 60, MAX_RUNTIME_MIN)))
        pull = _parse(m.get("pull_started_iso"))
        if pull is not None and (now - pull).total_seconds() > PULL_MAX_MIN * 60:
            flags.append((name, "pull running %.0f min > %d"
                          % ((now - pull).total_seconds() / 60, PULL_MAX_MIN)))
    return due, flags


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    active = [{"name": "w1", "last_active_iso": "2026-11-07T11:50:00+00:00",
               "started_iso": "2026-11-07T10:00:00+00:00",
               "pull_started_iso": "2026-11-07T11:55:00+00:00"}]
    due, flags = check(active, now)
    ck(due == [] and flags == [], "active worker within all limits -> PASS")
    idle = [{"name": "w2", "last_active_iso": "2026-11-07T11:00:00+00:00",
             "started_iso": "2026-11-07T11:00:00+00:00"}]
    due, _ = check(idle, now)
    ck(len(due) == 1 and "PAUSE-DUE" in due[0][1], "idle 60min -> PAUSE-DUE")
    longrun = [{"name": "w3", "last_active_iso": "2026-11-07T11:59:00+00:00",
                "started_iso": "2026-11-07T03:00:00+00:00"}]
    due, _ = check(longrun, now)
    ck(len(due) == 1 and "STOP-DUE" in due[0][1], "runtime 540min -> STOP-DUE")
    longpull = [{"name": "w4", "last_active_iso": "2026-11-07T11:59:00+00:00",
                 "started_iso": "2026-11-07T11:00:00+00:00",
                 "pull_started_iso": "2026-11-07T11:30:00+00:00"}]
    _, flags = check(longpull, now)
    ck(len(flags) == 1, "pull 30min > 20 -> FLAG")
    ck(check([], now) == ([], []), "empty machine list -> clean")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--now", help="ISO timestamp")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--outbox", default=OUTBOX)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    try:
        state = json.load(open(args.state))
    except (OSError, ValueError):
        print("SKIP no worker idle state %s" % args.state)
        return 2
    due, flags = check(state.get("machines", []), now)
    for name, reason in flags:
        print("FLAG %s: %s" % (name, reason))
    if due:
        os.makedirs(args.outbox, exist_ok=True)
        fn = os.path.join(args.outbox, "worker-idle-due-%s.json"
                          % now.strftime("%Y%m%d"))
        with open(fn, "w") as f:
            json.dump({"kind": "worker-idle-due",
                       "due": [{"machine": n, "reason": r} for n, r in due]},
                      f, ensure_ascii=False, indent=2)
        for name, reason in due:
            print("DUE %s: %s" % (name, reason))
        print("outbox: %s" % fn)
        return 1
    if flags:
        return 1
    print("PASS all workers within idle 30min / runtime 480min / pull 20min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
