#!/usr/bin/env python3
"""Watchdog registry check (2026-11-07 enforce scan).

Verifies every watch/sweep/scan script listed in
state/watchdog-registry.json:
  - script file exists under scripts/
  - .py scripts compile (py_compile)
  - cron presence matches the expected state:
      active       -> an UNCOMMENTED crontab line names the script
      retired      -> NO uncommented crontab line names it
      timer-driven -> a timer_submit cron line names the timer, and the
                      c2-executor handler table maps it to the script
      one-shot / superseded -> no cron expected (info only)
      orphan       -> FAIL: no cron, no caller; needs a decision
  - active entries with a log path: log file exists and is fresh
    (mtime within freshness_seconds).
Exit 0 = all entries OK; 1 = any FAIL. Prints JSON.
Usage: watchdog-registry-check-20261107.py [--registry PATH]
       [--crontab-file PATH] [--base PATH]
Defaults: hub paths. --crontab-file / --base exist for tests.
No writes except __pycache__ from py_compile; no network; no spend.
"""
import argparse
import json
import os
import subprocess
import sys
import time

BASE_DEFAULT = "/home/ubuntu/ai-team"
REGISTRY_DEFAULT = os.path.join(BASE_DEFAULT, "state", "watchdog-registry.json")


def _cron_lines(crontab_text):
    """Uncommented crontab lines (a line starting with '#' is a comment,
    including '# DISABLED-' / '# RETIRED-' markers)."""
    out = []
    for line in (crontab_text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _in_cron(name, cron_lines):
    return any(name in line for line in cron_lines)


def _log_fresh(base, log_rel, freshness_seconds):
    if log_rel is None or freshness_seconds is None:
        return ("na", "no log freshness requirement")
    p = os.path.join(base, log_rel)
    try:
        age = time.time() - os.path.getmtime(p)
    except OSError:
        return ("fail", "log missing: %s" % p)
    if age <= freshness_seconds:
        return ("pass", "log age %.0fs <= %ds" % (age, freshness_seconds))
    return ("fail", "log stale: age %.0fs > %ds" % (age, freshness_seconds))


def _compiles(path):
    r = subprocess.run([sys.executable, "-m", "py_compile", path],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return ("pass", "py_compile ok")
    return ("fail", "py_compile failed: %s" % ((r.stderr or "").strip()[:200]))


def check_entry(entry, cron_lines, base, executor_text):
    """Return (status, checks dict). status in ok/fail/info."""
    name = entry.get("name", "")
    expected = entry.get("expected", "")
    path = os.path.join(base, "scripts", name)
    checks = {}
    status = "ok"
    if not os.path.isfile(path):
        checks["exists"] = ("fail", "script missing: %s" % path)
        return "fail", checks
    checks["exists"] = ("pass", "script present")
    if name.endswith(".py"):
        checks["compiles"] = _compiles(path)
    in_cron = _in_cron(name, cron_lines)
    checks["in_cron"] = ("pass" if in_cron else "na", "in crontab" if in_cron else "not in crontab")
    if expected == "active":
        if not in_cron:
            checks["cron"] = ("fail", "expected active but no uncommented crontab line names %s" % name)
        else:
            checks["cron"] = ("pass", "crontab line present")
        if entry.get("silent_ok"):
            # Silent-on-success by design ("log only on action"): its own
            # log freshness proves nothing. Liveness proxy is the file
            # it watches, when one is declared.
            checks["log"] = ("na", "silent-on-success: own log freshness not applicable")
            if entry.get("watch_file"):
                checks["watch"] = _log_fresh(base, entry.get("watch_file"),
                                             entry.get("watch_freshness_seconds"))
        else:
            checks["log"] = _log_fresh(base, entry.get("log"), entry.get("freshness_seconds"))
    elif expected == "retired":
        if in_cron:
            checks["cron"] = ("fail", "expected retired but an uncommented crontab line still names %s" % name)
        else:
            checks["cron"] = ("pass", "not in crontab (retired)")
        checks["log"] = ("na", "retired: no log requirement")
    elif expected == "timer-driven":
        timer = entry.get("timer", "")
        timer_in_cron = bool(timer) and any("timer_submit-20261104.py %s" % timer in line for line in cron_lines)
        handler = bool(name) and (name in (executor_text or ""))
        if timer_in_cron and handler:
            checks["cron"] = ("pass", "timer_submit %s in cron + c2-executor handler present" % timer)
        else:
            checks["cron"] = ("fail", "timer-driven but timer_in_cron=%s handler=%s" % (timer_in_cron, handler))
        checks["log"] = ("na", "timer-driven: no log requirement")
    elif expected in ("one-shot", "superseded"):
        checks["cron"] = ("pass", "%s: no cron expected" % expected)
        checks["log"] = ("na", "%s: no log requirement" % expected)
        return "info", checks
    elif expected == "orphan":
        checks["cron"] = ("fail", "ORPHAN: no cron, no caller; decision needed (cron it or retire it)")
        checks["log"] = _log_fresh(base, entry.get("log"), entry.get("freshness_seconds"))
    else:
        checks["cron"] = ("fail", "unknown expected state: %r" % expected)
    for _k, (v, _d) in checks.items():
        if v == "fail":
            status = "fail"
            break
    return status, checks


def main(argv=None):
    ap = argparse.ArgumentParser(description="Watchdog registry check")
    ap.add_argument("--registry", default=REGISTRY_DEFAULT)
    ap.add_argument("--crontab-file", default=None,
                    help="read crontab text from this file instead of crontab -l (tests)")
    ap.add_argument("--base", default=BASE_DEFAULT)
    args = ap.parse_args(argv)
    try:
        registry = json.load(open(args.registry, encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": "cannot read registry: %s" % e}, ensure_ascii=False))
        return 1
    if args.crontab_file:
        try:
            crontab_text = open(args.crontab_file, encoding="utf-8").read()
        except Exception as e:
            print(json.dumps({"error": "cannot read crontab file: %s" % e}, ensure_ascii=False))
            return 1
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
        crontab_text = r.stdout if r.returncode == 0 else ""
    cron_lines = _cron_lines(crontab_text)
    executor_path = os.path.join(args.base, "scripts", "c2-executor-20261104.py")
    try:
        executor_text = open(executor_path, encoding="utf-8", errors="ignore").read()
    except OSError:
        executor_text = ""
    results = {}
    n_fail = 0
    for entry in registry.get("entries", []):
        status, checks = check_entry(entry, cron_lines, args.base, executor_text)
        results[entry.get("name", "?")] = {
            "expected": entry.get("expected"),
            "status": status,
            "checks": {k: {"result": v, "detail": d} for k, (v, d) in checks.items()},
        }
        if status == "fail":
            n_fail += 1
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "registry": args.registry,
           "entries": len(results), "fail": n_fail,
           "all_ok": n_fail == 0, "results": results}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
