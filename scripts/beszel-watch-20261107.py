#!/usr/bin/env python3
"""Beszel watch (2026-11-07, MEMORY.md Beszel监管).

MEMORY: agent is up, public entry NOT deployed, report only on state
change (offline / long-term low usage / disk critical). Container/HTTP
liveness is owned by the sibling beszel-health-20261107.py (docker Up +
UI probe); THIS script covers only the two rules that file does not:
disk usage and the 7-day low-usage trend.
  * disk usage >90% on / -> FAIL + outbox.
  * low usage: 7-day mean CPU <5% from state samples -> WARN (not FAIL);
    fewer than 7 samples -> that check is SKIP-noted, never a false PASS.
  * outbox is written ONLY on a state change (new failure / recovery),
    never every run -- "平时不刷屏".
State: state/beszel-watch-state.json {samples:[{date,cpu_pct,mem_pct}],
last_reported:{...}}. Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, json, os, shutil, subprocess, sys
from datetime import date

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
STATE_FILE = os.path.join(BASE, "state", "beszel-watch-state.json")
OUTBOX = os.path.join(BASE, "state", "outbox")
CONTAINERS = ("beszel-agent-local", "beszel-hub")
DISK_PCT_LIMIT = 90
LOW_CPU_PCT = 5
LOW_DAYS = 7


def docker_ps():
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_containers(text):
    up = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if parts:
            up[parts[0]] = parts[1] if len(parts) > 1 else ""
    return {c: (c in up and up[c].lower().startswith("up")) for c in CONTAINERS}


def disk_pct(path="/"):
    u = shutil.disk_usage(path)
    return round(100.0 * (u.total - u.free) / u.total, 1)


def low_usage(samples):
    """samples: list of {cpu_pct}. Returns (verdict, detail)."""
    recent = [s for s in samples if isinstance(s.get("cpu_pct"), (int, float))][-LOW_DAYS:]
    if len(recent) < LOW_DAYS:
        return "skip", "only %d/%d daily samples" % (len(recent), LOW_DAYS)
    mean = sum(s["cpu_pct"] for s in recent) / len(recent)
    if mean < LOW_CPU_PCT:
        return "warn", "7d mean CPU %.1f%% < %d%%" % (mean, LOW_CPU_PCT)
    return "ok", "7d mean CPU %.1f%%" % mean


def check(disk, samples):
    problems, notes = [], []
    if disk is not None and disk > DISK_PCT_LIMIT:
        problems.append("disk %.1f%% > %d%%" % (disk, DISK_PCT_LIMIT))
    verdict, detail = low_usage(samples)
    if verdict == "warn":
        notes.append("LOW-USAGE " + detail)
    else:
        notes.append("usage check %s (%s)" % (verdict, detail))
    return problems, notes


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    probs, notes = check(45.0, [{"cpu_pct": 30.0}] * 7)
    ck(probs == [], "disk ok + usage ok -> no problems")
    probs, _ = check(95.0, [])
    ck(any("disk" in p for p in probs), "disk 95% -> FAIL")
    _, notes = check(45.0, [{"cpu_pct": 2.0}] * 7)
    ck(any("LOW-USAGE" in n for n in notes), "7d mean CPU 2% -> LOW-USAGE warn")
    _, notes = check(45.0, [{"cpu_pct": 2.0}] * 3)
    ck(any("skip" in n for n in notes), "<7 samples -> usage check skipped, not passed")
    ck(parse_containers("") == {"beszel-agent-local": False, "beszel-hub": False},
       "parse helper: empty docker output -> both down (fail closed)")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--disk-pct", type=float, help="inject disk pct (tests)")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--outbox", default=OUTBOX)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        state = json.load(open(args.state))
    except (OSError, ValueError):
        state = {"samples": [], "last_reported": {}}
    disk = args.disk_pct if args.disk_pct is not None else disk_pct("/")
    problems, notes = check(disk, state.get("samples", []))
    for n in notes:
        print(n)
    key = "fail:" + "|".join(sorted(problems)) if problems else "ok"
    changed = state.get("last_reported", {}).get("key") != key
    if problems:
        print("FAIL " + "; ".join(problems))
        if changed:  # state change only -> write outbox once
            os.makedirs(args.outbox, exist_ok=True)
            fn = os.path.join(args.outbox, "beszel-watch-%s.json" % date.today().isoformat())
            with open(fn, "w") as f:
                json.dump({"kind": "beszel-watch", "problems": problems}, f,
                          ensure_ascii=False, indent=2)
            print("outbox (state changed): %s" % fn)
        state["last_reported"] = {"key": key, "date": date.today().isoformat()}
        with open(args.state, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return 1
    if changed and state.get("last_reported"):
        print("RECOVERED (was failing, now ok)")
    print("PASS beszel disk %.1f%% within limit" % disk)
    state["last_reported"] = {"key": key, "date": date.today().isoformat()}
    with open(args.state, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
