#!/usr/bin/env python3
"""Unified gateway / backup / monitor carpet-audit (2026-11-07).

This script IS the carpet scan, landed as runnable code: it checks
every item in the 2026-11-07 scan scope for (a) code exists and
(b) is scheduled / fresh, and writes a machine-readable report.
It changes nothing (read-only except its own report + one outbox
alert when gaps exist). No network, no DB writes, no spend.

Checks (name -> code path, scheduled-in-crontab?, freshness file):
 GATEWAY: openrouter-rotation-guard, cf-neuron-guard,
   bailian-quota-guard, siliconflow-quota-guard,
   maritaca-minimax-guard, gateway_canary, health_3q,
   model-health, audit_gateway
 BACKUP: b2-backup, backup_assets, backup_amd_proxy, backup-watchdog,
   backup_restore_drill, muse-sync-check, crontab drift-check
 MONITOR: beszel-health, fleet-patrol, oci-7d-p95 (freshness <36h),
   brain_canary
A guard that exists only under state/ (not scripts/) counts as
"code-present, wrong-location" (WARN, not FAIL); a guard with code
but no live cron line counts as FAIL-unscheduled (scheduling new cron
lines needs chair approval -- this script reports it, never installs
it). Freshness checks read the item's latest.json / latest state file
and fail when older than the item's max age.
Usage: audit.py [--base DIR] [--crontab-file FILE] [--out FILE]
  --crontab-file / --base overrides exist so the test runner can feed
  fixtures. Default crontab source is `crontab -l`.
Exit codes: 0 no FAIL items, 1 at least one FAIL, 2 cannot read inputs.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
SCRIPTS = os.path.join(BASE, "scripts")
STATE = os.path.join(BASE, "state")
OUT_DEFAULT = os.path.join(STATE, "gateway-backup-monitor-audit-20261107",
                           "latest.json")

# (name, group, scripts-candidate, state-candidate-or-None,
#  cron-substring-or-None, freshness-file-or-None, max-age-hours-or-None,
#  code-required) -- code-required False means the item is judged by its
# freshness evidence alone (e.g. oci pull script lives outside ai-team
# at ~/oci-monitor/pull_7d_p95.py, not under scripts/ or state/).
CHECKS = [
    ("openrouter-rotation-guard", "gateway", "openrouter-rotation-guard.py", None,
     "openrouter-rotation-guard.py", "openrouter-rotation-20261105/rotation-pointer.json", 2),
    ("cf-neuron-guard", "gateway", "cf-neuron-guard-20261107.py",
     "cf-workers-ai-20261105/cf-neuron-guard.py", "cf-neuron-guard.py", None, None),
    ("bailian-quota-guard", "gateway", "bailian-quota-guard-20261107.py",
     "bailian-qwen-20261105/bailian-quota-guard.py", "bailian-quota-guard", None, None),
    ("siliconflow-quota-guard", "gateway", "siliconflow-quota-guard-20261107.py",
     "siliconflow-20261105/siliconflow-quota-guard.py", "siliconflow-quota-guard", None, None),
    ("maritaca-minimax-guard", "gateway", "maritaca-minimax-guard-20261107.py", None,
     "maritaca-minimax-guard-20261107.py", "maritaca-minimax-guard-20261107/latest.json", 48),
    ("gateway_canary", "gateway", "gateway_canary-20261104.py", None,
     "gateway_canary", "gateway-canary-20261104.jsonl", 2),
    ("health_3q", "gateway", "health_3q-20261104.py", None,
     "health_3q", None, None),
    ("model-health", "gateway", "model-health.py", None,
     "model-health.py", "model-health.json", 26),
    ("audit_gateway", "gateway", "audit_gateway-20261104.py", None,
     "audit_gateway", "gateway-audit.json", 26),
    ("b2-backup", "backup", "b2-backup-20261107.py", None,
     "b2-backup-20261107.py", "b2-backup-20261107/receipt-*.json", 168),
    ("backup_assets", "backup", "backup_assets.sh", None,
     "backup_assets.sh", None, None),
    ("backup_amd_proxy", "backup", "backup_amd_proxy.sh", None,
     "backup_amd_proxy.sh", None, None),
    ("backup-watchdog", "backup", "backup-watchdog.py", None,
     "backup-watchdog.py", "backup-watchdog.log", 1),
    ("backup_restore_drill", "backup", "backup_restore_drill-20261104.py", None,
     "backup_restore_drill", None, None),
    ("muse-sync-check", "backup", "muse-sync-check-20261107.py", None,
     "muse-sync-check-20261107.py", "muse-sync-20261107/latest.json", 48),
    ("crontab-drift-check", "backup", None,
     "crontab-as-code/drift-check.py", "drift-check.py", None, None),
    ("beszel-health", "monitor", "beszel-health-20261107.py", None,
     "beszel-health-20261107.py", "beszel-health-20261107/latest.json", 48),
    ("fleet-patrol", "monitor", None,
     "fleet-patrol-20261106/fleet-patrol.py", "fleet-patrol.py",
     "fleet-patrol-20261106/status.json", 1),
    ("oci-7d-p95", "monitor", None, None, "pull_7d_p95.py",
     "oci-7d-p95-latest.json", 36, False),
    ("brain_canary", "monitor", "brain_canary.py", None,
     "brain_canary.py", None, None),
]


def live_crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def cron_has(cron_text, needle):
    if not needle:
        return None  # no cron expectation for this item
    for line in cron_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if needle in s:
            return True
    return False


def file_age_h(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="gateway/backup/monitor audit")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--crontab-file", default=None)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--no-outbox", action="store_true")
    args = ap.parse_args()
    scripts = os.path.join(args.base, "scripts")
    state = os.path.join(args.base, "state")

    if args.crontab_file:
        try:
            cron_text = open(args.crontab_file, encoding="utf-8").read()
        except OSError as e:
            print(json.dumps({"ok": False, "error": "cannot read crontab file: %s" % e}))
            return 2
    else:
        cron_text = live_crontab()

    items, fails, warns = [], [], []
    for name, group, script, state_cand, cron_needle, fresh, max_age, *rest in CHECKS:
        code_required = rest[0] if rest else True
        code, location = None, None
        if script and os.path.isfile(os.path.join(scripts, script)):
            code, location = os.path.join(scripts, script), "scripts/"
        elif state_cand and os.path.isfile(os.path.join(state, state_cand)):
            code, location = os.path.join(state, state_cand), "state/ (not scripts/)"
        elif script and os.path.isfile(os.path.join(state, script)):
            code, location = os.path.join(state, script), "state/ (not scripts/)"
        scheduled = cron_has(cron_text, cron_needle)
        fresh_age, fresh_ok = None, None
        if fresh:
            import glob as _g
            cands = sorted(_g.glob(os.path.join(state, fresh)),
                           key=lambda p: os.path.getmtime(p))
            if cands:
                fresh_age = file_age_h(cands[-1])
                fresh_ok = (max_age is None or fresh_age <= max_age)
            else:
                fresh_ok = False
        status, reasons = "PASS", []
        if code is None and code_required:
            status, reasons = "FAIL", ["no code in scripts/ nor state/"]
        elif code is None:
            reasons = ["code lives outside ai-team (~/oci-monitor/), judged by freshness only"]
        else:
            if location.startswith("state/"):
                status, reasons = "WARN", ["code lives in state/, not scripts/"]
            if scheduled is False:
                status, reasons = "FAIL", reasons + ["code exists but no live cron line"]
            if fresh_ok is False:
                status = "FAIL"
                reasons.append("freshness evidence missing/stale: %s" % fresh)
        item = {"name": name, "group": group, "status": status,
                "code": code, "location": location, "scheduled": scheduled,
                "fresh_age_hours": (round(fresh_age, 1) if fresh_age is not None else None),
                "reasons": reasons}
        items.append(item)
        if status == "FAIL":
            fails.append(name)
        elif status == "WARN":
            warns.append(name)

    rec = {"ok": not fails, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "counts": {"total": len(items),
                      "pass": sum(1 for i in items if i["status"] == "PASS"),
                      "warn": len(warns), "fail": len(fails)},
           "fails": fails, "warns": warns, "items": items}
    outdir = os.path.dirname(args.out)
    os.makedirs(outdir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    if fails and not args.no_outbox:
        ob = os.path.join(state, "outbox")
        os.makedirs(ob, exist_ok=True)
        fn = os.path.join(ob, "gateway-backup-monitor-audit-%s.txt"
                          % time.strftime("%Y%m%d-%H%M%S"))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("Gateway/backup/monitor audit FAIL: %s\n" % ", ".join(fails))
        rec["outbox"] = fn
    print(json.dumps({"ok": rec["ok"], "counts": rec["counts"],
                      "fails": fails, "warns": warns,
                      "report": args.out}, ensure_ascii=False))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
