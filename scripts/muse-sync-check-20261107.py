#!/usr/bin/env python3
"""muse_sync 4-step standard checker (2026-11-07, gap-fill).

GAP IT CLOSES: the 2026-11-04 decision (shared/3个muse磁盘利用-备份执行
要求-20261104.md) defines the muse_sync standard -- pull + SHA verify +
receipt + restore drill, all four steps, per disk; muse-1 mandatory,
muse/muse-2 optional extra -- but NO code on the hub checked any of it.
Central-queue tasks ws2-storage-muse / ws2-storage-muse-2 sit READY
unclaimed, and nothing reported that.

What this checks (hub-verifiable only; it never claims what it cannot
see):
  1. latest ~/backups-repo/backups/<date>/ exists and is <= max-age
     hours old (default 36h);
  2. SHA256SUMS present AND the sha256 of assets.tar.gz.enc actually
     matches (real verification, not a file-exists check);
  3. restore-drill evidence: weekly timer contract
     state/m5-20261105/cron-contracts/timer-backup_restore_drill.json
     exists (scheduled Sun 04:00); a drill *run* receipt is reported
     as present/missing honestly -- missing does NOT fail the run, it
     is flagged drill_evidence="missing" because the drill is weekly;
  4. per-disk verdicts: muse-1 VALID iff 1+2 pass (it is the hub-local
     copy); muse / muse-2 UNKNOWN (no pull receipt on the hub, queue
     tasks unclaimed) -- recorded, non-blocking, per the decision.
Writes state/muse-sync-20261107/latest.json.
Exit codes: 0 muse-1 valid, 1 muse-1 stale / SHA mismatch / repo missing.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
REPO = "/home/ubuntu/backups-repo"
STATE_DIR = os.path.join(BASE, "state", "muse-sync-20261107")
DRILL_CONTRACT = os.path.join(
    BASE, "state", "m5-20261105", "cron-contracts",
    "timer-backup_restore_drill.json")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="muse_sync standard checker")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--max-age-hours", type=float, default=36.0)
    ap.add_argument("--state-dir", default=STATE_DIR)
    ap.add_argument("--drill-contract", default=DRILL_CONTRACT)
    args = ap.parse_args()

    rec = {"ok": False, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "repo": args.repo, "problems": [], "disks": {}}
    bdir = os.path.join(args.repo, "backups")
    dates = []
    if os.path.isdir(bdir):
        dates = sorted(d for d in os.listdir(bdir)
                       if re.match(r"^\d{4}-\d{2}-\d{2}$", d))
    if not dates:
        rec["problems"].append("no dated backup dirs in %s" % bdir)
    else:
        latest = dates[-1]
        rec["latest_date"] = latest
        try:
            age_h = ((datetime.now(timezone.utc)
                      - datetime.strptime(latest, "%Y-%m-%d")
                      .replace(tzinfo=timezone.utc)).total_seconds() / 3600.0)
        except ValueError:
            age_h = 9999.0
        rec["latest_age_hours"] = round(age_h, 1)
        if age_h > args.max_age_hours:
            rec["problems"].append(
                "latest backup %s is %.1fh old (> %.0fh)" % (latest, age_h, args.max_age_hours))
        day = os.path.join(bdir, latest)
        sums = os.path.join(day, "SHA256SUMS")
        asset = os.path.join(day, "assets.tar.gz.enc")
        if not os.path.isfile(sums):
            rec["problems"].append("SHA256SUMS missing for %s" % latest)
            rec["sha_ok"] = False
        elif not os.path.isfile(asset):
            rec["problems"].append("assets.tar.gz.enc missing for %s" % latest)
            rec["sha_ok"] = False
        else:
            want = None
            with open(sums, encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].endswith("assets.tar.gz.enc"):
                        want = parts[0].lower()
            got = sha256_file(asset)
            rec["sha_ok"] = (want == got)
            rec["sha_want"] = (want or "")[:16]
            rec["sha_got"] = got[:16]
            if not rec["sha_ok"]:
                rec["problems"].append("SHA mismatch for %s assets.tar.gz.enc" % latest)

    rec["drill_scheduled"] = os.path.isfile(args.drill_contract)
    if not rec["drill_scheduled"]:
        rec["problems"].append("restore-drill timer contract missing: %s" % args.drill_contract)
    # Honest: no drill *run* receipt is tracked on the hub today.
    rec["drill_run_evidence"] = "missing"
    rec["drill_note"] = ("weekly Sun 04:00 timer; no run receipt found on hub -- "
                         "drill itself is NOT claimed passed here")

    muse1_valid = (not rec["problems"] and rec.get("sha_ok") is True
                   and rec["drill_scheduled"])
    rec["disks"]["muse-1"] = ("VALID" if muse1_valid else "INVALID")
    # muse / muse-2: no pull receipt exists on the hub; queue tasks
    # ws2-storage-muse(-2) READY unclaimed. Optional per decision:
    # recorded UNKNOWN, never blocks muse-1.
    rec["disks"]["muse"] = "UNKNOWN-no-pull-receipt-on-hub"
    rec["disks"]["muse-2"] = "UNKNOWN-no-pull-receipt-on-hub"
    rec["ok"] = muse1_valid
    os.makedirs(args.state_dir, exist_ok=True)
    with open(os.path.join(args.state_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
